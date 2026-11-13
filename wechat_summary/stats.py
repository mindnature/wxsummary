"""确定性统计：所有数字由程序计算，不交给模型。"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
from urllib.parse import urlsplit

from .models import TYPE_LABELS, Message
from .window import Window

# 不计入"发言"的类型
NON_SPEECH = frozenset({"system", "revoke", "pat"})
TRACKING_PARAMS = ("utm_", "spm", "from=", "scene=", "share_", "chksm=", "sessionid", "clicktime", "enterid")


def _clean_url(url: str) -> str:
    url = url.rstrip(".,;:!?)）】」")
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [p for p in parts.query.split("&") if p and not p.lower().startswith(TRACKING_PARAMS)]
    query = "&".join(kept)
    return parts._replace(query=query).geturl()


def collect_links(messages: list[Message]) -> list[dict[str, Any]]:
    """链接清单：按 URL 去重，记录首次出现、分享次数和分享人。"""
    links: dict[str, dict[str, Any]] = {}
    for m in messages:
        candidates: list[tuple[str, str]] = []
        if m.type == "link" and m.extra.get("url"):
            candidates.append((m.extra["url"], m.extra.get("title") or ""))
        for u in m.extra.get("urls") or []:
            candidates.append((u, ""))
        for raw_url, title in candidates:
            url = _clean_url(raw_url)
            entry = links.get(url)
            if entry is None:
                links[url] = {
                    "url": url,
                    "title": title,
                    "domain": urlsplit(url).netloc,
                    "source": m.extra.get("source", ""),
                    "first_msg_id": m.msg_id,
                    "first_time": m.timestamp.isoformat(timespec="seconds"),
                    "chat_id": m.chat_id,
                    "chat_name": m.chat_name,
                    "sharers": [m.sender_name],
                    "share_count": 1,
                    "msg_ids": [m.msg_id],
                }
            else:
                entry["share_count"] += 1
                entry["msg_ids"].append(m.msg_id)
                if m.sender_name not in entry["sharers"]:
                    entry["sharers"].append(m.sender_name)
                if title and not entry["title"]:
                    entry["title"] = title
    return sorted(links.values(), key=lambda x: (-x["share_count"], x["first_time"]))


def most_quoted(messages: list[Message], limit: int = 5) -> list[dict[str, Any]]:
    """被引用最多的消息：用 server_id 精确匹配，匹配不到时按 (发送者, 文本) 聚合。"""
    by_server = {m.server_id: m for m in messages if m.server_id}
    counts: Counter[str] = Counter()
    info: dict[str, dict[str, Any]] = {}
    for m in messages:
        q = m.extra.get("quote") if m.type == "quote" else None
        if not q:
            continue
        target = by_server.get(str(q.get("svrid") or ""))
        if target:
            key = target.msg_id
            info.setdefault(key, {"msg_id": target.msg_id, "sender": target.sender_name, "text": target.text[:200],
                                  "time": target.timestamp.isoformat(timespec="seconds"), "chat_name": target.chat_name})
        else:
            key = f"{q.get('sender_name')}|{q.get('text')}"
            info.setdefault(key, {"msg_id": None, "sender": q.get("sender_name") or "", "text": (q.get("text") or "")[:200],
                                  "time": None, "chat_name": m.chat_name})
        counts[key] += 1
        info[key].setdefault("quoted_by", [])
        info[key]["quoted_by"].append(m.msg_id)
    return [dict(info[k], count=c) for k, c in counts.most_common(limit)]


def compute_stats(messages: list[Message], window: Window, *, top_n: int = 10) -> dict[str, Any]:
    speech = [m for m in messages if m.type not in NON_SPEECH]
    senders = Counter(m.sender_name for m in speech)
    sender_chars: Counter[str] = Counter()
    for m in speech:
        if m.type in ("text", "quote"):
            sender_chars[m.sender_name] += len(m.text)
    hourly = [0] * 24
    for m in speech:
        hourly[m.timestamp.hour] += 1
    types = Counter(m.type for m in messages)
    daily_counts: dict[str, dict[str, Any]] = {d.isoformat(): {"date": d.isoformat(), "messages": 0, "senders": set()} for d in window.days}
    for m in speech:
        key = m.timestamp.date().isoformat()
        if key in daily_counts:
            daily_counts[key]["messages"] += 1
            daily_counts[key]["senders"].add(m.sender_id)
    daily = [{"date": v["date"], "weekday": _weekday(v["date"]), "messages": v["messages"], "senders": len(v["senders"])}
             for v in daily_counts.values()]
    peak_hour = max(range(24), key=lambda h: hourly[h]) if speech else None
    peak_day = max(daily, key=lambda d: d["messages"]) if speech and daily else None
    links = collect_links(messages)
    total_speech = len(speech)
    top = [
        {"name": name, "messages": n, "share": round(n / total_speech, 4) if total_speech else 0, "chars": sender_chars.get(name, 0)}
        for name, n in senders.most_common(top_n)
    ]
    return {
        "message_count": len(messages),
        "speech_count": total_speech,
        "sender_count": len(senders),
        "text_chars": sum(sender_chars.values()),
        "top_senders": top,
        "hourly": hourly,
        "peak_hour": peak_hour,
        "daily": daily,
        "peak_day": peak_day,
        "types": [{"type": t, "label": TYPE_LABELS.get(t, t), "count": n} for t, n in types.most_common()],
        "links": links,
        "link_count": len(links),
        "most_quoted": most_quoted(messages),
        "first_time": messages[0].timestamp.isoformat(timespec="seconds") if messages else None,
        "last_time": messages[-1].timestamp.isoformat(timespec="seconds") if messages else None,
        "empty": total_speech == 0,
    }


def _weekday(iso_date: str) -> str:
    from datetime import date

    return "一二三四五六日"[date.fromisoformat(iso_date).weekday()]


def compute_group_breakdown(by_chat: dict[str, list[Message]], chats_meta: dict[str, dict[str, Any]], window: Window) -> list[dict[str, Any]]:
    """多群合并报告中的分群概览。"""
    rows = []
    for chat_id, msgs in by_chat.items():
        s = compute_stats(msgs, window, top_n=3)
        rows.append({
            "chat_id": chat_id,
            "chat_name": chats_meta[chat_id]["name"],
            "messages": s["speech_count"],
            "senders": s["sender_count"],
            "links": s["link_count"],
            "top_senders": s["top_senders"],
            "peak_hour": s["peak_hour"],
        })
    return sorted(rows, key=lambda r: -r["messages"])


def mentions_of_me(messages: list[Message], me_names: list[str], me_ids: list[str]) -> list[str]:
    """提及我、回复我、引用我的消息 ID。"""
    names = [n for n in me_names if n]
    ids = set(i for i in me_ids if i)
    hits: list[str] = []
    for m in messages:
        if m.is_self or m.type in NON_SPEECH:
            continue
        q = m.extra.get("quote") if m.extra else None
        quoted_me = bool(q) and (q.get("sender_id") in ids or q.get("sender_name") in names)
        at_me = any(f"@{n}" in m.text for n in names) or "@所有人" in m.text
        if quoted_me or at_me:
            hits.append(m.msg_id)
    return hits


def group_by(messages: list[Message], key) -> dict[str, list[Message]]:
    out: dict[str, list[Message]] = defaultdict(list)
    for m in messages:
        out[key(m)].append(m)
    return dict(out)
