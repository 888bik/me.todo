"""Ctrl+double-click detection used to unlock the note.

DoubleClickTracker is pure logic. The WH_MOUSE_LL hook is installed on its own
thread with its own message pump, and the callback only reads a plain tuple and
queues a command: it must never call Tk, because that thread is not the Tk
thread and re-entering Tcl from a mouse hook deadlocks the pointer.

Every Win32 entry point gets explicit argtypes/restype: without them ctypes
truncates 64-bit handles (HHOOK in particular) to 32-bit ints. The hook
callback is kept alive for the whole process; stop() must never release it
while a callback may still be running on the hook thread.
"""

import atexit
import ctypes
import os
import threading
import time

MAX_DISTANCE = 10
JOIN_TIMEOUT = 2.0

_hook = None
_callback = None
_thread = None
_thread_id = 0
_generation = 0
_atexit_registered = False
_api_cache = None


def _api():
    """user32/kernel32 with every signature we use declared once."""
    global _api_cache
    if _api_cache is not None:
        return _api_cache

    from ctypes import wintypes

    lresult = getattr(wintypes, "LRESULT", ctypes.c_ssize_t)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = wintypes.SHORT

    user32.GetMessageW.argtypes = [
        wintypes.LPMSG, wintypes.HWND, wintypes.UINT, wintypes.UINT,
    ]
    user32.GetMessageW.restype = wintypes.BOOL

    user32.TranslateMessage.argtypes = [wintypes.LPMSG]
    user32.TranslateMessage.restype = wintypes.BOOL

    user32.DispatchMessageW.argtypes = [wintypes.LPMSG]
    user32.DispatchMessageW.restype = lresult

    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL

    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    user32.PostThreadMessageW.restype = wintypes.BOOL

    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    ]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t

    user32.GetDoubleClickTime.argtypes = []
    user32.GetDoubleClickTime.restype = wintypes.UINT

    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    _api_cache = (user32, kernel32)
    return _api_cache


def _system_double_click_ms():
    if os.name != "nt":
        return 500
    try:
        return int(_api()[0].GetDoubleClickTime())
    except Exception:
        return 500


class DoubleClickTracker:
    """True for the second Ctrl+click when it is quick and close enough."""

    def __init__(self, interval_ms=None, max_distance=MAX_DISTANCE):
        self.interval_ms = _system_double_click_ms() if interval_ms is None else int(interval_ms)
        self.max_distance = int(max_distance)
        self._last = None

    def down(self, x, y, time_ms, ctrl_held):
        if not ctrl_held:
            self._last = None
            return False
        last = self._last
        if last is not None:
            last_x, last_y, last_time = last
            close = abs(x - last_x) <= self.max_distance and abs(y - last_y) <= self.max_distance
            quick = (time_ms - last_time) <= self.interval_ms
            if quick and close:
                self._last = None
                return True
        self._last = (x, y, time_ms)
        return False


def is_active():
    """Return whether the low-level mouse hook is still installed."""
    return bool(_hook and _thread_id)


def start(on_unlock, get_rect, log=None):
    """Install the low-level hook on a dedicated thread.

    get_rect() must be cheap and Tk-free: return a cached
    (left, top, right, bottom) tuple or None.
    """
    global _hook, _callback, _thread, _thread_id, _generation, _atexit_registered
    if os.name != "nt":
        return None
    if _hook and _thread_id:
        return _hook

    # Let a previous generation finish its finally before we install ours;
    # a slow old thread must not be able to unhook the new handle later.
    old_thread = _thread
    if old_thread is not None and old_thread.is_alive():
        old_thread.join(JOIN_TIMEOUT)

    _hook = None
    _thread_id = 0
    _generation += 1
    generation = _generation

    ready = threading.Event()
    outcome = {}

    def hook_loop():
        global _hook, _callback, _thread_id
        user32 = None
        handle = None
        try:
            from ctypes import wintypes

            user32, kernel32 = _api()
            lresult = getattr(wintypes, "LRESULT", ctypes.c_ssize_t)

            class POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

            class MSLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("pt", POINT),
                    ("mouseData", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t),
                ]

            hook_proc = ctypes.WINFUNCTYPE(
                lresult, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
            )
            user32.SetWindowsHookExW.restype = wintypes.HHOOK
            user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int, hook_proc, wintypes.HINSTANCE, wintypes.DWORD,
            ]

            tracker = DoubleClickTracker(interval_ms=_system_double_click_ms())

            def callback(n_code, w_param, l_param):
                try:
                    if n_code >= 0 and w_param == 0x0201:  # WM_LBUTTONDOWN
                        if user32.GetAsyncKeyState(0x11) & 0x8000:  # VK_CONTROL
                            info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                            rect = get_rect()
                            if (
                                rect
                                and rect[0] <= info.pt.x <= rect[2]
                                and rect[1] <= info.pt.y <= rect[3]
                            ):
                                now_ms = int(time.monotonic() * 1000)
                                if tracker.down(info.pt.x, info.pt.y, now_ms, True):
                                    on_unlock()
                except Exception:
                    pass
                return user32.CallNextHookEx(None, n_code, w_param, l_param)

            proc = hook_proc(callback)
            handle = user32.SetWindowsHookExW(14, proc, None, 0)
            if not handle:
                outcome["error"] = ctypes.get_last_error()
                ready.set()
                return

            _hook = handle
            _callback = proc  # never cleared: ctypes must keep it alive
            _thread_id = kernel32.GetCurrentThreadId()
            ready.set()

            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            outcome["error"] = exc
            ready.set()
        finally:
            # Only this generation may unhook and clear: an old thread waking
            # up later must never tear down a newer hook.
            if (
                handle is not None
                and user32 is not None
                and _hook == handle
                and _generation == generation
            ):
                try:
                    user32.UnhookWindowsHookEx(handle)
                except Exception:
                    pass
                _hook = None
                _thread_id = 0
            # _callback stays referenced until process exit.

    thread = threading.Thread(target=hook_loop, name="me.todo-mouse-hook", daemon=True)
    thread.start()
    _thread = thread
    ready.wait(2.0)

    if outcome.get("error"):
        if log is not None:
            log("mouse hook failed: %r" % (outcome["error"],))
        return None

    if not _atexit_registered:
        atexit.register(stop)
        _atexit_registered = True
    return _hook


def stop():
    global _hook, _thread, _thread_id
    if not _hook and (_thread is None or not _thread.is_alive()):
        return

    thread = _thread
    hook = _hook
    thread_id = _thread_id
    generation = _generation

    if hook:
        try:
            user32, _kernel32 = _api()
            if thread_id:
                user32.PostThreadMessageW(thread_id, 0x0012, 0, 0)  # WM_QUIT
            user32.UnhookWindowsHookEx(hook)
        except Exception:
            pass

    if thread is not None and thread.is_alive():
        thread.join(JOIN_TIMEOUT)

    # Clear only what still belongs to this generation.
    if _generation == generation and _hook == hook:
        _hook = None
        _thread_id = 0
    if thread is not None and not thread.is_alive():
        _thread = None
    # _callback is intentionally kept: a callback may still be running.
