import calendar
import os
import queue
import threading
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

import sticky_window


class ViewGeometryTests(unittest.TestCase):
    def test_switching_to_a_compact_view_keeps_a_larger_window(self):
        self.assertEqual(sticky_window.size_for_view(920, 720, "list"), (920, 720))

    def test_switching_views_preserves_a_user_shrunk_list_window(self):
        self.assertEqual(sticky_window.size_for_view(300, 350, "list"), (600, 420))

    def test_switching_to_week_expands_only_to_its_minimum_size(self):
        self.assertEqual(sticky_window.size_for_view(420, 500, "week"), (600, 500))

    def test_switching_to_month_expands_both_dimensions_as_needed(self):
        self.assertEqual(sticky_window.size_for_view(800, 420, "month"), (800, 420))

    def test_default_window_size_and_minimum(self):
        root = _tk_root()
        self.addCleanup(root.destroy)
        window = sticky_window._StickyWindow(
            root, queue.Queue(), _Controller([]), {"transparency": 100}
        )
        window._dispatch(("show", True))
        root.update()

        self.assertTrue(root.wm_geometry().startswith("720x520"))
        self.assertEqual(tuple(root.minsize()), (600, 420))

    def test_saved_window_narrower_than_the_minimum_is_widened(self):
        root = _tk_root()
        self.addCleanup(root.destroy)
        controller = _Controller([])
        controller.get_config = lambda: {"window_width": 400, "window_height": 500}
        window = sticky_window._StickyWindow(root, queue.Queue(), controller, {"transparency": 100})
        window._dispatch(("show", True))
        root.update()

        self.assertTrue(root.wm_geometry().startswith("600x500"))


class DpiScalingTests(unittest.TestCase):
    def test_factor_uses_actual_dpi_and_clamps(self):
        root = mock.MagicMock()
        root.winfo_fpixels.return_value = 144
        self.assertAlmostEqual(sticky_window.dpi_scale(root), 2.0)

        root.winfo_fpixels.return_value = 48
        self.assertAlmostEqual(sticky_window.dpi_scale(root), 1.0)

        root.winfo_fpixels.return_value = 400
        self.assertAlmostEqual(sticky_window.dpi_scale(root), 2.5)

    def test_scaling_is_applied_to_tk(self):
        root = mock.MagicMock()
        root.winfo_fpixels.return_value = 192

        factor = sticky_window._apply_dpi_scaling(root)

        self.assertAlmostEqual(factor, 2.5)
        root.tk.call.assert_called_once_with("tk", "scaling", 2.5)


class AppearancePaletteTests(unittest.TestCase):
    def setUp(self):
        self.appearance = dict(sticky_window._current_appearance)
        self.theme = sticky_window._current_theme

    def tearDown(self):
        sticky_window._set_palette(self.appearance, self.theme)

    def test_dark_theme_has_a_dark_fallback_palette(self):
        sticky_window._set_palette({}, "dark")

        self.assertEqual(sticky_window.BG, "#202124")
        self.assertEqual(sticky_window.TEXT, "#E8EAED")
        self.assertEqual(sticky_window.ACCENT, "#8AB4F8")

    def test_native_look_overrides_the_fallback_palette(self):
        sticky_window._set_palette(
            {
                "nativeLook": {
                    "background": "#FF101820",
                    "layer": "#FF202830",
                    "divider": "#FF304050",
                    "text": "#FFF0F0F0",
                    "subtext": "#FFB0B8C0",
                    "accent": "#FF80A0C0",
                    "danger": "#FFFF7060",
                }
            },
            "dark",
        )

        self.assertEqual(sticky_window.BG, "#101820")
        self.assertEqual(sticky_window.CARD, "#202830")
        self.assertEqual(sticky_window.BORDER, "#304050")
        self.assertEqual(sticky_window.TEXT, "#F0F0F0")
        self.assertEqual(sticky_window.ACCENT, "#80A0C0")
        self.assertEqual(sticky_window.DANGER, "#FF7060")

    def test_translucent_host_colors_are_composited_to_opaque_rgb(self):
        self.assertEqual(sticky_window._color("#80FFFFFF", "#000000", "#000000"), "#808080")


class AlphaTests(unittest.TestCase):
    def test_percentage_becomes_alpha(self):
        self.assertAlmostEqual(sticky_window._alpha_value(95), 0.95)
        self.assertAlmostEqual(sticky_window._alpha_value(50), 0.5)
        self.assertAlmostEqual(sticky_window._alpha_value(100), 1.0)

    def test_out_of_range_is_clamped(self):
        self.assertAlmostEqual(sticky_window._alpha_value(10), 0.5)
        self.assertAlmostEqual(sticky_window._alpha_value(200), 1.0)
        self.assertAlmostEqual(sticky_window._alpha_value("nonsense"), 0.95)


class _Controller:
    def __init__(self, todos):
        self.todos = [dict(todo) for todo in todos]
        self.added = []
        self.saved_position = None
        self.reminders = []
        self.font_sizes = []
        self.views = []
        self.filters = []
        self.compacts = []
        self.transparencies = []
        self.deleted = []
        self.edited = []
        self.undone = 0
        self.added_parents = []
        self.dues = []
        self.snoozes = []
        self.details = []
        self.locked = None
        self.data_dir = ""

    def list(self):
        return [dict(todo) for todo in self.todos]

    def get_config(self):
        return {"window_x": 40, "window_y": 60}

    def icon_path(self):
        return ""

    def save_window_position(self, x, y, width=None, height=None):
        self.saved_position = (x, y, width, height)

    def add(self, text, parent_id=None):
        self.added.append(text)
        self.added_parents.append(parent_id)
        return {
            "id": "new%d" % len(self.added), "text": text, "done": False,
            "remind_at": None, "reminded": False, "parent_id": parent_id,
        }

    def toggle(self, todo_id):
        for todo in self.todos:
            if todo["id"] == todo_id:
                todo["done"] = not todo["done"]
                return todo["done"]
        return None

    def delete(self, todo_id):
        for index, todo in enumerate(self.todos):
            if todo["id"] == todo_id:
                self.todos.pop(index)
                self.deleted.append(todo_id)
                return True
        return False

    def edit_text(self, todo_id, text):
        self.edited.append((todo_id, text))
        for todo in self.todos:
            if todo["id"] == todo_id:
                todo["text"] = text
        return True

    def clear_done(self):
        done = [todo for todo in self.todos if todo["done"]]
        self.todos = [todo for todo in self.todos if not todo["done"]]
        return len(done)

    def undo_delete(self):
        self.undone += 1
        return True

    def set_remind(self, todo_id, iso, repeat=None):
        self.reminders.append((todo_id, iso, repeat))

    def set_font_size(self, size):
        self.font_sizes.append(size)

    def set_view(self, view):
        self.views.append(view)

    def set_filter(self, filter_key):
        self.filters.append(filter_key)

    def set_compact(self, compact):
        self.compacts.append(bool(compact))

    def set_transparency(self, value):
        self.transparencies.append(int(value))

    def set_due(self, todo_id, iso):
        self.dues.append((todo_id, iso))
        for todo in self.todos:
            if todo["id"] == todo_id:
                todo["due_at"] = iso
        return True

    def snooze(self, todo_id, iso):
        self.snoozes.append((todo_id, iso))
        for todo in self.todos:
            if todo["id"] == todo_id:
                todo["remind_at"] = iso
        return True

    def update_details(self, todo_id, priority=None, pinned=None, tags=None, note=None):
        self.details.append((todo_id, priority, pinned, tags, note))
        for todo in self.todos:
            if todo["id"] == todo_id:
                if priority is not None:
                    todo["priority"] = priority
                if pinned is not None:
                    todo["pinned"] = pinned
                if tags is not None:
                    todo["tags"] = tags
                if note is not None:
                    todo["note"] = note
        return True

    def set_locked(self, locked):
        self.locked = locked


def _tk_root():
    try:
        sticky_window._load_tk()
        root = sticky_window.tk.Tk()
    except Exception as exc:  # depends on the machine having a display
        raise unittest.SkipTest("没有可用的 Tk：%s" % (exc,))
    root.withdraw()
    return root


