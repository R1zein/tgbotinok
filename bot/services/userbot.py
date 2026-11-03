"""Обёртка над Telethon-клиентом (технический аккаунт) с ограничением частоты запросов."""
import asyncio
import logging
import time

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from ..config import Config

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
