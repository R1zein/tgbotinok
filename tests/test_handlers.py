"""Сквозные тесты обработчиков: апдейты прогоняются через Dispatcher с фейковой сессией."""
import asyncio
import itertools
import time

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BusinessConnection,
    Chat,
    ChatMemberAdministrator,
    ChatMemberMember,
    Dice,
    Message,
    Update,
    User,
)

from bot.config import Config
from bot.db import Database
from bot.handlers import get_routers
from bot.middlewares import TrackMiddleware
from bot.services.userbot import Userbot
from bot.services.usernames import UsernameWatcher

BOT_ID, OWNER, ADMIN, USER, OTHER = 42, 100, 200, 300, 400
ADMIN2 = 250  # второй админ чата, не создатель бота
GROUP = -1001
BC = "bc-1"
NAMES = {OWNER: "Owner", ADMIN: "Admin", ADMIN2: "Admin2", USER: "User", OTHER: "Other", BOT_ID: "Bot"}


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls: list = []
        self._ids = itertools.count(5000)

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    def _chat(self, chat_id):
        if chat_id < 0:
            return Chat(id=chat_id, type="supergroup", title="Group")
        return Chat(id=chat_id, type="private", first_name=NAMES.get(chat_id, "X"))

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name == "GetMe":
            return User(id=BOT_ID, is_bot=True, first_name="Bot", username="testbot").as_(bot)
        if name == "GetChatMember":
            u = User(id=method.user_id, is_bot=False, first_name=NAMES.get(method.user_id, "X"))
            if method.user_id in (ADMIN, ADMIN2):
                return ChatMemberAdministrator(
                    user=u, can_be_edited=False, is_anonymous=False, can_manage_chat=True,
                    can_delete_messages=True, can_manage_video_chats=True,
                    can_restrict_members=True, can_promote_members=True, can_change_info=True,
                    can_invite_users=True, can_post_stories=True, can_edit_stories=True,
                    can_delete_stories=True, can_send_welcome_messages=True,
                )
            return ChatMemberMember(user=u)
        if name.startswith("Send") or name.startswith("Edit") or name == "CopyMessage":
            chat_id = getattr(method, "chat_id", None) or 1
            kwargs = dict(message_id=getattr(method, "message_id", None) or next(self._ids),
                          date=int(time.time()), chat=self._chat(chat_id),
                          text=getattr(method, "text", None))
            if name == "SendDice":
                kwargs["dice"] = Dice(emoji=method.emoji or "🎲", value=6)
            if name == "CopyMessage":
                from aiogram.types import MessageId
                return MessageId(message_id=next(self._ids))
            return Message(**kwargs).as_(bot)
        return True

    def names(self):
        return [type(c).__name__ for c in self.calls]

    def texts(self):
        return [getattr(c, "text", None) or getattr(c, "caption", None) or "" for c in self.calls]

    def clear(self):
        self.calls.clear()


def user(uid):
    return {"id": uid, "is_bot": False, "first_name": NAMES[uid], "username": NAMES[uid].lower()}


_mid = itertools.count(1)
_uid = itertools.count(1)


def msg(chat_id, from_id, text=None, reply_to=None, **extra):
    chat = ({"id": chat_id, "type": "supergroup", "title": "Group"} if chat_id < 0
            else {"id": chat_id, "type": "private", "first_name": NAMES.get(chat_id, "X")})
    m = {"message_id": next(_mid), "date": int(time.time()), "chat": chat, "from": user(from_id)}
    if text is not None:
        m["text"] = text
    if reply_to:
        m["reply_to_message"] = reply_to
    m.update(extra)
    return m


def upd(**kw):
    return Update(update_id=next(_uid), **kw)


def cb(from_id, data, message):
    return upd(callback_query={"id": str(next(_uid)), "from": user(from_id), "chat_instance": "1",
                               "data": data, "message": message})


@pytest.fixture
async def env(tmp_path, monkeypatch):
    async def no_sleep(*_a, **_kw):
        return None

    import bot.handlers.economy as eco
    monkeypatch.setattr(eco.asyncio, "sleep", no_sleep)

    config = Config(bot_token="1:x", admin_ids=frozenset({ADMIN}), db_path=str(tmp_path / "t.db"),
                    api_id=None, api_hash=None, userbot_session=None, userbot_min_delay=0,
                    gift_poll_interval=30, username_check_delay=0, username_cycle_interval=60,
                    max_usernames=3, business_cache_days=14)
    db = Database(config.db_path)
    await db.connect()
    session = FakeSession()
    bot = Bot("1:x", session=session, default=DefaultBotProperties(parse_mode="HTML"))
    ub = Userbot(config)
    watcher = UsernameWatcher(bot, db, ub, 0, 60)

    async def fake_check(name):
        return "free" if name == "freename" else "taken"

    watcher.check = fake_check
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"], dp["config"], dp["userbot"], dp["username_watcher"] = db, config, ub, watcher
    track = TrackMiddleware(db, config)
    dp.message.outer_middleware(track)
    dp.callback_query.outer_middleware(track)
    from bot.handlers.nicks import NickMentionMiddleware
    dp.message.outer_middleware(NickMentionMiddleware())
    routers = get_routers()
    dp.include_routers(*routers)

    async def feed(update):
        await dp.feed_update(bot, update)
        await asyncio.sleep(0)

    yield dp, bot, session, db, feed
    for r in routers:
        r._parent_router = None
    await db.close()


async def test_private_menu(env):
    dp, bot, s, db, feed = env
    m = msg(USER, USER, "/start")
    await feed(upd(message=m))
    assert "SendMessage" in s.names()
    for data in ("menu:help", "menu:biz", "biz:t:biz_edits", "menu:gifts", "gift:toggle",
                 "menu:un", "menu:main"):
        await feed(cb(USER, data, msg(USER, BOT_ID, "x")))
    assert (await db.get_user_settings(USER))["biz_edits"] is False
    assert (await db.get_gift_sub(USER))["enabled"] == 1


