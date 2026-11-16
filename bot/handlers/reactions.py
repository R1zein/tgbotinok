"""Реакции бота в группах.

- Авто-реакции (включаются в /settings): с заданной вероятностью бот ставит на сообщение
  подходящую реакцию — по ключевым словам («ахах» → 😁, «спасибо» → 🙏…) или случайную.
- Команда «реакция 🔥» ответом на сообщение — поставить эту реакцию.

Ботам доступны только стандартные реакции Telegram (не премиум-эмодзи и не платные).
Если админы группы ограничили список реакций, неподходящие бот просто пропускает.
"""
import random
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, ReactionTypeEmoji, TelegramObject

from ..db import Database
from ..utils.filters import Cmd
from ..utils.text import esc

router = Router(name="reactions")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

# Стандартные реакции Telegram, доступные ботам
ALLOWED = [
    "👍", "👎", "❤", "🔥", "🥰", "👏", "😁", "🤔", "🤯", "😱", "🤬", "😢", "🎉", "🤩", "🤮", "💩",
    "🙏", "👌", "🕊", "🤡", "🥱", "🥴", "😍", "🐳", "❤‍🔥", "🌚", "🌭", "💯", "🤣", "⚡", "🍌", "🏆",
    "💔", "🤨", "😐", "🍓", "🍾", "💋", "🖕", "😈", "😴", "😭", "🤓", "👻", "👨‍💻", "👀", "🎃", "🙈",
    "😇", "😨", "🤝", "✍", "🤗", "🫡", "🎅", "🎄", "☃", "💅", "🤪", "🗿", "🆒", "💘", "🙉", "🦄",
    "😘", "💊", "🙊", "😎", "👾", "🤷‍♂", "🤷", "🤷‍♀", "😡",
]
# Ключевые слова → подходящие реакции (проверяются по порядку)
KEYWORDS: list[tuple[tuple[str, ...], list[str]]] = [
    (("ахах", "хаха", "хех", "лол", "ржу", "ору", "😂", "🤣"), ["😁", "🤣"]),
    (("спасибо", "спс", "благодар", "пасиб"), ["🙏", "🤝", "❤"]),
    (("люблю", "обожаю", "любимк", "❤"), ["❤", "🥰", "😍"]),
    (("круто", "огонь", "имба", "топчик", "класс", "пушка", "кайф"), ["🔥", "💯", "🆒"]),
    (("грустн", "плачу", "жаль", "печаль", "обидно"), ["😢", "🤗"]),
    (("привет", "здаров", "хай", "доброе утро", "всем ку"), ["🤗", "🫡"]),
    (("ура", "поздрав", "днюх", "день рожд", "победа"), ["🎉", "🍾", "🏆"]),
    (("спать", "сплю", "спокойной", "ночи"), ["😴", "🌚"]),
    (("кринж", "фу", "мерзко"), ["🤮", "🗿"]),
    (("бот",), ["👀", "👾"]),
]
RANDOM = ["👍", "🔥", "❤", "😁", "🤔", "👀", "🗿", "💯", "🤝", "😎", "🫡", "👏"]
CHANCES = [5, 10, 20, 35, 50, 100]
CHAT_COOLDOWN = 3  # не чаще раза в 3 сек на чат — лимиты Telegram
_last: dict[int, float] = {}


def pick_reaction(text: str) -> str:
    low = (text or "").lower()
    for words, options in KEYWORDS:
        if any(w in low for w in words):
            return random.choice(options)
    if low.rstrip().endswith("?"):
        return "🤔"
    return random.choice(RANDOM)


async def react(bot: Bot, chat_id: int, message_id: int, emoji: str) -> bool:
    try:
        await bot.set_message_reaction(chat_id, message_id, [ReactionTypeEmoji(emoji=emoji)])
        return True
    except TelegramBadRequest:
        return False  # реакция запрещена в группе, сообщение удалено и т.п.


@router.message(Cmd("реакция", "реакт", "react"))
async def cmd_react(message: Message, bot: Bot, db: Database, args: str):
    if not await db.group_feature(message.chat.id, "reactions"):
        return
    target = message.reply_to_message
    emoji = args.strip().split(maxsplit=1)[0] if args.strip() else ""
    emoji = emoji.replace("️", "")  # «❤️» → «❤»
    if not target or not emoji:
        return await message.reply("Ответьте на сообщение: <code>реакция 🔥</code>")
    if emoji not in ALLOWED:
        return await message.reply(
            f"Такую реакцию бот поставить не может. Доступные: {esc(' '.join(ALLOWED[:30]))} …")
    if await react(bot, message.chat.id, target.message_id, emoji):
        try:
            await message.delete()  # команду убираем, остаётся только реакция
        except TelegramBadRequest:
            pass
    else:
        await message.reply("Не получилось — возможно, эта реакция запрещена в группе.")


class AutoReactionMiddleware(BaseMiddleware):
    """После обработки сообщения в группе иногда ставит на него реакцию."""

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        result = await handler(event, data)
        if isinstance(event, Message):
            try:
                await self.maybe_react(event, data)
            except Exception:  # noqa: BLE001 — реакции не должны ломать остальное
                pass
        return result

    @staticmethod
    async def maybe_react(message: Message, data: dict[str, Any]) -> None:
        if message.chat.type not in ("group", "supergroup"):
            return
        if not message.from_user or message.from_user.is_bot:
            return
        text = message.text or message.caption or ""
        if text.lstrip()[:1] in ("/", "!", "."):
            return  # команды не трогаем
        db: Database = data["db"]
        settings = await db.get_chat_settings(message.chat.id)
        if not settings.get("reactions") or not db.feature_enabled("reactions"):
            return
        now = time.time()
        if now - _last.get(message.chat.id, 0) < CHAT_COOLDOWN:
            return
        if random.randint(1, 100) > int(settings.get("reactions_chance", 20)):
            return
        _last[message.chat.id] = now
        await react(data["bot"], message.chat.id, message.message_id, pick_reaction(text))
