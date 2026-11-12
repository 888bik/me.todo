"""Local performance baseline; run directly: python bench.py [counts...]."""

import queue
import sys
import time
from datetime import datetime, timedelta

import sticky_window


class _ListController:
    def __init__(self, todos):
        self.todos = todos

    def list(self):
        return [dict(todo) for todo in self.todos]

    def get_config(self):
        return {}

    def icon_path(self):
        return ""

    def icon_ico_path(self):
        return ""

    def save_window_position(self, *args, **kwargs):
        pass

    def set_view(self, view):
        pass

    def set_filter(self, key):
        pass

    def set_compact(self, value):
        pass

    def set_font_size(self, value):
        pass


def make_todos(count):
    now = datetime.now().astimezone()
    todos = []
    for index in range(count):
        kind = index % 10
        remind = (now + timedelta(days=index % 60, minutes=index % 240)).isoformat(timespec="seconds")
        todo = {
            "id": "bench-%d" % index,
            "text": "任务%d 尽快处理这件事" % index,
            "done": False,
            "created_at": remind,
            "remind_at": remind,
            "reminded": False,
            "repeat": None,
            "repeat_base": None,
            "priority": index % 4,
            "tags": ["工作"] if index % 3 == 0 else [],
            "note": "",
            "parent_id": None,
        }
        if kind == 0:
            todo["repeat"] = {"type": "daily"}
            todo["repeat_base"] = (now - timedelta(days=730)).isoformat(timespec="seconds")
        elif kind == 1:
            todo["repeat"] = {"type": "weekly", "days": [0, 3]}
            todo["repeat_base"] = (now - timedelta(days=365)).isoformat(timespec="seconds")
        todos.append(todo)
    return todos


def bench(count):
    sticky_window._load_tk()
    root = sticky_window.tk.Tk()
    started = time.perf_counter()
    window = sticky_window._StickyWindow(
        root, queue.Queue(), _ListController(make_todos(count)), {"transparency": 100}
    )
    root.update_idletasks()
    startup = time.perf_counter() - started

    started = time.perf_counter()
    window._render()
    root.update_idletasks()
    refresh = time.perf_counter() - started

    window.search = "任务1"
    started = time.perf_counter()
    window._render()
    root.update_idletasks()
    search = time.perf_counter() - started

    window.search = ""
    window._month_year, window._month_month = datetime.now().year, datetime.now().month
    started = time.perf_counter()
    window._render_month(window.controller.list())
    root.update_idletasks()
    month = time.perf_counter() - started
    root.destroy()
    return startup, refresh, search, month


def main():
    counts = [int(value) for value in sys.argv[1:]] or [500, 1000, 2000]
    bench(50)  # warm up Tk/font caches so the first row is comparable
    print("count  startup  refresh  search  month")
    for count in counts:
        startup, refresh, search, month = bench(count)
        print("%5d  %6.3fs  %6.3fs  %6.3fs  %6.3fs" % (count, startup, refresh, search, month))


if __name__ == "__main__":
    main()
