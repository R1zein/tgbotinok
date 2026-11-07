"""ИИ-ответы в группах.

Бот отвечает, когда его упомянули (@бот …), ответили на его сообщение или написали
«ии вопрос». Для контекста помнит последние сообщения группы (только в памяти, и только
в группах, где функция включена). Включается администратором в /settings.
"""
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.types import Message, TelegramObject

from ..db import Database
from ..services.ai import AIClient, AIError, ChatMemory, build_messages
from ..utils.filters import PREFIXES

router = Router(name="ai")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

COOLDOWNS = [0, 3, 5, 10, 20, 30, 60]  # варианты паузы между ответами ИИ в чате (сек)
AI_CMDS = {"ии", "ai", "нейро", "gpt"}
memory = ChatMemory()
_last_answer: dict[int, float] = {}       # чат -> когда ИИ начал последний ответ
_warned: dict[int, float] = {}            # чат -> когда последний раз писали «подождите»
_recorded: set[tuple[int, int]] = set()  # вопросы, уже записанные в память обработчиком


def author_name(message: Message) -> str:
    u = message.from_user
    return (u.full_name or u.username or str(u.id)) if u else "кто-то"


async def ai_trigger(message: Message, bot: Bot) -> bool | dict:
    """Упоминание бота, ответ на его сообщение или команда «ии …». Передаёт `question`."""
    text = (message.text or "").strip()
    if not text or not message.from_user or message.from_user.is_bot:
        return False
    me = await bot.me()
    first, _, rest = text.partition(" ")
    word = first.lower().lstrip("".join(PREFIXES)).split("@")[0]
    if word in AI_CMDS:
        return {"question": rest.strip()}
    handle = f"@{me.username}".lower() if me.username else None
    if handle and handle in text.lower():
        idx = text.lower().index(handle)
        question = (text[:idx] + text[idx + len(handle):]).strip(" ,:")
        return {"question": question}
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == me.id and text[:1] not in PREFIXES:
        return {"question": text}
    return False


@router.message(ai_trigger)
async def ai_answer(message: Message, bot: Bot, db: Database, ai: AIClient, question: str):
    chat_id = message.chat.id
    if not await db.group_feature(chat_id, "ai"):
        return
    if not question:
        return await message.reply("🤖 Спросите что-нибудь: <code>ии как дела?</code>")
    settings = await db.get_chat_settings(chat_id)
    cooldown = int(settings.get("ai_cooldown", 5))
    now = time.time()
    left = cooldown - (now - _last_answer.get(chat_id, 0))
    if left > 0:
        # подсказываем один раз за паузу, чтобы не превращать ожидание в спам
        if _warned.get(chat_id, 0) < _last_answer.get(chat_id, 0):
            _warned[chat_id] = now
            await message.reply(f"⏳ ИИ отвечает не чаще раза в {cooldown} сек — "
                                f"подождите ещё {int(left) + 1} сек.")
        return
    _last_answer[chat_id] = now
    persona = settings.get("ai_persona", "normal")
    me = await bot.me()
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == me.id and reply.text:
        question = f"(в ответ на твоё сообщение «{reply.text[:300]}») {question}"
    messages = build_messages(me.first_name or "бот", persona, memory.get(chat_id),
                              author_name(message), question)
    memory.add(chat_id, author_name(message), message.text)
    if len(_recorded) > 5000:
        _recorded.clear()
    _recorded.add((chat_id, message.message_id))
    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING,
                                   message_thread_id=message.message_thread_id
                                   if message.is_topic_message else None)
    except Exception:  # noqa: BLE001
        pass
    try:
        answer = await ai.complete(messages)
    except AIError as e:
        return await message.reply(f"🤖 {e}", parse_mode=None)
    answer = answer[:3500] or "🤔"
    memory.add(chat_id, me.first_name or "бот", answer)
    await message.reply(answer, parse_mode=None)


class AIMemoryMiddleware(BaseMiddleware):
    """Запоминает сообщения групп, где включены ИИ-ответы, — для контекста."""

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        result = await handler(event, data)
        if (isinstance(event, Message) and event.chat.type in ("group", "supergroup")
                and event.text and event.from_user and not event.from_user.is_bot):
            try:
                if (event.chat.id, event.message_id) in _recorded:
                    _recorded.discard((event.chat.id, event.message_id))
                elif await data["db"].group_feature(event.chat.id, "ai"):
                    memory.add(event.chat.id, author_name(event), event.text)
            except Exception:  # noqa: BLE001
                pass
        return result
