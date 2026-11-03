"""Ассистент чатов (бизнес-подключение Telegram к аккаунту пользователя).

Бот подключается к аккаунту владельца и получает сообщения его личных чатов:
- кэширует входящие сообщения, чтобы при изменении/удалении прислать владельцу «было → стало»
  и содержимое удалённого (включая медиа);
- при ответе владельцем на исчезающее фото/видео/голосовое присылает копию;
- команды владельца в чате: .мут / .размут, .шахматы, .шашки.
"""
import asyncio
import html
import logging
import re
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (
    BufferedInputFile,
    BusinessConnection,
    BusinessMessagesDeleted,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..db import Database
from ..services.notifier import safe_send
from ..services.textstyles import OFF_WORDS, STYLE_ALIASES, STYLES, render
from ..services.userinfo import format_card
from ..utils.text import mention, quote
from .boardgames import start_business_game

log = logging.getLogger(__name__)
router = Router(name="business")

MEDIA_TYPES = ("photo", "video", "animation", "voice", "video_note", "audio", "sticker", "document")
MEDIA_NAMES = {
    "photo": "фото", "video": "видео", "animation": "GIF", "voice": "голосовое",
    "video_note": "кружок", "audio": "аудио", "sticker": "стикер", "document": "файл",
}
DISAPPEARING_TYPES = ("photo", "video", "voice", "video_note")
# Время запуска процесса: всё обычное медиа, пришедшее позже, бот гарантированно закэшировал
STARTED_AT = time.time()
SELF_DELETED_TTL = 300

MUTE_CMDS = {"mute", "мут", "заглушить"}
UNMUTE_CMDS = {"unmute", "размут", "разглушить", "анмут", "анмьют"}
CHESS_CMDS = {"chess", "шахматы"}
CHECKERS_CMDS = {"checkers", "шашки"}

_self_deleted: dict[tuple[str, int, int], float] = {}

# Тексты, которые наш бот сам поставил в чужие сообщения (режимы текста, формат, команды).
# Если собеседник тоже пользуется этим ботом, его сообщения правит наш же бот — такие правки
# не должны приходить второму пользователю как «сообщение изменено».
# Ключ — (id автора, текст); ID сообщений у двух сторон личного чата разные, поэтому сверяем текст.
_bot_edits: dict[tuple[int, str], float] = {}
_bc_owner: dict[str, int] = {}  # id подключения -> владелец
BOT_EDIT_TTL = 600


def _plain(text_html: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text_html)).strip()


def remember_bot_edit(author_id: int, text_html: str) -> None:
    now = time.time()
    for k in [k for k, ts in _bot_edits.items() if now - ts > BOT_EDIT_TTL]:
        _bot_edits.pop(k, None)
    _bot_edits[(author_id, _plain(text_html))] = now


def is_bot_edit(author_id: int, text: str | None) -> bool:
    ts = _bot_edits.get((author_id, (text or "").strip()))
    return ts is not None and time.time() - ts <= BOT_EDIT_TTL


def extract_media(m: Message) -> tuple[str | None, str | None]:
    if m.photo:
        return "photo", m.photo[-1].file_id
    for t in MEDIA_TYPES[1:]:
        obj = getattr(m, t, None)
        if obj:
            return t, obj.file_id
    return None, None


def describe(m: Message) -> str | None:
    text = m.text or m.caption
    if text:
        return text
    if m.location:
        return "[геолокация]"
    if m.contact:
        return f"[контакт: {m.contact.first_name} {m.contact.phone_number}]"
    if m.poll:
        return f"[опрос: {m.poll.question}]"
    return None


def who(user_id: int, name: str | None, username: str | None) -> str:
    s = mention(user_id, name)
    return f"{s} (@{username})" if username else s


async def get_connection(bot: Bot, db: Database, bc_id: str):
    row = await db.get_business_connection(bc_id)
    if row is None:
        try:
            conn = await bot.get_business_connection(bc_id)
        except TelegramBadRequest:
            return None
        await _save_connection(db, conn)
        row = await db.get_business_connection(bc_id)
    if row:
        _bc_owner[bc_id] = row["user_id"]
    return row


async def _save_connection(db: Database, conn: BusinessConnection) -> tuple[bool, bool]:
    rights = conn.rights
    can_delete = bool(rights and rights.can_delete_all_messages)
    can_reply = bool((rights and rights.can_reply) or conn.can_reply)
    await db.save_business_connection(conn.id, conn.user.id, conn.user_chat_id, conn.is_enabled,
                                      can_reply, can_delete)
    await db.upsert_user(conn.user.id, conn.user.username, conn.user.first_name)
    return can_reply, can_delete


