"""端到端：合成明文镜像 -> prepare -> validate -> render（全部主题，不含 PNG）。"""
from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wechat_summary.config import DEFAULTS, deep_merge  # noqa: E402
from wechat_summary.demo_data import build  # noqa: E402
from wechat_summary.errors import WSError  # noqa: E402
from wechat_summary.prepare import prepare  # noqa: E402
from wechat_summary.render import render_run  # noqa: E402
from wechat_summary.semantic import validate_run  # noqa: E402
from wechat_summary.sources import open_source  # noqa: E402
from wechat_summary.themes import list_themes  # noqa: E402

TMP = Path(__file__).resolve().parent / "_tmp"
EXAMPLE_SEMANTIC = ROOT / "examples" / "semantic.weekly.json"


def config(**over):
    base = deep_merge(DEFAULTS, {"me": {"ids": ["wxid_demo_self"], "names": ["阿栗"]},
                                 "group_sets": {"demo": ["独立开发者交流群", "周末徒步小队"]}})
    base = deep_merge(base, over)
    base["_path"] = None
    return base


def stub_semantic(material: dict, period: str) -> dict:
    """按素材生成一份最小合法语义稿（模拟 Agent 输出）。"""
    msgs = material["messages"] or []
    first = msgs[0]["id"] if msgs else None
    sem = {"version": 1, "one_liner": "测试摘要：本期有讨论。", "topics": [], "insights": [], "follow_ups": [], "for_me": [], "links": []}
    if first:
        sem["topics"].append({"title": "测试话题", "summary": "测试摘要", "evidence": [first],
                              "chat_id": material["meta"]["chats"][0]["id"] if not material["meta"]["multi"] else msgs[0]["chat_id"]})
    if period != "daily":
        days = sorted({m["t"][:5] for m in msgs})
        year = material["meta"]["window"]["since"][:4]
        sem["daily"] = [{"date": f"{year}-{d[:2]}-{d[3:]}", "headline": "当天", "evidence": [next(m["id"] for m in msgs if m["t"].startswith(d))]}
                        for d in days]
    return sem


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TMP / ("e2e_" + uuid.uuid4().hex[:8])
        cls.tmp.mkdir(parents=True)
        build(cls.tmp / "mirror", "2026-10-19")
        cls.mirror = str(cls.tmp / "mirror")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_pipeline(self, *, period, date, chats=None, group_set=None, mode="merged", semantic=None):
        cfg = config()
        result = prepare(cfg, chats=chats or [], group_set=group_set, period=period, date=date, since=None, until=None,
                         mode=mode, out_root=str(self.tmp / "out"), now=None, source_overrides={"source_dir": self.mirror})
        run_dir = Path(result["run_dir"])
        for r in result["reports"]:
            rd = Path(r["dir"])
            material = json.loads((rd / "material.json").read_text(encoding="utf-8"))
            sem = semantic if semantic is not None else stub_semantic(material, period)
            (rd / "semantic.json").write_text(json.dumps(sem, ensure_ascii=False), encoding="utf-8")
        payload, code = validate_run(str(run_dir))
        self.assertEqual(code, 0, payload)
        status, code = render_run(cfg, str(run_dir), themes=["all"], formats=["md", "html"], stats_only=False)
        self.assertEqual(code, 0, status)
        return result, status, run_dir

    def test_source_reads_both_shards_and_nicknames(self):
        with open_source(config(), source_dir=self.mirror) as src:
            chats = {c.name: c for c in src.list_chats()}
            self.assertEqual(set(chats), {"独立开发者交流群", "周末徒步小队"})
            dev = chats["独立开发者交流群"]
            self.assertEqual(dev.member_count, 7)
            from datetime import datetime
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Asia/Shanghai")
            msgs = src.messages(dev, datetime(2026, 10, 19, tzinfo=tz), datetime(2026, 10, 26, tzinfo=tz))
            names = {m.sender_name for m in msgs}
            self.assertIn("舟舟·后端", names)          # 群昵称优先
            self.assertIn("赵小满", names)             # 无群昵称回退到微信昵称
            self.assertTrue(any(m.is_self for m in msgs))
            self.assertTrue(any("服务端单调序号" in m.text for m in msgs))   # zstd 消息
            self.assertTrue(any(m.timestamp.day == 23 for m in msgs))         # message_1 分片中的群1 消息
            types = {m.type for m in msgs}
            self.assertTrue({"text", "quote", "link", "file", "image", "emoji", "system", "revoke"} <= types)

    def test_daily_single_group(self):
        result, status, run_dir = self.run_pipeline(period="daily", date="2026-10-19", chats=["独立开发者交流群"])
        report_dir = Path(result["reports"][0]["dir"])
        self.assertTrue((report_dir / "report.md").exists())
        for t in list_themes():
            self.assertTrue((report_dir / f"report.{t['name']}.html").exists())
        material = json.loads((report_dir / "material.json").read_text(encoding="utf-8"))
        self.assertEqual(material["meta"]["window"]["range_text"], "2026-10-19")
        self.assertIn("mfca6b1323f", material["mentions_me"])  # @阿栗
        self.assertTrue((run_dir / "status.json").exists())
        self.assertNotIn("sync", status)

    def test_weekly_merged_with_example_semantic(self):
        semantic = json.loads(EXAMPLE_SEMANTIC.read_text(encoding="utf-8"))
        result, status, _ = self.run_pipeline(period="weekly", date="2026-10-21", group_set="demo", semantic=semantic)
        rd = Path(result["reports"][0]["dir"])
        report = json.loads((rd / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report["semantic"]["topics"]), 4)
        self.assertEqual(report["semantic"]["topics"][3]["chat_name"], "周末徒步小队")
        self.assertEqual(len(report["groups"]), 2)
        md = (rd / "report.md").read_text(encoding="utf-8")
        self.assertIn("定价之争落地", md)
        self.assertIn("## 每日动态", md)
        html_text = (rd / "report.newspaper.html").read_text(encoding="utf-8")
        self.assertIn("凤凰岭", html_text)

    def test_weekly_separate_mode(self):
        result, status, run_dir = self.run_pipeline(period="weekly", date="2026-10-21", group_set="demo", mode="separate")
        self.assertEqual(len(result["reports"]), 2)
        self.assertTrue((run_dir / "index.html").exists())

    def test_monthly(self):
        result, _, _ = self.run_pipeline(period="monthly", date="2026-10-01", chats=["周末徒步小队"])
        material = json.loads((Path(result["reports"][0]["dir"]) / "material.json").read_text(encoding="utf-8"))
        self.assertEqual(len(material["stats"]["daily"]), 31)

    def test_empty_window_and_stats_only(self):
        cfg = config()
        result = prepare(cfg, chats=["周末徒步小队"], group_set=None, period="daily", date="2026-11-11", since=None, until=None,
                         mode="merged", out_root=str(self.tmp / "out"), now=None, source_overrides={"source_dir": self.mirror})
        self.assertTrue(result["reports"][0]["empty"])
        status, code = render_run(cfg, result["run_dir"], themes=["claude"], formats=["md", "html"], stats_only=True)
        self.assertEqual(code, 0)
        md = (Path(result["reports"][0]["dir"]) / "report.md").read_text(encoding="utf-8")
        self.assertIn("没有发言", md)

    def test_validate_rejects_without_semantic(self):
        cfg = config()
        result = prepare(cfg, chats=["周末徒步小队"], group_set=None, period="daily", date="2026-10-19", since=None, until=None,
                         mode="merged", out_root=str(self.tmp / "out"), now=None, source_overrides={"source_dir": self.mirror})
        payload, code = validate_run(result["run_dir"])
        self.assertEqual(code, 1)
        self.assertIn("缺少", payload["errors"][0])
        with self.assertRaises(WSError):
            render_run(cfg, result["run_dir"], themes=["claude"], formats=["md"], stats_only=False)

    def test_ambiguous_and_missing_group(self):
        with self.assertRaises(WSError) as ctx:
            prepare(config(), chats=["徒步"], group_set=None, period="daily", date="2026-10-19", since=None, until=None,
                    mode="merged", out_root=str(self.tmp / "out"), now=None, source_overrides={"source_dir": self.mirror})
        self.assertEqual(ctx.exception.code, "chat_not_found")
        self.assertEqual(ctx.exception.details[0]["candidates"][0]["name"], "周末徒步小队")


if __name__ == "__main__":
    unittest.main()
