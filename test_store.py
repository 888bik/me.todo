import json
import shutil
import unittest
from datetime import date, datetime, timedelta

import store
from test_support import make_data_dir


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.dir = make_data_dir()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_defaults_when_missing(self):
        config = store.load_config(self.dir)

        self.assertIsNone(config["window_x"])
        self.assertIsNone(config["window_y"])
        self.assertIsNone(config["window_width"])
        self.assertIsNone(config["window_height"])
        self.assertEqual(config["filter"], "all")
        self.assertFalse(config["compact"])

    def test_filter_and_compact_roundtrip(self):
        config = store.load_config(self.dir)
        config["filter"] = "overdue"
        config["compact"] = True
        config["transparency"] = 70
        store.save_config(self.dir, config)

        loaded = store.load_config(self.dir)
        self.assertEqual(loaded["filter"], "overdue")
        self.assertTrue(loaded["compact"])
        self.assertEqual(loaded["transparency"], 70)
        self.assertEqual(store.normalize_filter("bogus"), "all")

    def test_window_size_roundtrip(self):
        store.save_config(self.dir, {
            "window_x": 120, "window_y": 340,
            "window_width": 920, "window_height": 720,
        })

        config = store.load_config(self.dir)

        self.assertEqual((config["window_width"], config["window_height"]), (920, 720))

    def test_invalid_window_size_is_ignored(self):
        (self.dir / "config.json").write_text(
            '{"window_width": 10000, "window_height": "huge"}', encoding="utf-8"
        )

        config = store.load_config(self.dir)

        self.assertIsNone(config["window_width"])
        self.assertIsNone(config["window_height"])

    def test_position_roundtrip(self):
        store.save_config(self.dir, {"window_x": 120, "window_y": 340})

        config = store.load_config(self.dir)

        self.assertEqual((config["window_x"], config["window_y"]), (120, 340))

    def test_bad_position_is_ignored(self):
        (self.dir / "config.json").write_text('{"window_x": "left", "window_y": 5.5}', encoding="utf-8")

        config = store.load_config(self.dir)

        self.assertIsNone(config["window_x"])
        self.assertIsNone(config["window_y"])

    def test_font_size_defaults_to_10(self):
        self.assertEqual(store.load_config(self.dir)["font_size"], 10)

    def test_font_size_roundtrip(self):
        store.save_config(self.dir, {"font_size": 13})

        self.assertEqual(store.load_config(self.dir)["font_size"], 13)

    def test_invalid_font_size_falls_back_to_10(self):
        (self.dir / "config.json").write_text('{"font_size": 99}', encoding="utf-8")

        self.assertEqual(store.load_config(self.dir)["font_size"], 10)

    def test_saved_font_size_is_clamped(self):
        store.save_config(self.dir, {"font_size": 3})

        self.assertEqual(store.load_config(self.dir)["font_size"], 10)

    def test_view_defaults_to_list(self):
        self.assertEqual(store.load_config(self.dir)["view"], "list")

    def test_view_roundtrip(self):
        store.save_config(self.dir, {"view": "week"})

        self.assertEqual(store.load_config(self.dir)["view"], "week")

    def test_invalid_view_falls_back_to_list(self):
        (self.dir / "config.json").write_text('{"view": "year"}', encoding="utf-8")

        self.assertEqual(store.load_config(self.dir)["view"], "list")


class TodoStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = make_data_dir()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_missing_file_is_an_empty_list(self):
        self.assertEqual(store.load_todos(self.dir), [])

    def test_roundtrip_keeps_fields(self):
        todo = store.new_todo("买牛奶")

        store.save_todos(self.dir, [todo])
        loaded = store.load_todos(self.dir)

        self.assertEqual(loaded[0]["text"], "买牛奶")
        self.assertFalse(loaded[0]["done"])
        self.assertIsNone(loaded[0]["remind_at"])
        self.assertFalse(loaded[0]["reminded"])

    def test_corrupt_file_is_backed_up_and_starts_empty(self):
        (self.dir / "todos.json").write_text("{not json", encoding="utf-8")

        self.assertEqual(store.load_todos(self.dir), [])
        self.assertTrue((self.dir / "todos.json.bak").exists())
        self.assertFalse((self.dir / "todos.json").exists())

    def test_entries_without_text_are_dropped(self):
        payload = [{"id": "a", "text": "ok"}, {"id": "b", "text": ""}, "junk"]
        (self.dir / "todos.json").write_text(json.dumps(payload), encoding="utf-8")

        loaded = store.load_todos(self.dir)

        self.assertEqual([todo["id"] for todo in loaded], ["a"])

    def test_only_the_known_keys_are_saved(self):
        store.save_todos(self.dir, [{"id": "a", "text": "x", "extra": "drop me"}])

        raw = json.loads((self.dir / "todos.json").read_text(encoding="utf-8"))

        self.assertNotIn("extra", raw[0])


class ReminderTests(unittest.TestCase):
    def test_parse_and_format(self):
        iso = store.parse_reminder("2026-11-11 09:30")

        self.assertEqual(store.format_reminder(iso), "2026-11-11 09:30")

    def test_format_keeps_seconds_when_present(self):
        self.assertEqual(store.format_reminder("2026-11-11T09:30:15+08:00"), "2026-11-11 09:30:15")

    def test_parse_rejects_garbage(self):
        with self.assertRaises(ValueError):
            store.parse_reminder("tomorrow")

    def test_due_skips_reminded_and_future(self):
        now = datetime.now().astimezone()
        past = (now - timedelta(minutes=1)).isoformat(timespec="seconds")
        future = (now + timedelta(hours=1)).isoformat(timespec="seconds")
        todos = [
            {"id": "a", "remind_at": past, "reminded": False},
            {"id": "b", "remind_at": past, "reminded": True},
            {"id": "c", "remind_at": future, "reminded": False},
            {"id": "d", "remind_at": None, "reminded": False},
        ]

        due = store.due_todos(todos, now)

        self.assertEqual([todo["id"] for todo in due], ["a"])


class RepeatScheduleTests(unittest.TestCase):
    def dt(self, year, month, day, hour=9, minute=0):
        return datetime(year, month, day, hour, minute).astimezone()

    def test_daily_moves_one_day(self):
        after = self.dt(2026, 1, 2)

        self.assertEqual(store.next_occurrence({"type": "daily"}, after), self.dt(2026, 1, 3))

    def test_weekdays_skip_the_weekend(self):
        friday = self.dt(2026, 1, 2)

        self.assertEqual(store.next_occurrence({"type": "weekdays"}, friday), self.dt(2026, 1, 5))

    def test_weekly_takes_the_next_selected_day(self):
        monday = self.dt(2026, 1, 5)

        self.assertEqual(
            store.next_occurrence({"type": "weekly", "days": [0, 2, 4]}, monday),
            self.dt(2026, 1, 7),
        )

    def test_monthly_clamps_but_keeps_the_anchor(self):
        january = self.dt(2026, 1, 31)
        february = store.next_occurrence({"type": "monthly", "day": 31}, january)

        self.assertEqual(february.date(), date(2026, 2, 28))
        self.assertEqual(
            store.next_occurrence({"type": "monthly", "day": 31}, february).date(),
            date(2026, 3, 31),
        )

    def test_invalid_repeat_has_no_next_time(self):
        self.assertIsNone(store.next_occurrence({"type": "weekly", "days": []}, self.dt(2026, 1, 2)))