async def cache(db: Database, m: Message, bc_id: str, auto: bool = False) -> None:
    mtype, fid = extract_media(m)
    u = m.from_user
    await db.cache_business_message(
        bc_id, m.chat.id, m.message_id, u.id if u else 0, u.full_name if u else None,
        u.username if u else None, describe(m), mtype, fid, int(m.date.timestamp()),
        auto=auto or bool(m.sender_business_bot),
    )


AUTO_EDIT_WINDOW = 5  # правка в первые секунды после отправки — это бот собеседника, не человек


def _ts(value) -> int:
    return int(value.timestamp()) if hasattr(value, "timestamp") else int(value)


def is_automatic(text: str | None) -> bool:
    """Команды ботов (.команды, .пикми …) — их правки и удаления не показываем."""
    return bool(text) and text.lstrip().startswith(".")


async def delete_business(bot: Bot, bc_id: str, chat_id: int, ids: list[int]) -> bool:
    now = time.time()
    for k in [k for k, ts in _self_deleted.items() if now - ts > SELF_DELETED_TTL]:
        _self_deleted.pop(k, None)
    for mid in ids:
        _self_deleted[(bc_id, chat_id, mid)] = now
    try:
        await bot.delete_business_messages(business_connection_id=bc_id, message_ids=ids)
        return True
    except TelegramBadRequest as e:
        log.info("Не удалось удалить бизнес-сообщения: %s", e)
        return False


async def send_media(bot: Bot, chat_id: int, media_type: str, file_id: str, caption: str) -> None:
    methods = {
        "photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation,
        "voice": bot.send_voice, "audio": bot.send_audio, "document": bot.send_document,
        "video_note": bot.send_video_note, "sticker": bot.send_sticker,
    }
    method = methods[media_type]
    no_caption = media_type in ("video_note", "sticker")
    if no_caption or len(caption) > 1024:
        await safe_send(bot, chat_id, caption[:4096])
    try:
        if no_caption or len(caption) > 1024:
            await method(chat_id, file_id)
        else:
            await method(chat_id, file_id, caption=caption)
    except TelegramBadRequest:
        # запасной путь: скачать и загрузить заново
        buf = await bot.download(file_id)
        ext = {"photo": "jpg", "video": "mp4", "voice": "ogg", "video_note": "mp4"}.get(media_type, "bin")
        upload = BufferedInputFile(buf.read(), filename=f"{media_type}.{ext}")
        if no_caption or len(caption) > 1024:
            await method(chat_id, upload)
        else:
            await method(chat_id, upload, caption=caption)


# ---------- подключение ----------

@router.business_connection()
async def on_connection(conn: BusinessConnection, bot: Bot, db: Database):
    can_reply, can_delete = await _save_connection(db, conn)
    if not conn.is_enabled:
        text = "💬 Ассистент чатов отключён. Уведомления больше не будут приходить."
    else:
        text = "✅ <b>Ассистент чатов подключён!</b>\n\n" \
               "Теперь я буду присылать сюда изменённые и удалённые сообщения ваших собеседников.\n" \
               "В любом личном чате можно писать: <code>.мут</code>, <code>.размут</code>, " \
               "<code>.шахматы</code>, <code>.шашки</code>."
        if not can_delete:
            text += "\n\n⚠️ Нет права «Удаление сообщений» — <code>.мут</code> не сможет удалять " \
                    "сообщения собеседника."
        if not can_reply:
            text += "\n⚠️ Нет права отвечать на сообщения — игры и уведомления в чате не будут " \
                    "работать."
    await safe_send(bot, conn.user_chat_id, text)

@router.business_message()
async def on_business_message(message: Message, bot: Bot, db: Database):
    if not db.feature_enabled("business"):
        return
    bc_id = message.business_connection_id
    conn = await get_connection(bot, db, bc_id)
    if not conn or not conn["enabled"]:
        return
    owner_id = conn["user_id"]
    sender = message.from_user
    is_owner = bool(sender and sender.id == owner_id)
    settings = await db.get_user_settings(owner_id)

    mode = await db.get_chat_mode(owner_id, message.chat.id)

    if not is_owner:
        if settings["biz_mute"] and await db.is_business_muted(owner_id, message.chat.id):
            await delete_business(bot, bc_id, message.chat.id, [message.message_id])
            return
        await cache(db, message, bc_id)
        if mode["clone"] and not is_automatic(message.text or message.caption):
            await echo(bot, bc_id, message)
        return

    text = (message.text or "").strip()
    if is_owner and text.startswith("."):
      if await dot_command(message, bot, db, conn, settings, mode, text):
          return
          
    if settings["biz_disappearing"] and message.reply_to_message:
        await check_disappearing(message, bot, db, conn)
    await apply_style(message, bot, conn, mode["style"], settings.get("msg_format", "none"))