async def test_usernames_fsm(env):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(USER, USER, "/start")))
    await feed(cb(USER, "un:add", msg(USER, BOT_ID, "x")))
    await feed(upd(message=msg(USER, USER, "@freename https://t.me/durov bad! x a1234 b12345")))
    rows = {r["username"]: r["status"] for r in await db.list_user_usernames(USER)}
    assert rows == {"freename": "free", "durov": "taken", "a1234": "taken"}
    assert any("Не влезли" in t for t in s.texts())
    await feed(cb(USER, "un:del:durov", msg(USER, BOT_ID, "x")))
    assert len(await db.list_user_usernames(USER)) == 2


async def test_moderation(env):
    dp, bot, s, db, feed = env
    target = msg(GROUP, USER, "привет")
    await feed(upd(message=target))
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN, "мут 30m флуд", reply_to=target)))
    assert "RestrictChatMember" in s.names()
    assert any("заглушен" in t and "флуд" in t for t in s.texts())
    s.clear()
    # обычный участник не может мутить
    await feed(upd(message=msg(GROUP, OTHER, "/mute", reply_to=target)))
    assert "RestrictChatMember" not in s.names()
    # варны до лимита
    for _ in range(3):
        await feed(upd(message=msg(GROUP, ADMIN, "варн", reply_to=target)))
    assert any("Лимит предупреждений" in t for t in s.texts())
    assert await db.get_warns(GROUP, USER) == 0
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN, "бан @user 1d")))
    assert "BanChatMember" in s.names()
    await feed(cb(ADMIN, f"mod:unban:{USER}", msg(GROUP, BOT_ID, "⛔ бан")))
    assert "UnbanChatMember" in s.names()
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN, "кик", reply_to=target)))
    assert s.names().count("BanChatMember") == 1 and "UnbanChatMember" in s.names()


async def test_settings_and_call(env):
    dp, bot, s, db, feed = env
    for uid in (USER, OTHER):
        await feed(upd(message=msg(GROUP, uid, "hi")))
    s.clear()
    await feed(upd(message=msg(GROUP, OTHER, "калл")))
    assert any("только администраторам" in t for t in s.texts())
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN, "калл сбор")))
    assert any("вызывает всех" in t for t in s.texts())
    assert any("@user" in t and "@other" in t for t in s.texts())
    await feed(upd(message=msg(GROUP, ADMIN, "/settings")))
    await feed(cb(ADMIN, "gs:economy", msg(GROUP, BOT_ID, "⚙️")))
    assert (await db.get_chat_settings(GROUP))["economy"] is False
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "баланс")))
    assert not s.calls  # экономика выключена
    await feed(cb(ADMIN, "gs:warn_action", msg(GROUP, BOT_ID, "⚙️")))
    assert (await db.get_chat_settings(GROUP))["warn_action"] == "kick"


async def test_economy_and_duel(env):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "баланс")))
    assert await db.get_balance(GROUP, USER) == 1000
    await feed(upd(message=msg(GROUP, USER, "бонус")))
    assert await db.get_balance(GROUP, USER) > 1000
    bal = await db.get_balance(GROUP, USER)
    await feed(upd(message=msg(GROUP, USER, "слоты 100")))  # фейковый кубик = 6 → проигрыш
    await feed(upd(message=msg(GROUP, USER, "дартс 100")))  # 6 → x3
    assert await db.get_balance(GROUP, USER) == bal - 100 + 200
    await feed(upd(message=msg(GROUP, USER, "казино все")))
    await feed(upd(message=msg(GROUP, USER, "топ")))
    other = msg(GROUP, OTHER, "я тут")
    await feed(upd(message=other))
    await db.add_balance(GROUP, USER, 500)
    await feed(upd(message=msg(GROUP, USER, "передать 100", reply_to=other)))
    assert await db.get_balance(GROUP, OTHER) == 1100
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "дуэль 50", reply_to=other)))
    data = s.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    b1, b2 = await db.get_balance(GROUP, USER), await db.get_balance(GROUP, OTHER)
    await feed(cb(USER, data, msg(GROUP, BOT_ID, "⚔️")))  # нельзя принять свой
    await feed(cb(OTHER, data, msg(GROUP, BOT_ID, "⚔️")))
    assert await db.get_balance(GROUP, USER) + await db.get_balance(GROUP, OTHER) == b1 + b2
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "кнб", reply_to=other)))
    data = s.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    await feed(cb(OTHER, data, msg(GROUP, BOT_ID, "✊")))
    edit = [c for c in s.calls if type(c).__name__ == "EditMessageText"][-1]
    pick = edit.reply_markup.inline_keyboard[0][0].callback_data
    await feed(cb(USER, pick, msg(GROUP, BOT_ID, "✊")))
    await feed(cb(OTHER, pick.replace(":r", ":s"), msg(GROUP, BOT_ID, "✊")))
    assert any("Побеждает" in t for t in s.texts())