class OccurrenceTests(unittest.TestCase):
    def todo(self, repeat):
        return {
            "remind_at": datetime(2026, 1, 5, 9, 0).astimezone().isoformat(),
            "repeat": repeat,
        }

    def test_daily_repeat_fills_the_week(self):
        days = store.occurrences(self.todo({"type": "daily"}), date(2026, 1, 5), date(2026, 1, 11))

        self.assertEqual(len(days), 7)
        self.assertEqual(days[0], date(2026, 1, 5))

    def test_weekly_repeat_only_on_selected_days(self):
        days = store.occurrences(
            self.todo({"type": "weekly", "days": [0, 2]}), date(2026, 1, 5), date(2026, 1, 11)
        )

        self.assertEqual([day.weekday() for day in days], [0, 2])

    def test_one_shot_appears_once_in_range(self):
        todo = self.todo(None)

        self.assertEqual(store.occurrences(todo, date(2026, 1, 5), date(2026, 1, 11)), [date(2026, 1, 5)])
        self.assertEqual(store.occurrences(todo, date(2026, 1, 6), date(2026, 1, 11)), [])

    def test_todo_without_reminder_is_not_on_the_calendar(self):
        self.assertEqual(store.occurrences({"remind_at": None}, date(2026, 1, 1), date(2026, 1, 31)), [])

    def completed(self, repeat, completed_at):
        todo = self.todo(repeat)
        todo.update({"done": True, "completed_at": completed_at})
        return todo

    def test_finished_daily_repeat_stops_after_the_completion_date(self):
        todo = self.completed(
            {"type": "daily"}, datetime(2026, 1, 5, 10, 0).astimezone().isoformat()
        )

        self.assertEqual(store.occurrences(todo, date(2026, 1, 6), date(2026, 1, 11)), [])
        self.assertEqual(
            store.occurrences(todo, date(2026, 1, 5), date(2026, 1, 5)),
            [date(2026, 1, 5)],
        )

    def test_finished_weekly_and_monthly_repeats_also_stop(self):
        weekly = self.completed(
            {"type": "weekly", "days": [0, 2]},
            datetime(2026, 1, 5, 10, 0).astimezone().isoformat(),
        )
        monthly = self.completed(
            {"type": "monthly", "day": 31},
            datetime(2026, 1, 31, 10, 0).astimezone().isoformat(),
        )

        self.assertEqual(store.occurrences(weekly, date(2026, 1, 6), date(2026, 1, 31)), [])
        self.assertEqual(store.occurrences(monthly, date(2026, 2, 1), date(2026, 2, 28)), [])
        self.assertEqual(
            store.occurrences(monthly, date(2026, 1, 31), date(2026, 1, 31)),
            [date(2026, 1, 31)],
        )

    def test_finished_repeat_without_completed_at_falls_back_to_one_shot(self):
        todo = self.todo({"type": "daily"})
        todo["done"] = True

        self.assertEqual(
            store.occurrences(todo, date(2026, 1, 5), date(2026, 1, 11)),
            [date(2026, 1, 5)],
        )
        self.assertEqual(store.occurrences(todo, date(2026, 1, 6), date(2026, 1, 11)), [])

    def test_finished_one_shot_still_appears_on_its_due_date(self):
        todo = self.todo(None)
        todo.update({
            "done": True,
            "completed_at": datetime(2026, 1, 5, 10, 0).astimezone().isoformat(),
        })

        self.assertEqual(
            store.occurrences(todo, date(2026, 1, 5), date(2026, 1, 5)),
            [date(2026, 1, 5)],
        )


