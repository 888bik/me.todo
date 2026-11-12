import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

import main
import quickparse
import store
from test_support import make_data_dir

HERE = os.path.dirname(os.path.abspath(__file__))


class PluginTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = make_data_dir()
        self.addCleanup(shutil.rmtree, self.dir, True)
        main.app = main.App(str(self.dir))
        main.app.load(str(self.dir))
        self.app = main.app

    def query(self, text=""):
        return main.winshun.Query(text=text, command="db", trigger="keyword", keyword="db", query_id=1)

    def action(self, data):
        return main.winshun.ActionRequest(action="default", item={"data": data}, text=data.get("text", ""))


class QueryTests(PluginTestCase):
    def test_text_offers_to_add_it_and_open_full_list(self):
        items = main.on_query(self.query("买牛奶"), None)

        add_item = next(item for item in items if item["id"].startswith("add-"))
        open_item = next(item for item in items if item["id"] == "open-todo-window")
        self.assertEqual(items[0]["id"], add_item["id"])
        self.assertEqual(add_item["title"], "新建待办：买牛奶")
        self.assertEqual(add_item["copy"], "买牛奶")
        self.assertEqual(add_item["data"], {"kind": "add", "text": "买牛奶"})
        self.assertEqual(open_item["data"]["kind"], "open")

    def test_empty_text_shows_current_todos_and_full_window_action(self):
        self.app.add("买牛奶")
        self.app.add("学习 Python")

        items = main.on_query(self.query(""), None)

        self.assertEqual(items[0]["id"], "open-todo-window")
        self.assertIn("买牛奶", [item["title"] for item in items])
        self.assertIn("学习 Python", [item["title"] for item in items])

    def test_empty_text_shows_clear_empty_state(self):
        items = main.on_query(self.query(""), None)

        self.assertEqual(items[0]["id"], "open-todo-window")
        self.assertEqual(items[-2]["title"], "还没有待办")
        self.assertFalse(items[-2]["valid"])
        self.assertEqual(items[-1]["id"], "keyboard-hint")
        self.assertFalse(items[-1]["valid"])

    def test_keyboard_hint_is_last_for_text_queries(self):
        items = main.on_query(self.query("买牛奶"), None)

        hint = items[-1]
        self.assertEqual(hint["id"], "keyboard-hint")
        self.assertEqual(hint["title"], "Ctrl+N 向下 · Ctrl+P 向上 · Ctrl+1~9 直达第几行")
        self.assertEqual(hint["subtitle"], "回车确认当前选中行")
        self.assertFalse(hint["valid"])

    def test_keyboard_hint_follows_the_preview_in_parsed_queries(self):
        items = main.on_query(self.query("明天下午3点开会"), None)

        self.assertTrue(items[0]["id"].startswith("add-parsed-"))
        self.assertEqual(items[-1]["id"], "keyboard-hint")

    def test_item_id_is_stable_for_the_same_text(self):
        first = main.on_query(self.query("买牛奶"), None)[0]["id"]
        second = main.on_query(self.query("买牛奶"), None)[0]["id"]

        self.assertEqual(first, second)

    def test_todo_keyword_behaves_like_db(self):
        from_todo = main.winshun.Query(text="买牛奶", command="todo", trigger="keyword", keyword="todo", query_id=1)

        self.assertEqual(
            main.on_query(from_todo, None)[0]["title"],
            main.on_query(self.query("买牛奶"), None)[0]["title"],
        )


class QuickAddQueryTests(PluginTestCase):
    def test_parsed_preview_comes_first(self):
        items = main.on_query(self.query("明天下午3点开会"), None)
        preview = items[0]

        self.assertTrue(preview["id"].startswith("add-parsed-"))
        self.assertEqual(preview["title"], "开会")
        self.assertIn("截止明天 15:00", preview["subtitle"])
        self.assertIn("回车确认添加", preview["subtitle"])
        self.assertEqual(preview["data"]["kind"], "add-parsed")
        expected = quickparse.parse_quick_input("明天下午3点开会")
        self.assertEqual(preview["data"]["remind_at"], expected["remind_at"])
        self.assertEqual(preview["data"]["due_at"], expected["due_at"])
        raw = next(item for item in items if item["data"].get("kind") == "add")
        self.assertTrue(raw["title"].startswith("直接添加原文："))

    def test_no_markers_keeps_the_old_rows(self):
        items = main.on_query(self.query("买牛奶"), None)

        self.assertTrue(items[0]["id"].startswith("add-"))
        self.assertFalse(items[0]["id"].startswith("add-parsed-"))
        self.assertFalse(any(item.get("data", {}).get("kind") == "add-parsed" for item in items))

    def test_repeat_preview_and_spawn(self):
        items = main.on_query(self.query("每天开会"), None)
        preview = items[0]

        self.assertEqual(preview["data"]["repeat"], {"type": "daily"})
        self.assertIn("每天重复", preview["subtitle"])

        with mock.patch("main.sticky_window.refresh"), \
                mock.patch.object(self.app, "show_sticky"):
            main.on_action(self.action(preview["data"]), None)

        todo = store.load_todos(self.dir)[0]
        self.assertEqual(todo["repeat"], {"type": "daily"})
        self.assertEqual(todo["repeat_base"], todo["remind_at"])

        with mock.patch("main.sticky_window.refresh"):
            self.app.toggle(todo["id"])

        loaded = store.load_todos(self.dir)
        old = [item for item in loaded if item["done"]][0]
        new = [item for item in loaded if not item["done"]][0]
        self.assertEqual(old["id"], todo["id"])
        self.assertEqual(new["repeat"], {"type": "daily"})

    def test_add_parsed_writes_dates_tags_and_priority(self):
        data = {
            "kind": "add-parsed",
            "text": "交报告",
            "remind_at": "2026-11-13T09:00:00+08:00",
            "due_at": "2026-11-13T23:59:00+08:00",
            "tags": ["工作"],
            "priority": 2,
        }

        with mock.patch("main.sticky_window.refresh"), \
                mock.patch.object(self.app, "show_sticky") as show:
            effects = main.on_action(self.action(data), None)

        show.assert_called_once_with()
        self.assertIn({"type": "hide"}, effects)
        todo = store.load_todos(self.dir)[0]
        self.assertEqual(todo["text"], "交报告")
        self.assertEqual(todo["remind_at"], "2026-11-13T09:00:00+08:00")
        self.assertEqual(todo["due_at"], "2026-11-13T23:59:00+08:00")
        self.assertEqual(todo["tags"], ["工作"])
        self.assertEqual(todo["priority"], 2)

    def test_search_operators_filter_existing_todos(self):
        now = datetime.now().astimezone()
        self.app.add(
            "今天的事",
            due_at=now.isoformat(timespec="seconds"),
        )
        self.app.add(
            "以后的事",
            due_at=(now + timedelta(days=3)).isoformat(timespec="seconds"),
        )

        items = main.on_query(self.query("due:today"), None)
        titles = [item["title"] for item in items if item["id"].startswith("toggle-")]

        self.assertIn("今天的事", titles)
        self.assertNotIn("以后的事", titles)


