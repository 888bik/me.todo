"""Detail sidebar panel and its window coordination methods."""

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


class DetailsMixin:
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
            self.root, bg=BG, width=self._scale_dimension(DETAIL_WIDTH),
            highlightbackground=BORDER, highlightthickness=1,
        )
        self._detail_frame.pack_propagate(False)
        self._details_panel = _DetailsPanel(self, self._detail_frame)

    def _show_detail_panel(self):
        if self._detail_visible:
            return
        width = self.root.winfo_width()
        if width <= 1:
            width = self._scale_dimension(
                self._logical_dimension("window_width", DEFAULT_WIDTH)
            )
        self._detail_base_width = max(self._scale_dimension(MIN_WIDTH), int(width))
        self._detail_visible = True
        self._detail_frame.pack(side="right", fill="y", before=self.main)
        target = self._physical_screen_limit(
            self._detail_base_width + self._scale_dimension(DETAIL_WIDTH), "width"
        )
        height = self.root.winfo_height()
        if height <= 1:
            height = self._scale_dimension(
                self._logical_dimension("window_height", DEFAULT_HEIGHT)
            )
        height = self._physical_screen_limit(height, "height")
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.geometry("%dx%d+%d+%d" % (target, int(height), x, y))

    def _hide_detail_panel(self):
        if not self._detail_visible:
            return
        self._detail_frame.pack_forget()
        self._detail_visible = False
        self._selected_todo_id = None
        self._details_dialog = None
        width = self._detail_base_width
        if not width:
            width = self._scale_dimension(
                self._logical_dimension("window_width", DEFAULT_WIDTH)
            )
        width = max(self._scale_dimension(MIN_WIDTH), int(width))
        width = self._physical_screen_limit(width, "width")
        height = self.root.winfo_height()
        if height <= 1:
            height = self._scale_dimension(
                self._logical_dimension("window_height", DEFAULT_HEIGHT)
            )
        height = self._physical_screen_limit(height, "height")
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.geometry("%dx%d+%d+%d" % (width, int(height), x, y))
        self._render()

    def _select_todo(self, todo):
        previous = getattr(self, "_selected_todo_id", None)
        self._open_details(todo)
        if previous and previous != todo["id"]:
            old_row = getattr(self, "_row_by_todo_id", {}).get(previous)
            if old_row is not None:
                old_row.set_selected(False)
        row = getattr(self, "_row_by_todo_id", {}).get(todo["id"])
        if row is not None:
            row.set_selected(True)

    def _activate_row(self, row):
        self._active_row = row
        for other in self._row_pool:
            if other is not row:
                try:
                    other.actions.pack_forget()
                except Exception:
                    pass