class OccurrenceParityTests(unittest.TestCase):
    """The direct occurrence math must match the old step-by-step walk."""

    def todo(self, repeat, anchor):
        return {
            "remind_at": anchor.isoformat(),
            "repeat": repeat,
            "repeat_base": anchor.isoformat(),
        }

    def walk(self, todo, start_date, end_date):
        repeat = todo["repeat"]
        when = store.parse_dt(todo["repeat_base"])
        current = when
        for _ in range(10000):
            if current.date() >= start_date:
                break
            nxt = store.next_occurrence(repeat, current)
            if nxt is None or nxt <= current:
                return []
            current = nxt
        dates = []
        for _ in range(10000):
            if current.date() > end_date:
                break
            if current.date() >= start_date:
                dates.append(current.date())
            nxt = store.next_occurrence(repeat, current)
            if nxt is None or nxt <= current:
                break
            current = nxt
        return dates

    def assert_parity(self, repeat, anchor, start, end):
        todo = self.todo(repeat, anchor)
        self.assertEqual(
            store.occurrences(todo, start, end),
            self.walk(todo, start, end),
            (repeat, anchor, start, end),
        )

    def test_daily_old_anchor(self):
        anchor = datetime(2024, 11, 11, 9, 0).astimezone()
        self.assert_parity({"type": "daily"}, anchor, date(2026, 11, 1), date(2026, 12, 31))

    def test_daily_three_hundred_days_back(self):
        anchor = datetime(2025, 1, 1, 9, 0).astimezone()
        self.assert_parity({"type": "daily"}, anchor, date(2026, 11, 1), date(2026, 11, 30))

    def test_weekly_multiple_days(self):
        anchor = datetime(2024, 5, 1, 9, 0).astimezone()
        self.assert_parity(
            {"type": "weekly", "days": [0, 2, 4]},
            anchor, date(2026, 11, 1), date(2026, 12, 31),
        )

    def test_weekdays(self):
        anchor = datetime(2025, 3, 7, 9, 0).astimezone()
        self.assert_parity({"type": "weekdays"}, anchor, date(2026, 11, 1), date(2026, 12, 31))

    def test_monthly_day_31(self):
        anchor = datetime(2024, 1, 31, 9, 0).astimezone()
        self.assert_parity(
            {"type": "monthly", "day": 31}, anchor, date(2026, 1, 1), date(2026, 6, 30),
        )

    def test_leap_day_anchor(self):
        anchor = datetime(2024, 2, 29, 9, 0).astimezone()
        self.assert_parity(
            {"type": "monthly", "day": 29}, anchor, date(2026, 1, 1), date(2026, 12, 31),
        )

    def test_finished_repeat_still_stops_at_completion(self):
        anchor = datetime(2024, 11, 11, 9, 0).astimezone()
        todo = self.todo({"type": "daily"}, anchor)
        todo.update({
            "done": True,
            "completed_at": datetime(2026, 11, 10, 10, 0).astimezone().isoformat(),
        })

        self.assertEqual(store.occurrences(todo, date(2026, 11, 9), date(2026, 11, 20)),
                         [date(2026, 11, 9), date(2026, 11, 10)])


class RepeatStorageTests(unittest.TestCase):
    def setUp(self):
        self.dir = make_data_dir()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_roundtrip_keeps_repeat(self):
        todo = store.new_todo("每周复盘", repeat={"type": "weekly", "days": [0, 2]})

        store.save_todos(self.dir, [todo])

        self.assertEqual(store.load_todos(self.dir)[0]["repeat"], {"type": "weekly", "days": [0, 2]})

    def test_old_file_without_repeat_reads_none(self):
        payload = [{"id": "a", "text": "旧待办"}]
        (self.dir / "todos.json").write_text(json.dumps(payload), encoding="utf-8")

        self.assertIsNone(store.load_todos(self.dir)[0]["repeat"])

    def test_malformed_repeat_is_dropped_but_todo_kept(self):
        payload = [{"id": "a", "text": "坏了", "repeat": {"type": "yearly"}}]
        (self.dir / "todos.json").write_text(json.dumps(payload), encoding="utf-8")

        loaded = store.load_todos(self.dir)

        self.assertEqual(len(loaded), 1)
        self.assertIsNone(loaded[0]["repeat"])

    def test_format_repeat(self):
        self.assertEqual(store.format_repeat({"type": "daily"}), "每天")
        self.assertEqual(store.format_repeat({"type": "weekdays"}), "工作日")
        self.assertEqual(store.format_repeat({"type": "weekly", "days": [0, 2, 4]}), "每周一、三、五")
        self.assertEqual(store.format_repeat({"type": "monthly", "day": 15}), "每月15日")
        self.assertEqual(store.format_repeat(None), "")

    def test_due_skips_completed_todos(self):
        now = datetime.now().astimezone()
        past = (now - timedelta(minutes=1)).isoformat(timespec="seconds")
        todos = [
            {"id": "a", "remind_at": past, "reminded": False, "done": True},
            {"id": "b", "remind_at": past, "reminded": False, "done": False},
        ]

        self.assertEqual([todo["id"] for todo in store.due_todos(todos, now)], ["b"])


