"""标准化数据结构。所有数据源都必须产出这些对象。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# 标准消息类型
MESSAGE_TYPES = (
    "text", "image", "voice", "video", "emoji", "link", "file", "quote", "forward",
    "miniprogram", "channels", "location", "card", "pat", "revoke", "redpacket",
    "transfer", "call", "system", "unknown",
)

# 不进入 Agent 素材的类型（仍参与统计）
NON_MATERIAL_TYPES = frozenset({"system", "revoke", "pat", "emoji", "call", "unknown"})

TYPE_LABELS = {
    "text": "文本", "image": "图片", "voice": "语音", "video": "视频", "emoji": "表情",
    "link": "链接", "file": "文件", "quote": "引用回复", "forward": "合并转发",
    "miniprogram": "小程序", "channels": "视频号", "location": "位置", "card": "名片",
    "pat": "拍一拍", "revoke": "撤回", "redpacket": "红包", "transfer": "转账",
    "call": "通话", "system": "系统消息", "unknown": "其他",
}


@dataclass
class Member:
    username: str
    nick_name: str = ""
    remark: str = ""
    alias: str = ""
    group_nick: str = ""

    def display(self) -> str:
        """群昵称 > 备注 > 微信昵称 > ID。"""
        return self.group_nick or self.remark or self.nick_name or self.username


@dataclass
class Chat:
    id: str
    name: str
    remark: str = ""
    member_count: int | None = None
    last_time: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "remark": self.remark,
            "member_count": self.member_count,
            "last_time": self.last_time.isoformat(timespec="seconds") if self.last_time else None,
        }


@dataclass
class Message:
    msg_id: str
    chat_id: str
    chat_name: str
    sender_id: str
    sender_name: str
    timestamp: datetime
    type: str
    text: str
    extra: dict[str, Any] = field(default_factory=dict)
    raw_type: int | None = None
    server_id: str | None = None
    is_self: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "chat_id": self.chat_id,
            "chat_name": self.chat_name,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "timestamp": self.timestamp.isoformat(timespec="seconds"),
            "type": self.type,
            "text": self.text,
            "extra": self.extra,
            "raw_type": self.raw_type,
            "server_id": self.server_id,
            "is_self": self.is_self,
        }
