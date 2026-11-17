"""ИИ-ответы в группах.

Бот отвечает, когда его упомянули (@бот …), ответили на его сообщение или написали
«ии вопрос». Для контекста помнит последние сообщения группы (только в памяти, и только
в группах, где функция включена). Включается администратором в /settings.
"""
import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, TelegramObject

from ..config import Config
from ..db import Database
from ..services.ai import AIClient, AIError, ChatMemory, build_messages, parse_reply
from ..services.stt import STTError, Transcriber, fetch_audio, media_of
from ..utils.filters import PREFIXES
from ..utils.perms import is_chat_admin
from ..utils.text import esc, mention
from .moderation import do_ban, do_kick, do_mute, undo_kb
from .reactions import react

log = logging.getLogger(__name__)
router = Router(name="ai")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

COOLDOWNS = [0, 3, 5, 10, 20, 30, 60]  # варианты паузы между ответами ИИ в чате (сек)
AI_CMDS = {"ии", "ai", "нейро", "gpt", "бот", "bot", "ботик"}
memory = ChatMemory()
_last_answer: dict[int, float] = {}       # чат -> когда ИИ начал последний ответ
_warned: dict[int, float] = {}            # чат -> когда последний раз писали «подождите»
_recorded: set[tuple[int, int]] = set()  # вопросы, уже записанные в память обработчиком


def author_name(message: Message) -> str:
    u = message.from_user
    return (u.full_name or u.username or str(u.id)) if u else "кто-то"


def call_words(config: Config) -> set[str]:
    """Слова в начале сообщения, на которые откликается ИИ: «ии», «бот», его имя (esg)…"""
    return AI_CMDS | {config.ai_name.lower()}


async def ai_trigger(message: Message, bot: Bot, config: Config, db: Database) -> bool | dict:
    """Обращение «ии/бот/esg …», упоминание бота или ответ на его сообщение. Передаёт `question`."""
    text = (message.text or "").strip()
    if not text or not message.from_user or message.from_user.is_bot:
        return False
    if not await db.group_feature(message.chat.id, "ai"):
        return False  # ИИ выключен — сообщение идёт дальше (режимы текста, RP и т.д.)
    me = await bot.me()
    first, _, rest = text.partition(" ")
    word = first.lower().lstrip("".join(PREFIXES)).split("@")[0].rstrip(",.!?:;")
    if word in call_words(config):
        return {"question": rest.strip(" ,:")}
    handle = f"@{me.username}".lower() if me.username else None
    if handle and handle in text.lower():
        idx = text.lower().index(handle)
        question = (text[:idx] + text[idx + len(handle):]).strip(" ,:")
        return {"question": question}
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == me.id and text[:1] not in PREFIXES:
        return {"question": text}
    return False


async def remember_style(db: Database, user_id: int, text: str | None) -> None:
    """Запоминаем, как пишет человек (для адаптивного режима). Команды и пустое не берём."""
    text = (text or "").strip()
    if len(text) < 3 or text[:1] in PREFIXES or text.startswith("[голосовое]"):
        return
    await db.add_style_sample(user_id, text)


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


async def apply_punishment(message: Message, bot: Bot, db: Database, config: Config,
                           punish: tuple[str, int], reason: str = "",
                           evidence: str = "") -> None:
    """Варн или мут, которые решил выдать ИИ за оскорбление бота/создателя."""
    chat_id, user = message.chat.id, message.from_user
    if not user or user.is_bot or user.id in config.admin_ids:
        return  # создателя не наказываем
    if await is_chat_admin(bot, chat_id, user.id):
        return  # админов Telegram ограничить не даст — и не пытаемся
    who = mention(user.id, user.full_name)
    kind, minutes = punish
    reason = reason.strip()
    if not reason or reason.lower() in {"нет", "none", "-"}:
        reason = "Адресное оскорбление бота или его создателя."
    details = f"\n📝 Причина: {esc(reason[:300])}"
    source = evidence or message.text or message.caption or ""
    if source:
        details += f"\n💬 Сообщение: <blockquote>{esc(source[:500])}</blockquote>"
    try:
        if kind == "mute":
            await do_mute(bot, chat_id, user.id, minutes * 60)
            await message.reply(f"🔇 {who} заглушен(а) на {minutes} мин.{details}",
                                reply_markup=undo_kb("unmute", user.id, "🔊 Размутить"))
            return
        settings = await db.get_chat_settings(chat_id)
        limit = int(settings.get("warn_limit", 3))
        count = await db.add_warn(chat_id, user.id)
        text = f"⚠️ {who} получает предупреждение ({count}/{limit}).{details}"
        if count >= limit:
            await db.reset_warns(chat_id, user.id)
            action = settings.get("warn_action", "mute")
            if action == "ban":
                await do_ban(bot, chat_id, user.id, None)
                text += "\n⛔ Лимит предупреждений — бан."
            elif action == "kick":
                await do_kick(bot, chat_id, user.id)
                text += "\n👢 Лимит предупреждений — кик."
            else:
                await do_mute(bot, chat_id, user.id, 86400)
                text += "\n🔇 Лимит предупреждений — мут на сутки."
            await message.reply(text)
        else:
            await message.reply(text, reply_markup=undo_kb("unwarn", user.id, "➖ Снять варн"))
    except TelegramBadRequest as e:
        log.info("ИИ не смог наказать %s: %s", user.id, e)
        await message.reply("⚠️ Хотел наказать, но у меня нет прав администратора на это.")