async def echo(bot: Bot, bc_id: str, m: Message) -> None:
    """Режим .клон: повторяет сообщение собеседника от имени владельца в том же чате."""
    chat_id = m.chat.id
    mtype, fid = extract_media(m)
    try:
        if mtype:
            methods = {
                "photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation,
                "voice": bot.send_voice, "audio": bot.send_audio, "document": bot.send_document,
                "video_note": bot.send_video_note, "sticker": bot.send_sticker,
            }
            kwargs = {"business_connection_id": bc_id}
            if m.caption and mtype not in ("video_note", "sticker"):
                kwargs["caption"] = m.html_text
            await methods[mtype](chat_id, fid, **kwargs)
        elif m.text:
            await bot.send_message(chat_id, m.html_text, business_connection_id=bc_id)
    except TelegramBadRequest as e:
        log.info("Клон: не удалось повторить сообщение: %s", e)


async def apply_style(message: Message, bot: Bot, conn, style: str, fmt: str) -> None:
    """Автоизменение своих сообщений: режим текста этого чата и/или общий формат."""
    if (style == "none" and fmt == "none") or not message.text or message.text.startswith("."):
        return
    new = render(message.text, message.html_text, style, fmt)
    if not new or len(new) > 4096:
        return
    remember_bot_edit(conn["user_id"], new)
    try:
        await bot.edit_message_text(new, chat_id=message.chat.id, message_id=message.message_id,
                                    business_connection_id=conn["id"])
    except TelegramBadRequest as e:
        log.info("Не удалось изменить сообщение владельца: %s", e)


_bg_tasks: set[asyncio.Task] = set()


async def flash(bot: Bot, bc_id: str, chat_id: int, message_id: int, text: str,
                seconds: int = 5) -> None:
    """Превращает команду владельца в короткое уведомление и удаляет его через пару секунд."""
    remember_bot_edit(_bc_owner.get(bc_id, 0), text)
    try:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                    business_connection_id=bc_id)
    except TelegramBadRequest:
        await delete_business(bot, bc_id, chat_id, [message_id])
        return

    async def later():
        await asyncio.sleep(seconds)
        await delete_business(bot, bc_id, chat_id, [message_id])
    task = asyncio.create_task(later())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def _style_cmd(key: str) -> str:
    return next(a for a, k in STYLE_ALIASES.items() if k == key)


def commands_text() -> str:
    styles = "\n".join(f"• <code>.{_style_cmd(key)}</code> — {label}" for key, (label, _) in STYLES.items())
    return (
        "📖 <b>Команды ассистента</b> (действуют только в этом чате)\n\n"
        "🔇 <code>.мут</code> — заглушить собеседника (его сообщения удаляются)\n"
        "🔊 <code>.размут</code> / <code>.анмут</code> — снять заглушку\n"
        "🪞 <code>.клон</code> — повторять каждое сообщение собеседника (ещё раз — выключить)\n"
        "ℹ️ <code>.инфа</code> — ID и примерная дата регистрации собеседника (ответ в чат)\n"
        f"🔁 <code>.спам текст 5</code> — отправить текст несколько раз (до {SPAM_MAX})\n"
        "♟ <code>.шахматы</code> — партия в шахматы\n"
        "⚪ <code>.шашки</code> — партия в шашки\n\n"
        "✨ <b>Режимы текста</b> (повтор команды — выключить):\n"
        f"{styles}\n"
        "• <code>.обычный</code> — выключить режим\n\n"
        "🔤 Жирный, курсив и другой формат текста — в меню бота → 💬 Ассистент чатов.\n"
        "📖 <code>.команды</code> — этот список"
    )


