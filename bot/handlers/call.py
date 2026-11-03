import asyncio
import time

from aiogram import Bot, F, Router
from aiogram.types import Message

from ..db import Database
from ..utils.filters import Cmd
from ..utils.perms import message_from_admin
from ..utils.text import chunks, esc, mention, user_mention

router = Router(name="call")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

COOLDOWN = 300
MENTIONS_PER_MESSAGE = 20
_last_call: dict[int, float] = {}


@router.message(Cmd("call", "калл", "колл", "everyone", "все", "всех"))
async def cmd_call(message: Message, bot: Bot, db: Database, args: str):
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
    members = [m for m in await db.list_members(chat_id)
               if m["user_id"] not in (caller_id, me.id)]
    if not members:
        return await message.reply("Пока некого звать — я ещё не видел участников этого чата.")
    who = user_mention(message.from_user) if message.from_user else "Администратор"
    header = f"📢 {who} вызывает всех!"
    if args:
        header += f"\n💬 {esc(args)}"
    await message.answer(header)
    for part in chunks(members, MENTIONS_PER_MESSAGE):
        await message.answer(" ".join(mention(m["user_id"], m["first_name"]) for m in part))
        await asyncio.sleep(0.5)
