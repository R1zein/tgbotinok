import asyncio
import random
import re
import time

from aiogram import F, Router
from aiogram.types import Message

from ..db import Database
from ..utils.filters import Cmd
from ..utils.text import fmt_num, mention, user_mention

router = Router(name="economy")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

CUR = "💎"
BONUS_COOLDOWN = 86400
MIN_BET = 10


def parse_amount(token: str, balance: int) -> int | None:
    token = token.strip().lower().replace(" ", "")
    if token in ("все", "всё", "all", "вабанк"):
        return balance
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)(к*|k*)", token)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    value *= 1000 ** len(m.group(2))
    return int(value)


async def enabled(message: Message, db: Database) -> bool:
    return await db.group_feature(message.chat.id, "economy")


async def take_bet(message: Message, db: Database, args: str) -> int | None:
    """Разбирает и списывает ставку. None — ставка не принята (с ответом пользователю)."""
    chat_id, uid = message.chat.id, message.from_user.id
    balance = await db.get_balance(chat_id, uid)
    token = args.split(maxsplit=1)[0] if args else ""
    bet = parse_amount(token, balance) if token else None
    if bet is None:
        await message.reply(f"Укажите ставку, например: <code>{message.text.split()[0]} 100</code>")
        return None
    if bet < MIN_BET:
        await message.reply(f"Минимальная ставка — {MIN_BET} {CUR}")
        return None
    if not await db.try_spend(chat_id, uid, bet):
        await message.reply(f"Недостаточно средств. Баланс: {fmt_num(balance)} {CUR}")
        return None
    return bet


async def settle(message: Message, db: Database, bet: int, mult: float, text: str) -> None:
    win = int(bet * mult)
    balance = await db.add_balance(message.chat.id, message.from_user.id, win) if win else \
        await db.get_balance(message.chat.id, message.from_user.id)
    if mult > 1:
        result = f"✅ Выигрыш: +{fmt_num(win - bet)} {CUR} (x{mult:g})"
    elif mult == 1:
        result = "🔁 Ставка возвращена"
    elif mult > 0:
        result = f"➖ Возврат {fmt_num(win)} {CUR} (x{mult:g})"
    else:
        result = f"❌ Проигрыш: -{fmt_num(bet)} {CUR}"
    await message.reply(f"{text}\n{result}\n💰 Баланс: {fmt_num(balance)} {CUR}")


@router.message(Cmd("баланс", "бал", "б", "balance", "bal"))
async def cmd_balance(message: Message, db: Database):
    if not await enabled(message, db):
        return
    reply = message.reply_to_message
    user = reply.from_user if reply and reply.from_user and not reply.from_user.is_bot else message.from_user
    balance = await db.get_balance(message.chat.id, user.id)
    await message.reply(f"💰 Баланс {user_mention(user)}: <b>{fmt_num(balance)}</b> {CUR}")