class FilterConfigTests(PluginTestCase):
    def test_filter_is_saved_and_restored(self):
        self.app.set_filter("overdue")

        fresh = main.App(str(self.dir))
        fresh.load(str(self.dir))

        self.assertEqual(fresh.config["filter"], "overdue")

    def test_compact_is_saved_and_restored(self):
        self.app.set_compact(True)

        fresh = main.App(str(self.dir))
        fresh.load(str(self.dir))

        self.assertTrue(fresh.config["compact"])

    def test_transparency_is_saved_and_restored(self):
        self.assertEqual(self.app.set_transparency(70), 70)

        fresh = main.App(str(self.dir))
        fresh.load(str(self.dir))

        self.assertEqual(fresh.config["transparency"], 70)
        self.assertEqual(self.app.set_transparency(20), 50)
        self.assertEqual(self.app.set_transparency(200), 100)

    def test_tray_compact_item_toggles_the_setting(self):
        self.assertFalse(self.app.config["compact"])
        items = main.on_tray_menu(None)
        compact_item = [item for item in items if item["id"] == "compact"][0]
        self.assertFalse(compact_item["checked"])

        with mock.patch("main.sticky_window.set_compact") as setter:
            main.on_menu("compact", None)

        self.assertTrue(self.app.config["compact"])
        setter.assert_called_once_with(True)
        items = main.on_tray_menu(None)
        self.assertTrue([item for item in items if item["id"] == "compact"][0]["checked"])


class ActionTests(PluginTestCase):
    def test_add_saves_the_todo_and_notifies(self):
        with mock.patch("main.sticky_window.refresh"), \
                mock.patch.object(self.app, "show_sticky") as show:
            effects = main.on_action(self.action({"kind": "add", "text": "买牛奶"}), None)

        show.assert_called_once_with()
        self.assertIn({"type": "notify", "text": "已添加：买牛奶"}, effects)
        self.assertIn({"type": "hide"}, effects)
        self.assertEqual([todo["text"] for todo in self.app.list()], ["买牛奶"])

        raw = json.loads((self.dir / "todos.json").read_text(encoding="utf-8"))
        self.assertEqual(raw[0]["text"], "买牛奶")

    def test_open_shows_the_note_with_input_focus(self):
        with mock.patch.object(self.app, "show_sticky") as show:
            effects = main.on_action(self.action({"kind": "open"}), None)

        show.assert_called_once_with(focus=True)
        self.assertIn({"type": "hide"}, effects)

    def test_blank_add_is_rejected(self):
        effects = main.on_action(self.action({"kind": "add", "text": "  "}), None)

        self.assertEqual(effects[0], {"type": "notify", "text": "待办内容为空"})
        self.assertEqual(self.app.list(), [])

    def test_toggle_action_marks_done_and_notifies(self):
        todo = self.app.add("买牛奶")

        with mock.patch("main.sticky_window.refresh") as refresh:
            effects = main.on_action(
                self.action({"kind": "toggle", "id": todo["id"], "text": "买牛奶"}), None
            )

        self.assertTrue(store.load_todos(self.dir)[0]["done"])
        refresh.assert_called_once()
        self.assertIn({"type": "notify", "text": "已完成：买牛奶"}, effects)
        self.assertIn({"type": "hide"}, effects)

    def test_toggle_action_cancels_completion(self):
        todo = self.app.add("买牛奶")
        self.app.toggle(todo["id"])

        with mock.patch("main.sticky_window.refresh"):
            effects = main.on_action(
                self.action({"kind": "toggle", "id": todo["id"], "text": "买牛奶"}), None
            )

        self.assertFalse(store.load_todos(self.dir)[0]["done"])
        self.assertIn({"type": "notify", "text": "已取消完成：买牛奶"}, effects)

    def test_toggle_action_on_a_deleted_todo_does_not_crash(self):
        with mock.patch("main.sticky_window.refresh"):
            effects = main.on_action(
                self.action({"kind": "toggle", "id": "missing", "text": "没了"}), None
            )

        self.assertIn({"type": "notify", "text": "该待办已删除"}, effects)
        self.assertIn({"type": "hide"}, effects)


