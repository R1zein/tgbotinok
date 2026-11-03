import itertools
import random
import time

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database
from ..utils.filters import Cmd
from ..utils.text import fmt_num, mention
from .economy import CUR, parse_amount

router = Router(name="duel")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

TTL = 600
_ids = itertools.count(1)
_pending: dict[int, dict] = {}

RPS = {"r": "🪨 Камень", "s": "✂️ Ножницы", "p": "📄 Бумага"}
BEATS = {"r": "s", "s": "p", "p": "r"}


def _cleanup() -> None:
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["ts"] > TTL]:
        _pending.pop(k, None)


def _challenge_kb(kind: str, cid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"{kind}:acc:{cid}"),
        InlineKeyboardButton(text="❌ Отказаться", callback_data=f"{kind}:dec:{cid}"),
    ]])


async def _create(message: Message, db: Database, args: str, kind: str) -> dict | None:
    chat_id, user = message.chat.id, message.from_user
    feature = "economy" if kind == "duel" else "games"
    if not await db.group_feature(chat_id, feature):
        return None
    bet = 0
    if args:
        balance = await db.get_balance(chat_id, user.id)
        bet = parse_amount(args.split()[0], balance) or 0
        if bet and balance < bet:
            await message.reply(f"Недостаточно средств. Баланс: {fmt_num(balance)} {CUR}")
            return None
        if bet and not await db.group_feature(chat_id, "economy"):
            bet = 0
    reply = message.reply_to_message
    p2 = reply.from_user if reply and reply.from_user and not reply.from_user.is_bot else None
    if p2 and p2.id == user.id:
        await message.reply("Нельзя вызвать самого себя 🙂")
        return None
    _cleanup()
    cid = next(_ids)
    data = {
        "id": cid, "kind": kind, "chat_id": chat_id, "ts": time.time(), "bet": bet,
        "p1": user.id, "p1_name": user.full_name,
        "p2": p2.id if p2 else None, "p2_name": p2.full_name if p2 else None,
        "picks": {},
    }
    _pending[cid] = data
    return data


async def _accept(call: CallbackQuery, db: Database, kind: str) -> dict | None:
    _, action, cid = call.data.split(":")
    data = _pending.get(int(cid))
    if not data:
        await call.answer("Вызов устарел", show_alert=True)
        return None
    user = call.from_user
    if action == "dec":
        if user.id not in (data["p1"], data["p2"]) and data["p2"] is not None:
            await call.answer("Это не ваш вызов", show_alert=True)
            return None
        if data["p2"] is None and user.id != data["p1"]:
            await call.answer("Это открытый вызов — просто не принимайте его 🙂", show_alert=True)
            return None
        _pending.pop(data["id"], None)
        await call.message.edit_text(f"{call.message.html_text}\n\n❌ Вызов отклонён.")
        await call.answer()
        return None
    if user.id == data["p1"]:
        await call.answer("Нельзя принять свой же вызов", show_alert=True)
        return None
    if data["p2"] is not None and user.id != data["p2"]:
        await call.answer("Этот вызов адресован не вам", show_alert=True)
        return None
    if data.get("started"):
        await call.answer("Игра уже началась")
        return None
    data["started"] = True  # до первых await — защита от двойного принятия
    bet = data["bet"]
    if bet:
        chat_id = data["chat_id"]
        if not await db.try_spend(chat_id, user.id, bet):
            data["started"] = False
            await call.answer(f"У вас не хватает {CUR} для ставки", show_alert=True)
            return None
        if not await db.try_spend(chat_id, data["p1"], bet):
            await db.add_balance(chat_id, user.id, bet)
            _pending.pop(data["id"], None)
            await call.message.edit_text("❌ У инициатора больше не хватает средств — вызов отменён.")
            await call.answer()
            return None
    data["p2"], data["p2_name"] = user.id, user.full_name
    await call.answer()
    return data


# ---------- дуэль ----------