async def test_board_games_group(env):
    dp, bot, s, db, feed = env
    other = msg(GROUP, OTHER, "го играть")
    await feed(upd(message=other))
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "шахматы", reply_to=other)))
    acc = s.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    gid = int(acc.split(":")[1])
    await feed(cb(USER, acc, msg(GROUP, BOT_ID, "♟")))  # свой вызов нельзя
    await feed(cb(OTHER, acc, msg(GROUP, BOT_ID, "♟")))
    game = await db.get_game(gid)
    assert game["status"] == "active"
    white = game["p1"]
    await feed(cb(white, f"gm:{gid}:c:52", msg(GROUP, BOT_ID, "♟")))  # e2
    await feed(cb(white, f"gm:{gid}:c:36", msg(GROUP, BOT_ID, "♟")))  # e4
    game = await db.get_game(gid)
    assert game["state"]["last_san"] == "e4"
    await feed(cb(game["p2"], f"gm:{gid}:draw", msg(GROUP, BOT_ID, "♟")))
    await feed(cb(white, f"gm:{gid}:draw", msg(GROUP, BOT_ID, "♟")))
    assert (await db.get_game(gid))["status"] == "finished"

    s.clear()
    await feed(upd(message=msg(GROUP, USER, "шашки")))
    acc = s.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    gid = int(acc.split(":")[1])
    await feed(cb(OTHER, acc, msg(GROUP, BOT_ID, "⚪")))
    game = await db.get_game(gid)
    white = game["p1"]
    await feed(cb(white, f"gm:{gid}:c:{5 * 8 + 0}", msg(GROUP, BOT_ID, "⚪")))
    await feed(cb(white, f"gm:{gid}:c:{4 * 8 + 1}", msg(GROUP, BOT_ID, "⚪")))
    game = await db.get_game(gid)
    assert game["state"]["turn"] == "b"
    await feed(cb(white, f"gm:{gid}:resign", msg(GROUP, BOT_ID, "⚪")))
    assert (await db.get_game(gid))["status"] == "finished"

    s.clear()
    await feed(upd(message=msg(GROUP, USER, "крестики", reply_to=other)))
    acc = s.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    gid = int(acc.split(":")[1])
    await feed(cb(OTHER, acc, msg(GROUP, BOT_ID, "❌")))
    g = await db.get_game(gid)
    x, o = g["p1"], g["p2"]
    for who, cell in ((x, 0), (o, 3), (x, 1), (o, 4), (x, 2)):
        await feed(cb(who, f"gm:{gid}:c:{cell}", msg(GROUP, BOT_ID, "❌")))
    assert (await db.get_game(gid))["status"] == "finished"


async def test_fun_and_rp(env):
    dp, bot, s, db, feed = env
    other = msg(GROUP, OTHER, "привет")
    await feed(upd(message=other))
    s.clear()
    for text in ("монетка", "шар будет дождь?", "инфа что я гений", "выбери чай или кофе",
                 "кто сегодня молодец", "!кто лучший", "рп"):
        await feed(upd(message=msg(GROUP, USER, text)))
    assert len([n for n in s.names() if n == "SendMessage"]) == 7
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "кто пойдёт гулять?")))  # обычная фраза — молчим
    assert not s.calls
    await feed(upd(message=msg(GROUP, USER, "обнять крепко", reply_to=other)))
    assert any("обнял(а)" in t and "крепко" in t for t in s.texts())
    await feed(upd(message=msg(GROUP, USER, "дать пять", reply_to=other)))
    assert any("дал(а) пять" in t for t in s.texts())
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "брак", reply_to=other)))
    yes = s.calls[-1].reply_markup.inline_keyboard[0][0].callback_data
    await feed(cb(OTHER, yes, msg(GROUP, BOT_ID, "💍")))
    assert await db.get_marriage(GROUP, USER)
    await feed(upd(message=msg(GROUP, USER, "мой брак")))
    await feed(upd(message=msg(GROUP, USER, "браки")))
    await feed(upd(message=msg(GROUP, USER, "развод")))
    assert not await db.get_marriage(GROUP, USER)
    await feed(upd(message=msg(GROUP, USER, "угадай")))
    import bot.handlers.fun as fun
    number = fun._guess[GROUP]["number"]
    await feed(upd(message=msg(GROUP, OTHER, str(number))))
    assert GROUP not in fun._guess


async def test_business(env):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(OWNER, OWNER, "/start")))
    conn = BusinessConnection.model_validate({
        "id": BC, "user": user(OWNER), "user_chat_id": OWNER, "date": int(time.time()),
        "is_enabled": True,
        "rights": {"can_reply": True, "can_delete_all_messages": True,
                   "can_delete_sent_messages": True},
    })
    s.clear()
    await feed(upd(business_connection=conn))
    assert any("подключён" in t for t in s.texts())

    def bmsg(from_id, text=None, **extra):
        m = msg(OTHER, from_id, text, **extra)
        m["business_connection_id"] = BC
        return m

    m1 = bmsg(OTHER, "секрет")
    m1["date"] = int(time.time()) - 60  # человек правит не сразу, а спустя время
    await feed(upd(business_message=m1))
    edited = dict(m1, text="не секрет", edit_date=int(time.time()))
    s.clear()
    await feed(upd(edited_business_message=edited))
    assert any("Было" in t and "секрет" in t and "не секрет" in t for t in s.texts())

    photo = bmsg(OTHER, None, photo=[{"file_id": "PH", "file_unique_id": "u", "width": 1, "height": 1}],
                 caption="фотка")
    await feed(upd(business_message=photo))
    s.clear()
    await feed(upd(deleted_business_messages={
        "business_connection_id": BC, "chat": {"id": OTHER, "type": "private", "first_name": "Other"},
        "message_ids": [m1["message_id"], photo["message_id"]],
    }))
    assert "SendPhoto" in s.names()
    assert any("Удалено сообщений: 2" in t and "не секрет" in t for t in s.texts())

    # исчезающее фото: в кэше его нет, владелец отвечает на него
    vanish = bmsg(OTHER, None, photo=[{"file_id": "VAN", "file_unique_id": "v", "width": 1, "height": 1}])
    s.clear()
    await feed(upd(business_message=bmsg(OWNER, "ого", reply_to_message=vanish)))
    assert "SendPhoto" in s.names() and any("Сохранено: фото" in t for t in s.texts())

    # .мут
    s.clear()
    await feed(upd(business_message=bmsg(OWNER, ".мут")))
    assert s.names().count("DeleteBusinessMessages") == 1
    send = [c for c in s.calls if type(c).__name__ == "SendMessage"][-1]
    assert send.business_connection_id == BC and "заглушен" in send.text
    s.clear()
    spam = bmsg(OTHER, "спам")
    await feed(upd(business_message=spam))
    assert "DeleteBusinessMessages" in s.names()
    s.clear()
    await feed(upd(deleted_business_messages={
        "business_connection_id": BC, "chat": {"id": OTHER, "type": "private", "first_name": "Other"},
        "message_ids": [spam["message_id"]],
    }))
    assert "SendMessage" not in s.names()  # своё удаление не репортим
    await feed(cb(OTHER, f"bmute:{OTHER}", bmsg(BOT_ID, "🔇")))
    assert await db.is_business_muted(OWNER, OTHER)
    await feed(cb(OWNER, f"bmute:{OTHER}", bmsg(BOT_ID, "🔇")))
    assert not await db.is_business_muted(OWNER, OTHER)

    # .шашки в ЛС
    s.clear()
    await feed(upd(business_message=bmsg(OWNER, ".шашки")))
    send = [c for c in s.calls if type(c).__name__ == "SendMessage"][-1]
    assert send.business_connection_id == BC and len(send.reply_markup.inline_keyboard) == 9
    gid = int(send.reply_markup.inline_keyboard[0][0].callback_data.split(":")[1])
    game = await db.get_game(gid)
    assert {game["p1"], game["p2"]} == {OWNER, OTHER}
    s.clear()
    await feed(cb(game["p1"], f"gm:{gid}:c:{5 * 8 + 2}", bmsg(BOT_ID, "⚪")))
    edit = [c for c in s.calls if type(c).__name__ == "EditMessageText"][-1]
    assert edit.business_connection_id == BC


