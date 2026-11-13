"""数据源抽象基类。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, tzinfo
from typing import Any

from ..models import Chat, Member, Message


class Source(ABC):
    """所有数据源的统一接口（只读）。"""

    type_name = "abstract"

    def __init__(self, *, tz: tzinfo, me: dict[str, Any] | None = None):
        self.tz = tz
        self.me = me or {"ids": [], "names": []}

    # -- 上下文管理
    def __enter__(self) -> "Source":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:  # pragma: no cover - 默认无资源
        pass

    # -- 必须实现
    @abstractmethod
    def doctor(self) -> dict[str, Any]:
        """返回可读性与覆盖范围摘要（不得包含密钥）。"""

    @abstractmethod
    def list_chats(self, include_private: bool = False) -> list[Chat]:
        """列出会话；默认只返回群聊。"""

    @abstractmethod
    def members(self, chat_id: str) -> dict[str, Member]:
        """返回 {username: Member}，Member.display() 已按群昵称优先。"""

    @abstractmethod
    def messages(self, chat: Chat, since: datetime, until: datetime) -> list[Message]:
        """返回 [since, until) 内该群的标准化消息，按时间升序。"""

    @abstractmethod
    def latest_time(self) -> datetime | None:
        """数据源里最新一条消息的时间，用于判断镜像是否过旧。"""

    # -- 通用
    def is_me(self, username: str, display: str = "") -> bool:
        ids = {str(i) for i in self.me.get("ids") or []}
        names = {str(n) for n in self.me.get("names") or []}
        return bool(username and username in ids) or bool(display and display in names)
