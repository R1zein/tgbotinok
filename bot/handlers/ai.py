"""ИИ-ответы в группах.

Бот отвечает, когда его упомянули (@бот …), ответили на его сообщение или написали
«ии вопрос». История хранится в SQLite для групп, где включён ИИ. Включается администратором в /settings.
"""
import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, TelegramObject

from ..middlewares import REFUSED_USER_ID, REFUSAL
from ..config import Config
from ..db import Database
from ..services.ai import (AIClient, AIError, MOSCOW_OFFSET, build_messages,
                           build_moderation_messages, explicit_direct_insult, parse_reply)
from ..services.stt import STTError, Transcriber, fetch_audio, media_of
from ..utils.filters import PREFIXES
from ..utils.perms import is_chat_admin
from ..utils.text import esc, mention
from .moderation import announce_penalty, do_ban, do_kick, do_mute, undo_kb
from .reactions import react

log = logging.getLogger(__name__)
router = Router(name="ai")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

COOLDOWNS = [0, 3, 5, 10, 20, 30, 60]  # варианты паузы между ответами ИИ в чате (сек)
AI_CMDS = {"ии", "ai", "нейро", "gpt", "бот", "bot", "ботик"}
_last_answer: dict[int, float] = {}       # чат -> когда ИИ начал последний ответ
_warned: dict[int, float] = {}            # чат -> когда последний раз писали «подождите»
_recorded: set[tuple[int, int]] = set()  # вопросы, уже записанные в память обработчиком
MODERATION_VERSION = "3"
moderation_status: dict[int, tuple[int, str]] = {}


def record_moderation(message: Message, status: str) -> None:
    moderation_status[message.chat.id] = (message.message_id, status)
    if len(moderation_status) > 256:
        moderation_status.pop(next(iter(moderation_status)))


def own_handle_match(text: str, username: str | None):
    if not username:
        return None
    return re.search(rf"(?<![\w@])@{re.escape(username)}(?!\w)", text, re.IGNORECASE)


def refers_to_bot(message: Message, me, config: Config) -> bool:
    """Допуск к самостоятельному ответу: только своё имя/ник или ответ боту."""
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    if own_handle_match(text, me.username) or any(
            e.type == "text_mention" and e.user and e.user.id == me.id for e in entities):
        return True
    reply = message.reply_to_message
    if reply and reply.from_user:
        return reply.from_user.id == me.id
    tokens = re.findall(r"[\w]+", text.casefold())
    return bool(config.ai_name and config.ai_name.casefold() in tokens)


def author_name(message: Message) -> str:
    u = message.from_user
    if not u:
        return "неизвестный участник"
    handle = f", @{u.username}" if u.username else ""
    return f"{u.full_name}{handle} [ID {u.id}]"


async def reply_context(db: Database, message: Message) -> dict | None:
    reply = message.reply_to_message
    if reply and reply.from_user:
        quoted = reply.text or reply.caption
        if not quoted and media_of(reply):
            quoted = await db.scalar("SELECT body FROM group_ai_history WHERE chat_id=? AND message_id=?",
                                     (reply.chat.id, reply.message_id)) or "[аудио без расшифровки]"
        return {"автор": author_name(reply), "текст": (quoted or "[медиа]")}
    return None


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
    parts = text.split(maxsplit=1)
    first, rest = parts[0], parts[1] if len(parts) > 1 else ""
    word = first.lower().lstrip("".join(PREFIXES)).rstrip(",.!?:;")
    word, _, command_bot = word.partition("@")
    if command_bot and command_bot != (me.username or "").lower():
        return False
    if first.lower().partition("@")[0] == "/code":
        return {"coding": True, "question": rest or ("Разбери этот код." if message.reply_to_message else "")}
    handle = own_handle_match(text, me.username)
    entity_call = any(e.type == "text_mention" and e.user and e.user.id == me.id
                      for e in message.entities or [])
    reply = message.reply_to_message
    if reply and reply.from_user and reply.from_user.id != me.id and not (handle or entity_call):
        return False
    if word in call_words(config) and word != "бот":
        return {"question": rest.strip(" ,:")}
    if word == "бот" and text[:1] in PREFIXES:
        return {"question": rest.strip(" ,:")}
    if re.search(r"^(?:эй[\s,]+)?бот(?:$|[,:!?]|(?:\s+(?:как|скок|сколько|что|кто|где|"
                 r"почему|скажи|помоги|подскажи|ты|объясни|можешь|привет)\b))|"
                 r"(?:^|[\s,])(?:привет|эй|слушай)[\s,]+бот\b|,\s*бот[?!.]*$", text, re.I):
        return {"question": text if text.strip(" ,.!?:;").casefold() != "бот" else ""}
    if handle:
        question = (text[:handle.start()] + text[handle.end():]).strip(" ,:")
        return {"question": question}
    if entity_call:
        return {"question": text}
    if reply and reply.from_user and reply.from_user.id == me.id and text[:1] not in PREFIXES:
        return {"question": text}
    return False


