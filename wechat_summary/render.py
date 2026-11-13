"""渲染：report.json -> Markdown（GFM）/ 单文件 HTML（按主题）/ PNG（可选）。"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .charts import ascii_bars, build_charts
from .errors import EXIT_FAIL, EXIT_OK, EXIT_PARTIAL, WSError
from .models import TYPE_LABELS
from .prepare import load_run
from .templating import Loader, TemplateError
from .themes import SHARED_DIR, get_theme, resolve_theme_names

# 只拦截会被浏览器"加载"的外部资源；<a href> 是用户点击的链接，允许。
EXTERNAL_RESOURCE_RE = re.compile(
    r"""<(?:script|img|iframe|link|source|video|audio|embed|object)\b[^>]*\b(?:src|href|data)\s*=\s*["']?(?:https?:)?//"""
    r"""|@import\s+(?:url\()?\s*["']?(?:https?:)?//|url\(\s*["']?(?:https?:)?//""",
    re.I,
)
WEEKDAY = "一二三四五六日"

# 章节骨架：所有主题共用同一套章节顺序与编号，主题只负责视觉，保证章节边界清晰。
SECTIONS = [
    ("overview", "数据概览", "Overview", "▦"),
    ("topics", "主线话题", "Top Stories", "◆"),
    ("insights", "观点与分歧", "Insights", "◎"),
    ("follow_ups", "值得跟进", "Follow-ups", "☐"),
    ("links", "{period_word}的链接", "Links", "↗"),
    ("for_me", "与我相关", "For You", "★"),
    ("daily", "每日动态", "Day by Day", "▤"),
    ("groups", "分群概览", "Groups", "▣"),
    ("activity", "活跃数据", "Activity", "▥"),
]
_CN_NUM = "壹贰叁肆伍陆柒捌玖拾"
_ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
DEFAULT_VARS = {"brand": "WECHAT DIGEST", "toc": "本期目录", "note": "编者按", "ascii": False}


def _numeral(i: int, style: str) -> str:
    if style == "cn":
        return _CN_NUM[(i - 1) % 10]
    if style == "roman":
        return _ROMAN[(i - 1) % 10]
    if style == "alpha":
        return "ABCDEFGHIJ"[(i - 1) % 10]
    if style == "hex":
        return f"0x{i:02X}"
    return f"{i:02d}"


