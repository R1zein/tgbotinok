from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..config import Config
from ..db import Database
from ..features import USER_TOGGLES

router = Router(name="start")

MAIN_TEXT = (
    "👋 <b>Привет! Я бот-помощник.</b>\n\n"
    "Что я умею:\n"
    "👥 <b>Ассистент групп</b> — добавь меня в группу: мут/бан/кик/варны, калл, "
    "экономика и казино, мини-игры, RP-команды, шахматы и шашки.\n"
    "💬 <b>Ассистент чатов</b> — подключи меня к своему аккаунту: уведомления об изменённых и удалённых "
    "сообщениях, сохранение исчезающих медиа, <code>.мут</code>, <code>.шахматы</code>, "
    "<code>.шашки</code> прямо в ЛС.\n"
    "🎁 <b>Подарки</b> — уведомления о новых улучшенных подарках и кто их улучшил.\n"
    "🔎 <b>Юзернеймы</b> — сообщу, когда нужный юзернейм освободится."
)

HELP_TEXT = (
    "📖 <b>Ассистент групп</b> (команды можно с / ! . или без префикса)\n\n"
    "<b>Модерация</b> (админы, ответом или @юзернейм):\n"
    "<code>мут 30m причина</code>, <code>размут</code>/<code>анмут</code>, <code>бан 1d</code>, <code>разбан</code>, "
    "<code>кик</code>, <code>варн</code>, <code>снять варн</code>, <code>варны</code>\n"
    "Время: s/m/h/d/w или с/м/ч/д/н. Без времени — навсегда.\n\n"
    "<b>Калл:</b> <code>калл текст</code> / <code>call</code> — пинг всех участников\n\n"
    "<b>Экономика:</b> <code>баланс</code>, <code>бонус</code>, <code>передать 100</code>, "
    "<code>топ</code>\n"
    "<b>Казино:</b> <code>казино 100</code>, <code>слоты 100</code>, <code>кости 100</code>, "
    "<code>дартс 100</code>, <code>баскет 100</code>, <code>футбол 100</code>, "
    "<code>боулинг 100</code> (вместо суммы можно «все»)\n"
    "<b>Дуэли:</b> <code>дуэль 100</code>, <code>кнб</code> [ставка]\n"
    "<b>Мини-игры:</b> <code>крестики</code>, <code>угадай</code>, <code>монетка</code>, "
    "<code>шар вопрос</code>, <code>инфа текст</code>, <code>кто текст</code>, "
    "<code>выбери A или B</code>\n"
    "<b>RP:</b> <code>обнять</code>, <code>поцеловать</code>, <code>ударить</code>, "
    "<code>погладить</code> и др. (ответом), <code>рп</code> — список\n"
    "<b>Браки:</b> <code>брак</code>, <code>развод</code>, <code>мой брак</code>, <code>браки</code>\n"
    "<b>Настольные:</b> <code>шахматы</code>, <code>шашки</code> (ответом — вызвать игрока)\n\n"
    "<b>Настройки группы:</b> /settings (админы)\n\n"
    "💬 <b>Ассистент чатов</b> (пишете вы сами в ЛС с человеком):\n"
    "<code>.мут</code> / <code>.mute</code> — заглушить собеседника\n"
    "<code>.размут</code> / <code>.unmute</code> — снять заглушку\n"
    "<code>.шахматы</code> / <code>.chess</code>, <code>.шашки</code> / <code>.checkers</code>\n"
    "<code>.клон</code> — повторять сообщения собеседника, <code>.спам текст 5</code> — до 10 раз\n"
    "<code>.пикми</code>, <code>.пират</code>, <code>.гопник</code>… — режимы текста (для этого чата), "
    "<code>.обычный</code> — выключить\n"
    "<code>.команды</code> — все команды прямо в чате\n"
    "Жирный/курсив и др. формат — в меню: 💬 Ассистент чатов → 🔤 Формат текста"
)