async def remember_style(db: Database, message: Message, text: str | None) -> None:
    """Запоминаем, как пишет человек (для адаптивного режима). Команды и пустое не берём."""
    text = (text or "").strip()
    if not text or text[:1] in PREFIXES:
        return
    await db.remember_member_message(message.chat.id, message.from_user.id, message.message_id, text)


async def group_people(db: Database, chat_id: int,
                       author_id: int) -> tuple[list[tuple[str, list[str]]], list[str]]:
    """Имена из настроек автоотметок: [(«Имя (@ник)», [имена])] и имена автора вопроса."""
    by_user: dict[int, tuple[str, list[str]]] = {}
    for r in await db.list_nicks(chat_id):
        if r["user_id"] not in by_user:
            name = r["first_name"] or (f"@{r['username']}" if r["username"] else str(r["user_id"]))
            who = f"{name} (@{r['username']})" if r["username"] and r["first_name"] else name
            who += f" [ID {r['user_id']}]"
            by_user[r["user_id"]] = (who, [])
        by_user[r["user_id"]][1].append(r["alias"])
    author = by_user.get(author_id)
    return list(by_user.values()), (author[1] if author else [])


async def creator_profile(db: Database, uid: int, chat_id: int | None = None):
    row = await db.fetchone("SELECT first_name, username FROM users WHERE id=?", (uid,))
    if chat_id is not None:
        member = await db.fetchone(
            "SELECT first_name, username FROM chat_members WHERE chat_id=? AND user_id=?",
            (chat_id, uid))
        if member:
            return member
    return row


async def creator_label(db: Database, config: Config, chat_id: int | None = None) -> str | None:
    """Имена создателей из ADMIN_IDS, в том числе знакомых боту только по группе."""
    if not config.admin_ids:
        return None
    labels = []
    for uid in sorted(config.admin_ids):
        row = await creator_profile(db, uid, chat_id)
        name = row["first_name"] if row and row["first_name"] else None
        username = f"@{row['username']}" if row and row["username"] else None
        labels.append(f"{name} ({username})" if name and username
                      else name or username or f"владелец бота (ID {uid})")
    return "; ".join(labels)


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
                           evidence: str = "") -> bool:
    """Варн или мут, которые решил выдать ИИ за оскорбление бота/создателя."""
    chat_id, user = message.chat.id, message.from_user
    if not user or user.is_bot or user.id in config.admin_ids:
        return False  # создателя не наказываем
    if await is_chat_admin(bot, chat_id, user.id):
        return False  # админов Telegram ограничить не даст — и не пытаемся
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
            await announce_penalty(message, db, user.id, user.full_name, f"мут на {minutes} мин.", reason)
            await message.reply(f"🔇 {who} заглушен(а) на {minutes} мин.{details}",
                                reply_markup=undo_kb("unmute", user.id, "🔊 Размутить"))
            return True
        settings = await db.get_chat_settings(chat_id)
        limit = int(settings.get("warn_limit", 3))
        count = await db.add_warn(chat_id, user.id)
        await announce_penalty(message, db, user.id, user.full_name, "варн", reason)
        text = f"⚠️ {who} получает предупреждение ({count}/{limit}).{details}"
        if count >= limit:
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
            await db.reset_warns(chat_id, user.id)
            undo = {"mute": ("unmute", "🔊 Размутить"), "ban": ("unban", "♻️ Разбанить")}.get(action)
            await message.reply(text, reply_markup=undo_kb(undo[0], user.id, undo[1]) if undo else None)
        else:
            await message.reply(text, reply_markup=undo_kb("unwarn", user.id, "➖ Снять варн"))
        return True
    except TelegramBadRequest as e:
        log.info("ИИ не смог наказать %s: %s", user.id, e)
        await message.reply(f"⚠️ Не удалось применить наказание: {esc(e.message)}")
        record_moderation(message, f"Ошибка Telegram: {e.message}")
        return False


