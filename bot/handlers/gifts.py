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
    text = (
        "🎁 <b>Отслеживание улучшенных подарков</b>\n\n"
        "Пока функция включена, я присылаю каждый новый улучшенный подарок: номер, модель, "
        "фон, узор с редкостью и юзернейм того, кто улучшил.\n\n"
        f"Статус: <b>{status}</b>\nКоллекции: <b>{cols}</b>"
    )
    if not userbot.ready:
        text += f"\n\n⚠️ Источник данных (юзербот) недоступен: {userbot.error or 'не запущен'}"
    if sub["enabled"] and sub["all_collections"]:
        text += "\n\nℹ️ Улучшений очень много — советую выбрать нужные коллекции."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔕 Выключить" if sub["enabled"] else "🔔 Включить",
                              callback_data="gift:toggle")],
        [InlineKeyboardButton(text=f"📚 Коллекции ({cols})", callback_data="gift:cols:0")],
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
