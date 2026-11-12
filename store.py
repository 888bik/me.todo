"""me.todo storage: todos.json and config.json under WS_DATA_DIR.

No network, no clipboard, no files outside the plugin data folder.
"""

import calendar
import json
import os
import uuid
from datetime import date, datetime, timedelta

TODO_KEYS = (
    "id", "text", "done", "created_at", "remind_at", "reminded", "repeat",
    "priority", "pinned", "tags", "parent_id", "note", "due_at", "completed_at", "repeat_base",
)
DEFAULT_CONFIG = {
    "window_x": None, "window_y": None,
    "window_width": None, "window_height": None,
    "font_size": 10, "view": "list", "locked": False, "filter": "all", "compact": False,
    "transparency": None,
}
FONT_MIN, FONT_MAX, FONT_DEFAULT = 9, 14, 10
VIEWS = ("list", "today", "week", "month")
FILTERS = ("all", "today", "overdue", "week7", "nodate", "done")
WEEKDAYS = "一二三四五六日"


def normalize_filter(value):
    return value if value in FILTERS else "all"


def _font_size(value):
    if isinstance(value, int) and not isinstance(value, bool) and FONT_MIN <= value <= FONT_MAX:
        return value
    return FONT_DEFAULT


def normalize_view(value):
    return value if value in VIEWS else "list"


def _priority(value):
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 3:
        return value
    return 0


def normalize_tags(value):
    """Strip, dedupe, drop bad entries, keep at most five 1-10 char tags."""
    if not isinstance(value, list):
        return []
    tags = []
    for item in value:
        if not isinstance(item, str):
            continue
        tag = item.strip()
        if 1 <= len(tag) <= 10 and tag not in tags:
            tags.append(tag)
        if len(tags) >= 5:
            break
    return tags


def parse_dt(iso):
    """ISO string -> aware datetime (naive counts as local); None when unparsable."""
    if not iso:
        return None
    try:
        value = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.astimezone()
    return value


def effective_due(todo):
    """The one deadline used by sorting, overdue, calendar and today view."""
    return todo.get("due_at") or todo.get("remind_at")


def effective_due_datetime(todo):
    return parse_dt(effective_due(todo))


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _path(data_dir, name):
    return os.path.join(str(data_dir), name)


def _atomic_write(path, payload):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


# ---- config.json ---------------------------------------------------------------