class DialogPlacementTests(unittest.TestCase):
    def test_dialog_is_centred_over_the_note(self):
        x, y = sticky_window._dialog_position(100, 200, 320, 420, 200, 300)

        self.assertEqual(x, 160)
        self.assertEqual(y, 240)


class MonthGridTests(unittest.TestCase):
    def test_six_weeks_of_seven_days(self):
        grid = sticky_window._month_grid(2026, 11)

        self.assertEqual(len(grid), 6)
        self.assertTrue(all(len(week) == 7 for week in grid))

    def test_first_of_month_sits_on_its_weekday(self):
        grid = sticky_window._month_grid(2026, 11)
        flat = [day for week in grid for day in week]

        self.assertEqual(flat.index(1), calendar.monthrange(2026, 11)[0])

    def test_every_day_appears_once(self):
        flat = [day for week in sticky_window._month_grid(2026, 11) for day in week if day]

        self.assertEqual(flat, list(range(1, 31)))


class ViewTests(unittest.TestCase):
    def setUp(self):
        sticky_window._state["visible"] = False
        self.root = _tk_root()
        self.addCleanup(self.root.destroy)
        self.controller = _Controller(
            [{"id": "a", "text": "买牛奶", "done": False, "remind_at": None, "reminded": False}]
        )

    def make_window(self):
        return sticky_window._StickyWindow(
            self.root, queue.Queue(), self.controller, {"transparency": 100}
        )

    def body_labels(self, window):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, sticky_window.tk.Label):
                    found.append(child)
                walk(child)

        walk(window.body)
        return found

    def body_buttons(self, window):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, sticky_window.tk.Button):
                    found.append(child)
                walk(child)

        walk(window.body)
        return found

    def labels(self, window):
        return self.body_labels(window)

    def test_switching_to_week_resizes_and_records_the_view(self):
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()

        window._set_view("week")
        window.root.update()

        self.assertEqual(window.view, "week")
        self.assertEqual(self.controller.views, ["week"])
        self.assertTrue(window.root.wm_geometry().startswith("720x520"))

    def test_switching_to_month_resizes(self):
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()

        window._set_view("month")
        window.root.update()

        self.assertTrue(window.root.wm_geometry().startswith("720x520"))

    def test_week_view_shows_every_daily_occurrence(self):
        self.controller.todos = [{
            "id": "d", "text": "每天的事", "done": False,
            "remind_at": datetime(2026, 1, 5, 9, 0).astimezone().isoformat(),
            "repeat": {"type": "daily"}, "reminded": False,
        }]
        window = self.make_window()
        window._week_start = date(2026, 1, 5)

        window._set_view("week")

        texts = [label.cget("text") for label in self.body_labels(window)]
        self.assertEqual(texts.count("每天的事"), 7)

    def test_month_view_shows_two_titles_and_a_more_hint(self):
        self.controller.todos = [
            {
                "id": "m%d" % index, "text": "事%d" % index, "done": False,
                "remind_at": datetime(2026, 1, 5, 9, 0).astimezone().isoformat(),
                "repeat": None, "reminded": False,
            }
            for index in range(3)
        ]
        window = self.make_window()
        window._month_year, window._month_month = 2026, 1

        window._set_view("month")

        texts = [label.cget("text") for label in self.body_labels(window)]
        self.assertIn("事0", texts)
        self.assertIn("事1", texts)
        self.assertNotIn("事2", texts)
        more = [button for button in self.body_buttons(window) if str(button.cget("text")) == "+1 更多"]
        self.assertEqual(len(more), 1)


