from __future__ import annotations

import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wechat_summary.models import Chat, Member  # noqa: E402
from wechat_summary.nicknames import encode_chatroom_ext, parse_chatroom_ext  # noqa: E402
from wechat_summary.parsing import (  # noqa: E402
    RawRow,
    decode_content,
    disambiguate_names,
    normalize_type,
    parse_row,
    split_sender,
    zstd_compress,
)

TZ = ZoneInfo("Asia/Shanghai")
GROUP = Chat(id="111@chatroom", name="测试群")
MEMBERS = {
    "wxid_a": Member("wxid_a", nick_name="Alice", group_nick="阿A"),
    "wxid_b": Member("wxid_b", nick_name="Bob", remark="老B"),
}


def row(content, local_type=1, sender=None, local_id=1, server_id=None, flag=None):
    return RawRow("message_0", local_id, server_id, local_type, 1_761_000_000, content, flag, sender)


def parse(raw, chat=GROUP, me=None):
    return parse_row(raw, chat=chat, tz=TZ, members=MEMBERS, contacts={}, self_wxid="wxid_me", me=me or {"ids": [], "names": []})


class DecodeTests(unittest.TestCase):
    def test_zstd_roundtrip(self):
        data = zstd_compress("你好，世界".encode("utf-8"))
        self.assertEqual(decode_content(data), "你好，世界")

    def test_plain_bytes_and_str(self):
        self.assertEqual(decode_content(b"abc"), "abc")
        self.assertEqual(decode_content("abc"), "abc")
        self.assertEqual(decode_content(None), "")

    def test_split_sender(self):
        self.assertEqual(split_sender("wxid_a:\nhello", True), ("wxid_a", "hello"))
        self.assertEqual(split_sender("wxid_a:\nhello", False), (None, "wxid_a:\nhello"))
        self.assertEqual(split_sender("no prefix", True), (None, "no prefix"))

    def test_normalize_type_high_bits(self):
        self.assertEqual(normalize_type(49 | (57 << 32)), 49)
        self.assertEqual(normalize_type(1), 1)
        self.assertEqual(normalize_type(10000), 10000)


