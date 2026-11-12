"""me.todo — 待办 / Todo: a resident Win顺 plugin with a desktop sticky note.

v1 scope: add / toggle / delete todos, one desktop sticky note, a tray menu,
one single-shot reminder per todo, one global hotkey, three host settings.
Everything lives in WS_DATA_DIR; no network, no clipboard, no user files.
"""

import hashlib
import os
import sys
import threading
import time
from datetime import datetime

import quickparse
import sticky_window
import store
import winshun

DEFAULT_HOTKEY = "Ctrl+Alt+T"
UNLOCK_HOTKEY = "Ctrl+Alt+U"
REMINDER_INTERVAL = 30


class App:
    """Todos plus the small amount of window state the sticker needs."""

    def __init__(self, data_dir=""):
        self.data_dir = str(data_dir or "")
        self.lock = threading.RLock()
        self.config = dict(store.DEFAULT_CONFIG)
        self.todos = []
        self.visible = False
        self.settings = {}
        self.plugin = None
        self.reminders_started = False
        self._last_removed = []

    # -- loading ---------------------------------------------------------
    def load(self, data_dir):
        self.data_dir = str(data_dir)
        self.config = store.load_config(self.data_dir)
        self.todos = store.load_todos(self.data_dir)

    def get_config(self):
        with self.lock:
            return dict(self.config)

    def icon_path(self):
        """The window keeps the blue check; the plugin card uses the line icon."""
        folder = getattr(self.plugin, "plugin_dir", "") or ""
        for name in ("sticky.png", "icon.png"):
            path = os.path.join(str(folder), name)
            if os.path.exists(path):
                return path
        return ""

    def icon_ico_path(self):
        folder = getattr(self.plugin, "plugin_dir", "") or ""
        path = os.path.join(str(folder), "icon.ico")
        return path if os.path.exists(path) else ""

    def save_window_position(self, x, y, width=None, height=None):
        """Persist position and the user's manually resized window dimensions."""
        with self.lock:
            self.config["window_x"] = int(x)
            self.config["window_y"] = int(y)
            if width is not None:
                self.config["window_width"] = int(width)
            if height is not None:
                self.config["window_height"] = int(height)
            store.save_config(self.data_dir, self.config)

    def set_font_size(self, size):
        value = max(store.FONT_MIN, min(store.FONT_MAX, int(size)))
        with self.lock:
            self.config["font_size"] = value
            store.save_config(self.data_dir, self.config)
        return value

    # -- todos -----------------------------------------------------------
    def list(self):
        """Top-level sorted; each parent is immediately followed by its children.

        Key: done, pinned, priority, effective deadline, created_at. Children
        sort without the pinned flag. Files keep insertion order.
        """
        with self.lock:
            todos = [dict(todo) for todo in self.todos]

        def key(todo):
            due = store.effective_due_datetime(todo)
            stamp = due.timestamp() if due is not None else float("inf")
            return (
                bool(todo.get("done")),
                not bool(todo.get("pinned")),
                -int(todo.get("priority") or 0),
                stamp,
                str(todo.get("created_at") or ""),
            )

        def child_key(todo):
            due = store.effective_due_datetime(todo)
            stamp = due.timestamp() if due is not None else float("inf")
            return (
                bool(todo.get("done")),
                -int(todo.get("priority") or 0),
                stamp,
                str(todo.get("created_at") or ""),
            )

        ids = {todo["id"] for todo in todos}
        children = {}
        top = []
        for todo in todos:
            parent = todo.get("parent_id")
            if parent and parent in ids:
                children.setdefault(parent, []).append(todo)
            else:
                top.append(todo)

        top.sort(key=key)
        ordered = []
        for todo in top:
            ordered.append(todo)
            for child in sorted(children.get(todo["id"], []), key=child_key):
                ordered.append(child)
        return ordered

    def add(self, text, parent_id=None, remind_at=None, due_at=None, priority=None, tags=None, repeat=None):
        raw = str(text).strip()
        if not raw:
            return None
        if tags is None:
            tags = []
            words = []
            for token in raw.split():
                if token.startswith("#") and 1 <= len(token) - 1 <= 10 and "#" not in token[1:]:
                    tag = token[1:].strip()
                    if tag and tag not in tags and len(tags) < 5:
                        tags.append(tag)
                    continue
                words.append(token)
            raw = " ".join(words).strip()
            if not raw:
                return None
        else:
            tags = [str(tag).strip() for tag in tags if str(tag).strip()][:5]
        priority_value = store._priority(priority) if priority is not None else 0
        with self.lock:
            todo = store.new_todo(
                raw,
                tags=tags,
                parent_id=parent_id,
                remind_at=remind_at,
                due_at=due_at,
                priority=priority_value,
                repeat=repeat,
            )
            if repeat:
                todo["repeat_base"] = remind_at
            self.todos.append(todo)
            store.save_todos(self.data_dir, self.todos)
        return dict(todo)

    def toggle(self, todo_id):
        with self.lock:
            target = None
            for todo in self.todos:
                if todo["id"] == todo_id:
                    target = todo
                    break
            if target is None:
                return None

            new_state = not target["done"]
            stamp = store.now_iso()
            affected = [target]
            if new_state:
                # Checking a parent finishes the whole group; unchecking it
                # reopens only the parent, leaving finished children finished.
                for todo in self.todos:
                    if todo.get("parent_id") == todo_id:
                        affected.append(todo)

            for todo in affected:
                if new_state:
                    if not todo["done"]:
                        todo["done"] = True
                        todo["completed_at"] = stamp
                        self._spawn_repeat(todo)
                else:
                    todo["done"] = False
                    todo["completed_at"] = None

            store.save_todos(self.data_dir, self.todos)
            return new_state
        return None

    def _spawn_repeat(self, todo):
        """Finishing a repeating todo schedules the next instance (old one stays)."""
        repeat = todo.get("repeat")
        remind = todo.get("remind_at")
        if not repeat or not remind:
            return None
        anchor = store.parse_dt(todo.get("repeat_base") or remind)
        if anchor is None:
            return None
        now = datetime.now().astimezone()
        if anchor > now:
            following = anchor
        else:
            following = _advance_repeat(repeat, todo.get("repeat_base") or remind, now)
        if following is None:
            return None

        stamp = following.isoformat(timespec="seconds")
        new = store.new_todo(
            todo["text"], remind_at=stamp, repeat=repeat, tags=todo.get("tags"),
            priority=todo.get("priority", 0), pinned=todo.get("pinned", False),
            note=todo.get("note", ""), parent_id=todo.get("parent_id"),
        )
        new["repeat_base"] = stamp
        old_due = store.parse_dt(todo.get("due_at"))
        old_remind = store.parse_dt(todo.get("remind_at"))
        if old_due is not None and old_remind is not None:
            new["due_at"] = (old_due + (following - old_remind)).isoformat(timespec="seconds")
        self.todos.append(new)
        return new

    def delete(self, todo_id):
        with self.lock:
            targets = {todo_id}
            for todo in self.todos:
                if todo.get("parent_id") == todo_id:
                    targets.add(todo["id"])
            removed = [(index, todo) for index, todo in enumerate(self.todos) if todo["id"] in targets]
            if not removed:
                return False
            self.todos = [todo for todo in self.todos if todo["id"] not in targets]
            self._last_removed = removed
            store.save_todos(self.data_dir, self.todos)
            return True

    def clear_done(self):
        """Remove finished todos (and their subtasks); the batch is undoable."""
        with self.lock:
            done_ids = {todo["id"] for todo in self.todos if todo.get("done")}
            if not done_ids:
                return 0
            targets = set(done_ids)
            for todo in self.todos:
                if todo.get("parent_id") in done_ids:
                    targets.add(todo["id"])
            removed = [(index, todo) for index, todo in enumerate(self.todos) if todo["id"] in targets]
            self.todos = [todo for todo in self.todos if todo["id"] not in targets]
            self._last_removed = removed
            store.save_todos(self.data_dir, self.todos)
            return len(removed)

    def undo_delete(self):
        """Put the last removal back at its original indexes (memory only)."""
        with self.lock:
            if not self._last_removed:
                return False
            for index, todo in sorted(self._last_removed, key=lambda pair: pair[0]):
                self.todos.insert(min(index, len(self.todos)), dict(todo))
            self._last_removed = []
            store.save_todos(self.data_dir, self.todos)
            return True

    def edit_text(self, todo_id, text):
        text = str(text).strip()
        if not text:
            return False
        with self.lock:
            for todo in self.todos:
                if todo["id"] == todo_id:
                    todo["text"] = text
                    store.save_todos(self.data_dir, self.todos)
                    return True
        return False

    def set_remind(self, todo_id, iso, repeat=None):
        with self.lock:
            for todo in self.todos:
                if todo["id"] == todo_id:
                    repeat = store.normalize_repeat(repeat)
                    todo["remind_at"] = iso
                    todo["repeat"] = repeat
                    todo["repeat_base"] = iso if repeat else None
                    todo["reminded"] = False
                    store.save_todos(self.data_dir, self.todos)
                    return True
        return False

    def set_due(self, todo_id, iso):
        with self.lock:
            for todo in self.todos:
                if todo["id"] == todo_id:
                    todo["due_at"] = iso
                    store.save_todos(self.data_dir, self.todos)
                    return True
        return False

    def snooze(self, todo_id, iso):
        """Move only remind_at; repeat_base keeps the original schedule."""
        with self.lock:
            for todo in self.todos:
                if todo["id"] == todo_id:
                    todo["remind_at"] = iso
                    todo["reminded"] = False
                    store.save_todos(self.data_dir, self.todos)
                    return True
        return False

    def update_details(self, todo_id, priority=None, pinned=None, tags=None, note=None):
        with self.lock:
            for todo in self.todos:
                if todo["id"] == todo_id:
                    if priority is not None:
                        todo["priority"] = store._priority(priority)
                    if pinned is not None:
                        todo["pinned"] = bool(pinned)
                    if tags is not None:
                        todo["tags"] = store.normalize_tags(tags)
                    if note is not None:
                        todo["note"] = str(note)
                    store.save_todos(self.data_dir, self.todos)
                    return True
        return False

    def set_locked(self, locked):
        locked = bool(locked)
        with self.lock:
            self.config["locked"] = locked
            store.save_config(self.data_dir, self.config)
        return locked

    def reschedule_repeat(self, todo_id, iso, save=True):
        with self.lock:
            for todo in self.todos:
                if todo["id"] == todo_id:
                    todo["remind_at"] = iso
                    todo["repeat_base"] = iso
                    todo["reminded"] = False
                    if save:
                        store.save_todos(self.data_dir, self.todos)
                    return True
        return False

    def set_view(self, view):
        view = store.normalize_view(view)
        with self.lock:
            self.config["view"] = view
            store.save_config(self.data_dir, self.config)
        return view

    def set_filter(self, filter_key):
        filter_key = store.normalize_filter(filter_key)
        with self.lock:
            self.config["filter"] = filter_key
            store.save_config(self.data_dir, self.config)
        return filter_key

    def set_compact(self, compact):
        with self.lock:
            self.config["compact"] = bool(compact)
            store.save_config(self.data_dir, self.config)
        return self.config["compact"]

    def set_transparency(self, value):
        value = max(50, min(100, int(value)))
        with self.lock:
            self.config["transparency"] = value
            self.settings["transparency"] = value
            store.save_config(self.data_dir, self.config)
        return value

    def mark_reminded(self, todo_id, save=True):
        with self.lock:
            for todo in self.todos:
                if todo["id"] == todo_id and not todo["reminded"]:
                    todo["reminded"] = True
                    if save:
                        store.save_todos(self.data_dir, self.todos)
                    return True
        return False

    def due(self, now=None):
        with self.lock:
            return [dict(todo) for todo in store.due_todos(self.todos, now)]

    # -- window ----------------------------------------------------------
    def show_sticky(self, focus=False):
        if focus and self.config.get("locked"):
            focus = False
        sticky_window.show(self, self.settings, focus)
        self.visible = True

    def hide_sticky(self):
        sticky_window.hide()
        self.visible = False

    def toggle_sticky(self):
        # The window thread owns the real visibility (the X button hides from
        # there), so ask it instead of trusting this object's last intent.
        if sticky_window.is_visible():
            self.hide_sticky()
        else:
            self.show_sticky()

    def apply_settings(self, settings):
        self.settings = dict(settings or {})
        sticky_window.apply_settings(self.settings)
        if self.settings.get("show_sticky", True):
            if not self.visible:
                self.show_sticky()
        else:
            self.hide_sticky()


