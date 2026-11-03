import time

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database
from ..utils.filters import Cmd
from ..utils.text import esc, mention, user_mention

router = Router(name="rp")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

# команда -> (эмодзи, действие в прошедшем времени)
RP_ACTIONS: dict[str, tuple[str, str]] = {
    "обнять": ("🤗", "обнял(а)"),
    "поцеловать": ("😘", "поцеловал(а)"),
    "чмок": ("😚", "чмокнул(а)"),
    "ударить": ("👊", "ударил(а)"),
    "погладить": ("🫳", "погладил(а)"),
    "укусить": ("😬", "укусил(а)"),
    "пнуть": ("🦶", "пнул(а)"),
    "шлёпнуть": ("🖐", "шлёпнул(а)"),
    "шлепнуть": ("🖐", "шлёпнул(а)"),
    "дать пять": ("🙏", "дал(а) пять"),
    "пожать руку": ("🤝", "пожал(а) руку"),
    "убить": ("🔪", "убил(а)"),
    "покормить": ("🍰", "покормил(а)"),
    "пожалеть": ("🥺", "пожалел(а)"),
    "прижать": ("🫂", "прижал(а) к себе"),
    "лизнуть": ("👅", "лизнул(а)"),
    "понюхать": ("👃", "понюхал(а)"),
    "похвалить": ("👏", "похвалил(а)"),
    "поздравить": ("🎉", "поздравил(а)"),
    "извиниться": ("🙇", "извинился(-ась) перед"),
    "испугать": ("👻", "испугал(а)"),
    "ущипнуть": ("🤏", "ущипнул(а)"),
    "уничтожить": ("💣", "уничтожил(а)"),
    "отравить": ("🧪", "отравил(а)"),
    "связать": ("🪢", "связал(а)"),
    "пощекотать": ("🤭", "пощекотал(а)"),
    "подарить цветы": ("💐", "подарил(а) цветы"),
    "угостить": ("☕", "угостил(а)"),
    "кусь": ("😼", "сделал(а) кусь"),
    "тык": ("👉", "тыкнул(а)"),
}
_MAX_WORDS = max(len(k.split()) for k in RP_ACTIONS)
_proposals: dict[str, float] = {}


def _match_rp(text: str) -> tuple[str, str] | None:
    words = text.strip().lstrip("/!.").split()
    for n in range(min(_MAX_WORDS, len(words)), 0, -1):
        key = " ".join(words[:n]).lower()
        if key in RP_ACTIONS:
            return key, " ".join(words[n:])
    return None


@router.message(Cmd("рп", "rp"))
async def cmd_rp_list(message: Message, db: Database):
    if not await db.group_feature(message.chat.id, "rp"):
        return
    await message.reply("🎭 <b>RP-команды</b> (ответом на сообщение):\n" +
                        ", ".join(f"<code>{k}</code>" for k in RP_ACTIONS) +
                        "\n\nМожно добавить текст: <code>обнять крепко-крепко</code>")


@router.message(F.reply_to_message, F.text, lambda m: _match_rp(m.text) is not None)
async def rp_action(message: Message, db: Database):
    if not await db.group_feature(message.chat.id, "rp"):
        return
    target = message.reply_to_message.from_user
    if not target or not message.from_user:
        return
    key, extra = _match_rp(message.text)
    emoji, verb = RP_ACTIONS[key]
    text = f"{emoji} {user_mention(message.from_user)} {verb} {user_mention(target)}"
    if extra:
        text += f"\n💬 {esc(extra)}"
    await message.answer(text)


# ---------- браки ----------

