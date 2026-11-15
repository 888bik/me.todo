"""Nav mixin for the sticky window."""

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

class NavMixin:
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
        self._resize_nav()

    def _update_nav_counts(self, todos):
        for key in ("today", "overdue", "all", "week7", "nodate", "done"):
            count = self.nav_counts.get(key)
            if count is not None:
                total = len(store.apply_filter(todos, key))
                count.configure(
                    text=str(total),
                    fg=DANGER if key == "overdue" and total else MUTED,
                )
        self._resize_nav()

    def _update_view_buttons(self):
        self._update_nav()

    def _update_filter_buttons(self):
        self._update_nav()

    def _nav_button(self, parent, text, command):
        return tk.Button(
            parent, text=text, command=command, relief="flat", bd=0, bg=BG, fg=TEXT,
            activebackground=BG, activeforeground=ACCENT, cursor="hand2", padx=6,
            font=_font(max(8, self.font_size - 1)),
        )

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

