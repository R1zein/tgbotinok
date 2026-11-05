"""Режимы текста в группах (.пикми, .милый, .пират …).

В группах бот не может редактировать чужие сообщения, поэтому сообщение участника,
включившего режим, удаляется и сразу публикуется ботом заново — в выбранном стиле
и с подписью автора. Нужны права администратора «Удаление сообщений».
"""
import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Filter
from aiogram.types import Message, ReplyParameters

from ..db import Database
from ..services.textstyles import OFF_WORDS, STYLE_ALIASES, STYLES
from ..utils.filters import Cmd
from ..utils.text import esc, user_mention

log = logging.getLogger(__name__)
router = Router(name="group_styles")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

RESET = ("обычный", "normal")
_cache: dict[tuple[int, int], str] = {}  # (чат, пользователь) -> стиль
_no_rights_warned: set[int] = set()
_bg: set[asyncio.Task] = set()


def _style_cmd(key: str) -> str:
    return next(a for a, k in STYLE_ALIASES.items() if k == key)


async def get_style(db: Database, chat_id: int, user_id: int) -> str:
    key = (chat_id, user_id)
    if key not in _cache:
        _cache[key] = (await db.get_chat_mode(user_id, chat_id))["style"]
    return _cache[key]


async def set_style(db: Database, chat_id: int, user_id: int, style: str) -> None:
    await db.set_chat_mode(user_id, chat_id, style=style)
    _cache[(chat_id, user_id)] = style


def _later_delete(bot: Bot, chat_id: int, message_id: int, seconds: int = 8) -> None:
    async def run():
        await asyncio.sleep(seconds)
        try:
            await bot.delete_message(chat_id, message_id)
        except TelegramBadRequest:
            pass
    task = asyncio.create_task(run())
    _bg.add(task)
    task.add_done_callback(_bg.discard)


@router.message(Cmd(*STYLE_ALIASES, *RESET, "стиль", "style", allow_bare=False))
async def cmd_group_style(message: Message, bot: Bot, db: Database, args: str):
    chat_id = message.chat.id
    if not message.from_user or not await db.group_feature(chat_id, "styles"):
        return
    uid = message.from_user.id
    cmd = (message.text or "").split(maxsplit=1)[0].lstrip("/!.").split("@")[0].lower()
    current = await get_style(db, chat_id, uid)
    if cmd in STYLE_ALIASES:
        key = STYLE_ALIASES[cmd]
        new = "none" if current == key else key
    elif cmd in RESET:
        new = "none"
    else:  # .стиль <имя> / .стиль — список
        arg = args.lower().strip()
        if arg in OFF_WORDS:
            new = "none"
        elif arg in STYLE_ALIASES:
            new = STYLE_ALIASES[arg]
        else:
            lines = "\n".join(f"• <code>.{_style_cmd(k)}</code> — {label}"
                              for k, (label, _) in STYLES.items())
            await message.reply("✨ <b>Режимы текста</b> — ваши сообщения будут переписываться:\n"
                                f"{lines}\n• <code>.обычный</code> — выключить")
            return
    await set_style(db, chat_id, uid, new)
    if new == "none":
        text = f"✨ Режим текста для {user_mention(message.from_user)} выключен"
    else:
        icon, label = STYLES[new][0].split(" ", 1)
        text = (f"{icon} {label.split(' (')[0].lower()}-режим включён~ для "
                f"{user_mention(message.from_user)}\nчтобы отключить, введите "
                f"<code>.{_style_cmd(new)}</code>")
    note = await message.answer(text)
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    _later_delete(bot, chat_id, note.message_id)


class HasStyle(Filter):
    """Срабатывает на обычный текст участника, у которого в этом чате включён режим."""

    async def __call__(self, message: Message, db: Database) -> bool | dict:
        if not message.text or not message.from_user or message.from_user.is_bot:
            return False
        if message.text.lstrip()[:1] in "/!.":
            return False  # команды не трогаем
        style = await get_style(db, message.chat.id, message.from_user.id)
        return {"style": style} if style in STYLES else False


@router.message(HasStyle())
async def restyle(message: Message, bot: Bot, db: Database, style: str):
    chat_id = message.chat.id
    if not await db.group_feature(chat_id, "styles"):
        return
    try:
        await message.delete()
    except (TelegramBadRequest, TelegramForbiddenError):
        # нет права удалять — не дублируем сообщение, один раз подсказываем
        if chat_id not in _no_rights_warned:
            _no_rights_warned.add(chat_id)
            await message.answer("⚠️ Для режимов текста дайте боту право «Удаление сообщений».")
        return
    styled = esc(STYLES[style][1](message.text))
    reply = message.reply_to_message
    params = (ReplyParameters(message_id=reply.message_id, allow_sending_without_reply=True)
              if reply and not reply.forum_topic_created else None)
    try:
        await bot.send_message(chat_id, f"{user_mention(message.from_user)}: {styled}"[:4096],
                               reply_parameters=params,
                               message_thread_id=message.message_thread_id
                               if message.is_topic_message else None)
    except TelegramBadRequest as e:
        log.info("group restyle: %s", e)
