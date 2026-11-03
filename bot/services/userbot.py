"""Обёртка над Telethon-клиентом (технический аккаунт) с ограничением частоты запросов."""
import asyncio
import logging
import time

from telethon import TelegramClient
from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from ..config import Config
from ..proxy import telethon_proxy

log = logging.getLogger(__name__)


class Userbot:
    def __init__(self, config: Config):
        self.config = config
        self.client: TelegramClient | None = None
        self.ready = False
        self.me = None
        self.error: str | None = None
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def start(self) -> None:
        if not self.config.userbot_configured:
            self.error = "не настроен (API_ID / API_HASH / USERBOT_SESSION)"
            log.warning("Юзербот %s — подарки и юзернеймы недоступны", self.error)
            return
        try:
            self.client = TelegramClient(
                StringSession(self.config.userbot_session),
                self.config.api_id,
                self.config.api_hash,
                proxy=telethon_proxy(self.config.proxy),
            )
            await self.client.connect()
            if not await self.client.is_user_authorized():
                self.error = "сессия недействительна, создайте новую: python -m bot.services.login"
                log.error("Юзербот: %s", self.error)
                return
            self.me = await self.client.get_me()
            self.ready = True
            log.info("Юзербот запущен как %s (%s)", self.me.username, self.me.id)
        except Exception as e:  # noqa: BLE001
            self.error = f"ошибка запуска: {e}"
            log.exception("Юзербот не запустился")

    async def stop(self) -> None:
        if self.client:
            await self.client.disconnect()

    async def call(self, request):
        """Выполняет MTProto-запрос с паузой между вызовами и ожиданием FloodWait."""
        if not self.ready:
            raise RuntimeError("Юзербот не готов")
        for _ in range(3):
            async with self._lock:
                delay = self.config.userbot_min_delay - (time.monotonic() - self._last_call)
                if delay > 0:
                    await asyncio.sleep(delay)
                try:
                    return await self.client(request)
                except FloodWaitError as e:
                    log.warning("FloodWait %s сек", e.seconds)
                    await asyncio.sleep(e.seconds + 1)
                finally:
                    self._last_call = time.monotonic()
        raise RuntimeError("Слишком много FloodWait подряд")

    # ---------- участники групп (для калла) ----------

    async def _group_entity(self, bot, chat_id: int):
        """Находит группу; если юзербота в ней нет — заходит (по юзернейму или
        по одноразовой ссылке-приглашению, которую создаёт бот-администратор)."""
        for attempt in range(2):
            try:
                return await self.client.get_entity(chat_id)
            except ValueError:
                if attempt == 0:
                    await self.client.get_dialogs()  # обновляем кэш известных чатов
        chat = await bot.get_chat(chat_id)
        if chat.username:
            await self.call(JoinChannelRequest(chat.username))
            return await self.client.get_entity(chat.username)
        link = await bot.create_chat_invite_link(chat_id, name="assistant", member_limit=1)
        invite_hash = link.invite_link.rsplit("/", 1)[-1].lstrip("+")
        try:
            updates = await self.call(ImportChatInviteRequest(invite_hash))
            return updates.chats[0]
        except UserAlreadyParticipantError:
            await self.client.get_dialogs()
            return await self.client.get_entity(chat_id)

    async def group_members(self, bot, chat_id: int, limit: int = 10000) -> list[tuple[int, str, str | None]]:
        """[(id, имя, юзернейм)] всех участников группы, кроме ботов и удалённых аккаунтов."""
        if not self.ready:
            return []
        entity = await self._group_entity(bot, chat_id)
        result = []
        async with self._lock:
            async for u in self.client.iter_participants(entity, limit=limit):
                if u.bot or u.deleted or u.id == self.me.id:
                    continue
                name = " ".join(filter(None, [u.first_name, u.last_name])) or str(u.id)
                result.append((u.id, name, u.username))
        return result