@router.message(Cmd("брак", "marry", "пожениться"))
async def cmd_marry(message: Message, db: Database):
    chat_id = message.chat.id
    if not await db.group_feature(chat_id, "rp"):
        return
    reply = message.reply_to_message
    if not reply or not reply.from_user or reply.from_user.is_bot:
        return await message.reply("💍 Ответьте на сообщение того, кому делаете предложение.")
    a, b = message.from_user, reply.from_user
    if a.id == b.id:
        return await message.reply("Нельзя жениться на себе 🙂")
    if await db.get_marriage(chat_id, a.id):
        return await message.reply("Вы уже состоите в браке. Сначала <code>развод</code>.")
    if await db.get_marriage(chat_id, b.id):
        return await message.reply(f"{user_mention(b)} уже состоит в браке.")
    _proposals[f"{chat_id}:{a.id}:{b.id}"] = time.time()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💍 Да!", callback_data=f"marry:yes:{a.id}:{b.id}"),
        InlineKeyboardButton(text="💔 Нет", callback_data=f"marry:no:{a.id}:{b.id}"),
    ]])
    await message.answer(f"💍 {user_mention(a)} делает предложение {user_mention(b)}!\n"
                         f"{user_mention(b)}, вы согласны?", reply_markup=kb)


@router.callback_query(F.data.startswith("marry:"))
async def cb_marry(call: CallbackQuery, db: Database):
    _, answer, a_id, b_id = call.data.split(":")
    a_id, b_id = int(a_id), int(b_id)
    chat_id = call.message.chat.id
    if call.from_user.id != b_id:
        return await call.answer("Предложение адресовано не вам", show_alert=True)
    if _proposals.pop(f"{chat_id}:{a_id}:{b_id}", None) is None:
        return await call.answer("Предложение устарело", show_alert=True)
    if answer == "no":
        await call.message.edit_text(f"{call.message.html_text}\n\n💔 Отказ...")
        return await call.answer()
    if await db.get_marriage(chat_id, a_id) or await db.get_marriage(chat_id, b_id):
        await call.message.edit_text("Кто-то из вас уже успел вступить в брак 🤷")
        return await call.answer()
    await db.add_marriage(chat_id, a_id, b_id)
    a = await db.get_member(chat_id, a_id)
    await call.message.edit_text(
        f"💒 Поздравляем! {mention(a_id, a['first_name'] if a else None)} и "
        f"{user_mention(call.from_user)} теперь в браке! 🎉"
    )
    await call.answer()


@router.message(Cmd("развод", "divorce"))
async def cmd_divorce(message: Message, db: Database):
    if not await db.group_feature(message.chat.id, "rp"):
        return
    if not await db.get_marriage(message.chat.id, message.from_user.id):
        return await message.reply("Вы не состоите в браке.")
    await db.remove_marriage(message.chat.id, message.from_user.id)
    await message.reply("💔 Брак расторгнут.")


async def _name(db: Database, chat_id: int, uid: int) -> str:
    m = await db.get_member(chat_id, uid)
    return mention(uid, m["first_name"] if m else None)


def _days(since: int) -> int:
    return max(0, (int(time.time()) - since) // 86400)


@router.message(Cmd("мой", "my"), F.text.lower().contains("брак"))
async def cmd_my_marriage(message: Message, db: Database):
    if not await db.group_feature(message.chat.id, "rp"):
        return
    m = await db.get_marriage(message.chat.id, message.from_user.id)
    if not m:
        return await message.reply("Вы не состоите в браке.")
    other = m["user2"] if m["user1"] == message.from_user.id else m["user1"]
    await message.reply(f"💞 Вы в браке с {await _name(db, message.chat.id, other)} "
                        f"уже {_days(m['since'])} дн.")


@router.message(Cmd("браки", "marriages"))
async def cmd_marriages(message: Message, db: Database):
    if not await db.group_feature(message.chat.id, "rp"):
        return
    rows = await db.list_marriages(message.chat.id)
    if not rows:
        return await message.reply("В этом чате пока нет браков 💔")
    lines = ["💞 <b>Браки чата</b>\n"]
    for i, m in enumerate(rows, 1):
        lines.append(f"{i}. {await _name(db, message.chat.id, m['user1'])} + "
                     f"{await _name(db, message.chat.id, m['user2'])} — {_days(m['since'])} дн.")
    await message.answer("\n".join(lines))
