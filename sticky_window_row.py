"""Reusable virtual-list task row."""

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

import sticky_window_theme as _theme

tk = None
ttk = None
messagebox = None
tkfont = None

register_palette_module(globals())

class _TaskRow:
    """One reusable list row; update() swaps the task without new widgets."""

    def __init__(self, window, parent):
        self.window = window
        self.todo = None
        self._needs_rebuild = False
        self._signature = None
        self._selected = False
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

    def _signature_for(self, todo, compact, font_size):
        counts = self.window._child_counts.get(todo.get("id"), (0, 0))
        return (
            todo.get("id"), todo.get("text"), bool(todo.get("done")),
            int(todo.get("priority") or 0), bool(todo.get("pinned")),
            tuple(todo.get("tags") or []), todo.get("note") or "",
            todo.get("due_at"), todo.get("remind_at"), repr(todo.get("repeat")),
            todo.get("parent_id"), counts, bool(compact), int(font_size),
            getattr(_theme, "_palette_version", 0),
            datetime.now().astimezone().replace(second=0, microsecond=0),
        )

    def update(self, todo, compact=False, font_size=10, selected=False):
        signature = self._signature_for(todo, compact, font_size)
        if signature == self._signature:
            self.todo = dict(todo)
            self.set_selected(selected)
            return
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
        self.set_selected(selected)
        self._signature = signature

    def clear(self):
        """Blank a pooled row that scrolled out of the visible window."""
        self.todo = None
        self._signature = None
        self.variable.set(False)
        self.title.configure(text="")
        for label in (
            self.tags_label, self.note_label, self.due_label, self.remind_label,
            self.progress_label, self.pill,
        ):
            label.configure(text="")
            label.pack_forget()
        self.actions.pack_forget()
        self.set_selected(False)

    def set_selected(self, selected):
        selected = bool(selected)
        if self._selected == selected:
            return
        self._selected = selected
        self.card.configure(
            highlightbackground=ACCENT if selected else BORDER,
            highlightthickness=2 if selected else 1,
        )