async def test_admin(env):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(ADMIN, ADMIN, "/admin")))
    for data in ("adm:stats", "adm:feat", "adm:f:economy", "adm:ub"):
        await feed(cb(ADMIN, data, msg(ADMIN, BOT_ID, "x")))
    assert not db.feature_enabled("economy")
    await feed(cb(ADMIN, "adm:ban", msg(ADMIN, BOT_ID, "x")))
    await feed(upd(message=msg(ADMIN, ADMIN, str(USER))))
    assert await db.is_banned(USER)
    s.clear()
    await feed(upd(message=msg(USER, USER, "/start")))
    assert not s.calls  # заблокированного игнорируем
    await feed(cb(USER, "adm:stats", msg(USER, BOT_ID, "x")))
    await feed(cb(ADMIN, "adm:bc", msg(ADMIN, BOT_ID, "x")))
    await feed(upd(message=msg(ADMIN, ADMIN, "рассылка")))
    import bot.handlers.admin as adm
    await asyncio.gather(*adm._tasks)
    assert "CopyMessage" in s.names()


async def test_mute_forever_and_unmute_alias(env):
    dp, bot, s, db, feed = env
    target = msg(GROUP, USER, "привет")
    await feed(upd(message=target))
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN, "мут", reply_to=target)))
    assert any("заглушен(а) навсегда" in t for t in s.texts())
    await feed(upd(message=msg(GROUP, ADMIN, "мут 2h", reply_to=target)))
    assert any("заглушен(а) на 2 ч" in t for t in s.texts())
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN, "анмут", reply_to=target)))
    assert "RestrictChatMember" in s.names() and any("снова может писать" in t for t in s.texts())


async def test_call_uses_usernames_and_userbot(env, monkeypatch):
    dp, bot, s, db, feed = env
    ub = dp["userbot"]
    ub.ready = True

    async def members(_bot, _chat):
        return [(USER, "User", "user"), (OTHER, "Other", None), (777, "Silent", "silent_guy")]

    monkeypatch.setattr(ub, "group_members", members)
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN, "/call")))
    joined = " ".join(s.texts())
    assert "@user" in joined and "@silent_guy" in joined and "tg://user?id=400" in joined
    assert await db.get_member(GROUP, 777)  # полный список сохраняется в базу


async def _connect_business(feed):
    await feed(upd(message=msg(OWNER, OWNER, "/start")))
    conn = BusinessConnection.model_validate({
        "id": BC, "user": user(OWNER), "user_chat_id": OWNER, "date": int(time.time()),
        "is_enabled": True, "rights": {"can_reply": True, "can_delete_all_messages": True},
    })
    await feed(upd(business_connection=conn))


def _bmsg(chat_id, from_id, text=None, **extra):
    m = msg(chat_id, from_id, text, **extra)
    m["business_connection_id"] = BC
    return m


