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
GROUP = -1001
BC = "bc-1"
NAMES = {OWNER: "Owner", ADMIN: "Admin", USER: "User", OTHER: "Other", BOT_ID: "Bot"}


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
            if method.user_id == ADMIN:
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
    assert any("tg://user?id=300" in t and "tg://user?id=400" in t for t in s.texts())
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
    assert "SendPhoto" in s.names() and any("Исчезающее" in t for t in s.texts())

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
