"""群聊选择：群名必须精确且唯一，否则停下让用户改用群 ID。"""
from __future__ import annotations

import unicodedata
from typing import Any

from .errors import WSError
from .models import Chat


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).strip()


def resolve_one(query: str, chats: list[Chat]) -> Chat:
    q = _norm(query)
    if not q:
        raise WSError("chat_not_found", "群名不能为空")
    by_id = [c for c in chats if c.id == q]
    if by_id:
        return by_id[0]
    exact = [c for c in chats if _norm(c.name) == q or (c.remark and _norm(c.remark) == q)]
    unique = {c.id: c for c in exact}
    if len(unique) == 1:
        return next(iter(unique.values()))
    if len(unique) > 1:
        raise WSError(
            "chat_ambiguous",
            f"“{query}”匹配到 {len(unique)} 个同名群",
            details=[c.to_dict() for c in unique.values()],
        )
    fuzzy = [c for c in chats if q.lower() in _norm(c.name).lower() or (c.remark and q.lower() in _norm(c.remark).lower())]
    raise WSError(
        "chat_not_found",
        f"没有名为“{query}”的群" + ("，但找到以下相近候选" if fuzzy else ""),
        details=[c.to_dict() for c in fuzzy[:10]] or None,
    )


def resolve_chats(
    chats: list[Chat],
    *,
    names: list[str],
    group_set: str | None,
    group_sets: dict[str, Any],
) -> tuple[list[Chat], str]:
    """返回 (群列表, 集合标签)。按输入顺序去重。"""
    queries: list[str] = []
    label = ""
    if group_set:
        if group_set not in group_sets:
            raise WSError("group_set_unknown", f"未定义的群组集合：{group_set}", details=sorted(group_sets))
        queries.extend(str(x) for x in group_sets[group_set])
        label = group_set
    queries.extend(names)
    if not queries:
        raise WSError("chat_not_found", "请用 --chats 或 --group-set 指定至少一个群")
    resolved: list[Chat] = []
    seen: set[str] = set()
    errors: list[dict[str, Any]] = []
    for q in queries:
        try:
            chat = resolve_one(q, chats)
        except WSError as exc:
            errors.append({"query": q, "code": exc.code, "message": exc.message, "candidates": exc.details})
            continue
        if chat.id not in seen:
            seen.add(chat.id)
            resolved.append(chat)
    if errors:
        first = errors[0]
        raise WSError(first["code"], "；".join(e["message"] for e in errors), details=errors)
    if not label:
        label = resolved[0].name if len(resolved) == 1 else f"{resolved[0].name}等{len(resolved)}群"
    return resolved, label