class SearchTests(PluginTestCase):
    def test_chinese_substring_finds_the_todo(self):
        self.app.add("买牛奶")

        items = main.on_query(self.query("牛奶"), None)

        self.assertEqual(
            [item["title"] for item in items],
            [
                "买牛奶", "新建待办：牛奶", "打开完整待办窗口",
                "Ctrl+N 向下 · Ctrl+P 向上 · Ctrl+1~9 直达第几行",
            ],
        )
        self.assertTrue(items[0]["id"].startswith("toggle-"))
        self.assertEqual(items[0]["copy"], "买牛奶")

    def test_latin_match_ignores_case(self):
        self.app.add("Buy MILK")

        items = main.on_query(self.query("milk"), None)

        self.assertIn("Buy MILK", [item["title"] for item in items])

    def test_finished_todo_is_listed_with_status(self):
        todo = self.app.add("买牛奶")
        self.app.toggle(todo["id"])

        items = main.on_query(self.query("牛奶"), None)
        toggle = [item for item in items if item["id"].startswith("toggle-")][0]

        self.assertIn("已完成", toggle["subtitle"])
        self.assertIn("取消完成", toggle["subtitle"])

    def test_subtitle_carries_deadline_and_repeat(self):
        todo = self.app.add("交房租")
        self.app.set_remind(todo["id"], "2026-11-12T09:00:00+08:00", {"type": "daily"})

        items = main.on_query(self.query("房租"), None)
        toggle = [item for item in items if item["id"].startswith("toggle-")][0]

        self.assertEqual(
            toggle["subtitle"], "回车标记完成 · 截止 2026-11-12 09:00 · 每天"
        )

    def test_no_match_offers_add_and_full_list_actions(self):
        items = main.on_query(self.query("不存在"), None)

        self.assertEqual(len(items), 3)
        self.assertTrue(items[0]["title"].startswith("新建待办："))
        self.assertEqual(items[1]["id"], "open-todo-window")
        self.assertEqual(items[2]["id"], "keyboard-hint")

    def test_at_most_eight_matches(self):
        for index in range(10):
            self.app.add("任务%d" % index)

        items = main.on_query(self.query("任务"), None)

        self.assertEqual(len([item for item in items if item["id"].startswith("toggle-")]), 8)

    def test_empty_query_lists_current_tasks(self):
        self.app.add("整理笔记")

        items = main.on_query(self.query(""), None)

        self.assertEqual(items[0]["id"], "open-todo-window")
        self.assertIn("整理笔记", [item["title"] for item in items])


class TrayMenuTests(PluginTestCase):
    def test_hidden_note_offers_show(self):
        with mock.patch("main.sticky_window.is_visible", return_value=False):
            items = main.on_tray_menu(None)

        self.assertEqual(
            [item["id"] for item in items],
            ["toggle", "lock", "new", "compact", "stats", "export", "import"],
        )
        self.assertEqual(items[0]["title"], "显示便签")
        self.assertFalse(items[0]["checked"])
        self.assertEqual(items[1]["title"], "锁定便签")
        self.assertFalse(items[1]["checked"])

    def test_visible_note_is_checked(self):
        with mock.patch("main.sticky_window.is_visible", return_value=True):
            items = main.on_tray_menu(None)

        self.assertEqual(items[0]["title"], "隐藏便签")
        self.assertTrue(items[0]["checked"])

    def test_menu_toggle_hides_when_the_window_is_visible(self):
        with mock.patch("main.sticky_window.is_visible", return_value=True), \
                mock.patch.object(self.app, "hide_sticky") as hide:
            main.on_menu("toggle", None)

        hide.assert_called_once()

    def test_menu_toggle_calls_the_toggle(self):
        with mock.patch.object(self.app, "toggle_sticky") as toggle:
            main.on_menu("toggle", None)

        toggle.assert_called_once()

    def test_menu_new_shows_with_focus(self):
        with mock.patch.object(self.app, "show_sticky") as show:
            main.on_menu("new", None)

        show.assert_called_once_with(focus=True)


class HotkeyTests(PluginTestCase):
    def test_hotkey_shows_the_note_and_focuses_input(self):
        with mock.patch.object(self.app, "show_sticky") as show:
            main.on_hotkey("Ctrl+Alt+T", None)

        show.assert_called_once_with(focus=True)

    def test_registration_uses_the_configured_key(self):
        self.app.settings = {"hotkey": "Ctrl+Shift+J"}
        plugin = mock.Mock()
        plugin.tool.return_value = {"errors": {}}

        self.assertTrue(main.register_hotkey(self.app, plugin))
        plugin.tool.assert_called_once_with("register_hotkeys", keys=["Ctrl+Shift+J", main.UNLOCK_HOTKEY])
        plugin.hotkeys.assert_called_with(["Ctrl+Shift+J", main.UNLOCK_HOTKEY])

    def test_emergency_unlock_hotkey_releases_a_locked_note(self):
        self.app.set_locked(True)
        with mock.patch("main.sticky_window.set_locked") as set_locked, \
                mock.patch("main.winshun.notify") as notify:
            main.on_hotkey(main.UNLOCK_HOTKEY, None)

        self.assertFalse(self.app.config["locked"])
        set_locked.assert_called_once_with(False)
        notify.assert_called_once_with("便签已解锁")

    def test_emergency_unlock_hotkey_does_not_toggle_visibility_when_unlocked(self):
        with mock.patch("main.sticky_window.is_visible") as visible, \
                mock.patch.object(self.app, "show_sticky") as show, \
                mock.patch.object(self.app, "hide_sticky") as hide:
            main.on_hotkey(main.UNLOCK_HOTKEY, None)

        visible.assert_not_called()
        show.assert_not_called()
        hide.assert_not_called()

    def test_registration_retries_once_after_300ms(self):
        plugin = mock.Mock()
        plugin.tool.side_effect = [{"errors": {"Ctrl+Alt+T": 87}}, {"errors": {}}]

        with mock.patch("main.time.sleep") as sleep:
            ok = main.register_hotkey(self.app, plugin)

        self.assertTrue(ok)
        self.assertEqual(plugin.tool.call_count, 2)
        sleep.assert_called_once_with(0.3)

    def test_registration_gives_up_after_the_retry(self):
        plugin = mock.Mock()
        plugin.tool.return_value = {"errors": {"Ctrl+Alt+T": 87}}

        with mock.patch("main.time.sleep"):
            ok = main.register_hotkey(self.app, plugin)

        self.assertFalse(ok)
        self.assertEqual(plugin.tool.call_count, 2)