@router.message(Cmd("дуэль", "duel", "дуель"))
async def cmd_duel(message: Message, db: Database, args: str):
    data = await _create(message, db, args, "duel")
    if not data:
        return
    target = mention(data["p2"], data["p2_name"]) if data["p2"] else "любого желающего"
    stake = f" на <b>{fmt_num(data['bet'])}</b> {CUR}" if data["bet"] else ""
    await message.answer(
        f"⚔️ {mention(data['p1'], data['p1_name'])} вызывает {target} на дуэль{stake}!",
        reply_markup=_challenge_kb("duel", data["id"]),
    )


@router.callback_query(F.data.startswith("duel:"))
async def cb_duel(call: CallbackQuery, db: Database):
    data = await _accept(call, db, "duel")
    if not data:
        return
    _pending.pop(data["id"], None)
    players = [(data["p1"], data["p1_name"]), (data["p2"], data["p2_name"])]
    winner, loser = random.sample(players, 2)
    text = (f"⚔️ Дуэль: {mention(*players[0])} vs {mention(*players[1])}\n\n"
            f"💥 Выстрел! {mention(*winner)} побеждает, {mention(*loser)} повержен(а).")
    if data["bet"]:
        await db.add_balance(data["chat_id"], winner[0], data["bet"] * 2)
        text += f"\n💰 Выигрыш: {fmt_num(data['bet'] * 2)} {CUR}"
    await call.message.edit_text(text)


# ---------- камень-ножницы-бумага ----------

def _rps_kb(cid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f"rps:pick:{cid}:{key}")
        for key, label in RPS.items()
    ]])


@router.message(Cmd("кнб", "rps", "камень"))
async def cmd_rps(message: Message, db: Database, args: str):
    data = await _create(message, db, args, "rps")
    if not data:
        return
    target = mention(data["p2"], data["p2_name"]) if data["p2"] else "любого желающего"
    stake = f" на <b>{fmt_num(data['bet'])}</b> {CUR}" if data["bet"] else ""
    await message.answer(
        f"✊ {mention(data['p1'], data['p1_name'])} вызывает {target} "
        f"в камень-ножницы-бумагу{stake}!",
        reply_markup=_challenge_kb("rps", data["id"]),
    )


@router.callback_query(F.data.regexp(r"^rps:(acc|dec):"))
async def cb_rps_accept(call: CallbackQuery, db: Database):
    data = await _accept(call, db, "rps")
    if not data:
        return
    await call.message.edit_text(
        f"✊ {mention(data['p1'], data['p1_name'])} vs {mention(data['p2'], data['p2_name'])}\n\n"
        "Оба игрока, сделайте выбор (его никто не увидит):",
        reply_markup=_rps_kb(data["id"]),
    )


@router.callback_query(F.data.startswith("rps:pick:"))
async def cb_rps_pick(call: CallbackQuery, db: Database):
    _, _, cid, choice = call.data.split(":")
    data = _pending.get(int(cid))
    if not data:
        return await call.answer("Игра устарела", show_alert=True)
    uid = call.from_user.id
    if uid not in (data["p1"], data["p2"]):
        return await call.answer("Вы не участвуете в этой игре", show_alert=True)
    if uid in data["picks"]:
        return await call.answer("Вы уже сделали выбор")
    data["picks"][uid] = choice
    await call.answer(f"Вы выбрали {RPS[choice]}")
    if len(data["picks"]) < 2:
        return
    _pending.pop(data["id"], None)
    a, b = data["picks"][data["p1"]], data["picks"][data["p2"]]
    p1, p2 = (data["p1"], data["p1_name"]), (data["p2"], data["p2_name"])
    text = f"✊ {mention(*p1)}: {RPS[a]}\n✊ {mention(*p2)}: {RPS[b]}\n\n"
    bet, chat_id = data["bet"], data["chat_id"]
    if a == b:
        text += "🤝 Ничья!"
        if bet:
            await db.add_balance(chat_id, p1[0], bet)
            await db.add_balance(chat_id, p2[0], bet)
    else:
        winner = p1 if BEATS[a] == b else p2
        text += f"🏆 Побеждает {mention(*winner)}!"
        if bet:
            await db.add_balance(chat_id, winner[0], bet * 2)
            text += f"\n💰 Выигрыш: {fmt_num(bet * 2)} {CUR}"
    await call.message.edit_text(text)