app = App()


# ---- hotkey --------------------------------------------------------------------

def register_hotkey(app_instance, plugin, attempt=0):
    """Register the show/hide key and a separate emergency unlock key."""
    key = str((app_instance.settings or {}).get("hotkey") or DEFAULT_HOTKEY).strip() or DEFAULT_HOTKEY
    keys = [key]
    if key.casefold() != UNLOCK_HOTKEY.casefold():
        keys.append(UNLOCK_HOTKEY)
    try:
        plugin.hotkeys(keys)
    except Exception:
        pass

    try:
        result = plugin.tool("register_hotkeys", keys=keys)
    except Exception as exc:
        result = {"error": {"message": str(exc)}}

    ok = isinstance(result, dict) and not result.get("error") and not (result.get("errors") or {})
    if ok:
        return True
    if attempt == 0:
        time.sleep(0.3)
        return register_hotkey(app_instance, plugin, 1)

    winshun.debug("me.todo: register_hotkeys failed: %r" % (result,))
    return False


# ---- reminders -----------------------------------------------------------------

def _advance_repeat(repeat, remind_at, now):
    """Walk the schedule forward past `now`; missed runs are skipped, not replayed."""
    try:
        current = datetime.fromisoformat(str(remind_at))
    except (TypeError, ValueError):
        return None
    for _ in range(10000):
        following = store.next_occurrence(repeat, current)
        if following is None or following <= current:
            return None
        if following > now:
            return following
        current = following
    return None