HELP_CMDS = {"команды", "commands", "help", "помощь", "хелп"}
INFO_CMDS = {"инфа", "инфо", "info", "whois", "id"}
CLONE_CMDS = {"клон", "clone", "зеркало", "mirror"}
SPAM_CMDS = {"спам", "spam"}
SPAM_MAX = 10
SPAM_PAUSE = 0.7
RESET_CMDS = {"обычный", "normal", "стильвыкл"}


async def style_command(message: Message, bot: Bot, db: Database, conn, mode: dict,
                        cmd: str, arg: str) -> bool:
    """Режимы текста (пикми, пират…) включаются командой в чате — только для этого чата.
    Формат (жирный, курсив…) настраивается в меню бота, как в TonzBot."""
    owner_id, bc_id, chat_id = conn["user_id"], conn["id"], message.chat.id
    style = mode["style"]
    if cmd in STYLE_ALIASES:
        key = STYLE_ALIASES[cmd]
        new_style = "none" if style == key else key
    elif cmd in ("стиль", "style", "режим"):
        if arg in OFF_WORDS:
            new_style = "none"
        elif arg in STYLE_ALIASES:
            new_style = STYLE_ALIASES[arg]
        else:
            await flash(bot, bc_id, chat_id, message.message_id, commands_text(), seconds=60)
            return True
    elif cmd in RESET_CMDS:
        new_style = "none"
    else:
        return False
    await db.set_chat_mode(owner_id, chat_id, style=new_style)
    if new_style != "none":
        icon, label = STYLES[new_style][0].split(" ", 1)
        text = (f"{icon} {label.split(' (')[0].lower()}-режим включён~ (в этом чате)\n\n"
                f"чтобы отключить, введите <code>.{_style_cmd(new_style)}</code>")
    else:
        text = "✨ режим текста выключен"
    await flash(bot, bc_id, chat_id, message.message_id, text)
    return True


async def spam(bot: Bot, bc_id: str, chat_id: int, text: str, count: int) -> None:
    for _ in range(count):
        try:
            await bot.send_message(chat_id, text, business_connection_id=bc_id, parse_mode=None)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest as e:
            log.info("Спам остановлен: %s", e)
            return
        await asyncio.sleep(SPAM_PAUSE)


async def dot_command(message: Message, bot: Bot, db: Database, conn, settings: dict, mode: dict,
                      text: str) -> bool:
    parts = text[1:].split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    raw_arg = parts[1].strip() if len(parts) > 1 else ""
    arg = raw_arg.lower()
    bc_id, chat_id, owner_id = conn["id"], message.chat.id, conn["user_id"]
    if cmd in HELP_CMDS:
        await flash(bot, bc_id, chat_id, message.message_id, commands_text(), seconds=60)
        return True
    if cmd in INFO_CMDS:
        await info_command(message, bot, db, conn)
        return True
    if await style_command(message, bot, db, conn, mode, cmd, arg):
        return True
    if cmd in CLONE_CMDS:
        on = not mode["clone"]
        await db.set_chat_mode(owner_id, chat_id, clone=on)
        await flash(bot, bc_id, chat_id, message.message_id,
                    "🪞 клон-режим включён~\n\nчтобы отключить, введите <code>.клон</code>" if on
                    else "🪞 клон-режим выключен")
        return True
    if cmd in SPAM_CMDS:
        words = raw_arg.rsplit(maxsplit=1)
        if len(words) < 2 or not words[1].isdigit() or int(words[1]) < 1:
            await flash(bot, bc_id, chat_id, message.message_id,
                        f"🔁 Пример: <code>.спам привет 5</code> (до {SPAM_MAX} раз)")
            return True
        await delete_business(bot, bc_id, chat_id, [message.message_id])
        task = asyncio.create_task(spam(bot, bc_id, chat_id, words[0][:4096],
                                        min(int(words[1]), SPAM_MAX)))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
        return True
    if cmd in MUTE_CMDS | UNMUTE_CMDS and settings["biz_mute"]:
        await delete_business(bot, bc_id, chat_id, [message.message_id])
        mute = cmd in MUTE_CMDS
        await db.set_business_mute(owner_id, chat_id, bc_id, mute)
        if mute:
            note = "" if conn["can_delete"] else \
                "\n⚠️ У бота нет права удалять сообщения — выдайте его в настройках чат-бота (Настройки → Telegram для бизнеса → Чат-боты)."
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔊 Размутить", callback_data=f"bmute:{chat_id}")
            ]])
            await bot.send_message(
                chat_id, f"🔇 <b>Пользователь заглушен.</b>\nЕго новые сообщения будут удаляться.{note}",
                business_connection_id=bc_id, reply_markup=kb,
            )
        else:
            await bot.send_message(chat_id, "🔊 <b>Пользователь размучен.</b>",
                                   business_connection_id=bc_id)
        return True
    if cmd in CHESS_CMDS | CHECKERS_CMDS and settings["biz_games"] and db.feature_enabled("boardgames"):
        await delete_business(bot, bc_id, chat_id, [message.message_id])
        owner = (owner_id, message.from_user.full_name)
        other = (chat_id, message.chat.full_name)
        kind = "chess" if cmd in CHESS_CMDS else "checkers"
        await start_business_game(bot, db, kind, bc_id, chat_id, owner, other)
        return True
    return False

