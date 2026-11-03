"""Шахматы, шашки и крестики-нолики на inline-кнопках.

Игры хранятся в таблице games, поэтому переживают перезапуск бота.
Работают в группах (вызов игрока) и в бизнес-чатах (владелец vs собеседник).
"""
import asyncio
import random
from collections import defaultdict

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ..db import Database
from ..games import checkers, chessgame, tictactoe
from ..utils.filters import Cmd
from ..utils.text import mention

router = Router(name="boardgames")

TITLES = {"chess": "♟ Шахматы", "checkers": "⚪ Шашки", "ttt": "❌ Крестики-нолики"}
FEATURE = {"chess": "boardgames", "checkers": "boardgames", "ttt": "games"}
_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def new_state(kind: str) -> dict:
    return {"chess": chessgame.new_state, "checkers": checkers.new_state,
            "ttt": tictactoe.new_state}[kind]()


# ---------- отрисовка ----------

def _side_to_move(game: dict) -> str:
    s = game["state"]
    if game["kind"] == "chess":
        return chessgame.turn_color(s)
    if game["kind"] == "checkers":
        return s["turn"]
    return "w" if s["turn"] == "X" else "b"


def _player_for(game: dict, side: str) -> tuple[int, str]:
    return (game["p1"], game["p1_name"]) if side == "w" else (game["p2"], game["p2_name"])


def render_text(game: dict) -> str:
    kind, s = game["kind"], game["state"]
    icons = {"chess": ("♔ Белые", "♚ Чёрные"), "checkers": ("⚪ Белые", "⚫ Чёрные"),
             "ttt": ("❌", "⭕")}[kind]
    lines = [f"<b>{TITLES[kind]}</b>",
             f"{icons[0]}: {mention(game['p1'], game['p1_name'])}",
             f"{icons[1]}: {mention(game['p2'], game['p2_name'])}", ""]
    if game["status"] == "finished":
        lines.append(s.get("result", "🏁 Игра окончена"))
        return "\n".join(lines)
    uid, name = _player_for(game, _side_to_move(game))
    lines.append(f"👉 Ход: {mention(uid, name)}")
    if kind == "chess":
        if s.get("last_san"):
            lines.append(f"Последний ход: <code>{s['last_san']}</code>")
        if chessgame.is_check(s):
            lines.append("⚠️ <b>Шах!</b>")
    if kind == "checkers" and s.get("chain") is not None:
        lines.append("💥 Продолжайте бой той же шашкой!")
    if s.get("draw_offer"):
        oid, oname = _player_for(game, s["draw_offer"])
        lines.append(f"🤝 {mention(oid, oname)} предлагает ничью")
    return "\n".join(lines)


def render_kb(game: dict) -> InlineKeyboardMarkup:
    gid, kind, s = game["id"], game["kind"], game["state"]
    rows = []
    if kind == "ttt":
        for r in range(3):
            rows.append([InlineKeyboardButton(text=tictactoe.SYMBOLS[s["board"][r * 3 + c]],
                                              callback_data=f"gm:{gid}:c:{r * 3 + c}")
                         for c in range(3)])
    else:
        if kind == "chess":
            tg = chessgame.targets(s)
            cell = lambda sq: chessgame.render_cell(s, sq, tg)  # noqa: E731
        else:
            moves = checkers.legal_moves(s)
            sel = s.get("selected")
            tg = {t for t, _ in moves.get(sel, [])} if sel is not None else set()
            cell = lambda sq: checkers.render_cell(s, sq, tg)  # noqa: E731
        for r in range(8):
            rows.append([InlineKeyboardButton(text=cell(r * 8 + c), callback_data=f"gm:{gid}:c:{r * 8 + c}")
                         for c in range(8)])
    if game["status"] == "active":
        extra = [InlineKeyboardButton(text="🏳 Сдаться", callback_data=f"gm:{gid}:resign")]
        if kind != "ttt":
            extra.insert(0, InlineKeyboardButton(text="🤝 Ничья", callback_data=f"gm:{gid}:draw"))
        rows.append(extra)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def refresh(bot: Bot, game: dict) -> None:
    try:
        await bot.edit_message_text(
            text=render_text(game), chat_id=game["chat_id"], message_id=game["message_id"],
            business_connection_id=game["bc_id"], reply_markup=render_kb(game),
        )
    except TelegramBadRequest as e:
        if "not modified" not in e.message:
            raise


# ---------- запуск ----------

async def start_business_game(bot: Bot, db: Database, kind: str, bc_id: str, chat_id: int,
                              owner: tuple[int, str], other: tuple[int, str]) -> None:
    players = [owner, other]
    random.shuffle(players)
    gid = await db.create_game(kind, chat_id, bc_id, players[0][0], players[0][1],
                               players[1][0], players[1][1], new_state(kind), "active")
    game = await db.get_game(gid)
    msg = await bot.send_message(chat_id, render_text(game), reply_markup=render_kb(game),
                                 business_connection_id=bc_id)
    await db.update_game(gid, message_id=msg.message_id)


async def start_group_challenge(message: Message, db: Database, kind: str) -> None:
    if not await db.group_feature(message.chat.id, FEATURE[kind]):
        return
    user = message.from_user
    reply = message.reply_to_message
    p2 = reply.from_user if reply and reply.from_user and not reply.from_user.is_bot else None
    if p2 and p2.id == user.id:
        await message.reply("Нельзя играть с самим собой 🙂")
        return
    gid = await db.create_game(kind, message.chat.id, None, user.id, user.full_name,
                               p2.id if p2 else None, p2.full_name if p2 else None,
                               new_state(kind), "pending")
    target = mention(p2.id, p2.full_name) if p2 else "любого желающего"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"gm:{gid}:acc"),
        InlineKeyboardButton(text="❌ Отказаться", callback_data=f"gm:{gid}:dec"),
    ]])
    msg = await message.answer(
        f"{TITLES[kind]}\n{mention(user.id, user.full_name)} вызывает {target} на партию!",
        reply_markup=kb,
    )
    await db.update_game(gid, message_id=msg.message_id)


