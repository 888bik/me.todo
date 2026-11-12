import unittest
from datetime import date, datetime

import quickparse

NOW = datetime(2026, 11, 11, 10, 0).astimezone()  # Wednesday


def parsed(text):
    return quickparse.parse_quick_input(text, NOW)


def stamp(value):
    return datetime.fromisoformat(value)


class QuickParseRepeatTests(unittest.TestCase):
    def test_daily(self):
        result = parsed("每天开会")

        self.assertEqual(result["text"], "开会")
        self.assertEqual(result["repeat"], {"type": "daily"})
        self.assertEqual(stamp(result["remind_at"]).date(), date(2026, 11, 11))
        self.assertEqual(stamp(result["remind_at"]).strftime("%H:%M"), "09:00")

    def test_workdays(self):
        for text in ("工作日开会", "每个工作日开会"):
            result = parsed(text)
            self.assertEqual(result["text"], "开会")
            self.assertEqual(result["repeat"], {"type": "weekdays"})

    def test_weekly_with_a_weekday(self):
        result = parsed("每周五开会")

        self.assertEqual(result["text"], "开会")
        self.assertEqual(result["repeat"], {"type": "weekly", "days": [4]})
        self.assertEqual(stamp(result["remind_at"]).date(), date(2026, 11, 13))

    def test_weekly_without_a_weekday_uses_today(self):
        result = parsed("每周开会")

        self.assertEqual(result["text"], "开会")
        self.assertEqual(result["repeat"], {"type": "weekly", "days": [2]})
        self.assertEqual(stamp(result["remind_at"]).date(), date(2026, 11, 11))

    def test_monthly_with_a_day(self):
        result = parsed("每月1号交房租")

        self.assertEqual(result["text"], "交房租")
        self.assertEqual(result["repeat"], {"type": "monthly", "day": 1})
        self.assertEqual(stamp(result["remind_at"]).date(), date(2026, 12, 1))

    def test_monthly_without_a_day_uses_today(self):
        result = parsed("每月开会")

        self.assertEqual(result["text"], "开会")
        self.assertEqual(result["repeat"], {"type": "monthly", "day": 11})
        self.assertEqual(stamp(result["remind_at"]).date(), date(2026, 11, 11))

    def test_repeat_with_an_explicit_date(self):
        result = parsed("每天明天下午3点开会")

        self.assertEqual(result["text"], "开会")
        self.assertEqual(result["repeat"], {"type": "daily"})
        self.assertEqual(stamp(result["remind_at"]).date(), date(2026, 11, 12))
        self.assertEqual(stamp(result["remind_at"]).strftime("%H:%M"), "15:00")

    def test_repeat_marker_is_removed_from_the_title(self):
        for text in ("每天开会", "工作日开会", "每周五开会", "每月1号交房租"):
            result = parsed(text)
            self.assertEqual(result["text"], "开会" if "交房租" not in text else "交房租")


class QuickParseBarePeriodTests(unittest.TestCase):
    def test_bare_period_after_a_date(self):
        result = parsed("明天下午开会")

        self.assertEqual(result["text"], "开会")
        self.assertEqual(stamp(result["remind_at"]), datetime(2026, 11, 12, 15, 0).astimezone())
        self.assertEqual(stamp(result["due_at"]), datetime(2026, 11, 12, 15, 0).astimezone())

    def test_bare_period_at_the_start(self):
        result = parsed("下午开会")

        self.assertEqual(result["text"], "开会")
        self.assertEqual(stamp(result["remind_at"]).date(), date(2026, 11, 11))
        self.assertEqual(stamp(result["remind_at"]).strftime("%H:%M"), "15:00")

    def test_morning_period(self):
        result = parsed("明天早上开会")

        self.assertEqual(stamp(result["remind_at"]).date(), date(2026, 11, 12))
        self.assertEqual(stamp(result["remind_at"]).strftime("%H:%M"), "09:00")

    def test_evening_period(self):
        result = parsed("晚上看电影")

        self.assertEqual(result["text"], "看电影")
        self.assertEqual(stamp(result["remind_at"]).strftime("%H:%M"), "19:00")

    def test_bare_period_at_the_end(self):
        result = parsed("开会下午")

        self.assertEqual(result["text"], "开会")
        self.assertEqual(stamp(result["remind_at"]).strftime("%H:%M"), "15:00")

    def test_period_inside_a_word_is_not_split(self):
        self.assertIsNone(parsed("开会下午茶"))

    def test_explicit_hour_wins_over_the_period_default(self):
        result = parsed("下午3点开会")

        self.assertEqual(stamp(result["remind_at"]).strftime("%H:%M"), "15:00")


