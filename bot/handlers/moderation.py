import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..db import Database
from ..utils.filters import Cmd
from ..utils.perms import is_chat_admin, message_from_admin
from ..utils.text import esc, mention
from ..utils.timeparse import for_duration, parse_duration

router = Router(name="moderation")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

MUTED = ChatPermissions(
    can_send_messages=False, can_send_audios=False, can_send_documents=False,
    can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
    can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
    can_add_web_page_previews=False,
)
UNMUTED = ChatPermissions(
    can_send_messages=True, can_send_audios=True, can_send_documents=True,
    can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
    can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True, can_invite_users=True,
)


class Target:
    def __init__(self, user_id: int, name: str, rest: str):
        self.id, self.name, self.rest = user_id, name, rest

    @property
    def link(self) -> str:
        return mention(self.id, self.name)


async def resolve_target(message: Message, bot: Bot, db: Database, args: str) -> Target | None:
    reply = message.reply_to_message
    if reply and not reply.forum_topic_created and reply.from_user and not reply.from_user.is_bot:
        return Target(reply.from_user.id, reply.from_user.full_name, args)
    parts = args.split(maxsplit=1)
    if not parts:
        return None
    token, rest = parts[0], (parts[1] if len(parts) > 1 else "")
    if token.startswith("@"):
        row = await db.find_member_by_username(message.chat.id, token)
        if row:
            return Target(row["user_id"], row["first_name"] or token, rest)
        return None
    if token.lstrip("-").isdigit():
        uid = int(token)
        try:
            member = await bot.get_chat_member(message.chat.id, uid)
            return Target(uid, member.user.full_name, rest)
        except TelegramBadRequest:
            return Target(uid, str(uid), rest)
    return None


def split_duration(rest: str) -> tuple[int | None, str]:
    parts = rest.split(maxsplit=1)
    if parts:
        secs = parse_duration(parts[0])
        if secs is not None:
            return secs, parts[1] if len(parts) > 1 else ""
    return None, rest


async def prepare(message: Message, bot: Bot, db: Database, args: str) -> Target | None:
    """Общие проверки: функция включена, автор — админ, цель найдена и не админ."""
    if not await db.group_feature(message.chat.id, "moderation"):
        return None
    if not await message_from_admin(message, bot, need_restrict=True):
        await message.reply("⛔ Нужны права администратора с возможностью блокировки.")
        return None
    target = await resolve_target(message, bot, db, args)
    if target is None:
        await message.reply("Ответьте на сообщение пользователя или укажите @юзернейм / ID.")
        return None
    me = await bot.me()
    if target.id == me.id:
        await message.reply("🙃 Не могу применить это к себе.")
        return None
    if await is_chat_admin(bot, message.chat.id, target.id):
        await message.reply("⛔ Нельзя применить к администратору.")
        return None
    return target


def until(secs: int | None) -> int | None:
    return int(time.time()) + secs if secs else None


def reason_line(reason: str) -> str:
    return f"\n📝 Причина: {esc(reason)}" if reason else ""


def undo_kb(action: str, user_id: int, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f"mod:{action}:{user_id}")
    ]])


async def do_mute(bot: Bot, chat_id: int, user_id: int, secs: int | None) -> None:
    await bot.restrict_chat_member(chat_id, user_id, MUTED, until_date=until(secs))


async def do_ban(bot: Bot, chat_id: int, user_id: int, secs: int | None) -> None:
    await bot.ban_chat_member(chat_id, user_id, until_date=until(secs))


async def do_kick(bot: Bot, chat_id: int, user_id: int) -> None:
    await bot.ban_chat_member(chat_id, user_id)
    await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)


@router.message(Cmd("mute", "мут", "замутить", "заткнуть"))
async def cmd_mute(message: Message, bot: Bot, db: Database, args: str):
    target = await prepare(message, bot, db, args)
    if not target:
        return
    secs, reason = split_duration(target.rest)
    try:
        await do_mute(bot, message.chat.id, target.id, secs)
    except TelegramBadRequest as e:
        return await message.reply(f"❌ Не удалось: {esc(e.message)}")
    await message.answer(
        f"🔇 {target.link} заглушен(а) {for_duration(secs)}.{reason_line(reason)}",
        reply_markup=undo_kb("unmute", target.id, "🔊 Размутить"),
    )


@router.message(Cmd("unmute", "размут", "размутить", "анмут", "анмьют", "unmut"))
async def cmd_unmute(message: Message, bot: Bot, db: Database, args: str):
    target = await prepare(message, bot, db, args)
    if not target:
        return
    try:
        await bot.restrict_chat_member(message.chat.id, target.id, UNMUTED)
    except TelegramBadRequest as e:
        return await message.reply(f"❌ Не удалось: {esc(e.message)}")
    await message.answer(f"🔊 {target.link} снова может писать.")


@router.message(Cmd("ban", "бан", "забанить"))
async def cmd_ban(message: Message, bot: Bot, db: Database, args: str):
    target = await prepare(message, bot, db, args)
    if not target:
        return
    secs, reason = split_duration(target.rest)
    try:
        await do_ban(bot, message.chat.id, target.id, secs)
    except TelegramBadRequest as e:
        return await message.reply(f"❌ Не удалось: {esc(e.message)}")
    await message.answer(
        f"⛔ {target.link} заблокирован(а) {for_duration(secs)}.{reason_line(reason)}",
        reply_markup=undo_kb("unban", target.id, "♻️ Разбанить"),
    )


