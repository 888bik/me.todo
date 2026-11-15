"""Desktop sticky note facade and core window class.

The window is created on its own daemon thread.  Tkinter is still imported
lazily through ``_load_tk``; importing this module never imports tkinter.
"""

import os
import queue
import threading
import time
from datetime import date, datetime, timedelta

import store
import sticky_window_dpi as _dpi
import sticky_window_theme as _theme

from sticky_window_theme import *
from sticky_window_theme import register_palette_module
from sticky_window_utils import *
from sticky_window_dpi import dpi_scale, _apply_dpi_scaling, _alpha_value
from sticky_window_row import _TaskRow
from sticky_window_details import _DetailsPanel, DetailsMixin
from sticky_window_dialogs import _FontSettings, _ReminderPicker, _StatsDialog
from sticky_window_nav import NavMixin
from sticky_window_list import ListMixin
from sticky_window_calendar import CalendarMixin
from sticky_window_lock import LockMixin
from sticky_window_actions import ActionsMixin

tk = None
ttk = None
messagebox = None
tkfont = None

register_palette_module(globals())

_state = {"thread": None, "queue": None, "window": None, "visible": False}
_lock = threading.Lock()


def _enqueue(command):
    commands = _state.get("queue")
    if commands is None:
        return
    commands.put(command)
    window = _state.get("window")
    if window is not None:
        window._wake_command()

_WIDGET_MODULES = ()


def _sync_widget_modules():
    global _WIDGET_MODULES
    import sticky_window_row as _row
    import sticky_window_details as _details
    import sticky_window_dialogs as _dialogs
    import sticky_window_nav as _nav
    import sticky_window_list as _list
    import sticky_window_calendar as _calendar
    import sticky_window_lock as _lock_mod
    import sticky_window_actions as _actions
    _WIDGET_MODULES = (_row, _details, _dialogs, _nav, _list, _calendar, _lock_mod, _actions)


_sync_widget_modules()


def _load_tk():
    """Load tkinter once and publish it to every module that builds widgets."""
    _dpi._load_tk()
    global tk, ttk, messagebox, tkfont
    tk = _dpi.tk
    ttk = _dpi.ttk
    messagebox = _dpi.messagebox
    tkfont = _dpi.tkfont
    for module in _WIDGET_MODULES:
        module.tk = tk
        module.ttk = ttk
        module.messagebox = messagebox
        module.tkfont = tkfont


_current_appearance = _theme._current_appearance
_current_theme = _theme._current_theme


def _set_palette(appearance=None, theme="light"):
    """Swap the shared palette and keep the facade's compatibility aliases."""
    _theme._set_palette(appearance, theme)
    global _current_appearance, _current_theme
    _current_appearance = dict(_theme._current_appearance)
    _current_theme = _theme._current_theme


def is_visible():
    return bool(_state["visible"])


def show(controller, settings, focus=False):
    _ensure(controller, settings)
    _enqueue(("show", bool(focus)))


def hide():
    _enqueue(("hide",))


def refresh():
    _enqueue(("refresh",))


def apply_settings(settings):
    _enqueue(("settings", dict(settings or {})))


def apply_appearance(appearance=None, theme=None):
    """Apply the current Win顺 appearance, or remember it before the UI starts."""
    appearance = dict(appearance or {})
    theme = theme or appearance.get("theme") or _current_theme
    thread = _state.get("thread")
    commands = _state.get("queue")
    if commands is not None and thread is not None and thread.is_alive():
        _enqueue(("appearance", appearance, theme))
    else:
        _set_palette(appearance, theme)


def set_locked(locked):
    _enqueue(("locked", bool(locked)))


def set_compact(compact):
    _enqueue(("compact", bool(compact)))


def show_stats():
    _enqueue(("stats",))


def export_todos():
    _enqueue(("export",))


def import_todos():
    _enqueue(("import",))


def _ensure(controller, settings):
    with _lock:
        thread = _state["thread"]
        if thread is not None and thread.is_alive():
            _enqueue(("settings", dict(settings or {})))
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
    dpi_factor = _apply_dpi_scaling(root)
    window = _StickyWindow(root, commands, controller, settings, dpi_factor=dpi_factor)
    _state["window"] = window
    _state["visible"] = False
    window._wake_command()
    root.after(1000, window.pump)
    root.mainloop()