async def info_command(message: Message, bot: Bot, db, conn) -> None:
# Получаем ID цели (из реплая или из текущего чата)
    reply = message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        target_id = reply.from_user.id
    else:
        target_id = message.chat.id

    # Делаем прямой запрос в твою рабочую таблицу users
    user_row = await db.fetchone("SELECT id, username, first_name, created_at FROM users WHERE id = ?", (target_id,))
    
    if not user_row:
        card = f"👤 <b>Пользователь не найден в базе данных бота.</b>\n🆔 ID: <code>{target_id}</code>"
    else:
        username_str = f"@{user_row['username']}" if user_row['username'] else "—"
        card = (
            f"ℹ️ <b>Информация о пользователе</b>\n\n"
            f"👤 <b>Имя:</b> {user_row['first_name']}\n"
            f"🔗 <b>Юзернейм:</b> {username_str}\n"
            f"🆔 <b>ID:</b> <code>{user_row['id']}</code>\n"
        )
    
    # Извлекаем ID бизнес-подключения и отправляем
    bc_id = conn['id'] if 'id' in conn.keys() else conn['user_id']
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        pass

    
    try:
        await bot.send_message(
            chat_id=message.chat.id,
            text=card,
            business_connection_id=bc_id,
            parse_mode="HTML"
        )
    except Exception as e:
        log.info("Не удалось отправить карточку: %s", e)
      
      
      

async def check_disappearing(message: Message, bot: Bot, db: Database, conn) -> None:
    reply = message.reply_to_message
    if not reply.from_user or reply.from_user.id == conn["user_id"]:
        return
    mtype, fid = extract_media(reply)
    if mtype not in DISAPPEARING_TYPES:
        return
    cached = await db.get_business_message(conn["id"], reply.chat.id, reply.message_id)
    if cached and cached["file_id"]:
        return  # обычное медиа, уже было в кэше
    # Telegram не помечает исчезающее медиа. Признак — его нет в кэше, хотя бот в момент отправки
    # уже работал и был подключён (обычное медиа тогда закэшировалось бы). Медиа, отправленное
    # до подключения или пока бот был выключен, не трогаем — иначе обычное фото выдаётся за временное.
    sent_at = _ts(reply.date)
    if sent_at < STARTED_AT - 1 or sent_at < (conn["updated_at"] or 0) - 1:
        return
    u = reply.from_user
    caption = f"🔥 <b>Исчезающее {MEDIA_NAMES[mtype]}</b>\n👤 {who(u.id, u.full_name, u.username)}"
    if reply.caption:
        caption += f"\n{quote(reply.caption, 800)}"
    try:
        await send_media(bot, conn["user_chat_id"], mtype, fid, caption)
    except Exception:  # noqa: BLE001
        log.exception("Не удалось сохранить исчезающее медиа")
        return
    await cache(db, reply, conn["id"])


@router.callback_query(F.data.startswith("bmute:"))
async def cb_business_unmute(call: CallbackQuery, bot: Bot, db: Database):
    chat_id = int(call.data.split(":")[1])
    row = await db.fetchone(
        "SELECT * FROM business_mutes WHERE owner_id=? AND chat_id=?", (call.from_user.id, chat_id)
    )
    if not row:
        if call.from_user.id == chat_id:
            return await call.answer("Размутить может только владелец чата", show_alert=True)
        return await call.answer("Пользователь уже размучен")
    await db.set_business_mute(call.from_user.id, chat_id, row["bc_id"], False)
    try:
        await bot.edit_message_text(
            "🔊 <b>Пользователь размучен.</b>", chat_id=chat_id,
            message_id=call.message.message_id, business_connection_id=row["bc_id"],
        )
    except TelegramBadRequest:
        pass
    await call.answer("Размучен")


