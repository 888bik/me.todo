"""List mixin for the sticky window."""

import calendar
import os
import queue
import threading
import time
import bisect
from datetime import date, datetime, timedelta

import store
from sticky_window_theme import *
from sticky_window_theme import register_palette_module
from sticky_window_utils import *

from sticky_window_row import _TaskRow

tk = None
ttk = None
messagebox = None
tkfont = None

register_palette_module(globals())

class ListMixin:
    def _render_list(self, todos, empty_text="还没有待办"):
        if not todos:
            self._render_empty_list(empty_text)
            return
        self._render_virtual_list(todos)

    def _render_empty_list(self, empty_text):
        self._list_total = 0
        self._list_items = []
        self._list_signature = None
        self._layout_signature = None
        if self.canvas is not None:
            try:
                self.canvas.pack_forget()
                self.scrollbar.pack_forget()
            except Exception:
                pass
        if self._list_empty_frame is not None:
            try:
                self._list_empty_frame.destroy()
            except Exception:
                pass
        empty = tk.Frame(self.body, bg=BG)
        empty.pack(fill="both", expand=True, pady=40)
        tk.Label(empty, text=empty_text, bg=BG, fg=MUTED, font=_font(self.font_size)).pack()
        tk.Label(
            empty, text="在下面输入一条，回车添加", bg=BG, fg=MUTED,
            font=_font(max(8, self.font_size - 1)),
        ).pack(pady=(4, 0))
        self._list_empty_frame = empty
        self._row_widgets = {}
        self._row_actions = {}
        self._row_items = {}
        self._check_vars = []
        self._row_by_todo_id = {}
        for row in self._row_pool:
            row.clear()

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
        layout_signature = (
            tuple(todo["id"] for todo in todos), self._subtask_parent,
            row_height, subtask_height, bool(self.compact), int(self.font_size),
        )
        if layout_signature == getattr(self, "_layout_signature", None) and self._list_items:
            by_id = {todo["id"]: todo for todo in todos}
            for item in self._list_items:
                item["todo"] = by_id[item["todo"]["id"]]
        else:
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
            self._list_offsets = [item["offset"] for item in items]
            self._list_total = offset
            self._layout_signature = layout_signature
        self._list_row_height = row_height

        reuse = bool(
            getattr(self, "canvas", None) is not None
            and self.canvas.winfo_exists()
            and self.canvas.master is self.body
        )
        if reuse:
            if self._list_empty_frame is not None:
                try:
                    self._list_empty_frame.destroy()
                except Exception:
                    pass
                self._list_empty_frame = None
            self.scrollbar.pack(side="right", fill="y")
            self.canvas.pack(side="left", fill="both", expand=True)
            canvas = self.canvas
            scrollbar = self.scrollbar
            rows = self.rows
        else:
            canvas = tk.Canvas(self.body, bg=BG, highlightthickness=0, bd=0)
            scrollbar = ttk.Scrollbar(self.body, orient="vertical", command=canvas.yview)
            rows = tk.Frame(canvas, bg=BG)
            rows.pack_propagate(False)
            canvas_window = canvas.create_window((0, 0), window=rows, anchor="nw")
            self._canvas_window = canvas_window
            canvas.configure(yscrollcommand=lambda first, last: self._on_list_yscroll(first, last))
            canvas.bind("<Configure>", self._on_list_configure)
            scrollbar.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            canvas.bind("<MouseWheel>", self._on_wheel)
            self.scrollbar = scrollbar
            self.canvas = canvas
            self.rows = rows
            self._row_pool = []
            self._row_by_todo_id = {}
            self._row_widgets = {}
            self._row_actions = {}
            self._row_items = {}
            self._subtask_item = None
            self._check_vars = []
            self._subtask_widget = None
            self._subtask_frame = None
            self._layout_signature = None
            self._row_by_todo_id = {}

        rows.configure(height=max(1, self._list_total), width=max(1, canvas.winfo_width()))
        canvas.configure(scrollregion=(0, 0, max(1, canvas.winfo_width()), max(1, self._list_total)))
        scrollbar.state(["!disabled"] if self._list_total > 1 else ["disabled"])
        if self._list_update_id is not None:
            try:
                self.root.after_cancel(self._list_update_id)
            except Exception:
                pass
            self._list_update_id = None
        self._list_signature = None
        self._update_visible_rows(force=True)

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
            font=getattr(self, "_entry_font", None) or _font(self.font_size),
        )
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<Return>", lambda event: self._create_subtask(self._subtask_parent, entry))
        entry.bind("<Tab>", lambda event: self._create_subtask_keep_open(self._subtask_parent, entry))
        entry.bind("<Control-Return>", lambda event: self._create_subtask_keep_open(self._subtask_parent, entry))
        entry.bind("<Escape>", lambda event: self._cancel_subtask())
        self._subtask_widget = frame
        self._subtask_frame = frame
        self._subtask_entry = entry

    def _update_visible_rows(self, force=False):
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
        if self._list_offsets and len(self._list_offsets) == len(self._list_items):
            start = bisect.bisect_left(self._list_offsets, first_y)
            if start > 0 and (
                self._list_items[start - 1]["offset"] + self._list_items[start - 1]["height"]
                >= first_y
            ):
                start -= 1
            end = bisect.bisect_right(self._list_offsets, last_y)
            visible = self._list_items[start:end]
        else:
            visible = [
                item for item in self._list_items
                if item["offset"] + item["height"] >= first_y and item["offset"] <= last_y
            ]
        signature = tuple((item["kind"], item["todo"]["id"]) for item in visible)
        if not force and signature == self._list_signature:
            return
        self._list_updating = True
        try:
            self._row_widgets = {}
            self._row_actions = {}
            self._row_items = {}
            self._check_vars = []
            subtask_item = None
            assigned = set()
            current = {}
            visible_ids = {
                item["todo"]["id"] for item in visible if item["kind"] == "todo"
            }
            for item in visible:
                if item["kind"] == "subtask":
                    subtask_item = item
                    continue
                todo = item["todo"]
                todo_id = todo["id"]
                row = self._row_by_todo_id.get(todo_id)
                if row is not None and getattr(row, "_needs_rebuild", False):
                    try:
                        row.card.destroy()
                    except Exception:
                        pass
                    self._row_pool = [candidate for candidate in self._row_pool if candidate is not row]
                    row = None
                if row is None:
                    protected = {
                        candidate for candidate_id, candidate in self._row_by_todo_id.items()
                        if candidate_id in visible_ids and candidate_id != todo_id
                    }
                    row = next(
                        (candidate for candidate in self._row_pool
                         if candidate not in assigned
                         and candidate not in protected
                         and not getattr(candidate, "_needs_rebuild", False)),
                        None,
                    )
                if row is None:
                    row = _TaskRow(self, self.rows)
                    self._row_pool.append(row)
                if row not in self._row_pool:
                    self._row_pool.append(row)
                selected = todo_id == getattr(self, "_selected_todo_id", None)
                row.update(todo, compact=self.compact, font_size=self.font_size,
                           selected=selected, read_only=self.simple_mode)
                self._place_card(row.card, item, width)
                assigned.add(row)
                current[todo_id] = row
                self._row_widgets[todo_id] = row.card
                self._row_actions[todo_id] = row.actions
                self._row_items[todo_id] = item
                self._check_vars.append(row.variable)
            for row in self._row_pool:
                if row not in assigned:
                    row.card.place_forget()
                    row.clear()
            self._row_by_todo_id = current
            ordered_rows = [current[item["todo"]["id"]] for item in visible
                            if item["kind"] == "todo" and item["todo"]["id"] in current]
            ordered_rows.extend(row for row in self._row_pool if row not in assigned)
            self._row_pool = ordered_rows

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

