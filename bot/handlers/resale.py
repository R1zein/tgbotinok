"""Меню «💸 Выгодные лоты маркета»: валюта, порог скидки, коллекции, фон и узор."""
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from ..db import Database
from ..services.resale import CURRENCY
from ..services.userbot import Userbot
from .gifts import _label, _page_rows

router = Router(name="resale")
DISCOUNTS = [10, 15, 20, 30, 40, 50]
CUR_ORDER = ["any", "stars", "ton"]
KINDS = {"p": ("r_pattern", "🌀 Узор", "Узоры")}
PAGE = 20


def B(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


async def resale_menu(db: Database, userbot: Userbot, user_id: int):
    sub = await db.get_resale_sub(user_id)
    cols = await db.get_resale_collections(user_id)
    cols_t = "все" if sub["all_collections"] else f"выбрано {len(cols)}"
    n = {k: await db.count_user_attrs(user_id, kind) for k, (kind, _, _) in KINDS.items()}
    n_bd = await db.count_user_attrs(user_id, "r_backdrop")
    fmt = lambda c: f"выбрано {c}" if c else "любой"  # noqa: E731
    text = (
        "💸 <b>Выгодные лоты маркета Telegram</b>\n\n"
        "Присылаю новые лоты, которые дешевле следующего лота <b>с той же моделью и фоном</b> на "
        "выбранный процент (узор не учитывается). Это оценка «ниже рынка», а не гарантия выгоды — проверяйте перед покупкой.\n\n"
        f"Статус: <b>{'🔔 включено' if sub['enabled'] else '🔕 выключено'}</b>\n"
        f"Валюта: <b>{CURRENCY[sub['currency']]}</b>\n"
        f"Дешевле рынка минимум на: <b>{sub['discount']}%</b>\n"
        f"Коллекции: <b>{cols_t}</b>\n"
        f"🎨 Фон: <b>{fmt(n_bd)}</b> · 🌀 Узор: <b>{fmt(n['p'])}</b>"
    )
    if not userbot.ready:
        text += f"\n\n⚠️ Источник данных (юзербот) недоступен: {userbot.error or 'не запущен'}"
    if sub["enabled"] and sub["all_collections"]:
        text += "\n\nℹ️ Лотов очень много — советую выбрать нужные коллекции, так быстрее."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [B("🔕 Выключить" if sub["enabled"] else "🔔 Включить", "rs:toggle")],
        [B(f"💱 Валюта: {CURRENCY[sub['currency']]}", "rs:cur"),
         B(f"📉 Скидка от {sub['discount']}%", "rs:disc")],
        [B(f"📚 Коллекции ({cols_t})", "rs:cols:0")],
        [B(f"🎨 Фон ({fmt(n_bd)})", "rs:bd:0"),
         B(f"🌀 Узор ({fmt(n['p'])})", "rs:al:p:0")],
        [B("⬅️ Назад", "menu:gifts")],
    ])
    return text, kb


