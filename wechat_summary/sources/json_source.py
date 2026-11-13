"""统一消息 JSON 数据源（离线导入、测试、演示）。

格式：
{
  "chats": [{"id": "x@chatroom", "name": "群名", "member_count": 3,
             "members": [{"username": "wxid_a", "nick_name": "A", "remark": "", "group_nick": "阿A"}]}],
  "messages": [{"msg_id": "...", "chat_id": "x@chatroom", "sender_id": "wxid_a",
                "timestamp": "2026-10-20T09:00:00+08:00" 或 unix 秒,
                "type": "text", "text": "...", "extra": {}, "local_type": 1, "content": "原始内容（可选）"}]
}
若消息只给 local_type + content（原始微信格式），会走与真实库相同的解析器。
同时兼容旧版 Hub chat-history 的 messages 数组（字段 chat/sender/time/content）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import WSError
from ..models import MESSAGE_TYPES, Chat, Member, Message
from ..parsing import URL_RE, RawRow, parse_row
from .base import Source


def _parse_time(value: Any, tz) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(tz)
    text = str(value).strip().replace("Z", "+00:00")
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc).astimezone(tz)
    dt = datetime.fromisoformat(text.replace(" ", "T", 1) if "T" not in text and " " in text else text)
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


class JsonSource(Source):
    type_name = "json"

    def __init__(self, path: str, *, tz, me=None):
        super().__init__(tz=tz, me=me)
        self.path = Path(path).expanduser()
        if not self.path.is_file():
            raise WSError("source_not_found", f"JSON 数据源不存在：{self.path}")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise WSError("database_unreadable", f"JSON 解析失败：{exc}") from exc
        if isinstance(data, list):
            data = {"messages": data}
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            raise WSError("schema_unrecognized", "JSON 需包含 messages 数组")
        self._chats: dict[str, Chat] = {}
        self._members: dict[str, dict[str, Member]] = {}
        self._raw_messages: list[dict[str, Any]] = data["messages"]
        for c in data.get("chats") or []:
            chat_id = str(c.get("id") or c.get("chat_id") or c.get("name"))
            self._chats[chat_id] = Chat(id=chat_id, name=str(c.get("name") or chat_id), remark=str(c.get("remark") or ""),
                                        member_count=c.get("member_count"))
            self._members[chat_id] = {
                str(m["username"]): Member(
                    username=str(m["username"]),
                    nick_name=str(m.get("nick_name") or ""),
                    remark=str(m.get("remark") or ""),
                    alias=str(m.get("alias") or ""),
                    group_nick=str(m.get("group_nick") or ""),
                )
                for m in c.get("members") or []
                if m.get("username")
            }
        # 兼容只有 messages 的旧格式：从消息里推出群
        for m in self._raw_messages:
            chat_id = str(m.get("chat_id") or m.get("chat") or m.get("talker") or "")
            if chat_id and chat_id not in self._chats:
                name = str(m.get("chat_name") or m.get("chat") or chat_id)
                self._chats[chat_id] = Chat(id=chat_id, name=name)
                self._members.setdefault(chat_id, {})
        self._parsed: dict[str, list[Message]] = {}

    def _parse_all(self, chat: Chat) -> list[Message]:
        if chat.id in self._parsed:
            return self._parsed[chat.id]
        members = self._members.get(chat.id, {})
        result: list[Message] = []
        for i, m in enumerate(self._raw_messages):
            chat_id = str(m.get("chat_id") or m.get("chat") or m.get("talker") or "")
            if chat_id != chat.id:
                continue
            ts = _parse_time(m.get("timestamp", m.get("time", m.get("create_time", 0))), self.tz)
            sender_id = str(m.get("sender_id") or m.get("sender_username") or m.get("sender") or "")
            if "local_type" in m and "content" in m and "type" not in m:
                raw = RawRow(
                    shard="json", local_id=i, server_id=m.get("server_id"), local_type=int(m["local_type"]),
                    create_time=int(ts.timestamp()), content=m.get("content"), compress_flag=None,
                    real_sender=sender_id or None, status=None,
                )
                msg = parse_row(raw, chat=chat, tz=self.tz, members=members, contacts={}, self_wxid="", me=self.me)
                if m.get("msg_id"):
                    msg.msg_id = str(m["msg_id"])
                result.append(msg)
                continue
            mtype = str(m.get("type") or "text")
            if mtype not in MESSAGE_TYPES:
                mtype = "text" if mtype in ("文本", "") else "unknown"
            member = members.get(sender_id)
            name = member.display() if member else str(m.get("sender_name") or m.get("sender") or sender_id)
            text = str(m.get("text") if m.get("text") is not None else m.get("content") or "")
            extra = dict(m.get("extra") or {})
            if mtype in ("text", "quote") and "urls" not in extra:
                urls = URL_RE.findall(text)
                if urls:
                    extra["urls"] = urls
            msg_id = str(m.get("msg_id") or m.get("id") or "")
            if not msg_id:
                digest = hashlib.sha1(f"{chat.id}|{ts.isoformat()}|{sender_id}|{text}".encode("utf-8")).hexdigest()[:10]
                msg_id = f"j{digest}"
            result.append(
                Message(
                    msg_id=msg_id,
                    chat_id=chat.id,
                    chat_name=chat.name,
                    sender_id=sender_id or name,
                    sender_name=name,
                    timestamp=ts,
                    type=mtype,
                    text=text,
                    extra=extra,
                    raw_type=m.get("local_type"),
                    is_self=self.is_me(sender_id, name),
                )
            )
        result.sort(key=lambda x: (x.timestamp, x.msg_id))
        self._parsed[chat.id] = result
        return result

    def list_chats(self, include_private: bool = False) -> list[Chat]:
        chats = []
        for chat in self._chats.values():
            if not include_private and not chat.id.endswith("@chatroom") and self._looks_private(chat):
                continue
            msgs = self._parse_all(chat)
            chat.last_time = msgs[-1].timestamp if msgs else None
            if chat.member_count is None and self._members.get(chat.id):
                chat.member_count = len(self._members[chat.id])
            chats.append(chat)
        return chats

    @staticmethod
    def _looks_private(chat: Chat) -> bool:
        # JSON 源里非 @chatroom 的 ID 可能是旧数据用群名充当 ID，默认仍视为群
        return chat.id.startswith("wxid_")

    def members(self, chat_id: str) -> dict[str, Member]:
        return self._members.get(chat_id, {})

    def messages(self, chat: Chat, since: datetime, until: datetime) -> list[Message]:
        return [m for m in self._parse_all(chat) if since <= m.timestamp < until]

    def latest_time(self) -> datetime | None:
        latest = None
        for chat in self._chats.values():
            msgs = self._parse_all(chat)
            if msgs and (latest is None or msgs[-1].timestamp > latest):
                latest = msgs[-1].timestamp
        return latest

    def doctor(self) -> dict[str, Any]:
        all_msgs = [m for c in self._chats.values() for m in self._parse_all(c)]
        earliest = min((m.timestamp for m in all_msgs), default=None)
        latest = max((m.timestamp for m in all_msgs), default=None)
        return {
            "ok": True,
            "type": self.type_name,
            "root": str(self.path),
            "contacts": sum(len(v) for v in self._members.values()),
            "groups": len(self._chats),
            "messages": len(all_msgs),
            "earliest": earliest.isoformat(timespec="seconds") if earliest else None,
            "latest": latest.isoformat(timespec="seconds") if latest else None,
            "warnings": [],
        }
