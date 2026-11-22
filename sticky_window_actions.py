"""Actions mixin for the sticky window."""

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

class ActionsMixin:
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

    def _start_edit(self, todo, middle, label):
        if self._editing_id is not None:
            return
        self._editing_id = todo["id"]
        for row in self._row_pool:
            if row.card is middle.master:
                row._needs_rebuild = True
                self._editing_row = row
                break
        # Replace the whole text block (title + deadline) with the editor; the
        # row is rebuilt from data on save/cancel, so the deadline returns.
        for child in middle.winfo_children():
            child.destroy()
        self._edit_entry = tk.Entry(
            middle, relief="flat", bg=CARD, fg=TEXT, insertbackground=TEXT,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
            font=getattr(self, "_entry_font", None) or _font(self.font_size),
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
        self._editing_row = None
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
            font=getattr(self, "_entry_font", None) or _font(self.font_size),
        )
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<Return>", lambda event: self._create_subtask(parent["id"], entry))
        entry.bind("<Tab>", lambda event: self._create_subtask_keep_open(parent["id"], entry))
        entry.bind("<Control-Return>", lambda event: self._create_subtask_keep_open(parent["id"], entry))
        entry.bind("<Escape>", lambda event: self._cancel_subtask())
        entry.focus_set()
        self._subtask_entry = entry

    def _create_subtask_keep_open(self, parent_id, entry):
        self._create_subtask(parent_id, entry, keep_open=True)
        return "break"

    def _create_subtask(self, parent_id, entry, keep_open=False):
        text = entry.get().strip()
        if not text and keep_open:
            return
        self._subtask_parent = None
        self._subtask_entry = None
        self._subtask_widget = None
        self._subtask_frame = None
        if text:
            self.controller.add(text, parent_id=parent_id)
        if keep_open:
            self._subtask_parent = parent_id
            self._subtask_needs_focus = True
        self._render()

    def _cancel_subtask(self):
        self._subtask_parent = None
        self._subtask_entry = None
        self._subtask_widget = None
        self._subtask_frame = None
        self._render()

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
            # Prefer the controller's locked import: it keeps the reminder
            # thread from writing a stale snapshot between merge and reload.
            import_todos = getattr(self.controller, "import_todos", None)
            if import_todos is not None:
                added, skipped = import_todos(path)
            else:
                added, skipped = store.import_todos(self.controller.data_dir, path)
                reload_todos = getattr(self.controller, "reload_todos", None)
                if reload_todos is not None:
                    reload_todos()
                elif hasattr(self.controller, "todos"):
                    self.controller.todos = store.load_todos(self.controller.data_dir)
        except Exception as exc:
            self.status.configure(text="导入失败：%s" % exc)
            return
        self.status.configure(text="导入 %d 条，跳过 %d 条重复" % (added, skipped))
        self._render()