async def _respond(message: Message, bot: Bot, db: Database, ai: AIClient, config: Config,
                   question: str, memory_text: str, coding: bool = False) -> None:
    chat_id = message.chat.id
    settings = await db.get_chat_settings(chat_id)
    persona = settings.get("ai_persona", "normal")
    raw_question = question
    people, author_names = await group_people(db, chat_id, message.from_user.id)
    style = None
    if persona == "adaptive":
        await remember_style(db, message, raw_question)
        style = await db.member_history(chat_id, message.from_user.id, message.message_id)
    history = await db.group_history(chat_id, message.message_id)
    messages = build_messages(config.ai_name, persona, history,
                              author_name(message), question,
                              creator=await creator_label(db, config, chat_id),
                              author_is_creator=message.from_user.id in config.admin_ids,
                              people=people, author_names=author_names, author_style=style,
                              reply_to=await reply_context(db, message),
                              utc_offset=settings.get("utc_offset", MOSCOW_OFFSET), punish=False)
    if not message.text:  # голосовое: проверяем уже расшифрованное сообщение
        if await check_moderation(message,
                               {"bot": bot, "db": db, "ai": ai, "config": config},
                               text=raw_question, direct=True):
            await db.remember_group(chat_id, message.message_id, author_name(message), memory_text)
            return
    await db.remember_group(chat_id, message.message_id, author_name(message), memory_text)
    if len(_recorded) > 5000:
        _recorded.clear()
    _recorded.add((chat_id, message.message_id))
    await _typing(bot, message)
    try:
        if coding:
            messages[0]["content"] += " Режим программирования: подробно разбери задачу и приведи полный код, когда он нужен. Можно использовать блоки кода."
            answer = await ai.complete(messages, max_tokens=config.ai_code_tokens)
        else:
            answer = await ai.complete(messages)
    except AIError as e:
        await message.reply(f"🤖 {e}", parse_mode=None)
        return
    res = parse_reply(answer)
    if res["reaction"]:  # реакция на сообщение по его эмоции — помимо ответа
        await react(bot, chat_id, message.message_id, res["reaction"])
    await send_answer(message, db, config, res["text"] or "🤔")


async def send_answer(message: Message, db: Database, config: Config, answer: str) -> None:
    sender = f"{config.ai_name} (бот, ответ для {author_name(message)})"
    while answer:
        end = min(len(answer), 3900)
        if end < len(answer):
            newline = answer.rfind("\n", 0, end)
            if newline > end // 2:
                end = newline + 1
        chunk, answer = answer[:end], answer[end:]
        sent = await message.reply(chunk, parse_mode=None)
        await db.remember_group(message.chat.id, sent.message_id, sender, chunk)


@router.message(ai_trigger)
async def ai_answer(message: Message, bot: Bot, db: Database, ai: AIClient, config: Config,
                    question: str, coding: bool = False):
    if not await db.group_feature(message.chat.id, "ai"):
        return
    if not question:
        answer = "Добавьте задачу после /code или ответьте командой на код." if coding else "Я здесь. Что подсказать?"
        await db.remember_group(message.chat.id, message.message_id, author_name(message), message.text)
        await send_answer(message, db, config, answer)
        _recorded.add((message.chat.id, message.message_id))
        return
    if await _cooldown_ok(message, db):
        await _respond(message, bot, db, ai, config, question, message.text, coding=coding)


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
        row = await creator_profile(db, uid, chat_id)
        if row:
            words.update(w.lower() for w in (row["first_name"], row["username"]) if w)
        words.update(r["alias"] for r in await db.list_nicks(chat_id) if r["user_id"] == uid)
    return {w for w in words if w}


