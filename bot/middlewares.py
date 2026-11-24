from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from .config import Config
from .db import Database


REFUSAL = "Я отказываюсь отвечать на ваши запросы."


class TrackMiddleware(BaseMiddleware):
    """Запоминает пользователей/участников групп и отсекает заблокированных в боте."""

    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config
        self._known_chats: set[int] = set()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if isinstance(event, Message) and event.business_connection_id:
            return await handler(event, data)  # бизнес-чаты обрабатываются отдельно
        if user and not user.is_bot and user.id not in self.config.admin_ids and await self.db.is_banned(user.id):
            if isinstance(event, CallbackQuery):
                return await event.answer(REFUSAL, show_alert=True)
            if isinstance(event, Message):
                if event.chat.type == "private":
                    return await event.reply(REFUSAL)
                from .handlers.ai import ai_trigger, ai_voice_trigger, check_moderation
                from .utils.filters import PREFIXES

                if ((event.text or event.caption) and not (event.voice or event.video_note)
                        and await self.db.group_feature(event.chat.id, "ai")
                        and await check_moderation(event, data)):
                    return None
                text = (event.text or "").strip()
                first = text.split(maxsplit=1)[0] if text else ""
                command = first[:1] in PREFIXES and first[1:2].isalpha()
                if command or await ai_trigger(event, data["bot"], self.config, self.db) or (
                        await ai_voice_trigger(event, data["bot"], self.config)):
                    return await event.reply(REFUSAL)
                return None
        if isinstance(event, Message) and user and not user.is_bot:
            chat = event.chat
            if chat.type == "private":
                await self.db.upsert_user(user.id, user.username, user.first_name, started=True)
            elif chat.type in ("group", "supergroup"):
                if chat.id not in self._known_chats:
                    await self.db.upsert_chat(chat.id, chat.title, chat.type)
                    self._known_chats.add(chat.id)
                await self.db.touch_member(chat.id, user.id, user.full_name, user.username)
        elif isinstance(event, CallbackQuery) and user and event.message:
            chat = event.message.chat
            if chat.type in ("group", "supergroup"):
                await self.db.touch_member(chat.id, user.id, user.full_name, user.username)
        return await handler(event, data)
