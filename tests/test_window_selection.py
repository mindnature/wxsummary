from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wechat_summary.errors import WSError  # noqa: E402
from wechat_summary.models import Chat  # noqa: E402
from wechat_summary.selection import resolve_chats, resolve_one  # noqa: E402
from wechat_summary.window import compute_window  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")


def win(period, **kw):
    return compute_window(period, tz=TZ, tz_name="Asia/Shanghai", **kw)


class WindowTests(unittest.TestCase):
    now = datetime(2026, 1, 1, 9, 30, tzinfo=TZ)  # 周四

    def test_daily_default_is_yesterday_across_year(self):
        w = win("daily", now=self.now)
        self.assertEqual(w.since, datetime(2025, 12, 31, tzinfo=TZ))
        self.assertEqual(w.until, datetime(2026, 1, 1, tzinfo=TZ))
        self.assertEqual(w.range_text(), "2025-12-31")
        self.assertEqual(w.slug(), "2025-12-31")

    def test_daily_explicit(self):
        w = win("daily", date_text="2026-10-26", now=self.now)
        self.assertEqual(w.since, datetime(2026, 10, 26, tzinfo=TZ))
        self.assertEqual(len(w.days), 1)

    def test_weekly_default_previous_full_week(self):
        w = win("weekly", now=self.now)
        self.assertEqual(w.since, datetime(2025, 12, 22, tzinfo=TZ))
        self.assertEqual(w.until, datetime(2025, 12, 29, tzinfo=TZ))
        self.assertEqual(len(w.days), 7)

    def test_weekly_sunday_start(self):
        w = win("weekly", week_start="sunday", date_text="2026-10-28")
        self.assertEqual(w.since, datetime(2026, 10, 25, tzinfo=TZ))
        self.assertEqual(w.since.weekday(), 6)

    def test_weekly_explicit_contains_date(self):
        w = win("weekly", date_text="2026-10-28")
        self.assertEqual(w.since, datetime(2026, 10, 26, tzinfo=TZ))
        self.assertEqual(w.slug(), "2026-W44")
        self.assertEqual(w.range_text(), "2026-10-26 至 2026-11-01")

    def test_monthly_default_previous_month_across_year(self):
        w = win("monthly", now=self.now)
        self.assertEqual(w.since, datetime(2025, 12, 1, tzinfo=TZ))
        self.assertEqual(w.until, datetime(2026, 1, 1, tzinfo=TZ))
        self.assertEqual(len(w.days), 31)

    def test_monthly_february(self):
        w = win("monthly", date_text="2028-02-10")
        self.assertEqual(len(w.days), 29)
        self.assertEqual(w.slug(), "2028-02")

    def test_custom(self):
        w = win("custom", since="2026-10-01T08:00", until="2026-10-02T08:00")
        self.assertEqual(w.period, "custom")
        self.assertEqual(w.range_text(), "2026-10-01 08:00 至 2026-10-02 08:00")
        self.assertEqual(len(w.days), 2)

    def test_since_until_forces_custom(self):
        self.assertEqual(win("daily", since="2026-10-01", until="2026-10-03").period, "custom")

    def test_invalid(self):
        with self.assertRaises(WSError):
            win("custom")
        with self.assertRaises(WSError):
            win("daily", since="2026-10-02", until="2026-10-01")
        with self.assertRaises(WSError):
            win("daily", since="2026-10-02")
        with self.assertRaises(WSError):
            win("daily", date_text="20261002x")


class SelectionTests(unittest.TestCase):
    chats = [
        Chat("1@chatroom", "AI 产品群"),
        Chat("2@chatroom", "读书会"),
        Chat("3@chatroom", "读书会"),
        Chat("4@chatroom", "前端交流", remark="FE"),
    ]

    def test_exact_and_id_and_remark(self):
        self.assertEqual(resolve_one("AI 产品群", self.chats).id, "1@chatroom")
        self.assertEqual(resolve_one("3@chatroom", self.chats).id, "3@chatroom")
        self.assertEqual(resolve_one("FE", self.chats).id, "4@chatroom")
        self.assertEqual(resolve_one("ＡＩ 产品群", self.chats).id, "1@chatroom")

    def test_ambiguous(self):
        with self.assertRaises(WSError) as ctx:
            resolve_one("读书会", self.chats)
        self.assertEqual(ctx.exception.code, "chat_ambiguous")
        self.assertEqual(len(ctx.exception.details), 2)

    def test_not_found_with_candidates(self):
        with self.assertRaises(WSError) as ctx:
            resolve_one("产品", self.chats)
        self.assertEqual(ctx.exception.code, "chat_not_found")
        self.assertEqual(ctx.exception.details[0]["id"], "1@chatroom")

    def test_group_set_and_dedupe(self):
        chats, label = resolve_chats(self.chats, names=["1@chatroom"], group_set="mine",
                                     group_sets={"mine": ["AI 产品群", "FE"]})
        self.assertEqual([c.id for c in chats], ["1@chatroom", "4@chatroom"])
        self.assertEqual(label, "mine")

    def test_multi_label_and_errors(self):
        chats, label = resolve_chats(self.chats, names=["AI 产品群", "前端交流"], group_set=None, group_sets={})
        self.assertEqual(label, "AI 产品群等2群")
        with self.assertRaises(WSError):
            resolve_chats(self.chats, names=[], group_set=None, group_sets={})
        with self.assertRaises(WSError) as ctx:
            resolve_chats(self.chats, names=[], group_set="nope", group_sets={})
        self.assertEqual(ctx.exception.code, "group_set_unknown")


if __name__ == "__main__":
    unittest.main()