class NewFieldTests(unittest.TestCase):
    def setUp(self):
        self.dir = make_data_dir()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_old_todo_gets_safe_defaults(self):
        (self.dir / "todos.json").write_text('[{"id": "a", "text": "旧待办"}]', encoding="utf-8")

        todo = store.load_todos(self.dir)[0]

        self.assertEqual(todo["priority"], 0)
        self.assertFalse(todo["pinned"])
        self.assertEqual(todo["tags"], [])
        self.assertIsNone(todo["parent_id"])
        self.assertEqual(todo["note"], "")
        self.assertIsNone(todo["due_at"])
        self.assertIsNone(todo["completed_at"])
        self.assertIsNone(todo["repeat_base"])

    def test_invalid_fields_fall_back(self):
        raw = [{
            "id": "a", "text": "坏的",
            "priority": 9,
            "tags": ["好", "好", "", "12345678901", 3],
            "parent_id": "missing",
            "due_at": 123,
            "note": None,
        }]
        (self.dir / "todos.json").write_text(json.dumps(raw), encoding="utf-8")

        todo = store.load_todos(self.dir)[0]

        self.assertEqual(todo["priority"], 0)
        self.assertEqual(todo["tags"], ["好"])
        self.assertIsNone(todo["parent_id"])
        self.assertIsNone(todo["due_at"])
        self.assertEqual(todo["note"], "")

    def test_nested_parent_is_dropped(self):
        raw = [
            {"id": "parent", "text": "父"},
            {"id": "child", "text": "子", "parent_id": "parent"},
            {"id": "grand", "text": "孙", "parent_id": "child"},
        ]
        (self.dir / "todos.json").write_text(json.dumps(raw), encoding="utf-8")

        todos = {todo["id"]: todo for todo in store.load_todos(self.dir)}

        self.assertEqual(todos["child"]["parent_id"], "parent")
        self.assertIsNone(todos["grand"]["parent_id"])

    def test_new_todo_keeps_all_fields(self):
        todo = store.new_todo(
            "写周报", remind_at="2026-11-12T09:00:00+08:00", repeat={"type": "daily"},
            tags=["工作", "工作"], priority=2, pinned=True, note="备注",
            due_at="2026-11-12T18:00:00+08:00", parent_id="p",
        )

        self.assertEqual(todo["tags"], ["工作"])
        self.assertEqual(todo["priority"], 2)
        self.assertTrue(todo["pinned"])
        self.assertEqual(todo["note"], "备注")
        self.assertEqual(todo["due_at"], "2026-11-12T18:00:00+08:00")
        self.assertEqual(todo["repeat_base"], "2026-11-12T09:00:00+08:00")

    def test_effective_due_prefers_due_at(self):
        todo = {"remind_at": "2026-11-12T09:00:00+08:00", "due_at": "2026-11-13T18:00:00+08:00"}
        self.assertEqual(store.effective_due(todo), "2026-11-13T18:00:00+08:00")
        self.assertEqual(store.effective_due({"remind_at": "2026-11-12T09:00:00+08:00"}), "2026-11-12T09:00:00+08:00")

    def test_occurrences_use_the_due_date(self):
        todo = {"due_at": "2026-11-13T18:00:00+08:00"}
        self.assertEqual(store.occurrences(todo, date(2026, 11, 13), date(2026, 11, 13)), [date(2026, 11, 13)])


class MatchTests(unittest.TestCase):
    def test_text_and_tags(self):
        todo = {"text": "写周报", "tags": ["工作", "学习"]}
        self.assertTrue(store.matches(todo, "周报"))
        self.assertTrue(store.matches(todo, "工作"))
        self.assertTrue(store.matches(todo, "#工作"))
        self.assertFalse(store.matches(todo, "#学习 周报 不存在"))
        self.assertTrue(store.matches(todo, ""))

    def test_latin_is_case_insensitive(self):
        self.assertTrue(store.matches({"text": "Buy MILK", "tags": []}, "milk"))

    def test_hash_only_matches_tags(self):
        todo = {"text": "工作笔记", "tags": []}
        self.assertFalse(store.matches(todo, "#工作"))


