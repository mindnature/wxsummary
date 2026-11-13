from __future__ import annotations

import copy
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wechat_summary.render import EXTERNAL_RESOURCE_RE, build_view, render_html, render_markdown  # noqa: E402
from wechat_summary.templating import Loader, Template  # noqa: E402
from wechat_summary.themes import list_themes, resolve_theme_names  # noqa: E402

THEMES = ["claude", "doodle", "newspaper", "terminal", "swiss", "bento", "brutal", "ink"]


def _stats(empty=False, days=1):
    daily = [{"date": f"2026-10-{19 + i:02d}", "weekday": "一二三四五六日"[i % 7], "messages": 0 if empty else (i * 3 + 2) % 9, "senders": 2}
             for i in range(days)]
    return {
        "message_count": 0 if empty else 12, "speech_count": 0 if empty else 10, "sender_count": 0 if empty else 3,
        "text_chars": 100, "link_count": 0 if empty else 1,
        "top_senders": [] if empty else [{"name": "阿A", "messages": 6, "share": 0.6, "chars": 60},
                                         {"name": "<b>老B</b>", "messages": 4, "share": 0.4, "chars": 40}],
        "hourly": [0] * 24 if empty else [0] * 9 + [5, 3, 2] + [0] * 12,
        "peak_hour": None if empty else 9,
        "daily": daily, "peak_day": None,
        "types": [] if empty else [{"type": "text", "label": "文本", "count": 10}],
        "links": [] if empty else [{"url": "https://example.com/a?x=1&y=2", "title": "文章", "domain": "example.com", "sharers": ["阿A"], "first_msg_id": "m1"}],
        "most_quoted": [], "empty": empty,
    }


def make_report(period="daily", multi=False, empty=False, stats_only=False):
    days = {"daily": 1, "weekly": 7, "monthly": 31}[period]
    chats = [{"id": "a@chatroom", "name": "A群"}] + ([{"id": "b@chatroom", "name": "B群"}] if multi else [])
    ref = {"id": "m1", "time": "2026-10-19T09:00:00+08:00", "sender": "阿A", "text": "原文 <script>alert(1)</script>", "chat_name": "A群", "chat_id": "a@chatroom"}
    semantic = {
        "title": "A群｜测试", "one_liner": "一句话 <i>总结</i>", "mood": "务实", "editor_note": "编者按",
        "topics": [] if empty else [
            {"title": "话题一", "summary": "摘要", "points": ["点1"], "quotes": [{"msg_id": "m1", "text": "原话", "sender": "阿A"}],
             "people": ["阿A"], "chat_id": "a@chatroom", "chat_name": "A群", "evidence": ["m1"], "evidence_refs": [ref]},
            {"title": "话题二", "summary": "摘要二", "evidence": ["m1"], "evidence_refs": [ref], "chat_name": "B群" if multi else "A群"},
        ],
        "insights": [] if empty else [{"claim": "观点", "stance": "debate", "stance_label": "分歧", "detail": "细节", "evidence": ["m1"], "evidence_refs": [ref]}],
        "follow_ups": [] if empty else [{"title": "跟进", "owner": "老B", "due": "周五", "evidence": ["m1"], "evidence_refs": [ref]}],
        "for_me": [] if empty else [{"title": "找我", "action": "回复", "evidence": ["m1"], "evidence_refs": [ref]}],
        "links": [] if empty else [{"url": "https://example.com/a?x=1&y=2", "title": "文章", "note": "值得读", "domain": "example.com", "evidence": ["m1"]}],
        "daily": [] if (empty or period == "daily") else [{"date": "2026-10-19", "headline": "第一天", "points": ["p"], "evidence": ["m1"]}],
    }
    if stats_only:
        semantic.update(topics=[], insights=[], follow_ups=[], for_me=[], daily=[], one_liner="仅统计版")
    groups = [{"chat_id": "a@chatroom", "chat_name": "A群", "messages": 7, "senders": 2, "links": 1, "top_senders": [], "peak_hour": 9},
              {"chat_id": "b@chatroom", "chat_name": "B群", "messages": 3, "senders": 1, "links": 0, "top_senders": [], "peak_hour": 10}] if multi else None
    return {
        "meta": {"title": "A群｜日报", "chats": chats, "multi": multi, "warnings": ["注意 <x>"], "source_latest": "2026-10-20T10:00:00+08:00",
                 "window": {"period": period, "label": {"daily": "日报", "weekly": "周报", "monthly": "月报"}[period],
                            "since": "2026-10-19T00:00:00+08:00", "until": "2026-10-20T00:00:00+08:00", "timezone": "Asia/Shanghai",
                            "range_text": "2026-10-19", "slug": "2026-10-19"}},
        "stats": _stats(empty, days), "groups": groups, "semantic": semantic, "stats_only": stats_only, "semantic_truth": "not-verified",
    }