class ParseTests(unittest.TestCase):
    def test_text_with_prefix_and_group_nick(self):
        m = parse(row("wxid_a:\n今天上线了 https://example.com/a"))
        self.assertEqual(m.type, "text")
        self.assertEqual(m.sender_id, "wxid_a")
        self.assertEqual(m.sender_name, "阿A")
        self.assertEqual(m.text, "今天上线了 https://example.com/a")
        self.assertEqual(m.extra["urls"], ["https://example.com/a"])

    def test_real_sender_wins_and_remark_fallback(self):
        m = parse(row("你好", sender="wxid_b"))
        self.assertEqual(m.sender_id, "wxid_b")
        self.assertEqual(m.sender_name, "老B")

    def test_zstd_compressed_text(self):
        m = parse(row(zstd_compress("wxid_a:\n压缩消息".encode()), flag=4))
        self.assertEqual(m.text, "压缩消息")
        self.assertEqual(m.sender_name, "阿A")

    def test_placeholders(self):
        self.assertEqual(parse(row("wxid_a:\n<msg><img/></msg>", 3)).type, "image")
        voice = parse(row('wxid_a:\n<msg><voicemsg voicelength="5300"/></msg>', 34))
        self.assertEqual(voice.type, "voice")
        self.assertEqual(voice.extra["duration_seconds"], 5)
        self.assertEqual(parse(row("wxid_a:\n<msg><emoji/></msg>", 47)).type, "emoji")

    def test_link_card(self):
        xml = ("wxid_a:\n<msg><appmsg><title>好文章</title><des>摘要</des><type>5</type>"
               "<url>https://mp.weixin.qq.com/s/x</url></appmsg></msg>")
        m = parse(row(xml, 49))
        self.assertEqual(m.type, "link")
        self.assertEqual(m.extra["url"], "https://mp.weixin.qq.com/s/x")
        self.assertEqual(m.extra["title"], "好文章")

    def test_file(self):
        xml = ("wxid_b:\n<msg><appmsg><title>方案.pdf</title><type>6</type>"
               "<appattach><totallen>2048</totallen><fileext>pdf</fileext></appattach></appmsg></msg>")
        m = parse(row(xml, 49))
        self.assertEqual(m.type, "file")
        self.assertEqual(m.extra["file_name"], "方案.pdf")
        self.assertEqual(m.extra["file_size"], 2048)

    def test_quote(self):
        xml = ("wxid_b:\n<msg><appmsg><title>同意</title><type>57</type><refermsg><type>1</type>"
               "<svrid>99</svrid><chatusr>wxid_a</chatusr><displayname>阿A</displayname>"
               "<content>我们周五发版吧</content></refermsg></appmsg></msg>")
        m = parse(row(xml, 49 | (57 << 32)))
        self.assertEqual(m.type, "quote")
        self.assertEqual(m.text, "同意")
        self.assertEqual(m.extra["quote"]["text"], "我们周五发版吧")
        self.assertEqual(m.extra["quote"]["sender_id"], "wxid_a")

    def test_forward_and_miniprogram_and_transfer(self):
        fwd = parse(row("wxid_a:\n<msg><appmsg><title>群聊的聊天记录</title><des>A: 1\nB: 2</des><type>19</type></appmsg></msg>", 49))
        self.assertEqual(fwd.type, "forward")
        mini = parse(row("wxid_a:\n<msg><appmsg><title>打卡</title><type>33</type></appmsg></msg>", 49))
        self.assertEqual(mini.type, "miniprogram")
        tr = parse(row("wxid_a:\n<msg><appmsg><title>转账</title><type>2000</type><wcpayinfo><feedesc>¥20.00</feedesc></wcpayinfo></appmsg></msg>", 49))
        self.assertEqual(tr.type, "transfer")
        self.assertEqual(tr.extra["amount"], "¥20.00")

    def test_system_and_revoke_and_pat(self):
        sys_msg = parse(row('"阿A"邀请"老B"加入了群聊', 10000))
        self.assertEqual(sys_msg.type, "system")
        self.assertEqual(sys_msg.sender_name, "系统")
        revoke = parse(row('<sysmsg type="revokemsg"><revokemsg><replacemsg><![CDATA["阿A" 撤回了一条消息]]></replacemsg></revokemsg></sysmsg>', 10002))
        self.assertEqual(revoke.type, "revoke")
        self.assertIn("撤回", revoke.text)
        pat = parse(row('<sysmsg type="pat"><pat><template><![CDATA["${fromusername}" 拍了拍我]]></template><fromusername>wxid_a</fromusername></pat></sysmsg>', 10002))
        self.assertEqual(pat.type, "pat")

    def test_unknown_type_kept(self):
        m = parse(row("wxid_a:\nxx", 999))
        self.assertEqual(m.type, "unknown")
        self.assertEqual(m.extra["raw_type"], 999)

    def test_is_self(self):
        self.assertTrue(parse(row("hi", sender="wxid_me")).is_self)
        self.assertTrue(parse(row("wxid_a:\nhi"), me={"ids": [], "names": ["阿A"]}).is_self)

    def test_msg_id_stable(self):
        a = parse(row("wxid_a:\nx", server_id="123"))
        b = parse(row("wxid_a:\nx", server_id="123", local_id=7))
        self.assertEqual(a.msg_id, b.msg_id)
        c = parse(row("wxid_a:\nx", local_id=2))
        self.assertNotEqual(a.msg_id, c.msg_id)

    def test_disambiguate(self):
        members = {"wxid_x1": Member("wxid_x1", nick_name="小王"), "wxid_x2": Member("wxid_x2", nick_name="小王")}
        msgs = [
            parse_row(row("wxid_x1:\na", local_id=1), chat=GROUP, tz=TZ, members=members, contacts={}, self_wxid="", me={}),
            parse_row(row("wxid_x2:\nb", local_id=2), chat=GROUP, tz=TZ, members=members, contacts={}, self_wxid="", me={}),
        ]
        self.assertEqual(disambiguate_names(msgs), ["小王"])
        self.assertEqual({m.sender_name for m in msgs}, {"小王#d_x1", "小王#d_x2"})


class NicknameTests(unittest.TestCase):
    def test_roundtrip(self):
        blob = encode_chatroom_ext({"wxid_a": "阿A", "wxid_b": "", "wxid_c": "C 同学"})
        self.assertEqual(parse_chatroom_ext(blob), {"wxid_a": "阿A", "wxid_b": "", "wxid_c": "C 同学"})

    def test_truncated_keeps_decoded(self):
        blob = encode_chatroom_ext({"wxid_a": "阿A", "wxid_b": "B"})
        self.assertEqual(parse_chatroom_ext(blob[:-3]).get("wxid_a"), "阿A")

    def test_empty(self):
        self.assertEqual(parse_chatroom_ext(b""), {})
        self.assertEqual(parse_chatroom_ext(None), {})

    def test_member_display_priority(self):
        self.assertEqual(Member("u", nick_name="n", remark="r", group_nick="g").display(), "g")
        self.assertEqual(Member("u", nick_name="n", remark="r").display(), "r")
        self.assertEqual(Member("u", nick_name="n").display(), "n")
        self.assertEqual(Member("u").display(), "u")


if __name__ == "__main__":
    unittest.main()
