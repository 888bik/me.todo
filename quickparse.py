"""Natural quick-input parsing for `db ...` (dates, times, tags, priority).

Pure functions only: no I/O, no network, standard library.
"""

import calendar
import re
from datetime import date, datetime, time, timedelta

WEEKDAYS = "一二三四五六日"
_WEEKDAY_INDEX = {name: index for index, name in enumerate(WEEKDAYS)}
_WEEKDAY_INDEX["天"] = 6
_CN_NUMBERS = {name: index for index, name in enumerate("一二三四五六七八九十", start=1)}
_CN_NUMBERS["两"] = 2
_PERIODS = ("上午", "下午", "晚上", "中午", "凌晨", "半夜")
_BARE_PERIOD_TIMES = {
    "早上": (9, 0), "上午": (9, 0), "中午": (12, 0), "下午": (15, 0),
    "晚上": (19, 0), "凌晨": (6, 0), "半夜": (0, 0),
}


def _month_date(year, month, day):
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def _shift_month(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _describe_date(value, today):
    """Consume one date expression; returns (date or None, remaining text)."""

    def take(match, parsed):
        return parsed, (value[:match.start()] + " " + value[match.end():])

    match = re.search(r"大后天|后天|明天|今天", value)
    if match:
        offset = {"今天": 0, "明天": 1, "后天": 2, "大后天": 3}[match.group(0)]
        return take(match, today + timedelta(days=offset))

    match = re.search(r"(\d{1,3})\s*天后", value)
    if match:
        return take(match, today + timedelta(days=int(match.group(1))))

    match = re.search(r"(?<![\d一二三四五六七八九十两])([一二三四五六七八九十两])\s*天后", value)
    if match:
        return take(match, today + timedelta(days=_CN_NUMBERS[match.group(1)]))

    match = re.search(r"下个月\s*(\d{1,2})\s*号", value)
    if match:
        year, month = _shift_month(today.year, today.month, 1)
        return take(match, _month_date(year, month, int(match.group(1))))

    match = re.search(r"这个月\s*(\d{1,2})\s*号", value)
    if match:
        day = int(match.group(1))
        candidate = _month_date(today.year, today.month, day)
        if candidate < today:
            year, month = _shift_month(today.year, today.month, 1)
            candidate = _month_date(year, month, day)
        return take(match, candidate)

    match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*号", value)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            candidate = _month_date(today.year, month, day)
            if candidate < today:
                candidate = _month_date(today.year + 1, month, day)
            return take(match, candidate)

    match = re.search(r"(?:这个|这|那个|那)\s*(?:周|星期)\s*([一二三四五六日天])", value)
    if match:
        target = _WEEKDAY_INDEX[match.group(1)]
        offset = target - today.weekday()
        if offset < 0:
            offset += 7
        return take(match, today + timedelta(days=offset))

    match = re.search(r"下个\s*(?:周|星期)\s*([一二三四五六日天])", value)
    if match:
        target = _WEEKDAY_INDEX[match.group(1)]
        return take(match, today + timedelta(days=7 - today.weekday() + target))

    match = re.search(r"下周\s*([一二三四五六日天])", value)
    if match:
        target = _WEEKDAY_INDEX[match.group(1)]
        candidate = today + timedelta(days=7 - today.weekday() + target)
        return take(match, candidate)

    match = re.search(r"本周\s*([一二三四五六日天])", value)
    if match:
        target = _WEEKDAY_INDEX[match.group(1)]
        offset = target - today.weekday()
        if offset < 0:
            offset += 7
        return take(match, today + timedelta(days=offset))

    match = re.search(r"(?:周|星期)\s*([一二三四五六日天])", value)
    if match:
        target = _WEEKDAY_INDEX[match.group(1)]
        offset = (target - today.weekday()) % 7
        return take(match, today + timedelta(days=offset))

    match = re.search(r"下周", value)
    if match:
        return take(match, today + timedelta(days=7 - today.weekday()))

    match = re.search(r"下个月", value)
    if match:
        year, month = _shift_month(today.year, today.month, 1)
        return take(match, _month_date(year, month, today.day))

    match = re.search(r"(\d{1,2})\s*号", value)
    if match:
        day = int(match.group(1))
        if 1 <= day <= 31:
            candidate = _month_date(today.year, today.month, day)
            if candidate < today:
                year, month = _shift_month(today.year, today.month, 1)
                candidate = _month_date(year, month, day)
            return take(match, candidate)

    return None, value


def _convert_period(period, hour):
    if period in ("下午", "晚上"):
        if hour == 12:
            return 12
        if 1 <= hour <= 11:
            return hour + 12
        return None
    if period == "中午":
        if hour == 12:
            return 12
        if 1 <= hour <= 11:
            return hour + 12
        return None
    if period == "上午":
        return hour if 0 <= hour <= 12 else None
    if period in ("凌晨", "半夜"):
        return 0 if hour == 12 else hour
    return None


def _describe_time(value):
    """Consume one time expression; returns (time or None, remaining text)."""

    def take(match, parsed):
        return parsed, (value[:match.start()] + " " + value[match.end():])

    match = re.search(
        r"(上午|下午|晚上|中午|凌晨|半夜)\s*(\d{1,2})\s*点(?:\s*(半|(\d{1,2})\s*分))?",
        value,
    )
    if match:
        hour = _convert_period(match.group(1), int(match.group(2)))
        minute = 30 if match.group(3) == "半" else int(match.group(4) or 0)
        if hour is not None and 0 <= hour <= 23 and 0 <= minute <= 59:
            return take(match, time(hour, minute))
        return None, value

    match = re.search(
        r"(?<!\d)(\d{1,2})\s*点(?:\s*(半|(\d{1,2})\s*分))?", value
    )
    if match:
        hour = int(match.group(1))
        minute = 30 if match.group(2) == "半" else int(match.group(3) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return take(match, time(hour, minute))

    match = re.search(r"(?<!\d)(\d{1,2})\s*[:：]\s*(\d{1,2})(?!\d)", value)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return take(match, time(hour, minute))

    return None, value


def _describe_bare_period(value):
    """Period words only at the start or end; 下午茶 in the middle stays."""
    match = re.match(r"^\s*(早上|上午|中午|下午|晚上|凌晨|半夜)\s*", value)
    if match is None:
        match = re.search(r"(早上|上午|中午|下午|晚上|凌晨|半夜)\s*$", value)
    if match is None:
        return None, value
    hour, minute = _BARE_PERIOD_TIMES[match.group(1)]
    return time(hour, minute), (value[:match.start()] + " " + value[match.end():])


def _describe_repeat(value, today):
    def take(match, repeat):
        return repeat, (value[:match.start()] + " " + value[match.end():])

    match = re.search(r"每\s*个?\s*工作日|工作日", value)
    if match:
        return take(match, {"type": "weekdays"})

    match = re.search(r"每\s*天", value)
    if match:
        return take(match, {"type": "daily"})

    match = re.search(r"每\s*周\s*([一二三四五六日天])", value)
    if match:
        return take(match, {"type": "weekly", "days": [_WEEKDAY_INDEX[match.group(1)]]})

    match = re.search(r"每\s*周", value)
    if match:
        return take(match, {"type": "weekly", "days": [today.weekday()]})

    match = re.search(r"每\s*个?\s*月\s*(\d{1,2})\s*号", value)
    if match:
        day = int(match.group(1))
        if 1 <= day <= 31:
            return take(match, {"type": "monthly", "day": day})
        return None, value

    match = re.search(r"每\s*个?\s*月\s*([一二三四五六七八九十两])\s*号", value)
    if match:
        return take(match, {"type": "monthly", "day": _CN_NUMBERS[match.group(1)]})

    match = re.search(r"每\s*个?\s*月", value)
    if match:
        return take(match, {"type": "monthly", "day": today.day})

    return None, value


def _repeat_first_date(repeat, today):
    if repeat["type"] == "weekly":
        target = int(repeat["days"][0])
        return today + timedelta(days=(target - today.weekday()) % 7)
    if repeat["type"] == "monthly":
        candidate = _month_date(today.year, today.month, int(repeat["day"]))
        if candidate < today:
            year, month = _shift_month(today.year, today.month, 1)
            candidate = _month_date(year, month, int(repeat["day"]))
        return candidate
    return today


def parse_quick_input(text, now=None):
    """Parse `db` text into title/date/time/tags/priority, or None."""
    raw = str(text or "").strip()
    if not raw:
        return None
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    today = now.date()
    working = raw

    tags = []
    words = []
    for token in working.split():
        if token.startswith("#") and 1 <= len(token) - 1 <= 10 and "#" not in token[1:]:
            tag = token[1:].strip()
            if tag and tag not in tags and len(tags) < 5:
                tags.append(tag)
            continue
        words.append(token)
    working = " ".join(words)

    priority = 0
    for match in re.finditer(r"!+", working):
        priority = max(priority, min(3, len(match.group(0))))
    working = re.sub(r"!+", " ", working)
    if "紧急" in working:
        priority = 3
        working = working.replace("紧急", " ")

    repeat, working = _describe_repeat(working, today)
    day_value, working = _describe_date(working, today)
    time_value, working = _describe_time(working)
    if time_value is None:
        time_value, working = _describe_bare_period(working)
    if repeat is not None and day_value is None:
        day_value = _repeat_first_date(repeat, today)

    title = re.sub(r"\s+", " ", working).strip(" ,，。;；")
    if not title:
        return None
    if day_value is None and time_value is None and not tags and not priority and repeat is None:
        return None

    remind_at = due_at = None
    if day_value is not None and time_value is not None:
        moment = datetime.combine(day_value, time_value).astimezone()
        remind_at = due_at = moment.isoformat(timespec="seconds")
    elif day_value is not None:
        due_at = datetime.combine(day_value, time(23, 59)).astimezone().isoformat(timespec="seconds")
        remind_at = datetime.combine(day_value, time(9, 0)).astimezone().isoformat(timespec="seconds")
    elif time_value is not None:
        moment = datetime.combine(today, time_value).astimezone()
        if moment <= now:
            moment += timedelta(days=1)
        remind_at = due_at = moment.isoformat(timespec="seconds")

    return {
        "text": title,
        "remind_at": remind_at,
        "due_at": due_at,
        "tags": tags,
        "priority": priority,
        "repeat": repeat,
        "raw": raw,
    }


def format_preview(value, now=None):
    """今天/明天/后天, weekday within a week, then M-D, plus HH:MM."""
    if value is None:
        return ""
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return ""
    if value.tzinfo is None:
        value = value.astimezone()
    clock = value.strftime("%H:%M")
    delta = (value.date() - now.date()).days
    if delta == 0:
        return "今天 " + clock
    if delta == 1:
        return "明天 " + clock
    if delta == 2:
        return "后天 " + clock
    if 0 <= delta <= 6:
        return "周" + WEEKDAYS[value.weekday()] + " " + clock
    return "%d-%d %s" % (value.month, value.day, clock)