def load_config(data_dir):
    config = dict(DEFAULT_CONFIG)
    try:
        with open(_path(data_dir, "config.json"), encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return config
    if isinstance(raw, dict):
        for axis in ("window_x", "window_y"):
            value = raw.get(axis)
            if isinstance(value, int) and not isinstance(value, bool):
                config[axis] = value
        for axis, minimum, maximum in (("window_width", 240, 4000), ("window_height", 260, 3000)):
            value = raw.get(axis)
            if isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum:
                config[axis] = value
        config["font_size"] = _font_size(raw.get("font_size"))
        config["view"] = normalize_view(raw.get("view"))
        config["locked"] = bool(raw.get("locked", False))
        config["filter"] = normalize_filter(raw.get("filter"))
        config["compact"] = bool(raw.get("compact", False))
        transparency = raw.get("transparency")
        if isinstance(transparency, int) and not isinstance(transparency, bool) and 50 <= transparency <= 100:
            config["transparency"] = transparency
    return config


def save_config(data_dir, config):
    payload = {}
    for axis in ("window_x", "window_y"):
        value = config.get(axis)
        if isinstance(value, int) and not isinstance(value, bool):
            payload[axis] = value
    for axis, minimum, maximum in (("window_width", 240, 4000), ("window_height", 260, 3000)):
        value = config.get(axis)
        if isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum:
            payload[axis] = value
    payload["font_size"] = _font_size(config.get("font_size"))
    payload["view"] = normalize_view(config.get("view"))
    payload["locked"] = bool(config.get("locked", False))
    payload["filter"] = normalize_filter(config.get("filter"))
    payload["compact"] = bool(config.get("compact", False))
    transparency = config.get("transparency")
    if isinstance(transparency, int) and not isinstance(transparency, bool) and 50 <= transparency <= 100:
        payload["transparency"] = transparency
    _atomic_write(_path(data_dir, "config.json"), payload)


# ---- todos.json ----------------------------------------------------------------

def _backup(path):
    """A broken todos.json is kept as todos.json.bak and replaced by an empty list."""
    try:
        os.replace(path, path + ".bak")
    except OSError:
        try:
            os.remove(path)
        except OSError:
            pass


def _clean_repeat(raw):
    """None for one-shot; a valid repeat dict; a malformed one is dropped (todo kept)."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind in ("daily", "weekdays"):
        return {"type": kind}
    if kind == "weekly":
        days = raw.get("days")
        if not isinstance(days, list) or not days:
            return None
        if not all(isinstance(day, int) and not isinstance(day, bool) and 0 <= day <= 6 for day in days):
            return None
        return {"type": "weekly", "days": sorted(set(days))}
    if kind == "monthly":
        day = raw.get("day")
        if isinstance(day, int) and not isinstance(day, bool) and 1 <= day <= 31:
            return {"type": "monthly", "day": day}
        return None
    return None


def normalize_repeat(value):
    """Public wrapper: a valid repeat dict, or None for one-shot/malformed."""
    return _clean_repeat(value)


def _clean(raw):
    if not isinstance(raw, dict):
        return None
    todo_id = raw.get("id")
    text = raw.get("text")
    if not isinstance(todo_id, str) or not todo_id:
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    remind_at = raw.get("remind_at")
    if remind_at is not None and not isinstance(remind_at, str):
        return None
    created = raw.get("created_at")
    parent = raw.get("parent_id")

    def optional_time(key):
        value = raw.get(key)
        return value if isinstance(value, str) else None

    return {
        "id": todo_id,
        "text": text.strip(),
        "done": bool(raw.get("done", False)),
        "created_at": created if isinstance(created, str) else now_iso(),
        "remind_at": remind_at,
        "reminded": bool(raw.get("reminded", False)),
        "repeat": _clean_repeat(raw.get("repeat")),
        "priority": _priority(raw.get("priority")),
        "pinned": bool(raw.get("pinned", False)),
        "tags": normalize_tags(raw.get("tags")),
        "parent_id": parent if isinstance(parent, str) and parent else None,
        "note": raw.get("note") if isinstance(raw.get("note"), str) else "",
        "due_at": optional_time("due_at"),
        "completed_at": optional_time("completed_at"),
        "repeat_base": optional_time("repeat_base"),
    }


def load_todos(data_dir):
    path = _path(data_dir, "todos.json")
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        _backup(path)
        return []

    if not isinstance(raw, list):
        _backup(path)
        return []

    todos = []
    for entry in raw:
        todo = _clean(entry)
        if todo is not None:
            todos.append(todo)

    # One subtask level only: the parent must exist and not be a child itself.
    ids = {todo["id"] for todo in todos}
    parents = {todo["id"]: todo.get("parent_id") for todo in todos}
    for todo in todos:
        parent = todo.get("parent_id")
        if parent and (parent == todo["id"] or parent not in ids or parents.get(parent)):
            todo["parent_id"] = None
    return todos


def save_todos(data_dir, todos):
    payload = [{key: todo.get(key) for key in TODO_KEYS} for todo in todos]
    _atomic_write(_path(data_dir, "todos.json"), payload)


def new_todo(text, remind_at=None, repeat=None, tags=None, priority=0, pinned=False,
             note="", due_at=None, parent_id=None):
    repeat = _clean_repeat(repeat)
    return {
        "id": uuid.uuid4().hex,
        "text": str(text).strip(),
        "done": False,
        "created_at": now_iso(),
        "remind_at": remind_at,
        "reminded": False,
        "repeat": repeat,
        "priority": _priority(priority),
        "pinned": bool(pinned),
        "tags": normalize_tags(tags),
        "parent_id": parent_id if isinstance(parent_id, str) and parent_id else None,
        "note": str(note or ""),
        "due_at": due_at if isinstance(due_at, str) else None,
        "completed_at": None,
        "repeat_base": remind_at if repeat else None,
    }


# ---- reminders -----------------------------------------------------------------

def parse_reminder(text):
    """'2026-11-11 10:30' -> ISO string with the local time zone. Raises ValueError."""
    parsed = datetime.strptime(str(text).strip(), "%Y-%m-%d %H:%M").astimezone()
    return parsed.isoformat(timespec="seconds")


def format_reminder(iso):
    """'2026-11-11 09:30' or, when seconds matter, '2026-11-11 09:30:15'."""
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if when.second or when.microsecond:
        return when.strftime("%Y-%m-%d %H:%M:%S")
    return when.strftime("%Y-%m-%d %H:%M")


def _monthly_candidate(year, month, day, after):
    last = calendar.monthrange(year, month)[1]
    return after.replace(year=year, month=month, day=min(day, last))


def next_occurrence(repeat, after):
    """Strictly after `after`, same time of day. None for an invalid repeat.

    Monthly is anchored to the chosen day, not to the clamped one: 31 -> Feb 28
    -> Mar 31, never drifting to the 28th.
    """
    if not isinstance(repeat, dict):
        return None
    kind = repeat.get("type")

    if kind == "daily":
        return after + timedelta(days=1)

    if kind == "weekdays":
        candidate = after + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    if kind == "weekly":
        days = set(repeat.get("days") or [])
        if not days:
            return None
        candidate = after + timedelta(days=1)
        for _ in range(7):
            if candidate.weekday() in days:
                return candidate
            candidate += timedelta(days=1)
        return None

    if kind == "monthly":
        day = repeat.get("day")
        if not isinstance(day, int) or not 1 <= day <= 31:
            return None
        year, month = after.year, after.month
        for _ in range(60):
            candidate = _monthly_candidate(year, month, day, after)
            if candidate > after:
                return candidate
            month += 1
            if month > 12:
                month, year = 1, year + 1
        return None

    return None


def occurrences(todo, start_date, end_date):
    """Dates this todo is due on, between two dates inclusive.

    One-shot todos use the effective due (due_at, else remind_at); repeating
    ones walk from repeat_base so a snooze never shifts the schedule. A
    finished repeating todo stops at its completion date: later dates belong
    to the instance spawned when it was checked off.
    """
    repeat = todo.get("repeat")
    if not repeat:
        when = effective_due_datetime(todo)
        if when is None:
            return []
        return [when.date()] if start_date <= when.date() <= end_date else []

    done = bool(todo.get("done"))
    completed = parse_dt(todo.get("completed_at")) if done else None
    if done and completed is None:
        # No usable completion time: fall back to a single due date.
        due = effective_due_datetime(todo)
        if due is None:
            return []
        return [due.date()] if start_date <= due.date() <= end_date else []

    end_limit = min(end_date, completed.date()) if completed is not None else end_date
    if start_date > end_limit:
        return []

    when = parse_dt(todo.get("repeat_base") or todo.get("remind_at") or todo.get("due_at"))
    if when is None:
        return []

    base_date = when.date()
    first_date = max(base_date, start_date)
    if first_date > end_limit:
        return []

    kind = repeat.get("type")
    dates = []
    steps = 0

    if kind == "daily":
        current = first_date
        while current <= end_limit and steps < 10000:
            dates.append(current)
            current += timedelta(days=1)
            steps += 1
    elif kind == "weekdays":
        current = first_date
        while current <= end_limit and steps < 10000:
            if current.weekday() < 5:
                dates.append(current)
            current += timedelta(days=1)
            steps += 1
    elif kind == "weekly":
        days = set(repeat.get("days") or [])
        if not days:
            return []
        current = first_date
        while current <= end_limit and steps < 10000:
            if current.weekday() in days:
                dates.append(current)
            current += timedelta(days=1)
            steps += 1
    elif kind == "monthly":
        day = repeat.get("day")
        if not isinstance(day, int) or isinstance(day, bool) or not 1 <= day <= 31:
            return []
        year, month = first_date.year, first_date.month
        while steps < 10000:
            last = calendar.monthrange(year, month)[1]
            candidate = date(year, month, min(day, last))
            if candidate > end_limit:
                break
            if candidate >= first_date:
                dates.append(candidate)
            month += 1
            if month > 12:
                month, year = 1, year + 1
            steps += 1
    else:
        return []
    return dates


def format_repeat(repeat):
    """'每天' / '工作日' / '每周一、三、五' / '每月15日'; empty for one-shot."""
    if not isinstance(repeat, dict):
        return ""
    kind = repeat.get("type")
    if kind == "daily":
        return "每天"
    if kind == "weekdays":
        return "工作日"
    if kind == "weekly":
        days = sorted(set(repeat.get("days") or []))
        if not days:
            return ""
        return "每周" + "、".join(WEEKDAYS[day] for day in days)
    if kind == "monthly":
        day = repeat.get("day")
        if isinstance(day, int) and not isinstance(day, bool) and 1 <= day <= 31:
            return "每月%d日" % day
    return ""


def due_todos(todos, now=None):
    now = now or datetime.now().astimezone()
    due = []
    for todo in todos:
        if todo.get("done") or todo.get("reminded") or not todo.get("remind_at"):
            continue
        try:
            when = datetime.fromisoformat(todo["remind_at"])
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.astimezone()
        if when <= now:
            due.append(todo)
    return due


def matches(todo, query, today=None, now=None):
    """Every whitespace-separated term must hit; #term compares tags only.

    Adds small search operators: is:done/open, due:today/tomorrow/overdue/
    week/none and priority:high/medium/low/none/0..3. Unknown x:y terms stay
    ordinary text so a colon in a title still searches normally.
    """
    query = str(query or "").strip()
    if not query:
        return True
    today = today or date.today()
    now = now or datetime.now().astimezone()
    text = str(todo.get("text", "")).lower()
    tags = [str(tag).lower() for tag in (todo.get("tags") or [])]

    def operator(term):
        low = term.lower()
        if low == "is:done":
            return bool(todo.get("done"))
        if low == "is:open":
            return not todo.get("done")
        if low.startswith("due:"):
            value = low[4:]
            due = effective_due_datetime(todo)
            if value == "none":
                return due is None
            if due is None:
                return False
            if value == "today":
                return due.date() == today
            if value == "tomorrow":
                return due.date() == today + timedelta(days=1)
            if value == "overdue":
                return (not todo.get("done")) and due < now
            if value == "week":
                return today <= due.date() <= today + timedelta(days=7)
            return None
        if low.startswith("priority:"):
            value = low[9:]
            mapping = {"high": 3, "medium": 2, "low": 1, "none": 0}
            if value in mapping:
                needed = mapping[value]
            elif value.isdigit() and 0 <= int(value) <= 3:
                needed = int(value)
            else:
                return None
            return _priority(todo.get("priority")) == needed
        return None

    for term in query.split():
        if term.startswith("#"):
            needle = term[1:].lower()
            if not any(needle in tag for tag in tags):
                return False
            continue
        result = operator(term)
        if result is not None:
            if not result:
                return False
            continue
        needle = term.lower()
        if needle not in text and not any(needle in tag for tag in tags):
            return False
    return True


def apply_filter(todos, filter_key, today=None, now=None):
    """Filter the ordered list for the note's filter row."""
    key = normalize_filter(filter_key)
    if key == "all":
        return list(todos)
    today = today or date.today()
    now = now or datetime.now().astimezone()
    result = []
    for todo in todos:
        due = effective_due_datetime(todo)
        done = bool(todo.get("done"))
        if key == "today":
            if (due is not None and due.date() == today) or (
                today in occurrences(todo, today, today)
            ):
                result.append(todo)
        elif key == "overdue":
            if not done and due is not None and due < now:
                result.append(todo)
        elif key == "week7":
            if not done and due is not None and today <= due.date() <= today + timedelta(days=7):
                result.append(todo)
        elif key == "nodate":
            if effective_due(todo) is None:
                result.append(todo)
        elif key == "done":
            if done:
                result.append(todo)
    return result


def completion_stats(todos, today=None):
    """Counts by completion day; only what is still in the list is counted."""
    today = today or date.today()
    days = []
    for todo in todos:
        when = parse_dt(todo.get("completed_at"))
        if when is not None:
            days.append(when.date())

    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    done_days = set(days)
    cursor = today if today in done_days else today - timedelta(days=1)
    streak = 0
    while cursor in done_days:
        streak += 1
        cursor -= timedelta(days=1)

    return {
        "today": sum(1 for day in days if day == today),
        "week": sum(1 for day in days if week_start <= day <= week_end),
        "month": sum(1 for day in days if day.year == today.year and day.month == today.month),
        "total": sum(1 for todo in todos if todo.get("done")),
        "streak": streak,
    }


def export_todos(data_dir, path):
    """Write the whole list to one JSON file; returns how many were written."""
    todos = load_todos(data_dir)
    payload = {"version": 1, "exported_at": now_iso(), "todos": todos}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return len(todos)


def import_todos(data_dir, path):
    """Merge a previously exported file by id; returns (added, skipped)."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError("无法读取导入文件") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("todos"), list):
        raise ValueError("导入文件格式不对")

    existing = load_todos(data_dir)
    ids = {todo["id"] for todo in existing}
    added = skipped = 0
    for entry in payload["todos"]:
        todo = _clean(entry)
        if todo is None or todo["id"] in ids:
            skipped += 1
            continue
        existing.append(todo)
        ids.add(todo["id"])
        added += 1

    for todo in existing:
        parent = todo.get("parent_id")
        if parent and parent not in ids:
            todo["parent_id"] = None
    save_todos(data_dir, existing)
    return added, skipped
