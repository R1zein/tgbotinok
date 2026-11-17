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
    from bot.handlers.ai import AIMemoryMiddleware
    from bot.services.ai import AIClient
    dp.message.outer_middleware(AIMemoryMiddleware())
    from bot.handlers.reactions import AutoReactionMiddleware
    dp.message.outer_middleware(AutoReactionMiddleware())
    dp["ai"] = AIClient(config)
    from bot.services.stt import Transcriber
    dp["stt"] = Transcriber(config)
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


@pytest.mark.parametrize("command, action", [
    ("мут Пашу 10m флуд", "mute"),
    ("бот, замути Пашу на 10 минут за флуд", "mute"),
    ("бот, выдай варн Паше за флуд", "warn"),
    ("варн ПАШЕ флуд", "warn"),
    ("размут Паши", "unmute"),
])
async def test_moderation_uses_saved_names(env, command, action):
    dp, bot, s, db, feed = env
    target = msg(GROUP, USER, "привет")
    await feed(upd(message=target))
    await db.set_chat_setting(GROUP, "nicks", True)
    await feed(upd(message=msg(GROUP, ADMIN, "ник паша", reply_to=target)))
    assert any(r["alias"] == "паша" for r in await db.list_nicks(GROUP))
    # Сохранённое имя работает и когда сами автоотметки выключены.
    await db.set_chat_setting(GROUP, "nicks", False)
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN, command)))
    if action == "warn":
        assert await db.get_warns(GROUP, USER) == 1
        assert any("предупреждение" in t and "флуд" in t for t in s.texts())
    else:
        restrictions = [c for c in s.calls if type(c).__name__ == "RestrictChatMember"]
        assert len(restrictions) == 1 and restrictions[0].user_id == USER
        assert restrictions[0].permissions.can_send_messages is (action == "unmute")
        if action == "mute":
            assert any("на 10 мин" in t and "флуд" in t for t in s.texts())


async def test_saved_name_ambiguity_never_punishes(env):
    dp, bot, s, db, feed = env
    for uid in (USER, OTHER):
        await feed(upd(message=msg(GROUP, uid, "привет")))
    await db.add_nick(GROUP, USER, "паша")
    await db.add_nick(GROUP, OTHER, "пашу")
    for command in ("бот замути Паше на 10 минут", "варн Паше", "варны Паше"):
        s.clear()
        await feed(upd(message=msg(GROUP, ADMIN, command)))
        assert "RestrictChatMember" not in s.names()
        assert await db.get_warns(GROUP, USER) == 0
        assert await db.get_warns(GROUP, OTHER) == 0
        assert any("нескольким участникам" in t for t in s.texts())


async def test_longest_saved_name_wins_and_preserves_reason(env):
    dp, bot, s, db, feed = env
    for uid in (USER, OTHER):
        await feed(upd(message=msg(GROUP, uid, "привет")))
    await db.add_nick(GROUP, USER, "паша")
    await db.add_nick(GROUP, OTHER, "паша петрович")
    await feed(upd(message=msg(GROUP, ADMIN, "бот выдай варн Пашу Петровича за флуд")))
    assert await db.get_warns(GROUP, USER) == 0
    assert await db.get_warns(GROUP, OTHER) == 1
    assert any("Причина: за флуд" in t for t in s.texts())


@pytest.mark.parametrize("name", ["Паша123", "Незнакомец"])
async def test_unknown_name_does_not_punish(env, name):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет")))
    await db.add_nick(GROUP, USER, "паша")
    await feed(upd(message=msg(GROUP, ADMIN, f"бот замути {name} на 10 минут")))
    assert "RestrictChatMember" not in s.names()
    assert any("имя, сохранённое для автоотметок" in t for t in s.texts())


async def test_names_respect_chat_and_admin_permissions(env):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет")))
    await db.add_nick(GROUP - 1, USER, "паша")
    await feed(upd(message=msg(GROUP, ADMIN, "варн паша флуд")))
    assert await db.get_warns(GROUP, USER) == 0
    await db.add_nick(GROUP, USER, "паша")
    await feed(upd(message=msg(GROUP, OTHER, "бот выдай варн Паше за флуд")))
    assert await db.get_warns(GROUP, USER) == 0
    assert any("Нужны права администратора" in t for t in s.texts())


async def test_name_alias_still_protects_creator(env):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, ADMIN, "привет")))
    await db.add_nick(GROUP, ADMIN, "егор")
    await feed(upd(message=msg(GROUP, ADMIN2, "бот выдай варн Егору за флуд")))
    assert await db.get_warns(GROUP, ADMIN) == 0
    assert any("создателю" in t or "создателя" in t for t in s.texts())


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


