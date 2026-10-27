"""昵称抽取、主人称呼和性格模板选择。"""

import asyncio

from core.handle.voice_activation import (
    clean_supplement,
    extract_nickname,
    extract_owner_title,
    is_no_supplement,
    match_persona,
    parse_nickname_reply,
    persona_menu,
    resolve_persona,
    supplement_text,
)


class _FakeLogger:
    def bind(self, **_kwargs):
        return self

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


class _FakeConn:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []
        self.logger = _FakeLogger()
        self.llm = self

    def response_no_stream(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

TEMPLATES = ["湾湾小何", "星际游子", "英语老师", "好奇男孩", "汪汪队长"]


def test_parse_nickname_reply():
    assert parse_nickname_reply('{"nickname":"小智"}') == "小智"
    assert parse_nickname_reply('```json\n{"nickname":"豆豆"}\n```') == "豆豆"
    assert parse_nickname_reply('好的 {"nickname":"小美"}') == "小美"
    assert parse_nickname_reply('{"nickname":""}') is None
    assert parse_nickname_reply('{"nickname":"你的昵称是小智"}') is None
    assert parse_nickname_reply("小智") is None


def test_extract_nickname_uses_llm_for_sentences():
    conn = _FakeConn('{"nickname":"小智"}')
    assert asyncio.run(extract_nickname(conn, "你的昵称是小智")) == "小智"
    assert conn.calls and "你的昵称是小智" in conn.calls[0][1]

    corrected = _FakeConn('{"nickname":"小美"}')
    assert asyncio.run(extract_nickname(corrected, "不对，你的昵称是小美")) == "小美"


def test_extract_nickname_bare_name_skips_llm_and_rejects_noise():
    conn = _FakeConn('{"nickname":"不该调用"}')
    assert asyncio.run(extract_nickname(conn, "小智")) == "小智"
    assert conn.calls == []
    assert asyncio.run(extract_nickname(conn, "小智吧")) == "小智"
    assert asyncio.run(extract_nickname(conn, "确认")) is None
    assert asyncio.run(extract_nickname(conn, "不对")) is None
    assert conn.calls == []
    assert asyncio.run(extract_nickname(_FakeConn('{"nickname":""}'), "你的昵称是")) is None
    long_name = "一二三四五六七八九十一二三"
    assert asyncio.run(extract_nickname(_FakeConn('{"nickname":"' + long_name + '"}'), long_name)) is None
    failed = _FakeConn(RuntimeError("down"))
    assert asyncio.run(extract_nickname(failed, "就叫小美")) is None


def test_extract_owner_title():
    bare = _FakeConn('{"title":"不该调用"}')
    assert asyncio.run(extract_owner_title(bare, "老板")) == "老板"
    assert asyncio.run(extract_owner_title(bare, "嗯，老板")) == "老板"
    assert asyncio.run(extract_owner_title(bare, "确认")) is None
    assert bare.calls == []

    called = _FakeConn('{"title":"张先生"}')
    assert asyncio.run(extract_owner_title(called, "你可以称呼我为张先生")) == "张先生"
    assert called.calls and "你可以称呼我为张先生" in called.calls[0][1]

    corrected = _FakeConn('{"title":"妈妈"}')
    assert asyncio.run(extract_owner_title(corrected, "不对，叫我妈妈")) == "妈妈"
    assert asyncio.run(extract_owner_title(_FakeConn('{"title":""}'), "我该怎么称呼您呢")) is None
    assert asyncio.run(extract_owner_title(_FakeConn(RuntimeError("down")), "叫我老板")) is None


def test_match_persona_by_name_or_index():
    assert match_persona("英语老师", TEMPLATES) == "英语老师"
    assert match_persona("我选汪汪队长", TEMPLATES) == "汪汪队长"
    assert match_persona("二", TEMPLATES) == "星际游子"
    assert match_persona("第二个", TEMPLATES) == "星际游子"
    assert match_persona("第3个", TEMPLATES) == "英语老师"
    assert match_persona("就用第五个", TEMPLATES) == "汪汪队长"
    assert match_persona("确认", TEMPLATES) is None
    assert match_persona("不要英语老师", TEMPLATES) is None
    assert match_persona("十", TEMPLATES) is None


def test_resolve_persona_uses_llm_for_description():
    local = _FakeConn('{"template":"不该调用"}')
    assert asyncio.run(resolve_persona(local, "英语老师", TEMPLATES)) == "英语老师"
    assert asyncio.run(resolve_persona(local, "第二个", TEMPLATES)) == "星际游子"
    assert local.calls == []

    described = _FakeConn('{"template":"湾湾小何"}')
    assert asyncio.run(resolve_persona(described, "我想要那个台湾女生", TEMPLATES)) == "湾湾小何"
    assert described.calls
    assert asyncio.run(resolve_persona(_FakeConn('{"template":"外星人不存在"}'), "随便来一个", TEMPLATES)) is None


def test_supplement_decline_uses_llm_for_sentence():
    assert asyncio.run(supplement_text(_FakeConn('{"decline":false}'), "没有")) == ""
    direct = _FakeConn('{"decline":true}')
    assert asyncio.run(supplement_text(direct, "说话温柔一点")) == "说话温柔一点"
    assert direct.calls == []
    declined = _FakeConn('{"decline":true}')
    assert asyncio.run(supplement_text(declined, "没什么要补充的")) == ""
    assert declined.calls


def test_supplement_and_menu():
    assert is_no_supplement("没有")
    assert is_no_supplement("没有了。")
    assert is_no_supplement("不用补充")
    assert not is_no_supplement("说话温柔一点")
    assert clean_supplement("  说话温柔一点。 ") == "说话温柔一点"
    menu = persona_menu(TEMPLATES)
    assert menu.startswith("接下来选一个性格。一，湾湾小何。")
    assert "五，汪汪队长" in menu