async def test_business_styles(env):
    dp, bot, s, db, feed = env
    await _connect_business(feed)
    THIRD = 500
    NAMES[THIRD] = "Third"

    def bmsg(from_id, text, chat=OTHER):
        return _bmsg(chat, from_id, text)

    def edits():
        return [c for c in s.calls if type(c).__name__ == "EditMessageText"]

    # без стиля сообщения не трогаем
    s.clear()
    await feed(upd(business_message=bmsg(OWNER, "привет")))
    assert not edits()

    # формат (жирный) — в меню бота, действует во всех чатах
    await feed(cb(OWNER, "sty:menu", msg(OWNER, BOT_ID, "x")))
    await feed(cb(OWNER, "sty:f:bold", msg(OWNER, BOT_ID, "x")))
    assert (await db.get_user_settings(OWNER))["msg_format"] == "bold"
    s.clear()
    await feed(upd(business_message=bmsg(OWNER, "как дела")))
    e = edits()[-1]
    assert e.text == "<b>как дела</b>" and e.business_connection_id == BC
    await feed(cb(OWNER, "sty:f:none", msg(OWNER, BOT_ID, "x")))

    # .пикми — режим только для этого чата
    s.clear()
    await feed(upd(business_message=bmsg(OWNER, ".пикми")))
    assert (await db.get_chat_mode(OWNER, OTHER))["style"] == "kawaii"
    assert (await db.get_chat_mode(OWNER, THIRD))["style"] == "none"
    assert "пикми-режим включён" in edits()[-1].text and ".пикми" in edits()[-1].text
    s.clear()
    await feed(upd(business_message=bmsg(OTHER, "я собеседник")))
    assert not edits()
    await feed(upd(business_message=bmsg(OWNER, "хочу кушать")))
    assert edits() and edits()[-1].chat_id == OTHER
    s.clear()
    await feed(upd(business_message=bmsg(OWNER, "в другом чате", chat=THIRD)))
    assert not edits()  # в другом чате режим не включён

    # повтор выключает, .пират переключает, .обычный выключает
    await feed(upd(business_message=bmsg(OWNER, ".пикми")))
    assert (await db.get_chat_mode(OWNER, OTHER))["style"] == "none"
    await feed(upd(business_message=bmsg(OWNER, ".пират")))
    assert (await db.get_chat_mode(OWNER, OTHER))["style"] == "pirate"
    await feed(upd(business_message=bmsg(OWNER, ".обычный")))
    assert (await db.get_chat_mode(OWNER, OTHER))["style"] == "none"

    # .команды — список прямо в чате
    s.clear()
    await feed(upd(business_message=bmsg(OWNER, ".команды")))
    e = edits()[-1]
    assert "Команды ассистента" in e.text and ".клон" in e.text and ".спам" in e.text
    assert e.chat_id == OTHER and e.business_connection_id == BC


async def test_business_clone_and_spam(env, monkeypatch):
    dp, bot, s, db, feed = env
    await _connect_business(feed)
    import bot.handlers.business as biz

    async def no_sleep(*_a, **_kw):
        return None
    monkeypatch.setattr(biz, "SPAM_PAUSE", 0)

    def sends():
        return [c for c in s.calls if type(c).__name__ in ("SendMessage", "SendPhoto")
                and getattr(c, "business_connection_id", None) == BC]

    await feed(upd(business_message=_bmsg(OTHER, OWNER, ".клон")))
    assert (await db.get_chat_mode(OWNER, OTHER))["clone"] == 1
    s.clear()
    await feed(upd(business_message=_bmsg(OTHER, OTHER, "я дурак")))
    await feed(upd(business_message=_bmsg(OTHER, OTHER, None, caption="смотри",
               photo=[{"file_id": "PH", "file_unique_id": "u", "width": 1, "height": 1}])))
    await feed(upd(business_message=_bmsg(OTHER, OTHER, ".команды")))  # команды чужого бота не клонируем
    out = sends()
    assert [type(c).__name__ for c in out] == ["SendMessage", "SendPhoto"]
    assert out[0].text == "я дурак" and out[0].chat_id == OTHER
    assert out[1].photo == "PH" and out[1].caption == "смотри"
    await feed(upd(business_message=_bmsg(OTHER, OWNER, ".клон")))
    assert (await db.get_chat_mode(OWNER, OTHER))["clone"] == 0
    s.clear()
    await feed(upd(business_message=_bmsg(OTHER, OTHER, "тишина")))
    assert not sends()

    # .спам — не больше 10 раз
    s.clear()
    await feed(upd(business_message=_bmsg(OTHER, OWNER, ".спам Привет мир 50")))
    await asyncio.gather(*biz._bg_tasks)
    out = [c for c in sends() if c.text == "Привет мир"]
    assert len(out) == biz.SPAM_MAX
    assert "DeleteBusinessMessages" in s.names()


async def test_business_ignores_automatic_changes(env):
    dp, bot, s, db, feed = env
    await _connect_business(feed)
    now = int(time.time())
    m = _bmsg(OTHER, OTHER, "обычный текст")
    m["date"] = now - 60
    await feed(upd(business_message=m))
    # правка через 2 секунды после отправки (бот собеседника «пикми»-режим) — молчим
    quick = _bmsg(OTHER, OTHER, "привеет~ (◕ᴗ◕✿)")
    quick["date"] = now
    await feed(upd(business_message=quick))
    s.clear()
    await feed(upd(edited_business_message=dict(quick, text="уже другое", edit_date=now + 2)))
    assert not any("изменено" in t.lower() for t in s.texts())
    # команда чужого бота: изменение и удаление не показываем
    cmd = _bmsg(OTHER, OTHER, ".команды")
    cmd["date"] = now - 100
    await feed(upd(business_message=cmd))
    await feed(upd(edited_business_message=dict(cmd, text="📖 список команд", edit_date=now)))
    await feed(upd(deleted_business_messages={
        "business_connection_id": BC, "chat": {"id": OTHER, "type": "private", "first_name": "Other"},
        "message_ids": [cmd["message_id"]],
    }))
    assert not s.texts() or not any("Удалено" in t or "изменено" in t for t in s.texts())
    # обычная правка человеком через минуту — показываем
    await feed(upd(edited_business_message=dict(m, text="исправил", edit_date=now)))
    assert any("Было" in t and "исправил" in t for t in s.texts())


