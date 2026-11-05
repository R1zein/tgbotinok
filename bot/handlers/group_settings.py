from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database
from ..features import GROUP_TOGGLES
from ..utils.filters import Cmd
from ..utils.perms import is_chat_admin, message_from_admin

router = Router(name="group_settings")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

WARN_ACTIONS = {"mute": "🔇 мут на сутки", "kick": "👢 кик", "ban": "⛔ бан"}


async def settings_kb(db: Database, chat_id: int) -> InlineKeyboardMarkup:
    s = await db.get_chat_settings(chat_id)
    rows = []
    for key, label in GROUP_TOGGLES.items():
        if not db.feature_enabled(key.replace("call_admins_only", "call")):
            continue
        mark = "✅" if s.get(key) else "❌"
        rows.append([InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"gs:{key}")])
    rows.append([
        InlineKeyboardButton(text=f"⚠️ Лимит варнов: {s['warn_limit']}", callback_data="gs:warn_limit"),
    ])
    rows.append([
        InlineKeyboardButton(text=f"При лимите: {WARN_ACTIONS[s['warn_action']]}",
                             callback_data="gs:warn_action"),
    ])
    if s.get("nicks") and db.feature_enabled("nicks"):
        rows.append([InlineKeyboardButton(text="🏷 Настроить имена для отметок",
                                          callback_data="nk:start")])
    rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data="gs:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Cmd("команды", "commands", "помощь"))
async def cmd_group_commands(message: Message):
    from .start import GROUP_HELP
    await message.reply(GROUP_HELP)


@router.message(Command("settings"))
async def cmd_settings(message: Message, bot: Bot, db: Database):
    if not await message_from_admin(message, bot):
        return await message.reply("⛔ Настройки доступны только администраторам.")
    await message.answer("⚙️ <b>Ассистент групп — настройки</b>", reply_markup=await settings_kb(db, message.chat.id))


@router.callback_query(F.data.startswith("gs:"))
async def cb_settings(call: CallbackQuery, bot: Bot, db: Database):
    chat_id = call.message.chat.id
    if not await is_chat_admin(bot, chat_id, call.from_user.id):
        return await call.answer("Только для администраторов", show_alert=True)
    key = call.data[3:]
    if key == "close":
        await call.message.delete()
        return await call.answer()
    s = await db.get_chat_settings(chat_id)
    if key in GROUP_TOGGLES:
        await db.set_chat_setting(chat_id, key, not s.get(key))
    elif key == "warn_limit":
        await db.set_chat_setting(chat_id, key, s["warn_limit"] + 1 if s["warn_limit"] < 10 else 2)
    elif key == "warn_action":
        order = list(WARN_ACTIONS)
        await db.set_chat_setting(chat_id, key, order[(order.index(s["warn_action"]) + 1) % len(order)])
    else:
        return await call.answer()
    await call.message.edit_reply_markup(reply_markup=await settings_kb(db, chat_id))
    await call.answer("Сохранено")