@router.message(Cmd("unban", "разбан", "разбанить", "анбан"))
async def cmd_unban(message: Message, bot: Bot, db: Database, args: str):
    target = await prepare(message, bot, db, args)
    if not target:
        return
    try:
        await bot.unban_chat_member(message.chat.id, target.id, only_if_banned=True)
    except TelegramBadRequest as e:
        return await message.reply(f"❌ Не удалось: {esc(e.message)}")
    await message.answer(f"♻️ {target.link} разблокирован(а).")


@router.message(Cmd("kick", "кик", "кикнуть"))
async def cmd_kick(message: Message, bot: Bot, db: Database, args: str):
    target = await prepare(message, bot, db, args)
    if not target:
        return
    try:
        await do_kick(bot, message.chat.id, target.id)
    except TelegramBadRequest as e:
        return await message.reply(f"❌ Не удалось: {esc(e.message)}")
    await db.remove_member(message.chat.id, target.id)
    await message.answer(f"👢 {target.link} исключён(а) из чата.{reason_line(target.rest)}")


@router.message(Cmd("warn", "варн", "пред", "предупреждение"))
async def cmd_warn(message: Message, bot: Bot, db: Database, args: str):
    target = await prepare(message, bot, db, args)
    if not target:
        return
    settings = await db.get_chat_settings(message.chat.id)
    limit = int(settings["warn_limit"])
    count = await db.add_warn(message.chat.id, target.id)
    text = f"⚠️ {target.link} получает предупреждение ({count}/{limit}).{reason_line(target.rest)}"
    if count >= limit:
        await db.reset_warns(message.chat.id, target.id)
        action = settings["warn_action"]
        try:
            if action == "ban":
                await do_ban(bot, message.chat.id, target.id, None)
                text += "\n⛔ Лимит предупреждений — бан."
            elif action == "kick":
                await do_kick(bot, message.chat.id, target.id)
                text += "\n👢 Лимит предупреждений — кик."
            else:
                await do_mute(bot, message.chat.id, target.id, 86400)
                text += "\n🔇 Лимит предупреждений — мут на сутки."
        except TelegramBadRequest as e:
            text += f"\n❌ Не удалось наказать: {esc(e.message)}"
    await message.answer(text, reply_markup=undo_kb("unwarn", target.id, "➖ Снять варн")
                         if count < limit else None)


async def _unwarn(message: Message, bot: Bot, db: Database, args: str):
    target = await prepare(message, bot, db, args)
    if not target:
        return
    count = await db.add_warn(message.chat.id, target.id, -1)
    await message.answer(f"✅ С {target.link} снято предупреждение. Осталось: {count}.")


@router.message(Cmd("unwarn", "разварн", "снятьварн", "анварн"))
async def cmd_unwarn(message: Message, bot: Bot, db: Database, args: str):
    await _unwarn(message, bot, db, args)


@router.message(Cmd("снять"), F.text.lower().regexp(r"^\S+\s+(варн|пред)"))
async def cmd_unwarn_ru(message: Message, bot: Bot, db: Database, args: str):
    await _unwarn(message, bot, db, args.split(maxsplit=1)[1] if " " in args else "")


@router.message(Cmd("warns", "варны", "преды"))
async def cmd_warns(message: Message, bot: Bot, db: Database, args: str):
    if not await db.group_feature(message.chat.id, "moderation"):
        return
    target = await resolve_target(message, bot, db, args)
    if target is None:
        u = message.from_user
        target = Target(u.id, u.full_name, "")
    settings = await db.get_chat_settings(message.chat.id)
    count = await db.get_warns(message.chat.id, target.id)
    await message.reply(f"⚠️ У {target.link} предупреждений: {count}/{settings['warn_limit']}")


@router.callback_query(F.data.startswith("mod:"))
async def cb_mod(call: CallbackQuery, bot: Bot, db: Database):
    _, action, uid = call.data.split(":")
    chat_id = call.message.chat.id
    if not await is_chat_admin(bot, chat_id, call.from_user.id, need_restrict=True):
        return await call.answer("Только для администраторов", show_alert=True)
    uid = int(uid)
    try:
        if action == "unmute":
            await bot.restrict_chat_member(chat_id, uid, UNMUTED)
            note = "🔊 Размучен(а)"
        elif action == "unban":
            await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
            note = "♻️ Разбанен(а)"
        elif action == "unwarn":
            count = await db.add_warn(chat_id, uid, -1)
            note = f"➖ Варн снят (осталось {count})"
        else:
            return await call.answer()
    except TelegramBadRequest as e:
        return await call.answer(f"Ошибка: {e.message}", show_alert=True)
    await call.message.edit_text(
        f"{call.message.html_text}\n\n{note} — {mention(call.from_user.id, call.from_user.full_name)}",
        reply_markup=None,
    )
    await call.answer()
