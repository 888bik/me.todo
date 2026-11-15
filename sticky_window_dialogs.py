"""Font settings, reminder picker and statistics dialogs."""

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

