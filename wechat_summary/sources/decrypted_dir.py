"""读取外部工具生成的微信 4.x 明文镜像（只读）。

支持的布局：
- weinanhu：WeinanHu/wechat-decrypt 的 decrypted/<wxid_device>/db_storage/{contact,session,message}/*.db
- flat：WeChatDataAnalysis / wechat4-db-decryptor 的 databases/<account>/*.db（库文件平铺）
- explicit：配置 source.paths 显式给出 contact / session / message_dir（或 messages 列表）

所有连接都以 SQLite URI mode=ro + PRAGMA query_only 打开，绝不写入。
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..errors import WSError
from ..models import Chat, Member, Message
from ..nicknames import parse_chatroom_ext
from ..parsing import RawRow, parse_row
from .base import Source

SQLITE_MAGIC = b"SQLite format 3\x00"
MESSAGE_DB_RE = re.compile(r"^(?:biz_)?message_(\d+)\.db$", re.I)
MSG_TABLE_RE = re.compile(r"^Msg_[0-9a-f]{32}$")


def _is_plain_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def _connect_ro(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class Layout:
    def __init__(self, kind: str, root: Path, contact: Path | None, session: Path | None, messages: list[Path]):
        self.kind = kind
        self.root = root
        self.contact = contact
        self.session = session
        self.messages = messages


def _message_sort_key(path: Path) -> tuple[int, int]:
    m = MESSAGE_DB_RE.match(path.name)
    return (1 if path.name.lower().startswith("biz_") else 0, int(m.group(1)) if m else 0)


def _collect_messages(folder: Path) -> list[Path]:
    dbs = [p for p in folder.glob("*.db") if MESSAGE_DB_RE.match(p.name) and not p.name.lower().startswith("biz_")]
    return sorted(dbs, key=_message_sort_key)


def detect_layout(root: Path, account: str = "", paths: dict[str, Any] | None = None) -> Layout:
    paths = paths or {}
    if paths:
        contact = Path(paths["contact"]).expanduser() if paths.get("contact") else None
        session = Path(paths["session"]).expanduser() if paths.get("session") else None
        if paths.get("messages"):
            messages = [Path(p).expanduser() for p in paths["messages"]]
        elif paths.get("message_dir"):
            messages = _collect_messages(Path(paths["message_dir"]).expanduser())
        else:
            messages = []
        base = root if str(root) not in ("", ".") else (contact.parent if contact else Path.cwd())
        return Layout("explicit", base, contact, session, messages)

    if not root.exists():
        raise WSError("source_not_found", f"解密镜像目录不存在：{root}")

    candidates: list[Layout] = []
    # weinanhu：任意层级下的 message/message_0.db，其上两级为 db_storage 根
    for hit in root.glob("**/message/message_0.db"):
        storage = hit.parent.parent
        contact = storage / "contact" / "contact.db"
        session = storage / "session" / "session.db"
        candidates.append(Layout("weinanhu", storage, contact if contact.exists() else None,
                                 session if session.exists() else None, _collect_messages(hit.parent)))
    # flat：同一目录下同时有 contact.db 与 message_0.db
    for hit in root.glob("**/message_0.db"):
        folder = hit.parent
        if folder.name == "message":
            continue
        contact = folder / "contact.db"
        if not contact.exists():
            continue
        session = folder / "session.db"
        candidates.append(Layout("flat", folder, contact, session if session.exists() else None, _collect_messages(folder)))

    if account:
        candidates = [c for c in candidates if account.lower() in str(c.root).lower()]
    if not candidates:
        raise WSError(
            "source_not_found",
            f"在 {root} 下没找到解密后的 message_0.db" + (f"（账号过滤：{account}）" if account else ""),
        )
    if len(candidates) > 1:
        # 优先最近更新的那份；同一账号的 weinanhu / flat 不会同时存在
        accounts = sorted({str(c.root) for c in candidates})
        newest = max(candidates, key=lambda c: max((m.stat().st_mtime for m in c.messages), default=0))
        if len(accounts) > 1 and not account:
            raise WSError("layout_ambiguous", f"发现 {len(accounts)} 个账号镜像", details=accounts)
        return newest
    return candidates[0]


class DecryptedDirSource(Source):
    type_name = "decrypted_dir"

    def __init__(self, root: str, *, tz, me=None, account: str = "", paths: dict[str, Any] | None = None):
        super().__init__(tz=tz, me=me)
        self.layout = detect_layout(Path(root).expanduser() if root else Path(""), account, paths)
        self._conns: dict[Path, sqlite3.Connection] = {}
        self._contacts: dict[str, dict[str, str]] | None = None
        self._name_tables: dict[str, list[tuple[Path, str]]] | None = None
        self._members_cache: dict[str, dict[str, Member]] = {}
        self._self_wxid: str | None = None
        self._check_files()

    # ------------------------------------------------------------ 连接
    def _check_files(self) -> None:
        if not self.layout.contact or not self.layout.contact.exists():
            raise WSError("source_not_found", f"缺少 contact.db（布局 {self.layout.kind}，根 {self.layout.root}）")
        if not self.layout.messages:
            raise WSError("source_not_found", f"缺少 message_N.db（布局 {self.layout.kind}，根 {self.layout.root}）")
        for path in [self.layout.contact, *self.layout.messages]:
            if not _is_plain_sqlite(path):
                raise WSError("database_unreadable", f"{path.name} 不是明文 SQLite，可能仍是加密原库")

    def _conn(self, path: Path) -> sqlite3.Connection:
        con = self._conns.get(path)
        if con is None:
            try:
                con = _connect_ro(path)
            except sqlite3.Error as exc:
                raise WSError("database_unreadable", f"无法打开 {path.name}：{exc}") from exc
            self._conns[path] = con
        return con

    def close(self) -> None:
        for con in self._conns.values():
            try:
                con.close()
            except sqlite3.Error:
                pass
        self._conns.clear()

    def _tables(self, path: Path) -> set[str]:
        rows = self._conn(path).execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r[0] for r in rows}

    @staticmethod
    def _columns(con: sqlite3.Connection, table: str) -> set[str]:
        return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()}

    # ------------------------------------------------------------ 联系人
    def contacts(self) -> dict[str, dict[str, str]]:
        if self._contacts is None:
            con = self._conn(self.layout.contact)
            if "contact" not in self._tables(self.layout.contact):
                raise WSError("schema_unrecognized", "contact.db 中没有 contact 表")
            cols = self._columns(con, "contact")
            wanted = [c for c in ("username", "alias", "nick_name", "remark", "local_type") if c in cols]
            if "username" not in wanted:
                raise WSError("schema_unrecognized", "contact 表缺少 username 列")
            result: dict[str, dict[str, str]] = {}
            for row in con.execute(f"SELECT {', '.join(wanted)} FROM contact"):
                d = {k: ("" if row[k] is None else str(row[k]).strip()) for k in wanted}
                if d["username"]:
                    result[d["username"]] = d
            self._contacts = result
        return self._contacts

    def _name_of(self, username: str) -> str:
        c = self.contacts().get(username) or {}
        return c.get("remark") or c.get("nick_name") or username

    # ------------------------------------------------------------ 会话表定位
    def _name2table(self) -> dict[str, list[tuple[Path, str]]]:
        """username -> [(分片路径, 表名)]。合并所有分片的 Name2Id，并以 md5 直接匹配表名兜底。"""
        if self._name_tables is None:
            mapping: dict[str, list[tuple[Path, str]]] = {}
            md5_to_user: dict[str, str] = {}
            for path in self.layout.messages:
                tables = self._tables(path)
                if "Name2Id" in tables:
                    con = self._conn(path)
                    col = "user_name" if "user_name" in self._columns(con, "Name2Id") else None
                    if col:
                        for (user,) in con.execute(f"SELECT {col} FROM Name2Id"):
                            if user:
                                md5_to_user[_md5(str(user))] = str(user)
            for username in self.contacts():
                md5_to_user.setdefault(_md5(username), username)
            for path in self.layout.messages:
                for table in self._tables(path):
                    if MSG_TABLE_RE.match(table):
                        user = md5_to_user.get(table[4:])
                        if user:
                            mapping.setdefault(user, []).append((path, table))
            self._name_tables = mapping
        return self._name_tables

    # ------------------------------------------------------------ Source 接口
    def list_chats(self, include_private: bool = False) -> list[Chat]:
        contacts = self.contacts()
        tables = self._name2table()
        member_counts = self._member_counts()
        chats: list[Chat] = []
        for username, locations in tables.items():
            is_group = username.endswith("@chatroom")
            if not is_group and not include_private:
                continue
            c = contacts.get(username, {})
            chats.append(
                Chat(
                    id=username,
                    name=c.get("remark") or c.get("nick_name") or username,
                    remark=c.get("remark", ""),
                    member_count=member_counts.get(username),
                    last_time=self._last_time(locations),
                )
            )
        return chats

    def _member_counts(self) -> dict[str, int]:
        con = self._conn(self.layout.contact)
        tables = self._tables(self.layout.contact)
        counts: dict[str, int] = {}
        if "chatroom_member" in tables and "chat_room" in tables:
            cols = self._columns(con, "chat_room")
            if "id" in cols:
                try:
                    for name, n in con.execute(
                        "SELECT r.username, COUNT(*) FROM chatroom_member m JOIN chat_room r ON r.id = m.room_id GROUP BY r.username"
                    ):
                        counts[str(name)] = int(n)
                    return counts
                except sqlite3.Error:
                    counts.clear()
        if "chat_room" in tables and "ext_buffer" in self._columns(con, "chat_room"):
            for name, blob in con.execute("SELECT username, ext_buffer FROM chat_room"):
                if blob:
                    counts[str(name)] = len(parse_chatroom_ext(bytes(blob)))
        return counts

    def _last_time(self, locations: Iterable[tuple[Path, str]]) -> datetime | None:
        latest = 0
        for path, table in locations:
            try:
                row = self._conn(path).execute(f'SELECT MAX(create_time) FROM "{table}"').fetchone()
            except sqlite3.Error:
                continue
            if row and row[0]:
                latest = max(latest, int(row[0]))
        return datetime.fromtimestamp(latest, tz=timezone.utc).astimezone(self.tz) if latest else None

    def members(self, chat_id: str) -> dict[str, Member]:
        if chat_id in self._members_cache:
            return self._members_cache[chat_id]
        contacts = self.contacts()
        con = self._conn(self.layout.contact)
        tables = self._tables(self.layout.contact)
        group_nicks: dict[str, str] = {}
        usernames: set[str] = set()
        if "chat_room" in tables:
            cols = self._columns(con, "chat_room")
            if "ext_buffer" in cols:
                row = con.execute("SELECT ext_buffer FROM chat_room WHERE username = ?", (chat_id,)).fetchone()
                if row and row[0]:
                    group_nicks = parse_chatroom_ext(bytes(row[0]))
                    usernames.update(group_nicks)
            if "chatroom_member" in tables and "id" in cols:
                try:
                    for (member_id,) in con.execute(
                        "SELECT c.username FROM chatroom_member m JOIN chat_room r ON r.id = m.room_id "
                        "JOIN contact c ON c.id = m.member_id WHERE r.username = ?",
                        (chat_id,),
                    ):
                        if member_id:
                            usernames.add(str(member_id))
                except sqlite3.Error:
                    pass
        result: dict[str, Member] = {}
        for username in usernames:
            c = contacts.get(username, {})
            result[username] = Member(
                username=username,
                nick_name=c.get("nick_name", ""),
                remark=c.get("remark", ""),
                alias=c.get("alias", ""),
                group_nick=group_nicks.get(username, ""),
            )
        self._members_cache[chat_id] = result
        return result

    def _sender_map(self, path: Path) -> dict[int, str]:
        """Name2Id 的 rowid -> username，用于把 real_sender_id 还原成 wxid。"""
        con = self._conn(path)
        if "Name2Id" not in self._tables(path):
            return {}
        cols = self._columns(con, "Name2Id")
        if "user_name" not in cols:
            return {}
        return {int(rowid): str(user) for rowid, user in con.execute("SELECT rowid, user_name FROM Name2Id") if user}

    def messages(self, chat: Chat, since: datetime, until: datetime) -> list[Message]:
        locations = self._name2table().get(chat.id, [])
        members = self.members(chat.id)
        contacts = self.contacts()
        start, end = int(since.timestamp()), int(until.timestamp())
        result: list[Message] = []
        seen: set[str] = set()
        for path, table in locations:
            con = self._conn(path)
            cols = self._columns(con, table)
            need = {"local_id", "local_type", "create_time", "message_content"}
            if not need <= cols:
                raise WSError("schema_unrecognized", f"{path.name}:{table} 缺少列 {sorted(need - cols)}")
            optional = [c for c in ("server_id", "real_sender_id", "status", "source", "packed_info_data", "WCDB_CT_message_content") if c in cols]
            sql = (
                f"SELECT local_id, local_type, create_time, message_content{''.join(', ' + c for c in optional)} "
                f'FROM "{table}" WHERE create_time >= ? AND create_time < ? ORDER BY create_time, local_id'
            )
            sender_map = self._sender_map(path)
            for row in con.execute(sql, (start, end)):
                d = dict(row)
                raw = RawRow(
                    shard=path.stem,
                    local_id=int(d["local_id"]),
                    server_id=str(d.get("server_id")) if d.get("server_id") not in (None, 0, "0") else None,
                    local_type=int(d["local_type"] or 0),
                    create_time=int(d["create_time"] or 0),
                    content=d.get("message_content"),
                    compress_flag=d.get("WCDB_CT_message_content"),
                    real_sender=sender_map.get(int(d["real_sender_id"])) if d.get("real_sender_id") else None,
                    status=d.get("status"),
                )
                msg = parse_row(
                    raw,
                    chat=chat,
                    tz=self.tz,
                    members=members,
                    contacts=contacts,
                    self_wxid=self.self_wxid(),
                    me=self.me,
                )
                if msg.msg_id in seen:
                    continue
                seen.add(msg.msg_id)
                result.append(msg)
        result.sort(key=lambda m: (m.timestamp, m.msg_id))
        return result

    def self_wxid(self) -> str:
        """推断账号本人 wxid：配置优先，其次账号目录名（wxid_xxx_yyyy 去掉设备后缀）。"""
        if self._self_wxid is None:
            ids = [str(i) for i in self.me.get("ids") or [] if i]
            if ids:
                self._self_wxid = ids[0]
            else:
                guess = ""
                for part in self.layout.root.parts[::-1]:
                    if part.startswith("wxid_"):
                        guess = re.sub(r"_[0-9a-f]{4}$", "", part)
                        break
                self._self_wxid = guess
        return self._self_wxid

    def latest_time(self) -> datetime | None:
        latest = 0
        for path in self.layout.messages:
            for table in self._tables(path):
                if MSG_TABLE_RE.match(table):
                    try:
                        row = self._conn(path).execute(f'SELECT MAX(create_time) FROM "{table}"').fetchone()
                    except sqlite3.Error:
                        continue
                    if row and row[0]:
                        latest = max(latest, int(row[0]))
        return datetime.fromtimestamp(latest, tz=timezone.utc).astimezone(self.tz) if latest else None

    def earliest_time(self) -> datetime | None:
        earliest = None
        for path in self.layout.messages:
            for table in self._tables(path):
                if MSG_TABLE_RE.match(table):
                    try:
                        row = self._conn(path).execute(f'SELECT MIN(create_time) FROM "{table}" WHERE create_time > 0').fetchone()
                    except sqlite3.Error:
                        continue
                    if row and row[0]:
                        earliest = int(row[0]) if earliest is None else min(earliest, int(row[0]))
        return datetime.fromtimestamp(earliest, tz=timezone.utc).astimezone(self.tz) if earliest else None

    def doctor(self) -> dict[str, Any]:
        warnings: list[str] = []
        readable = 0
        for path in self.layout.messages:
            try:
                self._tables(path)
                readable += 1
            except (sqlite3.Error, WSError):
                warnings.append(f"{path.name} 无法读取")
        contacts = self.contacts()
        tables = self._name2table()
        latest = self.latest_time()
        if latest:
            age_hours = (datetime.now(tz=self.tz) - latest).total_seconds() / 3600
            if age_hours > 24:
                warnings.append(f"镜像最新消息距今 {age_hours:.0f} 小时，可能未同步到最新，建议先运行 refresh")
        if not self.self_wxid():
            warnings.append("未能识别本人 wxid，建议在配置 me.ids 中填写，以便识别“与我相关”")
        return {
            "ok": True,
            "type": self.type_name,
            "layout": self.layout.kind,
            "root": str(self.layout.root),
            "message_dbs": len(self.layout.messages),
            "readable_message_dbs": readable,
            "has_session_db": bool(self.layout.session),
            "contacts": len(contacts),
            "groups": sum(1 for u in tables if u.endswith("@chatroom")),
            "conversations": len(tables),
            "earliest": self.earliest_time().isoformat(timespec="seconds") if self.earliest_time() else None,
            "latest": latest.isoformat(timespec="seconds") if latest else None,
            "self_wxid_known": bool(self.self_wxid()),
            "warnings": warnings,
        }
