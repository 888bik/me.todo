"""The desktop sticky note (tkinter), on its own daemon thread.

tkinter is imported only when the note is first shown. The JSON-RPC loop never
waits for the window: every call below only queues a command.
"""

import calendar
import os
import queue
import threading
from datetime import date, datetime, timedelta

import store

# Imported by _load_tk(), inside the window thread only.
tk = None
ttk = None
messagebox = None

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

BG = "#FFFDF8"
CARD = "#FFFFFF"
BORDER = "#ECE6D8"
HOVER = "#DDD5C2"
TEXT = "#2E2A24"
MUTED = "#9AA0A6"
ACCENT = "#4C6FFF"
ACCENT_DARK = "#3B5AE0"
CLOCK = "#D97706"
DEADLINE_FG = "#2E2A24"
DANGER = "#DC2626"
TODAY_BG = "#EEF1FF"
ACCENT_FILL = "#4C6FFF"
ACCENT_TEXT = "#FFFFFF"
PRIORITY_COLORS = {1: "#9AA0A6", 2: "#D97706", 3: "#DC2626"}
PRIORITY_LABELS = {1: "低", 2: "中", 3: "高"}
FONT_FAMILY = "Microsoft YaHei"

_LIGHT_PALETTE = {
    "BG": "#FFFDF8", "CARD": "#FFFFFF", "BORDER": "#ECE6D8", "HOVER": "#DDD5C2",
    "TEXT": "#2E2A24", "MUTED": "#9AA0A6", "ACCENT": "#4C6FFF",
    "ACCENT_DARK": "#3B5AE0", "CLOCK": "#D97706", "DEADLINE_FG": "#2E2A24",
    "DANGER": "#DC2626", "TODAY_BG": "#EEF1FF", "ACCENT_FILL": "#4C6FFF",
    "ACCENT_TEXT": "#FFFFFF",
}
_DARK_PALETTE = {
    "BG": "#202124", "CARD": "#292A2D", "BORDER": "#3A3B40", "HOVER": "#4A4B52",
    "TEXT": "#E8EAED", "MUTED": "#A1A6AD", "ACCENT": "#8AB4F8",
    "ACCENT_DARK": "#669DF6", "CLOCK": "#E2A44F", "DEADLINE_FG": "#D9A665",
    "DANGER": "#F28B82", "TODAY_BG": "#303D57", "ACCENT_FILL": "#8AB4F8",
    "ACCENT_TEXT": "#202124",
}
_PALETTE_KEYS = tuple(_LIGHT_PALETTE)
_current_appearance = {}
_current_theme = "light"


def _color(value, fallback, backdrop):
    """Convert Win顺 #AARRGGBB / Tk #RRGGBB colors to opaque Tk colors."""
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    if len(value) == 7 and value.startswith("#"):
        return value.upper()
    if len(value) == 9 and value.startswith("#"):
        try:
            alpha = int(value[1:3], 16) / 255.0
            foreground = tuple(int(value[index:index + 2], 16) for index in (3, 5, 7))
            background = tuple(int(backdrop[index:index + 2], 16) for index in (1, 3, 5))
            mixed = tuple(round(fg * alpha + bg * (1.0 - alpha)) for fg, bg in zip(foreground, background))
            return "#%02X%02X%02X" % mixed
        except (TypeError, ValueError):
            return fallback
    return fallback


def _palette_snapshot():
    return {key: globals()[key] for key in _PALETTE_KEYS}


def _set_palette(appearance=None, theme="light"):
    """Use the host's nativeLook colors when available; otherwise use a safe palette."""
    global BG, CARD, BORDER, HOVER, TEXT, MUTED, ACCENT, ACCENT_DARK
    global CLOCK, DEADLINE_FG, DANGER, TODAY_BG, ACCENT_FILL, ACCENT_TEXT
    global _current_appearance, _current_theme

    _current_appearance = dict(appearance or {})
    _current_theme = "dark" if str(theme).lower() == "dark" else "light"
    base = dict(_DARK_PALETTE if _current_theme == "dark" else _LIGHT_PALETTE)
    native = _current_appearance.get("nativeLook")
    if not isinstance(native, dict):
        native = {}

    # Resolve the base surface first, then composite translucent native colors
    # over it. Tk widgets expect opaque RGB values.
    base_bg = _color(native.get("background"), base["BG"], base["BG"])
    mapping = {
        "BG": "background", "CARD": "layer", "BORDER": "divider", "HOVER": "hover",
        "TEXT": "text", "MUTED": "subtext", "ACCENT": "accent", "ACCENT_DARK": "pressed",
        "DANGER": "danger", "TODAY_BG": "tagFill", "ACCENT_FILL": "accentFill",
        "ACCENT_TEXT": "accentText",
    }
    base["BG"] = base_bg
    for key, native_key in mapping.items():
        if key == "BG":
            continue
        base[key] = _color(native.get(native_key), base[key], base_bg)

    # nativeLook does not define warning/deadline colors; keep these legible
    # in both themes while using the host's danger color for overdue items.
    base["CLOCK"] = _color(native.get("accent"), base["CLOCK"], base_bg)
    base["DEADLINE_FG"] = base["MUTED"] if native else base["DEADLINE_FG"]

    for key, value in base.items():
        globals()[key] = value


def _recolor_widget_tree(widget, color_map):
    """Remap existing Tk widgets in-place so an appearance change is lossless."""
    options = (
        "background", "bg", "foreground", "fg", "activebackground", "activeforeground",
        "highlightbackground", "highlightcolor", "insertbackground", "selectbackground",
        "selectforeground", "disabledforeground",
    )
    try:
        for option in options:
            try:
                current = widget.cget(option)
            except Exception:
                continue
            replacement = color_map.get(str(current).upper())
            if replacement:
                try:
                    widget.configure(**{option: replacement})
                except Exception:
                    pass
        children = widget.winfo_children()
    except Exception:
        return
    for child in children:
        _recolor_widget_tree(child, color_map)

REPEAT_OPTIONS = (("none", "不重复"), ("daily", "每天"), ("weekdays", "工作日"),
                  ("weekly", "每周"), ("monthly", "每月"))

_state = {"thread": None, "queue": None, "window": None, "visible": False}
_lock = threading.Lock()


def _load_tk():
    global tk, ttk, messagebox
    if tk is not None:
        return
    import tkinter as _tk
    from tkinter import messagebox as _messagebox
    from tkinter import ttk as _ttk

    tk = _tk
    ttk = _ttk
    messagebox = _messagebox


def dpi_scale(root):
    """Tk scaling from the actual screen DPI, clamped for readable layouts."""
    try:
        factor = float(root.winfo_fpixels("1i")) / 72.0
    except Exception:
        return 1.0
    return max(1.0, min(2.5, factor))


def _apply_dpi_scaling(root):
    factor = dpi_scale(root)
    try:
        root.tk.call("tk", "scaling", factor)
    except Exception:
        pass
    return factor


def _font(size, bold=False, overstrike=False):
    """Every label in the note derives from the one font size the gear sets."""
    styles = []
    if bold:
        styles.append("bold")
    if overstrike:
        styles.append("overstrike")
    return (FONT_FAMILY, int(size)) + tuple(styles)


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


def _alpha_value(transparency):
    """50-100 -> 0.5-1.0, clamped."""
    try:
        value = int(transparency)
    except (TypeError, ValueError):
        value = 95
    value = max(50, min(100, value))
    return value / 100.0


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


def is_visible():
    return bool(_state["visible"])


def show(controller, settings, focus=False):
    _ensure(controller, settings)
    _state["queue"].put(("show", bool(focus)))


def hide():
    if _state["queue"] is not None:
        _state["queue"].put(("hide",))


def refresh():
    if _state["queue"] is not None:
        _state["queue"].put(("refresh",))


def apply_settings(settings):
    if _state["queue"] is not None:
        _state["queue"].put(("settings", dict(settings or {})))


def apply_appearance(appearance=None, theme=None):
    """Apply the current Win顺 appearance, or remember it before the UI starts."""
    appearance = dict(appearance or {})
    theme = theme or appearance.get("theme") or _current_theme
    thread = _state.get("thread")
    commands = _state.get("queue")
    if commands is not None and thread is not None and thread.is_alive():
        commands.put(("appearance", appearance, theme))
    else:
        _set_palette(appearance, theme)


def set_locked(locked):
    if _state["queue"] is not None:
        _state["queue"].put(("locked", bool(locked)))


def set_compact(compact):
    if _state["queue"] is not None:
        _state["queue"].put(("compact", bool(compact)))


def show_stats():
    if _state["queue"] is not None:
        _state["queue"].put(("stats",))


def export_todos():
    if _state["queue"] is not None:
        _state["queue"].put(("export",))


def import_todos():
    if _state["queue"] is not None:
        _state["queue"].put(("import",))


def _ensure(controller, settings):
    with _lock:
        thread = _state["thread"]
        if thread is not None and thread.is_alive():
            _state["queue"].put(("settings", dict(settings or {})))
            return
        commands = queue.Queue()
        _state["queue"] = commands
        _state["thread"] = threading.Thread(
            target=_run, args=(commands, controller, dict(settings or {})), name="me.todo-sticky", daemon=True
        )
        _state["thread"].start()


def _run(commands, controller, settings):
    _load_tk()
    root = tk.Tk()
    _apply_dpi_scaling(root)
    window = _StickyWindow(root, commands, controller, settings)
    _state["window"] = window
    _state["visible"] = False
    root.after(40, window.pump)
    root.mainloop()