async def test_group_ai(env, monkeypatch):
    dp, bot, s, db, feed = env
    import bot.handlers.ai as aih
    aih.memory._chats.clear()
    aih._last_answer.clear()
    aih._warned.clear()
    ai = dp["ai"]
    clock = [1000.0]
    monkeypatch.setattr(aih.time, "time", lambda: clock[0])

    def replies():
        return [c for c in s.calls if type(c).__name__ == "SendMessage"]

    # выключено — бот молчит
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "ии привет")))
    assert not replies()

    await db.set_chat_setting(GROUP, "ai", True)
    # ключа нет — понятная ошибка
    await feed(upd(message=msg(GROUP, USER, "ии привет")))
    assert "не настроен" in replies()[-1].text

    seen = []

    async def fake_complete(messages, **kw):
        seen.append(messages)
        return "Привет, я бот 🙂"
    monkeypatch.setattr(ai, "complete", fake_complete)

    await feed(upd(message=msg(GROUP, OTHER, "сегодня идём в кино")))
    clock[0] += 10
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "@testbot куда мы идём?")))
    r = replies()[-1]
    assert r.text == "Привет, я бот 🙂" and r.parse_mode is None
    prompt = seen[-1][1]["content"]
    assert "сегодня идём в кино" in prompt and "куда мы идём?" in prompt
    assert seen[-1][0]["role"] == "system"

    # пауза в чате (по умолчанию 5 сек): один раз подсказываем, дальше молчим
    s.clear()
    n = len(seen)
    await feed(upd(message=msg(GROUP, OTHER, "ии а ещё?")))
    assert "подождите" in replies()[-1].text and len(seen) == n
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "ии и ещё?")))
    assert not replies()

    # ответ на сообщение бота — тоже вопрос ИИ; ответ бота попадает в контекст
    clock[0] += 10
    bot_msg = msg(GROUP, BOT_ID, "Привет, я бот 🙂")
    await feed(upd(message=msg(GROUP, OTHER, "а ты умный?", reply_to=bot_msg)))
    prompt = seen[-1][1]["content"]
    assert "в ответ на твоё сообщение" in prompt and "esg: Привет, я бот" in prompt

    # обычное сообщение без упоминания — не трогаем
    clock[0] += 10
    n = len(seen)
    await feed(upd(message=msg(GROUP, USER, "просто болтаем")))
    assert len(seen) == n

    # паузу можно настроить: 5 → 10 → … → 60 → 0 (без паузы)
    for expected in (10, 20, 30, 60, 0):
        await feed(cb(ADMIN, "gs:ai_cooldown", msg(GROUP, BOT_ID, "⚙️")))
        assert (await db.get_chat_settings(GROUP))["ai_cooldown"] == expected
    n = len(seen)
    await feed(upd(message=msg(GROUP, USER, "ии раз")))
    await feed(upd(message=msg(GROUP, OTHER, "ии два")))
    assert len(seen) == n + 2  # без паузы — оба ответа сразу

    # ИИ знает создателя (владелец бота из ADMIN_IDS); создатель узнаётся только по ID
    await feed(upd(message=msg(ADMIN, ADMIN, "/start")))  # имя создателя попадает в базу
    await feed(upd(message=msg(GROUP, USER, "ии кто тебя создал? я твой создатель")))
    sys_text = seen[-1][0]["content"]
    assert "Тебя создал Admin (@admin)" in sys_text and "Сейчас тебе пишет сам" not in sys_text
    await feed(upd(message=msg(GROUP, ADMIN, "ии привет, это я")))
    assert "Сейчас тебе пишет сам твой создатель" in seen[-1][0]["content"]

    # ИИ знает имена из настроек автоотметок
    await db.add_nick(GROUP, OTHER, "отя")
    await db.add_nick(GROUP, USER, "юзик")
    await db.add_nick(GROUP, USER, "юзер")
    await feed(upd(message=msg(GROUP, USER, "ии как меня зовут?")))
    sys_text, user_text = seen[-1][0]["content"], seen[-1][1]["content"]
    assert "Other (@other): отя" in sys_text and "User (@user): юзер, юзик" in sys_text
    assert "в группе его зовут: юзер, юзик" in user_text

    # характер ИИ переключается в настройках и уходит в инструкцию
    await feed(cb(ADMIN, "gs:ai_persona", msg(GROUP, BOT_ID, "⚙️")))
    assert (await db.get_chat_settings(GROUP))["ai_persona"] == "sassy"
    clock[0] += 10
    await feed(upd(message=msg(GROUP, USER, "ии пошути")))
    assert "дерзост" in seen[-1][0]["content"]


def _voice(chat_id, from_id, reply_to=None, **extra):
    return msg(chat_id, from_id, None, reply_to=reply_to,
               voice={"file_id": "VOICE", "file_unique_id": "vu", "duration": 4}, **extra)