class ReminderTests(PluginTestCase):
    def due_todo(self, text="到期"):
        todo = self.app.add(text)
        past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat(timespec="seconds")
        self.app.set_remind(todo["id"], past)
        return todo

    def test_due_reminder_toasts_once(self):
        todo = self.due_todo()
        plugin = mock.Mock()

        first = main.fire_reminders(self.app, plugin)
        second = main.fire_reminders(self.app, plugin)

        self.assertEqual(first, [todo["id"]])
        self.assertEqual(second, [])
        plugin.toast.assert_called_once()
        args = plugin.toast.call_args
        self.assertEqual(args.args[0], "待办提醒")
        self.assertEqual(args.args[1], "到期")

    def test_reminded_flag_is_persisted(self):
        self.due_todo()
        main.fire_reminders(self.app, mock.Mock())

        reloaded = store.load_todos(self.dir)

        self.assertTrue(reloaded[0]["reminded"])

    def test_batch_due_reminders_merge_into_one_toast(self):
        ids = [self.due_todo("一")["id"], self.due_todo("二")["id"], self.due_todo("三")["id"]]
        plugin = mock.Mock()

        fired = main.fire_reminders(self.app, plugin)

        plugin.toast.assert_called_once()
        title, body = plugin.toast.call_args.args[0], plugin.toast.call_args.args[1]
        self.assertEqual(title, "待办提醒（3 条）")
        for text in ("一", "二", "三"):
            self.assertIn(text, body)
        self.assertEqual(sorted(fired), sorted(ids))
        self.assertTrue(all(todo["reminded"] for todo in self.app.list()))

    def test_batch_body_is_cut_to_200_characters(self):
        now = datetime.now().astimezone()
        past = (now - timedelta(minutes=1)).isoformat(timespec="seconds")
        for _ in range(3):
            todo = self.app.add("长" * 100)
            self.app.set_remind(todo["id"], past)
        plugin = mock.Mock()

        main.fire_reminders(self.app, plugin)

        self.assertLessEqual(len(plugin.toast.call_args.args[1]), 200)

    def test_repeating_reminder_moves_to_the_next_occurrence(self):
        todo = self.app.add("每天的事")
        now = datetime.now().astimezone()
        past = (now - timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
        self.app.set_remind(todo["id"], past.isoformat(timespec="seconds"), {"type": "daily"})

        main.fire_reminders(self.app, mock.Mock())

        reloaded = store.load_todos(self.dir)[0]
        self.assertFalse(reloaded["reminded"])
        self.assertEqual(reloaded["repeat"], {"type": "daily"})
        self.assertGreater(datetime.fromisoformat(reloaded["remind_at"]), now)

    def test_completed_todo_is_not_reminded(self):
        todo = self.app.add("已经做完")
        past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat(timespec="seconds")
        self.app.set_remind(todo["id"], past)
        self.app.toggle(todo["id"])
        plugin = mock.Mock()

        main.fire_reminders(self.app, plugin)

        plugin.toast.assert_not_called()


    def test_batch_reminders_save_once(self):
        now = datetime.now().astimezone()
        one = self.app.add("一次性")
        self.app.set_remind(one["id"], (now - timedelta(minutes=1)).isoformat(timespec="seconds"))
        rep = self.app.add("每天")
        self.app.set_remind(
            rep["id"], (now - timedelta(minutes=1)).isoformat(timespec="seconds"),
            {"type": "daily"},
        )

        with mock.patch("main.store.save_todos", wraps=store.save_todos) as save:
            fired = main.fire_reminders(self.app, mock.Mock(), now=now)

        self.assertEqual(len(fired), 2)
        self.assertEqual(save.call_count, 1)
        loaded = {item["id"]: item for item in store.load_todos(self.dir)}
        self.assertTrue(loaded[one["id"]]["reminded"])
        self.assertFalse(loaded[rep["id"]]["reminded"])
        self.assertGreater(
            datetime.fromisoformat(loaded[rep["id"]]["remind_at"]), now
        )

    def test_single_mark_reminded_still_saves(self):
        todo = self.app.add("单条")

        with mock.patch("main.store.save_todos", wraps=store.save_todos) as save:
            self.assertTrue(self.app.mark_reminded(todo["id"]))

        self.assertEqual(save.call_count, 1)

    def test_single_reschedule_still_saves(self):
        todo = self.app.add("单条重复")
        iso = datetime.now().astimezone().isoformat(timespec="seconds")

        with mock.patch("main.store.save_todos", wraps=store.save_todos) as save:
            self.assertTrue(self.app.reschedule_repeat(todo["id"], iso))

        self.assertEqual(save.call_count, 1)


class SettingsTests(PluginTestCase):
    def test_settings_change_applies_immediately(self):
        with mock.patch("main.sticky_window.apply_settings") as apply, \
                mock.patch.object(self.app, "show_sticky") as show, \
                mock.patch.object(self.app, "hide_sticky") as hide, \
                mock.patch("main.register_hotkey"):
            main.on_settings({"show_sticky": False, "transparency": 60, "hotkey": "Ctrl+Alt+Y"}, None)

        apply.assert_called_once()
        self.assertEqual(apply.call_args.args[0]["transparency"], 60)
        hide.assert_called_once()
        show.assert_not_called()

    def test_initialization_loads_data_and_shows_the_note(self):
        plugin = mock.Mock()
        plugin.data_dir = str(self.dir)
        plugin.settings = {"show_sticky": True}

        with mock.patch("main.sticky_window.show") as show, \
                mock.patch("main.start_reminders") as reminders, \
                mock.patch("main.threading.Thread"):
            main.on_initialized(plugin)

        show.assert_called_once()
        reminders.assert_called_once()


class TodoAppTests(PluginTestCase):
    def test_toggle_marks_done_and_persists(self):
        todo = self.app.add("买牛奶")

        self.assertTrue(self.app.toggle(todo["id"]))

        self.assertTrue(store.load_todos(self.dir)[0]["done"])
        self.assertEqual(self.app.list()[0]["done"], True)

    def test_delete_removes_and_persists(self):
        todo = self.app.add("买牛奶")

        self.assertTrue(self.app.delete(todo["id"]))

        self.assertEqual(self.app.list(), [])
        self.assertEqual(store.load_todos(self.dir), [])

    def test_set_remind_can_be_set_and_cleared(self):
        todo = self.app.add("买牛奶")

        self.assertTrue(self.app.set_remind(todo["id"], store.parse_reminder("2030-01-01 08:00")))
        self.assertIsNotNone(self.app.list()[0]["remind_at"])

        self.assertTrue(self.app.set_remind(todo["id"], None))
        self.assertIsNone(self.app.list()[0]["remind_at"])


class SortTests(PluginTestCase):
    def test_open_by_deadline_then_no_deadline_then_done(self):
        first = self.app.add("没截止")
        late = self.app.add("晚")
        early = self.app.add("早")
        finished = self.app.add("做完了")
        self.app.set_remind(late["id"], "2026-12-01T09:00:00+08:00")
        self.app.set_remind(early["id"], "2026-11-01T09:00:00+08:00")
        self.app.toggle(finished["id"])

        texts = [todo["text"] for todo in self.app.list()]

        self.assertEqual(texts, ["早", "晚", "没截止", "做完了"])

    def test_saving_keeps_the_insertion_order(self):
        first = self.app.add("没截止")
        late = self.app.add("晚")
        self.app.set_remind(late["id"], "2026-12-01T09:00:00+08:00")

        self.app.list()

        self.assertEqual([todo["text"] for todo in store.load_todos(self.dir)], ["没截止", "晚"])

    def test_unparsable_deadline_sorts_as_no_deadline(self):
        broken = self.app.add("坏时间")
        real = self.app.add("真截止")
        self.app.set_remind(broken["id"], "not-a-date")
        self.app.set_remind(real["id"], "2026-12-01T09:00:00+08:00")

        self.assertEqual([todo["text"] for todo in self.app.list()], ["真截止", "坏时间"])


class EditTextTests(PluginTestCase):
    def test_edit_keeps_reminder_and_done(self):
        todo = self.app.add("旧文字")
        self.app.set_remind(todo["id"], "2030-01-01T09:00:00+08:00")
        self.app.toggle(todo["id"])

        self.assertTrue(self.app.edit_text(todo["id"], "  新文字  "))

        reloaded = store.load_todos(self.dir)[0]
        self.assertEqual(reloaded["text"], "新文字")
        self.assertEqual(reloaded["remind_at"], "2030-01-01T09:00:00+08:00")
        self.assertTrue(reloaded["done"])

    def test_blank_text_is_not_saved(self):
        todo = self.app.add("旧文字")

        self.assertFalse(self.app.edit_text(todo["id"], "   "))

        self.assertEqual(store.load_todos(self.dir)[0]["text"], "旧文字")


class ClearDoneTests(PluginTestCase):
    def test_clear_done_removes_only_finished_todos(self):
        first = self.app.add("A")
        self.app.add("B")
        third = self.app.add("C")
        self.app.toggle(first["id"])
        self.app.toggle(third["id"])

        self.assertEqual(self.app.clear_done(), 2)

        self.assertEqual([todo["text"] for todo in store.load_todos(self.dir)], ["B"])

    def test_clear_done_is_undoable_as_a_batch(self):
        first = self.app.add("A")
        self.app.add("B")
        third = self.app.add("C")
        self.app.toggle(first["id"])
        self.app.toggle(third["id"])
        self.app.clear_done()

        self.assertTrue(self.app.undo_delete())

        self.assertEqual([todo["text"] for todo in store.load_todos(self.dir)], ["A", "B", "C"])


class UndoTests(PluginTestCase):
    def test_undo_puts_the_todo_back_at_its_index(self):
        self.app.add("A")
        middle = self.app.add("B")
        self.app.add("C")
        self.app.delete(middle["id"])

        self.assertTrue(self.app.undo_delete())

        self.assertEqual([todo["text"] for todo in store.load_todos(self.dir)], ["A", "B", "C"])

    def test_only_the_latest_delete_can_be_undone(self):
        self.app.add("A")
        second = self.app.add("B")
        third = self.app.add("C")
        self.app.add("D")
        self.app.delete(second["id"])
        self.app.delete(third["id"])

        self.assertTrue(self.app.undo_delete())

        self.assertEqual([todo["text"] for todo in store.load_todos(self.dir)], ["A", "C", "D"])

    def test_undo_does_not_survive_a_restart(self):
        todo = self.app.add("要删的")
        self.app.delete(todo["id"])

        fresh = main.App(str(self.dir))
        fresh.load(str(self.dir))

        self.assertFalse(fresh.undo_delete())


class HotkeyToggleTests(PluginTestCase):
    def test_hotkey_hides_a_visible_note(self):
        with mock.patch("main.sticky_window.is_visible", return_value=True), \
                mock.patch.object(self.app, "hide_sticky") as hide, \
                mock.patch.object(self.app, "show_sticky") as show:
            main.on_hotkey("Ctrl+Alt+T", None)

        hide.assert_called_once()
        show.assert_not_called()

    def test_hotkey_shows_and_focuses_a_hidden_note(self):
        with mock.patch("main.sticky_window.is_visible", return_value=False), \
                mock.patch.object(self.app, "hide_sticky") as hide, \
                mock.patch.object(self.app, "show_sticky") as show:
            main.on_hotkey("Ctrl+Alt+T", None)

        show.assert_called_once_with(focus=True)
        hide.assert_not_called()


class ViewConfigTests(PluginTestCase):
    def test_set_view_persists_to_config(self):
        self.assertEqual(self.app.set_view("month"), "month")

        self.assertEqual(store.load_config(self.dir)["view"], "month")

    def test_invalid_view_falls_back_to_list(self):
        self.assertEqual(self.app.set_view("year"), "list")
        self.assertEqual(store.load_config(self.dir)["view"], "list")


class LazyTkTests(unittest.TestCase):
    def test_importing_main_does_not_load_tkinter(self):
        code = "import sys, main; print('tkinter' in sys.modules)"
        proc = subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "False")


class ManifestTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "plugin.json"), encoding="utf-8") as handle:
            self.manifest = json.load(handle)

    def test_identity(self):
        self.assertEqual(self.manifest["id"], "me.todo")
        self.assertEqual(self.manifest["id"], os.path.basename(HERE))
        self.assertEqual(self.manifest["version"], "3.4.2")
        self.assertEqual(self.manifest["author"], "888bik")
        self.assertEqual(self.manifest["license"], "MIT")
        self.assertEqual(self.manifest["icon"], "icon.png")

    def test_required_manifest_keys(self):
        for key in ("manifest", "id", "name", "version", "commands"):
            self.assertIn(key, self.manifest)
        self.assertEqual(self.manifest["manifest"], 1)

    def test_schema_patterns(self):
        self.assertRegex(self.manifest["id"], r"^[a-z0-9_-]([a-z0-9._-]*[a-z0-9_-])?$")
        self.assertRegex(self.manifest["version"], r"^\d+(\.\d+)*([-+][0-9A-Za-z.-]+)?$")
        for command in self.manifest["commands"]:
            self.assertNotRegex(command["keyword"], r"[\s\"]")

    def test_runtime(self):
        runtime = self.manifest["runtime"]
        self.assertEqual(runtime["mode"], "resident")
        self.assertEqual(runtime["program"], "python")
        self.assertEqual(runtime["script"], "main.py")
        self.assertEqual(runtime["start"], "eager")
        self.assertEqual(runtime["idleExitSec"], 0)
        self.assertEqual(runtime["timeoutMs"], 10000)
        self.assertEqual(runtime["debounceMs"], 300)

    def test_only_ui_and_notifications(self):
        self.assertEqual(sorted(self.manifest["permissions"]), ["notifications", "ui"])

    def test_commands_and_tray_menu(self):
        self.assertEqual([command["keyword"] for command in self.manifest["commands"]], ["db", "todo"])
        self.assertEqual(self.manifest["trayMenu"], "待办")

    def test_settings_match_the_spec(self):
        settings = {setting["key"]: setting for setting in self.manifest["settings"]}
        self.assertEqual(sorted(settings), ["hotkey", "reminder_sound", "show_sticky", "transparency"])
        self.assertEqual(settings["show_sticky"]["type"], "bool")
        self.assertTrue(settings["show_sticky"]["default"])
        self.assertEqual(settings["transparency"]["type"], "number")
        self.assertEqual(settings["transparency"]["default"], 95)
        self.assertEqual(settings["hotkey"]["type"], "hotkey")
        self.assertEqual(settings["hotkey"]["default"], "Ctrl+Alt+T")
        self.assertEqual(settings["reminder_sound"]["type"], "bool")
        self.assertTrue(settings["reminder_sound"]["default"])