def main_kb(bot_username: str, is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text="👥 Ассистент групп — добавить в группу",
            url=f"https://t.me/{bot_username}?startgroup=true"
                "&admin=delete_messages+restrict_members+pin_messages+invite_users",
        )],
        [InlineKeyboardButton(text="💬 Ассистент чатов", callback_data="menu:biz"),
         InlineKeyboardButton(text="🎁 Подарки", callback_data="menu:gifts")],
        [InlineKeyboardButton(text="🔎 Юзернеймы", callback_data="menu:un"),
         InlineKeyboardButton(text="📖 Помощь", callback_data="menu:help")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 Админ-панель", callback_data="adm:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb(target: str = "menu:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=target)]]
    )


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, bot: Bot, config: Config):
    me = await bot.me()
    await message.answer(MAIN_TEXT, reply_markup=main_kb(me.username,
                                                         message.from_user.id in config.admin_ids))


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


@router.callback_query(F.data == "menu:main")
async def cb_main(call: CallbackQuery, bot: Bot, config: Config):
    me = await bot.me()
    await call.message.edit_text(
        MAIN_TEXT, reply_markup=main_kb(me.username, call.from_user.id in config.admin_ids)
    )
    await call.answer()


@router.callback_query(F.data == "menu:help")
async def cb_help(call: CallbackQuery):
    await call.message.edit_text(HELP_TEXT, reply_markup=back_kb())
    await call.answer()


# ---------- бизнес-меню ----------

async def biz_menu(db: Database, user_id: int, bot_username: str) -> tuple[str, InlineKeyboardMarkup]:
    conn = await db.get_user_business_connection(user_id)
    settings = await db.get_user_settings(user_id)
    if conn and conn["enabled"]:
        status = "✅ Подключён"
        if not conn["can_delete"]:
            status += ("\n⚠️ Нет права удалять сообщения — <code>.мут</code> не сможет удалять "
                       "сообщения. Выдайте право «Удаление сообщений» в настройках бота.")
    else:
        status = "❌ Не подключён"
    text = (
        "💬 <b>Ассистент чатов</b>\n\n"
        f"Статус: {status}\n\n"
        "<b>Как подключить:</b>\n"
        "Настройки → Telegram для бизнеса → Чат-боты → введите "
        f"@{bot_username} → разрешите читать и удалять сообщения.\n\n"
        "Уведомления будут приходить сюда, в этот чат.\n\n"
        "<b>Функции:</b>"
    )
    rows = []
    for key, label in USER_TOGGLES.items():
        mark = "✅" if settings.get(key) else "❌"
        rows.append([InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"biz:t:{key}")])
    rows.append([InlineKeyboardButton(text="🔤 Формат текста", callback_data="sty:menu")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:main")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu:biz")
async def cb_biz(call: CallbackQuery, db: Database, bot: Bot):
    if not db.feature_enabled("business"):
        return await call.answer("Ассистент чатов временно отключён администратором", show_alert=True)
    me = await bot.me()
    text, kb = await biz_menu(db, call.from_user.id, me.username)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("biz:t:"))
async def cb_biz_toggle(call: CallbackQuery, db: Database, bot: Bot):
    key = call.data.split(":", 2)[2]
    if key not in USER_TOGGLES:
        return await call.answer()
    settings = await db.get_user_settings(call.from_user.id)
    await db.set_user_setting(call.from_user.id, key, not settings.get(key))
    me = await bot.me()
    text, kb = await biz_menu(db, call.from_user.id, me.username)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


# ---------- бот добавлен/удалён из группы ----------

@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, db: Database):
    chat = event.chat
    if chat.type not in ("group", "supergroup"):
        return
    status = event.new_chat_member.status
    active = status in ("member", "administrator")
    await db.upsert_chat(chat.id, chat.title, chat.type, active=active)
    if active and event.old_chat_member.status in ("left", "kicked"):
        await event.answer(
            "👋 Спасибо, что добавили меня! Я — ассистент группы.\n\n"
            "Чтобы модерация работала, выдайте мне права администратора "
            "(удаление сообщений и блокировка участников).\n"
            "Настройки функций: /settings, список команд: /help"
        )


@router.chat_member()
async def on_chat_member(event: ChatMemberUpdated, db: Database):
    """Поддерживаем список участников для калла и «кто сегодня»."""
    user = event.new_chat_member.user
    if user.is_bot:
        return
    if event.new_chat_member.status in ("left", "kicked"):
        await db.remove_member(event.chat.id, user.id)
    elif event.new_chat_member.status in ("member", "administrator", "creator", "restricted"):
        await db.touch_member(event.chat.id, user.id, user.full_name, user.username)
