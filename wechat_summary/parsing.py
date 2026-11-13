"""微信 4.x 原始消息行 -> 标准化 Message。

要点：
- message_content 可能是 str、bytes 或 zstd 压缩 bytes（魔数 28 b5 2f fd，或 WCDB_CT 标记为 4）。
- 群消息正文常带 "wxid_xxx:\\n" 前缀；有 real_sender_id 时以它为准，否则从前缀拆。
- local_type 高位可能带子类型，取低 32 位做主类型；type 49 再按 XML <appmsg><type> 细分。
"""
from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .errors import WSError
from .models import Chat, Member, Message

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
SENDER_PREFIX_RE = re.compile(r"^([A-Za-z0-9_\-@.]{3,64}):\s*\n", re.S)
URL_RE = re.compile(r"https?://[^\s<>\"'，。！？、）)】\]]+", re.I)

BASE_TYPES = {
    1: "text", 3: "image", 34: "voice", 42: "card", 43: "video", 47: "emoji",
    48: "location", 50: "call", 10000: "system", 10002: "system",
}

APPMSG_TYPES = {
    "1": "text", "3": "link", "4": "link", "5": "link", "6": "file", "8": "emoji",
    "17": "location", "19": "forward", "24": "link", "33": "miniprogram", "36": "miniprogram",
    "40": "forward", "51": "channels", "53": "text", "57": "quote", "62": "pat",
    "63": "channels", "74": "file", "87": "system", "2000": "transfer", "2001": "redpacket",
}

PLACEHOLDER = {
    "image": "[图片]", "voice": "[语音]", "video": "[视频]", "emoji": "[表情]",
    "location": "[位置]", "card": "[名片]", "call": "[通话]", "redpacket": "[红包]",
    "transfer": "[转账]", "unknown": "[未识别消息]",
}


@dataclass
class RawRow:
    shard: str
    local_id: int
    server_id: str | None
    local_type: int
    create_time: int
    content: Any
    compress_flag: Any = None
    real_sender: str | None = None
    status: Any = None


# ---------------------------------------------------------------- 解码
def zstd_decompress(data: bytes) -> bytes:
    try:
        from compression import zstd  # Python 3.14+

        return zstd.decompress(data)
    except ImportError:
        pass
    try:
        import zstandard  # type: ignore

        return zstandard.ZstdDecompressor().decompress(data, max_output_size=64 * 1024 * 1024)
    except ImportError as exc:
        raise WSError("zstd_unavailable", "需要 zstd 解压能力") from exc


def zstd_compress(data: bytes) -> bytes:
    """仅用于合成样例与测试。"""
    try:
        from compression import zstd

        return zstd.compress(data)
    except ImportError:
        import zstandard  # type: ignore

        return zstandard.ZstdCompressor().compress(data)


def _salvage_text(data: bytes) -> str:
    for offset in range(min(16, len(data))):
        chunk = data[offset:].split(b"\x00", 1)[0]
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            continue
        text = "".join(ch for ch in text if ch in "\r\n\t" or ord(ch) >= 32).strip()
        if text and sum(ch.isprintable() or ch in "\r\n\t" for ch in text) / len(text) >= 0.85:
            return text
    return ""


def decode_content(content: Any, compress_flag: Any = None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, memoryview):
        content = content.tobytes()
    if isinstance(content, (bytes, bytearray)):
        data = bytes(content)
        if data[:4] == ZSTD_MAGIC or str(compress_flag) == "4":
            try:
                data = zstd_decompress(data)
            except WSError:
                raise
            except Exception:
                return _salvage_text(data)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", "replace")
    return str(content)


def split_sender(text: str, is_group: bool) -> tuple[str | None, str]:
    if not is_group:
        return None, text
    m = SENDER_PREFIX_RE.match(text)
    if m:
        return m.group(1), text[m.end():]
    return None, text


# ---------------------------------------------------------------- XML 工具
BARE_AMP_RE = re.compile(r"&(?!(?:[A-Za-z]+|#\d+|#x[0-9A-Fa-f]+);)")


def _xml_root(text: str) -> ET.Element | None:
    start = text.find("<")
    if start < 0:
        return None
    body = text[start:]
    body = re.sub(r"^<\?xml[^>]*\?>", "", body).strip()
    for candidate in (body, BARE_AMP_RE.sub("&amp;", body)):
        try:
            return ET.fromstring(candidate)
        except ET.ParseError:
            try:
                return ET.fromstring(f"<root>{candidate}</root>")
            except ET.ParseError:
                continue
    return None


def _find_text(node: ET.Element | None, path: str) -> str:
    if node is None:
        return ""
    found = node.find(path)
    return html.unescape((found.text or "").strip()) if found is not None and found.text else ""


