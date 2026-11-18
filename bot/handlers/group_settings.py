from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database
from ..config import Config
from ..features import GROUP_TOGGLES
from ..services.ai import MOSCOW_OFFSET, PERSONAS, UTC_OFFSETS, offset_label
from .ai import COOLDOWNS, PASSIVE_CHOICES, MODERATION_VERSION, moderation_status
from .reactions import CHANCES
from ..utils.filters import Cmd
from ..utils.text import esc
from ..utils.perms import is_chat_admin, message_from_admin

router = Router(name="group_settings")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

WARN_ACTIONS = {"mute": "🔇 мут на сутки", "kick": "👢 кик", "ban": "⛔ бан"}


async def settings_kb(db: Database, chat_id: int) -> InlineKeyboardMarkup:
    s = await db.get_chat_settings(chat_id)
    rows = []
    offset = s.get("utc_offset", MOSCOW_OFFSET)
    rows.append([InlineKeyboardButton(text=f"🕒 Часовой пояс: {offset_label(offset)}",
                                      callback_data="gs:timezone")])
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
    if s.get("reactions") and db.feature_enabled("reactions"):
        rows.append([InlineKeyboardButton(
            text=f"😀 Частота реакций: {s.get('reactions_chance', 20)}%",
            callback_data="gs:reactions_chance")])
    if s.get("ai") and db.feature_enabled("ai"):
        label = PERSONAS.get(s.get("ai_persona"), PERSONAS["normal"])[0]
        rows.append([InlineKeyboardButton(text=f"🤖 Характер ИИ: {label}",
                                          callback_data="gs:ai_persona")])
        rows.append([InlineKeyboardButton(
            text=f"⏱ Пауза ИИ: {s.get('ai_cooldown', 5)} сек" if s.get("ai_cooldown", 5)
            else "⏱ Пауза ИИ: без паузы", callback_data="gs:ai_cooldown")])
        rows.append([InlineKeyboardButton(
            text=("✅" if s.get("ai_proactive") else "❌") + " 🤖 ИИ вступает в разговор сам",
            callback_data="gs:ai_proactive")])
        if s.get("ai_proactive"):
            every = int(s.get("ai_passive_every", 0))
            rows.append([InlineKeyboardButton(
                text="🔁 ИИ читает: каждое сообщение" if not every
                else f"🔁 ИИ читает: раз в {every} сек", callback_data="gs:ai_passive_every")])
        rows.append([InlineKeyboardButton(
            text=("✅" if s.get("ai_punish") else "❌") + " 🤖 ИИ наказывает за оскорбления",
            callback_data="gs:ai_punish")])
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


@router.message(Command("aistatus"))
async def cmd_ai_status(message: Message, bot: Bot, db: Database, config: Config):
    if not await message_from_admin(message, bot):
        return await message.reply("⛔ Диагностика доступна только администраторам.")
    settings = await db.get_chat_settings(message.chat.id)
    me = await bot.me()
    can_mute = await is_chat_admin(bot, message.chat.id, me.id, need_restrict=True)
    enabled = lambda value: "✅" if value else "❌"
    text = (f"🤖 Диагностика модерации v{MODERATION_VERSION}\n"
            f"{enabled(db.feature_enabled('ai'))} ИИ включён глобально\n"
            f"{enabled(settings.get('ai'))} ИИ включён в группе\n"
            f"{enabled(settings.get('ai_punish'))} Автонаказания включены\n"
            f"{enabled(config.ai_configured)} Ключ ИИ настроен\n"
            f"{enabled(can_mute)} Есть права для мута\n"
            "Создатели и админы группы освобождены от автонаказаний.\n"
            "Явные прямые оскорбления проверяются правилами; остальные — ИИ.")
    reply = message.reply_to_message
    if reply and reply.from_user:
        exempt = (reply.from_user.id in config.admin_ids
                  or await is_chat_admin(bot, message.chat.id, reply.from_user.id))
        text += f"\nАвтор сообщения в ответе: {'защищён от автонаказаний' if exempt else 'обычный участник'}."
    last = moderation_status.get(message.chat.id)
    if last:
        text += f"\nПоследняя проверка, сообщение {last[0]}: {esc(last[1])}"
    else:
        text += "\nПосле запуска проверок ещё не было."
    await message.reply(text)


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
    if key == "timezone":
        selected = s.get("utc_offset", MOSCOW_OFFSET)
        buttons = [InlineKeyboardButton(
            text=("✓ " if offset == selected else "") + offset_label(offset)
                 + (" МСК" if offset == MOSCOW_OFFSET else ""),
            callback_data=f"gs:offset:{offset}") for offset in UTC_OFFSETS]
        rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
        rows.append([InlineKeyboardButton(text="Назад", callback_data="gs:back")])
        await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return await call.answer()
    if key == "back":
        await call.message.edit_reply_markup(reply_markup=await settings_kb(db, chat_id))
        return await call.answer()
    if key.startswith("offset:"):
        choices = {f"offset:{offset}": offset for offset in UTC_OFFSETS}
        if key not in choices:
            return await call.answer("Неизвестный часовой пояс")
        await db.set_chat_setting(chat_id, "utc_offset", choices[key])
    elif key in GROUP_TOGGLES:
        await db.set_chat_setting(chat_id, key, not s.get(key))
    elif key == "warn_limit":
        await db.set_chat_setting(chat_id, key, s["warn_limit"] + 1 if s["warn_limit"] < 10 else 2)
    elif key == "ai_persona":
        order = list(PERSONAS)
        cur = s.get("ai_persona") if s.get("ai_persona") in PERSONAS else "normal"
        await db.set_chat_setting(chat_id, key, order[(order.index(cur) + 1) % len(order)])
    elif key == "reactions_chance":
        cur = int(s.get("reactions_chance", 20))
        await db.set_chat_setting(chat_id, key, next((c for c in CHANCES if c > cur), CHANCES[0]))
    elif key == "ai_passive_every":
        cur = int(s.get("ai_passive_every", 0))
        await db.set_chat_setting(chat_id, key,
                                  next((c for c in PASSIVE_CHOICES if c > cur), PASSIVE_CHOICES[0]))
    elif key in ("ai_proactive", "ai_punish"):
        await db.set_chat_setting(chat_id, key, not s.get(key))
    elif key == "ai_cooldown":
        cur = int(s.get("ai_cooldown", 5))
        nxt = next((c for c in COOLDOWNS if c > cur), COOLDOWNS[0])
        await db.set_chat_setting(chat_id, key, nxt)
    elif key == "warn_action":
        order = list(WARN_ACTIONS)
        await db.set_chat_setting(chat_id, key, order[(order.index(s["warn_action"]) + 1) % len(order)])
    else:
        return await call.answer()
    await call.message.edit_reply_markup(reply_markup=await settings_kb(db, chat_id))
    await call.answer("Сохранено")
