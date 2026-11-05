"""Маркет Telegram: уведомления о выгодных лотах (потенциально ниже рынка).

Каждые N секунд для нужных коллекций берём свежие лоты (payments.getResaleStarGifts).
Для нового лота «рынок» — следующий по цене лот ТОЙ ЖЕ МОДЕЛИ в той же валюте
(модель сильнее всего влияет на цену). Если новый лот дешевле него на заданный
подписчиком процент — шлём уведомление. Это оценка, а не гарантия выгоды.
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import LinkPreviewOptions
from telethon.errors import RPCError
from telethon.tl import functions, types

from ..db import Database
from ..utils.text import esc, fmt_num
from .gifts import _rarity, attr_info
from .notifier import safe_send
from .userbot import Userbot

log = logging.getLogger(__name__)

NEW_LISTINGS = 30   # сколько свежих лотов смотрим за проход
SEEN_LIMIT = 3000   # сколько ключей помним на коллекцию
CURRENCY = {"any": "любая", "stars": "⭐ звёзды", "ton": "💎 TON"}
NANO = 1_000_000_000


def listing_price(gift: types.StarGiftUnique) -> tuple[int | None, float | None, bool]:
    """(цена в звёздах, цена в TON, только_TON)."""
    stars = ton = None
    for amount in gift.resell_amount or []:
        if isinstance(amount, types.StarsTonAmount):
            ton = amount.amount / NANO
        elif isinstance(amount, types.StarsAmount):
            stars = amount.amount
    ton_only = bool(gift.resale_ton_only)
    if ton_only:
        stars = None
    return stars, ton, ton_only


def gift_attrs(gift: types.StarGiftUnique) -> dict:
    out = {"model": None, "backdrop": None, "pattern": None, "model_doc": None, "lines": []}
    for attr in gift.attributes:
        info = attr_info(attr)
        if not info:
            continue
        kind, name = info[0], info[1]
        out[kind] = name
        if kind == "model":
            out["model_doc"] = getattr(attr.document, "id", None)
            out["lines"].append(f"🧸 Модель: {esc(name)} ({_rarity(attr)})")
        elif kind == "backdrop":
            out["lines"].append(f"🎨 Фон: {esc(name)} ({_rarity(attr)})")
        else:
            out["lines"].append(f"🌀 Узор: {esc(name)} ({_rarity(attr)})")
    return out


def _fmt_ton(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ").rstrip("0").rstrip(".")


def price_line(stars: int | None, ton: float | None) -> str:
    parts = []
    if stars is not None:
        parts.append(f"{fmt_num(stars)} ⭐")
    if ton is not None:
        parts.append(f"{_fmt_ton(ton)} TON")
    return " / ".join(parts) or "—"


class ResaleWatcher:
    def __init__(self, bot: Bot, db: Database, userbot: Userbot, interval: int):
        self.bot, self.db, self.ub, self.interval = bot, db, userbot, interval
        self._seen: dict[str, set[str]] = {}

    async def run(self) -> None:
        while True:
            try:
                if self.ub.ready and self.db.feature_enabled("gifts"):
                    await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Ошибка в отслеживании маркета")
            await asyncio.sleep(self.interval)

    async def tick(self) -> None:
        wanted = await self.db.resale_wanted_slugs()
        if wanted is not None and not wanted:
            return
        for col in await self.db.list_gift_collections():
            if col["gift_id"] and (wanted is None or col["slug"] in wanted):
                await self.poll(col)

    async def _resale(self, gift_id: int, limit: int, **kw):
        try:
            res = await self.ub.call(functions.payments.GetResaleStarGiftsRequest(
                gift_id=gift_id, offset="", limit=limit, **kw))
        except RPCError as e:
            log.debug("Маркет %s: %s", gift_id, e)
            return []
        return [g for g in res.gifts if isinstance(g, types.StarGiftUnique)]

    async def poll(self, col) -> None:
        slug = col["slug"]
        gifts = await self._resale(col["gift_id"], NEW_LISTINGS)  # по умолчанию — новые первыми
        first_pass = slug not in self._seen
        seen = self._seen.setdefault(slug, set())
        for g in gifts:
            stars, ton, _ = listing_price(g)
            key = f"{g.num}:{stars}:{ton}"  # тот же номер с новой ценой — новый лот
            if key in seen:
                continue
            seen.add(key)
            if not first_pass:  # при первом проходе только запоминаем текущие лоты
                await self.evaluate(col, g)
        if len(seen) > SEEN_LIMIT:
            self._seen[slug] = set(list(seen)[-SEEN_LIMIT // 2:])

    async def reference_price(self, col, gift, model_doc: int | None,
                              ton_only: bool) -> tuple[float | None, str]:
        """Цена следующего по дешевизне лота той же модели (без текущего) и валюта."""
        attrs = [types.StarGiftAttributeIdModel(document_id=model_doc)] if model_doc else None
        others = [g for g in await self._resale(col["gift_id"], 10, sort_by_price=True,
                                               attributes=attrs) if g.num != gift.num]
        prices = []
        for g in others:
            stars, ton, _ = listing_price(g)
            value = ton if ton_only else stars
            if value is not None:
                prices.append(value)
        return (min(prices) if prices else None), ("ton" if ton_only else "stars")

    async def evaluate(self, col, gift) -> None:
        slug = col["slug"]
        info = gift_attrs(gift)
        for attr in gift.attributes:  # запоминаем варианты для фильтров
            ai = attr_info(attr)
            if ai:
                await self.db.save_gift_attr(slug, *ai)
        candidates = await self.db.resale_candidates(slug, None, info["backdrop"], info["pattern"])
        stars, ton, ton_only = listing_price(gift)
        # валюта подписчика: «звёзды» — лот можно купить за звёзды, «TON» — за TON
        candidates = [c for c in candidates
                      if c["currency"] == "any"
                      or (c["currency"] == "stars" and stars is not None)
                      or (c["currency"] == "ton" and ton is not None)]
        if not candidates:
            return
        ref, unit = await self.reference_price(col, gift, info["model_doc"], ton_only)
        price = ton if unit == "ton" else stars
        if not ref or price is None or price >= ref:
            return
        discount = round((ref - price) / ref * 100)
        targets = [c["user_id"] for c in candidates if discount >= c["discount"]]
        if not targets:
            return
        ref_text = f"{_fmt_ton(ref)} TON" if unit == "ton" else f"{fmt_num(int(ref))} ⭐"
        text = "\n".join([
            "💸 <b>Выгодный лот на маркете</b>\n",
            f"<b>{esc(gift.title)} #{gift.num}</b>",
            *info["lines"],
            f"\n💰 Цена: <b>{price_line(stars, ton)}</b>" + (" (только TON)" if ton_only else ""),
            f"📉 Дешевле следующего лота этой модели на <b>{discount}%</b> ({ref_text})",
            f"🔗 https://t.me/nft/{gift.slug}",
        ])
        preview = LinkPreviewOptions(is_disabled=False, url=f"https://t.me/nft/{gift.slug}",
                                     prefer_large_media=True)
        for uid in targets:
            await safe_send(self.bot, uid, text, link_preview_options=preview)
            await asyncio.sleep(0.05)