async def test_business_both_users_on_our_bot(env):
    """Собеседник тоже подключил нашего бота и включил режим текста: правки, которые
    наш бот делает в его сообщениях, не должны приходить второму пользователю."""
    dp, bot, s, db, feed = env
    await _connect_business(feed)
    BC2 = "bc-2"
    await feed(upd(message=msg(OTHER, OTHER, "/start")))
    await feed(upd(business_connection=BusinessConnection.model_validate({
        "id": BC2, "user": user(OTHER), "user_chat_id": OTHER, "date": int(time.time()),
        "is_enabled": True, "rights": {"can_reply": True, "can_delete_all_messages": True},
    })))

    def via_other(text):  # сообщение собеседника, как его видит его собственное подключение
        m = msg(OWNER, OTHER, text)
        m["business_connection_id"] = BC2
        return m

    # собеседник включает у себя пикми-режим в чате со мной
    s.clear()
    await feed(upd(business_message=via_other(".пикми")))
    flash_text = [c for c in s.calls if type(c).__name__ == "EditMessageText"][-1].text
    # ...и я вижу, как его команда превращается в уведомление — это не показываем
    now = int(time.time())
    seen_cmd = _bmsg(OTHER, OTHER, ".пикми")
    seen_cmd["date"] = now - 120
    await feed(upd(business_message=seen_cmd))

    # он пишет сообщение; у меня оно тоже появляется (ID у сторон разные)
    seen = _bmsg(OTHER, OTHER, "хочу кушать")
    seen["date"] = now - 60
    await feed(upd(business_message=seen))
    s.clear()
    await feed(upd(business_message=via_other("хочу кушать")))
    styled_html = [c for c in s.calls if type(c).__name__ == "EditMessageText"][-1].text
    import html as _html
    import re as _re
    styled = _html.unescape(_re.sub(r"<[^>]+>", "", styled_html))

    # правка пришла ко мне с задержкой (дольше окна в 5 секунд)
    s.clear()
    await feed(upd(edited_business_message=dict(seen, text=styled, edit_date=now)))
    await feed(upd(edited_business_message=dict(
        seen_cmd, text=_html.unescape(_re.sub(r"<[^>]+>", "", flash_text)), edit_date=now)))
    assert not [t for t in s.texts() if "изменено" in t.lower()]

    # а обычная правка человеком по-прежнему приходит
    await feed(upd(edited_business_message=dict(seen, text="передумал", edit_date=now + 1)))
    assert any("Было" in t and "передумал" in t for t in s.texts())


async def test_reply_to_old_photo_is_not_disappearing(env):
    """Ответ на обычное фото, отправленное до запуска/подключения бота, — не «исчезающее»."""
    dp, bot, s, db, feed = env
    await _connect_business(feed)
    old = _bmsg(OTHER, OTHER, None,
                photo=[{"file_id": "OLD", "file_unique_id": "o", "width": 1, "height": 1}])
    old["date"] = int(time.time()) - 3600
    s.clear()
    await feed(upd(business_message=_bmsg(OTHER, OWNER, "красиво", reply_to_message=old)))
    assert "SendPhoto" not in s.names()
    assert not any("Сохранено:" in t for t in s.texts())

    # обычное фото, пришедшее при работающем боте, есть в кэше — тоже не «исчезающее»
    normal = _bmsg(OTHER, OTHER, None,
                   photo=[{"file_id": "NORM", "file_unique_id": "n", "width": 1, "height": 1}])
    await feed(upd(business_message=normal))
    s.clear()
    await feed(upd(business_message=_bmsg(OTHER, OWNER, "ок", reply_to_message=normal)))
    assert "SendPhoto" not in s.names()


async def test_creator_protection_group(env):
    """Создателя бота (ADMIN в admin_ids) нельзя замутить/забанить; мут — отражается на писавшего."""
    dp, bot, s, db, feed = env
    creator = msg(GROUP, ADMIN, "я создатель")
    await feed(upd(message=creator))
    # ADMIN2 — второй админ чата, пытается замутить создателя
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN2, "мут", reply_to=creator)))
    assert any("создател" in t.lower() for t in s.texts())
    # создатель НЕ замучен; замучен сам писавший (ADMIN2)
    restricts = [c for c in s.calls if type(c).__name__ == "RestrictChatMember"]
    assert restricts and all(c.user_id == ADMIN2 for c in restricts)
    assert all(c.user_id != ADMIN for c in restricts)

    # бан создателя — только сообщение, никого не банит
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN2, "бан", reply_to=creator)))
    assert "BanChatMember" not in s.names()
    assert any("создател" in t.lower() for t in s.texts())

    # кик создателя — тоже только сообщение
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN2, "кик", reply_to=creator)))
    assert "BanChatMember" not in s.names()


async def test_creator_protection_and_rival_business(env):
    """В ЛС: .мут на создателя не работает; чужой бот-мут получает ответ-защиту."""
    dp, bot, s, db, feed = env
    # владелец-ассистент — это создатель? Нет: создатель = ADMIN. Собеседник = ADMIN (создатель).
    await feed(upd(message=msg(ADMIN, ADMIN, "/start")))
    bc = "bc-admin"
    await feed(upd(business_connection=BusinessConnection.model_validate({
        "id": bc, "user": user(OWNER), "user_chat_id": OWNER, "date": int(time.time()),
        "is_enabled": True, "rights": {"can_reply": True, "can_delete_all_messages": True},
    })))

    def bmsg(from_id, text, chat=ADMIN):
        m = msg(chat, from_id, text)
        m["business_connection_id"] = bc
        return m

    # владелец пытается замутить создателя (собеседник = ADMIN) — не выходит
    s.clear()
    await feed(upd(business_message=bmsg(OWNER, ".мут")))
    assert any("создател" in t.lower() for t in s.texts())
    assert not await db.is_business_muted(OWNER, ADMIN)

    # собеседник пишет .мут своим ботом — наш бот отвечает «защитой от esgram»
    s.clear()
    await feed(upd(business_message=bmsg(ADMIN, ".мут")))
    assert any("esgram" in t.lower() for t in s.texts())
    # повторно сразу — кулдаун, молчим
    s.clear()
    await feed(upd(business_message=bmsg(ADMIN, "мут")))
    assert not any("esgram" in t.lower() for t in s.texts())

    # .докс собеседника тоже получает ответ-защиту (после кулдауна)
    import bot.handlers.business as _biz
    _biz._rival_cooldown.clear()
    s.clear()
    await feed(upd(business_message=bmsg(ADMIN, ".докс")))
    assert any("esgram" in t.lower() and "досье" in t.lower() for t in s.texts())