def fire_reminders(app_instance, plugin, now=None):
    """Toast every due todo once and mark them reminded.

    One-shot todos get reminded=True; repeating ones move remind_at to the next
    occurrence and stay unreminded. Everything due goes in a single toast:
    Win顺 keeps at most one notification per plugin every 2 seconds.
    """
    now = now or datetime.now().astimezone()
    due = app_instance.due(now)
    if not due:
        return []

    texts = [str(todo["text"]) for todo in due]
    body = "；".join(texts)
    if len(body) > 200:
        body = body[:200]
    title = "待办提醒" if len(due) == 1 else "待办提醒（%d 条）" % len(due)
    plugin.toast(title, body, click=[winshun.set_query("db ")])
    if app_instance.settings.get("reminder_sound", True):
        _play_reminder_sound()

    fired = []
    for todo in due:
        repeat = todo.get("repeat")
        if repeat:
            anchor = todo.get("repeat_base") or todo.get("remind_at")
            following = _advance_repeat(repeat, anchor, now)
            if following is not None and app_instance.reschedule_repeat(
                todo["id"], following.isoformat(timespec="seconds"), save=False
            ):
                fired.append(todo["id"])
        elif app_instance.mark_reminded(todo["id"], save=False):
            fired.append(todo["id"])
    if fired:
        with app_instance.lock:
            store.save_todos(app_instance.data_dir, app_instance.todos)
        sticky_window.refresh()
    return fired