class TagTests(PluginTestCase):
    def test_hash_tags_are_extracted(self):
        todo = self.app.add("写周报 #工作 #学习 #工作")

        self.assertEqual(todo["text"], "写周报")
        self.assertEqual(todo["tags"], ["工作", "学习"])

    def test_tags_only_add_is_rejected(self):
        self.assertIsNone(self.app.add("#工作 #学习"))
        self.assertEqual(self.app.list(), [])

    def test_tags_are_capped_at_five(self):
        todo = self.app.add("事 #a #b #c #d #e #f")
        self.assertEqual(todo["tags"], ["a", "b", "c", "d", "e"])


class CompletionTests(PluginTestCase):
    def test_completed_at_is_written_and_cleared(self):
        todo = self.app.add("做完")
        self.app.toggle(todo["id"])

        self.assertIsNotNone(store.load_todos(self.dir)[0]["completed_at"])

        self.app.toggle(todo["id"])
        self.assertIsNone(store.load_todos(self.dir)[0]["completed_at"])


class RepeatSpawnTests(PluginTestCase):
    def test_early_completion_keeps_the_future_anchor(self):
        now = datetime.now().astimezone()
        anchor = (now + timedelta(hours=2)).replace(second=0, microsecond=0)
        todo = self.app.add("每天的事")
        self.app.set_remind(todo["id"], anchor.isoformat(timespec="seconds"), {"type": "daily"})

        self.app.toggle(todo["id"])

        spawned = [item for item in store.load_todos(self.dir) if not item["done"]]
        self.assertEqual(len(spawned), 1)
        self.assertEqual(
            datetime.fromisoformat(spawned[0]["remind_at"]).replace(microsecond=0),
            anchor,
        )

    def test_late_completion_schedules_the_next_day(self):
        now = datetime.now().astimezone()
        todo = self.app.add("每天的事")
        self.app.set_remind(
            todo["id"], (now - timedelta(hours=3)).isoformat(timespec="seconds"),
            {"type": "daily"},
        )

        self.app.toggle(todo["id"])

        spawned = [item for item in store.load_todos(self.dir) if not item["done"]][0]
        self.assertGreater(datetime.fromisoformat(spawned["remind_at"]), now)
        self.assertEqual(spawned["repeat"], {"type": "daily"})
        self.assertEqual(spawned["repeat_base"], spawned["remind_at"])

    def test_missed_days_are_not_replayed(self):
        now = datetime.now().astimezone()
        old = (now - timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0)
        todo = self.app.add("错过三天")
        self.app.set_remind(todo["id"], old.isoformat(timespec="seconds"), {"type": "daily"})

        self.app.toggle(todo["id"])

        spawned = [item for item in store.load_todos(self.dir) if not item["done"]][0]
        following = datetime.fromisoformat(spawned["remind_at"])
        self.assertGreater(following, now)
        self.assertEqual(following.hour, 9)
        expected = now.date() if now.hour < 9 else now.date() + timedelta(days=1)
        self.assertEqual(following.date(), expected)

    def test_invalid_repeat_does_not_spawn(self):
        now = datetime.now().astimezone()
        todo = self.app.add("坏重复")
        self.app.set_remind(
            todo["id"], (now + timedelta(hours=1)).isoformat(timespec="seconds"),
            {"type": "yearly"},
        )

        self.app.toggle(todo["id"])

        self.assertEqual(len(store.load_todos(self.dir)), 1)

    def test_uncompleting_keeps_the_spawned_instance(self):
        now = datetime.now().astimezone()
        todo = self.app.add("每天")
        self.app.set_remind(
            todo["id"], (now + timedelta(hours=1)).isoformat(timespec="seconds"),
            {"type": "daily"},
        )
        self.app.toggle(todo["id"])
        self.app.toggle(todo["id"])

        self.assertEqual(len(store.load_todos(self.dir)), 2)

    def test_finished_repeat_leaves_later_calendar_days_to_the_new_instance(self):
        today = date.today()
        anchor = datetime.combine(today - timedelta(days=2), datetime.min.time()).replace(
            hour=9, minute=0
        ).astimezone()
        todo = self.app.add("每天的事")
        self.app.set_remind(
            todo["id"], anchor.isoformat(timespec="seconds"), {"type": "daily"}
        )

        self.app.toggle(todo["id"])

        loaded = store.load_todos(self.dir)
        old = next(item for item in loaded if item["done"])
        new = next(item for item in loaded if not item["done"])

        next_week = today + timedelta(days=7)
        self.assertEqual(store.occurrences(old, next_week, next_week + timedelta(days=6)), [])
        self.assertIn(today, store.occurrences(old, today, today))
        self.assertTrue(store.occurrences(new, next_week, next_week + timedelta(days=6)))
        self.assertEqual(old["repeat"], {"type": "daily"})
        self.assertEqual(old["repeat_base"], anchor.isoformat(timespec="seconds"))

    def test_due_date_shifts_by_the_same_offset(self):
        now = datetime.now().astimezone()
        remind = (now + timedelta(hours=1)).replace(second=0, microsecond=0)
        todo = self.app.add("带截止")
        self.app.set_remind(todo["id"], remind.isoformat(timespec="seconds"), {"type": "daily"})
        self.app.set_due(todo["id"], (remind + timedelta(hours=8)).isoformat(timespec="seconds"))

        self.app.toggle(todo["id"])

        spawned = [item for item in store.load_todos(self.dir) if not item["done"]][0]
        gap = datetime.fromisoformat(spawned["due_at"]) - datetime.fromisoformat(spawned["remind_at"])
        self.assertEqual(gap, timedelta(hours=8))