def _build_sections(view: dict[str, Any], theme: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    present = {
        "overview": (True, ""),
        "topics": (bool(view["topics"]), len(view["topics"])),
        "insights": (bool(view["insights"]), len(view["insights"])),
        "follow_ups": (bool(view["follow_ups"]), len(view["follow_ups"])),
        "for_me": (bool(view["for_me"]), len(view["for_me"])),
        "daily": (view["has_daily"], len(view["daily"])),
        "groups": (bool(view["groups"]), len(view["groups"])),
        "activity": (bool(view["top_senders"]), len(view["top_senders"])),
        "links": (bool(view["links"]), len(view["links"])),
    }
    overrides = theme.get("sections") or {}
    numerals = theme.get("numerals", "arabic")
    sec: dict[str, Any] = {}
    order: list[dict[str, Any]] = []
    n = 0
    for key, title, en, icon in SECTIONS:
        show, count = present[key]
        item = {"key": key, "title": title.format(period_word=view["period_word"]), "en": en, "icon": icon, "count": count, "show": show}
        item.update(overrides.get(key) or {})
        item["title"] = str(item["title"]).format(period_word=view["period_word"])
        if show:
            n += 1
            item["index"] = n
            item["no"] = _numeral(n, numerals)
            order.append(item)
        sec[key] = item
    return sec, order


# ---------------------------------------------------------------- 视图上下文
def _hour_range(h: int | None) -> str:
    return "" if h is None else f"{h:02d}:00-{(h + 1) % 24:02d}:00"


def build_view(report: dict[str, Any], theme: dict[str, Any] | None = None) -> dict[str, Any]:
    meta, stats, sem = report["meta"], report["stats"], report["semantic"]
    window = meta["window"]
    since = datetime.fromisoformat(window["since"])
    style = (theme or {}).get("chart_style", "clean")
    palette = (theme or {}).get("palette") or {}
    charts = build_charts(report, palette, "sketch" if style == "sketch" else "clean") if theme else {}
    top = stats["top_senders"]
    kpis = [
        {"label": "条发言", "value": stats["speech_count"]},
        {"label": "人参与", "value": stats["sender_count"]},
        {"label": "个链接", "value": stats["link_count"]},
        {"label": "最活跃时段", "value": _hour_range(stats.get("peak_hour")) or "—"},
    ]
    groups = report.get("groups") or []
    max_group = max((g["messages"] for g in groups), default=0) or 1
    for g in groups:
        g["bar_pct"] = round(100 * g["messages"] / max_group)
    daily_stats = stats.get("daily") or []
    max_day = max((d["messages"] for d in daily_stats), default=0) or 1
    daily_rows = []
    sem_daily = {d["date"]: d for d in sem.get("daily", [])}
    for d in daily_stats:
        filled = int(round(20 * d["messages"] / max_day))
        daily_rows.append({**d, "bar_pct": round(100 * d["messages"] / max_day), "note": sem_daily.get(d["date"]),
                           "ascii": "█" * filled + "░" * (20 - filled)})
    view = {
        "generator": f"wechat-summary {__version__}",
        "title": sem.get("title") or meta["title"],
        "period": window["period"],
        "period_label": window["label"],
        "period_word": {"daily": "今天", "weekly": "这周", "monthly": "这个月"}.get(window["period"], "这段时间"),
        "range_text": window["range_text"],
        "timezone": window["timezone"],
        "issue_date": since.strftime("%Y年%m月%d日"),
        "issue_weekday": "星期" + WEEKDAY[since.weekday()],
        "issue_no": window["slug"],
        "chats": meta["chats"],
        "chat_names": "、".join(c["name"] for c in meta["chats"]),
        "multi": meta["multi"],
        "one_liner": sem.get("one_liner", ""),
        "mood": sem.get("mood", ""),
        "editor_note": sem.get("editor_note", ""),
        "topics": sem.get("topics", []),
        "lead": (sem.get("topics") or [None])[0],
        "rest_topics": (sem.get("topics") or [])[1:],
        "insights": sem.get("insights", []),
        "follow_ups": sem.get("follow_ups", []),
        "for_me": sem.get("for_me", []),
        "links": sem.get("links", []),
        "daily": daily_rows,
        "has_daily": len(daily_stats) > 1,
        "has_daily_notes": bool(sem.get("daily")),
        "stats": stats,
        "kpis": kpis,
        "top_senders": top[:8],
        "top3": top[:3],
        "types": stats["types"][:6],
        "most_quoted": stats.get("most_quoted") or [],
        "groups": groups,
        "charts": charts,
        "ascii_senders": ascii_bars([(s["name"], s["messages"]) for s in top[:8]]),
        "ascii_hourly": ascii_bars([(f"{h:02d}", v) for h, v in enumerate(stats["hourly"]) if v], width=24),
        "stats_only": report.get("stats_only", False),
        "empty": stats.get("empty", False),
        "warnings": meta.get("warnings", []),
        "source_latest": meta.get("source_latest"),
        "generated_at": meta.get("generated_at"),
        "semantic_truth": report.get("semantic_truth", "not-verified"),
        "theme": {k: v for k, v in (theme or {}).items() if k in ("name", "label")},
    }
    view["tv"] = {**DEFAULT_VARS, **((theme or {}).get("vars") or {})}
    view["sec"], view["sec_list"] = _build_sections(view, theme or {})
    return view


# ---------------------------------------------------------------- Markdown
def _md_escape(text: Any) -> str:
    s = str(text or "").replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_\[\]<>|])", r"\\\1", s)


