"""Автоотметки по именам в группах.

Админ включает функцию в /settings и запускает мастер «🏷 Настроить имена»: бот по очереди
спрашивает про каждого участника «Как запомнить @pasha?», админ отвечает на сообщение
именем («паша», можно несколько через запятую). Потом, когда в чате пишут это имя в любом
падеже («Паше», «Пашу», «с Пашей»), бот отвечает на сообщение и отмечает человека.
"""
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyParameters,
    TelegramObject,
)

from ..db import Database
from ..services.userbot import Userbot
from ..utils.filters import Cmd
from ..utils.morph import alias_key, contains, norm, text_keys
from ..utils.perms import is_chat_admin, message_from_admin
from ..utils.text import esc, mention
from .call import collect_members, tag

router = Router(name="nicks")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

MENTION_COOLDOWN = 30  # не чаще раза в 30 сек на одного человека в чате
NICK_CMDS = {"ник", "ники", "забыть", "nick", "nicks"}

_wizards: dict[int, dict] = {}                       # chat_id -> состояние мастера
_cache: dict[int, list[tuple[int, tuple[str, ...]]]] = {}  # chat_id -> [(user_id, ключ имени)]
_last_ping: dict[tuple[int, int], float] = {}


def B(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


async def nick_index(db: Database, chat_id: int) -> list[tuple[int, tuple[str, ...]]]:
    if chat_id not in _cache:
        _cache[chat_id] = [(r["user_id"], alias_key(r["alias"])) for r in await db.list_nicks(chat_id)]
    return _cache[chat_id]


def parse_aliases(text: str) -> list[str]:
    out = []
    for part in text.replace("\n", ",").split(","):
        alias = " ".join(norm(part).split())
        if len(alias) >= 2 and alias_key(alias) and alias not in out:
            out.append(alias)
    return out


# ---------- мастер ----------

def _wizard_text(w: dict, user_aliases: list[str]) -> str:
    uid, name, username = w["queue"][w["idx"]]
    who = f"@{username}" if username else esc(name)
    known = ", ".join(f"<b>{esc(a)}</b>" for a in user_aliases) or "пока нет"
    return (f"🏷 <b>Имена для автоотметок</b> ({w['idx'] + 1}/{len(w['queue'])})\n\n"
            f"Как запомнить пользователя {who}?\n"
            "Ответьте на это сообщение именем, например <code>паша</code>. "
            "Можно несколько через запятую: <code>паша, павел</code>.\n\n"
            f"Уже запомнено: {known}")


def _wizard_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [B("➕ Ещё вариант", "nk:more"), B("➡️ Следующий", "nk:next")],
        [B("⏹ Готово", "nk:stop")],
    ])


async def _user_aliases(db: Database, chat_id: int, uid: int) -> list[str]:
    return [r["alias"] for r in await db.list_nicks(chat_id) if r["user_id"] == uid]


async def start_wizard(bot: Bot, db: Database, userbot: Userbot, chat_id: int, admin_id: int,
                       answer) -> None:
    members, _ = await collect_members(bot, db, userbot, chat_id)
    me = await bot.me()
    members.pop(me.id, None)
    queue = sorted(((uid, n or str(uid), u) for uid, (n, u) in members.items()),
                   key=lambda x: (x[1] or "").lower())
    if not queue:
        await answer("Пока некого запоминать — я ещё не видел участников этого чата.")
        return
    w = {"admin": admin_id, "queue": queue, "idx": 0, "msg": None}
    _wizards[chat_id] = w
    msg = await answer(_wizard_text(w, await _user_aliases(db, chat_id, queue[0][0])),
                       reply_markup=_wizard_kb())
    w["msg"] = msg.message_id


async def _refresh(bot: Bot, db: Database, chat_id: int) -> None:
    w = _wizards[chat_id]
    uid = w["queue"][w["idx"]][0]
    try:
        await bot.edit_message_text(_wizard_text(w, await _user_aliases(db, chat_id, uid)),
                                    chat_id=chat_id, message_id=w["msg"], reply_markup=_wizard_kb())
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "nk:start")
async def cb_start(call: CallbackQuery, bot: Bot, db: Database, userbot: Userbot):
    chat_id = call.message.chat.id
    if not await is_chat_admin(bot, chat_id, call.from_user.id):
        return await call.answer("Только для администраторов", show_alert=True)
    if not await db.group_feature(chat_id, "nicks"):
        return await call.answer("Сначала включите «🏷 Автоотметки по именам»", show_alert=True)
    await call.answer()
    await start_wizard(bot, db, userbot, chat_id, call.from_user.id, call.message.answer)


@router.callback_query(F.data.in_({"nk:more", "nk:next", "nk:stop"}))
async def cb_wizard(call: CallbackQuery, bot: Bot, db: Database):
    chat_id = call.message.chat.id
    w = _wizards.get(chat_id)
    if not w or w["msg"] != call.message.message_id:
        return await call.answer("Мастер уже закрыт", show_alert=True)
    if call.from_user.id != w["admin"]:
        return await call.answer("Имена настраивает тот, кто запустил мастер", show_alert=True)
    if call.data == "nk:more":
        return await call.answer("Ответьте на это сообщение ещё одним вариантом имени",
                                 show_alert=True)
    if call.data == "nk:next" and w["idx"] + 1 < len(w["queue"]):
        w["idx"] += 1
        await _refresh(bot, db, chat_id)
        return await call.answer()
    _wizards.pop(chat_id, None)
    total = len(await db.list_nicks(chat_id))
    await call.message.edit_text(f"✅ Готово! Запомнено имён: {total}.\n"
                                 "Список — <code>ники</code>, удалить — <code>забыть ник имя</code>.")
    await call.answer()