class _StickyWindow:
    def __init__(self, root, commands, controller, settings):
        self.root = root
        self.commands = commands
        self.controller = controller
        self.settings = dict(settings)
        self.config = dict(controller.get_config())
        self.font_size = store._font_size(self.config.get("font_size"))
        self.view = store.normalize_view(self.config.get("view"))
        self.filter = store.normalize_filter(self.config.get("filter"))
        if self.view == "today":
            self.view = "list"
            self.filter = "today"
        self.compact = bool(self.config.get("compact"))
        transparency = self.config.get("transparency")
        if not isinstance(transparency, int) or isinstance(transparency, bool):
            transparency = self.settings.get("transparency", 95)
        self.transparency = max(50, min(100, int(transparency)))
        today = date.today()
        self._week_start = today - timedelta(days=today.weekday())
        self._month_year, self._month_month = today.year, today.month
        self._geometry_job = None
        self._icon = None
        self._font_dialog = None
        self._reminder_picker = None
        self._stats_dialog = None
        self._editing_id = None
        self._edit_entry = None
        self._refresh_pending = False
        self._search_job = None
        self._undo_job = None
        self._day_dialog = None
        self._details_dialog = None
        self._detail_frame = None
        self._details_panel = None
        self._detail_visible = False
        self._detail_base_width = None
        self._selected_todo_id = None
        self._row_widgets = {}
        self._focus_todo_id = None
        self.search = ""
        self._subtask_parent = None
        self._subtask_entry = None
        self._child_counts = {}
        self._list_yview = 0.0
        self._list_total = 0
        self._list_items = []
        self._row_pool = []
        self._row_height_cache = {}
        self._row_items = {}
        self._subtask_item = None
        self._list_update_id = None
        self._list_updating = False
        self._list_signature = None
        self._subtask_widget = None
        self._subtask_frame = None
        self.locked = bool(self.config.get("locked"))
        self._topmost_restore_id = None
        self._hook = None
        self._hook_rect = None
        self._build()

    # -- construction ---------------------------------------------------
    def _build(self):
        self.root.withdraw()
        self.root.update_idletasks()
        _hide_from_taskbar(self.root)
        self.root.title("待办")
        self.root.configure(bg=BG)
        # Normal windows participate in the regular Windows z-order. Only a
        # visible, explicitly locked sticky note is kept above other apps.
        self.root.attributes("-topmost", False)
        self._set_icon()
        self._apply_alpha()
        self._apply_size()

        self.style = ttk.Style(self.root)
        self.style.configure("Card.TCheckbutton", background=CARD, font=_font(self.font_size))
        self.style.map("Card.TCheckbutton", background=[("active", CARD)])

        self.nav = tk.Frame(self.root, bg=BG, width=NAV_WIDTH)
        self.nav.pack(side="left", fill="y")
        self.nav.pack_propagate(False)
        tk.Frame(self.root, bg=BORDER, width=1).pack(side="left", fill="y")
        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)
        self._build_nav()

        header = tk.Frame(self.main, bg=BG)
        header.pack(side="top", fill="x", padx=14, pady=(12, 8))

        self.title_label = tk.Label(
            header, text="待办", bg=BG, fg=TEXT, font=_font(self.font_size + 2, bold=True)
        )
        self.title_label.pack(side="left", padx=(0, 6))

        # Title row: title on the left, count and gear on the right.
        right = tk.Frame(header, bg=BG)
        right.pack(side="right")

        self.gear = tk.Button(
            right,
            text="⚙",
            relief="flat",
            bd=0,
            bg=BG,
            fg=MUTED,
            activebackground=BG,
            activeforeground=TEXT,
            cursor="hand2",
            padx=2,
            font=_font(self.font_size),
            command=self._open_font_settings,
        )
        self.gear.pack(side="right")

        self.lock_button = tk.Button(
            right, text="🔒" if self.locked else "🔓", relief="flat", bd=0, bg=BG,
            fg=MUTED, activebackground=BG, activeforeground=TEXT, cursor="hand2", padx=2,
            font=_font(max(8, self.font_size - 1)), command=self._toggle_lock,
        )
        self.lock_button.pack(side="right", padx=(0, 2))

        self.count = tk.Label(
            right, text="", bg=TODAY_BG, fg=ACCENT,
            font=_font(max(8, self.font_size - 1), bold=True), padx=8, pady=3,
        )
        self.count.pack(side="right", padx=(0, 7))

        self.clear_button = tk.Button(
            right, text="清除已完成", relief="flat", bd=0, bg=BG, fg=MUTED,
            activebackground=BG, activeforeground=DANGER, cursor="hand2", padx=4,
            font=_font(max(8, self.font_size - 1)),
            command=self._clear_done,
        )

        search_bar = tk.Frame(self.main, bg=BG)
        search_bar.pack(side="top", fill="x", padx=14, pady=(0, 6))
        tk.Label(
            search_bar, text="🔍", bg=BG, fg=MUTED, font=_font(max(8, self.font_size - 1))
        ).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            search_bar, textvariable=self.search_var, relief="flat", bg=CARD, fg=TEXT,
            insertbackground=TEXT, highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT, font=_font(max(8, self.font_size - 1)),
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(6, 0), ipady=3)
        self.search_entry.bind("<KeyRelease>", self._on_search_key)
        self.search_entry.bind("<Escape>", self._clear_search)
        self.search_clear_button = tk.Button(
            search_bar, text="×", relief="flat", bd=0, bg=BG, fg=MUTED,
            activebackground=BG, activeforeground=TEXT, cursor="hand2", padx=5,
            font=_font(self.font_size + 2), command=self._clear_search,
        )

        tk.Frame(self.main, bg=BORDER, height=1).pack(side="top", fill="x", padx=12)

        self.bottom = tk.Frame(self.main, bg=BG)
        self.bottom.pack(side="bottom", fill="x")

        self.status_bar = tk.Frame(self.bottom, bg=BG)
        self.status_bar.pack(side="bottom", fill="x", padx=14, pady=(0, 6))
        self.status = tk.Label(
            self.status_bar, text="", anchor="w", bg=BG, fg=MUTED,
            font=_font(max(8, self.font_size - 2)),
        )
        self.status.pack(side="left")
        self.undo_button = tk.Button(
            self.status_bar, text="撤销", command=self._undo, relief="flat", bd=0,
            bg=BG, fg=ACCENT, activebackground=BG, activeforeground=ACCENT_DARK,
            cursor="hand2", padx=4, font=_font(max(8, self.font_size - 2)),
        )

        self.composer = tk.Frame(self.bottom, bg=BG)
        self.composer.pack(side="bottom", fill="x", padx=12, pady=10)
        self.entry = tk.Entry(
            self.composer,
            relief="flat",
            bg=CARD,
            fg=TEXT,
            insertbackground=TEXT,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=_font(self.font_size),
        )
        self.entry.bind("<Return>", lambda event: self._add_from_entry())
        self.add_button = tk.Button(
            self.composer,
            text="添加",
            command=self._add_from_entry,
            relief="flat",
            bg=ACCENT_FILL,
            fg=ACCENT_TEXT,
            activebackground=ACCENT_DARK,
            activeforeground=ACCENT_TEXT,
            bd=0,
            padx=12,
            cursor="hand2",
            font=_font(self.font_size),
        )
        self.add_button.pack(side="right", padx=(8, 0))
        self.entry.pack(side="left", fill="x", expand=True, ipady=5)

        self.composer_sep = tk.Frame(self.bottom, bg=BORDER, height=1)
        self.composer_sep.pack(side="bottom", fill="x", padx=12)

        self.body = tk.Frame(self.main, bg=BG)
        self.body.pack(side="top", fill="both", expand=True, padx=8, pady=8)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Configure>", self._on_configure)
        self._apply_lock(announce=False)
        self._render()
        _hide_from_taskbar(self.root)
        self._apply_size()

    def _set_icon(self):
        try:
            path = getattr(self.controller, "icon_path", lambda: "")()
            if path and os.path.exists(path):
                self._icon = tk.PhotoImage(file=path)
                self.root.iconphoto(True, self._icon)
        except Exception:
            pass
        try:
            # The taskbar button uses the big icon; on Windows only an .ico
            # bitmap reliably sets it, iconphoto alone often leaves Python's.
            ico = getattr(self.controller, "icon_ico_path", lambda: "")()
            if ico and os.path.exists(ico):
                self._icon_bitmap = ico
                self.root.iconbitmap(ico)
        except Exception:
            pass

    def _build_nav(self):
        tk.Frame(self.nav, bg=BG, height=6).pack(fill="x")

        small = max(8, self.font_size - 1)
        self._filter_buttons = {}
        self._nav_filter_frames = {}
        self.nav_counts = {}
        self._nav_today_sub = None
        for key, label in NAV_FILTERS:
            item = tk.Frame(self.nav, bg=BG, cursor="hand2")
            item.pack(fill="x", padx=(10, 8), pady=1)
            bar = tk.Frame(item, width=3, bg=BG)
            bar.pack(side="left", fill="y")
            count = tk.Label(item, text="", bg=BG, fg=MUTED, font=_font(max(8, small - 1)))
            count.pack(side="right", padx=(0, 6))
            name = tk.Label(
                item, text=label, anchor="w", bg=BG, fg=TEXT,
                font=_font(small), padx=7, pady=5,
            )
            name.pack(side="left", fill="x", expand=True)
            for widget in (item, bar, name, count):
                widget.bind("<Button-1>", lambda event, k=key: self._set_filter(k))
            self._filter_buttons[key] = name
            self.nav_counts[key] = count
            self._nav_filter_frames[key] = {"frame": item, "bar": bar, "name": name, "count": count}
            if key == "today":
                self._nav_today_sub = tk.Label(
                    self.nav, text="", anchor="w", bg=BG, fg=MUTED,
                    font=_font(max(8, small - 1)),
                )
                self._nav_today_sub.pack(fill="x", padx=(24, 8), pady=(0, 4))

        tk.Frame(self.nav, bg=BORDER, height=1).pack(fill="x", padx=14, pady=8)
        self._nav_view_frames = {}
        for key, label in NAV_VIEWS:
            item = tk.Frame(self.nav, bg=BG, cursor="hand2")
            item.pack(fill="x", padx=(10, 8), pady=1)
            bar = tk.Frame(item, width=3, bg=BG)
            bar.pack(side="left", fill="y")
            name = tk.Label(
                item, text=label, anchor="w", bg=BG, fg=TEXT,
                font=_font(small), padx=7, pady=5,
            )
            name.pack(side="left", fill="x", expand=True)
            for widget in (item, bar, name):
                widget.bind("<Button-1>", lambda event, k=key: self._set_view(k))
            self._nav_view_frames[key] = {"frame": item, "bar": bar, "name": name}

    def _apply_size(self):
        width = self.config.get("window_width") or DEFAULT_WIDTH
        height = self.config.get("window_height") or DEFAULT_HEIGHT
        try:
            width = int(width)
        except (TypeError, ValueError):
            width = DEFAULT_WIDTH
        try:
            height = int(height)
        except (TypeError, ValueError):
            height = DEFAULT_HEIGHT
        width = max(MIN_WIDTH, width)
        height = max(MIN_HEIGHT, height)
        if width != self.config.get("window_width"):
            self.config["window_width"] = width
        if height != self.config.get("window_height"):
            self.config["window_height"] = height
        self.root.minsize(MIN_WIDTH, MIN_HEIGHT)
        x, y = self.config.get("window_x"), self.config.get("window_y")
        if isinstance(x, int) and isinstance(y, int):
            self.root.geometry("%dx%d+%d+%d" % (width, height, x, y))
        else:
            self.root.geometry("%dx%d" % (width, height))

    def _apply_alpha(self):
        try:
            self.root.attributes("-alpha", _alpha_value(self.transparency))
        except Exception:
            pass

    # -- navigation ------------------------------------------------------
    def _set_view(self, view):
        view = store.normalize_view(view)
        if view == "today":
            self._set_filter("today")
            return
        if view == "list":
            self.view = "list"
        else:
            self.view = view
        self.config["view"] = self.view
        self.controller.set_view(self.view)
        if self.locked:
            self._update_hook_rect()
        self._close_day_dialog()
        self._update_nav()
        self._render()

    def _set_filter(self, filter_key):
        filter_key = store.normalize_filter(filter_key)
        if filter_key == self.filter and self.view in ("list", "today"):
            return
        self.filter = filter_key
        self.view = "list"
        self.config["filter"] = filter_key
        self.config["view"] = "list"
        setter = getattr(self.controller, "set_filter", None)
        if setter is not None:
            setter(filter_key)
        view_setter = getattr(self.controller, "set_view", None)
        if view_setter is not None:
            view_setter("list")
        self._update_nav()
        self._render()

    def _update_nav(self):
        active_filter = self.filter if self.view in ("list", "today") else None
        small = max(8, self.font_size - 1)
        for key, parts in self._nav_filter_frames.items():
            active = key == active_filter
            parts["frame"].configure(bg=TODAY_BG if active else BG)
            parts["bar"].configure(bg=ACCENT if active else BG)
            parts["name"].configure(
                bg=ACCENT_FILL if active else BG,
                fg=ACCENT_TEXT if active else TEXT,
                activebackground=ACCENT_DARK if active else TODAY_BG,
                activeforeground=ACCENT_TEXT if active else ACCENT,
                font=_font(small, bold=active),
            )
            parts["count"].configure(
                bg=TODAY_BG if active else BG,
                fg=DANGER if key == "overdue" and parts["count"].cget("text") not in ("", "0") else MUTED,
                font=_font(max(8, small - 1), bold=active),
            )
        for key, parts in self._nav_view_frames.items():
            active = key == self.view
            parts["frame"].configure(bg=TODAY_BG if active else BG)
            parts["bar"].configure(bg=ACCENT if active else BG)
            parts["name"].configure(
                bg=ACCENT_FILL if active else BG,
                fg=ACCENT_TEXT if active else TEXT,
                activebackground=ACCENT_DARK if active else TODAY_BG,
                activeforeground=ACCENT_TEXT if active else ACCENT,
                font=_font(small, bold=active),
            )
        if self._nav_today_sub is not None:
            todos = self.controller.list()
            overdue = len(store.apply_filter(todos, "overdue"))
            today = date.today()
            due_today = sum(
                1 for todo in todos
                if store.effective_due_datetime(todo) is not None
                and store.effective_due_datetime(todo).date() == today
            )
            self._nav_today_sub.configure(
                text="逾期 %d · 今日到期 %d" % (overdue, due_today),
                fg=DANGER if overdue else MUTED,
            )

    def _update_nav_counts(self, todos):
        for key in ("today", "overdue", "all", "week7", "nodate", "done"):
            count = self.nav_counts.get(key)
            if count is not None:
                total = len(store.apply_filter(todos, key))
                count.configure(
                    text=str(total),
                    fg=DANGER if key == "overdue" and total else MUTED,
                )

    def _update_view_buttons(self):
        self._update_nav()

    def _update_filter_buttons(self):
        self._update_nav()

    def _apply_appearance(self, appearance, theme):
        old = _palette_snapshot()
        _set_palette(appearance, theme)
        new = _palette_snapshot()
        color_map = {}
        for key in _PALETTE_KEYS:
            previous = str(old[key]).upper()
            if previous not in color_map:
                color_map[previous] = new[key]
        _recolor_widget_tree(self.root, color_map)
        try:
            self.root.configure(bg=BG)
            self.style.configure(
                "Card.TCheckbutton", background=CARD, foreground=TEXT,
                font=_font(self.font_size),
            )
            self.style.map("Card.TCheckbutton", background=[("active", CARD)])
            self.add_button.configure(
                bg=ACCENT_FILL, fg=ACCENT_TEXT,
                activebackground=ACCENT_DARK, activeforeground=ACCENT_TEXT,
            )
        except Exception:
            pass
        self._update_view_buttons()
        self._update_filter_buttons()
        self._render()

    # -- font size -------------------------------------------------------
    def _apply_fonts(self):
        self.title_label.configure(font=_font(self.font_size + 2, bold=True))
        self.count.configure(font=_font(max(8, self.font_size - 1), bold=True))
        self.status.configure(font=_font(max(8, self.font_size - 2)))
        self.entry.configure(font=_font(self.font_size))
        self.add_button.configure(font=_font(self.font_size))
        self.gear.configure(font=_font(self.font_size))
        self.lock_button.configure(font=_font(max(8, self.font_size - 1)))
        self.search_entry.configure(font=_font(max(8, self.font_size - 1)))
        self.search_clear_button.configure(font=_font(self.font_size + 2))
        self.clear_button.configure(font=_font(max(8, self.font_size - 1)))
        self.undo_button.configure(font=_font(max(8, self.font_size - 2)))
        self._update_view_buttons()
        self._update_filter_buttons()
        try:
            self.style.configure("Card.TCheckbutton", background=CARD, font=_font(self.font_size))
        except Exception:
            pass

    def set_font_size(self, size):
        size = max(store.FONT_MIN, min(store.FONT_MAX, int(size)))
        self.font_size = size
        self.config["font_size"] = size
        self.controller.set_font_size(size)
        self._apply_fonts()
        self._render()

    def _set_compact(self, compact):
        self.compact = bool(compact)
        self.config["compact"] = self.compact
        setter = getattr(self.controller, "set_compact", None)
        if setter is not None:
            setter(self.compact)
        self._render()

    def set_transparency(self, value):
        value = max(50, min(100, int(value)))
        self.transparency = value
        self.config["transparency"] = value
        setter = getattr(self.controller, "set_transparency", None)
        if setter is not None:
            setter(value)
        self._apply_alpha()

    def _open_font_settings(self):
        dialog = self._font_dialog
        if dialog is not None:
            try:
                if dialog.top.winfo_exists():
                    dialog.top.lift()
                    dialog.top.focus_force()
                    return
            except Exception:
                pass
            self._font_dialog = None
        self._font_dialog = _FontSettings(self)

    def _open_reminder_picker(self, todo, mode="remind", on_saved=None):
        picker = self._reminder_picker
        if picker is not None:
            try:
                if picker.top.winfo_exists():
                    picker.top.lift()
                    picker.top.focus_force()
                    return picker
            except Exception:
                pass
            self._reminder_picker = None
        return _ReminderPicker(self, todo, mode=mode, on_saved=on_saved)

    def _open_stats(self):
        dialog = self._stats_dialog
        if dialog is not None:
            try:
                if dialog.top.winfo_exists():
                    dialog.top.lift()
                    dialog.top.focus_force()
                    return
            except Exception:
                pass
            self._stats_dialog = None
        self._stats_dialog = _StatsDialog(self)

    # -- lock / search / files ------------------------------------------
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
            _set_click_through(self.root, bool(hook_active))
            if announce:
                self.status.configure(text="已锁定 · Ctrl+双击解锁 · Ctrl+Alt+U 紧急解锁")
        else:
            _set_click_through(self.root, False)
            self._stop_hook()
            if announce:
                self.status.configure(text="")

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

    def _on_search_key(self, event=None):
        # Avoid rebuilding every row for every keystroke on larger lists.
        if self._search_job is not None:
            try:
                self.root.after_cancel(self._search_job)
            except Exception:
                pass
        self._search_job = self.root.after(110, self._apply_search)

    def _apply_search(self):
        self._search_job = None
        self.search = self.search_var.get()
        self._update_search_clear_button()
        self._render()

    def _update_search_clear_button(self):
        if self.search_var.get().strip():
            if self.search_clear_button.winfo_manager() != "pack":
                self.search_clear_button.pack(side="right", padx=(2, 0))
        else:
            self.search_clear_button.pack_forget()

    def _clear_search(self, event=None):
        if self._search_job is not None:
            try:
                self.root.after_cancel(self._search_job)
            except Exception:
                pass
            self._search_job = None
        self.search_var.set("")
        self.search = ""
        self._update_search_clear_button()
        self._render()
        return "break"

    def _export(self):
        from tkinter import filedialog

        suggested = "todos-%s.json" % datetime.now().strftime("%Y%m%d-%H%M")
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".json", initialfile=suggested,
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        try:
            count = store.export_todos(self.controller.data_dir, path)
        except Exception as exc:
            self.status.configure(text="导出失败：%s" % exc)
            return
        self.status.configure(text="已导出 %d 条到 %s" % (count, path))

    def _import(self):
        from tkinter import filedialog

        path = filedialog.askopenfilename(parent=self.root, filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            added, skipped = store.import_todos(self.controller.data_dir, path)
        except Exception as exc:
            self.status.configure(text="导入失败：%s" % exc)
            return
        reload_todos = getattr(self.controller, "reload_todos", None)
        if reload_todos is not None:
            reload_todos()
        elif hasattr(self.controller, "todos"):
            self.controller.todos = store.load_todos(self.controller.data_dir)
        self.status.configure(text="导入 %d 条，跳过 %d 条重复" % (added, skipped))
        self._render()

    # -- queue pump ------------------------------------------------------
    def pump(self):
        try:
            while True:
                self._dispatch(self.commands.get_nowait())
        except queue.Empty:
            pass
        try:
            hidden = str(self.root.state()) in ("withdrawn", "iconic")
        except Exception:
            hidden = False
        self.root.after(500 if hidden else 40, self.pump)

    def _dispatch(self, command):
        name = command[0]
        if name == "show":
            # Render while withdrawn, then map the already-painted window.
            # Rebuilding widgets after deiconify can expose an empty/black
            # frame for a moment on Windows DWM.
            was_hidden = str(self.root.state()) in ("withdrawn", "iconic")
            self._render()
            if was_hidden:
                self.root.deiconify()
                self.root.update_idletasks()
            _state["visible"] = True
            if self.locked:
                self._update_hook_rect()
                self._apply_lock(announce=False)
            else:
                self._set_topmost(False)
                _set_click_through(self.root, False)
                self._stop_hook()
            self._raise_window()
            if command[1]:
                self.root.after_idle(self.entry.focus_set)
        elif name == "hide":
            # The sidebar is part of the main window; hide it with the note.
            self._hide_detail_panel()
            # Restore normal z-order and mouse handling before withdrawing.
            _set_click_through(self.root, False)
            self._stop_hook()
            self._set_topmost(False)
            self.root.withdraw()
            _state["visible"] = False
        elif name == "refresh":
            self._render()
        elif name == "settings":
            self.settings = dict(command[1])
            if not isinstance(self.config.get("transparency"), int):
                self.transparency = max(50, min(100, int(self.settings.get("transparency", 95))))
            self._apply_alpha()
        elif name == "appearance":
            self._apply_appearance(command[1], command[2])
        elif name == "locked":
            self._set_locked_ui(bool(command[1]))
            self.controller.set_locked(self.locked)
        elif name == "compact":
            self._set_compact(bool(command[1]))
        elif name == "stats":
            self._open_stats()
        elif name == "export":
            self._export()
        elif name == "import":
            self._import()

    # -- rendering -------------------------------------------------------
    def _render(self):
        if self._editing_id:
            # Never redraw under an open editor: the text being typed would be
            # lost. Remember it and redraw once the edit is saved or cancelled.
            self._refresh_pending = True
            return
        previous_yview = None
        if self.view in ("list", "today"):
            try:
                if self.canvas is not None and self.canvas.winfo_exists():
                    previous_yview = self.canvas.yview()[0]
            except Exception:
                previous_yview = None
        for child in self.body.winfo_children():
            child.destroy()
        todos = self.controller.list()
        self._update_nav_counts(todos)
        if self.search.strip():
            todos = [todo for todo in todos if store.matches(todo, self.search)]
        if self.view in ("list", "today"):
            todos = store.apply_filter(todos, self.filter)

        open_count = sum(1 for todo in todos if not todo["done"])
        done_count = len(todos) - open_count
        self._show_composer(self.view in ("list", "today"))
        self.count.configure(text=("%d 条未完成" % open_count) if open_count else "全部完成")
        if done_count:
            if self.clear_button.winfo_manager() != "pack":
                self.clear_button.pack(side="right")
        else:
            self.clear_button.pack_forget()

        if self.view == "today":
            self._render_list(self._today_todos(todos), empty_text="今天没有待办")
        elif self.view == "week":
            self._render_week(todos)
        elif self.view == "month":
            self._render_month(todos)
        else:
            self._render_list(todos)
        self._update_nav()
        if previous_yview is not None:
            try:
                if self.canvas is not None and self.canvas.winfo_exists():
                    self.canvas.yview_moveto(previous_yview)
                    self._list_yview = previous_yview
            except Exception:
                pass
        if self.view not in ("list", "today"):
            self._row_pool = []
            self._row_widgets = {}
            self._row_actions = {}

    def _today_todos(self, todos):
        today = date.today()
        now = datetime.now().astimezone()
        result = []
        for todo in todos:
            include = today in store.occurrences(todo, today, today)
            remind = store.parse_dt(todo.get("remind_at"))
            if remind is not None and remind.date() == today:
                include = True
            due = store.effective_due_datetime(todo)
            if not todo["done"] and due is not None and due < now:
                include = True
            if include:
                result.append(todo)
        return result

    def _show_composer(self, visible):
        """The add bar belongs to the list-like views."""
        if visible:
            for widget, options in (
                (self.composer, {"padx": 12, "pady": 10}),
                (self.composer_sep, {"padx": 12}),
            ):
                if widget.winfo_manager() != "pack":
                    widget.pack(side="bottom", fill="x", **options)
        else:
            self.composer.pack_forget()
            self.composer_sep.pack_forget()

    # -- list view -------------------------------------------------------
    def _render_list(self, todos, empty_text="还没有待办"):
        if not todos:
            self._list_total = 0
            self._list_items = []
            empty = tk.Frame(self.body, bg=BG)
            empty.pack(fill="both", expand=True, pady=40)
            tk.Label(empty, text=empty_text, bg=BG, fg=MUTED, font=_font(self.font_size)).pack()
            tk.Label(
                empty, text="在下面输入一条，回车添加", bg=BG, fg=MUTED,
                font=_font(max(8, self.font_size - 1)),
            ).pack(pady=(4, 0))
            return
        self._render_virtual_list(todos)

    def _measure_row_height(self):
        try:
            scaling = round(float(self.root.tk.call("tk", "scaling")), 3)
        except Exception:
            scaling = 1.0
        key = (bool(self.compact), int(self.font_size), scaling)
        cached = self._row_height_cache.get(key)
        if cached:
            return cached
        sample = {
            "id": "measure", "text": "测量", "done": False, "remind_at": None,
            "reminded": False, "priority": 3, "tags": ["测"], "parent_id": None,
            "due_at": None,
        }
        row = _TaskRow(self, self.body)
        row.update(sample, compact=self.compact, font_size=self.font_size)
        self.body.update_idletasks()
        height = max(20, int(row.card.winfo_reqheight()))
        row.card.destroy()
        self._row_height_cache[key] = height
        return height

    def _measure_subtask_height(self):
        try:
            scaling = round(float(self.root.tk.call("tk", "scaling")), 3)
        except Exception:
            scaling = 1.0
        key = ("subtask", bool(self.compact), int(self.font_size), scaling)
        cached = self._row_height_cache.get(key)
        if cached:
            return cached
        frame = tk.Frame(self.body, bg=BG)
        entry = tk.Entry(frame, font=_font(self.font_size))
        entry.pack(fill="x", ipady=4)
        frame.update_idletasks()
        height = max(28, int(frame.winfo_reqheight()) + 4)
        frame.destroy()
        self._row_height_cache[key] = height
        return height

    def _render_virtual_list(self, todos):
        self._child_counts = {}
        for todo in todos:
            parent = todo.get("parent_id")
            if parent:
                total, done = self._child_counts.get(parent, (0, 0))
                self._child_counts[parent] = (total + 1, done + (1 if todo["done"] else 0))

        row_height = self._measure_row_height()
        subtask_height = self._measure_subtask_height()
        items = []
        offset = 0
        for todo in todos:
            items.append({"kind": "todo", "todo": todo, "height": row_height, "offset": offset})
            offset += row_height
            if self._subtask_parent == todo["id"]:
                items.append({
                    "kind": "subtask", "todo": todo, "height": subtask_height, "offset": offset,
                })
                offset += subtask_height
        self._list_items = items
        self._list_total = offset
        self._list_row_height = row_height

        canvas = tk.Canvas(self.body, bg=BG, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(self.body, orient="vertical", command=canvas.yview)
        rows = tk.Frame(canvas, bg=BG)
        rows.configure(height=max(1, offset), width=1)
        rows.pack_propagate(False)
        canvas_window = canvas.create_window((0, 0), window=rows, anchor="nw")
        self._canvas_window = canvas_window
        canvas.configure(yscrollcommand=lambda first, last: self._on_list_yscroll(first, last))
        canvas.bind("<Configure>", self._on_list_configure)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.bind("<MouseWheel>", self._on_wheel)
        canvas.configure(scrollregion=(0, 0, 1, max(1, offset)))
        scrollbar.state(["!disabled"] if offset > 1 else ["disabled"])
        self.scrollbar = scrollbar
        self.canvas = canvas
        self.rows = rows
        if self._list_update_id is not None:
            try:
                self.root.after_cancel(self._list_update_id)
            except Exception:
                pass
            self._list_update_id = None
        self._row_pool = []
        self._row_widgets = {}
        self._row_actions = {}
        self._row_items = {}
        self._subtask_item = None
        self._list_signature = None
        self._check_vars = []
        self._subtask_widget = None
        self._subtask_frame = None
        self._update_visible_rows()

    def _on_list_configure(self, event):
        try:
            width = max(1, int(event.width))
            self.canvas.itemconfigure(self._canvas_window, width=width)
            self.rows.configure(width=width)
            self.canvas.configure(scrollregion=(0, 0, width, max(1, self._list_total)))
        except Exception:
            return
        # Re-place the already-built cards immediately (cheap, no per-row
        # update); coalesce the full rebuild behind a short debounce.
        self._reflow_visible_rows(width)
        self._schedule_visible_update()

    def _schedule_visible_update(self):
        if self._list_update_id is not None:
            try:
                self.root.after_cancel(self._list_update_id)
            except Exception:
                pass
            self._list_update_id = None
        try:
            self._list_update_id = self.root.after(40, self._run_scheduled_visible_update)
        except Exception:
            self._list_update_id = None

    def _run_scheduled_visible_update(self):
        self._list_update_id = None
        self._update_visible_rows()

    def _place_card(self, card, item, width):
        todo = item["todo"]
        is_child = bool(todo.get("parent_id"))
        x = 18 if is_child else 0
        card_width = max(1, width - 18) if is_child else width
        card.place(x=x, y=item["offset"], width=card_width, height=item["height"])

    def _reflow_visible_rows(self, width):
        if self._list_updating or not getattr(self, "_row_widgets", None):
            return
        width = max(1, int(width))
        for todo_id, card in list(self._row_widgets.items()):
            item = self._row_items.get(todo_id)
            if item is not None:
                self._place_card(card, item, width)
        if self._subtask_widget is not None and self._subtask_item is not None:
            self._subtask_widget.place(
                x=18, y=self._subtask_item["offset"],
                width=max(1, width - 18), height=self._subtask_item["height"],
            )

    def _on_list_yscroll(self, first, last):
        try:
            self.scrollbar.set(first, last)
            self._list_yview = float(first)
        except Exception:
            pass
        if not self._list_updating:
            self._update_visible_rows()

    def _ensure_subtask_widget(self):
        if self._subtask_widget is not None:
            return
        frame = tk.Frame(self.rows, bg=BG)
        entry = tk.Entry(
            frame, relief="flat", bg=CARD, fg=TEXT, insertbackground=TEXT,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
            font=_font(self.font_size),
        )
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<Return>", lambda event: self._create_subtask(self._subtask_parent, entry))
        entry.bind("<Escape>", lambda event: self._cancel_subtask())
        self._subtask_widget = frame
        self._subtask_frame = frame
        self._subtask_entry = entry

    def _update_visible_rows(self):
        if self._list_updating:
            return
        if not hasattr(self, "canvas") or not self._list_items:
            return
        try:
            if not self.canvas.winfo_exists():
                return
        except Exception:
            return
        width = max(1, self.canvas.winfo_width())
        if width <= 1:
            width = max(1, self.rows.winfo_width())
        top = self.canvas.canvasy(0)
        view = max(1, self.canvas.winfo_height())
        buffer = 4 * max(1, getattr(self, "_list_row_height", 30))
        first_y = max(0, top - buffer)
        last_y = top + view + buffer
        visible = [
            item for item in self._list_items
            if item["offset"] + item["height"] >= first_y and item["offset"] <= last_y
        ]
        signature = tuple((item["kind"], item["todo"]["id"]) for item in visible)
        if signature == self._list_signature:
            return
        self._list_updating = True
        try:
            pool_index = 0
            self._row_widgets = {}
            self._row_actions = {}
            self._row_items = {}
            self._check_vars = []
            subtask_item = None
            for item in visible:
                if item["kind"] == "subtask":
                    subtask_item = item
                    continue
                while len(self._row_pool) <= pool_index:
                    self._row_pool.append(_TaskRow(self, self.rows))
                row = self._row_pool[pool_index]
                todo = item["todo"]
                selected = todo["id"] == getattr(self, "_selected_todo_id", None)
                row.update(todo, compact=self.compact, font_size=self.font_size, selected=selected)
                self._place_card(row.card, item, width)
                self._row_widgets[todo["id"]] = row.card
                self._row_actions[todo["id"]] = row.actions
                self._row_items[todo["id"]] = item
                self._check_vars.append(row.variable)
                pool_index += 1
            for row in self._row_pool[pool_index:]:
                row.card.place_forget()

            self._subtask_item = subtask_item
            if self._subtask_parent is not None:
                self._ensure_subtask_widget()
                item = next(
                    (entry for entry in self._list_items if entry["kind"] == "subtask"),
                    subtask_item,
                )
                if item is not None:
                    self._subtask_item = item
                    self._subtask_widget.place(
                        x=18, y=item["offset"], width=max(1, width - 18), height=item["height"]
                    )
                    self._subtask_widget.lift()
                    if getattr(self, "_subtask_needs_focus", False):
                        self._subtask_needs_focus = False
                        self._subtask_entry.focus_set()
                else:
                    self._subtask_widget.place_forget()
            elif self._subtask_widget is not None:
                self._subtask_widget.place_forget()
            self._list_signature = signature
        finally:
            self._list_updating = False

    def _open_details(self, todo):
        self._ensure_detail_panel()
        self._show_detail_panel()
        self._details_panel.present(todo)
        self._selected_todo_id = todo["id"]
        self._details_dialog = self._details_panel

    def _ensure_detail_panel(self):
        if self._detail_frame is not None:
            return
        self._detail_frame = tk.Frame(
            self.root, bg=BG, width=DETAIL_WIDTH,
            highlightbackground=BORDER, highlightthickness=1,
        )
        self._detail_frame.pack_propagate(False)
        self._details_panel = _DetailsPanel(self, self._detail_frame)

    def _show_detail_panel(self):
        if self._detail_visible:
            return
        width = self.root.winfo_width()
        if width <= 1:
            width = self.config.get("window_width") or DEFAULT_WIDTH
        try:
            width = int(width)
        except (TypeError, ValueError):
            width = DEFAULT_WIDTH
        self._detail_base_width = max(MIN_WIDTH, width)
        self._detail_visible = True
        self._detail_frame.pack(side="right", fill="y", before=self.main)
        target = min(self._detail_base_width + DETAIL_WIDTH, self.root.winfo_screenwidth())
        height = self.root.winfo_height()
        if height <= 1:
            height = self.config.get("window_height") or DEFAULT_HEIGHT
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.geometry("%dx%d+%d+%d" % (target, int(height), x, y))

    def _hide_detail_panel(self):
        if not self._detail_visible:
            return
        self._detail_frame.pack_forget()
        self._detail_visible = False
        self._selected_todo_id = None
        self._details_dialog = None
        width = self._detail_base_width or self.config.get("window_width") or DEFAULT_WIDTH
        height = self.root.winfo_height()
        if height <= 1:
            height = self.config.get("window_height") or DEFAULT_HEIGHT
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.geometry("%dx%d+%d+%d" % (max(MIN_WIDTH, int(width)), int(height), x, y))
        self._render()

    def _select_todo(self, todo):
        self._open_details(todo)
        for row in list(self._row_pool):
            if row.todo is not None:
                try:
                    row.set_selected(row.todo["id"] == todo["id"])
                except Exception:
                    pass

    def _activate_row(self, row):
        self._active_row = row
        for other in self._row_pool:
            if other is not row:
                try:
                    other.actions.pack_forget()
                except Exception:
                    pass

    def _add_row(self, rows, todo):
        compact = self.compact
        font_size = max(8, self.font_size - (1 if compact else 0))
        small = max(8, font_size - 1)
        pad_y = 2 if compact else 6
        indent = (18, 2) if todo.get("parent_id") else 2
        card = tk.Frame(
            rows, bg=CARD, highlightbackground=BORDER, highlightthickness=1,
            takefocus=1,
        )
        card.pack(fill="x", pady=pad_y, padx=indent)
        self._row_widgets[todo["id"]] = card
        if (
            getattr(self, "_focus_todo_id", None) == todo["id"]
            or getattr(self, "_selected_todo_id", None) == todo["id"]
        ):
            card.configure(highlightbackground=ACCENT, highlightthickness=2)

        variable = tk.BooleanVar(value=bool(todo["done"]))
        self._check_vars.append(variable)
        ttk.Checkbutton(
            card, variable=variable, style="Card.TCheckbutton", cursor="hand2",
            command=lambda todo_id=todo["id"]: self._toggle(todo_id),
        ).pack(side="left", padx=(6, 0))

        actions = tk.Frame(card, bg=CARD)
        self._row_actions[todo["id"]] = actions

        def action(text, command, danger=False):
            button = tk.Button(
                actions, text=text, width=2, relief="flat", bd=0, bg=CARD,
                fg=DANGER if danger else MUTED, activebackground=CARD,
                activeforeground=DANGER if danger else ACCENT, cursor="hand2",
                font=_font(small), command=command,
            )
            button.pack(side="right")
            return button

        action("✕", lambda todo_id=todo["id"]: self._delete(todo_id), danger=True)
        action("···", lambda todo=todo: self._open_details(todo))
        action("⏰", lambda todo=todo: self._open_reminder_picker(todo))
        if not todo.get("parent_id"):
            action("＋", lambda todo_id=todo["id"]: self._start_subtask(todo_id))
        actions.pack_forget()

        middle = tk.Frame(card, bg=CARD)
        middle.pack(side="left", fill="x", expand=True, pady=(2 if compact else 4), padx=(2, 4))

        done = bool(todo["done"])
        priority = int(todo.get("priority") or 0)
        prefix = "📌 " if todo.get("pinned") else ""
        title = tk.Label(
            middle, text=prefix + todo["text"], anchor="w", justify="left",
            bg=CARD, fg=MUTED if done else TEXT,
            font=_font(font_size, overstrike=done, bold=priority >= 2 and not compact),
            wraplength=0,
        )
        if compact:
            title.pack(side="left", anchor="w")
        else:
            title.pack(fill="x", anchor="w")

        meta = tk.Frame(middle, bg=CARD)
        if compact:
            meta.pack(side="left", fill="x", expand=True, padx=(8, 0))
        else:
            meta.pack(fill="x", pady=(2, 0))

        def meta_label(text, color, size=small, bold=False):
            label = tk.Label(
                meta, text=text, anchor="w", bg=CARD, fg=color,
                font=_font(size, bold=bold),
            )
            label.pack(side="left")
            label.bind("<Button-1>", lambda event, todo=todo: self._select_todo(todo))
            return label

        if todo.get("tags"):
            meta_label(" ".join("#" + tag for tag in todo["tags"]), MUTED)
        if todo.get("note"):
            meta_label("📝", MUTED)
        due = store.effective_due(todo)
        due_dt = store.effective_due_datetime(todo)
        if due:
            now = datetime.now().astimezone()
            if done:
                due_color = MUTED
            elif due_dt is not None and due_dt < now:
                due_color = DANGER
            else:
                due_color = TEXT
            due_text = "截止 " + (_short_when(due) or store.format_reminder(due))
            repeat = store.format_repeat(todo.get("repeat"))
            if repeat:
                due_text += " · " + repeat
            meta_label(due_text, due_color)
        remind_dt = store.parse_dt(todo.get("remind_at"))
        if remind_dt is not None and (due_dt is None or remind_dt != due_dt):
            meta_label("提醒 " + _short_when(todo["remind_at"]), CLOCK)
        total, done_children = self._child_counts.get(todo["id"], (0, 0))
        if total:
            meta_label("子任务 %d/%d" % (done_children, total), MUTED)
        if priority in PRIORITY_COLORS:
            pill = tk.Label(
                meta, text=PRIORITY_LABELS[priority], bg=PRIORITY_COLORS[priority],
                fg="#FFFFFF", padx=5, pady=0, font=_font(max(8, font_size - 1), bold=True),
            )
            pill.pack(side="left", padx=(6, 0))

        title.bind("<Double-Button-1>", lambda event, todo=todo: self._open_details(todo))
        middle.bind("<Double-Button-1>", lambda event, todo=todo: self._open_details(todo))
        title.bind("<Button-1>", lambda event, todo=todo: self._select_todo(todo))
        middle.bind("<Button-1>", lambda event, todo=todo: self._select_todo(todo))

        def open_on_double(event, todo=todo):
            if event.widget.winfo_class() in ("TCheckbutton", "Checkbutton", "Button"):
                return
            self._open_details(todo)

        def pick_on_click(event, todo=todo):
            if event.widget.winfo_class() in ("TCheckbutton", "Checkbutton", "Button"):
                return
            self._select_todo(todo)

        card.bind("<Double-Button-1>", open_on_double)
        card.bind("<Button-1>", pick_on_click)
        self._bind_hover(card, actions)

    def _start_subtask(self, parent_id):
        self._subtask_parent = parent_id
        self._subtask_needs_focus = True
        self._render()

    def _add_subtask_entry(self, rows, parent):
        frame = tk.Frame(rows, bg=BG)
        frame.pack(fill="x", pady=2, padx=(18, 2))
        entry = tk.Entry(
            frame, relief="flat", bg=CARD, fg=TEXT, insertbackground=TEXT,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
            font=_font(self.font_size),
        )
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<Return>", lambda event: self._create_subtask(parent["id"], entry))
        entry.bind("<Escape>", lambda event: self._cancel_subtask())
        entry.focus_set()
        self._subtask_entry = entry

    def _create_subtask(self, parent_id, entry):
        text = entry.get().strip()
        self._subtask_parent = None
        self._subtask_entry = None
        if text:
            self.controller.add(text, parent_id=parent_id)
        self._render()

    def _cancel_subtask(self):
        self._subtask_parent = None
        self._subtask_entry = None
        self._render()

    # -- week view -------------------------------------------------------
    def _render_week(self, todos):
        start = self._week_start
        end = start + timedelta(days=6)
        small = max(8, self.font_size - 1)

        self._add_calendar_note(todos)

        nav = tk.Frame(self.body, bg=BG)
        nav.pack(fill="x", pady=(0, 6))
        self._nav_button(nav, "‹", lambda: self._shift_week(-1)).pack(side="left")
        self._nav_button(nav, "本周", lambda: self._go_week(0)).pack(side="left", padx=4)
        self._nav_button(nav, "›", lambda: self._shift_week(1)).pack(side="left")
        tk.Label(
            nav, text="%d/%d – %d/%d" % (start.month, start.day, end.month, end.day),
            bg=BG, fg=MUTED, font=_font(small),
        ).pack(side="right")

        grid = tk.Frame(self.body, bg=BG)
        grid.pack(fill="both", expand=True)
        by_day = self._occurrence_map(todos, start, end)
        today = date.today()

        for column in range(7):
            grid.columnconfigure(column, weight=1, uniform="week")
            day = start + timedelta(days=column)
            cell_bg = TODAY_BG if day == today else BG
            cell = tk.Frame(grid, bg=cell_bg, highlightbackground=BORDER, highlightthickness=1)
            cell.grid(row=0, column=column, sticky="nsew", padx=2, pady=2)
            tk.Label(cell, text="周" + store.WEEKDAYS[column], bg=cell_bg, fg=MUTED, font=_font(small)).pack(
                anchor="w", padx=4, pady=(4, 0)
            )
            tk.Label(
                cell, text="%d/%d" % (day.month, day.day), bg=cell_bg, fg=TEXT,
                font=_font(small, bold=True),
            ).pack(anchor="w", padx=4)
            for todo in by_day.get(day, []):
                self._calendar_label(cell, todo, cell_bg, width=70)
        grid.rowconfigure(0, weight=1)

    def _shift_week(self, delta):
        self._week_start += timedelta(weeks=delta)
        self._render()

    def _go_week(self, _):
        today = date.today()
        self._week_start = today - timedelta(days=today.weekday())
        self._render()

    # -- month view ------------------------------------------------------
    def _render_month(self, todos):
        small = max(8, self.font_size - 1)
        self._add_calendar_note(todos)
        nav = tk.Frame(self.body, bg=BG)
        nav.pack(fill="x", pady=(0, 6))
        self._nav_button(nav, "‹", lambda: self._shift_month(-1)).pack(side="left")
        self._nav_button(nav, "本月", lambda: self._go_month()).pack(side="left", padx=4)
        self._nav_button(nav, "›", lambda: self._shift_month(1)).pack(side="left")
        tk.Label(
            nav, text="%d 年 %d 月" % (self._month_year, self._month_month),
            bg=BG, fg=TEXT, font=_font(small, bold=True),
        ).pack(side="right")

        grid = tk.Frame(self.body, bg=BG)
        grid.pack(fill="both", expand=True)

        for column in range(7):
            grid.columnconfigure(column, weight=1, uniform="month")
            tk.Label(
                grid, text=store.WEEKDAYS[column], bg=BG, fg=MUTED, font=_font(small),
            ).grid(row=0, column=column, sticky="ew")
        weeks = _month_grid(self._month_year, self._month_month)
        used_rows = 6 if any(weeks[5]) else 5
        self._month_day_cells = []
        for row in range(1, used_rows + 1):
            grid.rowconfigure(row, weight=1, uniform="monthrow")

        days = [day for week in weeks for day in week if day]
        start = date(self._month_year, self._month_month, min(days)) if days else date.today()
        end = date(self._month_year, self._month_month, max(days)) if days else date.today()
        by_day = self._occurrence_map(todos, start, end)
        today = date.today()

        for row_index, week in enumerate(weeks, start=1):
            if row_index > used_rows:
                break
            for column, day in enumerate(week):
                if day is None:
                    # Blank days (before the 1st or after month end) stay
                    # unframed: only real dates get a bordered cell.
                    spacer = tk.Frame(grid, bg=BG)
                    spacer.grid(row=row_index, column=column, sticky="nsew", padx=1, pady=1)
                    continue
                cell_bg = BG
                cell = tk.Frame(grid, bg=BG, highlightbackground=BORDER, highlightthickness=1)
                cell.grid(row=row_index, column=column, sticky="nsew", padx=1, pady=1)
                self._month_day_cells.append(cell)
                current = date(self._month_year, self._month_month, day)
                if current == today:
                    cell.configure(bg=TODAY_BG)
                    cell_bg = TODAY_BG
                entries = by_day.get(current, [])

                # Day number and the "+N" link share one line: the cell is only
                # ~50px tall, which leaves room for exactly two titles below.
                top_row = tk.Frame(cell, bg=cell_bg)
                top_row.pack(fill="x", padx=3, pady=(0, 0))
                day_label = tk.Label(
                    top_row, text=str(day), bg=cell_bg, fg=ACCENT if current == today else TEXT,
                    font=_font(max(8, small - 2), bold=current == today),
                )
                day_label.pack(side="left")
                for widget in (cell, top_row, day_label):
                    widget.bind(
                        "<Button-1>",
                        lambda event, day=current, items=list(entries): self._open_day(day, items),
                    )
                if len(entries) > 2:
                    tk.Button(
                        top_row, text="+%d 更多" % (len(entries) - 2), bg=cell_bg, fg=ACCENT,
                        activebackground=cell_bg, activeforeground=ACCENT, relief="flat", bd=0,
                        cursor="hand2", font=_font(max(8, small - 2)), padx=0,
                        command=lambda day=current, items=list(entries): self._open_day(day, items),
                    ).pack(side="right")

                for todo in entries[:2]:
                    self._calendar_label(
                        cell, todo, cell_bg, width=86,
                        font_size=max(8, small - 1), pady=(0, 0),
                    )

    def _shift_month(self, delta):
        month = self._month_month + delta
        year = self._month_year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        self._month_year, self._month_month = year, month
        self._render()

    def _go_month(self):
        today = date.today()
        self._month_year, self._month_month = today.year, today.month
        self._render()

    # -- shared calendar bits -------------------------------------------
    def _occurrence_map(self, todos, start, end):
        mapping = {}
        for todo in todos:
            for day in store.occurrences(todo, start, end):
                mapping.setdefault(day, []).append(todo)
        return mapping

    def _calendar_label(self, parent, todo, cell_bg, width, font_size=None, on_toggle=None,
                        pady=(2, 0), label_width=None):
        options = {"width": label_width} if label_width else {}
        label = tk.Label(
            parent,
            text=todo["text"],
            bg=cell_bg,
            anchor="w",
            justify="left",
            wraplength=width,
            cursor="hand2",
            fg=MUTED if todo["done"] else TEXT,
            font=_font(font_size or max(8, self.font_size - 1), overstrike=todo["done"]),
            **options,
        )
        label.pack(anchor="w", padx=4, pady=pady)
        callback = on_toggle or self._toggle
        label.bind("<Button-1>", lambda event, todo_id=todo["id"]: callback(todo_id))
        return label

    def _open_day(self, day, entries):
        self._ensure_detail_panel()
        self._show_detail_panel()
        self._details_panel.show_day(day, entries)

    def _navigate_to_todo(self, todo_id):
        """Jump from a calendar day to the normal list and reveal one task."""
        self._close_day_dialog()
        self.view = "list"
        self.config["view"] = "list"
        self.controller.set_view("list")
        if self.search_var.get():
            self.search_var.set("")
        self.search = ""
        self._update_search_clear_button()
        self._update_view_buttons()
        self._focus_todo_id = todo_id
        self._render()
        # Complete layout before calculating the row's y-position. Doing this
        # synchronously also avoids leaving a stale after_idle callback behind
        # if the note closes immediately after the calendar popup.
        self.root.update_idletasks()
        self._reveal_todo(todo_id)

    def _reveal_todo(self, todo_id):
        card = self._row_widgets.get(todo_id)
        canvas = getattr(self, "canvas", None)
        rows = getattr(self, "rows", None)
        if card is None or canvas is None or rows is None:
            return
        try:
            self.root.update_idletasks()
            total = max(1, rows.winfo_height())
            y = max(0, card.winfo_y() - 8)
            canvas.yview_moveto(min(1.0, y / total))
            card.configure(highlightbackground=ACCENT, highlightthickness=2)
            self._focus_todo_id = None
        except Exception:
            pass

    def _close_day_dialog(self):
        if self._details_panel is not None and getattr(self._details_panel, "mode", None) == "day":
            self._hide_detail_panel()

    def _add_calendar_note(self, todos):
        without = [todo for todo in todos if not todo.get("remind_at")]
        if not without:
            return
        tk.Label(
            self.body,
            text="另有 %d 条无提醒待办，仅在列表视图显示" % len(without),
            bg=BG,
            fg=MUTED,
            font=_font(max(8, self.font_size - 1)),
        ).pack(side="bottom", fill="x", pady=(6, 0))

    def _nav_button(self, parent, text, command):
        return tk.Button(
            parent, text=text, command=command, relief="flat", bd=0, bg=BG, fg=TEXT,
            activebackground=BG, activeforeground=ACCENT, cursor="hand2", padx=6,
            font=_font(max(8, self.font_size - 1)),
        )

    # -- hover -----------------------------------------------------------
    def _sync_scroll(self, canvas, scrollbar):
        """Set the virtual list height explicitly; bbox only contains visible rows."""
        total = getattr(self, "_list_total", 0)
        view = canvas.winfo_height()
        if total <= 0:
            bbox = canvas.bbox("all")
            if not bbox:
                return
            total = bbox[3]
        canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), max(total, view)))
        if _needs_scroll(total, view):
            scrollbar.state(["!disabled"])
        else:
            canvas.yview_moveto(0)
            scrollbar.state(["disabled"])

    def _on_wheel(self, event):
        canvas = getattr(self, "canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        total = getattr(self, "_list_total", 0)
        if total <= 0:
            bbox = canvas.bbox("all")
            if not bbox:
                return
            total = bbox[3]
        if not _needs_scroll(total, canvas.winfo_height()):
            return
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _bind_hover(self, card, actions=None):
        def show(_event=None):
            try:
                card.configure(highlightbackground=HOVER)
                if actions is not None and actions.winfo_manager() != "pack":
                    actions.pack(side="right", fill="y")
            except Exception:
                pass

        def hide(event=None):
            try:
                if event is not None:
                    widget = card.winfo_containing(event.x_root, event.y_root)
                    while widget is not None:
                        if widget is card:
                            return
                        widget = widget.master
                if actions is not None:
                    actions.pack_forget()
                card.configure(highlightbackground=BORDER)
            except Exception:
                pass

        stack = [card]
        while stack:
            widget = stack.pop()
            widget.bind("<Enter>", show, add="+")
            widget.bind("<Leave>", hide, add="+")
            # The pointer sits on a label or button, not the canvas, so the
            # wheel event never reaches the canvas without these bindings.
            widget.bind("<MouseWheel>", self._on_wheel, add="+")
            stack.extend(widget.winfo_children())
        def focus_out(_event=None):
            try:
                widget = self.root.focus_get()
                while widget is not None:
                    if widget is card:
                        return
                    widget = widget.master
            except Exception:
                pass
            hide(None)

        card.bind("<FocusIn>", show, add="+")
        card.bind("<FocusOut>", focus_out, add="+")

    # -- edits -----------------------------------------------------------
    def _add_from_entry(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.controller.add(text)
        self.entry.delete(0, "end")
        self._render()

    def _toggle(self, todo_id):
        self.controller.toggle(todo_id)
        self._render()

    def _delete(self, todo_id):
        if self.controller.delete(todo_id):
            self._render()
            self._show_undo(1)

    def _clear_done(self):
        count = self.controller.clear_done()
        if count:
            self._render()
            self._show_undo(count)

    def _show_undo(self, count):
        self.status.configure(text="已删除 %d 条" % count)
        if self.undo_button.winfo_manager() != "pack":
            self.undo_button.pack(side="right")
        if self._undo_job is not None:
            self.root.after_cancel(self._undo_job)
        self._undo_job = self.root.after(5000, self._hide_undo)

    def _hide_undo(self):
        self._undo_job = None
        self.status.configure(text="")
        self.undo_button.pack_forget()

    def _undo(self):
        if self.controller.undo_delete():
            self._render()
        self._hide_undo()

    # -- inline text editing ---------------------------------------------
    def _start_edit(self, todo, middle, label):
        if self._editing_id is not None:
            return
        self._editing_id = todo["id"]
        # Replace the whole text block (title + deadline) with the editor; the
        # row is rebuilt from data on save/cancel, so the deadline returns.
        for child in middle.winfo_children():
            child.destroy()
        self._edit_entry = tk.Entry(
            middle, relief="flat", bg=CARD, fg=TEXT, insertbackground=TEXT,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
            font=_font(self.font_size),
        )
        self._edit_entry.insert(0, todo["text"])
        self._edit_entry.pack(fill="x", anchor="w")
        self._edit_entry.select_range(0, "end")
        self._edit_entry.focus_set()
        self._edit_entry.bind("<Return>", lambda event: self._save_edit())
        self._edit_entry.bind("<Escape>", lambda event: self._cancel_edit())

    def _finish_edit(self):
        entry = self._edit_entry
        self._editing_id = None
        self._edit_entry = None
        if entry is not None:
            try:
                entry.destroy()
            except Exception:
                pass

    def _save_edit(self):
        if self._editing_id is None:
            return
        text = self._edit_entry.get().strip()
        todo_id = self._editing_id
        self._finish_edit()
        if text:
            self.controller.edit_text(todo_id, text)
        self._render()

    def _cancel_edit(self):
        if self._editing_id is None:
            return
        self._finish_edit()
        self._render()

    # -- window state ----------------------------------------------------
    def _on_close(self):
        """X hides the note, the same way QQ/WeChat minimize to the tray.

        Hiding loses no data (todos live in todos.json) and the process keeps
        running, so reminders and the hotkey still work; only quitting would
        stop them, and this button does not quit. No confirm box needed.
        """
        self._hide_detail_panel()
        _set_click_through(self.root, False)
        self._stop_hook()
        self._set_topmost(False)
        self.root.withdraw()
        self.status.configure(text="")
        _state["visible"] = False

    def _on_configure(self, event):
        if event.widget is not self.root:
            return
        if self.locked:
            self._update_hook_rect()
        if self._geometry_job is not None:
            self.root.after_cancel(self._geometry_job)
        self._geometry_job = self.root.after(800, self._save_geometry)

    def _save_geometry(self):
        self._geometry_job = None
        try:
            # Do not persist the screen-sized geometry while maximized; retain
            # the last useful normal size so the next launch is predictable.
            if self.root.state() == "zoomed":
                return
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            if self._detail_visible and self._detail_base_width:
                width = self._detail_base_width
            if width < 240 or height < 260:
                return
            self.controller.save_window_position(
                self.root.winfo_x(), self.root.winfo_y(), width, height
            )
            self.config["window_width"] = width
            self.config["window_height"] = height
        except Exception:
            pass


class _FontSettings:
    """The gear dialog: one read-only font size, saved into config.json."""

    def __init__(self, window):
        self.window = window
        small = max(8, window.font_size - 1)
        self.top = tk.Toplevel(window.root)
        self.top.title("界面设置")
        self.top.configure(bg=BG)
        self.top.transient(window.root)
        self.top.resizable(False, False)
        self.top.grab_set()

        tk.Label(self.top, text="界面字号", bg=BG, fg=MUTED, font=_font(small)).pack(
            anchor="w", padx=12, pady=(12, 4)
        )
        self.combo = ttk.Combobox(
            self.top,
            state="readonly",
            width=5,
            values=[str(value) for value in range(store.FONT_MIN, store.FONT_MAX + 1)],
        )
        self.combo.set(str(window.font_size))
        self.combo.pack(anchor="w", padx=12)

        self.compact_var = tk.BooleanVar(value=bool(window.compact))
        tk.Checkbutton(
            self.top, text="紧凑模式（单行，字号小一号）", variable=self.compact_var,
            bg=BG, fg=TEXT, activebackground=BG, activeforeground=TEXT,
            selectcolor=CARD, anchor="w", font=_font(small), cursor="hand2",
        ).pack(anchor="w", padx=8, pady=(10, 0))

        opacity_row = tk.Frame(self.top, bg=BG)
        opacity_row.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(opacity_row, text="窗口透明度", bg=BG, fg=MUTED, font=_font(small)).pack(side="left")
        self.transparency_value = tk.Label(
            opacity_row, text="%d%%" % window.transparency, bg=BG, fg=TEXT, font=_font(small),
        )
        self.transparency_value.pack(side="right")
        self.transparency = tk.IntVar(value=int(window.transparency))
        scale = ttk.Scale(
            self.top, from_=50, to=100, orient="horizontal", variable=self.transparency,
            command=self._transparency_changed,
        )
        scale.pack(fill="x", padx=12, pady=(2, 0))

        buttons = tk.Frame(self.top, bg=BG)
        buttons.pack(fill="x", padx=12, pady=(12, 12))
        tk.Button(
            buttons, text="取消", command=self.close, relief="flat", bd=0, bg=BG,
            fg=TEXT, activebackground=BG, cursor="hand2", font=_font(window.font_size),
        ).pack(side="right")
        tk.Button(
            buttons, text="保存", command=self._save, relief="flat", bg=ACCENT, fg="#FFFFFF",
            activebackground=ACCENT_DARK, activeforeground="#FFFFFF", bd=0, padx=12,
            cursor="hand2", font=_font(window.font_size),
        ).pack(side="right", padx=(0, 6))

        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda event: self.close())
        _place_dialog(self.top, window.root)
        window._font_dialog = self

    def close(self):
        try:
            self.top.destroy()
        except Exception:
            pass
        if getattr(self.window, "_font_dialog", None) is self:
            self.window._font_dialog = None

    def _transparency_changed(self, value):
        try:
            percent = max(50, min(100, int(round(float(value)))))
        except (TypeError, ValueError):
            return
        self.transparency_value.configure(text="%d%%" % percent)

    def _save(self):
        try:
            size = int(self.combo.get())
        except (TypeError, ValueError):
            return
        self.window._set_compact(self.compact_var.get())
        self.window.set_transparency(int(round(self.transparency.get())))
        self.window.set_font_size(size)
        self.close()


class _ReminderPicker:
    """A small calendar plus hour/minute pickers and the repeat choice."""

    WEEKDAYS = store.WEEKDAYS

    def __init__(self, window, todo, mode="remind", on_saved=None):
        self.window = window
        self.todo = todo
        self.mode = "due" if mode == "due" else "remind"
        self.on_saved = on_saved
        anchor = todo.get("due_at") if self.mode == "due" else todo.get("remind_at")
        if anchor:
            try:
                when = datetime.fromisoformat(anchor)
            except ValueError:
                when = self._default_when()
        else:
            when = self._default_when()
        self.selected = when.date()
        self.year, self.month = when.year, when.month
        self.repeat = store.normalize_repeat(todo.get("repeat"))
        self._build(when)

    @staticmethod
    def _default_when():
        tomorrow = date.today() + timedelta(days=1)
        return datetime(tomorrow.year, tomorrow.month, tomorrow.day, 9, 0)

    def _build(self, when):
        small = max(8, self.window.font_size - 1)
        self.top = tk.Toplevel(self.window.root)
        self.top.title("截止日期" if self.mode == "due" else "提醒")
        self.top.configure(bg=BG)
        self.top.transient(self.window.root)
        self.top.resizable(False, False)
        self.top.grab_set()
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda event: self.close())

        head = tk.Frame(self.top, bg=BG)
        head.pack(fill="x", padx=12, pady=(12, 4))
        self._flat(head, "‹", self._prev_month, small).pack(side="left")
        self.title = tk.Label(head, bg=BG, fg=TEXT, font=_font(small + 1, bold=True))
        self.title.pack(side="left", expand=True)
        self._flat(head, "›", self._next_month, small).pack(side="right")

        week = tk.Frame(self.top, bg=BG)
        week.pack(padx=12)
        self._week_labels = []
        for column, name in enumerate(self.WEEKDAYS):
            label = tk.Label(week, text=name, bg=BG, fg=MUTED, width=3, font=_font(small))
            label.grid(row=0, column=column)
            self._week_labels.append(label)

        self.grid = tk.Frame(self.top, bg=BG)
        self.grid.pack(padx=12, pady=(2, 8))

        # The 42 day cells are created once; changing month or selection only
        # rewrites their text and colours, so the dialog never flickers.
        self._cells = []
        for row_index in range(6):
            row = []
            for column in range(7):
                button = tk.Button(
                    self.grid, text="", width=3, relief="flat", bd=0,
                    bg=BG, fg=TEXT, activebackground=ACCENT, activeforeground="#FFFFFF",
                    cursor="hand2", font=_font(small),
                )
                button.grid(row=row_index, column=column, pady=1)
                row.append(button)
            self._cells.append(row)

        time_row = tk.Frame(self.top, bg=BG)
        time_row.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(time_row, text="时间", bg=BG, fg=MUTED, font=_font(small)).pack(side="left", padx=(0, 6))
        self.hour = tk.StringVar(value="%02d" % when.hour)
        self.minute = tk.StringVar(value="%02d" % when.minute)
        ttk.Combobox(
            time_row, textvariable=self.hour, values=["%02d" % value for value in range(24)],
            width=3, state="readonly", font=_font(small),
        ).pack(side="left")
        tk.Label(time_row, text=":", bg=BG, fg=TEXT, font=_font(small)).pack(side="left", padx=2)
        ttk.Combobox(
            time_row, textvariable=self.minute, values=["%02d" % value for value in range(60)],
            width=3, state="readonly", font=_font(small),
        ).pack(side="left")

        self.repeat_row = tk.Frame(self.top, bg=BG)
        self.repeat_row.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(self.repeat_row, text="重复", bg=BG, fg=MUTED, font=_font(small)).pack(side="left", padx=(0, 6))
        self.repeat_combo = ttk.Combobox(
            self.repeat_row, state="readonly", width=8, font=_font(small),
            values=[label for _key, label in REPEAT_OPTIONS],
        )
        self.repeat_combo.set(self._repeat_label())
        self.repeat_combo.pack(side="left")
        self.repeat_combo.bind("<<ComboboxSelected>>", lambda event: self._refresh_repeat_rows())

        self.repeat_extra = tk.Frame(self.top, bg=BG)
        self.repeat_extra.pack(fill="x", padx=12)

        # weekly: seven day toggles, four per row so they do not crowd
        self.week_row = tk.Frame(self.repeat_extra, bg=BG)
        existing_days = self.repeat.get("days", []) if self.repeat and self.repeat.get("type") == "weekly" else []
        default_days = existing_days or [self.selected.weekday()]
        self.week_vars = []
        for index, name in enumerate(self.WEEKDAYS):
            variable = tk.BooleanVar(value=index in default_days)
            self.week_vars.append(variable)
            row, column = divmod(index, 4)
            tk.Checkbutton(
                self.week_row, text="周" + name, variable=variable, bg=BG, activebackground=BG,
                highlightthickness=0, bd=0, font=_font(small), cursor="hand2",
            ).grid(row=row, column=column, sticky="w", padx=(0, 10), pady=2)

        # monthly: 1-31
        self.month_row = tk.Frame(self.repeat_extra, bg=BG)
        tk.Label(self.month_row, text="每月", bg=BG, fg=MUTED, font=_font(small)).pack(side="left", padx=(0, 4))
        existing_day = self.repeat.get("day") if self.repeat and self.repeat.get("type") == "monthly" else None
        self.month_day = tk.Spinbox(
            self.month_row, from_=1, to=31, width=3, font=_font(small),
            relief="flat", highlightthickness=1, highlightbackground=BORDER,
        )
        self.month_day.delete(0, "end")
        self.month_day.insert(0, str(existing_day or self.selected.day))
        self.month_day.pack(side="left")
        tk.Label(self.month_row, text="日", bg=BG, fg=MUTED, font=_font(small)).pack(side="left", padx=(2, 0))

        self.repeat_error = tk.Label(
            self.top, text="", bg=BG, fg=DANGER, font=_font(max(8, small - 1))
        )
        self.repeat_error.pack(anchor="w", padx=12)

        if self.mode == "due":
            self.repeat_row.pack_forget()
            self.repeat_extra.pack_forget()
            self.repeat_error.pack_forget()
        else:
            snooze = tk.Frame(self.top, bg=BG)
            snooze.pack(fill="x", padx=12, pady=(0, 6))
            for minutes, label in ((10, "10分钟后"), (30, "30分钟后"), (60, "1小时后")):
                self._flat(
                    snooze, label, lambda value=minutes: self._snooze(value), small
                ).pack(side="left", padx=(0, 6))

        buttons = tk.Frame(self.top, bg=BG)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        self._flat(buttons, "取消", self.close, self.window.font_size).pack(side="right")
        self._flat(buttons, "清除", self._clear, self.window.font_size).pack(side="right", padx=6)
        tk.Button(
            buttons, text="保存", command=self._save, relief="flat", bg=ACCENT, fg="#FFFFFF",
            activebackground=ACCENT_DARK, activeforeground="#FFFFFF", bd=0, padx=12,
            cursor="hand2", font=_font(self.window.font_size),
        ).pack(side="right")

        self._refresh_repeat_rows()
        self._draw()
        _place_dialog(self.top, self.window.root)
        self.window._reminder_picker = self

    def close(self):
        try:
            self.top.destroy()
        except Exception:
            pass
        if getattr(self.window, "_reminder_picker", None) is self:
            self.window._reminder_picker = None

    def _flat(self, parent, text, command, size):
        return tk.Button(
            parent, text=text, command=command, relief="flat", bd=0, bg=BG, fg=TEXT,
            activebackground=BG, cursor="hand2", padx=6, font=_font(size),
        )

    def _repeat_kind(self):
        label = self.repeat_combo.get()
        for key, text in REPEAT_OPTIONS:
            if text == label:
                return key
        return "none"

    def _repeat_label(self):
        kind = self.repeat.get("type") if self.repeat else "none"
        for key, text in REPEAT_OPTIONS:
            if key == kind:
                return text
        return REPEAT_OPTIONS[0][1]

    def _refresh_repeat_rows(self):
        kind = self._repeat_kind()
        self.week_row.pack_forget()
        self.month_row.pack_forget()
        if kind == "weekly":
            self.week_row.pack(fill="x", pady=(2, 0))
        elif kind == "monthly":
            self.month_row.pack(fill="x", pady=(2, 0))
        self.repeat_error.configure(text="")

    def _draw(self):
        self.title.configure(text="%d 年 %d 月" % (self.year, self.month))
        weeks = _month_grid(self.year, self.month)
        used_rows = 6 if any(weeks[5]) else 5
        for row_index, week in enumerate(weeks):
            for column, day in enumerate(week):
                button = self._cells[row_index][column]
                if row_index >= used_rows:
                    button.grid_remove()
                    continue
                button.grid()
                if day is None:
                    button.configure(text="", state="disabled", bg=BG, fg=BG, command="")
                else:
                    button.configure(
                        text=str(day), state="normal", command=lambda day=day: self._pick(day)
                    )
        self._paint()

    def _paint(self):
        today = date.today()
        for row_index, week in enumerate(_month_grid(self.year, self.month)):
            for column, day in enumerate(week):
                if day is None:
                    continue
                button = self._cells[row_index][column]
                if self.selected == date(self.year, self.month, day):
                    background, foreground = ACCENT, "#FFFFFF"
                elif date(self.year, self.month, day) == today:
                    background, foreground = TODAY_BG, ACCENT
                else:
                    background, foreground = BG, TEXT
                button.configure(bg=background, fg=foreground)

    def _pick(self, day):
        self.selected = date(self.year, self.month, day)
        self._paint()

    def _shift(self, delta):
        month = self.month + delta
        year = self.year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        self.year, self.month = year, month
        self._draw()

    def _prev_month(self):
        self._shift(-1)

    def _next_month(self):
        self._shift(1)

    def _build_repeat(self):
        kind = self._repeat_kind()
        if kind == "weekly":
            days = [index for index, variable in enumerate(self.week_vars) if variable.get()]
            if not days:
                self.repeat_error.configure(text="每周至少选一天")
                return None
            return {"type": "weekly", "days": days}
        if kind == "monthly":
            try:
                day = int(self.month_day.get())
            except (TypeError, ValueError):
                day = self.selected.day
            return {"type": "monthly", "day": max(1, min(31, day))}
        if kind in ("daily", "weekdays"):
            return {"type": kind}
        return None

    def _save(self):
        repeat = None
        if self.mode == "remind":
            repeat = self._build_repeat()
            if repeat is None and self._repeat_kind() == "weekly":
                return
        try:
            hour = int(self.hour.get())
            minute = int(self.minute.get())
        except (TypeError, ValueError):
            return
        when = datetime(
            self.selected.year, self.selected.month, self.selected.day, hour, minute, 0
        ).astimezone()
        iso = when.isoformat(timespec="seconds")
        if self.mode == "due":
            self.window.controller.set_due(self.todo["id"], iso)
        else:
            self.window.controller.set_remind(self.todo["id"], iso, repeat)
        self.close()
        if self.on_saved is not None:
            self.on_saved()
        self.window._render()

    def _snooze(self, minutes):
        when = (datetime.now().astimezone() + timedelta(minutes=minutes)).replace(
            second=0, microsecond=0
        )
        self.window.controller.snooze(
            self.todo["id"], when.isoformat(timespec="seconds")
        )
        self.close()
        self.window._render()

    def _clear(self):
        if self.mode == "due":
            self.window.controller.set_due(self.todo["id"], None)
        else:
            self.window.controller.set_remind(self.todo["id"], None, None)
        self.close()
        if self.on_saved is not None:
            self.on_saved()
        self.window._render()


