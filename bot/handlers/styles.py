"""Меню выбора стиля и формата своих сообщений (ассистент чатов)."""
import random

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from ..db import Database
from ..services.textstyles import FORMATS, STYLES, render

router = Router(name="styles")
SAMPLE = "Привет! Как дела? Что делаешь сейчас?"


async def styles_menu(db: Database, user_id: int):
    s = await db.get_user_settings(user_id)
    style, fmt = s.get("msg_style", "none"), s.get("msg_format", "none")
    random.seed(user_id)  # стабильный пример, чтобы превью не «прыгало» при каждом нажатии
    preview = render(SAMPLE, SAMPLE, style, fmt) or SAMPLE
    random.seed()
    text = (
        "✨ <b>Стиль и формат моих сообщений</b>\n\n"
        "Когда вы пишете в личке, бот сразу переписывает ваше сообщение выбранным стилем "
        "и/или оформляет его (жирный, курсив…). Собеседник видит уже изменённый текст.\n"
        "В чате можно переключать командами: <code>.пикми</code>, <code>.стиль пират</code>, "
        "<code>.жирный</code>, <code>.обычный</code>.\n\n"
        f"<b>Пример:</b>\n{preview}"
    )
    rows = [[InlineKeyboardButton(text=("✅ " if style == "none" else "") + "🚫 Без стиля",
                                  callback_data="sty:s:none")]]
    row = []
    for key, (label, _) in STYLES.items():
        row.append(InlineKeyboardButton(text=("✅ " if style == key else "") + label,
                                        callback_data=f"sty:s:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text=("✅ " if fmt == "none" else "") + "🔤 Без формата",
                                      callback_data="sty:f:none")])
    row = []
    for key, (label, _, _) in FORMATS.items():
        row.append(InlineKeyboardButton(text=("✅ " if fmt == key else "") + label,
                                        callback_data=f"sty:f:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:biz")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "sty:menu")
async def cb_styles(call: CallbackQuery, db: Database):
    text, kb = await styles_menu(db, call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.regexp(r"^sty:[sf]:\w+$"))
async def cb_style_set(call: CallbackQuery, db: Database):
    _, kind, key = call.data.split(":")
    if kind == "s" and (key in STYLES or key == "none"):
        await db.set_user_setting(call.from_user.id, "msg_style", key)
    elif kind == "f" and (key in FORMATS or key == "none"):
        await db.set_user_setting(call.from_user.id, "msg_format", key)
    else:
        return await call.answer()
    text, kb = await styles_menu(db, call.from_user.id)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Сохранено")
