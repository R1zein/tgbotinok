import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..config import Config
from ..db import Database
from ..features import GLOBAL_FEATURES
from ..services.userbot import Userbot

log = logging.getLogger(__name__)
router = Router(name="admin")
_tasks: set[asyncio.Task] = set()  # держим ссылки на фоновые рассылки


class AdminStates(StatesGroup):
    broadcast = State()
    ban = State()
    unban = State()


def is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
         InlineKeyboardButton(text="⚙️ Функции", callback_data="adm:feat")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm:bc"),
         InlineKeyboardButton(text="🤖 Юзербот", callback_data="adm:ub")],
        [InlineKeyboardButton(text="🚫 Заблокировать", callback_data="adm:ban"),
         InlineKeyboardButton(text="♻️ Разблокировать", callback_data="adm:unban")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")],
    ])


BACK = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")]])


@router.message(Command("admin"), F.chat.type == "private")
async def cmd_admin(message: Message, config: Config, state: FSMContext):
    if not is_admin(message.from_user.id, config):
        return
    await state.clear()
    await message.answer("🛠 <b>Админ-панель</b>", reply_markup=admin_kb())


@router.callback_query(F.data.startswith("adm:"))
async def cb_admin(call: CallbackQuery, db: Database, config: Config, userbot: Userbot,
                   state: FSMContext):
    if not is_admin(call.from_user.id, config):
        return await call.answer("Нет доступа", show_alert=True)
    action = call.data[4:]
    await state.clear()
    if action == "main":
        await call.message.edit_text("🛠 <b>Админ-панель</b>", reply_markup=admin_kb())
    elif action == "stats":
        s = await db.stats()
        await call.message.edit_text(
            "📊 <b>Статистика</b>\n\n"
            f"👤 Пользователей: {s['users']} (заблокировано: {s['banned']})\n"
            f"👥 Групп: {s['groups']}\n"
            f"💼 Бизнес-подключений: {s['business']}\n"
            f"🎁 Подписок на подарки: {s['gift_subs']}\n"
            f"🔎 Отслеживаемых юзернеймов: {s['usernames']}\n"
            f"♟ Активных партий: {s['games']}",
            reply_markup=BACK,
        )
    elif action == "feat" or action.startswith("f:"):
        if action.startswith("f:"):
            key = action[2:]
            if key in GLOBAL_FEATURES:
                await db.set_feature(key, not db.feature_enabled(key))
        rows = [[InlineKeyboardButton(
            text=("✅ " if db.feature_enabled(k) else "❌ ") + label, callback_data=f"adm:f:{k}"
        )] for k, label in GLOBAL_FEATURES.items()]
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")])
        await call.message.edit_text("⚙️ <b>Глобальные функции</b>\nВыключенная функция недоступна "
                                     "всем пользователям и группам.",
                                     reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif action == "ub":
        if userbot.ready:
            me = userbot.me
            text = f"🤖 Юзербот работает: {me.first_name} (@{me.username}, <code>{me.id}</code>)"
        else:
            text = f"🤖 Юзербот не работает: {userbot.error or 'неизвестно'}"
        cols = await db.list_gift_collections(only_ok=False)
        text += f"\n🎁 Коллекций подарков в базе: {len(cols)}"
        await call.message.edit_text(text, reply_markup=BACK)
    elif action == "bc":
        await state.set_state(AdminStates.broadcast)
        await call.message.edit_text("📢 Отправьте сообщение для рассылки (текст, фото, видео — "
                                     "что угодно). Оно будет скопировано всем пользователям.",
                                     reply_markup=BACK)
    elif action in ("ban", "unban"):
        await state.set_state(AdminStates.ban if action == "ban" else AdminStates.unban)
        await call.message.edit_text("Отправьте ID пользователя.", reply_markup=BACK)
    await call.answer()


@router.message(AdminStates.broadcast, F.chat.type == "private")
async def on_broadcast(message: Message, bot: Bot, db: Database, config: Config, state: FSMContext):
    if not is_admin(message.from_user.id, config):
        return
    await state.clear()
    users = await db.started_user_ids()
    status = await message.answer(f"⏳ Рассылка на {len(users)} пользователей…")
    task = asyncio.create_task(_broadcast(bot, message, users, status))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _broadcast(bot: Bot, source: Message, users: list[int], status: Message) -> None:
    ok = fail = 0
    for uid in users:
        for _ in range(2):
            try:
                await source.copy_to(uid)
                ok += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                fail += 1
                break
            except Exception as e:  # noqa: BLE001
                log.info("Рассылка %s: %s", uid, e)
                fail += 1
                break
        await asyncio.sleep(0.05)
    try:
        await status.edit_text(f"✅ Рассылка завершена.\nДоставлено: {ok}\nОшибок: {fail}")
    except Exception:  # noqa: BLE001
        log.info("Рассылка: %s ок, %s ошибок", ok, fail)


@router.message(AdminStates.ban, F.chat.type == "private")
@router.message(AdminStates.unban, F.chat.type == "private")
async def on_ban(message: Message, db: Database, config: Config, state: FSMContext):
    if not is_admin(message.from_user.id, config):
        return
    banning = await state.get_state() == AdminStates.ban.state
    await state.clear()
    raw = (message.text or "").strip()
    if not raw.lstrip("-").isdigit():
        return await message.answer("Нужен числовой ID.", reply_markup=admin_kb())
    uid = int(raw)
    if banning and uid in config.admin_ids:
        return await message.answer("Нельзя заблокировать администратора.", reply_markup=admin_kb())
    await db.set_banned(uid, banning)
    await message.answer(f"{'🚫 Заблокирован' if banning else '♻️ Разблокирован'}: <code>{uid}</code>",
                         reply_markup=admin_kb())