def _plain(text: str, limit: int = 400) -> str:
    text = re.sub(r"<[^>]+>", "", html.unescape(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


# ---------------------------------------------------------------- 系统消息
SYSTEM_RULES = (
    ("pat", ("拍了拍",)),
    ("revoke", ("撤回了一条消息", "撤回了")),
    ("redpacket", ("红包",)),
    ("transfer", ("转账", "收款")),
)


def _system_text(text: str) -> str:
    root = _xml_root(text) if "<" in text else None
    if root is not None:
        for tag in ("replacemsg", "template", "content", "title", "plain"):
            for node in root.iter(tag):
                if node.text and node.text.strip():
                    value = html.unescape(node.text.strip())
                    fields = {n.tag: (n.text or "") for n in root.iter() if len(n) == 0}
                    value = re.sub(r"\$\{([A-Za-z0-9_]+)\}", lambda m: fields.get(m.group(1), m.group(0)), value)
                    return _plain(value)
        return _plain(ET.tostring(root, encoding="unicode", method="text"))
    return _plain(text)


def _classify_system(text: str, raw_type: int) -> str:
    if raw_type == 10002:
        root = _xml_root(text)
        if (root is not None and root.attrib.get("type") == "pat") or "拍了拍" in text:
            return "pat"
        return "revoke"
    for kind, needles in SYSTEM_RULES:
        if any(n in text for n in needles):
            return kind
    return "system"


# ---------------------------------------------------------------- appmsg
def _parse_appmsg(text: str) -> tuple[str, str, dict[str, Any]]:
    root = _xml_root(text)
    if root is None:
        return "unknown", "[无法解析的卡片消息]", {}
    appmsg = root if root.tag == "appmsg" else root.find(".//appmsg")
    if appmsg is None:
        return "unknown", "[无法解析的卡片消息]", {}
    sub = _find_text(appmsg, "type")
    kind = APPMSG_TYPES.get(sub, "link" if _find_text(appmsg, "url") else "unknown")
    title = _plain(_find_text(appmsg, "title"), 200)
    des = _plain(_find_text(appmsg, "des"), 300)
    url = _find_text(appmsg, "url")
    extra: dict[str, Any] = {"appmsg_type": sub}
    source = _find_text(appmsg, "sourcedisplayname") or _find_text(root, ".//appinfo/appname")
    if source:
        extra["source"] = _plain(source, 60)

    if kind == "quote":
        refer = appmsg.find("refermsg")
        quoted_content = _find_text(refer, "content")
        quoted_type = _find_text(refer, "type")
        if quoted_content and quoted_type == "49":
            _, quoted_text, _ = _parse_appmsg(quoted_content)
        elif quoted_type in ("3", "43", "34", "47"):
            quoted_text = PLACEHOLDER.get(BASE_TYPES.get(int(quoted_type), "unknown"), "[消息]")
        else:
            _, quoted_text = split_sender(quoted_content, True)
            quoted_text = _plain(quoted_text, 200)
        extra["quote"] = {
            "sender_id": _find_text(refer, "chatusr") or _find_text(refer, "fromusr"),
            "sender_name": _plain(_find_text(refer, "displayname"), 60),
            "text": quoted_text,
            "svrid": _find_text(refer, "svrid"),
        }
        return "quote", title, extra
    if kind == "link":
        if url:
            extra["url"] = url
        extra["title"] = title
        if des:
            extra["description"] = des
        return "link", title or url or "[链接]", extra
    if kind == "file":
        extra["file_name"] = title
        ext = _find_text(appmsg, "appattach/fileext")
        if ext:
            extra["file_ext"] = ext
        size = _find_text(appmsg, "appattach/totallen")
        if size.isdigit():
            extra["file_size"] = int(size)
        return "file", f"[文件] {title}", extra
    if kind == "forward":
        extra["title"] = title
        if des:
            extra["description"] = des
        return "forward", f"[聊天记录] {title}" + (f"：{des[:120]}" if des else ""), extra
    if kind == "miniprogram":
        extra["title"] = title
        return "miniprogram", f"[小程序] {title}", extra
    if kind == "channels":
        finder = appmsg.find(".//finderFeed")
        desc = _plain(_find_text(finder, "desc"), 200) if finder is not None else ""
        nick = _plain(_find_text(finder, "nickname"), 60) if finder is not None else ""
        extra["title"] = desc or title
        if nick:
            extra["author"] = nick
        return "channels", f"[视频号] {nick} {desc or title}".strip(), extra
    if kind == "pat":
        return "pat", title or "[拍一拍]", extra
    if kind == "transfer":
        fee = _find_text(appmsg, "wcpayinfo/feedesc")
        if fee:
            extra["amount"] = fee
        return "transfer", f"[转账] {fee}".strip(), extra
    if kind == "redpacket":
        return "redpacket", "[红包] " + (_find_text(appmsg, "wcpayinfo/receivertitle") or title), extra
    if kind == "text":
        return "text", title, extra
    if kind == "location":
        return "location", f"[位置] {title}", extra
    if kind == "system":
        return "system", title, extra
    return "unknown", title or "[卡片消息]", extra


# ---------------------------------------------------------------- 主入口
def normalize_type(local_type: int) -> int:
    """微信 4.x 的 local_type 可能把子类型放在高 32 位（如 49 | (57<<32)），取低 32 位；
    低 32 位仍超出已知范围时再尝试低 16 位。"""
    t = int(local_type or 0)
    low32 = t & 0xFFFFFFFF
    if low32 in BASE_TYPES or low32 == 49:
        return low32
    low16 = t & 0xFFFF
    if low16 in BASE_TYPES or low16 == 49:
        return low16
    return low32


def stable_msg_id(chat_id: str, raw: RawRow) -> str:
    if raw.server_id:
        seed = f"s:{raw.server_id}"
    else:
        seed = f"l:{chat_id}:{raw.shard}:{raw.local_id}:{raw.create_time}"
    return "m" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]


def resolve_name(username: str, members: dict[str, Member], contacts: dict[str, dict[str, str]]) -> str:
    member = members.get(username)
    if member:
        return member.display()
    c = contacts.get(username)
    if c:
        return c.get("remark") or c.get("nick_name") or username
    return username


def parse_row(
    raw: RawRow,
    *,
    chat: Chat,
    tz,
    members: dict[str, Member],
    contacts: dict[str, dict[str, str]],
    self_wxid: str,
    me: dict[str, Any],
) -> Message:
    is_group = chat.id.endswith("@chatroom")
    text = decode_content(raw.content, raw.compress_flag)
    prefix_sender, body = split_sender(text, is_group)
    sender_id = raw.real_sender or prefix_sender or ""
    main_type = normalize_type(raw.local_type)
    extra: dict[str, Any] = {}

    if main_type == 49:
        mtype, display, extra = _parse_appmsg(body)
        quote = extra.get("quote")
        if quote and quote.get("sender_id"):
            resolved = resolve_name(quote["sender_id"], members, contacts)
            if resolved != quote["sender_id"]:
                quote["sender_name"] = resolved
    elif main_type in (10000, 10002):
        display = _system_text(body)
        mtype = _classify_system(body, main_type)
        sender_id = sender_id if mtype == "pat" else ""
    elif main_type == 1:
        mtype, display = "text", body.strip()
    elif main_type in BASE_TYPES:
        mtype = BASE_TYPES[main_type]
        display = PLACEHOLDER.get(mtype, f"[{mtype}]")
        if mtype == "voice":
            root = _xml_root(body)
            length = root.find(".//voicemsg") if root is not None else None
            if length is not None and str(length.attrib.get("voicelength", "")).isdigit():
                seconds = round(int(length.attrib["voicelength"]) / 1000)
                extra["duration_seconds"] = seconds
                display = f"[语音 {seconds}秒]"
        elif mtype == "location":
            root = _xml_root(body)
            loc = root.find(".//location") if root is not None else None
            if loc is not None:
                label = loc.attrib.get("poiname") or loc.attrib.get("label") or ""
                if label:
                    extra["label"] = label
                    display = f"[位置] {label}"
    else:
        mtype, display = "unknown", PLACEHOLDER["unknown"]
        extra["raw_type"] = raw.local_type

    if mtype in ("text", "quote"):
        urls = URL_RE.findall(display)
        if urls:
            extra.setdefault("urls", urls)

    is_self = bool(sender_id) and (sender_id == self_wxid or sender_id in {str(i) for i in me.get("ids") or []})
    if not is_group and not sender_id:
        # 私聊里 real_sender 缺失时无法判断方向，退化为对方
        sender_id = chat.id
    if mtype in ("system", "revoke") and not sender_id:
        sender_name = "系统"
    else:
        sender_name = resolve_name(sender_id, members, contacts) if sender_id else "未知成员"
    if not is_self and me.get("names") and sender_name in {str(n) for n in me.get("names")}:
        is_self = True

    ts = datetime.fromtimestamp(raw.create_time, tz=timezone.utc).astimezone(tz)
    return Message(
        msg_id=stable_msg_id(chat.id, raw),
        chat_id=chat.id,
        chat_name=chat.name,
        sender_id=sender_id or "system",
        sender_name=sender_name,
        timestamp=ts,
        type=mtype,
        text=display,
        extra=extra,
        raw_type=raw.local_type,
        server_id=raw.server_id,
        is_self=is_self,
    )


def disambiguate_names(messages: list[Message]) -> list[str]:
    """同一显示名对应多个发送者时追加 ID 尾号，返回发生消歧的名字。"""
    owners: dict[str, set[str]] = {}
    for m in messages:
        if m.sender_id != "system":
            owners.setdefault(m.sender_name, set()).add(m.sender_id)
    clashes = {name for name, ids in owners.items() if len(ids) > 1}
    for m in messages:
        if m.sender_name in clashes:
            m.sender_name = f"{m.sender_name}#{m.sender_id[-4:]}"
        quote = m.extra.get("quote") if m.extra else None
        if quote and quote.get("sender_name") in clashes and quote.get("sender_id"):
            quote["sender_name"] = f"{quote['sender_name']}#{quote['sender_id'][-4:]}"
    return sorted(clashes)
