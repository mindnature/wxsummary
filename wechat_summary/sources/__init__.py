"""数据源适配器。

适配器只负责把底层存储读成 Chat / Member / 原始行；消息语义解析在 parsing.py。
"""
from __future__ import annotations

from typing import Any

from ..errors import WSError
from .base import Source


def open_source(
    config: dict[str, Any],
    *,
    source_dir: str | None = None,
    source_json: str | None = None,
    account: str | None = None,
) -> Source:
    """按配置（命令行参数优先）打开数据源。"""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(config.get("timezone") or "Asia/Shanghai")
    src = config.get("source") or {}
    me = config.get("me") or {}
    if source_json:
        from .json_source import JsonSource

        return JsonSource(source_json, tz=tz, me=me)
    if source_dir:
        from .decrypted_dir import DecryptedDirSource

        return DecryptedDirSource(source_dir, tz=tz, me=me, account=account or src.get("account") or "", paths={})
    kind = (src.get("type") or "decrypted_dir").lower()
    if kind == "json":
        from .json_source import JsonSource

        if not src.get("path"):
            raise WSError("source_not_configured", "source.type=json 但未填写 source.path")
        return JsonSource(src["path"], tz=tz, me=me)
    if kind == "decrypted_dir":
        from .decrypted_dir import DecryptedDirSource

        if not src.get("path") and not (src.get("paths") or {}):
            raise WSError("source_not_configured", "未配置解密镜像目录 source.path")
        return DecryptedDirSource(
            src.get("path") or "",
            tz=tz,
            me=me,
            account=account or src.get("account") or "",
            paths=src.get("paths") or {},
        )
    raise WSError("config_invalid", f"不支持的 source.type：{kind}（可选 decrypted_dir / json）")


__all__ = ["Source", "open_source"]