class _StatsDialog:
    """Today / week / month / total / streak, from the current list only."""

    def __init__(self, window):
        self.window = window
        small = max(8, window.font_size - 1)
        stats = store.completion_stats(window.controller.list(), date.today())

        self.top = tk.Toplevel(window.root)
        self.top.title("完成统计")
        self.top.configure(bg=BG)
        self.top.transient(window.root)
        self.top.resizable(False, False)
        self.top.bind("<Escape>", lambda event: self.top.destroy())

        body = tk.Frame(self.top, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=(12, 4))
        rows = (
            ("今日完成", "%d 条" % stats["today"]),
            ("本周完成", "%d 条" % stats["week"]),
            ("本月完成", "%d 条" % stats["month"]),
            ("累计完成", "%d 条" % stats["total"]),
            ("连续完成", "%d 天" % stats["streak"]),
        )
        for label, value in rows:
            row = tk.Frame(body, bg=BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg=BG, fg=MUTED, font=_font(small)).pack(side="left")
            tk.Label(row, text=value, bg=BG, fg=TEXT, font=_font(small, bold=True)).pack(side="right")

        tk.Button(
            self.top, text="关闭", command=self.close, relief="flat", bd=0, bg=BG,
            fg=TEXT, activebackground=BG, cursor="hand2", font=_font(window.font_size),
        ).pack(pady=(6, 10))
        self.top.minsize(220, 0)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda event: self.close())
        _place_dialog(self.top, window.root)
        window._stats_dialog = self

    def close(self):
        try:
            self.top.destroy()
        except Exception:
            pass
        if getattr(self.window, "_stats_dialog", None) is self:
            self.window._stats_dialog = None


