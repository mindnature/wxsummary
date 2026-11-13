from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wechat_summary.semantic import build_report, validate_semantic  # noqa: E402

EVIDENCE = {
    "m1": {"chat_id": "a@chatroom", "chat_name": "A群", "sender": "阿A", "time": "2026-10-26T09:00:00+08:00", "type": "text", "type_label": "文本", "text": "我们周五发版吧，先灰度 10%", "url": None},
    "m2": {"chat_id": "a@chatroom", "chat_name": "A群", "sender": "老B", "time": "2026-10-26T09:05:00+08:00", "type": "text", "type_label": "文本", "text": "同意，但要先补监控", "url": None},
    "m3": {"chat_id": "b@chatroom", "chat_name": "B群", "sender": "小C", "time": "2026-10-26T10:00:00+08:00", "type": "link", "type_label": "链接", "text": "好文章", "url": "https://example.com/x"},
}


def material(multi=False, period="daily", me=False, empty=False):
    chats = [{"id": "a@chatroom", "name": "A群"}] + ([{"id": "b@chatroom", "name": "B群"}] if multi else [])
    days = [{"date": "2026-10-26", "messages": 3, "senders": 3}]
    if period == "weekly":
        days += [{"date": f"2026-10-{d}", "messages": 0, "senders": 0} for d in range(27, 33) if d <= 31] + [{"date": "2026-11-01", "messages": 0, "senders": 0}]
    return {
        "meta": {"title": "A群｜日报", "window": {"period": period}, "chats": chats, "multi": multi, "me_configured": me},
        "stats": {"links": [{"url": "https://example.com/x", "title": "好文章", "domain": "example.com", "sharers": ["小C"], "first_msg_id": "m3"}],
                  "daily": days, "empty": empty, "speech_count": 3, "sender_count": 3},
    }


GOOD = {
    "version": 1,
    "one_liner": "周五灰度发版定了，前提是先补监控。",
    "topics": [{"title": "周五灰度 10% 发版，先补监控", "summary": "阿A 提议周五发版，老B 同意但要求先补监控。",
                "quotes": [{"msg_id": "m1", "text": "先灰度 10%"}], "evidence": ["m1", "m2"]}],
    "insights": [{"claim": "发版前监控是共识", "stance": "consensus", "evidence": ["m2"]}],
    "follow_ups": [{"title": "补监控", "owner": "老B", "evidence": ["m2"]}],
    "for_me": [],
    "links": [],
}


class ValidateTests(unittest.TestCase):
    def check(self, semantic, **kw):
        return validate_semantic(copy.deepcopy(semantic), material(**kw), EVIDENCE)

    def test_good_passes(self):
        errors, _ = self.check(GOOD)
        self.assertEqual(errors, [])

    def test_missing_one_liner_and_topics(self):
        bad = dict(GOOD, one_liner="", topics=[])
        errors, _ = self.check(bad)
        self.assertTrue(any("one_liner" in e for e in errors))
        self.assertTrue(any("topics" in e for e in errors))

    def test_empty_window_allows_no_topics(self):
        errors, _ = self.check(dict(GOOD, topics=[], insights=[], follow_ups=[]), empty=True)
        self.assertEqual(errors, [])

    def test_unknown_evidence_rejected(self):
        bad = copy.deepcopy(GOOD)
        bad["topics"][0]["evidence"] = ["m1", "m999"]
        errors, _ = self.check(bad)
        self.assertTrue(any("m999" in e for e in errors))

    def test_missing_evidence_rejected(self):
        bad = copy.deepcopy(GOOD)
        del bad["insights"][0]["evidence"]
        errors, _ = self.check(bad)
        self.assertTrue(any("insights[0] 缺少 evidence" in e for e in errors))

    def test_quote_must_be_verbatim(self):
        bad = copy.deepcopy(GOOD)
        bad["topics"][0]["quotes"] = [{"msg_id": "m1", "text": "我们下周发版"}]
        errors, _ = self.check(bad)
        self.assertTrue(any("原文" in e for e in errors))

    def test_chat_id_cross_group_rejected(self):
        bad = copy.deepcopy(GOOD)
        bad["topics"][0]["chat_id"] = "b@chatroom"
        errors, _ = self.check(bad, multi=True)
        self.assertTrue(any("属于其他群" in e for e in errors))

    def test_chat_id_not_in_report(self):
        bad = copy.deepcopy(GOOD)
        bad["topics"][0]["chat_id"] = "zzz@chatroom"
        errors, _ = self.check(bad)
        self.assertTrue(any("不属于本报告" in e for e in errors))

    def test_link_must_come_from_stats(self):
        bad = dict(GOOD, links=[{"url": "https://evil.example/", "note": "x", "evidence": ["m3"]}])
        errors, _ = self.check(bad, multi=True)
        self.assertTrue(any("stats.links" in e for e in errors))
        ok = dict(GOOD, links=[{"url": "https://example.com/x", "note": "值得读", "evidence": ["m3"]}])
        self.assertEqual(self.check(ok, multi=True)[0], [])

    def test_stance_values(self):
        bad = copy.deepcopy(GOOD)
        bad["insights"][0]["stance"] = "maybe"
        errors, _ = self.check(bad)
        self.assertTrue(any("stance" in e for e in errors))

    def test_for_me_requires_identity(self):
        bad = dict(GOOD, for_me=[{"title": "x", "evidence": ["m1"]}])
        self.assertTrue(any("我的身份" in e for e in self.check(bad)[0]))
        self.assertEqual(self.check(bad, me=True)[0], [])

    def test_weekly_requires_daily_in_range(self):
        errors, _ = self.check(GOOD, period="weekly")
        self.assertTrue(any("daily" in e for e in errors))
        ok = dict(GOOD, daily=[{"date": "2026-10-26", "headline": "定了发版", "evidence": ["m1"]}])
        self.assertEqual(self.check(ok, period="weekly")[0], [])
        bad = dict(GOOD, daily=[{"date": "2026-12-01", "headline": "x", "evidence": ["m1"]}])
        self.assertTrue(any("不在报告范围" in e for e in self.check(bad, period="weekly")[0]))


class BuildReportTests(unittest.TestCase):
    def test_backfill(self):
        report = build_report(material(multi=True), copy.deepcopy(GOOD), EVIDENCE)
        topic = report["semantic"]["topics"][0]
        self.assertEqual(topic["chat_id"], "a@chatroom")
        self.assertEqual(topic["chat_name"], "A群")
        self.assertEqual(topic["evidence_refs"][0]["sender"], "阿A")
        self.assertEqual(topic["quotes"][0]["sender"], "阿A")
        self.assertEqual(report["semantic"]["insights"][0]["stance_label"], "共识")
        self.assertEqual(report["semantic_truth"], "not-verified")
        self.assertFalse(report["stats_only"])

    def test_stats_only(self):
        report = build_report(material(), None, EVIDENCE)
        self.assertTrue(report["stats_only"])
        self.assertIn("仅统计版", report["semantic"]["one_liner"])
        self.assertEqual(report["semantic"]["links"][0]["url"], "https://example.com/x")


if __name__ == "__main__":
    unittest.main()