class PrioritySortTests(PluginTestCase):
    def test_pinned_then_priority_then_due(self):
        low = self.app.add("低")
        high = self.app.add("高")
        pin = self.app.add("置顶")
        due = self.app.add("有截止")
        self.app.update_details(low["id"], priority=1)
        self.app.update_details(high["id"], priority=3)
        self.app.update_details(pin["id"], priority=0, pinned=True)
        self.app.set_due(due["id"], "2026-11-12T09:00:00+08:00")

        self.assertEqual(
            [todo["text"] for todo in self.app.list()],
            ["置顶", "高", "低", "有截止"],
        )

    def test_subtasks_follow_their_parent(self):
        parent = self.app.add("父")
        child_low = self.app.add("子低", parent_id=parent["id"])
        child_high = self.app.add("子高", parent_id=parent["id"])
        other = self.app.add("别的")
        self.app.update_details(child_low["id"], priority=1)
        self.app.update_details(child_high["id"], priority=3)

        texts = [todo["text"] for todo in self.app.list()]

        self.assertEqual(texts, ["父", "子高", "子低", "别的"])


class SubtaskTests(PluginTestCase):
    def test_deleting_a_parent_removes_its_children_and_undo_restores_all(self):
        parent = self.app.add("父")
        self.app.add("子一", parent_id=parent["id"])
        self.app.add("子二", parent_id=parent["id"])
        self.app.add("别的")

        self.assertTrue(self.app.delete(parent["id"]))
        self.assertEqual([todo["text"] for todo in store.load_todos(self.dir)], ["别的"])

        self.assertTrue(self.app.undo_delete())
        self.assertEqual(
            sorted(todo["text"] for todo in store.load_todos(self.dir)),
            ["别的", "子一", "子二", "父"],
        )

    def test_clear_done_also_removes_children(self):
        parent = self.app.add("父")
        self.app.add("子", parent_id=parent["id"])
        self.app.toggle(parent["id"])

        self.assertEqual(self.app.clear_done(), 2)
        self.assertEqual(store.load_todos(self.dir), [])

    def test_checking_parent_completes_all_children(self):
        parent = self.app.add("父")
        first = self.app.add("子一", parent_id=parent["id"])
        second = self.app.add("子二", parent_id=parent["id"])

        self.app.toggle(parent["id"])

        loaded = {todo["id"]: todo for todo in store.load_todos(self.dir)}
        self.assertTrue(loaded[parent["id"]]["done"])
        self.assertTrue(loaded[first["id"]]["done"])
        self.assertTrue(loaded[second["id"]]["done"])
        self.assertIsNotNone(loaded[first["id"]]["completed_at"])
        self.assertIsNotNone(loaded[second["id"]]["completed_at"])
        self.assertEqual(
            loaded[first["id"]]["completed_at"], loaded[second["id"]]["completed_at"]
        )

    def test_unchecking_parent_keeps_the_children_as_they_are(self):
        parent = self.app.add("父")
        reopened = self.app.add("子一", parent_id=parent["id"])
        finished = self.app.add("子二", parent_id=parent["id"])
        self.app.toggle(parent["id"])          # 勾父：全部完成
        self.app.toggle(reopened["id"])        # 单独把一个子任务重新打开
        finished_stamp = {todo["id"]: todo for todo in store.load_todos(self.dir)}[finished["id"]]["completed_at"]

        self.app.toggle(parent["id"])          # 取消父：只打开父任务

        loaded = {todo["id"]: todo for todo in store.load_todos(self.dir)}
        self.assertFalse(loaded[parent["id"]]["done"])
        self.assertFalse(loaded[reopened["id"]]["done"])
        self.assertIsNone(loaded[reopened["id"]]["completed_at"])
        self.assertTrue(loaded[finished["id"]]["done"])
        self.assertEqual(loaded[finished["id"]]["completed_at"], finished_stamp)

    def test_checking_a_child_leaves_the_parent_open(self):
        parent = self.app.add("父")
        child = self.app.add("子", parent_id=parent["id"])

        self.app.toggle(child["id"])

        loaded = {todo["id"]: todo for todo in store.load_todos(self.dir)}
        self.assertFalse(loaded[parent["id"]]["done"])
        self.assertTrue(loaded[child["id"]]["done"])

    def test_search_subtitle_marks_the_parent(self):
        parent = self.app.add("买菜")
        self.app.add("买鸡蛋", parent_id=parent["id"])

        items = main.on_query(self.query("鸡蛋"), None)
        child = [item for item in items if item["id"].startswith("toggle-")][0]

        self.assertTrue(child["subtitle"].startswith("所属：买菜 · "))


