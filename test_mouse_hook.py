import ctypes
import threading
import unittest
from ctypes import wintypes
from unittest import mock

import mouse_hook

LRESULT = getattr(wintypes, "LRESULT", ctypes.c_ssize_t)


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", _POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _FakeApi:
    """user32/kernel32 mocks; GetMessageW blocks until the thread's gate is set."""

    def __init__(self, block=True, release_on_quit=True):
        self.user32 = mock.MagicMock(name="user32")
        self.kernel32 = mock.MagicMock(name="kernel32")
        self.block = block
        self.release_on_quit = release_on_quit
        self.gates = {}
        self.tid_gates = {}
        self._next_tid = 1000
        self.install_thread = None
        self.proc = None
        self.install_calls = 0
        self.unhooked = []
        self.message_script = None

        self.user32.GetDoubleClickTime.return_value = 500
        self.user32.GetAsyncKeyState.return_value = 0x8000
        self.user32.CallNextHookEx.return_value = 42
        self.user32.GetMessageW.side_effect = self._get_message
        self.user32.SetWindowsHookExW.side_effect = self._install
        self.user32.PostThreadMessageW.side_effect = self._post_quit
        self.user32.UnhookWindowsHookEx.side_effect = self._unhook
        self.kernel32.GetCurrentThreadId.side_effect = self._thread_id

    def _install(self, *args):
        self.install_calls += 1
        self.install_thread = threading.get_ident()
        self.proc = args[1]
        self.gates.setdefault(threading.current_thread(), threading.Event())
        return 0x1000 + self.install_calls

    def _thread_id(self):
        tid = self._next_tid
        self._next_tid += 1
        self.tid_gates[tid] = self.gates.setdefault(threading.current_thread(), threading.Event())
        return tid

    def _get_message(self, *args):
        if self.message_script is not None:
            return self.message_script.pop(0) if self.message_script else 0
        gate = self.gates.setdefault(threading.current_thread(), threading.Event())
        if self.block:
            gate.wait(5)
        return 0

    def _post_quit(self, thread_id, message, wparam, lparam):
        gate = self.tid_gates.get(thread_id)
        if self.release_on_quit and gate is not None:
            gate.set()
        return 1

    def _unhook(self, hook):
        self.unhooked.append(hook)
        return 1

    def release_all(self):
        for gate in self.gates.values():
            gate.set()


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = mouse_hook.DoubleClickTracker(interval_ms=500, max_distance=10)

    def test_ctrl_double_click_is_detected(self):
        self.assertFalse(self.tracker.down(100, 100, 1000, True))
        self.assertTrue(self.tracker.down(105, 103, 1300, True))

    def test_slow_second_click_is_not_a_double_click(self):
        self.assertFalse(self.tracker.down(100, 100, 1000, True))
        self.assertFalse(self.tracker.down(100, 100, 1700, True))

    def test_far_second_click_is_not_a_double_click(self):
        self.assertFalse(self.tracker.down(100, 100, 1000, True))
        self.assertFalse(self.tracker.down(140, 100, 1100, True))

    def test_missing_ctrl_is_ignored(self):
        self.assertFalse(self.tracker.down(100, 100, 1000, False))
        self.assertFalse(self.tracker.down(100, 100, 1100, False))

    def test_success_resets_the_tracker(self):
        self.tracker.down(100, 100, 1000, True)
        self.assertTrue(self.tracker.down(100, 100, 1100, True))

        self.assertFalse(self.tracker.down(100, 100, 1200, True))

    def test_is_active_is_false_when_hook_is_not_installed(self):
        with mock.patch.object(mouse_hook, "_hook", None), mock.patch.object(mouse_hook, "_thread_id", 0):
            self.assertFalse(mouse_hook.is_active())

    def test_start_is_a_noop_off_windows(self):
        with mock.patch("mouse_hook.os.name", "posix"):
            self.assertIsNone(mouse_hook.start(lambda: None, lambda: (0, 0, 1, 1)))


