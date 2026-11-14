"""validate：校验 Agent 写的 semantic.json，并与统计合成 report.json。

report.json 是渲染的唯一输入：统计来自程序，语义来自 Agent，证据原文由程序回填。
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from . import __version__
from .errors import EXIT_FAIL, EXIT_OK, WSError
from .prepare import load_run
from .schema import (CONCLUSION_STATUS_VALUES, SEMANTIC_VERSION, STANCE_LABELS,
                     STANCE_VALUES, TOP_FIELDS, schema_for_period)

REPORT_SCHEMA = 1


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"\s+", "", text)


def _check_type(value: Any, kind: str) -> bool:
    if kind == "str":
        return isinstance(value, str) and value.strip() != ""
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "list[str]":
        return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)
    if kind == "list[quote]":
        return isinstance(value, list) and all(isinstance(v, dict) and isinstance(v.get("msg_id"), str) and isinstance(v.get("text"), str) for v in value)
    return True


def validate_semantic(semantic: Any, material: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    """返回 (errors, warnings)。errors 非空即不通过。"""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(semantic, dict):
        return ["semantic.json 顶层必须是对象"], warnings
    meta = material["meta"]
    period = meta["window"]["period"]
    chat_ids = {c["id"] for c in meta["chats"]}
    chat_names = {c["name"]: c["id"] for c in meta["chats"]}
    link_urls = {link["url"] for link in material["stats"]["links"]}
    days = {d["date"] for d in material["stats"]["daily"]}
    multi = meta["multi"]

    for key, (kind, required, _desc) in TOP_FIELDS.items():
        if key not in semantic or semantic[key] in (None, ""):
            if required:
                errors.append(f"缺少顶层字段 {key}")
            continue
        if not _check_type(semantic[key], kind):
            errors.append(f"顶层字段 {key} 类型应为 {kind}")
    if semantic.get("version") not in (None, SEMANTIC_VERSION) and isinstance(semantic.get("version"), int):
        errors.append(f"version 应为 {SEMANTIC_VERSION}")
    known = set(TOP_FIELDS) | set(schema_for_period(period))
    for key in semantic:
        if key not in known:
            warnings.append(f"忽略未知字段 {key}")

    empty_window = material["stats"]["empty"]
    for section, spec in schema_for_period(period).items():
        items = semantic.get(section, [])
        if items is None:
            items = []
        if not isinstance(items, list):
            errors.append(f"{section} 必须是数组")
            continue
        if spec["max"] == 0 and items:
            warnings.append(f"{period} 报告不需要 {section}，已忽略")
            continue
        min_needed = 0 if empty_window else spec["min"]
        if len(items) < min_needed:
            errors.append(f"{section}（{spec['label']}）至少需要 {min_needed} 条，当前 {len(items)} 条")
        if len(items) > spec["max"]:
            errors.append(f"{section}（{spec['label']}）最多 {spec['max']} 条，当前 {len(items)} 条")
        if section == "for_me" and items and not meta["me_configured"]:
            errors.append("未配置“我的身份”，for_me 必须为空数组")
        for i, item in enumerate(items):
            where = f"{section}[{i}]"
            if not isinstance(item, dict):
                errors.append(f"{where} 必须是对象")
                continue
            for field, (kind, required, _d) in spec["fields"].items():
                if field not in item or item[field] in (None, "", []):
                    if required:
                        errors.append(f"{where} 缺少 {field}")
                    continue
                if not _check_type(item[field], kind):
                    errors.append(f"{where}.{field} 类型应为 {kind}")
            ev = item.get("evidence")
            if not isinstance(ev, list) or not ev or not all(isinstance(x, str) for x in ev):
                errors.append(f"{where} 缺少 evidence（至少 1 个 msg_id）")
                ev = []
            bad = [x for x in ev if x not in evidence]
            if bad:
                errors.append(f"{where} 的证据 ID 不存在于本次素材：{', '.join(bad[:5])}")
            # chat_id 归属
            cid = item.get("chat_id")
            if cid:
                if cid in chat_names:  # 允许直接写群名
                    item["chat_id"] = cid = chat_names[cid]
                if cid not in chat_ids:
                    errors.append(f"{where}.chat_id 不属于本报告的群：{cid}")
                else:
                    foreign = [x for x in ev if x in evidence and evidence[x]["chat_id"] != cid]
                    if foreign:
                        errors.append(f"{where} 标注来自 {cid}，但证据 {', '.join(foreign[:3])} 属于其他群")
            elif multi and section in ("topics", "insights", "follow_ups", "for_me"):
                warnings.append(f"{where} 未填写 chat_id，将按证据自动推断")
            # 字段级规则
            if section == "topics" and item.get("conclusion_status") and item["conclusion_status"] not in CONCLUSION_STATUS_VALUES:
                errors.append(f"{where}.conclusion_status 无效：{item['conclusion_status']}")
            if section == "insights" and item.get("stance") and item["stance"] not in STANCE_VALUES:
                errors.append(f"{where}.stance 只能是 {', '.join(sorted(STANCE_VALUES))}")
            if section == "links" and item.get("url") and item["url"] not in link_urls:
                errors.append(f"{where}.url 不在 stats.links 中：{item['url'][:80]}")
            if section == "daily" and item.get("date") and item["date"] not in days:
                errors.append(f"{where}.date 不在报告范围内：{item['date']}")
            for qi, quote in enumerate(item.get("quotes") or []):
                if not isinstance(quote, dict):
                    continue
                qid, qtext = quote.get("msg_id"), quote.get("text", "")
                if qid not in evidence:
                    errors.append(f"{where}.quotes[{qi}] 的 msg_id 不存在：{qid}")
                elif not isinstance(qtext, str) or not qtext.strip():
                    errors.append(f"{where}.quotes[{qi}] 引文不能为空")
                elif qtext not in evidence[qid]["text"]:
                    errors.append(f"{where}.quotes[{qi}] 不是该消息原文的连续片段，请逐字摘录")
                elif qid not in ev:
                    item.setdefault("evidence", []).append(qid)
    return errors, warnings


def _infer_chat(ev_ids: list[str], evidence: dict[str, dict[str, Any]]) -> str | None:
    counts: dict[str, int] = {}
    for x in ev_ids:
        if x in evidence:
            counts[evidence[x]["chat_id"]] = counts.get(evidence[x]["chat_id"], 0) + 1
    return max(counts, key=counts.get) if counts else None


def _evidence_refs(ids: list[str], evidence: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    refs = []
    for x in ids:
        e = evidence.get(x)
        if e:
            refs.append({"id": x, "time": e["time"], "sender": e["sender"], "text": e["text"][:160], "chat_name": e["chat_name"], "chat_id": e["chat_id"]})
    return refs


def build_report(material: dict[str, Any], semantic: dict[str, Any] | None, evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    meta = dict(material["meta"])
    period = meta["window"]["period"]
    chat_name = {c["id"]: c["name"] for c in meta["chats"]}
    stats = material["stats"]
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "generator": f"wechat-summary {__version__}",
        "meta": meta,
        "stats": stats,
        "groups": material.get("groups"),
        "stats_only": semantic is None,
        "semantic_truth": "not-verified",
    }
    if semantic is None:
        report["semantic"] = {
            "title": meta["title"],
            "one_liner": ("该时间窗内没有发言消息。" if stats["empty"] else
                          f"共 {stats['speech_count']} 条发言、{stats['sender_count']} 人参与；本报告为仅统计版，未经语义编辑。"),
            "mood": "",
            "editor_note": "",
            "topics": [], "insights": [], "follow_ups": [], "for_me": [], "daily": [],
            "links": [{"url": l["url"], "title": l["title"] or l["domain"], "note": f"{'、'.join(l['sharers'])} 分享",
                       "evidence": [l["first_msg_id"]]} for l in stats["links"][:10]],
        }
    else:
        report["semantic"] = {
            "title": semantic.get("title") or meta["title"],
            "one_liner": semantic.get("one_liner", ""),
            "mood": semantic.get("mood", ""),
            "editor_note": semantic.get("editor_note", ""),
        }
        for section, spec in schema_for_period(period).items():
            items = [dict(x) for x in (semantic.get(section) or [])] if spec["max"] > 0 else []
            report["semantic"][section] = items
    # 回填：证据原文、群名、立场标签、链接标题
    links_by_url = {l["url"]: l for l in stats["links"]}
    for section in ("topics", "insights", "follow_ups", "for_me", "links", "daily"):
        for item in report["semantic"].get(section, []):
            ids = [x for x in item.get("evidence", []) if x in evidence]
            item["evidence"] = ids
            item["evidence_refs"] = _evidence_refs(ids, evidence)
            if section in ("topics", "insights", "follow_ups", "for_me"):
                cid = item.get("chat_id") or _infer_chat(ids, evidence)
                if cid:
                    item["chat_id"] = cid
                    item["chat_name"] = chat_name.get(cid, "")
            if section == "insights":
                item["stance"] = item.get("stance") or "insight"
                item["stance_label"] = STANCE_LABELS.get(item["stance"], "")
            if section == "links":
                src = links_by_url.get(item.get("url"), {})
                item.setdefault("title", src.get("title") or src.get("domain") or item.get("url"))
                item["domain"] = src.get("domain", "")
                item["sharers"] = src.get("sharers", [])
            for quote in item.get("quotes", []) or []:
                e = evidence.get(quote.get("msg_id"))
                if e:
                    quote["sender"] = e["sender"]
                    quote["time"] = e["time"]
    report["semantic"]["daily"] = sorted(report["semantic"].get("daily", []), key=lambda d: d.get("date", ""))
    return report


def _report_dirs(run_dir: Path, run_meta: dict[str, Any]) -> list[Path]:
    return [run_dir / r["key"] for r in run_meta["reports"]]


def _load(report_dir: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        material = json.loads((report_dir / "material.json").read_text(encoding="utf-8"))
        evidence = json.loads((report_dir / "evidence.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WSError("material_invalid", f"读取素材失败：{report_dir}：{exc}") from exc
    return material, evidence


def validate_run(run: str, semantic_path: str | None = None, *, strict_editorial: bool = False) -> tuple[dict[str, Any], int]:
    run_dir, run_meta = load_run(run)
    dirs = _report_dirs(run_dir, run_meta)
    if semantic_path and len(dirs) > 1:
        raise WSError("semantic_invalid", "separate 模式有多份报告，请把 semantic.json 分别放到各报告目录，不要用 --semantic")
    all_errors: list[str] = []
    all_warnings: list[str] = []
    written: list[str] = []
    pending: list[tuple[Path, dict[str, Any]]] = []
    for report_dir in dirs:
        material, evidence = _load(report_dir)
        path = Path(semantic_path).expanduser() if semantic_path else report_dir / "semantic.json"
        prefix = f"[{report_dir.name}] " if len(dirs) > 1 else ""
        if not path.exists():
            all_errors.append(f"{prefix}缺少 {path}")
            continue
        try:
            semantic = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            all_errors.append(f"{prefix}semantic.json 不是合法 JSON：{exc}")
            continue
        errors, warnings = validate_semantic(semantic, material, evidence)
        if strict_editorial and isinstance(semantic, dict) and not material["stats"]["empty"]:
            items = semantic.get("topics") or []
            if isinstance(items, list):
                for index, topic in enumerate(items):
                    if not isinstance(topic, dict):
                        continue
                    for field in ("background", "key_views", "debate", "conclusion", "conclusion_status"):
                        if not topic.get(field):
                            errors.append(f"topics[{index}] 缺少深度编辑字段 {field}（--strict-editorial）")
                    if topic.get("conclusion_status") in ("confirmed", "leaning") and not topic.get("conclusion"):
                        errors.append(f"topics[{index}] 结论状态必须有具体结论")
        all_errors += [prefix + e for e in errors]
        all_warnings += [prefix + w for w in warnings]
        if not errors:
            pending.append((report_dir, build_report(material, semantic, evidence)))
    if all_errors:
        return {"ok": False, "errors": all_errors, "warnings": all_warnings,
                "hint": "按 errors 逐条修正 semantic.json 后重新运行 validate"}, EXIT_FAIL
    for report_dir, report in pending:
        (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(str(report_dir / "report.json"))
    return {"ok": True, "reports": written, "warnings": all_warnings,
            "next": f"python wxsummary.py render --run \"{run_dir}\" --theme newspaper --format md,html,png"}, EXIT_OK


def finalize_run(run: str, *, fallback_stats: bool) -> dict[str, Any]:
    """定时任务用的宿主收尾：逐份报告生成 report.json。

    空窗口直接出统计版；semantic.json 校验通过出完整版；缺失或不通过时记录错误，
    fallback_stats=True 时该份降级为统计版（其他份不受影响）。
    返回 {"ok": 全部完整或空窗口, "errors": {key: [...]}, "degraded": [key], "reports": [...]}。
    """
    run_dir, run_meta = load_run(run)
    errors: dict[str, list[str]] = {}
    degraded: list[str] = []
    written: list[str] = []
    for report_dir in _report_dirs(run_dir, run_meta):
        material, evidence = _load(report_dir)
        semantic_file = report_dir / "semantic.json"
        if material["stats"]["empty"]:
            report = build_report(material, None, evidence)
        else:
            errs: list[str] = []
            semantic: Any = None
            if not semantic_file.exists():
                errs.append(f"缺少 {semantic_file}")
            else:
                try:
                    semantic = json.loads(semantic_file.read_text(encoding="utf-8-sig"))
                except ValueError as exc:
                    errs.append(f"semantic.json 不是合法 JSON：{exc}")
            if not errs:
                errs, _ = validate_semantic(semantic, material, evidence)
            if errs:
                errors[report_dir.name] = errs
                if not fallback_stats:
                    continue
                degraded.append(report_dir.name)
                report = build_report(material, None, evidence)
            else:
                report = build_report(material, semantic, evidence)
        path = report_dir / "report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(str(path))
    return {"ok": not errors, "errors": errors, "degraded": degraded, "reports": written}


def build_stats_only(run: str) -> list[Path]:
    run_dir, run_meta = load_run(run)
    paths = []
    for report_dir in _report_dirs(run_dir, run_meta):
        material, evidence = _load(report_dir)
        report = build_report(material, None, evidence)
        path = report_dir / "report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        paths.append(path)
    return paths