class DetailTests(PluginTestCase):
    def test_update_details_persists_four_fields(self):
        todo = self.app.add("详情")

        self.assertTrue(
            self.app.update_details(
                todo["id"], priority=2, pinned=True, tags=["工作", "工作"], note="备注内容"
            )
        )

        loaded = store.load_todos(self.dir)[0]
        self.assertEqual(loaded["priority"], 2)
        self.assertTrue(loaded["pinned"])
        self.assertEqual(loaded["tags"], ["工作"])
        self.assertEqual(loaded["note"], "备注内容")

    def test_set_due_and_snooze_keep_repeat_base(self):
        todo = self.app.add("提醒")
        base = "2026-11-12T09:00:00+08:00"
        self.app.set_remind(todo["id"], base, {"type": "daily"})

        self.app.set_due(todo["id"], "2026-11-12T18:00:00+08:00")
        self.app.snooze(todo["id"], "2026-11-12T09:10:00+08:00")

        loaded = store.load_todos(self.dir)[0]
        self.assertEqual(loaded["due_at"], "2026-11-12T18:00:00+08:00")
        self.assertEqual(loaded["remind_at"], "2026-11-12T09:10:00+08:00")
        self.assertEqual(loaded["repeat_base"], base)


class SearchDetailTests(PluginTestCase):
    def test_search_subtitle_shows_due_and_remind_when_different(self):
        todo = self.app.add("交房租")
        self.app.set_due(todo["id"], "2026-11-12T18:00:00+08:00")
        self.app.set_remind(todo["id"], "2026-11-12T09:00:00+08:00")

        items = main.on_query(self.query("房租"), None)
        toggle = [item for item in items if item["id"].startswith("toggle-")][0]

        self.assertIn("截止 2026-11-12 18:00", toggle["subtitle"])
        self.assertIn("提醒 2026-11-12 09:00", toggle["subtitle"])

    def test_search_by_tag(self):
        self.app.add("写周报 #工作")

        items = main.on_query(self.query("#工作"), None)

        self.assertIn("写周报", [item["title"] for item in items])


class SoundTests(PluginTestCase):
    def test_sound_plays_once_when_enabled(self):
        todo = self.app.add("响一下")
        self.app.set_remind(
            todo["id"],
            (datetime.now().astimezone() - timedelta(minutes=1)).isoformat(timespec="seconds"),
        )
        self.app.settings = {"reminder_sound": True}

        with mock.patch("main._play_reminder_sound") as sound, \
                mock.patch("main.sticky_window.refresh"):
            main.fire_reminders(self.app, mock.Mock())

        sound.assert_called_once()

    def test_sound_is_skipped_when_disabled(self):
        todo = self.app.add("别响")
        self.app.set_remind(
            todo["id"],
            (datetime.now().astimezone() - timedelta(minutes=1)).isoformat(timespec="seconds"),
        )
        self.app.settings = {"reminder_sound": False}

        with mock.patch("main._play_reminder_sound") as sound, \
                mock.patch("main.sticky_window.refresh"):
            main.fire_reminders(self.app, mock.Mock())

        sound.assert_not_called()

    def test_sound_is_a_noop_off_windows(self):
        with mock.patch("main.os.name", "posix"):
            self.assertFalse(main._play_reminder_sound())


class LockMenuTests(PluginTestCase):
    def test_lock_title_follows_the_config(self):
        self.app.set_locked(True)

        items = {item["id"]: item for item in main.on_tray_menu(None)}

        self.assertEqual(items["lock"]["title"], "解锁便签")
        self.assertTrue(items["lock"]["checked"])

    def test_locked_roundtrips_through_config(self):
        self.app.set_locked(True)
        self.assertTrue(store.load_config(self.dir)["locked"])

        self.app.set_locked(False)
        self.assertFalse(store.load_config(self.dir)["locked"])


if __name__ == "__main__":
    unittest.main()