def _play_reminder_sound():
    """One system beep per batch; silent failure (no extra permissions)."""
    if os.name != "nt":
        return False
    try:
        import winsound

        winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS)
        return True
    except Exception:
        return False


def start_reminders(app_instance, plugin):
    if app_instance.reminders_started:
        return
    app_instance.reminders_started = True

    def loop():
        while True:
            try:
                fire_reminders(app_instance, plugin)
            except Exception as exc:
                winshun.debug("me.todo: reminder loop failed: %r" % (exc,))
            time.sleep(REMINDER_INTERVAL)

    threading.Thread(target=loop, name="me.todo-reminders", daemon=True).start()


# ---- protocol handlers ---------------------------------------------------------

MAX_SEARCH_MATCHES = 8


def _new_todo_item(text):
    item_id = "add-" + hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
    return winshun.item(
        item_id,
        "新建待办：" + text,
        "回车添加",
        copy=text,
        data={"kind": "add", "text": text},
    )


def _existing_todo_item(todo, parent_text=""):
    text = str(todo["text"])
    prefix = ("所属：%s · " % parent_text) if parent_text else ""
    if todo.get("done"):
        subtitle = prefix + "已完成 · 回车取消完成"
    else:
        subtitle = prefix + "回车标记完成"
        deadline = store.format_reminder(store.effective_due(todo))
        if deadline:
            subtitle += " · 截止 " + deadline
            remind = store.parse_dt(todo.get("remind_at"))
            due = store.effective_due_datetime(todo)
            if remind is not None and (due is None or remind != due):
                subtitle += " · 提醒 " + store.format_reminder(todo["remind_at"])
            repeat = store.format_repeat(todo.get("repeat"))
            if repeat:
                subtitle += " · " + repeat
    return winshun.item(
        "toggle-" + todo["id"],
        text,
        subtitle,
        copy=text,
        data={"kind": "toggle", "id": todo["id"], "text": text},
    )


