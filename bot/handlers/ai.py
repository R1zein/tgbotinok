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

from ..config import Config
from ..db import Database
from ..services.ai import AIClient, AIError, ChatMemory, build_messages
from ..services.stt import STTError, Transcriber, fetch_audio, media_of
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


async def group_people(db: Database, chat_id: int,
                       author_id: int) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Имена из настроек автоотметок: [(«Имя (@ник)», [имена])] и имена автора вопроса."""
    by_user: dict[int, tuple[str, list[str]]] = {}
    for r in await db.list_nicks(chat_id):
        if r["user_id"] not in by_user:
            name = r["first_name"] or (f"@{r['username']}" if r["username"] else str(r["user_id"]))
            who = f"{name} (@{r['username']})" if r["username"] and r["first_name"] else name
            by_user[r["user_id"]] = (who, [])
        by_user[r["user_id"]][1].append(r["alias"])
    author = by_user.get(author_id)
    return list(by_user.values()), (author[1] if author else [])


async def creator_label(db: Database, config: Config) -> str | None:
    """Как ИИ называет своего создателя: имя и @юзернейм владельца бота (первый из ADMIN_IDS)."""
    if not config.admin_ids:
        return None
    uid = min(config.admin_ids)
    row = await db.fetchone("SELECT first_name, username FROM users WHERE id=?", (uid,))
    name = row["first_name"] if row and row["first_name"] else None
    username = f"@{row['username']}" if row and row["username"] else None
    if name and username:
        return f"{name} ({username})"
    return name or username or "владелец этого бота"


async def _cooldown_ok(message: Message, db: Database) -> bool:
    """Пауза между ответами ИИ в чате. True — можно отвечать (и пауза запускается)."""
    chat_id = message.chat.id
    cooldown = int((await db.get_chat_settings(chat_id)).get("ai_cooldown", 5))
    now = time.time()
    left = cooldown - (now - _last_answer.get(chat_id, 0))
    if left > 0:
        # подсказываем один раз за паузу, чтобы не превращать ожидание в спам
        if _warned.get(chat_id, 0) < _last_answer.get(chat_id, 0):
            _warned[chat_id] = now
            await message.reply(f"⏳ ИИ отвечает не чаще раза в {cooldown} сек — "
                                f"подождите ещё {int(left) + 1} сек.")
        return False
    _last_answer[chat_id] = now
    return True


async def _typing(bot: Bot, message: Message) -> None:
    try:
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING,
                                   message_thread_id=message.message_thread_id
                                   if message.is_topic_message else None)
    except Exception:  # noqa: BLE001
        pass


async def _respond(message: Message, bot: Bot, db: Database, ai: AIClient, config: Config,
                   question: str, memory_text: str) -> None:
    chat_id = message.chat.id
    persona = (await db.get_chat_settings(chat_id)).get("ai_persona", "normal")
    me = await bot.me()
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == me.id and reply.text:
        question = f"(в ответ на твоё сообщение «{reply.text[:300]}») {question}"
    people, author_names = await group_people(db, chat_id, message.from_user.id)
    messages = build_messages(me.first_name or "бот", persona, memory.get(chat_id),
                              author_name(message), question,
                              creator=await creator_label(db, config),
                              author_is_creator=message.from_user.id in config.admin_ids,
                              people=people, author_names=author_names)
    memory.add(chat_id, author_name(message), memory_text)
    if len(_recorded) > 5000:
        _recorded.clear()
    _recorded.add((chat_id, message.message_id))
    await _typing(bot, message)
    try:
        answer = await ai.complete(messages)
    except AIError as e:
        await message.reply(f"🤖 {e}", parse_mode=None)
        return
    answer = answer[:3500] or "🤔"
    memory.add(chat_id, me.first_name or "бот", answer)
    await message.reply(answer, parse_mode=None)


@router.message(ai_trigger)
async def ai_answer(message: Message, bot: Bot, db: Database, ai: AIClient, config: Config,
                    question: str):
    if not await db.group_feature(message.chat.id, "ai"):
        return
    if not question:
        return await message.reply("🤖 Спросите что-нибудь: <code>ии как дела?</code>")
    if await _cooldown_ok(message, db):
        await _respond(message, bot, db, ai, config, question, message.text)


async def ai_voice_trigger(message: Message, bot: Bot) -> bool:
    """Голосовое/кружок в ответ на сообщение бота или с подписью «ии …» / @бот."""
    if not (message.voice or message.video_note) or not message.from_user \
            or message.from_user.is_bot:
        return False
    me = await bot.me()
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == me.id:
        return True
    caption = (message.caption or "").lower()
    word = caption.split(maxsplit=1)[0].lstrip("".join(PREFIXES)) if caption else ""
    return word in AI_CMDS or bool(me.username and f"@{me.username}".lower() in caption)


@router.message(ai_voice_trigger)
async def ai_voice(message: Message, bot: Bot, db: Database, ai: AIClient, config: Config,
                   stt: Transcriber):
    """ИИ отвечает на голосовые: расшифровка Whisper → вопрос к ИИ."""
    if not await db.group_feature(message.chat.id, "ai"):
        return
    if not stt.enabled or not db.feature_enabled("stt"):
        return await message.reply("🤖 Голосовые я пока не понимаю — расшифровка не настроена.")
    if not await _cooldown_ok(message, db):
        return
    await _typing(bot, message)
    file_id, name, duration = media_of(message)
    try:
        text = await stt.transcribe(await fetch_audio(bot, file_id), name, duration)
    except STTError as e:
        return await message.reply(f"🤖 {e}", parse_mode=None)
    except Exception:  # noqa: BLE001
        return await message.reply("🤖 Не получилось расслышать это сообщение.", parse_mode=None)
    if not text:
        return await message.reply("🤖 Не разобрал, что сказано 🙉", parse_mode=None)
    await _respond(message, bot, db, ai, config, text, f"[голосовое] {text}")


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