class QuickParseExampleTests(unittest.TestCase):
    def test_tomorrow_afternoon(self):
        result = parsed("明天下午3点开会")

        self.assertEqual(result["text"], "开会")
        self.assertEqual(stamp(result["remind_at"]), datetime(2026, 11, 12, 15, 0).astimezone())
        self.assertEqual(stamp(result["due_at"]), datetime(2026, 11, 12, 15, 0).astimezone())

    def test_friday_with_a_tag(self):
        result = parsed("周五交项目报告 #工作")

        self.assertEqual(result["text"], "交项目报告")
        self.assertEqual(stamp(result["due_at"]).date(), date(2026, 11, 13))
        self.assertEqual(stamp(result["remind_at"]).time().strftime("%H:%M"), "09:00")
        self.assertEqual(result["tags"], ["工作"])

    def test_this_month_day(self):
        result = parsed("这个月20号缴费")

        self.assertEqual(result["text"], "缴费")
        self.assertEqual(stamp(result["due_at"]).date(), date(2026, 11, 20))


class QuickParseDateTests(unittest.TestCase):
    def assert_day(self, text, expected, time_text="09:00"):
        result = parsed(text)
        self.assertIsNotNone(result, text)
        self.assertEqual(stamp(result["remind_at"]).date(), expected, text)
        self.assertEqual(stamp(result["remind_at"]).strftime("%H:%M"), time_text, text)

    def test_relative_days(self):
        self.assert_day("今天开会", date(2026, 11, 11))
        self.assert_day("明天开会", date(2026, 11, 12))
        self.assert_day("后天开会", date(2026, 11, 13))
        self.assert_day("大后天开会", date(2026, 11, 14))
        self.assert_day("3天后开会", date(2026, 11, 14))

    def test_chinese_number_days(self):
        self.assert_day("三天后开会", date(2026, 11, 14))
        self.assert_day("两天后开会", date(2026, 11, 13))
        self.assert_day("十天后开会", date(2026, 11, 21))

    def test_composite_chinese_numbers_are_not_supported(self):
        self.assertIsNone(parsed("十一天后开会"))

    def test_weekday_forms(self):
        self.assert_day("周一开会", date(2026, 11, 16))
        self.assert_day("周五开会", date(2026, 11, 13))
        self.assert_day("星期天开会", date(2026, 11, 15))
        self.assert_day("本周五开会", date(2026, 11, 13))
        self.assert_day("下周一开会", date(2026, 11, 16))
        self.assert_day("下周开会", date(2026, 11, 16))

    def test_today_counts_for_its_own_weekday(self):
        self.assert_day("周三开会", date(2026, 11, 11))
        self.assert_day("本周三开会", date(2026, 11, 11))

    def test_passed_weekday_moves_to_next_week(self):
        self.assert_day("本周一开会", date(2026, 11, 16))

    def test_indicator_words_before_a_weekday(self):
        self.assert_day("这个周五开会", date(2026, 11, 13))
        self.assert_day("这周五开会", date(2026, 11, 13))
        self.assert_day("那个周五开会", date(2026, 11, 13))
        self.assert_day("下个周五开会", date(2026, 11, 20))
        for text in ("这个周五开会", "这周五开会", "那个周五开会", "下个周五开会"):
            self.assertNotIn("这", parsed(text)["text"])
            self.assertNotIn("那", parsed(text)["text"])
            self.assertNotIn("下个", parsed(text)["text"])

    def test_day_of_month(self):
        self.assert_day("20号开会", date(2026, 11, 20))
        self.assert_day("5号开会", date(2026, 12, 5))
        self.assert_day("这个月20号开会", date(2026, 11, 20))
        self.assert_day("下个月20号开会", date(2026, 12, 20))

    def test_next_month_without_day_clamps(self):
        now = datetime(2026, 1, 31, 10, 0).astimezone()
        result = quickparse.parse_quick_input("下个月开会", now)

        self.assertEqual(datetime.fromisoformat(result["remind_at"]).date(), date(2026, 2, 28))

    def test_month_and_day(self):
        self.assert_day("12月1号开会", date(2026, 12, 1))
        self.assert_day("1月5号开会", date(2027, 1, 5))


class QuickParseTimeTests(unittest.TestCase):
    def assert_time(self, text, expected):
        result = parsed(text)
        self.assertIsNotNone(result, text)
        self.assertEqual(stamp(result["remind_at"]).strftime("%H:%M"), expected, text)

    def test_period_words(self):
        self.assert_time("上午9点开会", "09:00")
        self.assert_time("上午12点开会", "12:00")
        self.assert_time("中午12点开会", "12:00")
        self.assert_time("中午1点开会", "13:00")
        self.assert_time("下午3点开会", "15:00")
        self.assert_time("晚上12点开会", "12:00")
        self.assert_time("凌晨3点开会", "03:00")
        self.assert_time("半夜3点开会", "03:00")

    def test_literal_hours_and_minutes(self):
        self.assert_time("3点开会", "03:00")
        self.assert_time("15点开会", "15:00")
        self.assert_time("3点半开会", "03:30")
        self.assert_time("3点20分开会", "03:20")
        self.assert_time("15:30开会", "15:30")
        self.assert_time("15：05开会", "15:05")

    def test_time_only_rolls_to_tomorrow_when_passed(self):
        result = parsed("3点开会")

        self.assertEqual(stamp(result["remind_at"]).date(), date(2026, 11, 12))

    def test_time_only_stays_today_when_still_ahead(self):
        result = parsed("下午3点开会")

        self.assertEqual(stamp(result["remind_at"]).date(), date(2026, 11, 11))

    def test_out_of_range_time_is_not_recognized(self):
        self.assertIsNone(parsed("25点开会"))
        self.assertIsNone(parsed("10:75开会"))