class V3WindowTests(unittest.TestCase):
    def setUp(self):
        sticky_window._state["visible"] = False
        self.root = _tk_root()
        self.addCleanup(self.root.destroy)
        self.controller = _Controller([
            {"id": "a", "text": "买牛奶", "done": False, "remind_at": None, "reminded": False},
        ])

    def make_window(self):
        return sticky_window._StickyWindow(
            self.root, queue.Queue(), self.controller, {"transparency": 100}
        )

    def body_labels(self, window):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, sticky_window.tk.Label):
                    found.append(child)
                walk(child)

        walk(window.body)
        return found

    def labels(self, window):
        return self.body_labels(window)

    def buttons_in(self, widget):
        found = []

        def walk(current):
            for child in current.winfo_children():
                if isinstance(child, sticky_window.tk.Button):
                    found.append(child)
                walk(child)

        walk(widget)
        return found

    def test_click_through_sets_and_clears_the_transparent_bit(self):
        window = self.make_window()
        fake = mock.MagicMock()
        fake.GetParent.return_value = 123
        fake.GetWindowLongPtrW.return_value = 0x100
        fake.SetWindowLongPtrW.return_value = 0x100
        fake.SetWindowPos.return_value = 1

        sticky_window._set_click_through(window.root, True, user32=fake)
        enabled = fake.SetWindowLongPtrW.call_args.args[2]
        sticky_window._set_click_through(window.root, False, user32=fake)
        disabled = fake.SetWindowLongPtrW.call_args.args[2]

        self.assertTrue(enabled & 0x20)
        self.assertFalse(disabled & 0x20)

    def test_hook_bounds_read_the_cached_rect_without_tk(self):
        window = self.make_window()
        with mock.patch.object(window.root, "winfo_rootx", return_value=10) as rootx, \
                mock.patch.object(window.root, "winfo_rooty", return_value=20), \
                mock.patch.object(window.root, "winfo_width", return_value=300), \
                mock.patch.object(window.root, "winfo_height", return_value=400):
            window._update_hook_rect()
            first = window._hook_bounds()
            second = window._hook_bounds()

        self.assertEqual(first, (10, 20, 310, 420))
        self.assertEqual(second, first)
        self.assertEqual(rootx.call_count, 1)

    def test_filter_buttons_switch_the_visible_list(self):
        today = date.today()
        soon = datetime.combine(today, datetime.min.time()).replace(hour=15).astimezone()
        later = soon + timedelta(days=4)
        self.controller.todos = [
            {
                "id": "soon", "text": "今天的事", "done": False, "remind_at": None,
                "reminded": False, "due_at": soon.isoformat(timespec="seconds"),
            },
            {
                "id": "later", "text": "以后的事", "done": False, "remind_at": None,
                "reminded": False, "due_at": later.isoformat(timespec="seconds"),
            },
        ]
        window = self.make_window()

        self.assertEqual(len(window._filter_buttons), 6)
        window._set_filter("today")

        texts = [label.cget("text") for label in self.body_labels(window)]
        self.assertTrue(any("今天的事" in text for text in texts))
        self.assertFalse(any("以后的事" in text for text in texts))
        self.assertEqual(self.controller.filters, ["today"])
        self.assertEqual(window._filter_buttons["today"].cget("bg"), sticky_window.ACCENT_FILL)

    def test_search_is_applied_after_the_filter(self):
        today = date.today()
        soon = datetime.combine(today, datetime.min.time()).replace(hour=15).astimezone()
        self.controller.todos = [
            {
                "id": "soon", "text": "今天的事", "done": False, "remind_at": None,
                "reminded": False, "due_at": soon.isoformat(timespec="seconds"),
            },
            {
                "id": "later", "text": "以后的事", "done": False, "remind_at": None,
                "reminded": False, "due_at": (soon + timedelta(days=4)).isoformat(timespec="seconds"),
            },
        ]
        window = self.make_window()
        window._set_filter("today")

        window.search = "以后"
        window._render()
        texts = [label.cget("text") for label in self.body_labels(window)]
        self.assertFalse(any("以后的事" in text for text in texts))

        window.search = "今天"
        window._render()
        texts = [label.cget("text") for label in self.body_labels(window)]
        self.assertTrue(any("今天的事" in text for text in texts))

    def test_done_filter_shows_only_finished_todos(self):
        self.controller.todos = [
            {
                "id": "open", "text": "未完成", "done": False, "remind_at": None,
                "reminded": False, "due_at": None,
            },
            {
                "id": "done", "text": "已完成", "done": True, "remind_at": None,
                "reminded": False, "due_at": None,
                "completed_at": "2026-11-11T10:00:00+08:00",
            },
        ]
        window = self.make_window()

        window._set_filter("done")

        texts = [label.cget("text") for label in self.body_labels(window)]
        self.assertTrue(any("已完成" in text for text in texts))
        self.assertFalse(any("未完成" in text for text in texts))

    def test_navigation_has_six_filters_then_views(self):
        window = self.make_window()

        self.assertEqual(list(window._filter_buttons), [key for key, _label in sticky_window.NAV_FILTERS])
        self.assertEqual(list(window._nav_view_frames), ["week", "month"])

    def test_navigation_switches_filter_and_highlights_the_item(self):
        window = self.make_window()

        window._set_filter("overdue")

        self.assertEqual(window.view, "list")
        self.assertEqual(window.filter, "overdue")
        self.assertEqual(self.controller.filters, ["overdue"])
        self.assertEqual(window._filter_buttons["overdue"].cget("bg"), sticky_window.ACCENT_FILL)
        self.assertEqual(window._filter_buttons["all"].cget("bg"), sticky_window.BG)

        window._set_view("week")

        self.assertEqual(window.view, "week")
        self.assertEqual(
            window._nav_view_frames["week"]["name"].cget("bg"), sticky_window.ACCENT_FILL
        )

    def test_today_nav_counts_and_hint(self):
        now = datetime.now().astimezone()
        today_due = now.replace(hour=23, minute=59, second=59, microsecond=0)
        self.controller.todos = [
            {"id": "today", "text": "今天到期", "done": False,
             "remind_at": today_due.isoformat(), "reminded": False},
            {"id": "overdue", "text": "逾期了", "done": False,
             "remind_at": (now - timedelta(days=1)).isoformat(), "reminded": False},
            {"id": "done", "text": "今天做完", "done": True,
             "remind_at": today_due.isoformat(), "reminded": False,
             "completed_at": now.isoformat()},
        ]
        window = self.make_window()

        window._update_nav_counts(window.controller.list())
        window._update_nav()

        self.assertEqual(window.nav_counts["today"].cget("text"), "2")
        self.assertEqual(window.nav_counts["overdue"].cget("text"), "1")
        self.assertEqual(window.nav_counts["overdue"].cget("fg"), sticky_window.DANGER)
        self.assertEqual(window._nav_today_sub.cget("text"), "逾期 1 · 今日到期 2")

    def test_hover_and_focus_reveal_row_actions(self):
        window = self.make_window()
        window.root.update()
        row = window.rows.winfo_children()[0]
        actions = window._row_actions["a"]
        self.assertEqual(actions.winfo_manager(), "")

        row.event_generate("<Enter>")
        window.root.update()
        self.assertEqual(actions.winfo_manager(), "pack")

        row.event_generate("<FocusOut>")
        window.root.update()
        self.assertEqual(actions.winfo_manager(), "")

        row.event_generate("<FocusIn>")
        window.root.update()
        self.assertEqual(actions.winfo_manager(), "pack")

        row.event_generate("<FocusOut>")
        window.root.update()
        self.assertEqual(actions.winfo_manager(), "")

    def test_mouse_leave_hides_row_actions(self):
        window = self.make_window()
        window.root.update()
        row = window.rows.winfo_children()[0]
        actions = window._row_actions["a"]

        row.event_generate("<Enter>")
        window.root.update()
        self.assertEqual(actions.winfo_manager(), "pack")

        row.event_generate("<Leave>", rootx=0, rooty=0)
        window.root.update()
        self.assertEqual(actions.winfo_manager(), "")

    def test_row_double_click_is_bound_to_details(self):
        window = self.make_window()
        title = [label for label in self.labels(window) if label.cget("text") == "买牛奶"][0]

        self.assertTrue(title.bind("<Double-Button-1>"))

    def test_compact_mode_changes_row_layout_and_font(self):
        window = self.make_window()
        card = window._row_widgets["a"]
        comfortable_height = int(card.place_info()["height"])

        window._set_compact(True)

        card = window._row_widgets["a"]
        compact_height = int(card.place_info()["height"])
        title = [label for label in self.labels(window) if label.cget("text") == "买牛奶"][0]
        self.assertLess(compact_height, comfortable_height)
        self.assertEqual(int(str(title.cget("font")).split()[-1]), 9)
        self.assertEqual(self.controller.compacts, [True])
        self.assertTrue(window.config["compact"])

    def test_priority_pills_use_the_three_colours(self):
        self.controller.todos = [
            {"id": "high", "text": "高", "done": False, "remind_at": None,
             "reminded": False, "priority": 3},
            {"id": "medium", "text": "中", "done": False, "remind_at": None,
             "reminded": False, "priority": 2},
            {"id": "low", "text": "低", "done": False, "remind_at": None,
             "reminded": False, "priority": 1},
        ]
        window = self.make_window()

        pills = {}
        for label in self.labels(window):
            text = str(label.cget("text"))
            if text in ("高", "中", "低"):
                pills[text] = label

        self.assertEqual(pills["高"].cget("bg"), sticky_window.DANGER)
        self.assertEqual(pills["中"].cget("bg"), sticky_window.CLOCK)
        self.assertEqual(pills["低"].cget("bg"), sticky_window.MUTED)

    def test_reminder_time_is_orange_and_future_due_is_normal(self):
        now = datetime.now().astimezone()
        due = now + timedelta(days=2)
        self.controller.todos[0]["remind_at"] = (due - timedelta(hours=3)).isoformat()
        self.controller.todos[0]["due_at"] = due.isoformat()
        window = self.make_window()

        deadline = [label for label in self.labels(window) if str(label.cget("text")).startswith("截止")][0]
        reminder = [label for label in self.labels(window) if str(label.cget("text")).startswith("提醒")][0]

        self.assertEqual(deadline.cget("fg"), sticky_window.TEXT)
        self.assertEqual(reminder.cget("fg"), sticky_window.CLOCK)

    def test_saved_filter_is_restored_on_start(self):
        controller = _Controller(self.controller.todos)
        controller.get_config = lambda: {"filter": "done", "view": "list"}
        window = sticky_window._StickyWindow(
            self.root, queue.Queue(), controller, {"transparency": 100}
        )

        self.assertEqual(window.filter, "done")
        self.assertEqual(window._filter_buttons["done"].cget("bg"), sticky_window.ACCENT_FILL)

    def test_two_thousand_rows_render_only_a_windowful(self):
        now = datetime.now().astimezone()
        self.controller.todos = [
            {
                "id": "many-%d" % index, "text": "任务%d" % index, "done": False,
                "remind_at": (now + timedelta(days=index % 30)).isoformat(),
                "reminded": False, "priority": index % 4,
            }
            for index in range(2000)
        ]
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()

        cards = window.rows.winfo_children()
        self.assertLessEqual(len(cards), 60)
        self.assertLess(len(cards), 2000)

    def test_scroll_position_survives_refresh_and_selection(self):
        now = datetime.now().astimezone()
        self.controller.todos = [
            {
                "id": "s-%d" % index, "text": "任务%d" % index, "done": False,
                "remind_at": (now + timedelta(days=index % 30)).isoformat(),
                "reminded": False,
            }
            for index in range(300)
        ]
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()
        window.canvas.yview_moveto(1)
        window.root.update()
        before = window.canvas.yview()[0]
        self.assertGreater(before, 0.5)

        window._render()
        window.root.update()
        self.assertAlmostEqual(window.canvas.yview()[0], before, places=2)

        first = window._row_pool[0]
        window._select_todo(first.todo)
        window.root.update()
        self.assertAlmostEqual(window.canvas.yview()[0], before, places=2)

    def test_toggle_and_delete_keep_scroll_position(self):
        now = datetime.now().astimezone()
        self.controller.todos = [
            {
                "id": "t-%d" % index, "text": "任务%d" % index, "done": False,
                "remind_at": (now + timedelta(days=index % 30)).isoformat(),
                "reminded": False,
            }
            for index in range(200)
        ]
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()
        window.canvas.yview_moveto(0.6)
        window.root.update()
        before = window.canvas.yview()[0]

        window._toggle("t-100")
        window.root.update()
        self.assertAlmostEqual(window.canvas.yview()[0], before, places=2)

        window._delete("t-101")
        window.root.update()
        self.assertAlmostEqual(window.canvas.yview()[0], before, places=2)

    def test_hover_moves_actions_to_only_one_row(self):
        self.controller.todos = [
            {"id": "h%d" % index, "text": "任务%d" % index, "done": False,
             "remind_at": None, "reminded": False}
            for index in range(3)
        ]
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()
        first, second = window._row_pool[0], window._row_pool[1]

        first.card.event_generate("<Enter>")
        window.root.update()
        self.assertEqual(first.actions.winfo_manager(), "pack")

        second.card.event_generate("<Enter>")
        window.root.update()
        self.assertEqual(second.actions.winfo_manager(), "pack")
        self.assertEqual(first.actions.winfo_manager(), "")

    def test_subtask_entry_survives_scrolling(self):
        now = datetime.now().astimezone()
        self.controller.todos = [
            {"id": "p", "text": "父", "done": False, "remind_at": None, "reminded": False},
        ] + [
            {"id": "c%d" % index, "text": "子%d" % index, "done": False,
             "remind_at": (now + timedelta(days=index)).isoformat(), "reminded": False}
            for index in range(50)
        ]
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()

        window._start_subtask("p")
        window.root.update()
        entry = window._subtask_entry
        self.assertIsNotNone(entry)
        self.assertEqual(window.root.focus_get(), entry)

        window.canvas.yview_moveto(1)
        window.root.update()
        self.assertIs(window._subtask_entry, entry)
        window.canvas.yview_moveto(0)
        window.root.update()
        self.assertIs(window._subtask_entry, entry)

    def test_compact_and_font_changes_remeasure_rows(self):
        self.controller.todos = [
            {"id": "m%d" % index, "text": "任务%d" % index, "done": False,
             "remind_at": None, "reminded": False}
            for index in range(100)
        ]
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()
        comfortable = window._measure_row_height()

        window._set_compact(True)
        window.root.update()
        compact = window._measure_row_height()
        self.assertLess(compact, comfortable)

        window.set_font_size(14)
        window.root.update()
        larger = window._measure_row_height()
        self.assertGreater(larger, compact)

    def test_delete_shifts_following_rows_without_mixing_data(self):
        self.controller.todos = [
            {"id": "d%d" % index, "text": "任务%d" % index, "done": False,
             "remind_at": None, "reminded": False}
            for index in range(10)
        ]
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()
        before = [row.todo["id"] for row in window._row_pool if row.todo is not None]

        window._delete(before[0])
        window.root.update()
        after = [row.todo["id"] for row in window._row_pool if row.todo is not None]

        self.assertEqual(after[0], before[1])
        self.assertNotIn(before[0], after)

    def test_toggle_updates_only_the_affected_row(self):
        self.controller.todos = [
            {"id": "x%d" % index, "text": "任务%d" % index, "done": False,
             "remind_at": None, "reminded": False}
            for index in range(10)
        ]
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()
        before = [row.todo["id"] for row in window._row_pool if row.todo is not None]

        window._toggle("x3")
        window.root.update()

        after = [row.todo["id"] for row in window._row_pool if row.todo is not None]
        self.assertEqual(after, before)
        changed = [row for row in window._row_pool if row.todo and row.todo["id"] == "x3"][0]
        self.assertTrue(changed.variable.get())

    def test_lock_button_toggles_and_saves(self):
        window = self.make_window()
        with mock.patch("sticky_window._set_click_through"), \
                mock.patch.object(window, "_start_hook"), \
                mock.patch.object(window, "_stop_hook"):
            window._toggle_lock()

        self.assertTrue(window.locked)
        self.assertTrue(self.controller.locked)
        self.assertEqual(window.lock_button.cget("text"), "🔒")

        with mock.patch("sticky_window._set_click_through"), \
                mock.patch.object(window, "_start_hook"), \
                mock.patch.object(window, "_stop_hook"):
            window._toggle_lock()

        self.assertFalse(window.locked)
        self.assertEqual(window.lock_button.cget("text"), "🔓")

    def test_lock_keeps_window_clickable_when_hook_is_not_confirmed_active(self):
        window = self.make_window()
        with mock.patch.object(window, "_start_hook", return_value=False), \
                mock.patch("sticky_window._set_click_through") as click:
            window.locked = True
            window._apply_lock(announce=True)
        click.assert_called_with(window.root, False)

    def test_hook_receives_only_a_cached_rect(self):
        window = self.make_window()
        with mock.patch("mouse_hook.start", return_value=1) as start:
            window._start_hook()

        bounds = start.call_args.args[1]
        captured = []
        thread = threading.Thread(target=lambda: captured.append(bounds()))
        thread.start()
        thread.join()

        self.assertEqual(captured, [window._hook_rect])

    def test_lock_without_a_hook_stays_clickable(self):
        if os.name != "nt":
            self.skipTest("仅 Windows 需要穿透样式")
        window = self.make_window()
        window.locked = True
        with mock.patch("mouse_hook.start", return_value=None), \
                mock.patch("mouse_hook.is_active", return_value=False), \
                mock.patch("sticky_window._set_click_through") as click:
            window._apply_lock(announce=False)

        click.assert_called_with(window.root, False)

    def test_hook_starts_and_stops(self):
        window = self.make_window()
        with mock.patch("mouse_hook.start", return_value=42) as start, \
                mock.patch("mouse_hook.is_active", return_value=True):
            self.assertTrue(window._start_hook())
        self.assertEqual(window._hook, 42)
        self.assertTrue(start.called)

        with mock.patch("mouse_hook.stop") as stop:
            window._stop_hook()
        stop.assert_called_once()
        self.assertIsNone(window._hook)

    def test_row_shows_pin_flag_tags_note_and_child_progress(self):
        self.controller.todos = [
            {"id": "p", "text": "父任务", "done": False, "remind_at": None, "reminded": False,
             "priority": 3, "pinned": True, "tags": ["工作"], "note": "记一笔"},
            {"id": "c", "text": "子任务", "done": True, "remind_at": None, "reminded": False,
             "parent_id": "p"},
        ]
        window = self.make_window()

        texts = [str(label.cget("text")) for label in self.body_labels(window)]
        combined = " | ".join(texts)

        self.assertIn("📌 父任务", combined)
        self.assertIn("高", texts)
        self.assertIn("#工作", combined)
        self.assertIn("📝", combined)
        self.assertIn("子任务 1/1", combined)

    def test_child_rows_have_no_add_button(self):
        self.controller.todos = [
            {"id": "p", "text": "父", "done": False, "remind_at": None, "reminded": False},
            {"id": "c", "text": "子", "done": False, "remind_at": None, "reminded": False, "parent_id": "p"},
        ]
        window = self.make_window()

        cards = window.rows.winfo_children()
        parent_buttons = [button.cget("text") for button in self.buttons_in(cards[0])
                          if button.winfo_manager() == "pack"]
        child_buttons = [button.cget("text") for button in self.buttons_in(cards[1])
                         if button.winfo_manager() == "pack"]

        self.assertIn("＋", parent_buttons)
        self.assertNotIn("＋", child_buttons)

    def test_inline_subtask_creation_passes_the_parent(self):
        window = self.make_window()
        window._start_subtask("a")
        window._subtask_entry.insert(0, "子任务")
        window._create_subtask("a", window._subtask_entry)

        self.assertEqual(self.controller.added, ["子任务"])
        self.assertEqual(self.controller.added_parents, ["a"])

    def test_details_sidebar_is_reused_for_different_todos(self):
        self.controller.todos.append(
            {"id": "b", "text": "学习 Python", "done": False, "remind_at": None,
             "reminded": False, "priority": 2, "pinned": True, "tags": ["学习"], "note": "新备注"}
        )
        window = self.make_window()
        window._open_details(self.controller.todos[0])
        panel = window._details_panel
        window._open_details(self.controller.todos[1])

        self.assertIs(window._details_panel, panel)
        self.assertEqual(panel.todo["id"], "b")
        self.assertEqual(panel.priority.get(), "中")
        self.assertTrue(panel.pinned.get())
        self.assertEqual(panel.tags.get(), "学习")
        self.assertEqual(panel.note.get("1.0", "end").strip(), "新备注")
        self.assertTrue(window._detail_visible)

    def test_clicking_a_row_opens_the_sidebar_and_widens_the_window(self):
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()
        base = window.root.winfo_width()
        title = [label for label in self.labels(window) if label.cget("text") == "买牛奶"][0]

        title.event_generate("<Button-1>")
        window.root.update()

        self.assertTrue(window._detail_visible)
        self.assertEqual(window._details_panel.mode, "details")
        self.assertEqual(window._details_panel.title_var.get(), "买牛奶")
        self.assertGreater(window.root.winfo_width(), base)

        window._details_panel.close_button.invoke()
        window.root.update()
        self.assertFalse(window._detail_visible)
        self.assertEqual(window.root.winfo_width(), base)

    def test_sidebar_save_writes_title_due_and_remind(self):
        window = self.make_window()
        window._open_details(self.controller.todos[0])
        panel = window._details_panel
        panel.title_var.set("新标题")
        panel.priority.set("高")
        panel.pinned.set(True)
        panel.tags.set("工作, 学习")
        panel.note.insert("1.0", "备注内容")

        panel._save()

        self.assertEqual(self.controller.edited[-1], ("a", "新标题"))
        todo_id, priority, pinned, tags, note = self.controller.details[-1]
        self.assertEqual(todo_id, "a")
        self.assertEqual(priority, 3)
        self.assertTrue(pinned)
        self.assertEqual(tags, ["工作", "学习"])
        self.assertEqual(note, "备注内容")

    def test_sidebar_close_changes_nothing(self):
        window = self.make_window()
        window._open_details(self.controller.todos[0])

        window._details_panel.close_button.invoke()

        self.assertEqual(self.controller.details, [])
        self.assertFalse(window._detail_visible)

    def test_sidebar_due_and_remind_rows_update(self):
        window = self.make_window()
        window._open_details(self.controller.todos[0])
        panel = window._details_panel
        self.controller.todos[0]["due_at"] = "2030-01-02T08:30:00+08:00"
        self.controller.todos[0]["remind_at"] = "2030-01-02T07:30:00+08:00"

        panel._after_due()
        panel._after_remind()

        self.assertIn("08:30", panel.due_label.cget("text"))
        self.assertIn("07:30", panel.remind_label.cget("text"))

        panel._clear_due()
        panel._clear_remind()
        self.assertEqual(self.controller.dues[-1], ("a", None))
        self.assertEqual(self.controller.reminders[-1], ("a", None, None))

    def test_sidebar_width_is_capped_by_the_screen(self):
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()

        with mock.patch.object(window.root, "winfo_screenwidth", return_value=900):
            window._open_details(self.controller.todos[0])
            window.root.update()

        self.assertEqual(window.root.winfo_width(), 900)

    def test_sidebar_width_is_not_persisted(self):
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()
        base = window.root.winfo_width()
        window._open_details(self.controller.todos[0])
        window.root.update()

        with mock.patch.object(window.controller, "save_window_position") as save:
            window._save_geometry()

        save.assert_called_once()
        self.assertEqual(save.call_args.args[2], base)
        self.assertEqual(window.config["window_width"], base)

    def test_opening_details_creates_no_toplevel(self):
        window = self.make_window()
        window._open_details(self.controller.todos[0])

        self.assertEqual(
            [child for child in window.root.winfo_children() if child.winfo_class() == "Toplevel"],
            [],
        )

    def test_unlocked_window_is_not_topmost_when_shown(self):
        window = self.make_window()
        window.locked = False
        window._dispatch(("show", False))
        self.assertFalse(bool(self.root.attributes("-topmost")))

    @unittest.skipUnless(os.name == "nt", "需要 Windows 原生置顶行为")
    def test_locked_window_is_topmost_only_while_visible(self):
        window = self.make_window()
        window.locked = True
        with mock.patch.object(window, "_start_hook", return_value=False):
            window._dispatch(("show", False))
            self.assertTrue(bool(self.root.attributes("-topmost")))
            window._on_close()
        self.assertFalse(bool(self.root.attributes("-topmost")))

    def test_details_save_writes_priority_pin_tags_note(self):
        window = self.make_window()
        window._open_details(self.controller.todos[0])
        panel = window._details_panel
        panel.priority.set("高")
        panel.pinned.set(True)
        panel.tags.set("工作, 学习")
        panel.note.insert("1.0", "备注内容")

        panel._save()

        todo_id, priority, pinned, tags, note = self.controller.details[-1]
        self.assertEqual(todo_id, "a")
        self.assertEqual(priority, 3)
        self.assertTrue(pinned)
        self.assertEqual(tags, ["工作", "学习"])
        self.assertEqual(note, "备注内容")

    def test_closing_the_sidebar_keeps_unsaved_fields(self):
        window = self.make_window()
        window._open_details(self.controller.todos[0])
        window._details_panel.priority.set("高")
        window._details_panel.tags.set("工作")

        window._hide_detail_panel()

        self.assertEqual(self.controller.details, [])
        self.assertEqual(self.controller.dues, [])

    def test_due_picker_hides_repeat_and_saves_due(self):
        window = self.make_window()
        todo = dict(self.controller.todos[0])
        picker = sticky_window._ReminderPicker(window, todo, mode="due")
        picker.selected = date(2030, 1, 2)
        picker.year, picker.month = 2030, 1
        picker.hour.set("08")
        picker.minute.set("30")
        self.assertEqual(picker.top.title(), "截止日期")
        self.assertEqual(picker.repeat_row.winfo_manager(), "")

        picker._save()

        self.assertEqual(self.controller.reminders, [])
        self.assertIn("2030-01-02T08:30", self.controller.dues[0][1])

    def test_snooze_records_a_near_future_reminder(self):
        window = self.make_window()
        todo = dict(self.controller.todos[0])
        todo["remind_at"] = datetime.now().astimezone().isoformat()
        picker = sticky_window._ReminderPicker(window, todo)

        picker._snooze(10)

        vendor_id, iso = self.controller.snoozes[0]
        delta = datetime.fromisoformat(iso) - datetime.now().astimezone()
        self.assertEqual(vendor_id, "a")
        self.assertTrue(timedelta(minutes=9) < delta <= timedelta(minutes=11))

    def test_today_view_keeps_due_overdue_and_today_completions(self):
        now = datetime.now().astimezone()
        today_noon = now.replace(hour=23, minute=0, second=0, microsecond=0)
        self.controller.todos = [
            {"id": "due", "text": "今天到期", "done": False,
             "remind_at": today_noon.isoformat(), "reminded": False},
            {"id": "late", "text": "逾期了", "done": False,
             "remind_at": (now - timedelta(days=1)).isoformat(), "reminded": False},
            {"id": "future", "text": "明天", "done": False,
             "remind_at": (now + timedelta(days=1)).isoformat(), "reminded": False},
            {"id": "none", "text": "没截止", "done": False, "remind_at": None, "reminded": False},
            {"id": "done", "text": "今天做完", "done": True,
             "remind_at": today_noon.isoformat(), "reminded": False},
        ]
        window = self.make_window()

        window._set_view("today")

        texts = [str(label.cget("text")) for label in self.body_labels(window)]
        self.assertIn("今天到期", texts)
        self.assertNotIn("逾期了", texts)
        self.assertIn("今天做完", texts)
        self.assertNotIn("明天", texts)
        self.assertNotIn("没截止", texts)

    def test_search_filters_every_view_and_escape_clears(self):
        now = datetime.now().astimezone()
        self.controller.todos = [
            {"id": "milk", "text": "买牛奶", "done": False,
             "remind_at": now.isoformat(), "reminded": False},
            {"id": "report", "text": "写周报", "done": False,
             "remind_at": now.isoformat(), "reminded": False},
        ]
        window = self.make_window()

        for view in ("list", "today", "week", "month"):
            window._set_view(view)
            window.search = "牛奶"
            window._render()
            texts = [str(label.cget("text")) for label in self.body_labels(window)]
            self.assertIn("买牛奶", texts)
            self.assertNotIn("写周报", texts)

        window._clear_search()
        self.assertEqual(window.search, "")
        texts = [str(label.cget("text")) for label in self.body_labels(window)]
        self.assertIn("写周报", texts)

    def test_stats_dialog_shows_the_counts(self):
        self.controller.todos = [
            {"id": "a", "text": "完成", "done": True,
             "remind_at": None, "reminded": False,
             "completed_at": datetime.now().astimezone().isoformat()},
        ]
        window = self.make_window()

        dialog = sticky_window._StatsDialog(window)

        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, sticky_window.tk.Label):
                    found.append(str(child.cget("text")))
                walk(child)

        walk(dialog.top)
        self.assertIn("1 条", found)
        self.assertIn("1 天", found)


