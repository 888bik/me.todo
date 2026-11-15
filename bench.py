"""Local performance baseline; run directly: python bench.py [counts...]."""

import queue
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta

import store
import sticky_window

ROUNDS = 7


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
        kind = index % 12
        remind = (now + timedelta(days=index % 60, minutes=index % 240)).isoformat(timespec="seconds")
        due = (now + timedelta(days=(index % 30) - 5)).isoformat(timespec="seconds")
        todo = {
            "id": "bench-%d" % index,
            "text": "任务%d 尽快处理这件事" % index,
            "done": index % 11 == 0,
            "created_at": remind,
            "remind_at": remind if kind not in (4, 8) else None,
            "reminded": False,
            "repeat": None,
            "repeat_base": None,
            "priority": index % 4,
            "tags": ["工作"] if index % 3 == 0 else ["生活", "临时"] if index % 7 == 0 else [],
            "note": "备注" if index % 8 == 0 else "",
            "parent_id": None,
            "due_at": due if kind not in (5, 9) else None,
        }
        if kind == 0:
            todo["repeat"] = {"type": "daily"}
            todo["repeat_base"] = (now - timedelta(days=730)).isoformat(timespec="seconds")
        elif kind == 1:
            todo["repeat"] = {"type": "weekly", "days": [0, 3]}
            todo["repeat_base"] = (now - timedelta(days=365)).isoformat(timespec="seconds")
        elif kind == 2 and index > 2:
            todo["parent_id"] = "bench-%d" % (index - 1)
        todos.append(todo)
    return todos


def timed(fn):
    started = time.perf_counter()
    value = fn()
    return time.perf_counter() - started, value


def summarize(samples):
    return (
        statistics.median(samples),
        min(samples),
        max(samples),
    )


def run_count(count):
    todos = make_todos(count)
    samples = {name: [] for name in (
        "startup", "refresh", "unchanged", "one_change", "search",
        "nav", "layout", "scroll", "month", "save",
    )}
    save_dir = tempfile.TemporaryDirectory(prefix="me.todo-bench-")
    try:
        for _ in range(ROUNDS):
            controller = _ListController([dict(todo) for todo in todos])
            sticky_window._load_tk()
            root = sticky_window.tk.Tk()
            try:
                elapsed, window = timed(lambda: sticky_window._StickyWindow(
                    root, queue.Queue(), controller, {"transparency": 100}
                ))
                root.update_idletasks()
                samples["startup"].append(elapsed)

                elapsed, _ = timed(window._render)
                root.update_idletasks()
                samples["refresh"].append(elapsed)

                elapsed, _ = timed(window._render)
                root.update_idletasks()
                samples["unchanged"].append(elapsed)

                controller.todos[0] = dict(controller.todos[0], text="单条变化")
                elapsed, _ = timed(window._render)
                root.update_idletasks()
                samples["one_change"].append(elapsed)

                window.search = "任务1"
                elapsed, _ = timed(window._render)
                root.update_idletasks()
                samples["search"].append(elapsed)
                window.search = ""

                elapsed, _ = timed(lambda: window._update_nav_counts(controller.list()))
                root.update_idletasks()
                samples["nav"].append(elapsed)

                window._layout_signature = None
                elapsed, _ = timed(lambda: window._render_virtual_list(controller.list()))
                root.update_idletasks()
                samples["layout"].append(elapsed)

                window.canvas.yview_moveto(0.5)
                elapsed, _ = timed(lambda: window._update_visible_rows(force=True))
                root.update_idletasks()
                samples["scroll"].append(elapsed)

                window._month_year, window._month_month = datetime.now().year, datetime.now().month
                elapsed, _ = timed(lambda: window._render_month(controller.list()))
                root.update_idletasks()
                samples["month"].append(elapsed)

                elapsed, _ = timed(lambda: store.save_todos(save_dir.name, controller.todos))
                samples["save"].append(elapsed)
            finally:
                root.destroy()
    finally:
        save_dir.cleanup()
    return {name: summarize(values) for name, values in samples.items()}


def main():
    counts = [int(value) for value in sys.argv[1:]] or [500, 1000, 2000, 5000, 10000]
    run_count(50)  # warm up Tk/font caches so the first row is comparable
    print("count  startup  refresh  unchanged  one_change  search  nav  layout  scroll  month  save")
    for count in counts:
        result = run_count(count)
        values = [result[name][0] for name in (
            "startup", "refresh", "unchanged", "one_change", "search",
            "nav", "layout", "scroll", "month", "save",
        )]
        print("%5d  %6.3fs  %6.3fs  %7.3fs  %8.3fs  %6.3fs  %6.3fs  %6.3fs  %6.3fs  %6.3fs  %6.3fs"
              % (count, *values))
        ranges = "  ranges(min/max): " + " ".join(
            "%s=%.3f/%.3f" % (name, result[name][1], result[name][2])
            for name in ("refresh", "unchanged", "search", "nav", "layout", "scroll")
        )
        print(ranges)


if __name__ == "__main__":
    main()
