import asyncio
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import Message

from ..db import Database
from ..services.userbot import Userbot
from ..utils.filters import Cmd
from ..utils.perms import message_from_admin
from ..utils.text import chunks, esc, mention, user_mention

log = logging.getLogger(__name__)
router = Router(name="call")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

COOLDOWN = 300
MENTIONS_PER_MESSAGE = 20
_last_call: dict[int, float] = {}


def tag(user_id: int, name: str | None, username: str | None) -> str:
    # @юзернейм уведомляет всегда; ссылка по ID — только если Telegram «знает» пользователя для бота
    return f"@{username}" if username else mention(user_id, name)


async def collect_members(bot: Bot, db: Database, userbot: Userbot,
                          chat_id: int) -> tuple[dict[int, tuple[str, str | None]], bool]:
    """Участники группы: полный список через юзербот + администраторы + все, кого бот видел.
    Возвращает ({id: (имя, юзернейм)}, получен_ли_полный_список)."""
    members: dict[int, tuple[str, str | None]] = {}
    full = False
    if userbot.ready:
        try:
            for uid, name, username in await userbot.group_members(bot, chat_id):
                members[uid] = (name, username)
                await db.touch_member(chat_id, uid, name, username)
            full = True
        except Exception as e:  # noqa: BLE001
            log.warning("Юзербот не смог получить участников %s: %s", chat_id, e)
    try:
        for adm in await bot.get_chat_administrators(chat_id):
            u = adm.user
            if not u.is_bot and not getattr(adm, "is_anonymous", False):  # анонимов не раскрываем
                members.setdefault(u.id, (u.full_name, u.username))
    except Exception as e:  # noqa: BLE001
        log.info("get_chat_administrators %s: %s", chat_id, e)
    for m in await db.list_members(chat_id):
        members.setdefault(m["user_id"], (m["first_name"], m["username"]))
    return members, full


@router.message(Cmd("call", "калл", "колл", "everyone", "все", "всех"))
async def cmd_call(message: Message, bot: Bot, db: Database, userbot: Userbot, args: str):
    chat_id = message.chat.id
    if not await db.group_feature(chat_id, "call"):
        return
    # «все»/«всех» без префикса — частые слова, срабатываем только с префиксом
    first = (message.text or "").split(maxsplit=1)[0].lower()
    if first in ("все", "всех"):
        return
    settings = await db.get_chat_settings(chat_id)
    is_admin = await message_from_admin(message, bot)
    if settings.get("call_admins_only") and not is_admin:
        return await message.reply("⛔ Калл доступен только администраторам.")
    left = COOLDOWN - (time.time() - _last_call.get(chat_id, 0))
    if left > 0 and not is_admin:
        return await message.reply(f"⏳ Калл можно использовать раз в 5 минут. Осталось {int(left)} сек.")
    _last_call[chat_id] = time.time()

    me = await bot.me()
    caller_id = message.from_user.id if message.from_user else 0
    members, full = await collect_members(bot, db, userbot, chat_id)
    members.pop(caller_id, None)
    members.pop(me.id, None)
    if not members:
        return await message.reply("Пока некого звать — я ещё не видел участников этого чата.")
    who = user_mention(message.from_user) if message.from_user else "Администратор"
    header = f"📢 {who} вызывает всех!"
    if args:
        header += f"\n💬 {esc(args)}"
    if not full:
        try:
            total = await bot.get_chat_member_count(chat_id)
            if total - 2 > len(members):  # минус бот и вызывающий
                header += (f"\n\n<i>Отмечено {len(members)} из ~{total - 1}: Telegram не даёт боту "
                           "полный список участников. Остальные появятся, когда напишут в чат.</i>")
        except Exception:  # noqa: BLE001
            pass
    await message.answer(header)
    parts = list(chunks(list(members.items()), MENTIONS_PER_MESSAGE))
    pause = 0.5 if len(parts) <= 15 else 3.0  # лимит Telegram — ~20 сообщений в минуту в группу
    for part in parts:
        text = " ".join(tag(uid, name, username) for uid, (name, username) in part)
        for _ in range(3):
            try:
                await message.answer(text)
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
        await asyncio.sleep(pause)