async def test_voice_to_text(env, monkeypatch):
    dp, bot, s, db, feed = env
    import bot.handlers.business as biz
    import bot.handlers.voice as vh
    calls = []

    async def fake_fetch(_bot, file_id):
        calls.append(file_id)
        return b"ogg"

    async def fake_transcribe(data, name, duration=None):
        return "привет из голосового"
    for mod in (vh, biz):
        monkeypatch.setattr(mod, "fetch_audio", fake_fetch)
    monkeypatch.setattr(dp["stt"], "transcribe", fake_transcribe)

    def edits():
        return [c for c in s.calls if type(c).__name__ == "EditMessageText"]

    # группа: «текст» ответом на голосовое
    voice = _voice(GROUP, USER)
    await feed(upd(message=voice))
    s.clear()
    await feed(upd(message=msg(GROUP, OTHER, "текст", reply_to=voice)))
    assert "привет из голосового" in edits()[-1].text and calls == ["VOICE"]
    s.clear()
    await feed(upd(message=msg(GROUP, OTHER, "текст")))  # без ответа — подсказка
    assert any("Ответьте этой командой" in t for t in s.texts())

    # личка с ботом: прислать голосовое — сразу расшифровка
    s.clear()
    await feed(upd(message=_voice(USER, USER)))
    assert "привет из голосового" in edits()[-1].text

    # ЛС через ассистента: .текст ответом на голосовое собеседника
    await _connect_business(feed)
    bvoice = _voice(OTHER, OTHER)
    bvoice["business_connection_id"] = BC
    s.clear()
    await feed(upd(business_message=_bmsg(OTHER, OWNER, ".текст", reply_to_message=bvoice)))
    last = edits()[-1]
    assert "привет из голосового" in last.text and last.business_connection_id == BC


async def test_ai_answers_voice(env, monkeypatch):
    dp, bot, s, db, feed = env
    import bot.handlers.ai as aih
    aih.memory._chats.clear()
    aih._last_answer.clear()
    aih._warned.clear()
    await db.set_chat_setting(GROUP, "ai", True)

    async def fake_fetch(_bot, file_id):
        return b"ogg"

    async def fake_transcribe(data, name, duration=None):
        return "сколько будет два плюс два"
    seen = []

    async def fake_complete(messages, **kw):
        seen.append(messages)
        return "Четыре!"
    monkeypatch.setattr(aih, "fetch_audio", fake_fetch)
    monkeypatch.setattr(dp["stt"], "transcribe", fake_transcribe)
    monkeypatch.setattr(dp["ai"], "complete", fake_complete)

    # голосовое в ответ на сообщение бота — ИИ слышит и отвечает
    bot_msg = msg(GROUP, BOT_ID, "Спрашивайте")
    s.clear()
    await feed(upd(message=_voice(GROUP, USER, reply_to=bot_msg)))
    assert "сколько будет два плюс два" in seen[-1][1]["content"]
    assert any(t == "Четыре!" for t in s.texts())
    assert any("[голосовое]" in t for _, t in aih.memory.get(GROUP))

    # обычное голосовое без обращения к боту — ИИ не трогает
    aih._last_answer.clear()
    n = len(seen)
    await feed(upd(message=_voice(GROUP, OTHER)))
    assert len(seen) == n
    # голосовое с подписью «ии» — тоже вопрос
    await feed(upd(message=_voice(GROUP, OTHER, caption="ии")))
    assert len(seen) == n + 1


async def test_group_reactions(env, monkeypatch):
    dp, bot, s, db, feed = env
    import bot.handlers.reactions as rx
    rx._last.clear()
    for words, options in rx.KEYWORDS:  # все эмодзи из ключевых слов — разрешённые Telegram
        assert set(options) <= set(rx.ALLOWED), options
    assert set(rx.RANDOM) <= set(rx.ALLOWED)

    def reacts():
        return [c for c in s.calls if type(c).__name__ == "SetMessageReaction"]

    # выключено — не реагирует
    await feed(upd(message=msg(GROUP, USER, "ахаха смешно")))
    assert not reacts()

    await db.set_chat_setting(GROUP, "reactions", True)
    await db.set_chat_setting(GROUP, "reactions_chance", 100)
    clock = [1000.0]
    monkeypatch.setattr(rx.time, "time", lambda: clock[0])
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "ахаха смешно")))
    assert reacts() and reacts()[-1].reaction[0].emoji in ("😁", "🤣")
    clock[0] += 5
    await feed(upd(message=msg(GROUP, USER, "спасибо большое")))
    assert reacts()[-1].reaction[0].emoji in ("🙏", "🤝", "❤")
    # антифлуд: сразу следующее — без реакции
    n = len(reacts())
    await feed(upd(message=msg(GROUP, USER, "люблю вас")))
    assert len(reacts()) == n

    # команда «реакция 🔥» ответом
    clock[0] += 5
    target = msg(GROUP, OTHER, "пост")
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "реакция 🔥", reply_to=target)))
    r = [c for c in reacts() if c.message_id == target["message_id"]]
    assert r and r[-1].reaction[0].emoji == "🔥"
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "реакция 🦖", reply_to=target)))
    assert any("не может" in t for t in s.texts())

    # частота настраивается в /settings
    await db.set_chat_setting(GROUP, "reactions_chance", 20)
    await feed(cb(ADMIN, "gs:reactions_chance", msg(GROUP, BOT_ID, "⚙️")))
    assert (await db.get_chat_settings(GROUP))["reactions_chance"] == 35


