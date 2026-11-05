from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from ..db import Database
from ..services.userbot import Userbot

router = Router(name="gifts")
PAGE = 16


async def gifts_menu(db: Database, userbot: Userbot, user_id: int):
    sub = await db.get_gift_sub(user_id)
    chosen = await db.get_user_gift_collections(user_id)
    status = "🔔 включено" if sub["enabled"] else "🔕 выключено"
    cols = "все" if sub["all_collections"] else f"выбрано {len(chosen)}"
    n_bd = len(await db.get_user_gift_attrs(user_id, "backdrop"))
    n_pt = await db.count_user_patterns(user_id)
    bds = f"выбрано {n_bd}" if n_bd else "любой"
    pts = f"выбрано {n_pt}" if n_pt else "любой"
    text = (
        "🎁 <b>Отслеживание улучшенных подарков</b>\n\n"
        "Пока функция включена, я присылаю каждый новый улучшенный подарок: номер, модель, "
        "фон, узор с редкостью и юзернейм того, кто улучшил.\n\n"
        f"Статус: <b>{status}</b>\nКоллекции: <b>{cols}</b>\n"
        f"🎨 Фон: <b>{bds}</b>\n🌀 Узор: <b>{pts}</b>"
    )
    if not userbot.ready:
        text += f"\n\n⚠️ Источник данных (юзербот) недоступен: {userbot.error or 'не запущен'}"
    if sub["enabled"] and sub["all_collections"]:
        text += "\n\nℹ️ Улучшений очень много — советую выбрать нужные коллекции."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔕 Выключить" if sub["enabled"] else "🔔 Включить",
                              callback_data="gift:toggle")],
        [InlineKeyboardButton(text=f"📚 Коллекции ({cols})", callback_data="gift:cols:0")],
        [InlineKeyboardButton(text=f"🎨 Фон ({bds})", callback_data="gift:bd:0"),
         InlineKeyboardButton(text=f"🌀 Узор ({pts})", callback_data="gift:ptl:0")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])
    return text, kb


@router.callback_query(F.data == "menu:gifts")
async def cb_gifts(call: CallbackQuery, db: Database, userbot: Userbot):
    if not db.feature_enabled("gifts"):
        return await call.answer("Функция временно отключена администратором", show_alert=True)
    text, kb = await gifts_menu(db, userbot, call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "gift:toggle")