TODAY = date(2026, 11, 11)
NOW = datetime(2026, 11, 11, 10, 0).astimezone()


def filtered_todo(todo_id, **fields):
    todo = {
        "id": todo_id,
        "text": todo_id,
        "done": False,
        "remind_at": None,
        "due_at": None,
        "priority": 0,
        "tags": [],
        "repeat": None,
    }
    todo.update(fields)
    return todo


class ApplyFilterTests(unittest.TestCase):
    def setUp(self):
        self.today_due = filtered_todo("today", due_at="2026-11-11T15:00:00+08:00")
        self.repeating = filtered_todo(
            "repeating",
            remind_at="2026-11-11T15:00:00+08:00",
            repeat={"type": "daily"},
            repeat_base="2026-11-11T15:00:00+08:00",
        )
        self.tomorrow_due = filtered_todo("tomorrow", due_at="2026-11-12T15:00:00+08:00")
        self.overdue = filtered_todo("overdue", due_at="2026-11-10T15:00:00+08:00")
        self.finished = filtered_todo(
            "finished", done=True, completed_at="2026-11-10T16:00:00+08:00"
        )
        self.no_date = filtered_todo("nodate")
        self.todos = [
            self.today_due, self.repeating, self.tomorrow_due,
            self.overdue, self.finished, self.no_date,
        ]

    def ids(self, key):
        return [todo["id"] for todo in store.apply_filter(self.todos, key, TODAY, NOW)]

    def test_all_returns_everything(self):
        self.assertEqual(self.ids("all"), [todo["id"] for todo in self.todos])

    def test_today_includes_a_repeat_occurrence(self):
        self.assertEqual(self.ids("today"), ["today", "repeating"])

    def test_overdue_excludes_done(self):
        self.assertEqual(self.ids("overdue"), ["overdue"])

    def test_week7_bounds(self):
        week = filtered_todo("week", due_at="2026-11-18T09:00:00+08:00")
        later = filtered_todo("later", due_at="2026-11-19T09:00:00+08:00")
        todos = self.todos + [week, later]

        result = [todo["id"] for todo in store.apply_filter(todos, "week7", TODAY, NOW)]

        self.assertIn("week", result)
        self.assertNotIn("later", result)
        self.assertNotIn("finished", result)

    def test_nodate(self):
        self.assertEqual(self.ids("nodate"), ["finished", "nodate"])

    def test_done(self):
        self.assertEqual(self.ids("done"), ["finished"])

    def test_unknown_key_falls_back_to_all(self):
        self.assertEqual(self.ids("bogus"), [todo["id"] for todo in self.todos])


class MatchOperatorTests(unittest.TestCase):
    def setUp(self):
        self.done_high = filtered_todo(
            "done work", done=True, priority=3, tags=["工作"],
            due_at="2026-11-11T15:00:00+08:00",
        )
        self.open_low = filtered_todo("open low", priority=1)
        self.medium_tomorrow = filtered_todo(
            "medium", priority=2, due_at="2026-11-12T15:00:00+08:00"
        )
        self.overdue = filtered_todo("overdue", due_at="2026-11-10T15:00:00+08:00")
        self.week = filtered_todo("week", due_at="2026-11-18T09:00:00+08:00")
        self.colon = filtered_todo("see foo:bar")

    def match(self, todo, query):
        return store.matches(todo, query, TODAY, NOW)

    def test_is_operators(self):
        self.assertTrue(self.match(self.done_high, "is:done"))
        self.assertFalse(self.match(self.open_low, "is:done"))
        self.assertTrue(self.match(self.open_low, "is:open"))

    def test_due_operators(self):
        self.assertTrue(self.match(self.done_high, "due:today"))
        self.assertTrue(self.match(self.medium_tomorrow, "due:tomorrow"))
        self.assertTrue(self.match(self.overdue, "due:overdue"))
        self.assertFalse(self.match(self.done_high, "due:overdue"))
        self.assertTrue(self.match(self.week, "due:week"))
        self.assertTrue(self.match(self.open_low, "due:none"))
        self.assertFalse(self.match(self.overdue, "due:none"))

    def test_priority_operators(self):
        self.assertTrue(self.match(self.done_high, "priority:high"))
        self.assertTrue(self.match(self.medium_tomorrow, "priority:medium"))
        self.assertTrue(self.match(self.open_low, "priority:low"))
        self.assertTrue(self.match(self.colon, "priority:none"))
        self.assertTrue(self.match(self.medium_tomorrow, "priority:2"))
        self.assertTrue(self.match(self.colon, "priority:0"))
        self.assertFalse(self.match(self.open_low, "priority:0"))

    def test_combination_is_and(self):
        self.assertTrue(self.match(self.done_high, "#工作 is:done due:today priority:high"))
        self.assertFalse(self.match(self.done_high, "#工作 is:open"))
        self.assertFalse(self.match(self.medium_tomorrow, "due:today priority:high"))

    def test_unknown_operator_is_searched_as_text(self):
        self.assertTrue(self.match(self.colon, "foo:bar"))
        self.assertFalse(self.match(self.done_high, "foo:bar"))


