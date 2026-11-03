import random
import re

from aiogram import F, Router
from aiogram.types import Message

from ..db import Database
from ..utils.filters import Cmd
from ..utils.text import esc, fmt_num, mention, user_mention
from .economy import CUR

router = Router(name="fun")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

BALL = [
    "Бесспорно ✅", "Предрешено 🔮", "Никаких сомнений", "Определённо да", "Можешь быть уверен",
    "Мне кажется — да", "Вероятнее всего", "Хорошие перспективы", "Знаки говорят — да",
    "Пока не ясно, попробуй снова 🌫", "Спроси позже", "Лучше не рассказывать",
    "Сейчас нельзя предсказать", "Сконцентрируйся и спроси опять",
    "Даже не думай ❌", "Мой ответ — нет", "По моим данным — нет", "Перспективы не очень",
    "Весьма сомнительно",
]
GUESS_REWARD = 300
_guess: dict[int, dict] = {}


async def on(message: Message, db: Database) -> bool:
    return await db.group_feature(message.chat.id, "games")


@router.message(Cmd("монетка", "coin", "монета", "флип"))
async def cmd_coin(message: Message, db: Database):
    if await on(message, db):
        await message.reply(random.choice(["🪙 Орёл!", "🪙 Решка!"]))


@router.message(Cmd("шар", "8ball"))
async def cmd_ball(message: Message, db: Database, args: str):
    if not await on(message, db):
        return
    if not args:
        return await message.reply("Задайте вопрос: <code>шар я сдам экзамен?</code>")
    await message.reply(f"🎱 {random.choice(BALL)}")


@router.message(Cmd("инфа", "вероятность", "шанс"))
async def cmd_info(message: Message, db: Database, args: str):
    if not await on(message, db):
        return
    if not args:
        return await message.reply("Напишите событие: <code>инфа что завтра будет снег</code>")
    await message.reply(f"🔮 Вероятность того, что {esc(args)} — <b>{random.randint(0, 100)}%</b>")


@router.message(Cmd("выбери", "choose"))
async def cmd_choose(message: Message, db: Database, args: str):
    if not await on(message, db):
        return
    options = [o.strip() for o in re.split(r"\s+или\s+|\s+or\s+|,", args) if o.strip()]
    if len(options) < 2:
        return await message.reply("Пример: <code>выбери пицца или суши</code>")
    await message.reply(f"🤔 Я выбираю: <b>{esc(random.choice(options))}</b>")


@router.message(Cmd("кто", "who"))
async def cmd_who(message: Message, db: Database, args: str):
    if not await on(message, db):
        return
    raw = (message.text or "").strip().lower()
    # «кто ...» — частое начало обычной фразы, без префикса реагируем только на «кто сегодня»
    if raw[:1] not in "/!." and not raw.startswith("кто сегодня"):
        return
    members = await db.list_members(message.chat.id)
    if not members:
        return
    m = random.choice(members)
    text = args or "избранный"
    if text.lower().startswith("сегодня"):
        text = text[7:].strip() or "герой дня"
        return await message.answer(f"🌟 Сегодня {esc(text)} — {mention(m['user_id'], m['first_name'])}!")
    await message.answer(f"🤔 Я думаю, что {esc(text)} — {mention(m['user_id'], m['first_name'])}")


# ---------- угадай число ----------

@router.message(Cmd("угадай", "guess", "число"))
async def cmd_guess(message: Message, db: Database):
    if not await on(message, db):
        return
    chat_id = message.chat.id
    if chat_id in _guess:
        return await message.reply("Игра уже идёт! Пишите числа от 1 до 100.")
    _guess[chat_id] = {"number": random.randint(1, 100), "tries": 0}
    await message.answer("🔢 Я загадал число от 1 до 100. Пишите ваши варианты в чат!\n"
                         "Остановить: <code>стоп угадай</code>")


@router.message(Cmd("стоп"), F.text.lower().contains("угад"))
async def cmd_guess_stop(message: Message):
    game = _guess.pop(message.chat.id, None)
    if game:
        await message.answer(f"⏹ Игра остановлена. Было загадано число <b>{game['number']}</b>.")


@router.message(F.text.regexp(r"^\d{1,3}$"), lambda m: m.chat.id in _guess)
async def guess_try(message: Message, db: Database):
    game = _guess.get(message.chat.id)
    if not game:
        return
    n = int(message.text)
    game["tries"] += 1
    if n < game["number"]:
        return await message.reply("⬆️ Больше")
    if n > game["number"]:
        return await message.reply("⬇️ Меньше")
    _guess.pop(message.chat.id, None)
    text = (f"🎉 {user_mention(message.from_user)} угадал(а) число <b>{n}</b> "
            f"за {game['tries']} попыток всего чата!")
    if await db.group_feature(message.chat.id, "economy"):
        await db.add_balance(message.chat.id, message.from_user.id, GUESS_REWARD)
        text += f"\n💰 Награда: {fmt_num(GUESS_REWARD)} {CUR}"
    await message.answer(text)