BOT_FORMS = {"бот", "бота", "боту", "ботом", "боте", "боты", "ботяра", "ботик", "bot"}


def mentions_target(text: str, words: set[str]) -> bool:
    """Есть ли в тексте слово про бота/создателя. Сравниваем по словам, а не подстрокой:
    «работа» не должна считаться упоминанием «бота». Имена — с учётом окончаний (паш-е)."""
    words = {w.lower().replace("ё", "е") for w in words}
    tokens = re.findall(r"[a-zа-яё0-9_]+", text.lower().replace("ё", "е"))
    for t in tokens:
        if t in BOT_FORMS or t in words:
            return True
        if any(len(w) >= 4 and w not in BOT_FORMS and t.startswith(w[:-1]) and len(t) - len(w) <= 2
               for w in words):
            return True
    return False


async def check_moderation(message: Message, data: dict[str, Any], *, text: str | None = None,
                           direct: bool = False) -> bool:
    """Модерация до разговорного обработчика, независимо от пауз и характера ИИ."""
    db, bot, config, ai = data["db"], data["bot"], data["config"], data.get("ai")
    if not message.from_user:
        return False
    settings = await db.get_chat_settings(message.chat.id)
    if not settings.get("ai_punish"):
        return False
    text = (text if text is not None else message.text or message.caption or "").strip()
    if not text:
        return False
    if re.fullmatch(r"/aistatus(?:@\w+)?", text, re.IGNORECASE):
        return False
    me = await bot.me()
    reply_user = message.reply_to_message.from_user if message.reply_to_message else None
    protected = config.admin_ids | {me.id}
    reply_target = bool(reply_user and reply_user.id in protected)
    entities = message.entities or message.caption_entities or []
    entity_target = any(e.type == "text_mention" and e.user and e.user.id in protected
                        for e in entities)
    words = await target_words(bot, db, config, message.chat.id)
    first = text.split(maxsplit=1)[0].lower().lstrip("".join(PREFIXES)).rstrip(",.!?:;")
    relevant = (direct or reply_target or entity_target or mentions_target(text, words)
                or first in call_words(config))
    if not relevant:
        return False
    uid = message.from_user.id
    if uid in config.admin_ids or await is_chat_admin(bot, message.chat.id, uid):
        record_moderation(message, "Автор — создатель или админ группы: автоматическое наказание пропущено.")
        log.info("AI moderation chat=%s message=%s: protected author %s",
                 message.chat.id, message.message_id, uid)
        return False
    creator_names = {"создатель", "создатель бота"}
    bot_names = call_words(config)
    bot_names.update(w for w in (me.first_name, f"@{me.username}" if me.username else None) if w)
    for creator_id in config.admin_ids:
        profile = await creator_profile(db, creator_id, message.chat.id)
        if profile:
            creator_names.update(w for w in (profile["first_name"],
                           f"@{profile['username']}" if profile["username"] else None) if w)
    creator_names.update(r["alias"] for r in await db.list_nicks(message.chat.id)
                   if r["user_id"] in config.admin_ids)
    for entity in entities:
        if entity.type == "text_mention" and entity.user and entity.user.id in protected:
            names = bot_names if entity.user.id == me.id else creator_names
            names.add(entity.extract_from(message.text or message.caption or ""))
    bot_hit = explicit_direct_insult(text, bot_names)
    creator_hit = explicit_direct_insult(text, creator_names)
    if not (bot_hit or creator_hit) and (reply_target or direct):
        if explicit_direct_insult(text, bot_names | creator_names, reply_target=True):
            creator_hit = bool(reply_target and reply_user.id in config.admin_ids)
            bot_hit = not creator_hit
    if bot_hit != creator_hit:
        reason = "Оскорбление создателя бота." if creator_hit else "Оскорбление бота."
        applied = await apply_punishment(message, bot, db, config, ("warn", 0), reason, text)
        if applied:
            record_moderation(message, "Выдан варн по правилу прямого оскорбления.")
        return applied
    if ai is None or not config.ai_configured:
        record_moderation(message, "Ключ ИИ не настроен; доступны только точные правила оскорблений.")
        return False
    context = ""
    if reply_user:
        target = ("боту" if reply_user.id == me.id else "создателю бота"
                  if reply_user.id in config.admin_ids else "другому участнику")
        context = f"Это ответ {target} ({reply_user.full_name}, ID {reply_user.id})."
    if entity_target:
        context += " Сообщение содержит Telegram-упоминание бота или создателя по ID."
    if direct:
        context += " Это прямое обращение к боту."
    aliases = [r["alias"] for r in await db.list_nicks(message.chat.id)
               if r["user_id"] in config.admin_ids]
    if aliases:
        context += f" Имена создателей, заданные в группе: {', '.join(aliases)}."
    messages = build_moderation_messages(
        f"{config.ai_name} (@{me.username}, {me.first_name})",
        await creator_label(db, config, message.chat.id), f"{author_name(message)} (ID {uid})",
        text, await db.group_history(message.chat.id, message.message_id, 30), context)
    try:
        decision = await ai.complete(messages, max_tokens=250, temperature=0)
        target_line, _, details = decision.strip().partition("\n")
        reasons = {"АДРЕСАТ: бот": "Оскорбление бота.",
                   "АДРЕСАТ: создатель": "Оскорбление создателя бота."}
        reason = reasons.get(target_line.strip())
        res = parse_reply(details)
    except AIError as e:
        record_moderation(message, f"Ошибка ИИ: {e}")
        log.warning("AI moderation chat=%s message=%s failed: %s",
                    message.chat.id, message.message_id, e)
        return False
    log.info("AI moderation chat=%s message=%s decision=%s",
             message.chat.id, message.message_id, res["punish"])
    if res["punish"] and reason:
        applied = await apply_punishment(message, bot, db, config, res["punish"], reason, text)
        if applied:
            record_moderation(message, f"Применено решение ИИ: {res['punish'][0]}.")
        return applied
    else:
        record_moderation(message, "ИИ не назначил наказание (либо не вернул строку НАКАЗАНИЕ).")
    return False


