"""Lock mixin for the sticky window."""

import calendar
import os
import queue
import threading
import time
from datetime import date, datetime, timedelta

import store
from sticky_window_theme import *
from sticky_window_theme import register_palette_module
from sticky_window_utils import *

tk = None
ttk = None
messagebox = None
tkfont = None

register_palette_module(globals())


def _click_through(root, enabled):
    # Lazy facade lookup keeps mock.patch("sticky_window._set_click_through")
    # working after the split.
    import sticky_window
    return sticky_window._set_click_through(root, enabled)


def _unlock_label(window):
    key = str((getattr(window, "settings", None) or {}).get("lock_hotkey") or "Ctrl+Alt+U").strip()
    return key or "Ctrl+Alt+U"

class LockMixin:
    def _set_topmost(self, enabled):
        """Keep the main note above other apps only while it is visible+locked."""
        try:
            visible = str(self.root.state()) not in ("withdrawn", "iconic")
            self.root.attributes("-topmost", bool(enabled and visible))
        except Exception:
            pass

    def _raise_window(self):
        """Bring a shown note forward, then drop the temporary topmost flag."""
        try:
            self.root.lift()
        except Exception:
            pass
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self._cancel_topmost_restore()
        try:
            self._topmost_restore_id = self.root.after(200, self._restore_topmost)
        except Exception:
            self._topmost_restore_id = None

    def _cancel_topmost_restore(self):
        if self._topmost_restore_id is not None:
            try:
                self.root.after_cancel(self._topmost_restore_id)
            except Exception:
                pass
            self._topmost_restore_id = None

    def _restore_topmost(self):
        self._topmost_restore_id = None
        if not self.locked:
            self._set_topmost(False)

    def _toggle_lock(self):
        self._set_locked_ui(not self.locked)
        self.controller.set_locked(self.locked)

    def _set_locked_ui(self, locked):
        self.locked = bool(locked)
        self.config["locked"] = self.locked
        self._apply_lock(announce=True)

    def _apply_lock(self, announce=False):
        try:
            self.lock_button.configure(text="🔒" if self.locked else "🔓")
        except Exception:
            pass
        self._set_topmost(self.locked)
        if self.locked:
            hook_active = self._start_hook()
            # Never make the whole window transparent unless the global hook
            # is confirmed active. Otherwise the UI would have no mouse or
            # keyboard route back to the unlocked state.
            _click_through(self.root, bool(hook_active))
            if announce:
                self.status.configure(text="已锁定 · Ctrl+双击解锁 · %s 紧急解锁" % _unlock_label(self))
            return bool(hook_active)
        else:
            _click_through(self.root, False)
            self._stop_hook()
            if announce:
                self.status.configure(text="")
            return False

    def _check_lock_hook(self, hidden=False):
        """Reinstall a dead Windows mouse hook without sacrificing recovery input."""
        if not self.locked or hidden or os.name != "nt":
            return
        now = time.monotonic()
        if now - self._last_hook_check < 2.0:
            return
        self._last_hook_check = now

        try:
            import mouse_hook
            if mouse_hook.is_active():
                return
        except Exception:
            pass

        try:
            hook_active = bool(self._apply_lock(announce=False))
        except Exception as exc:
            hook_active = False
            _log("mouse hook watchdog restart failed: %r" % (exc,))

        if hook_active:
            try:
                self.status.configure(text="已锁定 · Ctrl+双击解锁 · %s 紧急解锁" % _unlock_label(self))
            except Exception:
                pass
        else:
            # Be explicit even if _apply_lock's fallback already did this:
            # a failed hook must never leave WS_EX_TRANSPARENT enabled.
            _click_through(self.root, False)
            try:
                self.status.configure(text="钩子不可用，无法 Ctrl+双击解锁 · 请使用 %s 解锁" % _unlock_label(self))
            except Exception:
                pass

    def _clear_lock_hint(self):
        if self.locked:
            self.status.configure(text="")

    def _update_hook_rect(self):
        """Cache the window rect from the Tk thread; the hook only reads it."""
        try:
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            self._hook_rect = (
                x, y, x + self.root.winfo_width(), y + self.root.winfo_height()
            )
        except Exception:
            self._hook_rect = None

    def _hook_bounds(self):
        return self._hook_rect

    def _start_hook(self):
        if os.name != "nt":
            return False
        try:
            import mouse_hook
        except Exception:
            return False

        if self._hook is not None and mouse_hook.is_active():
            return True
        self._hook = None
        self._update_hook_rect()
        self._hook = mouse_hook.start(
            lambda: self.commands.put(("locked", False)), self._hook_bounds, _log
        )
        return bool(self._hook) and mouse_hook.is_active()

    def _stop_hook(self):
        if self._hook is None:
            return
        try:
            import mouse_hook

            mouse_hook.stop()
        except Exception:
            pass
        self._hook = None

