"""V1.2 editorial report regression tests (synthetic chat data only)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wechat_summary.config import DEFAULTS, deep_merge
from wechat_summary.demo_data import build
from wechat_summary.errors import WSError
from wechat_summary.prepare import prepare
from wechat_summary.render import render_png, render_run
from wechat_summary.semantic import validate_run
from wxsummary import build_parser


class FullReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mirror = build(self.root / "mirror", "2026-10-19")
        self.cfg = deep_merge(DEFAULTS, {"me": {"names": ["阿栗"]}})
        self.prepared = prepare(
            self.cfg, chats=["独立开发者交流群"], group_set=None,
            period="daily", date="2026-10-19", since=None, until=None,
            mode="merged", out_root=str(self.root / "out"), now=None,
            source_overrides={"source_dir": str(self.mirror)})
        self.run_dir = self.prepared["run_dir"]
        self.report_dir = Path(self.prepared["reports"][0]["dir"])
        material = json.loads((self.report_dir / "material.json").read_text(encoding="utf-8"))
        self.msg_id = material["messages"][0]["id"]
        self.semantic = {
            "version": 1,
            "one_liner": "基于真实证据的总结。",
            "topics": [{"title": "测试话题", "summary": "该群讨论了一个议题。", "evidence": [self.msg_id]}],
            "insights": [], "follow_ups": [], "for_me": [], "links": [],
        }

    def save(self):
        (self.report_dir / "semantic.json").write_text(
            json.dumps(self.semantic, ensure_ascii=False), encoding="utf-8")

    def test_strict_validation_rejects_minimal_topics(self):
        self.save()
        result, code = validate_run(self.run_dir, strict_editorial=True)
        self.assertEqual(code, 1)
        self.assertTrue(any("background" in error for error in result["errors"]))
        self.assertFalse((self.report_dir / "report.json").exists())

    def test_editorial_sections_in_html_and_markdown(self):
        self.semantic["topics"][0].update(
            background="群友提出某个问题。",
            key_views=["方案A", "方案B"],
            debate="两种方案各有风险。",
            conclusion="尚待试验验证。",
            conclusion_status="pending")
        self.save()
        result, code = validate_run(self.run_dir, strict_editorial=True)
        self.assertEqual(code, 0, result)
        status, code = render_run(self.cfg, self.run_dir, themes=["newspaper"],
                                  formats=["md", "html"], stats_only=False)
        self.assertEqual(code, 0)
        md = (self.report_dir / "report.md").read_text(encoding="utf-8")
        html = (self.report_dir / "report.newspaper.html").read_text(encoding="utf-8")
        for phrase in ("背景", "关键观点", "主要争议", "当前结论", "待确认"):
            self.assertIn(phrase, md)
            self.assertIn(phrase, html)

    def test_default_output_has_png_and_stats_not_accepted(self):
        args = build_parser().parse_args(["render", "--run", "sample"])
        self.assertEqual(args.format, "md,html,png")
        render_run(self.cfg, self.run_dir, themes=["newspaper"],
                   formats=["md", "html"], stats_only=True)
        with self.assertRaises(WSError) as err:
            render_run(self.cfg, self.run_dir, themes=["newspaper"],
                       formats=["md", "html"], stats_only=False)
        self.assertEqual(err.exception.code, "semantic_missing")

    def test_invalid_conclusion_status_rejected(self):
        self.semantic["topics"][0]["conclusion_status"] = "invented"
        self.save()
        result, code = validate_run(self.run_dir)
        self.assertEqual(code, 1)
        self.assertTrue(any("conclusion_status" in e for e in result["errors"]))

    def test_long_png_not_silently_truncated(self):
        source = self.root / "long.html"
        source.write_text("<html></html>", encoding="utf-8")
        proc = SimpleNamespace(stdout=b'<html data-ws-height="39000"></html>',
                               stderr=b"", returncode=0)
        with patch("wechat_summary.render.find_browser", return_value="mock-browser"):
            with patch("wechat_summary.render.subprocess.run", return_value=proc) as run:
                ok, detail = render_png(source, self.root / "long.png")
        self.assertFalse(ok)
        self.assertIn("39000", detail)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.root / "long.png").exists())


if __name__ == "__main__":
    unittest.main()
