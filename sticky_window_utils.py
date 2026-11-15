"""View constants, time labels, window helpers and calendar helpers."""

import calendar
import ctypes
import os
import time
from datetime import date, datetime, timedelta

import store

__all__ = ['VIEW_SIZES', 'VIEW_MIN_SIZES', 'VIEW_BUTTONS', 'FILTER_BUTTONS', 'NAV_FILTERS', 'NAV_VIEWS', 'NAV_WIDTH', 'DETAIL_WIDTH', 'DEFAULT_WIDTH', 'DEFAULT_HEIGHT', 'MIN_WIDTH', 'MIN_HEIGHT', 'REPEAT_OPTIONS', 'size_for_view', '_needs_scroll', '_local_datetime', '_short_when', '_log', '_hide_from_taskbar', '_set_click_through', '_dialog_position', '_place_dialog', '_month_grid']

VIEW_SIZES = {"list": (720, 520), "today": (720, 520), "week": (720, 520), "month": (720, 520)}

VIEW_MIN_SIZES = {"list": (600, 420), "today": (600, 420), "week": (600, 420), "month": (600, 420)}

VIEW_BUTTONS = (("list", "列表"), ("today", "今日"), ("week", "周"), ("month", "月"))

FILTER_BUTTONS = (
    ("all", "全部"), ("today", "今日"), ("overdue", "逾期"),
    ("week7", "7天"), ("nodate", "无截止"), ("done", "已完成"),
)

NAV_FILTERS = (
    ("today", "今日"), ("overdue", "逾期"), ("all", "全部"),
    ("week7", "未来7天"), ("nodate", "无截止"), ("done", "已完成"),
)

NAV_VIEWS = (("week", "周视图"), ("month", "月视图"))

NAV_WIDTH = 160

DETAIL_WIDTH = 300
DEFAULT_WIDTH, DEFAULT_HEIGHT = 720, 520
MIN_WIDTH, MIN_HEIGHT = 600, 420

REPEAT_OPTIONS = (("none", "不重复"), ("daily", "每天"), ("weekdays", "工作日"),
                  ("weekly", "每周"), ("monthly", "每月"))


def size_for_view(current_width, current_height, view):
    """Keep the user's current size; only enlarge when a view needs more room."""
    minimum_width, minimum_height = VIEW_MIN_SIZES[store.normalize_view(view)]
    try:
        width = int(current_width)
    except (TypeError, ValueError):
        width = minimum_width
    try:
        height = int(current_height)
    except (TypeError, ValueError):
        height = minimum_height
    return max(width, minimum_width), max(height, minimum_height)


def _needs_scroll(content_height, view_height):
    return content_height > view_height


def _local_datetime(iso):
    """Parse an ISO reminder; naive values count as local time (like due_todos)."""
    try:
        when = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.astimezone()
    return when


def _short_when(iso):
    """Today 15:00 / tomorrow 09:30 / 11-12 08:30 — one compact meta line."""
    when = _local_datetime(iso)
    if when is None:
        return ""
    today = date.today()
    if when.date() == today:
        prefix = "今天"
    elif when.date() == today + timedelta(days=1):
        prefix = "明天"
    else:
        prefix = "%d-%d" % (when.month, when.day)
    clock = "%H:%M"
    return prefix + " " + when.strftime(clock)


def _log(message):
    """Diagnostics go to stderr, i.e. plugins.log; never a dialog."""
    try:
        import sys

        sys.stderr.write("me.todo: %s\n" % message)
        sys.stderr.flush()
    except Exception:
        pass


def _hide_from_taskbar(root, user32=None):
    """Keep the note out of the taskbar/Alt+Tab: it is opened from the tray.

    Order matters: withdraw first, then change the style, then SetWindowPos
    with SWP_FRAMECHANGED so the shell re-reads the style. Skipping that
    refresh is why some machines kept showing the button.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        if user32 is None:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()

        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t

        style = get_style(hwnd, -20)  # GWL_EXSTYLE
        new_style = (style | 0x00000080) & ~0x00040000  # | TOOLWINDOW, & ~APPWINDOW

        ctypes.set_last_error(0)
        previous = set_style(hwnd, -20, new_style)
        error = ctypes.get_last_error()
        if previous == 0 and error:
            _log("SetWindowLongPtrW failed: error %s" % error)
            return False

        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        if not user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027):
            _log("SetWindowPos failed: error %s" % ctypes.get_last_error())
            return False
        return True
    except Exception as exc:
        _log("taskbar hide failed: %r" % (exc,))
        return False


def _set_click_through(root, enabled, user32=None):
    """Toggle WS_EX_TRANSPARENT so the locked note lets clicks pass through."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        if user32 is None:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()

        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t

        style = get_style(hwnd, -20)
        if enabled:
            new_style = style | 0x00000020  # WS_EX_TRANSPARENT
        else:
            new_style = style & ~0x00000020

        ctypes.set_last_error(0)
        previous = set_style(hwnd, -20, new_style)
        if previous == 0 and ctypes.get_last_error():
            _log("click-through style failed: %s" % ctypes.get_last_error())
            return False

        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        if not user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027):
            _log("click-through refresh failed: %s" % ctypes.get_last_error())
            return False
        return True
    except Exception as exc:
        _log("click-through failed: %r" % (exc,))
        return False


def _dialog_position(parent_x, parent_y, parent_w, parent_h, width, height):
    """Where a dialog opens: horizontally centred over the note, a third down."""
    x = parent_x + max(0, (parent_w - width) // 2)
    y = parent_y + max(0, (parent_h - height) // 3)
    return x, y


def _place_dialog(top, parent):
    """Open a dialog over the note instead of the top-left corner."""
    try:
        top.update_idletasks()
        width, height = top.winfo_reqwidth(), top.winfo_reqheight()
        if parent.winfo_width() <= 1:
            return
        x, y = _dialog_position(
            parent.winfo_rootx(), parent.winfo_rooty(),
            parent.winfo_width(), parent.winfo_height(), width, height,
        )
        top.geometry("+%d+%d" % (x, y))
    except Exception:
        pass


def _month_grid(year, month):
    """Six weeks of day numbers for the calendar, Monday first; None is a blank."""
    first_weekday = calendar.monthrange(year, month)[0]
    days = calendar.monthrange(year, month)[1]
    cells = [None] * first_weekday + list(range(1, days + 1))
    while len(cells) < 42:
        cells.append(None)
    return [cells[index:index + 7] for index in range(0, 42, 7)]