class _DetailsPanel:
    """Right-hand details sidebar: task form and a day's task list."""

    def __init__(self, window, frame):
        self.window = window
        self.frame = frame
        self.mode = None
        self.todo = None
        self.original_due = None
        self._due_value = None
        self._remind_value = None

        header = tk.Frame(frame, bg=BG)
        header.pack(fill="x", padx=12, pady=(10, 4))
        self.panel_title = tk.Label(
            header, text="详情", anchor="w", bg=BG, fg=TEXT,
            font=_font(max(9, window.font_size + 1), bold=True),
        )
        self.panel_title.pack(side="left", fill="x", expand=True)
        tk.Button(
            header, text="✕", command=window._hide_detail_panel, relief="flat", bd=0,
            bg=BG, fg=MUTED, activebackground=BG, activeforeground=TEXT,
            cursor="hand2", padx=4, font=_font(window.font_size),
        ).pack(side="right")
        self.close_button = header.winfo_children()[-1]
        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", padx=10)

        self.footer = tk.Frame(frame, bg=BG)
        tk.Frame(self.footer, bg=BORDER, height=1).pack(side="top", fill="x", padx=10)
        footer_buttons = tk.Frame(self.footer, bg=BG)
        footer_buttons.pack(fill="x", padx=12, pady=(8, 10))
        tk.Button(
            footer_buttons, text="保存", command=self._save, relief="flat", bg=ACCENT,
            fg="#FFFFFF", activebackground=ACCENT_DARK, activeforeground="#FFFFFF",
            bd=0, padx=12, cursor="hand2", font=_font(window.font_size),
        ).pack(side="right")
        self._flat(footer_buttons, "删除", self._delete, fg=DANGER).pack(side="left")
        self.toggle_button = self._flat(footer_buttons, "完成", self._toggle)
        self.toggle_button.pack(side="left", padx=6)

        self.container = tk.Frame(frame, bg=BG)
        self.container.pack(fill="both", expand=True, padx=10, pady=8)
        container = self.container
        self.canvas = tk.Canvas(container, bg=BG, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=BG)
        self._canvas_window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind(
            "<Configure>",
            lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self._canvas_window, width=event.width),
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set, yscrollincrement=1)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.body.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", self._on_wheel)
        self.canvas.bind("<Button-5>", self._on_wheel)
        self.body.bind("<Button-4>", self._on_wheel)
        self.body.bind("<Button-5>", self._on_wheel)

    def _bind_scroll_tree(self):
        pending = list(self.body.winfo_children())
        while pending:
            widget = pending.pop()
            widget.bind("<MouseWheel>", self._on_wheel, add="+")
            widget.bind("<Button-4>", self._on_wheel, add="+")
            widget.bind("<Button-5>", self._on_wheel, add="+")
            pending.extend(widget.winfo_children())

    def _on_wheel(self, event):
        delta = getattr(event, "delta", 0)
        num = getattr(event, "num", None)
        if num == 4:
            delta = 120
        elif num == 5:
            delta = -120
        if not delta:
            return "break"
        # Canvas yscrollincrement is 1px, so a 120-delta notch moves 40px
        # and high-resolution wheel deltas move proportionally.
        step = int(-delta / 3)
        if step == 0:
            step = -1 if delta > 0 else 1
        self.canvas.yview_scroll(step, "units")
        return "break"

    def _flat(self, parent, text, command, size=None, fg=TEXT):
        return tk.Button(
            parent, text=text, command=command, relief="flat", bd=0, bg=BG, fg=fg,
            activebackground=BG, activeforeground=ACCENT, cursor="hand2",
            padx=5, font=_font(size or max(8, self.window.font_size - 1)),
        )

    def _clear(self):
        for child in self.body.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)

    def _current(self):
        if self.todo is None:
            return None
        for item in self.window.controller.list():
            if item["id"] == self.todo["id"]:
                return item
        return dict(self.todo)

    def present(self, todo):
        self.mode = "details"
        self.todo = dict(todo)
        self.original_due = self._current().get("due_at") if self._current() else todo.get("due_at")
        self._due_value = self.original_due
        self._remind_value = todo.get("remind_at")
        self._clear()
        self.panel_title.configure(text="详情")
        self.toggle_button.configure(
            text="取消完成" if bool(todo.get("done")) else "完成"
        )
        if self.footer.winfo_manager() != "pack":
            self.footer.pack(side="bottom", fill="x", before=self.container)
        self._build_form(self._current() or todo)

    def _build_form(self, todo):
        small = max(8, self.window.font_size - 1)
        self.todo = dict(todo)
        self._due_value = todo.get("due_at")
        self._remind_value = todo.get("remind_at")

        tk.Label(self.body, text="标题", bg=BG, fg=MUTED, font=_font(small)).pack(anchor="w")
        self.title_var = tk.StringVar(value=str(todo.get("text") or ""))
        tk.Entry(
            self.body, textvariable=self.title_var, relief="flat", bg=CARD, fg=TEXT,
            insertbackground=TEXT, highlightthickness=1, highlightbackground=BORDER,
            highlightcolor=ACCENT, font=_font(small),
        ).pack(fill="x", ipady=3)

        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=(8, 0))
        tk.Label(row, text="优先级", bg=BG, fg=MUTED, font=_font(small)).pack(side="left")
        self.priority = ttk.Combobox(
            row, state="readonly", width=6, values=["无", "低", "中", "高"], font=_font(small)
        )
        self.priority.set(PRIORITY_LABELS.get(int(todo.get("priority") or 0), "无"))
        self.priority.bind("<<ComboboxSelected>>", lambda event: self._save_priority_now())
        self.priority.pack(side="right")
        self.pinned = tk.BooleanVar(value=bool(todo.get("pinned")))
        tk.Checkbutton(
            self.body, text="置顶", variable=self.pinned, bg=BG, activebackground=BG,
            highlightthickness=0, bd=0, font=_font(small), cursor="hand2",
            command=self._save_pinned_now,
        ).pack(anchor="w", pady=(4, 0))

        due_row = tk.Frame(self.body, bg=BG)
        due_row.pack(fill="x", pady=(8, 0))
        tk.Label(due_row, text="截止", bg=BG, fg=MUTED, font=_font(small)).pack(side="left")
        self.due_label = tk.Label(due_row, text="", bg=BG, fg=TEXT, font=_font(small))
        self.due_label.pack(side="left", padx=(6, 0), fill="x", expand=True)
        actions = tk.Frame(self.body, bg=BG)
        actions.pack(fill="x", pady=(2, 0))
        self._flat(actions, "设置截止", self._pick_due, small).pack(side="left")
        self._flat(actions, "清除", self._clear_due, small).pack(side="left", padx=4)

        remind_row = tk.Frame(self.body, bg=BG)
        remind_row.pack(fill="x", pady=(8, 0))
        tk.Label(remind_row, text="提醒", bg=BG, fg=MUTED, font=_font(small)).pack(side="left")
        self.remind_label = tk.Label(remind_row, text="", bg=BG, fg=TEXT, font=_font(small))
        self.remind_label.pack(side="left", padx=(6, 0), fill="x", expand=True)
        remind_actions = tk.Frame(self.body, bg=BG)
        remind_actions.pack(fill="x", pady=(2, 0))
        self._flat(remind_actions, "设置提醒", self._pick_remind, small).pack(side="left")
        self._flat(remind_actions, "清除", self._clear_remind, small).pack(side="left", padx=4)
        self.repeat_label = tk.Label(self.body, text="", bg=BG, fg=MUTED, font=_font(max(8, small - 1)))
        self.repeat_label.pack(anchor="w", pady=(2, 0))

        tk.Label(self.body, text="标签（逗号分隔）", bg=BG, fg=MUTED, font=_font(small)).pack(
            anchor="w", pady=(8, 0)
        )
        self.tags = tk.StringVar(value=", ".join(todo.get("tags") or []))
        tk.Entry(
            self.body, textvariable=self.tags, relief="flat", bg=CARD, fg=TEXT,
            highlightthickness=1, highlightbackground=BORDER, font=_font(small),
        ).pack(fill="x", ipady=3)

        tk.Label(self.body, text="备注", bg=BG, fg=MUTED, font=_font(small)).pack(anchor="w", pady=(8, 0))
        self.note = tk.Text(
            self.body, height=4, width=28, relief="flat", bg=CARD, fg=TEXT,
            highlightthickness=1, highlightbackground=BORDER, font=_font(small), wrap="word",
        )
        self.note.pack(fill="x")
        self.note.insert("1.0", todo.get("note") or "")

        tk.Label(self.body, text="子任务", bg=BG, fg=MUTED, font=_font(small)).pack(
            anchor="w", pady=(8, 2)
        )
        children = [
            item for item in self.window.controller.list()
            if item.get("parent_id") == todo.get("id")
        ]
        if not children:
            tk.Label(self.body, text="暂无子任务", bg=BG, fg=MUTED, font=_font(max(8, small - 1))).pack(anchor="w")
        for child in children:
            prefix = "✓ " if child.get("done") else "○ "
            self._flat(
                self.body, prefix + str(child.get("text") or ""),
                lambda child=child: self.window._select_todo(child), small,
                fg=MUTED if child.get("done") else TEXT,
            ).pack(fill="x", anchor="w")

        self._refresh_labels()
        self._bind_scroll_tree()

    def _refresh_labels(self):
        self.due_label.configure(
            text=("截止 " + _short_when(self._due_value)) if self._due_value else "未设置"
        )
        self.remind_label.configure(
            text=("提醒 " + _short_when(self._remind_value)) if self._remind_value else "未设置"
        )
        repeat = store.format_repeat((self.todo or {}).get("repeat"))
        self.repeat_label.configure(text=("重复：" + repeat) if repeat else "不重复")

    def _pick_due(self):
        self.window._open_reminder_picker(
            self._current() or self.todo, mode="due", on_saved=self._after_due
        )

    def _after_due(self):
        current = self._current()
        self.todo = dict(current)
        self._due_value = current.get("due_at")
        self._refresh_labels()
        self.window._render()

    def _clear_due(self):
        self.window.controller.set_due(self.todo["id"], None)
        self._due_value = None
        self._refresh_labels()
        self.window._render()

    def _pick_remind(self):
        self.window._open_reminder_picker(
            self._current() or self.todo, on_saved=self._after_remind
        )

    def _after_remind(self):
        current = self._current()
        self.todo = dict(current)
        self._remind_value = current.get("remind_at")
        self._refresh_labels()
        self.window._render()

    def _clear_remind(self):
        self.window.controller.set_remind(self.todo["id"], None, None)
        self._remind_value = None
        self._refresh_labels()
        self.window._render()

    def _save_priority_now(self):
        if self.todo is None:
            return
        label = self.priority.get()
        priority = {value: key for key, value in PRIORITY_LABELS.items()}.get(label, 0)
        self.window.controller.update_details(self.todo["id"], priority=priority)
        self.todo["priority"] = priority
        self.window._render()

    def _save_pinned_now(self):
        if self.todo is None:
            return
        pinned = bool(self.pinned.get())
        self.window.controller.update_details(self.todo["id"], pinned=pinned)
        self.todo["pinned"] = pinned
        self.window._render()

    def _save(self):
        current = self._current() or self.todo
        title = self.title_var.get().strip()
        if title and title != current.get("text"):
            self.window.controller.edit_text(self.todo["id"], title)
        label = self.priority.get()
        priority = {value: key for key, value in PRIORITY_LABELS.items()}.get(label, 0)
        tags = [
            part.strip() for part in self.tags.get().replace("，", ",").split(",") if part.strip()
        ]
        self.window.controller.update_details(
            self.todo["id"], priority=priority, pinned=self.pinned.get(),
            tags=tags, note=self.note.get("1.0", "end").strip(),
        )
        self.window._render()
        self.present(self._current() or self.todo)

    def _toggle(self):
        self.window.controller.toggle(self.todo["id"])
        self.window._render()
        self.present(self._current() or self.todo)

    def _delete(self):
        self.window.controller.delete(self.todo["id"])
        self.window._hide_detail_panel()
        self.window._render()

    def show_day(self, day, entries):
        self.mode = "day"
        self.todo = None
        self.footer.pack_forget()
        self._clear()
        self.panel_title.configure(
            text="%d月%d日 周%s" % (day.month, day.day, store.WEEKDAYS[day.weekday()])
        )
        small = max(8, self.window.font_size - 1)
        tk.Label(
            self.body, text="当天待办（%d 条）" % len(entries), bg=BG, fg=TEXT,
            font=_font(small, bold=True),
        ).pack(anchor="w", pady=(0, 6))
        if not entries:
            tk.Label(self.body, text="当天没有待办了", bg=BG, fg=MUTED, font=_font(self.window.font_size)).pack(
                anchor="w", pady=12
            )
            return
        for todo in entries:
            done = bool(todo.get("done"))
            title = ("✓  " if done else "○  ") + str(todo.get("text") or "")
            button = self._flat(
                self.body, title, lambda todo=todo: self.window._select_todo(todo),
                small, fg=MUTED if done else TEXT,
            )
            button.pack(fill="x", anchor="w", pady=1)
            button.bind("<MouseWheel>", self._on_wheel)
        self._bind_scroll_tree()