class CompletionStatsTests(unittest.TestCase):
    def stats(self, todos, today=date(2026, 11, 11)):
        return store.completion_stats(todos, today)

    def test_counts_and_streak(self):
        todos = [
            {"done": True, "completed_at": "2026-11-11T10:00:00+08:00"},
            {"done": True, "completed_at": "2026-11-11T12:00:00+08:00"},
            {"done": True, "completed_at": "2026-11-10T09:00:00+08:00"},
            {"done": True, "completed_at": "2026-11-09T09:00:00+08:00"},
            {"done": True, "completed_at": "2026-10-30T09:00:00+08:00"},
            {"done": False, "completed_at": None},
        ]

        stats = self.stats(todos)

        self.assertEqual(stats["today"], 2)
        self.assertEqual(stats["week"], 4)
        self.assertEqual(stats["month"], 4)
        self.assertEqual(stats["total"], 5)
        self.assertEqual(stats["streak"], 3)

    def test_streak_starts_yesterday_when_today_is_empty(self):
        todos = [
            {"done": True, "completed_at": "2026-11-10T09:00:00+08:00"},
            {"done": True, "completed_at": "2026-11-09T09:00:00+08:00"},
        ]

        self.assertEqual(self.stats(todos)["streak"], 2)


class ExportImportTests(unittest.TestCase):
    def setUp(self):
        self.dir = make_data_dir()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.other = make_data_dir()
        self.addCleanup(shutil.rmtree, self.other, True)
        self.path = str(self.dir / "export.json")

    def test_roundtrip_restores_every_field(self):
        todo = store.new_todo(
            "导出我", remind_at="2026-11-12T09:00:00+08:00", repeat={"type": "daily"},
            tags=["工作"], priority=3, pinned=True, note="note", due_at="2026-11-12T18:00:00+08:00",
        )
        store.save_todos(self.dir, [todo])

        self.assertEqual(store.export_todos(self.dir, self.path), 1)
        added, skipped = store.import_todos(self.other, self.path)

        self.assertEqual((added, skipped), (1, 0))
        loaded = store.load_todos(self.other)[0]
        for key in ("text", "tags", "priority", "pinned", "note", "due_at", "remind_at", "repeat"):
            self.assertEqual(loaded[key], todo[key])

    def test_duplicate_ids_are_skipped(self):
        todo = store.new_todo("重复")
        store.save_todos(self.dir, [todo])
        store.export_todos(self.dir, self.path)

        added, skipped = store.import_todos(self.dir, self.path)

        self.assertEqual((added, skipped), (0, 1))
        self.assertEqual(len(store.load_todos(self.dir)), 1)

    def test_broken_file_changes_nothing(self):
        store.save_todos(self.dir, [store.new_todo("保留我")])
        (self.dir / "bad.json").write_text("{not json", encoding="utf-8")

        with self.assertRaises(ValueError):
            store.import_todos(self.dir, str(self.dir / "bad.json"))

        self.assertEqual([todo["text"] for todo in store.load_todos(self.dir)], ["保留我"])


if __name__ == "__main__":
    unittest.main()
