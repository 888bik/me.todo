"""Calendar mixin for the sticky window."""

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

class CalendarMixin:
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

        by_day = self._occurrence_map(todos, start, start + timedelta(days=13))
        today = date.today()
        for title, block_start in (("本周", start), ("下周", start + timedelta(days=7))):
            block_end = block_start + timedelta(days=6)
            block = tk.Frame(self.body, bg=BG)
            block.pack(fill="both", expand=True, pady=(0, 6))
            tk.Label(
                block,
                text="%s %d/%d – %d/%d" % (
                    title, block_start.month, block_start.day, block_end.month, block_end.day,
                ),
                anchor="w", bg=BG, fg=TEXT, font=_font(small, bold=True),
            ).pack(fill="x", pady=(0, 2))
            grid = tk.Frame(block, bg=BG)
            grid.pack(fill="both", expand=True)
            for column in range(7):
                grid.columnconfigure(column, weight=1, uniform="week")
                day = block_start + timedelta(days=column)
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

