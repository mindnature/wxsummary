"""群昵称解析：contact.db 表 chat_room 的 ext_buffer 是 protobuf。

每个重复的 field 1 是一个成员条目：1=username，2=群昵称，3=flags，4=邀请人。
这里只依赖标准库手写最小 protobuf 解码，遇到异常帧时保留已解析部分。
"""
from __future__ import annotations

from typing import Iterator


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, i
        shift += 7
        if shift > 63:
            break
    raise ValueError("truncated varint")


def iter_fields(buf: bytes) -> Iterator[tuple[int, int, object]]:
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 2:
            length, i = _varint(buf, i)
            if i + length > len(buf):
                raise ValueError("truncated bytes")
            value = buf[i:i + length]
            i += length
        elif wire == 5:
            value = buf[i:i + 4]
            i += 4
        elif wire == 1:
            value = buf[i:i + 8]
            i += 8
        else:
            raise ValueError(f"unsupported wire type {wire}")
        yield field, wire, value


def parse_chatroom_ext(blob: bytes | None) -> dict[str, str]:
    """返回 {username: 群昵称}；没有设置群昵称的成员值为空串。"""
    result: dict[str, str] = {}
    if not blob:
        return result
    try:
        for field, wire, value in iter_fields(bytes(blob)):
            if field != 1 or wire != 2:
                continue
            username = nickname = ""
            try:
                for sub_field, sub_wire, sub_value in iter_fields(value):  # type: ignore[arg-type]
                    if sub_wire != 2:
                        continue
                    if sub_field == 1:
                        username = bytes(sub_value).decode("utf-8", "replace").strip()  # type: ignore[arg-type]
                    elif sub_field == 2:
                        nickname = bytes(sub_value).decode("utf-8", "replace").strip()  # type: ignore[arg-type]
            except ValueError:
                continue
            if username:
                result[username] = nickname
    except ValueError:
        pass
    return result


def encode_chatroom_ext(members: dict[str, str]) -> bytes:
    """生成 ext_buffer（仅用于合成样例与测试）。"""

    def varint(n: int) -> bytes:
        out = bytearray()
        while True:
            b = n & 0x7F
            n >>= 7
            if n:
                out.append(b | 0x80)
            else:
                out.append(b)
                return bytes(out)

    def field(num: int, data: bytes) -> bytes:
        return varint((num << 3) | 2) + varint(len(data)) + data

    blob = bytearray()
    for username, nick in members.items():
        entry = field(1, username.encode("utf-8"))
        if nick:
            entry += field(2, nick.encode("utf-8"))
        entry += varint((3 << 3) | 0) + varint(0)
        blob += field(1, entry)
    return bytes(blob)