async def passive_check(message: Message, data: dict[str, Any]) -> None:
    """Разговорный ИИ решает, вмешаться ли в беседу; наказания проверяются отдельно."""
    db: Database = data["db"]
    bot: Bot = data["bot"]
    ai: AIClient | None = data.get("ai")
    config: Config = data["config"]
    if ai is None or not config.ai_configured:
        return
    chat_id = message.chat.id
    settings = await db.get_chat_settings(chat_id)
    proactive = bool(settings.get("ai_proactive"))
    if not proactive:
        return
    text = (message.text or "").strip()
    if not text or text[:1] in PREFIXES:
        return
    now = time.time()
    every = int(settings.get("ai_passive_every", 0))
    if not message.reply_to_message and re.fullmatch(
            r"(?:[азхah]+[.,! ]*)?(?:это\s+)?(?:гениально|смешно|угар|прикол)[.!😂🤣 ]*|"
            r"[ахзha😂🤣 ]{3,}", text, re.IGNORECASE):
        return
    me = await bot.me()
    reply = message.reply_to_message
    if reply and (not reply.from_user or reply.from_user.id != me.id):
        return
    history = await db.group_history(chat_id, message.message_id)
    if not refers_to_bot(message, me, config):
        recent_sender = f"{config.ai_name} (бот, ответ для {author_name(message)})"
        continuing = any(sender == recent_sender for sender, _ in history[-6:])
        if every or not continuing:
            return
        # Даже во время диалога короткое суждение может относиться к разговору людей.
        if not ("?" in text or re.match(
                r"^(?:а\s+)?(?:да|нет|давай|продолжай|объясни|расскажи|покажи|почему|"
                r"зачем|как|уточни|можешь|подробнее|что|когда|где|сколько)\b", text, re.I)):
            return
    if message.from_user.id == REFUSED_USER_ID:
        await message.reply(REFUSAL)
        return
    if now - _passive_last.get(chat_id, 0) < every:
        return  # ограничиваем самостоятельные ответы; модерация уже выполнена отдельно
    _passive_last[chat_id] = now

    people, author_names = await group_people(db, chat_id, message.from_user.id)
    persona = settings.get("ai_persona", "normal")
    style = await db.member_history(chat_id, message.from_user.id, message.message_id) if persona == "adaptive" else None
    messages = build_messages(config.ai_name, persona, history, author_name(message), text,
                              creator=await creator_label(db, config, chat_id),
                              author_is_creator=message.from_user.id in config.admin_ids,
                              people=people, author_names=author_names, author_style=style,
                              reply_to=await reply_context(db, message),
                              utc_offset=settings.get("utc_offset", MOSCOW_OFFSET), punish=False, passive="reply")
    try:
        decision = (await ai.complete(messages)).strip()
        verdict, _, body = decision.partition("\n")
        if verdict.strip().upper() != "АДРЕСАТ: БОТ":
            return
        res = parse_reply(body)
        greeting = r"\b(?:доброе утро|добрый (?:день|вечер)|привет|здравствуй(?:те)?)\b"
        if re.search(greeting, res["text"], re.I) and not re.search(greeting, text, re.I):
            return
    except AIError:
        return  # в фоне ошибки ИИ не показываем
    if proactive and not res["skip"]:
        cooldown = int(settings.get("ai_cooldown", 5))
        if time.time() - _last_answer.get(chat_id, 0) >= cooldown:
            _last_answer[chat_id] = time.time()
            if res["reaction"]:
                await react(bot, chat_id, message.message_id, res["reaction"])
            await send_answer(message, db, config, res["text"])