SCENARIOS = {
    "daily": dict(period="daily"),
    "weekly": dict(period="weekly"),
    "monthly_multi": dict(period="monthly", multi=True),
    "empty": dict(period="daily", empty=True),
    "stats_only": dict(period="weekly", stats_only=True),
}


class ThemeRenderTests(unittest.TestCase):
    def test_all_themes_registered(self):
        names = [t["name"] for t in list_themes()]
        for t in THEMES:
            self.assertIn(t, names)
        self.assertEqual(resolve_theme_names(["all"], []), names)

    def test_smoke_all_themes_all_scenarios(self):
        for theme in THEMES:
            for name, kw in SCENARIOS.items():
                with self.subTest(theme=theme, scenario=name):
                    html_text = render_html(make_report(**kw), theme)
                    self.assertTrue(html_text.lstrip().lower().startswith("<!doctype html>"))
                    self.assertIn("</html>", html_text)
                    self.assertIsNone(EXTERNAL_RESOURCE_RE.search(html_text), "不得加载外部资源")
                    # 用户内容必须被转义
                    self.assertNotIn("<script>alert(1)</script>", html_text)
                    self.assertNotIn("<i>总结</i>", html_text)
                    self.assertIn("not-verified", html_text)
                    if name == "stats_only":
                        self.assertIn("仅统计版", html_text)
                    if not kw.get("empty") and not kw.get("stats_only"):
                        self.assertIn("话题一", html_text)
                    # 模板语法不能泄漏
                    self.assertIsNone(re.search(r"{[{%]", html_text.split("<style>")[0] + html_text.split("</style>")[-1]))

    def test_markdown_scenarios(self):
        for name, kw in SCENARIOS.items():
            with self.subTest(scenario=name):
                md = render_markdown(make_report(**kw))
                self.assertTrue(md.startswith("# "))
                self.assertIn("semantic_truth: not-verified", md)
                self.assertNotIn("<script>", md)
        md = render_markdown(make_report(period="weekly", multi=True))
        self.assertIn("## 每日动态", md)
        self.assertIn("## 分群概览", md)
        self.assertIn("[文章](https://example.com/a?x=1&y=2)", md)
        self.assertIn("## 证据索引", md)


class TemplateEngineTests(unittest.TestCase):
    def render(self, src, **ctx):
        return Template(src, Loader([])).render(ctx)

    def test_escape_and_raw(self):
        self.assertEqual(self.render("{{ x }}|{{ x | raw }}", x="<b>"), "&lt;b&gt;|<b>")

    def test_if_elif_else_and_ops(self):
        src = "{% if n > 5 %}big{% elif n == 3 %}three{% else %}small{% endif %}"
        self.assertEqual(self.render(src, n=9), "big")
        self.assertEqual(self.render(src, n=3), "three")
        self.assertEqual(self.render(src, n=1), "small")
        self.assertEqual(self.render("{% if a and not b %}y{% endif %}", a=1, b=0), "y")

    def test_for_loop_and_empty(self):
        self.assertEqual(self.render("{% for i in xs %}{{ loop.index }}{{ i }}{% if not loop.last %},{% endif %}{% endfor %}", xs=["a", "b"]), "1a,2b")
        self.assertEqual(self.render("{% for i in xs %}x{% else %}none{% endfor %}", xs=[]), "none")

    def test_filters_and_paths(self):
        self.assertEqual(self.render("{{ d.v | pct }} {{ t | md }} {{ n | pad2 }}", d={"v": 0.256}, t="2026-10-05T09:00", n=3), "26% 10月5日 03")
        self.assertEqual(self.render("{{ xs.1 }}", xs=["a", "b"]), "b")

    def test_view_daily_rows(self):
        v = build_view(make_report(period="weekly"))
        self.assertTrue(v["has_daily"])
        self.assertEqual(len(v["daily"]), 7)
        self.assertEqual(v["daily"][0]["note"]["headline"], "第一天")


if __name__ == "__main__":
    unittest.main()