# ---------- изменения ----------

@router.edited_business_message()
async def on_business_edit(message: Message, bot: Bot, db: Database):
    if not db.feature_enabled("business"):
        return
    bc_id = message.business_connection_id
    conn = await get_connection(bot, db, bc_id)
    if not conn or not conn["enabled"]:
        return
    sender = message.from_user
    if not sender or sender.id == conn["user_id"]:
        return
    old = await db.get_business_message(bc_id, message.chat.id, message.message_id)
    new_text = describe(message)
    # Команда бота собеседника (.команды и т.п.) — помечаем навсегда, чтобы не показывать
    # ни её правку, ни последующее удаление
    bot_command = bool(message.sender_business_bot or (old and old["auto"])
                       or is_automatic(old["text"] if old else None) or is_automatic(new_text))
    await cache(db, message, bc_id, auto=bot_command)
    if is_bot_edit(sender.id, new_text):
        return  # это наш же бот применил режим текста/формат у собеседника
    settings = await db.get_user_settings(conn["user_id"])
    if not settings["biz_edits"]:
        return
    # Правка в первые секунды после отправки — это бот собеседника («пикми»-режим и т.п.)
    quick = bool(message.edit_date) and _ts(message.edit_date) - _ts(message.date) <= AUTO_EDIT_WINDOW
    if bot_command or quick:
        return
    new_type, new_fid = extract_media(message)
    if old and old["text"] == new_text and old["file_id"] == new_fid:
        return
    text = (f"✏️ <b>Сообщение изменено</b>\n👤 {who(sender.id, sender.full_name, sender.username)}\n\n"
            f"<b>Было:</b>\n{quote(old['text'], 1800) if old else '<i>нет в кэше</i>'}\n"
            f"<b>Стало:</b>\n{quote(new_text, 1800)}")
    await safe_send(bot, conn["user_chat_id"], text)
    if old and old["file_id"] and old["file_id"] != new_fid and old["media_type"]:
        try:
            await send_media(bot, conn["user_chat_id"], old["media_type"], old["file_id"],
                             f"🖼 Прежнее {MEDIA_NAMES[old['media_type']]} до изменения")
        except Exception:  # noqa: BLE001
            log.exception("Не удалось отправить прежнее медиа")


# ---------- удаления ----------

@router.deleted_business_messages()
async def on_business_deleted(event: BusinessMessagesDeleted, bot: Bot, db: Database):
    if not db.feature_enabled("business"):
        return
    bc_id, chat = event.business_connection_id, event.chat
    conn = await get_connection(bot, db, bc_id)
    if not conn or not conn["enabled"]:
        return
    owner_id = conn["user_id"]
    settings = await db.get_user_settings(owner_id)
    items = []
    for mid in sorted(event.message_ids):
        if _self_deleted.pop((bc_id, chat.id, mid), None):
            continue
        row = await db.get_business_message(bc_id, chat.id, mid)
        if row:
            await db.delete_business_message(bc_id, chat.id, mid)
        if row and row["from_id"] != owner_id and not row["auto"] and not is_automatic(row["text"]):
            items.append(row)
    if not items or not settings["biz_deletes"]:
        return
    first = items[0]
    author = who(first["from_id"], first["from_name"], first["from_username"])
    title = "Удалено сообщение" if len(items) == 1 else f"Удалено сообщений: {len(items)}"
    header = f"🗑 <b>{title}</b>\n👤 {author}\n"
    target = conn["user_chat_id"]

    texts = [r for r in items if not r["file_id"]]
    medias = [r for r in items if r["file_id"]]
    if texts:
        body, parts = header, []
        for r in texts:
            piece = "\n" + quote(r["text"], 1500)
            if len(body) + len(piece) > 3900:
                parts.append(body)
                body = ""
            body += piece
        parts.append(body)
        for p in parts:
            await safe_send(bot, target, p)
    for r in medias[:20]:
        caption = f"🗑 <b>Удалённое {MEDIA_NAMES.get(r['media_type'], 'медиа')}</b>\n👤 {author}"
        if r["text"]:
            caption += f"\n{quote(r['text'], 700)}"
        try:
            await send_media(bot, target, r["media_type"], r["file_id"], caption)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось отправить удалённое медиа")
            await safe_send(bot, target, caption + "\n<i>(медиа недоступно)</i>")
    if len(medias) > 20:
        await safe_send(bot, target, f"…и ещё {len(medias) - 20} медиа.")