class _TaskRow:
    """One reusable list row; update() swaps the task without new widgets."""

    def __init__(self, window, parent):
        self.window = window
        self.todo = None
        self.card = tk.Frame(
            parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1, takefocus=1,
        )
        self.variable = tk.BooleanVar(value=False)
        self.check = ttk.Checkbutton(
            self.card, variable=self.variable, style="Card.TCheckbutton",
            cursor="hand2", command=self._toggle,
        )
        self.check.pack(side="left", padx=(6, 0))

        self.actions = tk.Frame(self.card, bg=CARD)
        self.action_buttons = []
        self._date_cache_key = None
        self._date_cache = (None, TEXT, None)

        def action(text, command, danger=False):
            button = tk.Button(
                self.actions, text=text, width=3, relief="flat", bd=0, bg=CARD,
                fg=DANGER if danger else MUTED, activebackground=CARD,
                activeforeground=DANGER if danger else ACCENT, cursor="hand2",
                font=_font(window.font_size), padx=2, command=command,
            )
            button.pack(side="right")
            self.action_buttons.append(button)
            return button

        action("✕", self._delete, danger=True)
        action("···", self._open)
        action("⏰", self._remind)
        self.plus = action("＋", self._subtask)
        self.actions.pack_forget()

        self.middle = tk.Frame(self.card, bg=CARD)
        self.middle.pack(side="left", fill="x", expand=True, pady=4, padx=(2, 4))
        self.title = tk.Label(
            self.middle, text="", anchor="w", justify="left", bg=CARD,
            fg=TEXT, font=_font(window.font_size),
        )
        self.title.pack(fill="x", anchor="w")
        self.meta = tk.Frame(self.middle, bg=CARD)
        self.meta.pack(fill="x", pady=(2, 0))
        self.tags_label = tk.Label(self.meta, anchor="w", bg=CARD, fg=MUTED, font=_font(10))
        self.note_label = tk.Label(self.meta, anchor="w", bg=CARD, fg=MUTED, font=_font(10))
        self.due_label = tk.Label(self.meta, anchor="w", bg=CARD, fg=TEXT, font=_font(10))
        self.remind_label = tk.Label(self.meta, anchor="w", bg=CARD, fg=CLOCK, font=_font(10))
        self.progress_label = tk.Label(self.meta, anchor="w", bg=CARD, fg=MUTED, font=_font(10))
        self.pill = tk.Label(self.meta, anchor="w", bg=MUTED, fg="#FFFFFF", font=_font(9, bold=True), padx=5)

        for widget in (self.title, self.middle, self.card):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Double-Button-1>", self._double)
        self.card.bind("<Enter>", lambda event: window._activate_row(self), add="+")
        self.card.bind("<FocusIn>", lambda event: window._activate_row(self), add="+")
        self._update_bindings()
        window._bind_hover(self.card, self.actions)

    def _update_bindings(self):
        # Commands are bound methods, so a reused row always acts on self.todo.
        pass

    def _click(self, event):
        try:
            kind = event.widget.winfo_class()
        except Exception:
            return
        if kind in ("TCheckbutton", "Checkbutton", "Button"):
            return
        if self.todo is not None:
            self.window._select_todo(self.todo)

    def _double(self, event):
        try:
            kind = event.widget.winfo_class()
        except Exception:
            return
        if kind in ("TCheckbutton", "Checkbutton", "Button"):
            return
        if self.todo is not None:
            self.window._select_todo(self.todo)

    def _toggle(self):
        if self.todo is not None:
            self.window._toggle(self.todo["id"])

    def _delete(self):
        if self.todo is not None:
            self.window._delete(self.todo["id"])

    def _open(self):
        if self.todo is not None:
            self.window._select_todo(self.todo)

    def _remind(self):
        if self.todo is not None:
            self.window._open_reminder_picker(self.todo)

    def _subtask(self):
        if self.todo is not None:
            self.window._start_subtask(self.todo["id"])

    def _date_labels(self, todo, done):
        """Cache due/remind text per task + minute, not per Configure event."""
        now = datetime.now().astimezone()
        key = (
            todo.get("due_at"), todo.get("remind_at"), bool(todo.get("done")),
            repr(todo.get("repeat")), now.replace(second=0, microsecond=0),
        )
        if key == self._date_cache_key:
            return self._date_cache
        due = store.effective_due(todo)
        due_dt = store.effective_due_datetime(todo)
        due_text = None
        color = TEXT
        if due:
            due_text = "截止 " + (_short_when(due) or store.format_reminder(due))
            repeat = store.format_repeat(todo.get("repeat"))
            if repeat:
                due_text += " · " + repeat
            if done:
                color = MUTED
            elif due_dt is not None and due_dt < now:
                color = DANGER
        remind_dt = store.parse_dt(todo.get("remind_at"))
        remind_text = None
        if remind_dt is not None and (due_dt is None or remind_dt != due_dt):
            remind_text = "提醒 " + _short_when(todo["remind_at"])
        self._date_cache_key = key
        self._date_cache = (due_text, color, remind_text)
        return self._date_cache

    def update(self, todo, compact=False, font_size=10, selected=False):
        self.todo = dict(todo)
        self.variable.set(bool(todo.get("done")))
        font_size = max(8, int(font_size) - (1 if compact else 0))
        small = max(8, font_size - 1)
        for button in self.action_buttons:
            button.configure(font=_font(font_size))
        done = bool(todo.get("done"))
        priority = int(todo.get("priority") or 0)
        prefix = "📌 " if todo.get("pinned") else ""
        self.title.configure(
            text=prefix + str(todo.get("text") or ""),
            fg=MUTED if done else TEXT,
            font=_font(font_size, overstrike=done, bold=priority >= 2 and not compact),
            wraplength=0,
        )
        self.middle.configure(pady=2 if compact else 4)
        for widget in (self.title, self.meta):
            widget.pack_forget()
        if compact:
            self.title.pack(side="left", anchor="w")
            self.meta.pack(side="left", fill="x", expand=True, padx=(8, 0))
        else:
            self.title.pack(fill="x", anchor="w")
            self.meta.pack(fill="x", pady=(2, 0))

        for label in (self.tags_label, self.note_label, self.due_label,
                      self.remind_label, self.progress_label, self.pill):
            label.pack_forget()

        tags = todo.get("tags") or []
        if tags:
            self.tags_label.configure(text=" ".join("#" + tag for tag in tags), font=_font(small))
            self.tags_label.pack(side="left")
        if todo.get("note"):
            self.note_label.configure(text="📝", font=_font(small))
            self.note_label.pack(side="left")
        due_text, due_color, remind_text = self._date_labels(todo, done)
        if due_text is not None:
            self.due_label.configure(text=due_text, fg=due_color, font=_font(small))
            self.due_label.pack(side="left")
        if remind_text is not None:
            self.remind_label.configure(text=remind_text, font=_font(small))
            self.remind_label.pack(side="left")
        total, done_children = self.window._child_counts.get(todo.get("id"), (0, 0))
        if total:
            self.progress_label.configure(text="子任务 %d/%d" % (done_children, total), font=_font(small))
            self.progress_label.pack(side="left")
        if priority in PRIORITY_COLORS:
            self.pill.configure(
                text=PRIORITY_LABELS[priority], bg=PRIORITY_COLORS[priority],
                font=_font(max(8, small - 1), bold=True),
            )
            self.pill.pack(side="left", padx=(6, 0))

        is_child = bool(todo.get("parent_id"))
        if is_child:
            self.plus.pack_forget()
        else:
            self.plus.pack(side="right")
        self.actions.pack_forget()
        self.card.configure(
            highlightbackground=ACCENT if selected else BORDER,
            highlightthickness=2 if selected else 1,
        )



    def set_selected(self, selected):
        self.card.configure(
            highlightbackground=ACCENT if selected else BORDER,
            highlightthickness=2 if selected else 1,
        )