async def test_ai_reacts_by_emotion(env, monkeypatch):
    """ИИ ставит реакцию по эмоции сообщения помимо ответа; авто-реакции — через ИИ."""
    import dataclasses

    dp, bot, s, db, feed = env
    import bot.handlers.ai as aih
    import bot.handlers.reactions as rx
    aih.memory._chats.clear()
    aih._last_answer.clear()
    rx._last.clear()
    await db.set_chat_setting(GROUP, "ai", True)

    async def rude_complete(messages, **kw):
        assert "РЕАКЦИЯ:" in messages[0]["content"]  # модель просят выбрать реакцию
        return "РЕАКЦИЯ: 🤡\nСам такой 😏"
    monkeypatch.setattr(dp["ai"], "complete", rude_complete)

    def reacts():
        return [c for c in s.calls if type(c).__name__ == "SetMessageReaction"]

    s.clear()
    q = msg(GROUP, USER, "ии ты тупой бот")
    await feed(upd(message=q))
    r = reacts()
    assert r and r[-1].reaction[0].emoji == "🤡" and r[-1].message_id == q["message_id"]
    sent = [c for c in s.calls if type(c).__name__ == "SendMessage"][-1]
    assert sent.text == "Сам такой 😏"  # строка с реакцией в ответ не попадает

    # авто-реакции на обычные сообщения: эмоцию определяет ИИ (если есть ключ)
    dp["config"] = dataclasses.replace(dp["config"], ai_api_key="sk-test")
    await db.set_chat_setting(GROUP, "reactions", True)
    await db.set_chat_setting(GROUP, "reactions_chance", 100)
    picked = []

    async def fake_pick(text):
        picked.append(text)
        return "😢" if "грустно" in text else None
    monkeypatch.setattr(dp["ai"], "pick_reaction", fake_pick)
    clock = [5000.0]
    monkeypatch.setattr(rx.time, "time", lambda: clock[0])
    s.clear()
    await feed(upd(message=msg(GROUP, OTHER, "мне сегодня грустно")))
    assert reacts() and reacts()[-1].reaction[0].emoji == "😢"
    clock[0] += 5
    s.clear()
    await feed(upd(message=msg(GROUP, OTHER, "обычное сообщение")))
    assert not reacts() and picked[-1] == "обычное сообщение"  # ИИ сказал «нет» — без реакции


async def test_admin_say_to_group(env):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет всем")))  # группа попадает в базу
    await db.execute("UPDATE chats SET title='Group' WHERE id=?", (GROUP,))
    await feed(upd(message=msg(ADMIN, ADMIN, "/admin")))
    m = msg(ADMIN, BOT_ID, "x")
    s.clear()
    await feed(cb(ADMIN, "adm:say", m))
    edit = [c for c in s.calls if type(c).__name__ == "EditMessageText"][-1]
    buttons = [b.callback_data for row in edit.reply_markup.inline_keyboard for b in row]
    assert f"adm:sg:{GROUP}" in buttons

    await feed(cb(ADMIN, f"adm:sg:{GROUP}", m))
    s.clear()
    await feed(upd(message=msg(ADMIN, ADMIN, "Всем привет от бота!")))
    copy = [c for c in s.calls if type(c).__name__ == "CopyMessage"]
    assert copy and copy[-1].chat_id == GROUP and copy[-1].from_chat_id == ADMIN
    assert any("Отправлено" in t for t in s.texts())

    # «Готово» выходит из режима — следующее сообщение уже никуда не уходит
    await feed(cb(ADMIN, "adm:main", m))
    s.clear()
    await feed(upd(message=msg(ADMIN, ADMIN, "это уже не в группу")))
    assert not [c for c in s.calls if type(c).__name__ == "CopyMessage"]

    # не-админу недоступно
    s.clear()
    await feed(cb(USER, "adm:say", msg(USER, BOT_ID, "x")))
    assert not [c for c in s.calls if type(c).__name__ == "EditMessageText"]


async def test_ai_adaptive_persona(env, monkeypatch):
    dp, bot, s, db, feed = env
    import bot.handlers.ai as aih
    aih.memory._chats.clear()
    aih._last_answer.clear()
    await feed(upd(message=msg(GROUP, USER, "йоу")))
    await db.set_chat_setting(GROUP, "ai", True)
    # режим переключается кнопкой: обычный → дерзкий → милый → краткий → адаптивный
    for _ in range(4):
        await feed(cb(ADMIN, "gs:ai_persona", msg(GROUP, BOT_ID, "⚙️")))
    assert (await db.get_chat_settings(GROUP))["ai_persona"] == "adaptive"

    seen = []

    async def fake_complete(messages, **kw):
        seen.append(messages)
        return "РЕАКЦИЯ: нет\nну ты и выдал лол"
    monkeypatch.setattr(dp["ai"], "complete", fake_complete)

    # бот запоминает, как пишет человек (только в группах с адаптивным режимом)
    for t in ("чё как пацаны))", "го катку лол", "/help", "ок"):
        await feed(upd(message=msg(GROUP, USER, t)))
    assert await db.get_style(USER) == ["чё как пацаны))", "го катку лол"]
    await feed(upd(message=msg(GROUP, USER, "ии ты кто такой вообще")))
    sys_text = seen[-1][0]["content"]
    assert "Зеркаль манеру общения" in sys_text and "жёстко" in sys_text
    assert "- чё как пацаны))" in sys_text and "- ты кто такой вообще" in sys_text
    assert await db.get_style(USER) == ["чё как пацаны))", "го катку лол", "ты кто такой вообще"]

    # в другом режиме стиль не копируется и новые сообщения не запоминаются
    await db.set_chat_setting(GROUP, "ai_persona", "normal")
    await feed(upd(message=msg(GROUP, USER, "новое сообщение")))
    assert len(await db.get_style(USER)) == 3