@router.message(Cmd("бонус", "bonus", "daily"))
async def cmd_bonus(message: Message, db: Database):
    if not await enabled(message, db):
        return
    chat_id, uid = message.chat.id, message.from_user.id
    last = await db.get_last_bonus(chat_id, uid)
    left = BONUS_COOLDOWN - (int(time.time()) - last)
    if left > 0:
        h, m = divmod(left // 60, 60)
        return await message.reply(f"⏳ Следующий бонус через {h} ч {m} мин.")
    amount = random.randint(200, 1000)
    await db.set_last_bonus(chat_id, uid, int(time.time()))
    balance = await db.add_balance(chat_id, uid, amount)
    await message.reply(f"🎁 Ежедневный бонус: +{fmt_num(amount)} {CUR}\n💰 Баланс: {fmt_num(balance)} {CUR}")


@router.message(Cmd("передать", "перевести", "pay", "перевод"))
async def cmd_pay(message: Message, db: Database, args: str):
    if not await enabled(message, db):
        return
    reply = message.reply_to_message
    if not reply or not reply.from_user or reply.from_user.is_bot:
        return await message.reply("Ответьте на сообщение получателя: <code>передать 100</code>")
    if reply.from_user.id == message.from_user.id:
        return await message.reply("Нельзя переводить самому себе 🙂")
    chat_id = message.chat.id
    balance = await db.get_balance(chat_id, message.from_user.id)
    amount = parse_amount(args.split()[0], balance) if args else None
    if not amount or amount <= 0:
        return await message.reply("Укажите сумму: <code>передать 100</code>")
    if not await db.try_spend(chat_id, message.from_user.id, amount):
        return await message.reply(f"Недостаточно средств. Баланс: {fmt_num(balance)} {CUR}")
    await db.add_balance(chat_id, reply.from_user.id, amount)
    await message.reply(
        f"💸 {user_mention(message.from_user)} → {user_mention(reply.from_user)}: "
        f"{fmt_num(amount)} {CUR}"
    )


@router.message(Cmd("топ", "богачи", "top", "форбс"))
async def cmd_top(message: Message, db: Database):
    if not await enabled(message, db):
        return
    rows = await db.top_balances(message.chat.id)
    if not rows:
        return await message.reply("Пока никто не играл.")
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Самые богатые в чате</b>\n"]
    for i, r in enumerate(rows):
        icon = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{icon} {mention(r['user_id'], r['first_name'])} — {fmt_num(r['balance'])} {CUR}")
    await message.answer("\n".join(lines))


# ---------- казино ----------

CASINO_TABLE = [(0, 45), (0.5, 15), (1, 10), (1.5, 15), (2, 12), (5, 3)]


@router.message(Cmd("казино", "casino", "ставка"))
async def cmd_casino(message: Message, db: Database, args: str):
    if not await enabled(message, db):
        return
    bet = await take_bet(message, db, args)
    if bet is None:
        return
    mults, weights = zip(*CASINO_TABLE)
    mult = random.choices(mults, weights=weights)[0]
    await settle(message, db, bet, mult, f"🎰 Казино: множитель <b>x{mult:g}</b>")


def slot_reels(value: int) -> list[int]:
    v = value - 1
    return [v & 3, (v >> 2) & 3, (v >> 4) & 3]  # 0 bar, 1 виноград, 2 лимон, 3 семёрка


async def dice_game(message: Message, db: Database, args: str, emoji: str, payout) -> None:
    if not await enabled(message, db):
        return
    bet = await take_bet(message, db, args)
    if bet is None:
        return
    dice = await message.reply_dice(emoji=emoji)
    await asyncio.sleep(3.5)
    mult, text = payout(dice.dice.value)
    await settle(message, db, bet, mult, text)


def _slots(v: int):
    reels = slot_reels(v)
    if v == 64:
        return 10, "🎰 <b>ДЖЕКПОТ 777!</b>"
    if reels[0] == reels[1] == reels[2]:
        return 5, "🎰 Три в ряд!"
    if reels.count(3) == 2:
        return 2, "🎰 Две семёрки!"
    return 0, "🎰 Не повезло"


def _darts(v: int):
    return {6: (3, "🎯 В яблочко!"), 5: (1.5, "🎯 Почти в центр"), 4: (1, "🎯 Неплохо")}.get(
        v, (0, "🎯 Мимо"))


def _basket(v: int):
    return (2, "🏀 Попадание!") if v in (4, 5) else (0, "🏀 Промах")


def _football(v: int):
    return (1.5, "⚽ Гол!") if v in (3, 4, 5) else (0, "⚽ Мимо ворот")


def _bowling(v: int):
    return {6: (3, "🎳 Страйк!"), 5: (1.5, "🎳 Почти все кегли")}.get(v, (0, "🎳 Слабовато"))


@router.message(Cmd("слоты", "слот", "slots", "spin"))
async def cmd_slots(message: Message, db: Database, args: str):
    await dice_game(message, db, args, "🎰", _slots)


@router.message(Cmd("дартс", "darts"))
async def cmd_darts(message: Message, db: Database, args: str):
    await dice_game(message, db, args, "🎯", _darts)


@router.message(Cmd("баскет", "баскетбол", "basket"))
async def cmd_basket(message: Message, db: Database, args: str):
    await dice_game(message, db, args, "🏀", _basket)


@router.message(Cmd("футбол", "football"))
async def cmd_football(message: Message, db: Database, args: str):
    await dice_game(message, db, args, "⚽", _football)


@router.message(Cmd("боулинг", "bowling"))
async def cmd_bowling(message: Message, db: Database, args: str):
    await dice_game(message, db, args, "🎳", _bowling)


@router.message(Cmd("кости", "кубик", "dice"))
async def cmd_dice(message: Message, db: Database, args: str):
    if not await enabled(message, db):
        return
    bet = await take_bet(message, db, args)
    if bet is None:
        return
    mine = await message.reply_dice(emoji="🎲")
    theirs = await message.answer_dice(emoji="🎲")
    await asyncio.sleep(3.5)
    a, b = mine.dice.value, theirs.dice.value
    text = f"🎲 Вы: <b>{a}</b> — Бот: <b>{b}</b>"
    await settle(message, db, bet, 2 if a > b else 1 if a == b else 0, text)
