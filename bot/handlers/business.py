"""Бизнес-режим (Telegram Business, нужен Premium у владельца).

Бот подключается к аккаунту владельца и получает сообщения его личных чатов:
- кэширует входящие сообщения, чтобы при изменении/удалении прислать владельцу «было → стало»
  и содержимое удалённого (включая медиа);
- при ответе владельцем на исчезающее фото/видео/голосовое присылает копию;
- команды владельца в чате: .мут / .размут, .шахматы, .шашки.
"""
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
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
SELF_DELETED_TTL = 300

MUTE_CMDS = {"mute", "мут", "заглушить"}
UNMUTE_CMDS = {"unmute", "размут", "разглушить"}
CHESS_CMDS = {"chess", "шахматы"}
CHECKERS_CMDS = {"checkers", "шашки"}

_self_deleted: dict[tuple[str, int, int], float] = {}


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
    return row


async def _save_connection(db: Database, conn: BusinessConnection) -> tuple[bool, bool]:
    rights = conn.rights
    can_delete = bool(rights and rights.can_delete_all_messages)
    can_reply = bool((rights and rights.can_reply) or conn.can_reply)
    await db.save_business_connection(conn.id, conn.user.id, conn.user_chat_id, conn.is_enabled,
                                      can_reply, can_delete)
    await db.upsert_user(conn.user.id, conn.user.username, conn.user.first_name)
    return can_reply, can_delete


async def cache(db: Database, m: Message, bc_id: str) -> None:
    mtype, fid = extract_media(m)
    u = m.from_user
    await db.cache_business_message(
        bc_id, m.chat.id, m.message_id, u.id if u else 0, u.full_name if u else None,
        u.username if u else None, describe(m), mtype, fid, int(m.date.timestamp()),
    )


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
        text = "💼 Бизнес-ассистент отключён. Уведомления больше не будут приходить."
    else:
        text = "✅ <b>Бизнес-ассистент подключён!</b>\n\n" \
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


# ---------- новые сообщения ----------

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

    if not is_owner:
        if settings["biz_mute"] and await db.is_business_muted(owner_id, message.chat.id):
            await delete_business(bot, bc_id, message.chat.id, [message.message_id])
            return
        await cache(db, message, bc_id)
        return

    text = (message.text or "").strip()
    if text.startswith(".") and await dot_command(message, bot, db, conn, settings, text):
        return
    if settings["biz_disappearing"] and message.reply_to_message:
        await check_disappearing(message, bot, db, conn)


async def dot_command(message: Message, bot: Bot, db: Database, conn, settings: dict,
                      text: str) -> bool:
    cmd = text[1:].split(maxsplit=1)[0].lower() if len(text) > 1 else ""
    bc_id, chat_id, owner_id = conn["id"], message.chat.id, conn["user_id"]
    if cmd in MUTE_CMDS | UNMUTE_CMDS and settings["biz_mute"]:
        await delete_business(bot, bc_id, chat_id, [message.message_id])
        mute = cmd in MUTE_CMDS
        await db.set_business_mute(owner_id, chat_id, bc_id, mute)
        if mute:
            note = "" if conn["can_delete"] else \
                "\n⚠️ У бота нет права удалять сообщения — выдайте его в настройках бизнеса."
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
    await cache(db, message, bc_id)
    settings = await db.get_user_settings(conn["user_id"])
    if not settings["biz_edits"]:
        return
    new_text = describe(message)
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
        if row and row["from_id"] != owner_id:
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