async def test_ai_proactive_and_punish(env, monkeypatch):
    """ИИ сам решает, вмешаться ли, и наказывает за оскорбления бота/создателя — с проверками."""
    import asyncio
    import dataclasses

    dp, bot, s, db, feed = env
    import bot.handlers.ai as aih
    aih.memory._chats.clear()
    aih._last_answer.clear()
    aih._passive_last.clear()
    dp["config"] = dataclasses.replace(dp["config"], ai_api_key="sk-test")
    await feed(upd(message=msg(GROUP, USER, "всем привет")))
    await db.set_chat_setting(GROUP, "ai", True)
    clock = [10_000.0]
    monkeypatch.setattr(aih.time, "time", lambda: clock[0])

    replies = []

    async def fake_complete(messages, **kw):
        replies.append(messages)
        return answers.pop(0)
    monkeypatch.setattr(dp["ai"], "complete", fake_complete)

    async def feed_bg(update):
        await feed(update)
        await asyncio.gather(*list(aih._bg_tasks))

    def sent():
        return [c for c in s.calls if type(c).__name__ == "SendMessage"]

    def restricts():
        return [c for c in s.calls if type(c).__name__ == "RestrictChatMember"]

    # всё выключено — ИИ на обычные сообщения не тратится
    answers = []
    await feed_bg(upd(message=msg(GROUP, USER, "этот бот тупой")))
    assert replies == []

    # «вступает сам»: посторонние сообщения не отправляются ИИ вообще
    await db.set_chat_setting(GROUP, "ai_proactive", True)
    answers = ["РЕАКЦИЯ: нет\nНАКАЗАНИЕ: нет\nПРОПУСК"]
    s.clear()
    await feed_bg(upd(message=msg(GROUP, USER, "ну и ладно")))
    assert len(replies) == 0
    assert not sent()
    clock[0] += 20
    answers = ["РЕАКЦИЯ: 🤔\nНАКАЗАНИЕ: нет\nПитон, конечно 🙂"]
    await feed_bg(upd(message=msg(GROUP, OTHER, "интересно, esg знает питон?")))
    assert sent()[-1].text == "Питон, конечно 🙂"
    # по умолчанию ИИ читает каждое сообщение
    answers = ["РЕАКЦИЯ: нет\nНАКАЗАНИЕ: нет\nПРОПУСК"]
    n = len(replies)
    await feed_bg(upd(message=msg(GROUP, OTHER, "а esg ещё что знает")))
    assert len(replies) == n + 1
    # частоту можно ограничить: 0 → 5 → 15 сек; тогда следующее сразу — не оцениваем
    for expected in (5, 15):
        await feed(cb(ADMIN, "gs:ai_passive_every", msg(GROUP, BOT_ID, "⚙️")))
        assert (await db.get_chat_settings(GROUP))["ai_passive_every"] == expected
    n = len(replies)
    await feed_bg(upd(message=msg(GROUP, OTHER, "и ещё вопрос про esg")))
    assert len(replies) == n
    await db.set_chat_setting(GROUP, "ai_passive_every", 0)

    # наказания: только за оскорбление бота/создателя, решает ИИ
    await db.set_chat_setting(GROUP, "ai_proactive", False)
    await db.set_chat_setting(GROUP, "ai_punish", True)
    n = len(replies)
    await feed_bg(upd(message=msg(GROUP, USER, "я на работе устал")))  # не про бота — не проверяем
    assert len(replies) == n
    clock[0] += 5
    answers = ["РЕАКЦИЯ: 🤡\nНАКАЗАНИЕ: мут 30\nПРИЧИНА: Бота назвали <тупой железкой>.\nПРОПУСК"]
    s.clear()
    await feed_bg(upd(message=msg(GROUP, USER, "этот бот тупая железка")))
    assert "НАКАЗАНИЕ" in replies[-1][0]["content"]
    r = restricts()
    assert r and r[-1].user_id == USER
    assert any("заглушен(а) на 30 мин" in t for t in s.texts())
    assert any("📝 Причина: Бота назвали &lt;тупой железкой&gt;." in t for t in s.texts())
    assert any("<blockquote>этот бот тупая железка</blockquote>" in t for t in s.texts())
    assert not [t for t in s.texts() if "ПРОПУСК" in t]  # молчит, только наказание

    # варны с лимитом из настроек
    await db.set_chat_setting(GROUP, "warn_limit", 2)
    for i in range(2):
        clock[0] += 5
        answers = ["РЕАКЦИЯ: 😡\nНАКАЗАНИЕ: варн\nПРОПУСК"]
        await feed_bg(upd(message=msg(GROUP, OTHER, f"да этот бот лох {i}")))
    assert any("Лимит предупреждений" in t for t in s.texts())
    assert await db.get_warns(GROUP, OTHER) == 0  # сброшены после наказания

    # создателя и админов не наказываем, даже если ИИ «захотел»
    for who in (ADMIN, ADMIN2):
        clock[0] += 5
        answers = ["РЕАКЦИЯ: 🤡\nНАКАЗАНИЕ: мут 60\nПРОПУСК"]
        s.clear()
        await feed_bg(upd(message=msg(GROUP, who, "ну и бот глупый")))
        assert not restricts()

    # при прямом обращении тоже можно наказать
    clock[0] += 10
    answers = ["РЕАКЦИЯ: 🗿\nСам такой."]
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "ии ты тупой")))
    assert any(t == "Сам такой." for t in s.texts())
    assert any("предупреждение" in t for t in s.texts())
    assert any("📝 Причина: Прямое адресное оскорбление" in t for t in s.texts())