class AIMemoryMiddleware(BaseMiddleware):
    """Запоминает сообщения групп, где включены ИИ-ответы, — для контекста."""

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        db = data["db"]
        if (isinstance(event, Message) and event.chat.type in ("group", "supergroup")
                and (event.text or event.caption) and not (event.voice or event.video_note)
                and event.from_user and not event.from_user.is_bot
                and await data["db"].group_feature(event.chat.id, "ai")):
            try:
                if await check_moderation(event, data):
                    await db.remember_group(event.chat.id, event.message_id, author_name(event), event.text or event.caption)
                    return
            except Exception:
                record_moderation(event, "Ошибка обработки модерации; подробности в журнале сервера.")
                log.exception("AI moderation failed chat=%s message=%s",
                              event.chat.id, event.message_id)
        if (isinstance(event, Message) and event.chat.type in ("group", "supergroup")
                and event.from_user and not event.from_user.is_bot and media_of(event)
                and await data["db"].group_feature(event.chat.id, "ai")):
            await db.remember_group(event.chat.id, event.message_id, author_name(event), "[отправлено аудио или видеосообщение]")
        result = await handler(event, data)
        if (isinstance(event, Message) and event.chat.type in ("group", "supergroup")
                and (event.text or event.caption) and not (event.voice or event.video_note)
                and event.from_user and not event.from_user.is_bot):
            try:
                if (event.chat.id, event.message_id) in _recorded:
                    _recorded.discard((event.chat.id, event.message_id))
                elif await data["db"].group_feature(event.chat.id, "ai"):
                    if event.text and result is UNHANDLED:
                        task = asyncio.create_task(passive_check(event, data))
                        _bg_tasks.add(task)
                        task.add_done_callback(_bg_tasks.discard)
                    body = event.text or event.caption
                    await db.remember_group(event.chat.id, event.message_id, author_name(event), body)
                    settings = await data["db"].get_chat_settings(event.chat.id)
                    if settings.get("ai_persona") == "adaptive":
                        await remember_style(db, event, body)
            except Exception:
                log.exception("Не удалось сохранить контекст сообщения %s", event.message_id)
        return result