class WindowTests(unittest.TestCase):
    def setUp(self):
        sticky_window._state["visible"] = False
        self.root = _tk_root()
        self.addCleanup(self.root.destroy)
        self.todos = [
            {"id": "a", "text": "买牛奶", "done": False, "remind_at": None, "reminded": False},
            {"id": "b", "text": "已经完成", "done": True, "remind_at": None, "reminded": False},
        ]
        self.controller = _Controller(self.todos)

    def make_window(self, settings=None):
        return sticky_window._StickyWindow(
            self.root, queue.Queue(), self.controller, settings or {"transparency": 80}
        )

    def labels(self, window):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, sticky_window.tk.Label):
                    found.append(child)
                walk(child)

        for row in window.rows.winfo_children():
            walk(row)
        return found

    def buttons(self, widget):
        found = []

        def walk(current):
            for child in current.winfo_children():
                if isinstance(child, sticky_window.tk.Button):
                    found.append(child)
                walk(child)

        walk(widget)
        return found

    def dialog_labels(self, dialog):
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, sticky_window.tk.Label):
                    found.append(child)
                walk(child)

        walk(dialog.body)
        return found

    def test_row_shows_the_deadline(self):
        self.controller.todos[0]["remind_at"] = "2030-01-02T08:30:15+08:00"
        window = self.make_window()

        texts = [label.cget("text") for label in self.labels(window)]

        self.assertIn("截止 1-2 08:30", texts)

    def test_row_without_deadline_shows_no_deadline(self):
        window = self.make_window()

        texts = [str(label.cget("text")) for label in self.labels(window)]

        self.assertFalse(any(text.startswith("截止") for text in texts))

    def test_deadline_uses_a_readable_font(self):
        self.controller.todos[0]["remind_at"] = "2030-01-02T08:30:15+08:00"
        window = self.make_window()

        deadline = [
            label for label in self.labels(window) if str(label.cget("text")).startswith("截止")
        ][0]

        self.assertGreaterEqual(int(str(deadline.cget("font")).split()[-1]), 8)

    def test_one_row_per_todo(self):
        window = self.make_window()

        self.assertEqual(len(window.rows.winfo_children()), 2)
        texts = [label.cget("text") for label in self.labels(window)]
        self.assertIn("买牛奶", texts)
        self.assertIn("已经完成", texts)

    def test_done_todo_is_struck_through(self):
        window = self.make_window()

        done = [label for label in self.labels(window) if label.cget("text") == "已经完成"][0]

        self.assertIn("overstrike", str(done.cget("font")))

    def test_opacity_follows_the_setting(self):
        self.make_window({"transparency": 80})

        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 0.8, places=2)

    def test_reminder_row_shows_one_clock_button(self):
        self.controller.todos[0]["remind_at"] = "2030-01-01T08:00:00+08:00"
        window = self.make_window()

        row = window.rows.winfo_children()[0]
        clocks = [
            button for button in self.buttons(row)
            if button.cget("text") == "⏰"
        ]

        self.assertEqual(len(clocks), 1)

    def test_close_hides_the_note_and_clears_the_status(self):
        window = self.make_window()
        window._dispatch(("show", False))
        self.assertTrue(sticky_window.is_visible())

        window._on_close()

        self.assertFalse(sticky_window.is_visible())
        self.assertTrue(self.root.winfo_exists())
        self.assertEqual(window.status.cget("text"), "")
        self.assertEqual(str(window.root.state()), "withdrawn")

    def test_tray_menu_offers_show_after_close(self):
        import main

        window = self.make_window()
        window._dispatch(("show", False))
        window._on_close()

        items = main.on_tray_menu(None)

        self.assertEqual(items[0]["title"], "显示便签")
        self.assertFalse(items[0]["checked"])

    def test_taskbar_hide_refreshes_the_frame(self):
        window = self.make_window()
        fake = mock.MagicMock()
        fake.GetParent.return_value = 123
        fake.GetWindowLongPtrW.return_value = 4
        fake.SetWindowLongPtrW.return_value = 4
        fake.SetWindowPos.return_value = 1

        self.assertTrue(sticky_window._hide_from_taskbar(window.root, user32=fake))
        self.assertEqual(fake.SetWindowPos.call_args.args[-1], 0x0027)

    def test_enter_adds_the_typed_text(self):
        window = self.make_window()
        window.entry.insert(0, "新的一条")

        window._add_from_entry()

        self.assertEqual(self.controller.added, ["新的一条"])
        self.assertEqual(window.entry.get(), "")

    def test_picking_a_day_reuses_the_same_cells(self):
        window = self.make_window()
        picker = sticky_window._ReminderPicker(window, self.todos[0])
        cell = picker._cells[2][3]

        picker._pick(15)

        self.assertIs(picker._cells[2][3], cell)
        self.assertEqual(picker.selected.day, 15)
        row_index, column = next(
            (row, column)
            for row, week in enumerate(sticky_window._month_grid(picker.year, picker.month))
            for column, day in enumerate(week)
            if day == 15
        )
        self.assertEqual(picker._cells[row_index][column].cget("bg"), sticky_window.ACCENT)

    def test_ui_uses_microsoft_yahei(self):
        window = self.make_window()

        self.assertIn("Microsoft YaHei", str(window.title_label.cget("font")))

    def test_overdue_deadline_is_red(self):
        self.controller.todos[0]["remind_at"] = (
            datetime.now().astimezone() - timedelta(hours=1)
        ).isoformat()
        window = self.make_window()

        deadline = [label for label in self.labels(window) if str(label.cget("text")).startswith("截止")][0]

        self.assertEqual(str(deadline.cget("fg")), sticky_window.DANGER)

    def test_future_and_done_deadlines_keep_the_normal_colour(self):
        self.controller.todos[0]["remind_at"] = (
            datetime.now().astimezone() + timedelta(days=1)
        ).isoformat()
        self.controller.todos[1]["remind_at"] = (
            datetime.now().astimezone() - timedelta(hours=1)
        ).isoformat()
        window = self.make_window()

        colours = [
            str(label.cget("fg")) for label in self.labels(window)
            if str(label.cget("text")).startswith("截止")
        ]

        self.assertEqual(colours, [sticky_window.TEXT, sticky_window.MUTED])

    def test_double_click_edit_saves_the_new_text(self):
        window = self.make_window()
        label = [item for item in self.labels(window) if item.cget("text") == "买牛奶"][0]

        window._start_edit(self.controller.todos[0], label.master, label)
        window._edit_entry.delete(0, "end")
        window._edit_entry.insert(0, "新买的")
        window._save_edit()

        self.assertEqual(self.controller.edited[-1], ("a", "新买的"))

    def test_escape_cancels_the_edit(self):
        window = self.make_window()
        label = [item for item in self.labels(window) if item.cget("text") == "买牛奶"][0]
        window._start_edit(self.controller.todos[0], label.master, label)
        window._edit_entry.insert("end", "不要了")

        window._cancel_edit()

        self.assertEqual(self.controller.edited, [])
        texts = [item.cget("text") for item in self.labels(window)]
        self.assertIn("买牛奶", texts)

    def test_reminder_refresh_while_editing_keeps_the_typing(self):
        window = self.make_window()
        label = [item for item in self.labels(window) if item.cget("text") == "买牛奶"][0]
        window._start_edit(self.controller.todos[0], label.master, label)
        window._edit_entry.delete(0, "end")
        window._edit_entry.insert(0, "半截")

        window._dispatch(("refresh",))

        self.assertTrue(window._refresh_pending)
        self.assertEqual(window._edit_entry.get(), "半截")
        window._save_edit()
        self.assertEqual(self.controller.edited[-1], ("a", "半截"))

    def test_clear_button_appears_only_with_finished_todos(self):
        self.controller.todos = [
            {"id": "a", "text": "买牛奶", "done": False, "remind_at": None, "reminded": False},
        ]
        window = self.make_window()
        self.assertEqual(window.clear_button.winfo_manager(), "")

        self.controller.todos[0]["done"] = True
        window._render()

        self.assertEqual(window.clear_button.winfo_manager(), "pack")

    def test_clearing_done_todos_shows_undo(self):
        window = self.make_window()

        window._clear_done()

        self.assertEqual([todo["id"] for todo in self.controller.todos], ["a"])
        self.assertEqual(window.status.cget("text"), "已删除 1 条")
        self.assertEqual(window.undo_button.winfo_manager(), "pack")

    def test_undo_entry_disappears_when_the_timer_fires(self):
        window = self.make_window()
        window._show_undo(1)

        window._hide_undo()

        self.assertEqual(window.status.cget("text"), "")
        self.assertEqual(window.undo_button.winfo_manager(), "")

    def test_undo_restores_the_note(self):
        window = self.make_window()

        window._delete("a")
        window._undo()

        self.assertEqual(self.controller.undone, 1)
        self.assertEqual(window.status.cget("text"), "")

    def month_window(self, count):
        self.controller.todos = [
            {
                "id": "m%d" % index, "text": "事%d" % index, "done": False,
                "remind_at": datetime(2026, 1, 5, 9, 0).astimezone().isoformat(),
                "repeat": None, "reminded": False,
            }
            for index in range(count)
        ]
        window = self.make_window()
        window._month_year, window._month_month = 2026, 1
        window._set_view("month")
        return window

    def test_list_has_a_visible_scrollbar(self):
        self.controller.todos = [
            {"id": "s%d" % index, "text": "事%d" % index, "done": False, "remind_at": None, "reminded": False}
            for index in range(12)
        ]
        window = self.make_window()

        bars = [child for child in window.body.winfo_children() if child.winfo_class() == "TScrollbar"]

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].winfo_manager(), "pack")

    def test_rows_forward_the_mouse_wheel_to_the_list(self):
        self.controller.todos = [
            {"id": "s%d" % index, "text": "事%d" % index, "done": False, "remind_at": None, "reminded": False}
            for index in range(12)
        ]
        window = self.make_window()

        card = window.rows.winfo_children()[0]
        label = [item for item in self.labels(window) if item.cget("text") == "事0"][0]

        self.assertTrue(card.bind("<MouseWheel>"))
        self.assertTrue(label.bind("<MouseWheel>"))

    def test_scroll_only_when_content_overflows(self):
        self.assertTrue(sticky_window._needs_scroll(200, 100))
        self.assertFalse(sticky_window._needs_scroll(80, 100))

    def test_wheel_does_nothing_when_the_list_fits(self):
        window = self.make_window()
        fake = mock.Mock()
        fake.winfo_exists.return_value = True
        fake.winfo_height.return_value = 100
        window._list_total = 40

        with mock.patch.object(window, "canvas", fake):
            window._on_wheel(mock.Mock(delta=-120))

        fake.yview_scroll.assert_not_called()

    def test_wheel_scrolls_when_content_overflows(self):
        window = self.make_window()
        fake = mock.Mock()
        fake.winfo_exists.return_value = True
        fake.winfo_height.return_value = 100
        window._list_total = 400

        with mock.patch.object(window, "canvas", fake):
            window._on_wheel(mock.Mock(delta=-120))

        fake.yview_scroll.assert_called_once()

    def test_month_frames_only_the_days_of_that_month(self):
        window = self.month_window(1)

        self.assertEqual(len(window._month_day_cells), 31)

    def test_short_month_does_not_render_a_sixth_row(self):
        window = self.month_window(1)
        window._month_year, window._month_month = 2026, 2
        window._render()

        grid = window._month_day_cells[0].master
        self.assertEqual(len(window._month_day_cells), 28)
        self.assertEqual(grid.grid_slaves(row=6), [])

    def test_more_hint_opens_the_day_in_the_sidebar(self):
        window = self.month_window(3)
        more = [button for button in self.buttons(window.body) if str(button.cget("text")).startswith("+")][0]
        toplevels_before = [child for child in self.root.winfo_children() if child.winfo_class() == "Toplevel"]

        more.invoke()

        panel = window._details_panel
        self.assertTrue(window._detail_visible)
        self.assertEqual(panel.mode, "day")
        texts = [str(button.cget("text")) for button in self.buttons(panel.body)]
        self.assertEqual([text for text in texts if text.endswith(("事0", "事1", "事2"))],
                         ["○  事0", "○  事1", "○  事2"])
        self.assertEqual(
            [child for child in self.root.winfo_children() if child.winfo_class() == "Toplevel"],
            toplevels_before,
        )

    def test_clicking_a_day_entry_switches_to_its_details(self):
        window = self.month_window(3)
        more = [button for button in self.buttons(window.body) if str(button.cget("text")).startswith("+")][0]
        more.invoke()
        panel = window._details_panel
        task = [button for button in self.buttons(panel.body) if str(button.cget("text")).endswith("事0")][0]

        task.invoke()

        self.assertEqual(panel.mode, "details")
        self.assertEqual(panel.todo["id"], "m0")
        self.assertTrue(window._detail_visible)
        self.assertFalse(self.controller.todos[0]["done"])
        self.assertEqual(
            [child for child in self.root.winfo_children() if child.winfo_class() == "Toplevel"],
            [],
        )

    def test_day_list_closes_with_the_sidebar_close_button(self):
        window = self.month_window(3)
        window._open_day(date(2026, 1, 5), [])
        panel = window._details_panel
        self.assertEqual(panel.mode, "day")

        panel.close_button.invoke()

        self.assertFalse(window._detail_visible)

    def test_opening_another_day_reuses_the_sidebar(self):
        window = self.month_window(3)
        window._open_day(date(2026, 1, 5), [])
        panel = window._details_panel

        window._open_day(date(2026, 1, 6), [])

        self.assertIs(window._details_panel, panel)
        self.assertEqual(panel.mode, "day")
        self.assertIn("1月6日", panel.panel_title.cget("text"))

    def test_two_entries_have_no_more_hint(self):
        window = self.month_window(2)

        self.assertEqual(
            [button for button in self.buttons(window.body) if str(button.cget("text")).startswith("+")],
            [],
        )

    def test_clicking_a_month_day_opens_the_day_list(self):
        window = self.month_window(3)
        window._dispatch(("show", True))
        window.root.update()
        cell = window._month_day_cells[4]

        cell.event_generate("<Button-1>")
        window.root.update()

        self.assertTrue(window._detail_visible)
        self.assertEqual(window._details_panel.mode, "day")
        self.assertEqual(
            [child for child in self.root.winfo_children() if child.winfo_class() == "Toplevel"],
            [],
        )

    def test_long_day_list_uses_the_sidebar_scrollbar(self):
        window = self.month_window(10)
        window._open_day(date(2026, 1, 5), [])

        panel = window._details_panel
        self.assertEqual(panel.mode, "day")
        self.assertIsNotNone(panel.scrollbar)
        self.assertEqual(panel.scrollbar.winfo_manager(), "pack")

    def test_day_list_rows_forward_mouse_wheel_to_the_canvas(self):
        window = self.month_window(12)
        more = [button for button in self.buttons(window.body) if str(button.cget("text")).startswith("+")][0]
        more.invoke()
        panel = window._details_panel
        task_buttons = self.buttons(panel.body)
        self.assertTrue(task_buttons)
        self.assertTrue(task_buttons[0].bind("<MouseWheel>"))
        with mock.patch.object(panel.canvas, "yview_scroll") as scroll:
            panel._on_wheel(mock.Mock(delta=-120))
        scroll.assert_called_once_with(40, "units")

    def test_gear_button_sits_in_the_header(self):
        window = self.make_window()

        self.assertEqual(window.gear.cget("text"), "⚙")

    def test_gear_opens_the_font_dialog_with_the_size_choices(self):
        window = self.make_window()

        window._open_font_settings()
        dialog = window._font_dialog

        self.assertEqual(list(dialog.combo["values"]), [str(size) for size in range(9, 15)])
        self.assertEqual(dialog.combo.get(), "10")

    def test_settings_dialog_saves_compact_mode(self):
        window = self.make_window()
        window._open_font_settings()
        dialog = window._font_dialog
        self.assertFalse(dialog.compact_var.get())

        dialog.compact_var.set(True)
        dialog._save()

        self.assertTrue(window.compact)
        self.assertEqual(self.controller.compacts, [True])

    def test_settings_dialog_saves_transparency(self):
        window = self.make_window()
        window._open_font_settings()
        dialog = window._font_dialog
        self.assertEqual(dialog.transparency.get(), window.transparency)

        dialog.transparency.set(70)
        dialog._save()

        self.assertEqual(window.transparency, 70)
        self.assertEqual(self.controller.transparencies, [70])
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 0.7, places=2)

    def test_saved_transparency_is_restored(self):
        controller = _Controller(self.controller.todos)
        controller.get_config = lambda: {"transparency": 60}
        window = sticky_window._StickyWindow(
            self.root, queue.Queue(), controller, {"transparency": 95}
        )

        self.assertEqual(window.transparency, 60)
        self.assertAlmostEqual(float(self.root.attributes("-alpha")), 0.6, places=2)

    def test_font_settings_is_single_instance(self):
        window = self.make_window()
        window._open_font_settings()
        first = window._font_dialog
        window._open_font_settings()

        self.assertIs(window._font_dialog, first)
        self.assertEqual(
            len([child for child in self.root.winfo_children() if child.winfo_class() == "Toplevel"]),
            1,
        )

        first.close()
        self.assertIsNone(window._font_dialog)
        window._open_font_settings()
        self.assertIsNot(window._font_dialog, first)

    def test_reminder_picker_is_single_instance(self):
        window = self.make_window()
        first = window._open_reminder_picker(self.controller.todos[0])
        second = window._open_reminder_picker(self.controller.todos[0])

        self.assertIs(first, second)
        self.assertEqual(
            len([child for child in self.root.winfo_children() if child.winfo_class() == "Toplevel"]),
            1,
        )

        first.close()
        self.assertIsNone(window._reminder_picker)
        third = window._open_reminder_picker(self.controller.todos[0])
        self.assertIsNot(third, first)

    def test_stats_dialog_is_single_instance(self):
        window = self.make_window()
        window._open_stats()
        first = window._stats_dialog
        window._open_stats()

        self.assertIs(window._stats_dialog, first)
        self.assertEqual(
            len([child for child in self.root.winfo_children() if child.winfo_class() == "Toplevel"]),
            1,
        )

        first.close()
        self.assertIsNone(window._stats_dialog)
        window._open_stats()
        self.assertIsNot(window._stats_dialog, first)

    def test_cancel_does_not_change_the_font(self):
        window = self.make_window()
        window._open_font_settings()

        window._font_dialog.top.destroy()

        self.assertEqual(self.controller.font_sizes, [])
        self.assertEqual(window.font_size, 10)

    def test_saving_a_font_size_applies_to_the_whole_note(self):
        window = self.make_window()
        window._open_font_settings()
        dialog = window._font_dialog
        dialog.combo.set("14")

        dialog._save()

        self.assertEqual(self.controller.font_sizes, [14])
        self.assertEqual(window.font_size, 14)
        body = [label for label in self.labels(window) if label.cget("text") == "买牛奶"][0]
        self.assertEqual(int(str(body.cget("font")).split()[-1]), 14)

    def test_reminder_picker_saves_hour_and_minute_without_seconds(self):
        window = self.make_window()
        picker = sticky_window._ReminderPicker(window, self.todos[0])
        picker.selected = date(2030, 1, 2)
        picker.year, picker.month = 2030, 1
        picker.hour.set("08")
        picker.minute.set("30")

        picker._save()

        vendor_id, iso, repeat = self.controller.reminders[0]
        self.assertEqual(vendor_id, "a")
        self.assertIn("2030-01-02T08:30:00", iso)
        self.assertFalse(hasattr(picker, "second"))

    def test_new_reminder_defaults_to_tomorrow_0900(self):
        todo = {"id": "c", "text": "新的一条", "done": False, "remind_at": None, "reminded": False}
        window = self.make_window()

        picker = sticky_window._ReminderPicker(window, todo)

        self.assertEqual(picker.selected, date.today() + timedelta(days=1))
        self.assertEqual(picker.hour.get(), "09")
        self.assertEqual(picker.minute.get(), "00")

    def test_weekday_header_matches_day_cell_width(self):
        window = self.make_window()
        picker = sticky_window._ReminderPicker(window, self.todos[0])

        self.assertEqual(len(picker._week_labels), 7)
        for label in picker._week_labels:
            self.assertEqual(int(label.cget("width")), 3)
        self.assertEqual(int(picker._cells[0][0].cget("width")), 3)

    def test_weekly_defaults_to_the_selected_weekday(self):
        todo = {"id": "c", "text": "新的", "done": False, "remind_at": None, "reminded": False}
        window = self.make_window()
        picker = sticky_window._ReminderPicker(window, todo)
        picker.repeat_combo.set("每周")
        picker._refresh_repeat_rows()

        expected = picker.selected.weekday()
        self.assertTrue(picker.week_vars[expected].get())
        self.assertEqual(sum(1 for variable in picker.week_vars if variable.get()), 1)

    def test_weekly_without_days_cannot_be_saved(self):
        todo = {"id": "c", "text": "新的", "done": False, "remind_at": None, "reminded": False}
        window = self.make_window()
        picker = sticky_window._ReminderPicker(window, todo)
        picker.repeat_combo.set("每周")
        picker._refresh_repeat_rows()
        for variable in picker.week_vars:
            variable.set(False)

        picker._save()

        self.assertEqual(self.controller.reminders, [])
        self.assertIn("至少", picker.repeat_error.cget("text"))

    def test_monthly_defaults_to_the_selected_day(self):
        window = self.make_window()
        picker = sticky_window._ReminderPicker(window, self.todos[0])
        picker.repeat_combo.set("每月")
        picker._refresh_repeat_rows()

        self.assertEqual(int(picker.month_day.get()), picker.selected.day)

    def test_existing_repeat_is_shown_in_the_dialog(self):
        todo = dict(self.todos[0])
        todo["repeat"] = {"type": "weekly", "days": [0, 2]}
        window = self.make_window()

        picker = sticky_window._ReminderPicker(window, todo)

        self.assertEqual(picker.repeat_combo.get(), "每周")
        self.assertTrue(picker.week_vars[0].get())
        self.assertTrue(picker.week_vars[2].get())
        self.assertFalse(picker.week_vars[1].get())

    def test_saving_a_daily_repeat_passes_it_along(self):
        window = self.make_window()
        picker = sticky_window._ReminderPicker(window, self.todos[0])
        picker.repeat_combo.set("每天")
        picker._refresh_repeat_rows()

        picker._save()

        self.assertEqual(self.controller.reminders[0][2], {"type": "daily"})

    def test_row_shows_the_repeat_marker(self):
        self.controller.todos[0]["remind_at"] = "2030-01-02T08:30:00+08:00"
        self.controller.todos[0]["repeat"] = {"type": "daily"}
        window = self.make_window()

        texts = [label.cget("text") for label in self.labels(window)]

        self.assertIn("截止 1-2 08:30 · 每天", texts)

    def test_note_is_hidden_from_the_taskbar(self):
        if os.name != "nt":
            self.skipTest("任务栏样式只在 Windows 上")
        window = self.make_window()

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        hwnd = user32.GetParent(window.root.winfo_id()) or window.root.winfo_id()
        style = get_style(hwnd, -20)

        self.assertTrue(style & 0x00000080)  # WS_EX_TOOLWINDOW

    def test_window_uses_the_plugin_icon_bitmap(self):
        here = os.path.dirname(os.path.abspath(__file__))
        self.controller.icon_path = lambda: os.path.join(here, "icon.png")
        self.controller.icon_ico_path = lambda: os.path.join(here, "icon.ico")

        window = self.make_window()

        self.assertIsNotNone(window._icon)
        self.assertTrue(window._icon_bitmap.endswith("icon.ico"))

    def test_geometry_is_remembered(self):
        window = self.make_window()
        window._dispatch(("show", True))
        window.root.update()

        window._save_geometry()

        self.assertIsNotNone(self.controller.saved_position)


if __name__ == "__main__":
    unittest.main()