@pytest.mark.parametrize("text, reply_id, caption", [
    ("@testbot иди нахуй", None, False),
    ("иди нахуй", BOT_ID, False),
    ("иди нахуй", ADMIN, False),
    ("@admin иди нахуй", None, False),
    ("/ии иди нахуй", None, False),
    ("@testbot иди нахуй", None, True),
])
async def test_ai_moderates_during_cooldown(env, monkeypatch, text, reply_id, caption):
    """Реальная маршрутизация: отдельная проверка до кулдауна, в том числе ответ создателю."""
    import dataclasses
    import bot.handlers.ai as aih
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет")))
    await feed(upd(message=msg(GROUP, ADMIN, "я здесь")))
    dp["config"] = dataclasses.replace(dp["config"], ai_api_key="test")
    await db.set_chat_setting(GROUP, "ai", True)
    await db.set_chat_setting(GROUP, "ai_punish", True)
    await db.set_chat_setting(GROUP, "ai_persona", "adaptive")
    await db.set_chat_setting(GROUP, "ai_passive_every", 60)
    aih._last_answer[GROUP] = time.time()
    seen = []

    async def complete(messages, **kw):
        seen.append((messages, kw))
        assert "независимый модератор" in messages[0]["content"]
        return "НАКАЗАНИЕ: варн\nПРИЧИНА: Адресное оскорбление."

    monkeypatch.setattr(dp["ai"], "complete", complete)
    reply = msg(GROUP, reply_id, "текст") if reply_id else None
    s.clear()
    for _ in range(2):
        message = (msg(GROUP, USER, reply_to=reply, caption=text) if caption
                   else msg(GROUP, USER, text, reply_to=reply))
        await feed(upd(message=message))
        await asyncio.gather(*list(aih._bg_tasks))
    assert len(seen) == 0  # точное правило выдаёт варн без решения ИИ
    assert await db.get_warns(GROUP, USER) == 2  # ровно один варн на сообщение
    assert "Выдан варн по правилу" in aih.moderation_status[GROUP][1]


async def test_ai_text_mention_creator_and_muting(env, monkeypatch):
    import dataclasses
    import bot.handlers.ai as aih
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет")))
    dp["config"] = dataclasses.replace(dp["config"], ai_api_key="test")
    await db.set_chat_setting(GROUP, "ai", True)
    await db.set_chat_setting(GROUP, "ai_punish", True)
    seen = []

    async def complete(messages, **kw):
        seen.append(messages)
        return "НАКАЗАНИЕ: мут 15\nПРИЧИНА: Грубое оскорбление создателя."

    monkeypatch.setattr(dp["ai"], "complete", complete)
    s.clear()
    await feed(upd(message=msg(GROUP, USER, "он мерзкая железка", entities=[
        {"type": "text_mention", "offset": 0, "length": 2, "user": user(ADMIN)},
    ])))
    await asyncio.gather(*list(aih._bg_tasks))
    assert len(seen) == 1
    assert "упоминание бота или создателя по ID" in seen[0][1]["content"]
    restrictions = [c for c in s.calls if type(c).__name__ == "RestrictChatMember"]
    assert len(restrictions) == 1 and restrictions[0].user_id == USER
    assert any("Причина: Грубое оскорбление создателя" in t for t in s.texts())


async def test_ai_conversation_cannot_issue_second_punishment(env, monkeypatch):
    import dataclasses
    import bot.handlers.ai as aih
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет")))
    dp["config"] = dataclasses.replace(dp["config"], ai_api_key="test")
    await db.set_chat_setting(GROUP, "ai", True)
    await db.set_chat_setting(GROUP, "ai_punish", True)
    aih._last_answer.clear()
    seen = []

    async def complete(messages, **kw):
        seen.append(messages)
        if "независимый модератор" in messages[0]["content"]:
            return "НАКАЗАНИЕ: нет\nПРИЧИНА: нет"
        return "НАКАЗАНИЕ: варн\nОбычный ответ."

    monkeypatch.setattr(dp["ai"], "complete", complete)
    await feed(upd(message=msg(GROUP, USER, "@testbot неверно отвечаешь")))
    await asyncio.gather(*list(aih._bg_tasks))
    assert len(seen) == 2
    assert await db.get_warns(GROUP, USER) == 0