def _keyboard_hint_item():
    return winshun.item(
        "keyboard-hint",
        "Ctrl+N 向下 · Ctrl+P 向上 · Ctrl+1~9 直达第几行",
        "回车确认当前选中行",
        valid=False,
    )


def _open_window_item():
    return winshun.item(
        "open-todo-window",
        "打开完整待办窗口",
        "查看完整清单、日历、提醒和设置 · 回车打开",
        data={"kind": "open"},
        score=120,
    )


def _raw_add_item(text):
    item_id = "add-" + hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
    return winshun.item(
        item_id,
        "直接添加原文：" + text,
        "回车添加",
        copy=text,
        data={"kind": "add", "text": text},
    )


def _parsed_add_item(parsed):
    raw = parsed["raw"]
    item_id = "add-parsed-" + hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]
    parts = []
    if parsed.get("due_at"):
        parts.append("截止" + quickparse.format_preview(parsed["due_at"]))
    if parsed.get("remind_at") and parsed["remind_at"] != parsed.get("due_at"):
        remind = datetime.fromisoformat(parsed["remind_at"])
        parts.append("提醒" + remind.strftime("%H:%M"))
    for tag in parsed.get("tags") or []:
        parts.append("#" + tag)
    priority = int(parsed.get("priority") or 0)
    if priority:
        parts.append("优先级" + {3: "高", 2: "中", 1: "低"}[priority])
    repeat = parsed.get("repeat")
    if repeat:
        parts.append(store.format_repeat(repeat) + "重复")
    parts.append("回车确认添加")
    return winshun.item(
        item_id,
        parsed["text"],
        " · ".join(parts),
        copy=parsed["text"],
        data={
            "kind": "add-parsed",
            "text": parsed["text"],
            "remind_at": parsed.get("remind_at"),
            "due_at": parsed.get("due_at"),
            "tags": list(parsed.get("tags") or []),
            "priority": priority,
            "repeat": repeat,
        },
    )


def on_query(query, plugin):
    text = (query.text or "").strip()

    # Typing only the `db` / `todo` keyword should be useful immediately:
    # show the current todo list in Win顺 instead of a lone "new todo" row.
    ordered = app.list()
    by_id = {todo["id"]: todo for todo in ordered}
    if not text:
        items = [_open_window_item()]
        visible = ordered[:MAX_SEARCH_MATCHES]
        for todo in visible:
            parent = by_id.get(todo.get("parent_id"))
            items.append(_existing_todo_item(todo, parent["text"] if parent else ""))
        if not ordered:
            items.append(winshun.item(
                "empty-todo-list", "还没有待办", "在桌面便签中添加第一条待办",
                valid=False,
            ))
        items.append(_keyboard_hint_item())
        return items

    # In-memory filter only: this runs on every keystroke, so no disk or host calls.
    matches = []
    for todo in ordered:
        if store.matches(todo, text):
            matches.append(todo)
            if len(matches) >= MAX_SEARCH_MATCHES:
                break

    parsed = quickparse.parse_quick_input(text)
    items = []
    if parsed:
        items.append(_parsed_add_item(parsed))
        if parsed["text"] != text:
            items.append(_raw_add_item(text))
    for todo in matches:
        parent = by_id.get(todo.get("parent_id"))
        items.append(_existing_todo_item(todo, parent["text"] if parent else ""))
    if not parsed:
        items.append(_new_todo_item(text))
    items.append(_open_window_item())
    items.append(_keyboard_hint_item())
    return items