async def _respond(message: Message, bot: Bot, db: Database, ai: AIClient, config: Config,
                   question: str, memory_text: str) -> None:
    chat_id = message.chat.id
    settings = await db.get_chat_settings(chat_id)
    persona = settings.get("ai_persona", "normal")
    punish = bool(settings.get("ai_punish"))
    raw_question = question
    me = await bot.me()
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == me.id and reply.text:
        question = f"(в ответ на твоё сообщение «{reply.text[:300]}») {question}"
    people, author_names = await group_people(db, chat_id, message.from_user.id)
    style = None
    if persona == "adaptive":
        await remember_style(db, message.from_user.id, raw_question)
        style = await db.get_style(message.from_user.id)
    messages = build_messages(config.ai_name, persona, memory.get(chat_id),
                              author_name(message), question,
                              creator=await creator_label(db, config),
                              author_is_creator=message.from_user.id in config.admin_ids,
                              people=people, author_names=author_names, author_style=style,
                              punish=punish)
    memory.add(chat_id, author_name(message), memory_text)
    if len(_recorded) > 5000:
        _recorded.clear()
    _recorded.add((chat_id, message.message_id))
    await _typing(bot, message)
    try:
        answer = await ai.complete(messages, temperature=0.3 if punish else 1.0)
    except AIError as e:
        await message.reply(f"🤖 {e}", parse_mode=None)
        return
    res = parse_reply(answer)
    if res["reaction"]:  # реакция на сообщение по его эмоции — помимо ответа
        await react(bot, chat_id, message.message_id, res["reaction"])
    answer = res["text"][:3500] or "🤔"
    memory.add(chat_id, config.ai_name, answer)
    await message.reply(answer, parse_mode=None)
    if punish and res["punish"]:
        await apply_punishment(message, bot, db, config, res["punish"],
                               res["reason"], memory_text)


@router.message(ai_trigger)
async def ai_answer(message: Message, bot: Bot, db: Database, ai: AIClient, config: Config,
                    question: str):
    if not await db.group_feature(message.chat.id, "ai"):
        return
    if not question:
        return await message.reply("🤖 Спросите что-нибудь: <code>ии как дела?</code>")
    if await _cooldown_ok(message, db):
        await _respond(message, bot, db, ai, config, question, message.text)


async def ai_voice_trigger(message: Message, bot: Bot, config: Config) -> bool:
    """Голосовое/кружок в ответ на сообщение бота или с подписью «ии …» / @бот."""
    if not (message.voice or message.video_note) or not message.from_user \
            or message.from_user.is_bot:
        return False
    me = await bot.me()
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id == me.id:
        return True
    caption = (message.caption or "").lower()
    word = caption.split(maxsplit=1)[0].lstrip("".join(PREFIXES)).rstrip(",.!?:;") \
        if caption else ""
    return word in call_words(config) or bool(me.username and f"@{me.username}".lower() in caption)


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


# ---------- ИИ читает чат сам ----------
PASSIVE_CHOICES = [0, 5, 15, 30, 60]  # как часто ИИ читает обычные сообщения (0 — каждое)
_passive_last: dict[int, float] = {}
_bg_tasks: set[asyncio.Task] = set()