@pytest.mark.parametrize("request_text, use_reply, action, seconds", [
    ("бот, выдай варн @user за флуд", False, "warn", None),
    ("@testbot дай предупреждение @user за оскорбление", False, "warn", None),
    ("esg выпиши ему варн за флуд", True, "warn", None),
    ("бот, поставь варн за флуд", True, "warn", None),
    ("бот, замути @user на 10 минут за флуд", False, "mute", 600),
    ("бот дай мут @user 2h за флуд", False, "mute", 7200),
    ("бот, пожалуйста замуть его на 1 час", True, "mute", 3600),
    ("бот замути @user на час за флуд", False, "mute", 3600),
    ("бот замути @user на полчаса за флуд", False, "mute", 1800),
    ("бот замути @user на два часа за флуд", False, "mute", 7200),
])
async def test_natural_admin_requests(env, request_text, use_reply, action, seconds):
    """Настоящие функции модерации, без вызовов ИИ и без включения ИИ в группе."""
    dp, bot, s, db, feed = env
    target = msg(GROUP, USER, "привет")
    await feed(upd(message=target))
    s.clear()
    before = int(time.time())
    await feed(upd(message=msg(GROUP, ADMIN, request_text, reply_to=target if use_reply else None)))
    if action == "warn":
        assert await db.get_warns(GROUP, USER) == 1
        assert any("предупреждение" in t and "Причина:" in t for t in s.texts())
    else:
        restrictions = [c for c in s.calls if type(c).__name__ == "RestrictChatMember"]
        assert len(restrictions) == 1 and restrictions[0].user_id == USER
        expiry = restrictions[0].until_date
        expiry = int(expiry.timestamp()) if hasattr(expiry, "timestamp") else expiry
        assert before + seconds <= expiry <= int(time.time()) + seconds


@pytest.mark.parametrize("command, mute", [
    ("бот замути Пашу на час, причину сам придумай", True),
    ("мут Пашу на час причину сам придумай", True),
    ("бот выдай варн Паше причину придумай сам", False),
])
async def test_requested_reason_with_name_and_human_duration(env, monkeypatch, command, mute):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет")))
    await db.add_nick(GROUP, USER, "паша")
    seen = []

    async def complete(messages, **kw):
        seen.append(messages)
        return "По просьбе администратора — пауза <для отдыха>."

    monkeypatch.setattr(dp["ai"], "complete", complete)
    s.clear()
    before = int(time.time())
    await feed(upd(message=msg(GROUP, ADMIN, command)))
    assert len(seen) == 1
    assert any("Причина: По просьбе администратора — пауза &lt;для отдыха&gt;." in t
               for t in s.texts())
    if mute:
        restrictions = [c for c in s.calls if type(c).__name__ == "RestrictChatMember"]
        assert len(restrictions) == 1 and restrictions[0].user_id == USER
        until_date = restrictions[0].until_date
        until_date = int(until_date.timestamp()) if hasattr(until_date, "timestamp") else until_date
        assert before + 3600 <= until_date <= int(time.time()) + 3600
        assert any("на 1 ч" in t for t in s.texts())
    else:
        assert await db.get_warns(GROUP, USER) == 1


async def test_requested_reason_fallback_and_unknown_duration(env, monkeypatch):
    from bot.services.ai import AIError
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет")))
    await db.add_nick(GROUP, USER, "паша")

    async def unavailable(*args, **kw):
        raise AIError("ИИ недоступен")

    monkeypatch.setattr(dp["ai"], "complete", unavailable)
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN, "бот замути Пашу на час сам придумай причину")))
    assert "RestrictChatMember" in s.names()
    assert any("Причина: По просьбе администратора" in t for t in s.texts())
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN, "бот замути Пашу на непонятный срок")))
    assert "RestrictChatMember" not in s.names()
    assert any("Не понял срок мута" in t for t in s.texts())


@pytest.mark.parametrize("request_text", ["бот дай варн @user за флуд",
                                     "бот замути @user на 10 минут"])
async def test_natural_request_denied_for_regular_member(env, request_text):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет")))
    s.clear()
    await feed(upd(message=msg(GROUP, OTHER, request_text)))
    assert await db.get_warns(GROUP, USER) == 0
    assert "RestrictChatMember" not in s.names()
    assert any("Нужны права администратора" in t for t in s.texts())


