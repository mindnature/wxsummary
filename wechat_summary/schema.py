"""semantic.json 的结构定义：写作说明、校验、渲染共用这一份。

约定：
- 每条结论通过 evidence: ["m1a2b3c4d5", ...] 绑定 material.json 里的 msg_id，至少 1 个。
- chat_id 可选；多群合并报告中建议填写，校验会确认证据确实来自该群。
- quotes 是"关键原话"，必须逐字来自证据消息（校验会做包含检查）。
"""
from __future__ import annotations

from typing import Any

SEMANTIC_VERSION = 1

# 字段规格：(字段名, 类型, 是否必填, 说明)
ITEM_SPECS: dict[str, dict[str, Any]] = {
    "topics": {
        "label": "主线话题",
        "min": 1,
        "max": 8,
        "evidence": True,
        "fields": {
            "title": ("str", True, "结论式标题，12-28 字，说清发生了什么或得出了什么，不要写成『关于 X 的讨论』"),
            "summary": ("str", True, "2-4 句，像看完全程的群友在讲：谁提出、有什么分歧、最后落在哪"),
            "points": ("list[str]", False, "2-5 条要点，每条一句"),
            "quotes": ("list[quote]", False, "1-3 句关键原话，quote={msg_id, text}，text 必须是原文片段"),
            "people": ("list[str]", False, "主要参与者显示名"),
            "chat_id": ("str", False, "多群时标注话题来自哪个群"),
            "tags": ("list[str]", False, "1-3 个短标签"),
        },
    },
    "insights": {
        "label": "观点与分歧",
        "min": 0,
        "max": 6,
        "evidence": True,
        "fields": {
            "claim": ("str", True, "一句话观点或判断"),
            "stance": ("str", False, "consensus 共识 | debate 分歧 | insight 洞见 | warning 风险"),
            "detail": ("str", False, "补充说明，包括反方意见"),
            "chat_id": ("str", False, "多群时标注来源群"),
        },
    },
    "follow_ups": {
        "label": "值得跟进",
        "min": 0,
        "max": 8,
        "evidence": True,
        "fields": {
            "title": ("str", True, "要跟进的事"),
            "owner": ("str", False, "谁在负责/谁提出"),
            "due": ("str", False, "时间点（只写聊天里出现过的）"),
            "why": ("str", False, "为什么值得跟进"),
            "chat_id": ("str", False, "多群时标注来源群"),
        },
    },
    "for_me": {
        "label": "与我相关",
        "min": 0,
        "max": 8,
        "evidence": True,
        "fields": {
            "title": ("str", True, "需要我知道或处理的事"),
            "action": ("str", False, "建议动作：回复 / 确认 / 阅读 / 知悉"),
            "chat_id": ("str", False, "多群时标注来源群"),
        },
    },
    "links": {
        "label": "精选链接",
        "min": 0,
        "max": 10,
        "evidence": True,
        "fields": {
            "url": ("str", True, "必须来自 material.stats.links"),
            "title": ("str", False, "链接标题"),
            "note": ("str", True, "一句话说明为什么值得点开"),
        },
    },
    "daily": {
        "label": "每日要点",
        "min": 0,
        "max": 31,
        "evidence": True,
        "fields": {
            "date": ("str", True, "YYYY-MM-DD，必须在报告范围内"),
            "headline": ("str", True, "当天一句话"),
            "points": ("list[str]", False, "1-4 条当天要点"),
        },
    },
}

TOP_FIELDS: dict[str, tuple[str, bool, str]] = {
    "version": ("int", True, f"固定为 {SEMANTIC_VERSION}"),
    "title": ("str", False, "报告主标题（可选，不填则用『群名 + 周期』）"),
    "one_liner": ("str", True, "一句话总结整期，30-60 字，有判断不空泛"),
    "mood": ("str", False, "群聊氛围，2-6 字，如『热烈但务实』"),
    "editor_note": ("str", False, "编者按，1-3 句，可写本期观察或数据局限"),
}

STANCE_VALUES = {"consensus", "debate", "insight", "warning"}
STANCE_LABELS = {"consensus": "共识", "debate": "分歧", "insight": "洞见", "warning": "风险"}


def schema_for_period(period: str) -> dict[str, Any]:
    specs = {k: dict(v) for k, v in ITEM_SPECS.items()}
    if period in ("weekly", "monthly", "custom"):
        specs["daily"]["min"] = 1
    else:
        specs["daily"]["max"] = 0
    return specs


def example_semantic(period: str, sample_ids: list[str], sample_url: str | None, sample_date: str | None) -> dict[str, Any]:
    ev = sample_ids[:2] or ["<msg_id>"]
    example: dict[str, Any] = {
        "version": SEMANTIC_VERSION,
        "one_liner": "一句有判断的总结：今天群里真正发生了什么。",
        "mood": "务实",
        "topics": [
            {
                "title": "结论式标题：谁做了什么 / 得出了什么",
                "summary": "谁提出了什么，谁给了反例，最后落在哪。",
                "points": ["要点一", "要点二"],
                "quotes": [{"msg_id": ev[0], "text": "从该消息原文里逐字摘的一句"}],
                "people": ["发言人A", "发言人B"],
                "evidence": ev,
            }
        ],
        "insights": [{"claim": "一句话观点", "stance": "debate", "detail": "正反两方各自理由", "evidence": ev[:1]}],
        "follow_ups": [{"title": "要跟进的事", "owner": "某人", "evidence": ev[:1]}],
        "for_me": [],
        "links": ([{"url": sample_url, "note": "为什么值得点开", "evidence": ev[:1]}] if sample_url else []),
    }
    if period in ("weekly", "monthly", "custom"):
        example["daily"] = [{"date": sample_date or "YYYY-MM-DD", "headline": "当天一句话", "points": ["要点"], "evidence": ev[:1]}]
    return example
