"""Отслеживание освобождения юзернеймов.

1) Быстрая проверка без авторизации: страница t.me/<username> (есть профиль — занят).
2) Подтверждение через юзербот: account.checkUsername
   (True — свободен, UsernamePurchaseAvailable — продаётся на Fragment).
   Если веб показывает профиль, MTProto-перепроверка делается раз в 6 часов,
   иначе — не чаще раза в 15 минут (бережём лимиты аккаунта).
"""
import asyncio
import logging
import re
import time

import aiohttp
from aiogram import Bot
from telethon.errors import (
    UsernameInvalidError,
    UsernameOccupiedError,
    UsernamePurchaseAvailableError,
)
from telethon.tl import functions

from ..db import Database
from ..proxy import aiohttp_connector
from .notifier import safe_send
from .userbot import Userbot

log = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{2,30}[a-zA-Z0-9]$")
MTPROTO_CACHE_TTL = 15 * 60
WEB_TAKEN_RECHECK = 6 * 3600

STATUS_TEXT = {
    "free": "🟢 свободен",
    "taken": "🔴 занят",
    "fragment": "💎 на Fragment",
    "invalid": "⚠️ недопустим",
    "unknown": "⏳ проверяется",
}


def normalize_username(raw: str) -> str | None:
    raw = raw.strip()
    raw = re.sub(r"^(https?://)?(t\.me|telegram\.me)/", "", raw, flags=re.IGNORECASE)
    raw = raw.lstrip("@").strip("/")
    if not USERNAME_RE.match(raw) or "__" in raw:
        return None
    return raw.lower()


class UsernameWatcher:
    def __init__(self, bot: Bot, db: Database, userbot: Userbot, delay: float, cycle: int,
                 proxy: str | None = None):
        self.bot, self.db, self.ub = bot, db, userbot
        self.proxy = proxy
        self.delay, self.cycle = delay, cycle
        self._mt_cache: dict[str, tuple[float, str]] = {}
        self._session: aiohttp.ClientSession | None = None

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Mozilla/5.0 (compatible; TgHelperBot/1.0)"},
                connector=aiohttp_connector(self.proxy),
                trust_env=not self.proxy,
            )
        return self._session

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def web_taken(self, username: str) -> bool | None:
        try:
            session = await self._http()
            async with session.get(f"https://t.me/{username}") as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
        except Exception as e:  # noqa: BLE001
            log.debug("web-проверка %s: %s", username, e)
            return None
        return 'class="tgme_page_title"' in html

    async def check(self, username: str) -> str | None:
        taken = await self.web_taken(username)
        if not self.ub.ready:
            if taken is None:
                return None
            return "taken" if taken else "free"
        # MTProto — главный источник истины. Если веб показывает профиль, перепроверяем редко
        # (на случай изменения вёрстки t.me), иначе — с коротким кэшем.
        cached = self._mt_cache.get(username)
        ttl = WEB_TAKEN_RECHECK if taken else MTPROTO_CACHE_TTL
        if cached and time.time() - cached[0] < ttl and cached[1] != "free":
            return "taken" if taken else cached[1]
        try:
            ok = await self.ub.call(functions.account.CheckUsernameRequest(username=username))
            status = "free" if ok else "taken"
        except UsernamePurchaseAvailableError:
            status = "fragment"
        except UsernameInvalidError:
            status = "invalid"
        except UsernameOccupiedError:
            status = "taken"
        except Exception as e:  # noqa: BLE001
            log.warning("checkUsername %s: %s", username, e)
            return None
        self._mt_cache[username] = (time.time(), status)
        return status

    async def run(self) -> None:
        while True:
            try:
                if self.db.feature_enabled("usernames"):
                    for username in await self.db.all_watched_usernames():
                        status = await self.check(username)
                        if status:
                            await self.apply(username, status)
                        await asyncio.sleep(self.delay)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Ошибка в отслеживании юзернеймов")
            await asyncio.sleep(self.cycle)

    async def apply(self, username: str, status: str) -> None:
        for row in await self.db.watchers_of(username):
            old = row["status"]
            if old == status:
                continue
            await self.db.set_watch_status(row["user_id"], username, status)
            if old == "unknown" and status != "free":
                continue
            text = self.change_text(username, old, status)
            if text:
                await safe_send(self.bot, row["user_id"], text)

    @staticmethod
    def change_text(username: str, old: str, new: str) -> str | None:
        if new == "free":
            return (f"🎉 Юзернейм <b>@{username}</b> освободился!\n"
                    "Скорее займите его: Настройки → Имя пользователя.")
        if new == "fragment":
            return (f"💎 Юзернейм <b>@{username}</b> сейчас можно получить только на Fragment:\n"
                    f"https://fragment.com/username/{username}")
        if new == "taken" and old in ("free", "fragment"):
            return f"😔 Юзернейм <b>@{username}</b> заняли."
        if new == "invalid":
            return f"⚠️ Юзернейм <b>@{username}</b> недопустим для регистрации."
        return None