async def _show_menu(call: CallbackQuery, db: Database, userbot: Userbot, note: str | None = None):
    text, kb = await resale_menu(db, userbot, call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer(note)


@router.callback_query(F.data == "rs:menu")
async def cb_menu(call: CallbackQuery, db: Database, userbot: Userbot):
    if not db.feature_enabled("gifts"):
        return await call.answer("Функция временно отключена администратором", show_alert=True)
    await _show_menu(call, db, userbot)


@router.callback_query(F.data == "rs:toggle")
async def cb_toggle(call: CallbackQuery, db: Database, userbot: Userbot):
    sub = await db.get_resale_sub(call.from_user.id)
    await db.set_resale_sub(call.from_user.id, enabled=not sub["enabled"])
    await _show_menu(call, db, userbot, "Выключено" if sub["enabled"] else "Включено")


@router.callback_query(F.data == "rs:cur")
async def cb_currency(call: CallbackQuery, db: Database, userbot: Userbot):
    sub = await db.get_resale_sub(call.from_user.id)
    nxt = CUR_ORDER[(CUR_ORDER.index(sub["currency"]) + 1) % len(CUR_ORDER)]
    await db.set_resale_sub(call.from_user.id, currency=nxt)
    await _show_menu(call, db, userbot)


@router.callback_query(F.data == "rs:disc")
async def cb_discount(call: CallbackQuery, db: Database, userbot: Userbot):
    sub = await db.get_resale_sub(call.from_user.id)
    i = DISCOUNTS.index(sub["discount"]) if sub["discount"] in DISCOUNTS else 0
    await db.set_resale_sub(call.from_user.id, discount=DISCOUNTS[(i + 1) % len(DISCOUNTS)])
    await _show_menu(call, db, userbot)


# ---------- коллекции ----------

async def _show_cols(call: CallbackQuery, db: Database, page: int):
    cols = await db.list_gift_collections()
    if not cols:
        return await call.answer("Список коллекций ещё не загружен — попробуйте позже",
                                 show_alert=True)
    uid = call.from_user.id
    sub = await db.get_resale_sub(uid)
    chosen = await db.get_resale_collections(uid)
    rows, page = _page_rows(
        cols, page, 16,
        lambda i, c: B(("✅ " if not sub["all_collections"] and c["slug"] in chosen else "")
                       + c["title"], f"rs:c:{c['slug']}:{page}"),
        "rs:cols:")
    rows.insert(0, [B(("✅" if sub["all_collections"] else "▫️") + " Все коллекции",
                      f"rs:all:{page}")])
    rows.append([B("⬅️ Назад", "rs:menu")])
    await call.message.edit_text("📚 <b>Коллекции для маркета</b>",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("rs:cols:"))
async def cb_cols(call: CallbackQuery, db: Database):
    await _show_cols(call, db, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("rs:all:"))
async def cb_all(call: CallbackQuery, db: Database):
    await db.set_resale_sub(call.from_user.id, all_collections=1)
    await db.clear_resale_collections(call.from_user.id)
    await _show_cols(call, db, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("rs:c:"))
async def cb_col(call: CallbackQuery, db: Database):
    _, _, slug, page = call.data.split(":")
    uid = call.from_user.id
    if (await db.get_resale_sub(uid))["all_collections"]:
        await db.set_resale_sub(uid, all_collections=0)
        await db.clear_resale_collections(uid)
    await db.toggle_resale_collection(uid, slug)
    if not await db.get_resale_collections(uid):
        await db.set_resale_sub(uid, all_collections=1)
    await _show_cols(call, db, int(page))


# ---------- фон (общий для всех коллекций) ----------

async def _show_backdrops(call: CallbackQuery, db: Database, page: int):
    items = await db.list_backdrops()
    if not items:
        return await call.answer("Список фонов ещё не загружен — попробуйте позже", show_alert=True)
    chosen = await db.get_user_gift_attrs(call.from_user.id, "r_backdrop", "*")
    rows, page = _page_rows(items, page, PAGE,
                            lambda i, r: B(_label(r, r["name"] in chosen), f"rs:bdt:{i}:{page}"),
                            "rs:bd:")
    rows.insert(0, [B(("✅ " if not chosen else "") + "Любой фон", f"rs:bdr:{page}")])
    rows.append([B("⬅️ Назад", "rs:menu")])
    await call.message.edit_text("🎨 <b>Фон</b> — отсортировано от редких к частым",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("rs:bd:"))
async def cb_bd(call: CallbackQuery, db: Database):
    await _show_backdrops(call, db, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("rs:bdt:"))
async def cb_bd_toggle(call: CallbackQuery, db: Database):
    _, _, idx, page = call.data.split(":")
    items = await db.list_backdrops()
    if int(idx) < len(items):
        await db.toggle_user_gift_attr(call.from_user.id, "r_backdrop", "*", items[int(idx)]["name"])
    await _show_backdrops(call, db, int(page))


@router.callback_query(F.data.startswith("rs:bdr:"))
async def cb_bd_reset(call: CallbackQuery, db: Database):
    await db.clear_user_gift_attrs(call.from_user.id, "r_backdrop")
    await _show_backdrops(call, db, int(call.data.split(":")[2]))


# ---------- узор (свой у каждой коллекции) ----------

async def _items(db: Database, k: str, slug: str):
    return await db.list_patterns(slug)


@router.callback_query(F.data.startswith("rs:al:"))
async def cb_attr_collections(call: CallbackQuery, db: Database):
    _, _, k, page = call.data.split(":")
    kind, title, _ = KINDS[k]
    uid = call.from_user.id
    cols = await db.list_gift_collections()
    sub = await db.get_resale_sub(uid)
    if not sub["all_collections"]:
        chosen = await db.get_resale_collections(uid)
        cols = [c for c in cols if c["slug"] in chosen] or cols
    if not cols:
        return await call.answer("Список коллекций ещё не загружен — попробуйте позже",
                                 show_alert=True)
    counts = {c["slug"]: len(await db.get_user_gift_attrs(uid, kind, c["slug"])) for c in cols}
    rows, page = _page_rows(
        cols, int(page), 16,
        lambda i, c: B(c["title"] + (f" ({counts[c['slug']]})" if counts[c["slug"]] else ""),
                       f"rs:ac:{k}:{c['slug']}:0"),
        f"rs:al:{k}:")
    rows.append([B("🧹 Сбросить всё", f"rs:ax:{k}")])
    rows.append([B("⬅️ Назад", "rs:menu")])
    await call.message.edit_text(f"{title} — у каждой коллекции свои варианты, выберите коллекцию",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


async def _show_attrs(call: CallbackQuery, db: Database, k: str, slug: str, page: int):
    kind, title, plural = KINDS[k]
    items = await _items(db, k, slug)
    if not items:
        return await call.answer(f"{plural} этой коллекции ещё не загружены — попробуйте позже",
                                 show_alert=True)
    chosen = await db.get_user_gift_attrs(call.from_user.id, kind, slug)
    rows, page = _page_rows(items, page, PAGE,
                            lambda i, r: B(_label(r, r["name"] in chosen),
                                           f"rs:at:{k}:{slug}:{i}:{page}"),
                            f"rs:ac:{k}:{slug}:")
    rows.insert(0, [B(("✅ " if not chosen else "") + "Любой", f"rs:ar:{k}:{slug}:{page}")])
    rows.append([B("⬅️ К коллекциям", f"rs:al:{k}:0")])
    await call.message.edit_text(f"{title} — от редких к частым",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("rs:ac:"))
async def cb_attrs(call: CallbackQuery, db: Database):
    _, _, k, slug, page = call.data.split(":")
    await _show_attrs(call, db, k, slug, int(page))


@router.callback_query(F.data.startswith("rs:at:"))
async def cb_attr_toggle(call: CallbackQuery, db: Database):
    _, _, k, slug, idx, page = call.data.split(":")
    items = await _items(db, k, slug)
    if int(idx) < len(items):
        await db.toggle_user_gift_attr(call.from_user.id, KINDS[k][0], slug, items[int(idx)]["name"])
    await _show_attrs(call, db, k, slug, int(page))


@router.callback_query(F.data.startswith("rs:ar:"))
async def cb_attr_reset(call: CallbackQuery, db: Database):
    _, _, k, slug, page = call.data.split(":")
    await db.clear_user_gift_attrs(call.from_user.id, KINDS[k][0], slug)
    await _show_attrs(call, db, k, slug, int(page))


@router.callback_query(F.data.startswith("rs:ax:"))
async def cb_attr_reset_all(call: CallbackQuery, db: Database, userbot: Userbot):
    await db.clear_user_gift_attrs(call.from_user.id, KINDS[call.data.split(":")[2]][0])
    await _show_menu(call, db, userbot, "Фильтр сброшен")
