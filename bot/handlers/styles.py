"""Меню «Формат текста» (как в TonzBot): оформление всех своих сообщений в ассистенте чатов.

Режимы текста (пикми, пират и т.д.) включаются командой прямо в чате — см. .команды.
"""
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from ..db import Database
from ..services.textstyles import FORMATS, STYLES, render

router = Router(name="styles")
SAMPLE = "Привет! Как дела?"


async def format_menu(db: Database, user_id: int):
    s = await db.get_user_settings(user_id)
    style, fmt = s.get("msg_style", "none"), s.get("msg_format", "none")
    preview = render(SAMPLE, SAMPLE, "none", fmt) or SAMPLE
    mode = STYLES[style][0] if style in STYLES else "выключен"
    text = (
        "🔤 <b>Формат текста</b>\n\n"
        "Все ваши сообщения в личных чатах будут автоматически оформляться выбранным форматом.\n\n"
        f"<b>Пример:</b> {preview}\n\n"
        f"✨ Режим текста: <b>{mode}</b>\n"
        "Режимы (пикми, пират, гопник…) включаются командой прямо в чате, например "
        "<code>.пикми</code>. Список всех команд — <code>.команды</code>."
    )
    rows = [[InlineKeyboardButton(text=("✅ " if fmt == "none" else "") + "Обычный текст",
                                  callback_data="sty:f:none")]]
    row = []
    for key, (label, _, _) in FORMATS.items():
        row.append(InlineKeyboardButton(text=("✅ " if fmt == key else "") + label,
                                        callback_data=f"sty:f:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if style in STYLES:
        rows.append([InlineKeyboardButton(text=f"✖️ Выключить режим «{STYLES[style][0]}»",
                                          callback_data="sty:s:none")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:biz")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "sty:menu")
async def cb_format_menu(call: CallbackQuery, db: Database):
    text, kb = await format_menu(db, call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.regexp(r"^sty:[sf]:\w+$"))
async def cb_format_set(call: CallbackQuery, db: Database):
    _, kind, key = call.data.split(":")
    if kind == "f" and (key in FORMATS or key == "none"):
        await db.set_user_setting(call.from_user.id, "msg_format", key)
    elif kind == "s" and key == "none":
        await db.set_user_setting(call.from_user.id, "msg_style", "none")
    else:
        return await call.answer()
    text, kb = await format_menu(db, call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Сохранено")