def on_action(request, plugin):
    data = (request.item or {}).get("data") or {}
    kind = data.get("kind")

    if kind == "toggle":
        todo_id = data.get("id")
        text = str(data.get("text", ""))
        done = app.toggle(todo_id)
        sticky_window.refresh()
        if done is None:
            return [winshun.notify("该待办已删除"), winshun.hide()]
        prefix = "已完成：" if done else "已取消完成："
        return [winshun.notify(prefix + text), winshun.hide()]

    if kind in ("add", "add-parsed"):
        text = str(data.get("text", "")).strip()
        if not text:
            return [winshun.notify("待办内容为空"), winshun.hide()]
        if kind == "add-parsed":
            app.add(
                text,
                remind_at=data.get("remind_at"),
                due_at=data.get("due_at"),
                priority=data.get("priority"),
                tags=data.get("tags"),
                repeat=data.get("repeat"),
            )
        else:
            app.add(text)
        sticky_window.refresh()
        app.show_sticky()
        return [winshun.notify("已添加：" + text), winshun.hide()]

    app.show_sticky(focus=True)
    return [winshun.hide()]


def on_tray_menu(plugin):
    """Reads memory only, so Win顺 gets the menu within a few milliseconds.

    Visibility comes from the window thread: the X button hides the note
    there, and app.visible would not see that.
    """
    visible = sticky_window.is_visible()
    locked = bool(app.config.get("locked"))
    return [
        {"id": "toggle", "title": "隐藏便签" if visible else "显示便签", "checked": visible},
        {"id": "lock", "title": "解锁便签" if locked else "锁定便签", "checked": locked},
        {"id": "new", "title": "新建待办"},
        {"id": "compact", "title": "紧凑模式", "checked": bool(app.config.get("compact"))},
        {"id": "stats", "title": "完成统计"},
        {"id": "export", "title": "导出待办…"},
        {"id": "import", "title": "导入待办…"},
    ]


def on_menu(menu_id, plugin):
    if menu_id == "toggle":
        app.toggle_sticky()
    elif menu_id == "new":
        app.show_sticky(focus=True)
    elif menu_id == "lock":
        locked = app.set_locked(not app.config.get("locked"))
        sticky_window.set_locked(locked)
    elif menu_id == "compact":
        compact = app.set_compact(not app.config.get("compact"))
        sticky_window.set_compact(compact)
    elif menu_id == "stats":
        sticky_window.show_stats()
    elif menu_id == "export":
        sticky_window.export_todos()
    elif menu_id == "import":
        sticky_window.import_todos()


def on_hotkey(key, plugin):
    # This second global hotkey remains usable while the note itself is
    # click-through. It is deliberately independent of the show/hide key.
    if str(key).casefold() == UNLOCK_HOTKEY.casefold():
        if app.config.get("locked"):
            app.set_locked(False)
            sticky_window.set_locked(False)
            try:
                winshun.notify("便签已解锁")
            except Exception:
                pass
        return
    if sticky_window.is_visible():
        app.hide_sticky()
    else:
        app.show_sticky(focus=True)


def on_settings(settings, plugin):
    app.apply_settings(settings)
    threading.Thread(target=register_hotkey, args=(app, plugin), daemon=True).start()


def on_initialized(plugin):
    app.plugin = plugin
    app.load(plugin.data_dir or winshun.data_dir())
    app.settings = dict(plugin.settings or {})
    appearance = getattr(plugin, "appearance", {})
    if not isinstance(appearance, dict):
        appearance = {}
    theme = getattr(plugin, "theme", "light")
    if theme not in ("light", "dark"):
        theme = "light"
    sticky_window.apply_appearance(appearance, theme)
    if app.settings.get("show_sticky", True):
        app.show_sticky()
    threading.Thread(target=register_hotkey, args=(app, plugin), daemon=True).start()
    start_reminders(app, plugin)


def on_appearance(appearance, plugin):
    """Keep the desktop note visually aligned with Win顺's current theme."""
    if not isinstance(appearance, dict):
        appearance = {}
    sticky_window.apply_appearance(appearance, getattr(plugin, "theme", "light"))


def main():
    winshun.serve(
        on_query,
        on_action,
        on_settings=on_settings,
        name="me.todo",
        on_tray_menu=on_tray_menu,
        on_menu=on_menu,
        on_hotkey=on_hotkey,
        on_initialized=on_initialized,
        on_appearance=on_appearance,
    )


if __name__ == "__main__":
    main()