@pytest.mark.parametrize("text, reply_id", [
    ("какой язык учить новичку?", None),
    ("ахаха", None),
    ("у меня работа бот не причём", None),
    ("@testbot_extra как дела", None),
    ("бот@otherbot привет", None),
    ("создатель молодец", None),
    ("esg ты это видел?", OTHER),
    ("бот как дела", OTHER),
])
async def test_proactive_ignores_unrelated_conversations(env, monkeypatch, text, reply_id):
    import dataclasses
    import bot.handlers.ai as aih
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет")))
    dp["config"] = dataclasses.replace(dp["config"], ai_api_key="test")
    await db.set_chat_setting(GROUP, "ai", True)
    await db.set_chat_setting(GROUP, "ai_proactive", True)
    seen = []

    async def complete(messages, **kw):
        seen.append(messages)
        return "РЕАКЦИЯ: 🤣\nОчень хочется вмешаться!"

    monkeypatch.setattr(dp["ai"], "complete", complete)
    s.clear()
    reply = msg(GROUP, reply_id, "привет") if reply_id else None
    await feed(upd(message=msg(GROUP, USER, text, reply_to=reply)))
    await asyncio.gather(*list(aih._bg_tasks))
    assert not seen
    assert not [c for c in s.calls if type(c).__name__ == "SendMessage"]


async def test_auto_warn_without_ai_key_and_status(env):
    import bot.handlers.ai as aih
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет")))
    await db.set_chat_setting(GROUP, "ai", True)
    await db.set_chat_setting(GROUP, "ai_punish", True)
    # Настроенного ключа нет. Прямое правило работает до любого запроса к API.
    target = msg(GROUP, USER, "иди нахуй", reply_to=msg(GROUP, ADMIN, "текст"))
    await feed(upd(message=target))
    assert await db.get_warns(GROUP, USER) == 1
    last = aih.moderation_status[GROUP]
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN, "/aistatus", reply_to=target)))
    assert aih.moderation_status[GROUP] == last
    assert any("Диагностика модерации v3" in t and "Выдан варн по правилу" in t for t in s.texts())


async def test_warns_survive_failed_limit_punishment(env, monkeypatch):
    from aiogram.exceptions import TelegramBadRequest
    import bot.handlers.ai as aih
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет")))
    await db.set_chat_setting(GROUP, "ai", True)
    await db.set_chat_setting(GROUP, "ai_punish", True)
    original = s.make_request

    async def deny_restriction(bot, method, timeout=None):
        if type(method).__name__ == "RestrictChatMember":
            raise TelegramBadRequest(method=method, message="not enough rights to restrict")
        return await original(bot, method, timeout)

    monkeypatch.setattr(s, "make_request", deny_restriction)
    for _ in range(3):
        await feed(upd(message=msg(GROUP, USER, "иди нахуй",
                                  reply_to=msg(GROUP, ADMIN, "текст"))))
    assert await db.get_warns(GROUP, USER) == 3
    assert "not enough rights" in aih.moderation_status[GROUP][1]
    assert any("Не удалось применить наказание" in t for t in s.texts())


async def test_admin_request_still_protects_creator(env):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, ADMIN, "привет")))
    await feed(upd(message=msg(GROUP, ADMIN2, "бот выдай варн @admin за флуд")))
    assert await db.get_warns(GROUP, ADMIN) == 0
    assert any("создателю" in t or "создателя" in t for t in s.texts())


async def test_admin_quoted_request_does_not_execute(env):
    dp, bot, s, db, feed = env
    await feed(upd(message=msg(GROUP, USER, "привет")))
    s.clear()
    await feed(upd(message=msg(GROUP, ADMIN, 'бот, он сказал «выдай варн @user»')))
    assert await db.get_warns(GROUP, USER) == 0
    assert "RestrictChatMember" not in s.names()


async def test_ai_called_by_bot_word_and_name(env, monkeypatch):
    dp, bot, s, db, feed = env
    import bot.handlers.ai as aih
    aih.memory._chats.clear()
    aih._last_answer.clear()
    await feed(upd(message=msg(GROUP, USER, "привет")))
    seen = []

    async def fake_complete(messages, **kw):
        seen.append(messages)
        return "РЕАКЦИЯ: нет\nЯ esg 🙂"
    monkeypatch.setattr(dp["ai"], "complete", fake_complete)
    clock = [1000.0]
    monkeypatch.setattr(aih.time, "time", lambda: clock[0])

    # ИИ выключен — «бот …» не перехватывается
    await feed(upd(message=msg(GROUP, USER, "бот, как дела?")))
    assert not seen
    await db.set_chat_setting(GROUP, "ai", True)
    for text, question in (("бот, как дела?", "как дела?"), ("Бот расскажи анекдот", "расскажи анекдот"),
                           ("esg как тебя зовут", "как тебя зовут"), ("ИИ: привет", "привет")):
        clock[0] += 10
        await feed(upd(message=msg(GROUP, USER, text)))
        assert question in seen[-1][1]["content"], text
    assert "Тебя зовут esg" in seen[-1][0]["content"]
    # слово «бот» не в начале — не прямое обращение
    n = len(seen)
    clock[0] += 10
    await feed(upd(message=msg(GROUP, USER, "у меня работа бот не причём")))
    assert len(seen) == n
