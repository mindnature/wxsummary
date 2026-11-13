#!/usr/bin/env python3
"""wxsummary V1.1: user-authorized local WeChat connection, refresh and group reports.

Never silently extracts keys, closes WeChat, sends messages or schedules jobs.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from wechat_summary.config import load_config
from wechat_summary.errors import WSError
from wechat_summary.prepare import prepare
from wechat_summary.render import render_run
from wechat_summary.semantic import validate_run
from wechat_summary.sources import open_source
from wechat_summary.themes import list_themes

VERSION = "1.1.0"


def _source_args(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-dir", help="用户已获授权的微信 4.x 明文 SQLite 镜像目录（只读）")
    source.add_argument("--source-json", help="标准化聊天记录 JSON 导出文件（只读）")
    parser.add_argument("--account", help="镜像包含多账号时指定账号目录")


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="配置路径，默认 ~/.wxsummary/config.yaml")
    parser.add_argument("--json", action="store_true", help="机器可读 JSON 输出")


def _selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--chats", action="append", default=[], help="群名称或群 ID；多个群可以重复传入")
    parser.add_argument("--group-set", help="配置文件中预设的群集合名称")
    parser.add_argument("--date", help="YYYY-MM-DD；不填默认今天（配置时区）")
    parser.add_argument("--period", choices=["daily", "weekly", "monthly", "custom"], default="daily")
    parser.add_argument("--since", help="自定义时间窗起点（包含）")
    parser.add_argument("--until", help="自定义时间窗终点（不包含）")
    parser.add_argument("--mode", choices=["merged", "separate"], default="merged")
    parser.add_argument("--out-root", help="报告输出根目录，默认 ~/Documents/wxsummary")


def _source_override(args: argparse.Namespace) -> dict:
    return {"source_dir": getattr(args, "source_dir", None),
            "source_json": getattr(args, "source_json", None),
            "account": getattr(args, "account", None)}


def _csv(values: list[str]) -> list[str]:
    return [item.strip() for value in values for item in value.split(",") if item.strip()]


def _chosen_date(args: argparse.Namespace, config: dict) -> str | None:
    if args.since or args.until:
        return args.date  # prepare rejects incompatible date + since/until
    return args.date or datetime.now(ZoneInfo(config["timezone"])).date().isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="wxsummary：只读微信群聊数据，按需生成带原话证据的群报。",
        epilog="第三方工具与取钥需要分别授权；不发消息或定时执行。")
    parser.add_argument("--version", action="version", version=f"wxsummary {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("demo", help="生成纯虚构的 SQLite 示例，不需要微信")
    p.add_argument("--out", default="./demo-mirror")
    p.add_argument("--start", default="2026-10-19")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("setup", help="首次接入向导（只读状态或经授权安装外部工具）")
    _common(p)
    p.add_argument("--status", action="store_true", help="只检查接入状态，不修改任何文件")
    p.add_argument("--use-mirror", help="配置已有授权的明文 SQLite 镜像")
    p.add_argument("--wechat-data", help="指定微信加密数据目录（只用于定位）")
    p.add_argument("--account", help="明文镜像中的账号目录")
    p.add_argument("--tool-dir", help="外部工具的安装目录")
    p.add_argument("--install-tool", action="store_true", help="安装独立第三方解密工具")
    p.add_argument("--trust-third-party-code", dest="trust_tool", action="store_true",
                   help="明确同意下载并执行外部源码")
    p.add_argument("--extract-key", action="store_true", help="用户授权后执行首次取钥")
    p.add_argument("--consent-restart-wechat", dest="consent_restart", action="store_true",
                   help="明确同意关闭微信并由用户重新打开")
    p.add_argument("--key-wait", type=int, default=120, help="等待重新打开微信的时间（30-300秒）")

    p = sub.add_parser("refresh", help="按需调用本机已授权外部工具，刷新明文数据库")
    _common(p)

    p = sub.add_parser("groups", help="列出可读取的群及群 ID")
    _common(p)
    _source_args(p)
    p.add_argument("--filter", default="", help="筛选群名关键词")
    p.add_argument("--limit", type=int, default=100)

    p = sub.add_parser("doctor", help="检查明文数据库或 JSON 数据源")
    _common(p)
    _source_args(p)

    for command, help_text in (
        ("prepare", "只读取数并生成带消息 ID 的素材包，供 Agent 写摘要"),
        ("quick", "立刻生成纯统计版 Markdown/HTML 群报（不用模型）"),
    ):
        p = sub.add_parser(command, help=help_text)
        _common(p)
        _source_args(p)
        _selection(p)
        p.add_argument("--refresh", action="store_true", help="先刷新本机明文镜像，再选群准备群报")
        if command == "quick":
            p.add_argument("--theme", default="newspaper", help="主题：newspaper/ink 等")
            p.add_argument("--format", default="md,html", help="输出格式：md,html,png")

    p = sub.add_parser("validate", help="校验 Agent 语义稿的证据，并生成 report.json")
    _common(p)
    p.add_argument("--run", required=True, help="prepare 的 run_dir")
    p.add_argument("--semantic", help="语义稿路径；默认在报告子目录 semantic.json")

    p = sub.add_parser("render", help="把校验通过的报告渲染为 Markdown/HTML/PNG")
    _common(p)
    p.add_argument("--run", required=True)
    p.add_argument("--theme", default="newspaper")
    p.add_argument("--format", default="md,html")
    p.add_argument("--stats-only", action="store_true", help="无语义稿时渲染纯统计版")

    p = sub.add_parser("themes", help="查看本机可用主题")
    _common(p)
    return parser


def _run(args: argparse.Namespace) -> tuple[dict, int]:
    if args.command == "demo":
        from wechat_summary.demo_data import build
        dest = build(Path(args.out).expanduser(), args.start)
        return {"ok": True, "example_dir": str(dest),
                "next": f'python wxsummary.py quick --source-dir "{args.out}" '
                        f'--chats "独立开发者交流群" --date {args.start}'}, 0

    cfg = load_config(args.config)
    command = args.command
    if command == "themes":
        return {"ok": True, "themes": [{k: v for k, v in t.items() if k != "dir"} for t in list_themes()]}, 0
    if command == "setup":
        from wechat_summary.setup import run_setup
        return run_setup(cfg, args)
    if command == "refresh":
        from wechat_summary.sources.refresh import run_refresh
        return run_refresh(cfg), 0
    if command == "doctor":
        with open_source(cfg, **_source_override(args)) as src:
            return {"ok": True, "source": src.doctor()}, 0
    if command == "groups":
        with open_source(cfg, **_source_override(args)) as src:
            chats = src.list_chats()
        word = args.filter.casefold().strip()
        if word:
            chats = [c for c in chats if word in c.name.casefold() or word in c.id.casefold()]
        chats.sort(key=lambda c: c.last_time.timestamp() if c.last_time else 0, reverse=True)
        return {"ok": True, "total": len(chats),
                "chats": [c.to_dict() for c in chats[:max(1, args.limit)]]}, 0
    if command in ("prepare", "quick"):
        chats = _csv(args.chats)
        if not chats and not args.group_set:
            raise WSError("chats_required", "请用 --chats 指定至少一个群，或用 --group-set 指定群集合")
        refreshed = None
        if args.refresh:
            if args.source_json or (not args.source_dir and (cfg.get("source") or {}).get("type") == "json"):
                raise WSError("refresh_not_configured", "JSON 离线导入不能执行微信数据库刷新")
            from wechat_summary.sources.refresh import run_refresh
            refreshed = run_refresh(cfg)
        result = prepare(
            cfg, chats=chats, group_set=args.group_set,
            period=args.period, date=_chosen_date(args, cfg),
            since=args.since, until=args.until, mode=args.mode,
            out_root=args.out_root, now=None,
            source_overrides=_source_override(args))
        if refreshed is not None:
            result["refresh"] = refreshed
        if command == "prepare":
            return result, 0
        status, code = render_run(cfg, result["run_dir"], themes=_csv([args.theme]),
                                  formats=_csv([args.format]), stats_only=True)
        return {"ok": code == 0, "run_dir": result["run_dir"], "refresh": refreshed,
                "reports": result["reports"], "render": status,
                "note": "统计版不包含 AI 语义分析；完整群报请用 Agent Skill。"}, code
    if command == "validate":
        return validate_run(args.run, args.semantic)
    if command == "render":
        return render_run(cfg, args.run, themes=_csv([args.theme]),
                          formats=_csv([args.format]), stats_only=args.stats_only)
    raise WSError("unknown_command", command)


def main(argv: list[str] | None = None) -> int:
    # Windows PowerShell/GitHub Actions may expose cp1252/GBK streams.
    # CLI JSON and report paths include Chinese, so request UTF-8 explicitly.
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result, code = _run(args)
    except WSError as exc:
        if args.json:
            print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"错误：{exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, ImportError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"执行失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
