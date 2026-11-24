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
    say = State()
    ban = State()
    unban = State()
    ignore_add = State()
    ignore_remove = State()


def is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
         InlineKeyboardButton(text="⚙️ Функции", callback_data="adm:feat")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm:bc"),
         InlineKeyboardButton(text="🤖 Юзербот", callback_data="adm:ub")],
        [InlineKeyboardButton(text="✍️ Написать в группу от бота", callback_data="adm:say")],
        [InlineKeyboardButton(text="🤖 Игнор ИИ", callback_data="adm:ignore:0")],
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
async def cb_admin(call: CallbackQuery, bot: Bot, db: Database, config: Config,
                   userbot: Userbot, state: FSMContext):
    if not is_admin(call.from_user.id, config):
        return await call.answer("Нет доступа", show_alert=True)
    action = call.data[4:]
    await state.clear()
    if action == "main":
        await call.message.edit_text("🛠 <b>Админ-панель</b>", reply_markup=admin_kb())
    elif action.startswith("ignore:"):
        page = int(action.split(":")[1])
        ids = await db.fetchall("SELECT user_id FROM ai_ignore WHERE enabled=1 ORDER BY user_id "
                                "LIMIT 21 OFFSET ?", (page * 20,))
        listing = "\n".join(str(row["user_id"]) for row in ids[:20]) or "Список пуст."
        rows = [[InlineKeyboardButton(text="Добавить ID", callback_data="adm:ignore_add"),
                 InlineKeyboardButton(text="Удалить ID", callback_data="adm:ignore_remove")]]
        if page:
            rows.append([InlineKeyboardButton(text="←", callback_data=f"adm:ignore:{page-1}")])
        if len(ids) > 20:
            rows.append([InlineKeyboardButton(text="→", callback_data=f"adm:ignore:{page+1}")])
        rows.extend(BACK.inline_keyboard)
        await call.message.edit_text(f"🤖 Игнор ИИ\n{listing}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif action in ("ignore_add", "ignore_remove"):
        await state.set_state(AdminStates.ignore_add if action == "ignore_add" else AdminStates.ignore_remove)
        await call.message.edit_text("Введите Telegram ID пользователя.", reply_markup=BACK)
    elif action == "stats":
        s = await db.stats()
        await call.message.edit_text(
            "📊 <b>Статистика</b>\n\n"
            f"👤 Пользователей: {s['users']} (заблокировано: {s['banned']})\n"
            f"👥 Групп: {s['groups']}\n"
            f"💬 Подключений ассистента чатов: {s['business']}\n"
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
    elif action == "say":
        await call.answer("Ищу группы…")
        groups = await shared_groups(bot, db, call.from_user.id)
        if not groups:
            return await call.message.edit_text(
                "✍️ Нет групп, где есть и бот, и вы.", reply_markup=BACK)
        rows = [[InlineKeyboardButton(text=f"👥 {title}"[:60], callback_data=f"adm:sg:{cid}")]
                for cid, title in groups]
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:main")])
        return await call.message.edit_text(
            "✍️ <b>Написать от лица бота</b>\nВыберите группу (показаны те, где есть и бот, "
            "и вы):", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif action.startswith("sg:"):
        chat_id = int(action[3:])
        if not await both_in_chat(bot, chat_id, call.from_user.id):
            return await call.answer("Вас или бота уже нет в этой группе", show_alert=True)
        title = await db.scalar("SELECT title FROM chats WHERE id=?", (chat_id,)) or str(chat_id)
        await state.set_state(AdminStates.say)
        await state.update_data(say_chat=chat_id, say_title=title)
        await call.message.edit_text(
            f"✍️ Пишите — всё, что отправите сюда, бот опубликует в «{title}» от своего имени: "
            "текст, фото, видео, стикеры, голосовые.\nКогда закончите — нажмите «Готово».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Готово", callback_data="adm:main")]]))
    elif action in ("ban", "unban"):
        await state.set_state(AdminStates.ban if action == "ban" else AdminStates.unban)
        await call.message.edit_text("Отправьте ID пользователя.", reply_markup=BACK)
    await call.answer()


MEMBER_STATUSES = ("creator", "administrator", "member", "restricted")


async def both_in_chat(bot: Bot, chat_id: int, user_id: int) -> bool:
    """И бот, и пользователь сейчас состоят в группе."""
    try:
        me = await bot.me()
        bot_member = await bot.get_chat_member(chat_id, me.id)
        user_member = await bot.get_chat_member(chat_id, user_id)
    except Exception:  # noqa: BLE001 — бота выгнали, группа удалена и т.п.
        return False
    return bot_member.status in MEMBER_STATUSES and user_member.status in MEMBER_STATUSES


async def shared_groups(bot: Bot, db: Database, user_id: int) -> list[tuple[int, str]]:
    rows = await db.fetchall(
        "SELECT id, title FROM chats WHERE active=1 AND type IN ('group','supergroup') "
        "ORDER BY title LIMIT 60")
    checks = await asyncio.gather(*(both_in_chat(bot, r["id"], user_id) for r in rows))
    return [(r["id"], r["title"] or str(r["id"])) for r, ok in zip(rows, checks) if ok]


@router.message(AdminStates.say, F.chat.type == "private")
async def on_say(message: Message, config: Config, state: FSMContext):
    if not is_admin(message.from_user.id, config):
        return
    data = await state.get_data()
    try:
        await message.copy_to(data["say_chat"])
    except Exception as e:  # noqa: BLE001
        log.info("Сообщение в группу не отправлено: %s", e)
        return await message.reply("❌ Не получилось отправить — возможно, у бота нет прав писать "
                                   "в этой группе.")
    await message.reply(f"✅ Отправлено в «{data['say_title']}». Можно писать ещё.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="✅ Готово", callback_data="adm:main")]]))


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

@router.message(AdminStates.ignore_add, F.chat.type == "private")
@router.message(AdminStates.ignore_remove, F.chat.type == "private")
async def on_ignore(message: Message, db: Database, config: Config, state: FSMContext):
    if not is_admin(message.from_user.id, config):
        return
    raw = (message.text or "").strip()
    if not raw.isascii() or not raw.isdigit() or len(raw) > 19 or not 0 < int(raw) < 2**63:
        return await message.answer("Введите положительный числовой Telegram ID.", reply_markup=BACK)
    uid = int(raw)
    adding = await state.get_state() == AdminStates.ignore_add.state
    if adding and uid in config.admin_ids:
        return await message.answer("Нельзя добавить администратора в игнор.", reply_markup=BACK)
    await db.set_ai_ignore(uid, adding)
    await state.clear()
    await message.answer(f"{uid} {'добавлен в игнор ИИ' if adding else 'удалён из игнора ИИ'}.",
                         reply_markup=admin_kb())