async def test_group_text_styles(env):
    """В группе .милый включает режим: сообщения автора удаляются и публикуются ботом в стиле."""
    dp, bot, s, db, feed = env
    import bot.handlers.group_styles as gs
    gs._cache.clear()
    await feed(upd(message=msg(GROUP, USER, "всем привет")))
    s.clear()
    await feed(upd(message=msg(GROUP, USER, ".милый")))
    assert (await db.get_chat_mode(USER, GROUP))["style"] == "sweet"
    assert any("милый-режим включён" in t for t in s.texts())

    s.clear()
    await feed(upd(message=msg(GROUP, USER, "спасибо друг")))
    assert "DeleteMessage" in s.names()
    sent = [c for c in s.calls if type(c).__name__ == "SendMessage"][-1]
    assert "tg://user?id=300" in sent.text and "спасибочки" in sent.text.lower()

    # другие участники и команды не трогаются
    s.clear()
    await feed(upd(message=msg(GROUP, OTHER, "я без режима")))
    await feed(upd(message=msg(GROUP, USER, "/help")))
    assert "DeleteMessage" not in s.names()

    # голое слово «пират» — обычный текст, режим не меняет (только с точкой)
    await feed(upd(message=msg(GROUP, OTHER, "пират")))
    assert (await db.get_chat_mode(OTHER, GROUP))["style"] == "none"

    # повтор выключает
    await feed(upd(message=msg(GROUP, USER, ".милый")))
    assert (await db.get_chat_mode(USER, GROUP))["style"] == "none"
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "снова обычный")))
    assert "DeleteMessage" not in s.names()

    # выключено в настройках группы — не работает
    await db.set_chat_setting(GROUP, "styles", False)
    s.clear()
    await feed(upd(message=msg(GROUP, USER, ".пират")))
    assert (await db.get_chat_mode(USER, GROUP))["style"] == "none"


async def test_gift_filter_menu(env):
    dp, bot, s, db, feed = env
    for slug, title in (("PlushPepe", "Plush Pepe"),):
        await db.execute("INSERT INTO gift_collections(slug, title, gift_id, status) VALUES(?,?,1,'ok')",
                         (slug, title))
    await db.save_gift_attr("PlushPepe", "backdrop", "Black", "1%", 10)
    await db.save_gift_attr("PlushPepe", "backdrop", "White", "2.5%", 25)
    await db.save_gift_attr("PlushPepe", "pattern", "Stars", "0.5%", 5)
    await feed(upd(message=msg(USER, USER, "/start")))
    m = msg(USER, BOT_ID, "x")
    for data in ("menu:gifts", "gift:bd:0", "gift:bdt:1:0", "gift:ptl:0",
                 "gift:ptc:PlushPepe:0", "gift:ptt:PlushPepe:0:0"):
        await feed(cb(USER, data, m))
    assert await db.get_user_gift_attrs(USER, "backdrop") == {"White"}
    assert await db.get_user_gift_attrs(USER, "pattern", "PlushPepe") == {"Stars"}
    await feed(cb(USER, "gift:up", m))
    assert any("Фон: <b>выбрано 1</b>" in t and "Узор: <b>выбрано 1</b>" in t for t in s.texts())
    await feed(cb(USER, "gift:bdr:0", m))
    await feed(cb(USER, "gift:ptx", m))
    assert not await db.get_user_gift_attrs(USER, "backdrop")
    assert await db.count_user_patterns(USER) == 0


async def test_resale_menu(env):
    dp, bot, s, db, feed = env
    await db.execute("INSERT INTO gift_collections(slug, title, gift_id, status) "
                     "VALUES('PlushPepe','Plush Pepe',1,'ok')")
    await db.save_gift_attr("PlushPepe", "model", "Gold", "1%", 10)
    await db.save_gift_attr("PlushPepe", "backdrop", "Black", "1%", 10)
    await feed(upd(message=msg(USER, USER, "/start")))
    m = msg(USER, BOT_ID, "x")
    await db.save_gift_attr("PlushPepe", "pattern", "Stars", "0.5%", 5)
    for data in ("menu:gifts", "rs:menu", "rs:toggle", "rs:cur", "rs:disc", "rs:cols:0",
                 "rs:c:PlushPepe:0", "rs:al:p:0", "rs:ac:p:PlushPepe:0", "rs:at:p:PlushPepe:0:0",
                 "rs:bd:0", "rs:bdt:0:0", "rs:menu"):
        await feed(cb(USER, data, m))
    sub = await db.get_resale_sub(USER)
    assert sub["enabled"] == 1 and sub["currency"] == "stars" and sub["discount"] == 30
    assert sub["all_collections"] == 0 and await db.get_resale_collections(USER) == {"PlushPepe"}
    assert await db.get_user_gift_attrs(USER, "r_pattern", "PlushPepe") == {"Stars"}
    assert await db.get_user_gift_attrs(USER, "r_backdrop", "*") == {"Black"}
    assert any("Выгодные лоты маркета" in t for t in s.texts())
    assert not any("Модель (" in t for t in s.texts())
    await feed(cb(USER, "rs:ax:p", m))
    assert await db.count_user_attrs(USER, "r_pattern") == 0


