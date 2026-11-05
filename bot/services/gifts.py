"""Отслеживание новых улучшенных (уникальных) подарков.

Номера уникальных подарков в коллекции выдаются последовательно при улучшении,
поэтому для каждой коллекции храним последний известный номер и запрашиваем
следующий: payments.getUniqueStarGift(slug="<Коллекция>-<номер>").
Владелец сразу после улучшения — тот, кто улучшил подарок.
"""
import asyncio
import logging
import re
import time

from aiogram import Bot
from aiogram.types import LinkPreviewOptions
from telethon.errors import RPCError
from telethon.tl import functions, types

from ..db import Database
from ..utils.text import esc
from .notifier import safe_send
from .userbot import Userbot

log = logging.getLogger(__name__)

REFRESH_EVERY = 6 * 3600
MAX_PER_POLL = 30
RARITY_NAMES = {
    types.StarGiftAttributeRarityUncommon: "необычная",
    types.StarGiftAttributeRarityRare: "редкая",
    types.StarGiftAttributeRarityEpic: "эпическая",
    types.StarGiftAttributeRarityLegendary: "легендарная",
}


def slug_base(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", title)


def _rarity(attr) -> str:
    r = getattr(attr, "rarity", None)
    if isinstance(r, types.StarGiftAttributeRarity):
        return f"{r.permille / 10:g}%"
    for cls, name in RARITY_NAMES.items():
        if isinstance(r, cls):
            return name
    return "?"


# Порядок «от редких к частым» для уровней редкости без процента
_TIER_KEY = {
    types.StarGiftAttributeRarityLegendary: 1,
    types.StarGiftAttributeRarityEpic: 5,
    types.StarGiftAttributeRarityRare: 20,
    types.StarGiftAttributeRarityUncommon: 50,
}


def attr_info(attr) -> tuple[str, str, str, int] | None:
    """(вид 'backdrop'/'pattern', название, редкость, ключ сортировки) или None."""
    if isinstance(attr, types.StarGiftAttributeBackdrop):
        kind = "backdrop"
    elif isinstance(attr, types.StarGiftAttributePattern):
        kind = "pattern"
    else:
        return None
    r = getattr(attr, "rarity", None)
    if isinstance(r, types.StarGiftAttributeRarity):
        key = r.permille
    else:
        key = next((v for cls, v in _TIER_KEY.items() if isinstance(r, cls)), 1000)
    return kind, attr.name, _rarity(attr), key


def gift_backdrop_pattern(gift) -> tuple[str | None, str | None]:
    backdrop = pattern = None
    for attr in gift.attributes:
        info = attr_info(attr)
        if info and info[0] == "backdrop":
            backdrop = info[1]
        elif info and info[0] == "pattern":
            pattern = info[1]
    return backdrop, pattern


def _peer_label(peer, users, chats) -> str | None:
    if isinstance(peer, types.PeerUser):
        for u in users:
            if u.id == peer.user_id:
                name = " ".join(filter(None, [u.first_name, u.last_name])) or str(u.id)
                if u.username:
                    return f"@{u.username} ({esc(name)})"
                return f'<a href="tg://user?id={u.id}">{esc(name)}</a> (без юзернейма)'
    if isinstance(peer, types.PeerChannel):
        for c in chats:
            if c.id == peer.channel_id:
                return f"@{c.username}" if getattr(c, "username", None) else esc(c.title)
    return None


def format_gift(gift: types.StarGiftUnique, users, chats) -> str:
    lines = [f"🎁 <b>Новый улучшенный подарок!</b>\n\n<b>{esc(gift.title)} #{gift.num}</b>"]
    for attr in gift.attributes:
        if isinstance(attr, types.StarGiftAttributeModel):
            lines.append(f"🧸 Модель: {esc(attr.name)} ({_rarity(attr)})")
        elif isinstance(attr, types.StarGiftAttributeBackdrop):
            lines.append(f"🎨 Фон: {esc(attr.name)} ({_rarity(attr)})")
        elif isinstance(attr, types.StarGiftAttributePattern):
            lines.append(f"🌀 Узор: {esc(attr.name)} ({_rarity(attr)})")
    owner = _peer_label(gift.owner_id, users, chats) if gift.owner_id else None
    if owner is None and gift.owner_name:
        owner = f"{esc(gift.owner_name)} (скрыт)"
    if owner is None and gift.owner_address:
        owner = f"TON-кошелёк <code>{esc(gift.owner_address)}</code>"
    lines.append(f"👤 Улучшил: {owner or 'скрыт'}")
    lines.append(f"📊 Выпущено: {gift.availability_issued}/{gift.availability_total}")
    lines.append(f"🔗 https://t.me/nft/{gift.slug}")
    return "\n".join(lines)


class GiftWatcher:
    def __init__(self, bot: Bot, db: Database, userbot: Userbot, interval: int):
        self.bot, self.db, self.ub, self.interval = bot, db, userbot, interval
        self._last_refresh = 0.0
        self._misses: dict[str, int] = {}

    async def run(self) -> None:
        while True:
            try:
                if self.ub.ready and self.db.feature_enabled("gifts"):
                    await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Ошибка в отслеживании подарков")
            await asyncio.sleep(self.interval)

    async def tick(self) -> None:
        if time.time() - self._last_refresh > REFRESH_EVERY:
            await self.refresh_collections()
        wanted = await self.db.wanted_gift_slugs()
        if wanted is not None and not wanted:
            return  # никто не подписан
        for col in await self.db.list_gift_collections():
            if wanted is None or col["slug"] in wanted:
                await self.poll(col)

    async def refresh_collections(self) -> None:
        res = await self.ub.call(functions.payments.GetStarGiftsRequest(hash=0))
        count = 0
        for g in getattr(res, "gifts", []):
            if isinstance(g, types.StarGift) and g.upgrade_stars and g.title:
                slug = slug_base(g.title)
                if not slug:
                    continue
                await self.db.execute(
                    "INSERT INTO gift_collections(slug, title, gift_id, status, updated_at) "
                    "VALUES(?,?,?,'new',?) ON CONFLICT(slug) DO UPDATE SET title=excluded.title, "
                    "status=CASE WHEN gift_collections.status='bad' THEN 'new' "
                    "ELSE gift_collections.status END",
                    (slug, g.title, g.id, int(time.time())),
                )
                count += 1
        self._last_refresh = time.time()
        log.info("Коллекций подарков: %s", count)
        await self.load_attributes()

    async def load_attributes(self) -> None:
        """Загружает возможные фоны и узоры для коллекций, где их ещё нет (для фильтров)."""
        known = await self.db.slugs_with_attrs()
        for col in await self.db.list_gift_collections(only_ok=False):
            if col["slug"] in known or not col["gift_id"]:
                continue
            try:
                res = await self.ub.call(
                    functions.payments.GetStarGiftUpgradeAttributesRequest(gift_id=col["gift_id"]))
            except RPCError as e:
                log.debug("Атрибуты %s: %s", col["slug"], e)
                continue
            for attr in getattr(res, "attributes", []):
                info = attr_info(attr)
                if info:
                    await self.db.save_gift_attr(col["slug"], *info)

    async def fetch(self, slug: str):
        try:
            res = await self.ub.call(functions.payments.GetUniqueStarGiftRequest(slug=slug))
        except RPCError as e:
            log.debug("Подарок %s не найден: %s", slug, e)
            return None
        if not isinstance(res.gift, types.StarGiftUnique):
            return None
        return res

    async def poll(self, col) -> None:
        slug = col["slug"]
        if col["last_num"] is None:
            first = await self.fetch(f"{slug}-1")
            if first is None:
                await self.db.execute(
                    "UPDATE gift_collections SET status='bad' WHERE slug=?", (slug,)
                )
                return
            await self.db.execute(
                "UPDATE gift_collections SET last_num=?, status='ok' WHERE slug=?",
                (first.gift.availability_issued, slug),
            )
            return
        last = col["last_num"]
        found = 0
        while found < MAX_PER_POLL:
            res = await self.fetch(f"{slug}-{last + 1}")
            if res is None:
                break
            found += 1
            last += 1
            await self.announce(slug, res)
            issued = res.gift.availability_issued
            if issued - last > MAX_PER_POLL * 3:
                last = issued - MAX_PER_POLL  # сильно отстали — не заваливаем уведомлениями
        if found == 0:
            # Номер мог «выпасть» (сгорел и т.п.) — периодически сверяемся с общим счётчиком
            misses = self._misses.get(slug, 0) + 1
            self._misses[slug] = misses
            if misses % 20 == 0:
                ref = await self.fetch(f"{slug}-1")
                if ref and ref.gift.availability_issued > last + 1:
                    last += 1
        else:
            self._misses[slug] = 0
        if last != col["last_num"]:
            await self.db.execute(
                "UPDATE gift_collections SET last_num=?, updated_at=? WHERE slug=?",
                (last, int(time.time()), slug),
            )

    async def announce(self, slug: str, res) -> None:
        text = format_gift(res.gift, res.users, res.chats)
        preview = LinkPreviewOptions(
            is_disabled=False, url=f"https://t.me/nft/{res.gift.slug}", prefer_large_media=True
        )
        backdrop, pattern = gift_backdrop_pattern(res.gift)
        for attr in res.gift.attributes:  # дополняем список вариантов тем, что встретилось
            info = attr_info(attr)
            if info:
                await self.db.save_gift_attr(slug, *info)
        for uid in await self.db.gift_subscribers_for(slug, backdrop, pattern):
            await safe_send(self.bot, uid, text, link_preview_options=preview)
            await asyncio.sleep(0.05)