async def target_words(bot: Bot, db: Database, config: Config, chat_id: int) -> set[str]:
    """Слова, по которым понятно, что речь о боте или создателе (имена, ники, «бот»)."""
    me = await bot.me()
    words: set[str] = {config.ai_name.lower()}
    for w in (me.first_name, me.username):
        if w:
            words.add(w.lower())
    for uid in config.admin_ids:
        row = await db.fetchone("SELECT first_name, username FROM users WHERE id=?", (uid,))
        if row:
            words.update(w.lower() for w in (row["first_name"], row["username"]) if w)
        words.update(r["alias"] for r in await db.list_nicks(chat_id) if r["user_id"] == uid)
    return {w for w in words if len(w) >= 3}


BOT_FORMS = {"бот", "бота", "боту", "ботом", "боте", "боты", "ботяра", "ботик", "bot"}


def mentions_target(text: str, words: set[str]) -> bool:
    """Есть ли в тексте слово про бота/создателя. Сравниваем по словам, а не подстрокой:
    «работа» не должна считаться упоминанием «бота». Имена — с учётом окончаний (паш-е)."""
    tokens = re.findall(r"[a-zа-яё0-9_]+", text.lower().replace("ё", "е"))
    for t in tokens:
        if t in BOT_FORMS or t in words:
            return True
        if any(len(w) >= 4 and w not in BOT_FORMS and t.startswith(w[:-1]) and len(t) - len(w) <= 2
               for w in words):
            return True
    return False


async def passive_check(message: Message, history: list[tuple[str, str]],
                        data: dict[str, Any]) -> None:
    """Сообщение не адресовано боту: ИИ решает, вмешаться ли и не наказать ли за оскорбление."""
    db: Database = data["db"]
    bot: Bot = data["bot"]
    ai: AIClient | None = data.get("ai")
    config: Config = data["config"]
    if ai is None or not config.ai_configured:
        return
    chat_id = message.chat.id
    settings = await db.get_chat_settings(chat_id)
    proactive, punish = bool(settings.get("ai_proactive")), bool(settings.get("ai_punish"))
    if not (proactive or punish):
        return
    text = (message.text or "").strip()
    if len(text) < 3 or text[:1] in PREFIXES:
        return
    relevant = mentions_target(text, await target_words(bot, db, config, chat_id))
    if not relevant and not proactive:
        return  # только наказания: проверяем лишь сообщения про бота или создателя
    now = time.time()
    every = int(settings.get("ai_passive_every", 0))
    if not relevant and now - _passive_last.get(chat_id, 0) < every:
        return  # сообщения про бота/создателя проверяем всегда
    _passive_last[chat_id] = now

    people, author_names = await group_people(db, chat_id, message.from_user.id)
    persona = settings.get("ai_persona", "normal")
    style = await db.get_style(message.from_user.id) if persona == "adaptive" else None
    messages = build_messages(config.ai_name, persona, history, author_name(message), text,
                              creator=await creator_label(db, config),
                              author_is_creator=message.from_user.id in config.admin_ids,
                              people=people, author_names=author_names, author_style=style,
                              punish=punish, passive="reply" if proactive else "silent")
    try:
        res = parse_reply(await ai.complete(messages, temperature=0.3 if punish else 1.0))
    except AIError:
        return  # в фоне ошибки ИИ не показываем
    if proactive and not res["skip"]:
        cooldown = int(settings.get("ai_cooldown", 5))
        if time.time() - _last_answer.get(chat_id, 0) >= cooldown:
            _last_answer[chat_id] = time.time()
            if res["reaction"]:
                await react(bot, chat_id, message.message_id, res["reaction"])
            answer = res["text"][:3500]
            memory.add(chat_id, config.ai_name, answer)
            await message.reply(answer, parse_mode=None)
    if punish and res["punish"]:
        await apply_punishment(message, bot, db, config, res["punish"], res["reason"], text)


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
                    history = memory.get(event.chat.id)  # контекст до этого сообщения
                    task = asyncio.create_task(passive_check(event, history, data))
                    _bg_tasks.add(task)
                    task.add_done_callback(_bg_tasks.discard)
                    memory.add(event.chat.id, author_name(event), event.text)
                    settings = await data["db"].get_chat_settings(event.chat.id)
                    if settings.get("ai_persona") == "adaptive":
                        await remember_style(data["db"], event.from_user.id, event.text)
            except Exception:  # noqa: BLE001
                pass
        return result