async def test_group_earning_and_commands(env):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "работа")))
    bal = await db.get_balance(GROUP, USER)
    assert bal > 1000
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "работа")))  # кулдаун час
    assert await db.get_balance(GROUP, USER) == bal and any("Отдохни" in t for t in s.texts())
    await feed(upd(message=msg(GROUP, USER, "копать")))
    assert await db.get_balance(GROUP, USER) > bal
    # пособие — только если почти ничего не осталось
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "пособие")))
    assert any("меньше 100" in t for t in s.texts())
    await db.add_balance(GROUP, USER, -await db.get_balance(GROUP, USER))
    await feed(upd(message=msg(GROUP, USER, "пособие")))
    assert await db.get_balance(GROUP, USER) == 300
    # «команды» без точки — список команд группы
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "команды")))
    assert any("Команды для группы" in t and "работа" in t for t in s.texts())


async def test_group_nicks(env):
    dp, bot, s, db, feed = env
    import bot.handlers.nicks as nk
    nk._cache.clear()
    nk._last_ping.clear()
    nk._wizards.clear()
    pasha = 600
    NAMES[pasha] = "Pasha"
    await feed(upd(message=msg(GROUP, pasha, "я паша")))
    await feed(upd(message=msg(GROUP, USER, "привет")))
    await db.set_chat_setting(GROUP, "nicks", True)

    # админ запускает мастер из /settings
    s.clear()
    await feed(cb(ADMIN, "nk:start", msg(GROUP, BOT_ID, "⚙️")))
    wiz = [c for c in s.calls if type(c).__name__ == "SendMessage"][-1]
    assert "Как запомнить" in wiz.text
    w = nk._wizards[GROUP]
    # листаем до Паши и отвечаем на сообщение мастера
    while w["queue"][w["idx"]][0] != pasha:
        await feed(cb(ADMIN, "nk:next", msg(GROUP, BOT_ID, "🏷", message_id=w["msg"])))
    wiz_msg = msg(GROUP, BOT_ID, "🏷", message_id=w["msg"])
    await feed(upd(message=msg(GROUP, ADMIN, "паша, павел", reply_to=wiz_msg)))
    assert {r["alias"] for r in await db.list_nicks(GROUP)} == {"паша", "павел"}
    await feed(cb(ADMIN, "nk:stop", wiz_msg))
    assert GROUP not in nk._wizards

    # упоминание в любом падеже — бот отвечает и отмечает Пашу (@pasha)
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "скажите Паше, что мы ждём")))
    ping = [c for c in s.calls if type(c).__name__ == "SendMessage" and "@pasha" in (c.text or "")]
    assert ping and ping[-1].reply_parameters is not None
    # кулдаун: сразу повторно — молчим
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "где Павел?")))
    assert not any("@pasha" in t for t in s.texts())
    # сам Паша, пишущий своё имя, себя не отмечает; похожие слова не срабатывают
    nk._last_ping.clear()
    s.clear()
    await feed(upd(message=msg(GROUP, pasha, "паша на связи")))
    await feed(upd(message=msg(GROUP, USER, "вспахали пашню")))
    assert not any("@pasha" in t for t in s.texts())
    # ручное добавление и удаление
    await feed(upd(message=msg(GROUP, ADMIN, "ник @user юзик")))
    assert "юзик" in {r["alias"] for r in await db.list_nicks(GROUP)}
    await feed(upd(message=msg(GROUP, ADMIN, "забыть ник юзик")))
    assert "юзик" not in {r["alias"] for r in await db.list_nicks(GROUP)}
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "ники")))
    assert any("паша" in t and "павел" in t for t in s.texts())


async def test_group_nicks_delete_buttons_and_two_aliases(env, monkeypatch):
    dp, bot, s, db, feed = env
    import bot.handlers.nicks as nk
    nk._cache.clear()
    nk._last_ping.clear()
    pasha = 600
    NAMES[pasha] = "Pasha"
    await feed(upd(message=msg(GROUP, pasha, "я тут")))
    await db.set_chat_setting(GROUP, "nicks", True)
    await db.add_nick(GROUP, pasha, "паша")
    await db.add_nick(GROUP, pasha, "павел")

    # два разных имени одного человека подряд — оба срабатывают (антифлуд всего 3 сек)
    clock = [1000.0]
    monkeypatch.setattr(nk.time, "time", lambda: clock[0])
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "позовите Пашу")))
    clock[0] += 4
    await feed(upd(message=msg(GROUP, USER, "Павел, ау")))
    assert len([t for t in s.texts() if "@pasha" in t]) == 2

    # список с кнопками удаления: не-админ не может, админ — может
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "ники")))
    kb = [c for c in s.calls if type(c).__name__ == "SendMessage"][-1].reply_markup
    buttons = [b for row in kb.inline_keyboard for b in row]
    assert [b.text for b in buttons] == ["❌ павел", "❌ паша"]
    lst = msg(GROUP, BOT_ID, "🏷")
    await feed(cb(USER, buttons[0].callback_data, lst))
    assert len(await db.list_nicks(GROUP)) == 2
    await feed(cb(ADMIN, buttons[0].callback_data, lst))
    assert [r["alias"] for r in await db.list_nicks(GROUP)] == ["паша"]

    # в мастере у текущего человека тоже есть кнопки удаления
    nk._wizards.clear()
    await feed(cb(ADMIN, "nk:start", msg(GROUP, BOT_ID, "⚙️")))
    w = nk._wizards[GROUP]
    while w["queue"][w["idx"]][0] != pasha:
        await feed(cb(ADMIN, "nk:next", msg(GROUP, BOT_ID, "🏷", message_id=w["msg"])))
    await feed(cb(ADMIN, "nk:wdel:0", msg(GROUP, BOT_ID, "🏷", message_id=w["msg"])))
    assert await db.list_nicks(GROUP) == []
