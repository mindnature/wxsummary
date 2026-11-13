"""Regression checks for stricter quote matching and on-demand semantics."""
from __future__ import annotations

import unittest

from wechat_summary.semantic import validate_semantic


class EvidenceRegressionTests(unittest.TestCase):
    def setUp(self):
        self.material = {
            "meta": {"title": "测试群日报", "window": {"period": "daily"},
                     "chats": [{"id": "g@chatroom", "name": "测试群"}],
                     "multi": False, "me_configured": False},
            "stats": {"links": [], "daily": [{"date": "2026-11-13"}], "empty": False},
        }
        self.evidence = {"m1": {"chat_id": "g@chatroom", "text": "先讨论，再决定",
                                 "sender": "甲", "time": "2026-11-13T10:00:00+08:00",
                                 "chat_name": "测试群"}}
        self.semantic = {
            "version": 1, "one_liner": "今天讨论了安排，暂无最终决定。",
            "topics": [{"title": "安排仍需讨论", "summary": "目前未形成正式决定。",
                        "quotes": [{"msg_id": "m1", "text": "先讨论"}], "evidence": ["m1"]}],
            "insights": [], "follow_ups": [], "for_me": [], "links": [],
        }

    def test_valid_quote(self):
        errors, _ = validate_semantic(self.semantic, self.material, self.evidence)
        self.assertEqual(errors, [])

    def test_empty_quote_rejected(self):
        self.semantic["topics"][0]["quotes"][0]["text"] = ""
        errors, _ = validate_semantic(self.semantic, self.material, self.evidence)
        self.assertTrue(any("引文不能为空" in error for error in errors))

    def test_noncontiguous_quote_rejected(self):
        self.semantic["topics"][0]["quotes"][0]["text"] = "先讨论决定"
        errors, _ = validate_semantic(self.semantic, self.material, self.evidence)
        self.assertTrue(any("连续片段" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