def _is_wizard_reply(message: Message) -> bool:
    w = _wizards.get(message.chat.id)
    r = message.reply_to_message
    return bool(w and r and r.message_id == w["msg"] and message.from_user
                and message.from_user.id == w["admin"] and message.text)


@router.message(_is_wizard_reply)
async def wizard_reply(message: Message, bot: Bot, db: Database):
    chat_id = message.chat.id
    w = _wizards[chat_id]
    uid = w["queue"][w["idx"]][0]
    for alias in parse_aliases(message.text):
        await db.add_nick(chat_id, uid, alias)
    _cache.pop(chat_id, None)
    try:
        await message.delete()  # не засоряем чат ответами мастеру
    except TelegramBadRequest:
        pass
    await _refresh(bot, db, chat_id)


# ---------- команды ----------

@router.message(Cmd("ники", "nicks"))
async def cmd_list(message: Message, db: Database):
    if not await db.group_feature(message.chat.id, "nicks"):
        return
    rows = await db.list_nicks(message.chat.id)
    if not rows:
        return await message.reply("🏷 Имён пока нет. Админ может настроить их в /settings.")
    by_user: dict[int, list] = {}
    for r in rows:
        by_user.setdefault(r["user_id"], []).append(r)
    lines = ["🏷 <b>Имена для автоотметок</b>\n"]
    for uid, items in by_user.items():
        first = items[0]
        who = f"@{first['username']}" if first["username"] else mention(uid, first["first_name"])
        lines.append(f"• {who}: " + ", ".join(esc(i["alias"]) for i in items))
    await message.answer("\n".join(lines))


@router.message(Cmd("ник", "nick"))
async def cmd_add(message: Message, bot: Bot, db: Database, args: str):
    """ник имя — ответом на сообщение человека; или: ник @юзернейм имя."""
    chat_id = message.chat.id
    if not await db.group_feature(chat_id, "nicks"):
        return
    if not await message_from_admin(message, bot):
        return await message.reply("⛔ Имена задают администраторы.")
    reply = message.reply_to_message
    target = None
    if reply and reply.from_user and not reply.from_user.is_bot and not reply.forum_topic_created:
        target = reply.from_user.id
    elif args.startswith("@"):
        handle, _, args = args.partition(" ")
        row = await db.find_member_by_username(chat_id, handle)
        target = row["user_id"] if row else None
    aliases = parse_aliases(args)
    if not target or not aliases:
        return await message.reply("Пример: ответом на сообщение — <code>ник паша</code>, "
                                   "или <code>ник @pasha паша, павел</code>")
    for alias in aliases:
        await db.add_nick(chat_id, target, alias)
    _cache.pop(chat_id, None)
    await message.reply("✅ Запомнил: " + ", ".join(f"<b>{esc(a)}</b>" for a in aliases))


@router.message(Cmd("забыть", "forget"), F.text.lower().regexp(r"^\S+\s+ник"))
async def cmd_forget(message: Message, bot: Bot, db: Database, args: str):
    chat_id = message.chat.id
    if not await message_from_admin(message, bot):
        return await message.reply("⛔ Имена удаляют администраторы.")
    alias = norm(args.split(maxsplit=1)[1]).strip() if " " in args else ""
    if alias and await db.remove_nick(chat_id, " ".join(alias.split())):
        _cache.pop(chat_id, None)
        return await message.reply(f"🗑 Забыл имя <b>{esc(alias)}</b>")
    await message.reply("Не нашёл такого имени. Список — <code>ники</code>")


# ---------- автоотметка ----------

class NickMentionMiddleware(BaseMiddleware):
    """После обработки сообщения в группе ищет в нём запомненные имена и отмечает людей."""

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        result = await handler(event, data)
        if isinstance(event, Message):
            try:
                await self.check(event, data)
            except Exception:  # noqa: BLE001 — автоотметка не должна ломать остальное
                pass
        return result

    @staticmethod
    async def check(message: Message, data: dict[str, Any]) -> None:
        if message.chat.type not in ("group", "supergroup") or not message.text:
            return
        if not message.from_user or message.from_user.is_bot or _is_wizard_reply(message):
            return
        text = message.text.strip()
        first = text.split(maxsplit=1)[0].lower().lstrip("/!.") if text else ""
        if text[:1] in "/!." or first in NICK_CMDS:
            return  # команды и настройка имён не отмечают
        db: Database = data["db"]
        bot: Bot = data["bot"]
        if not await db.group_feature(message.chat.id, "nicks"):
            return
        index = await nick_index(db, message.chat.id)
        if not index:
            return
        keys = text_keys(text)
        now = time.time()
        found = []
        for uid, key in index:
            if uid == message.from_user.id or uid in found or not contains(keys, key):
                continue
            if now - _last_ping.get((message.chat.id, uid), 0) < MENTION_COOLDOWN:
                continue
            found.append(uid)
        if not found:
            return
        tags = []
        for uid in found:
            _last_ping[(message.chat.id, uid)] = now
            m = await db.get_member(message.chat.id, uid)
            tags.append(tag(uid, m["first_name"] if m else None, m["username"] if m else None))
        await bot.send_message(
            message.chat.id, "👋 " + " ".join(tags),
            reply_parameters=ReplyParameters(message_id=message.message_id,
                                             allow_sending_without_reply=True),
            message_thread_id=message.message_thread_id if message.is_topic_message else None,
        )