@router.message(F.chat.type.in_({"group", "supergroup"}), Cmd("шахматы", "chess"))
async def cmd_chess(message: Message, db: Database):
    await start_group_challenge(message, db, "chess")


@router.message(F.chat.type.in_({"group", "supergroup"}), Cmd("шашки", "checkers", "draughts"))
async def cmd_checkers(message: Message, db: Database):
    await start_group_challenge(message, db, "checkers")


@router.message(F.chat.type.in_({"group", "supergroup"}),
                Cmd("крестики", "ттт", "ttt", "xo", "крестики-нолики", "кн"))
async def cmd_ttt(message: Message, db: Database):
    await start_group_challenge(message, db, "ttt")


# ---------- нажатия ----------

def _finish(game: dict, text: str) -> None:
    game["status"] = "finished"
    game["state"]["result"] = text
    game["state"]["selected"] = None


@router.callback_query(F.data.startswith("gm:"))
async def cb_game(call: CallbackQuery, bot: Bot, db: Database):
    parts = call.data.split(":")
    gid, action = int(parts[1]), parts[2]
    async with _locks[gid]:
        game = await db.get_game(gid)
        if not game:
            return await call.answer("Игра не найдена", show_alert=True)
        uid = call.from_user.id
        if game["status"] == "pending":
            return await _handle_pending(call, bot, db, game, action)
        if game["status"] == "finished":
            return await call.answer("Игра окончена")
        if uid not in (game["p1"], game["p2"]):
            return await call.answer("Вы не участвуете в этой игре", show_alert=True)
        my_side = "w" if uid == game["p1"] else "b"
        s = game["state"]
        note = None

        if action == "resign":
            winner = _player_for(game, "b" if my_side == "w" else "w")
            _finish(game, f"🏳 {mention(uid, call.from_user.full_name)} сдался(-ась). "
                          f"🏆 Победа: {mention(*winner)}")
        elif action == "draw":
            if s.get("draw_offer") and s["draw_offer"] != my_side:
                _finish(game, "🤝 Ничья по соглашению сторон")
            else:
                s["draw_offer"] = my_side
                note = "Вы предложили ничью"
        elif action == "c":
            if _side_to_move(game) != my_side:
                return await call.answer("Сейчас не ваш ход", show_alert=False)
            err = _click(game, int(parts[3]))
            if err:
                return await call.answer(err)
        else:
            return await call.answer()

        await db.update_game(gid, state=game["state"], status=game["status"])
        await refresh(bot, game)
        await call.answer(note)


def _click(game: dict, sq: int) -> str | None:
    kind, s = game["kind"], game["state"]
    if kind == "ttt":
        new, err = tictactoe.move(s, sq)
        if err:
            return err
        game["state"] = new
        w = tictactoe.winner(new)
        if w == "draw":
            _finish(game, "🤝 Ничья!")
        elif w:
            _finish(game, f"🏆 Победа: {mention(*_player_for(game, 'w' if w == 'X' else 'b'))}")
        return None

    if kind == "chess":
        new, err, moved = chessgame.click(s, sq)
        if err:
            return err
        game["state"] = new
        if moved:
            res, reason = chessgame.outcome(new)
            if res == "draw":
                _finish(game, f"🤝 Ничья ({reason})")
            elif res:
                _finish(game, f"🏆 Победа: {mention(*_player_for(game, res))} ({reason})")
        return None

    # шашки
    moves = checkers.legal_moves(s)
    sel = s.get("selected")
    piece_side = checkers._side(s["board"][sq])
    if s.get("chain") is None and piece_side == s["turn"]:
        if sq not in moves:
            return "Этой шашкой нельзя ходить" + (" — есть обязательное взятие" if moves and
                                                  any(v is not None for m in moves.values()
                                                      for _, v in m) else "")
        s["selected"] = None if sel == sq else sq
        return None
    if sel is None:
        return "Выберите свою шашку"
    new, err = checkers.apply_move(s, sel, sq)
    if err:
        return err
    game["state"] = new
    w = checkers.winner(new)
    if w and new.get("chain") is None:
        _finish(game, f"🏆 Победа: {mention(*_player_for(game, w))}")
    return None


async def _handle_pending(call: CallbackQuery, bot: Bot, db: Database, game: dict, action: str):
    uid = call.from_user.id
    if action == "dec":
        if uid not in (game["p1"], game["p2"]):
            return await call.answer("Это не ваш вызов", show_alert=True)
        await db.update_game(game["id"], status="finished")
        await call.message.edit_text(f"{call.message.html_text}\n\n❌ Вызов отклонён.")
        return await call.answer()
    if action != "acc":
        return await call.answer()
    if uid == game["p1"]:
        return await call.answer("Нельзя принять свой же вызов", show_alert=True)
    if game["p2"] is not None and uid != game["p2"]:
        return await call.answer("Этот вызов адресован не вам", show_alert=True)
    players = [(game["p1"], game["p1_name"]), (uid, call.from_user.full_name)]
    random.shuffle(players)
    game.update(p1=players[0][0], p1_name=players[0][1], p2=players[1][0], p2_name=players[1][1],
                status="active", message_id=call.message.message_id)
    await db.update_game(game["id"], p1=game["p1"], p1_name=game["p1_name"], p2=game["p2"],
                         p2_name=game["p2_name"], status="active", message_id=game["message_id"])
    await refresh(bot, game)
    await call.answer("Игра началась!")