class HookInstallTests(unittest.TestCase):
    def setUp(self):
        mouse_hook._hook = None
        mouse_hook._thread = None
        mouse_hook._thread_id = 0
        mouse_hook._callback = None
        mouse_hook._api_cache = None

    def tearDown(self):
        mouse_hook._hook = None
        mouse_hook._thread = None
        mouse_hook._thread_id = 0
        mouse_hook._api_cache = None

    def start_hook(self, block=True, release_on_quit=True, get_rect=None, on_unlock=None, api=None):
        self.api = api or _FakeApi(block=block, release_on_quit=release_on_quit)

        os_patch = mock.patch("mouse_hook.os.name", "nt")
        os_patch.start()
        self.addCleanup(os_patch.stop)

        win_patch = mock.patch(
            "mouse_hook.ctypes.WinDLL", side_effect=self._win_dll, create=True
        )
        win_patch.start()
        self.addCleanup(win_patch.stop)

        # WINFUNCTYPE is Windows-only as well; the hook callback only needs a
        # plain callable, so use it as-is instead of a C function pointer.
        func_patch = mock.patch(
            "mouse_hook.ctypes.WINFUNCTYPE",
            lambda *args: (lambda cb: cb),
            create=True,
        )
        func_patch.start()
        self.addCleanup(func_patch.stop)

        self.handle = mouse_hook.start(
            on_unlock or (lambda: None),
            get_rect or (lambda: (0, 0, 300, 400)),
        )
        self.addCleanup(self._cleanup)
        return self.handle

    def _win_dll(self, name, **kwargs):
        return self.api.user32 if name == "user32" else self.api.kernel32

    def _cleanup(self):
        self.api.release_all()
        mouse_hook.stop()

    def click(self, x=100, y=100):
        info = _MSLLHOOKSTRUCT()
        info.pt.x = x
        info.pt.y = y
        return self.api.proc(0, 0x0201, ctypes.addressof(info))

    def test_every_user32_function_has_64_bit_signatures(self):
        self.start_hook()

        user32 = self.api.user32
        self.assertEqual(user32.GetAsyncKeyState.argtypes, [ctypes.c_int])
        self.assertIs(user32.GetAsyncKeyState.restype, wintypes.SHORT)

        self.assertEqual(
            user32.GetMessageW.argtypes,
            [wintypes.LPMSG, wintypes.HWND, wintypes.UINT, wintypes.UINT],
        )
        self.assertIs(user32.GetMessageW.restype, wintypes.BOOL)

        self.assertEqual(user32.TranslateMessage.argtypes, [wintypes.LPMSG])
        self.assertIs(user32.TranslateMessage.restype, wintypes.BOOL)

        self.assertEqual(user32.DispatchMessageW.argtypes, [wintypes.LPMSG])
        self.assertIs(user32.DispatchMessageW.restype, LRESULT)

        self.assertEqual(user32.UnhookWindowsHookEx.argtypes, [wintypes.HHOOK])
        self.assertIs(user32.UnhookWindowsHookEx.restype, wintypes.BOOL)

        self.assertEqual(
            user32.PostThreadMessageW.argtypes,
            [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM],
        )
        self.assertIs(user32.PostThreadMessageW.restype, wintypes.BOOL)

        self.assertEqual(self.api.kernel32.GetCurrentThreadId.argtypes, [])
        self.assertIs(self.api.kernel32.GetCurrentThreadId.restype, wintypes.DWORD)

        self.assertEqual(
            user32.CallNextHookEx.argtypes,
            [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM],
        )
        self.assertIs(user32.SetWindowsHookExW.restype, wintypes.HHOOK)

    def test_hook_is_installed_on_its_own_thread(self):
        self.start_hook()

        self.assertNotEqual(self.api.install_thread, threading.get_ident())
        self.assertEqual(self.api.install_calls, 1)

    def test_repeated_start_does_not_install_again(self):
        self.start_hook()

        mouse_hook.start(lambda: None, lambda: (0, 0, 300, 400))

        self.assertEqual(self.api.install_calls, 1)

    def test_stop_keeps_the_callback_alive(self):
        self.start_hook()
        callback = mouse_hook._callback
        self.assertIsNotNone(callback)

        mouse_hook.stop()

        self.assertIs(mouse_hook._callback, callback)

    def test_dispatch_error_does_not_kill_the_message_pump(self):
        api = _FakeApi(block=False)
        api.message_script = [1, 1, 0]
        errors = []

        def dispatch(*args):
            if not errors:
                errors.append(True)
                self.assertTrue(mouse_hook.is_active())
                raise RuntimeError("boom")
            return 1

        api.user32.DispatchMessageW.side_effect = dispatch
        self.start_hook(api=api)
        mouse_hook._thread.join(2)

        self.assertFalse(mouse_hook._thread.is_alive())
        self.assertEqual(api.user32.DispatchMessageW.call_count, 2)
        self.assertEqual(api.unhooked, [0x1001])

    def test_stop_waits_for_the_hook_thread_to_exit(self):
        self.start_hook()
        thread = mouse_hook._thread
        self.assertIsNotNone(thread)

        mouse_hook.stop()

        self.assertFalse(thread.is_alive())

    def test_old_thread_cannot_unhook_a_new_generation(self):
        self.start_hook(release_on_quit=False)
        thread_a = mouse_hook._thread
        self.assertIsNotNone(thread_a)

        with mock.patch.object(mouse_hook, "JOIN_TIMEOUT", 0.05):
            mouse_hook.stop()
            self.assertTrue(thread_a.is_alive())

            handle_b = mouse_hook.start(lambda: None, lambda: (0, 0, 300, 400))
            self.assertEqual(handle_b, 0x1002)

            self.api.gates[thread_a].set()
            thread_a.join(2)

        self.assertFalse(thread_a.is_alive())
        self.assertEqual(mouse_hook._hook, 0x1002)
        self.assertTrue(mouse_hook.is_active())
        self.assertNotIn(0x1002, self.api.unhooked)

    def test_five_stop_start_cycles_keep_one_live_hook(self):
        self.start_hook()

        for _ in range(5):
            mouse_hook.stop()
            self.assertIsNotNone(
                mouse_hook.start(lambda: None, lambda: (0, 0, 300, 400))
            )

        current = mouse_hook._hook
        self.assertIsNotNone(current)
        self.assertTrue(mouse_hook.is_active())
        self.assertNotIn(current, self.api.unhooked)
        self.assertEqual(self.api.install_calls, 6)

    def test_callback_only_reads_the_cached_rectangle(self):
        rect = mock.Mock(return_value=(0, 0, 300, 400))
        unlocked = []
        self.start_hook(get_rect=rect, on_unlock=lambda: unlocked.append(True))

        self.assertEqual(self.click(), 42)
        self.assertEqual(self.click(), 42)

        self.assertEqual(rect.call_count, 2)
        self.assertEqual(unlocked, [True])

    def test_callback_still_calls_the_next_hook_when_it_fails(self):
        self.start_hook(get_rect=mock.Mock(side_effect=RuntimeError("boom")))

        self.assertEqual(self.click(), 42)
        self.assertEqual(self.api.user32.CallNextHookEx.call_count, 1)


if __name__ == "__main__":
    unittest.main()