class _StickyWindow(DetailsMixin, NavMixin, ListMixin, CalendarMixin, LockMixin, ActionsMixin):
    def __init__(self, root, commands, controller, settings, dpi_factor=None):
        self.root = root
        try:
            # The production entry point passes _apply_dpi_scaling()'s value.
            # Direct construction (used by tests and embedders) stays at the
            # unscaled baseline unless a factor is explicitly supplied.
            self._dpi_factor = max(1.0, min(2.5, float(
                1.0 if dpi_factor is None else dpi_factor
            )))
        except (TypeError, ValueError):
            self._dpi_factor = 1.0
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
        self._editing_row = None
        self._pump_after_id = None
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
        self._list_offsets = []
        self._layout_signature = None
        self._row_pool = []
        self._row_by_todo_id = {}
        self._row_height_cache = {}
        self._row_items = {}
        self._subtask_item = None
        self._list_update_id = None
        self._list_updating = False
        self._list_signature = None
        self._list_empty_frame = None
        self.canvas = None
        self.scrollbar = None
        self.rows = None
        self._canvas_window = None
        self._subtask_widget = None
        self._subtask_frame = None
        self.locked = bool(self.config.get("locked"))
        self._topmost_restore_id = None
        self._hook = None
        self._hook_rect = None
        self._last_hook_check = 0.0
        self._build()

    def _build(self):
        self.root.withdraw()
        self.root.update_idletasks()
        _hide_from_taskbar(self.root)
        self.root.bind("<<TodoCommand>>", self._on_command_event, add="+")
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
        self._resize_nav()

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

    def _scale_dimension(self, logical_pixels):
        return round(int(logical_pixels) * self._dpi_factor)

    def _logical_dimension(self, key, default):
        value = self.config.get(key)
        try:
            return int(value) if value else int(default)
        except (TypeError, ValueError):
            return int(default)

    def _physical_screen_limit(self, value, axis):
        try:
            screen = int(
                self.root.winfo_screenwidth() if axis == "width"
                else self.root.winfo_screenheight()
            )
        except Exception:
            return int(value)
        return min(int(value), max(1, screen))

    def _apply_size(self):
        # config.json stores logical pixels. Tk geometry and minsize use the
        # physical pixels of the current DPI-aware display.
        logical_width = max(
            MIN_WIDTH, self._logical_dimension("window_width", DEFAULT_WIDTH)
        )
        logical_height = max(
            MIN_HEIGHT, self._logical_dimension("window_height", DEFAULT_HEIGHT)
        )
        self.config["window_width"] = logical_width
        self.config["window_height"] = logical_height

        width = self._physical_screen_limit(self._scale_dimension(logical_width), "width")
        height = self._physical_screen_limit(self._scale_dimension(logical_height), "height")
        minimum_width = self._scale_dimension(MIN_WIDTH)
        minimum_height = self._scale_dimension(MIN_HEIGHT)
        self.root.minsize(minimum_width, minimum_height)
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

    def _measure_nav_width(self):
        """Measure the widest navigation row with Tk's actual font metrics."""
        fallback = round(NAV_WIDTH * self._dpi_factor)
        if tkfont is None:
            return fallback
        try:
            small = max(8, self.font_size - 1)
            count_size = max(8, small - 1)
            font_key = (small, count_size, FONT_FAMILY)
            if getattr(self, "_nav_measure_font_key", None) != font_key:
                self._nav_measure_fonts = {
                    "label": tkfont.Font(
                        root=self.root, family=FONT_FAMILY, size=small
                    ),
                    "label_bold": tkfont.Font(
                        root=self.root, family=FONT_FAMILY, size=small, weight="bold"
                    ),
                    "count_bold": tkfont.Font(
                        root=self.root, family=FONT_FAMILY, size=count_size, weight="bold"
                    ),
                    "sub": tkfont.Font(
                        root=self.root, family=FONT_FAMILY, size=count_size
                    ),
                }
                self._nav_measure_font_key = font_key
            fonts = self._nav_measure_fonts
            label_font = fonts["label"]
            label_bold_font = fonts["label_bold"]
            count_bold_font = fonts["count_bold"]
            sub_font = fonts["sub"]

            # Each filter item: outer padx 10+8, 3px active bar, 7px
            # padding on each side of the name, and count padx (0, 6).
            filter_widths = [
                10 + 8 + 3 + max(label_font.measure(label), label_bold_font.measure(label))
                + 7 + 7 + count_bold_font.measure("99") + 6
                for _key, label in NAV_FILTERS
            ]
            # The small hint is packed with padx=(24, 8).
            sub_width = 24 + 8 + sub_font.measure("逾期 99 · 今日到期 99")
            # View rows have the same item / bar / name padding, but no count.
            view_widths = [
                10 + 8 + 3 + max(label_font.measure(label), label_bold_font.measure(label))
                + 7 + 7
                for _key, label in NAV_VIEWS
            ]
            return max(fallback, max(filter_widths + view_widths + [sub_width]) + 4)
        except Exception:
            return fallback

    def _resize_nav(self):
        """Keep the fixed-width sidebar wide enough for its real content."""
        nav = getattr(self, "nav", None)
        if nav is None:
            return
        width = self._measure_nav_width()
        nav.configure(width=width)
        self._nav_width = width

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
        self._resize_nav()

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

    def pump(self):
        current_pump = getattr(self, "_pump_after_id", None)
        if current_pump is not None:
            try:
                self.root.after_cancel(current_pump)
            except Exception:
                pass
            self._pump_after_id = None
        commands = []
        try:
            while True:
                commands.append(self.commands.get_nowait())
        except queue.Empty:
            pass
        pending_refresh = None
        for command in commands:
            if command[0] == "refresh":
                pending_refresh = command
                continue
            if pending_refresh is not None:
                self._dispatch(pending_refresh)
                pending_refresh = None
            self._dispatch(command)
        if pending_refresh is not None:
            self._dispatch(pending_refresh)
        try:
            hidden = str(self.root.state()) in ("withdrawn", "iconic")
        except Exception:
            hidden = False
        self._check_lock_hook(hidden=hidden)
        self._schedule_pump()

    def _schedule_pump(self):
        if getattr(self, "_pump_after_id", None) is not None:
            return
        try:
            self._pump_after_id = self.root.after(1000, self.pump)
        except Exception:
            self._pump_after_id = None

    def _wake_command(self):
        try:
            self.root.event_generate("<<TodoCommand>>", when="tail")
        except Exception:
            pass

    def _on_command_event(self, event):
        self.pump()

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
        same_list = bool(
            self.view in ("list", "today")
            and getattr(self, "canvas", None) is not None
            and self.canvas.winfo_exists()
            and self.canvas.master is self.body
        )
        if not same_list:
            for child in self.body.winfo_children():
                child.destroy()
            self._list_empty_frame = None
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
            self._row_by_todo_id = {}
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
            logical_width = round(width / self._dpi_factor)
            logical_height = round(height / self._dpi_factor)
            self.controller.save_window_position(
                self.root.winfo_x(), self.root.winfo_y(), logical_width, logical_height
            )
            self.config["window_width"] = logical_width
            self.config["window_height"] = logical_height
        except Exception:
            pass