async def cb_gift_toggle(call: CallbackQuery, db: Database, userbot: Userbot):
    sub = await db.get_gift_sub(call.from_user.id)
    await db.set_gift_sub(call.from_user.id, enabled=not sub["enabled"])
    text, kb = await gifts_menu(db, userbot, call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Включено" if not sub["enabled"] else "Выключено")


async def collections_kb(db: Database, user_id: int, page: int):
    cols = await db.list_gift_collections()
    sub = await db.get_gift_sub(user_id)
    chosen = await db.get_user_gift_collections(user_id)
    pages = max(1, (len(cols) + PAGE - 1) // PAGE)
    page = max(0, min(page, pages - 1))
    rows = [[InlineKeyboardButton(
        text=("✅" if sub["all_collections"] else "▫️") + " Все коллекции",
        callback_data=f"gift:all:{page}",
    )]]
    row = []
    for col in cols[page * PAGE:(page + 1) * PAGE]:
        on = not sub["all_collections"] and col["slug"] in chosen
        row.append(InlineKeyboardButton(text=("✅ " if on else "") + col["title"],
                                        callback_data=f"gift:c:{col['slug']}:{page}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"gift:cols:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="gift:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"gift:cols:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:gifts")])
    return InlineKeyboardMarkup(inline_keyboard=rows), len(cols)


async def _show_cols(call: CallbackQuery, db: Database, page: int):
    kb, total = await collections_kb(db, call.from_user.id, page)
    if total == 0:
        return await call.answer("Список коллекций ещё не загружен — попробуйте позже", show_alert=True)
    await call.message.edit_text(
        "📚 <b>Выберите коллекции</b>\nОтмеченные коллекции будут присылаться. "
        "«Все коллекции» — без фильтра.",
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data.startswith("gift:cols:"))
async def cb_cols(call: CallbackQuery, db: Database):
    await _show_cols(call, db, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("gift:all:"))
async def cb_all(call: CallbackQuery, db: Database):
    await db.set_gift_sub(call.from_user.id, all_collections=1)
    await db.clear_user_gift_collections(call.from_user.id)
    await _show_cols(call, db, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("gift:c:"))
async def cb_col(call: CallbackQuery, db: Database):
    _, _, slug, page = call.data.split(":")
    uid = call.from_user.id
    sub = await db.get_gift_sub(uid)
    if sub["all_collections"]:
        await db.set_gift_sub(uid, all_collections=0)
        await db.clear_user_gift_collections(uid)
    await db.toggle_user_gift_collection(uid, slug)
    if not await db.get_user_gift_collections(uid):
        await db.set_gift_sub(uid, all_collections=1)
    await _show_cols(call, db, int(page))


@router.callback_query(F.data == "gift:noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()


# ---------- фильтры по фону и узору ----------
ATTR_PAGE = 20


def _page_rows(items: list, page: int, size: int, make_button, nav_prefix: str):
    pages = max(1, (len(items) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    rows, row = [], []
    for i, item in enumerate(items[page * size:(page + 1) * size], start=page * size):
        row.append(make_button(i, item))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"{nav_prefix}{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="gift:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"{nav_prefix}{page + 1}"))
    rows.append(nav)
    return rows, page


def _label(row, on: bool) -> str:
    rarity = f" · {row['rarity']}" if row["rarity"] and row["rarity"] != "?" else ""
    return ("✅ " if on else "") + row["name"] + rarity


async def _show_backdrops(call: CallbackQuery, db: Database, page: int):
    items = await db.list_backdrops()
    if not items:
        return await call.answer("Список фонов ещё не загружен — попробуйте позже", show_alert=True)
    chosen = await db.get_user_gift_attrs(call.from_user.id, "backdrop")
    rows, page = _page_rows(
        items, page, ATTR_PAGE,
        lambda i, r: InlineKeyboardButton(text=_label(r, r["name"] in chosen),
                                          callback_data=f"gift:bdt:{i}:{page}"),
        "gift:bd:")
    rows.insert(0, [InlineKeyboardButton(text=("✅ " if not chosen else "") + "Любой фон",
                                         callback_data=f"gift:bdr:{page}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:gifts")])
    await call.message.edit_text(
        "🎨 <b>Фильтр по фону</b>\nОтмеченные фоны — присылаю только подарки с ними "
        "(во всех коллекциях). Отсортировано от редких к частым.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("gift:bd:"))
async def cb_backdrops(call: CallbackQuery, db: Database):
    await _show_backdrops(call, db, int(call.data.split(":")[2]))


@router.callback_query(F.data.startswith("gift:bdt:"))
async def cb_backdrop_toggle(call: CallbackQuery, db: Database):
    _, _, idx, page = call.data.split(":")
    items = await db.list_backdrops()
    if int(idx) < len(items):
        await db.toggle_user_gift_attr(call.from_user.id, "backdrop", "*", items[int(idx)]["name"])
    await _show_backdrops(call, db, int(page))


@router.callback_query(F.data.startswith("gift:bdr:"))
async def cb_backdrop_reset(call: CallbackQuery, db: Database):
    await db.clear_user_gift_attrs(call.from_user.id, "backdrop")
    await _show_backdrops(call, db, int(call.data.split(":")[2]))


async def _pattern_collections(db: Database, user_id: int) -> list:
    cols = await db.list_gift_collections()
    sub = await db.get_gift_sub(user_id)
    if not sub["all_collections"]:
        chosen = await db.get_user_gift_collections(user_id)
        cols = [c for c in cols if c["slug"] in chosen] or cols
    return cols


@router.callback_query(F.data.startswith("gift:ptl:"))
async def cb_pattern_collections(call: CallbackQuery, db: Database):
    page = int(call.data.split(":")[2])
    cols = await _pattern_collections(db, call.from_user.id)
    if not cols:
        return await call.answer("Список коллекций ещё не загружен — попробуйте позже",
                                 show_alert=True)
    counts = {}
    for c in cols:
        counts[c["slug"]] = len(await db.get_user_gift_attrs(call.from_user.id, "pattern", c["slug"]))
    rows, page = _page_rows(
        cols, page, 16,
        lambda i, c: InlineKeyboardButton(
            text=c["title"] + (f" ({counts[c['slug']]})" if counts[c["slug"]] else ""),
            callback_data=f"gift:ptc:{c['slug']}:0"),
        "gift:ptl:")
    rows.append([InlineKeyboardButton(text="🧹 Сбросить все узоры", callback_data="gift:ptx")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:gifts")])
    await call.message.edit_text(
        "🌀 <b>Фильтр по узору</b>\nУзоры у каждой коллекции свои — выберите коллекцию. "
        "В скобках — сколько узоров отмечено.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


async def _show_patterns(call: CallbackQuery, db: Database, slug: str, page: int):
    items = await db.list_patterns(slug)
    if not items:
        return await call.answer("Узоры этой коллекции ещё не загружены — попробуйте позже",
                                 show_alert=True)
    chosen = await db.get_user_gift_attrs(call.from_user.id, "pattern", slug)
    rows, page = _page_rows(
        items, page, ATTR_PAGE,
        lambda i, r: InlineKeyboardButton(text=_label(r, r["name"] in chosen),
                                          callback_data=f"gift:ptt:{slug}:{i}:{page}"),
        f"gift:ptc:{slug}:")
    rows.insert(0, [InlineKeyboardButton(text=("✅ " if not chosen else "") + "Любой узор",
                                         callback_data=f"gift:ptr:{slug}:{page}")])
    rows.append([InlineKeyboardButton(text="⬅️ К коллекциям", callback_data="gift:ptl:0")])
    title = next((c["title"] for c in await db.list_gift_collections() if c["slug"] == slug), slug)
    await call.message.edit_text(
        f"🌀 <b>Узоры: {title}</b>\nОтмеченные — присылаю подарки этой коллекции только с ними. "
        "Отсортировано от редких к частым.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("gift:ptc:"))
async def cb_patterns(call: CallbackQuery, db: Database):
    _, _, slug, page = call.data.split(":")
    await _show_patterns(call, db, slug, int(page))


@router.callback_query(F.data.startswith("gift:ptt:"))
async def cb_pattern_toggle(call: CallbackQuery, db: Database):
    _, _, slug, idx, page = call.data.split(":")
    items = await db.list_patterns(slug)
    if int(idx) < len(items):
        await db.toggle_user_gift_attr(call.from_user.id, "pattern", slug, items[int(idx)]["name"])
    await _show_patterns(call, db, slug, int(page))


@router.callback_query(F.data.startswith("gift:ptr:"))
async def cb_pattern_reset(call: CallbackQuery, db: Database):
    _, _, slug, page = call.data.split(":")
    await db.clear_user_gift_attrs(call.from_user.id, "pattern", slug)
    await _show_patterns(call, db, slug, int(page))


@router.callback_query(F.data == "gift:ptx")
async def cb_pattern_reset_all(call: CallbackQuery, db: Database, userbot: Userbot):
    await db.clear_user_gift_attrs(call.from_user.id, "pattern")
    text, kb = await gifts_menu(db, userbot, call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Фильтр по узорам сброшен")
