import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..config import Config
from ..db import Database
from ..services.usernames import STATUS_TEXT, UsernameWatcher, normalize_username

router = Router(name="usernames")


class AddUsername(StatesGroup):
    waiting = State()


async def un_menu(db: Database, config: Config, user_id: int):
    rows = await db.list_user_usernames(user_id)
    lines = ["🔎 <b>Отслеживание юзернеймов</b>\n",
             "Добавьте юзернеймы, которые хотите получить — я пришлю уведомление, "
             "как только какой-то из них освободится.\n"]
    if rows:
        for r in rows:
            lines.append(f"• @{r['username']} — {STATUS_TEXT.get(r['status'], r['status'])}")
    else:
        lines.append("<i>Список пуст.</i>")
    lines.append(f"\nЛимит: {len(rows)}/{config.max_usernames}")
    kb = [[InlineKeyboardButton(text="➕ Добавить", callback_data="un:add")]]
    for r in rows:
        kb.append([InlineKeyboardButton(text=f"❌ @{r['username']}", callback_data=f"un:del:{r['username']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)


@router.callback_query(F.data == "menu:un")
async def cb_un(call: CallbackQuery, db: Database, config: Config, state: FSMContext):
    if not db.feature_enabled("usernames"):
        return await call.answer("Функция временно отключена администратором", show_alert=True)
    await state.clear()
    text, kb = await un_menu(db, config, call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "un:add")
async def cb_un_add(call: CallbackQuery, db: Database, config: Config, state: FSMContext):
    if len(await db.list_user_usernames(call.from_user.id)) >= config.max_usernames:
        return await call.answer("Достигнут лимит юзернеймов", show_alert=True)
    await state.set_state(AddUsername.waiting)
    await call.message.edit_text(
        "✍️ Отправьте один или несколько юзернеймов (через пробел, запятую или с новой строки).\n"
        "Например: <code>@paveldurov @example</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Отмена", callback_data="menu:un")
        ]]),
    )
    await call.answer()


@router.message(AddUsername.waiting, F.text, F.chat.type == "private")
async def on_usernames(message: Message, db: Database, config: Config, state: FSMContext,
                       username_watcher: UsernameWatcher):
    await state.clear()
    tokens = [t for t in re.split(r"[\s,;]+", message.text) if t]
    current = {r["username"] for r in await db.list_user_usernames(message.from_user.id)}
    added, bad, skipped = [], [], []
    for t in tokens:
        name = normalize_username(t)
        if not name:
            bad.append(t)
            continue
        if name in current or name in added:
            continue
        if len(current) + len(added) >= config.max_usernames:
            skipped.append(name)
            continue
        added.append(name)
    wait = await message.answer("⏳ Проверяю…") if added else None
    report = []
    for name in added:
        await db.add_username_watch(message.from_user.id, name)
        status = await username_watcher.check(name)
        if status:
            await db.set_watch_status(message.from_user.id, name, status)
        report.append(f"• @{name} — {STATUS_TEXT.get(status or 'unknown')}")
    if wait:
        await wait.delete()
    parts = []
    if report:
        parts.append("✅ Добавлено:\n" + "\n".join(report))
    if bad:
        parts.append("⚠️ Некорректные: " + ", ".join(bad[:10]))
    if skipped:
        parts.append(f"🚫 Не влезли в лимит ({config.max_usernames}): " +
                     ", ".join("@" + s for s in skipped))
    if not parts:
        parts.append("Ничего нового не добавлено.")
    await message.answer("\n\n".join(parts))
    text, kb = await un_menu(db, config, message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("un:del:"))
async def cb_un_del(call: CallbackQuery, db: Database, config: Config):
    await db.remove_username_watch(call.from_user.id, call.data.split(":", 2)[2])
    text, kb = await un_menu(db, config, call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Удалено")