class QuickParseMarkerTests(unittest.TestCase):
    def test_tags_are_stripped_and_kept(self):
        result = parsed("#工作 #学习 写报告")

        self.assertEqual(result["text"], "写报告")
        self.assertEqual(result["tags"], ["工作", "学习"])

    def test_priority_markers(self):
        self.assertEqual(parsed("! 开会")["priority"], 1)
        self.assertEqual(parsed("!! 开会")["priority"], 2)
        self.assertEqual(parsed("!!! 开会")["priority"], 3)
        self.assertEqual(parsed("紧急 开会")["priority"], 3)

    def test_priority_marker_is_stripped(self):
        self.assertEqual(parsed("!! 开会")["text"], "开会")

    def test_priority_markers_attached_to_words(self):
        cases = [
            ("开会!!", 2),
            ("开会!", 1),
            ("明天开会!", 1),
            ("!!开会", 2),
            ("开会 紧急!!", 3),
            ("开会 !!", 2),
            ("开会!!!", 3),
            ("开会!!!!", 3),
        ]
        for text, expected in cases:
            result = parsed(text)
            self.assertIsNotNone(result, text)
            self.assertEqual(result["priority"], expected, text)
            self.assertNotIn("!", result["text"], text)

    def test_attached_marker_keeps_the_date(self):
        result = parsed("明天开会!")

        self.assertEqual(result["text"], "开会")
        self.assertEqual(stamp(result["due_at"]).date(), date(2026, 11, 12))

    def test_full_width_exclamation_is_not_a_marker(self):
        result = parsed("明天 生日快乐！")

        self.assertIsNotNone(result)
        self.assertEqual(result["priority"], 0)
        self.assertEqual(result["text"], "生日快乐！")
        self.assertIn("！", result["text"])

    def test_no_half_width_exclamation_survives_in_any_title(self):
        for text in (
            "开会!!", "开会!", "明天开会!", "!!开会",
            "开会 紧急!!", "开会 !!", "开会!!!", "!!! 写报告",
        ):
            result = parsed(text)
            self.assertNotIn("!", result["text"], text)

    def test_only_tags_or_priority_is_still_a_preview(self):
        result = parsed("#工作 开会")
        self.assertIsNone(result["remind_at"])
        self.assertIsNone(result["due_at"])
        self.assertEqual(result["tags"], ["工作"])

        result = parsed("! 开会")
        self.assertEqual(result["priority"], 1)
        self.assertIsNone(result["remind_at"])

    def test_nothing_recognized_returns_none(self):
        self.assertIsNone(parsed("买20个苹果"))
        self.assertIsNone(parsed("普通待办"))

    def test_empty_title_returns_none(self):
        self.assertIsNone(parsed("明天"))
        self.assertIsNone(parsed("明天 #工作"))

    def test_only_date_sets_reminder_and_deadline(self):
        result = parsed("明天开会")

        self.assertEqual(stamp(result["remind_at"]).strftime("%H:%M"), "09:00")
        self.assertEqual(stamp(result["due_at"]).strftime("%H:%M"), "23:59")


class PreviewFormatTests(unittest.TestCase):
    def test_friendly_dates(self):
        self.assertEqual(
            quickparse.format_preview(datetime(2026, 11, 11, 15, 0).astimezone(), NOW),
            "今天 15:00",
        )
        self.assertEqual(
            quickparse.format_preview(datetime(2026, 11, 12, 9, 0).astimezone(), NOW),
            "明天 09:00",
        )
        self.assertEqual(
            quickparse.format_preview(datetime(2026, 11, 13, 9, 0).astimezone(), NOW),
            "后天 09:00",
        )

    def test_weekday_within_a_week(self):
        self.assertEqual(
            quickparse.format_preview(datetime(2026, 11, 15, 9, 0).astimezone(), NOW),
            "周日 09:00",
        )

    def test_far_date_uses_month_day(self):
        self.assertEqual(
            quickparse.format_preview(datetime(2026, 11, 21, 9, 0).astimezone(), NOW),
            "11-21 09:00",
        )

    def test_none_is_empty(self):
        self.assertEqual(quickparse.format_preview(None, NOW), "")


if __name__ == "__main__":
    unittest.main()