def render_markdown(report: dict[str, Any]) -> str:
    v = build_view(report)
    out = [f"# {_md_escape(v['title'])}", ""]
    out.append(f"> {v['period_label']}｜{v['range_text']}（{v['timezone']}）｜{_md_escape(v['chat_names'])}")
    out.append("")
    if v["one_liner"]:
        out += [f"**{_md_escape(v['one_liner'])}**", ""]
    if v["mood"]:
        out += [f"氛围：{_md_escape(v['mood'])}", ""]
    if v["editor_note"]:
        out += [f"> 编者按：{_md_escape(v['editor_note'])}", ""]
    out += ["## 数据概览", "", "| 指标 | 数值 |", "|---|---|"]
    for k in v["kpis"]:
        out.append(f"| {k['label']} | {k['value']} |")
    out.append("")
    if v["topics"]:
        out += ["## 主线话题", ""]
        for i, t in enumerate(v["topics"], 1):
            chat = f"〔{_md_escape(t['chat_name'])}〕" if v["multi"] and t.get("chat_name") else ""
            out += [f"### {i}. {chat}{_md_escape(t['title'])}", "", _md_escape(t.get("summary", "")), ""]
            for p in t.get("points") or []:
                out.append(f"- {_md_escape(p)}")
            for q in t.get("quotes") or []:
                out.append(f"> 「{_md_escape(q['text'])}」—— {_md_escape(q.get('sender', ''))}")
            if t.get("people"):
                out.append(f"\n参与：{_md_escape('、'.join(t['people']))}")
            out.append("")
    if v["insights"]:
        out += ["## 观点与分歧", ""]
        for it in v["insights"]:
            label = f"【{it['stance_label']}】" if it.get("stance_label") else ""
            out.append(f"- {label}{_md_escape(it['claim'])}" + (f"：{_md_escape(it['detail'])}" if it.get("detail") else ""))
        out.append("")
    if v["follow_ups"]:
        out += ["## 值得跟进", "", "| 事项 | 负责人 | 时间 | 说明 |", "|---|---|---|---|"]
        for f in v["follow_ups"]:
            out.append(f"| {_md_escape(f['title'])} | {_md_escape(f.get('owner', ''))} | {_md_escape(f.get('due', ''))} | {_md_escape(f.get('why', ''))} |")
        out.append("")
    if v["links"]:
        out += [f"## {v['period_word']}的链接", ""]
        for link in v["links"]:
            title = _md_escape(link.get("title") or link["url"])
            note = f" — {_md_escape(link['note'])}" if link.get("note") else ""
            out.append(f"- [{title}]({link['url']}){note}")
        out.append("")
    if v["for_me"]:
        out += ["## 与我相关", ""]
        for f in v["for_me"]:
            act = f"（{_md_escape(f['action'])}）" if f.get("action") else ""
            out.append(f"- [ ] {_md_escape(f['title'])}{act}")
        out.append("")
    if v["has_daily"]:
        out += ["## 每日动态", "", "| 日期 | 发言 | 人数 | 要点 |", "|---|---:|---:|---|"]
        for d in v["daily"]:
            note = _md_escape(d["note"]["headline"]) if d.get("note") else ""
            out.append(f"| {d['date'][5:]} 周{d['weekday']} | {d['messages']} | {d['senders']} | {note} |")
        out.append("")
    if v["groups"]:
        out += ["## 分群概览", "", "| 群 | 发言 | 人数 | 链接 |", "|---|---:|---:|---:|"]
        for g in v["groups"]:
            out.append(f"| {_md_escape(g['chat_name'])} | {g['messages']} | {g['senders']} | {g['links']} |")
        out.append("")
    if v["top_senders"]:
        out += ["## 发言排行", "", "| # | 发言人 | 条数 | 占比 |", "|---:|---|---:|---:|"]
        for i, s in enumerate(v["top_senders"], 1):
            out.append(f"| {i} | {_md_escape(s['name'])} | {s['messages']} | {s['share']:.0%} |")
        out.append("")
    # 证据附录
    refs = []
    for section, label in (("topics", "话题"), ("insights", "观点"), ("follow_ups", "跟进"), ("for_me", "与我相关")):
        for i, item in enumerate(v[section], 1):
            for ref in item.get("evidence_refs", [])[:3]:
                refs.append(f"| {label}{i} | {ref['time'][5:16].replace('T', ' ')} | {_md_escape(ref['sender'])} | {_md_escape(ref['text'][:60])} |")
    if refs:
        out += ["## 证据索引", "", "| 条目 | 时间 | 发言人 | 原文片段 |", "|---|---|---|---|", *refs, ""]
    foot = [f"范围：{v['range_text']}（{v['timezone']}，左闭右开）", f"数据最新：{v['source_latest'] or '未知'}"]
    if v["stats_only"]:
        foot.append("本报告为仅统计版，未经语义编辑")
    foot.append("semantic_truth: not-verified（格式校验通过不代表观点已核实）")
    for w in v["warnings"]:
        foot.append(f"注意：{_md_escape(w)}")
    out += ["---", ""] + [f"- {f}" for f in foot] + ["", f"*由 {v['generator']} 生成*", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- HTML
def render_html(report: dict[str, Any], theme_name: str) -> str:
    theme = get_theme(theme_name)
    folder = Path(theme["dir"])
    loader = Loader([folder, SHARED_DIR])
    css_parts = []
    for css in (SHARED_DIR / "base.css", SHARED_DIR / "layout.css", folder / "style.css"):
        if css.is_file():
            css_parts.append(css.read_text(encoding="utf-8"))
    view = build_view(report, theme)
    view["css"] = "\n".join(css_parts)
    try:
        html_text = loader.load("template.html").render(view)
    except TemplateError as exc:
        raise WSError("render_failed", f"主题 {theme_name} 渲染失败：{exc}") from exc
    if EXTERNAL_RESOURCE_RE.search(html_text):
        raise WSError("render_failed", f"主题 {theme_name} 引用了外部网络资源，违反单文件约束")
    return html_text


# ---------------------------------------------------------------- PNG
def find_browser() -> str | None:
    candidates = [
        shutil.which("chrome"), shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("msedge"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


def render_png(html_path: Path, png_path: Path, width: int = 480) -> tuple[bool, str]:
    """用本机 Chrome/Edge headless 截长图：先测高度，再按实际高度截图。

    Windows 上 Chrome 对含中文的 --screenshot 路径和 file:// URL 处理不稳定，
    因此统一复制到纯 ASCII 临时目录里截图，再拷回目标位置。
    """
    browser = find_browser()
    if not browser:
        return False, "未找到 Chrome 或 Edge，已跳过 PNG"
    proc = None
    with tempfile.TemporaryDirectory(prefix="wsshot_") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / "page.html"
        shutil.copyfile(html_path, src)
        shot = tmp_dir / "shot.png"
        profile = tmp_dir / "profile"
        url = src.resolve().as_uri()
        base = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
                "--force-device-scale-factor=2", "--no-first-run", "--no-default-browser-check"]
        height = 6000
        try:
            dump = subprocess.run(base + [f"--window-size={width},1000", "--virtual-time-budget=2000", "--dump-dom", url],
                                  capture_output=True, timeout=90)
            m = re.search(rb'data-ws-height="(\d+)"', dump.stdout or b"")
            if m:
                height = min(30000, max(800, int(m.group(1)) + 8))
            proc = subprocess.run(base + [f"--window-size={width},{height}", "--virtual-time-budget=2000",
                                          f"--screenshot={shot}", url],
                                  capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"截图失败：{exc}"
        if shot.exists() and shot.stat().st_size > 0:
            shutil.copyfile(shot, png_path)
            return True, f"{width}x{height}@2x"
    detail = (proc.stderr or b"").decode("utf-8", "replace")[-300:] if proc else ""
    return False, f"截图失败：{detail}"


# ---------------------------------------------------------------- 主流程
def render_run(config: dict[str, Any], run: str, *, themes: list[str], formats: list[str], stats_only: bool) -> tuple[dict[str, Any], int]:
    run_dir, run_meta = load_run(run)
    render_cfg = config.get("render") or {}
    theme_names = resolve_theme_names(themes, render_cfg.get("themes") or ["newspaper"])
    fmts = [f.lower() for f in (formats or render_cfg.get("formats") or ["md", "html"])]
    unknown = [f for f in fmts if f not in ("md", "html", "png")]
    if unknown:
        raise WSError("render_failed", f"不支持的格式：{', '.join(unknown)}（可选 md, html, png）")
    if "png" in fmts and "html" not in fmts:
        fmts.append("html")

    if stats_only:
        from .semantic import build_stats_only

        build_stats_only(str(run_dir))

    files: list[str] = []
    warnings: list[str] = []
    report_status: list[dict[str, Any]] = []
    partial = False
    for r in run_meta["reports"]:
        report_dir = run_dir / r["key"]
        report_path = report_dir / "report.json"
        if not report_path.exists():
            raise WSError("report_missing", f"缺少 {report_path}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        entry: dict[str, Any] = {"key": r["key"], "title": report["meta"]["title"], "outputs": {}}
        if "md" in fmts:
            md_path = report_dir / "report.md"
            md_path.write_text(render_markdown(report), encoding="utf-8")
            files.append(str(md_path))
            entry["outputs"]["md"] = md_path.name
        for theme_name in theme_names:
            if "html" in fmts:
                html_path = report_dir / f"report.{theme_name}.html"
                html_path.write_text(render_html(report, theme_name), encoding="utf-8")
                files.append(str(html_path))
                entry["outputs"][f"html:{theme_name}"] = html_path.name
                if "png" in fmts:
                    png_path = report_dir / f"report.{theme_name}.png"
                    ok, detail = render_png(html_path, png_path)
                    if ok:
                        files.append(str(png_path))
                        entry["outputs"][f"png:{theme_name}"] = png_path.name
                    else:
                        partial = True
                        entry["outputs"][f"png:{theme_name}"] = "skipped"
                        warnings.append(f"{r['key']}/{theme_name}: {detail}")
        entry["stats_only"] = report.get("stats_only", False)
        report_status.append(entry)

    if len(run_meta["reports"]) > 1 and "html" in fmts:
        index_path = run_dir / "index.html"
        index_path.write_text(_index_html(run_meta, report_status, theme_names), encoding="utf-8")
        files.append(str(index_path))

    status: dict[str, Any] = {
        "ok": True,
        "status": "partial" if partial else "complete",
        "generator": f"wechat-summary {__version__}",
        "rendered_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "themes": theme_names,
        "formats": fmts,
        "reports": report_status,
        "files": files,
        "warnings": warnings,
        "semantic_truth": "not-verified",
    }
    code = EXIT_PARTIAL if partial else EXIT_OK
    (run_dir / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    return status, code


def _index_html(run_meta: dict[str, Any], reports: list[dict[str, Any]], themes: list[str]) -> str:
    import html as h

    rows = []
    for r in reports:
        links = " · ".join(f'<a href="{h.escape(r["key"])}/report.{t}.html">{h.escape(t)}</a>' for t in themes)
        rows.append(f"<li><strong>{h.escape(r['title'])}</strong><br><small>{links}</small></li>")
    w = run_meta["window"]
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{h.escape(run_meta['set_label'])}｜{h.escape(w['label'])}</title>"
        "<style>body{font:16px/1.7 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif;max-width:640px;margin:40px auto;padding:0 20px;color:#222}"
        "li{margin:14px 0}a{color:#c96442}</style></head><body>"
        f"<h1>{h.escape(run_meta['set_label'])}｜{h.escape(w['label'])}</h1><p>{h.escape(w['range_text'])}（{h.escape(w['timezone'])}）</p>"
        f"<ul>{''.join(rows)}</ul></body></html>"
    )


def type_label(t: str) -> str:
    return TYPE_LABELS.get(t, t)
