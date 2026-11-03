"""Русские шашки.

Доска — строка из 64 символов, индекс = row*8 + col, row 0 — верх (сторона чёрных).
'.' пусто, 'w'/'b' — шашки, 'W'/'B' — дамки, 'x' — уже побитая в текущей серии шашка
(убирается в конце хода, перепрыгивать её повторно нельзя — «турецкий удар»).
Белые ходят первыми и движутся вверх (row уменьшается).
"""

DIRS = ((-1, -1), (-1, 1), (1, -1), (1, 1))


def initial_board() -> str:
    cells = []
    for r in range(8):
        for c in range(8):
            if (r + c) % 2 == 1 and r < 3:
                cells.append("b")
            elif (r + c) % 2 == 1 and r > 4:
                cells.append("w")
            else:
                cells.append(".")
    return "".join(cells)


def new_state() -> dict:
    return {"board": initial_board(), "turn": "w", "selected": None, "chain": None, "last": None}


def _side(piece: str) -> str | None:
    if piece in "wW":
        return "w"
    if piece in "bB":
        return "b"
    return None


def _inside(r: int, c: int) -> bool:
    return 0 <= r < 8 and 0 <= c < 8


def _captures_from(board: str, sq: int) -> list[tuple[int, int]]:
    """Список (куда, кого_бьём) для фигуры на sq."""
    piece = board[sq]
    side = _side(piece)
    r, c = divmod(sq, 8)
    result = []
    if piece in "wb":
        for dr, dc in DIRS:
            mr, mc, tr, tc = r + dr, c + dc, r + 2 * dr, c + 2 * dc
            if not _inside(tr, tc):
                continue
            mid = board[mr * 8 + mc]
            if _side(mid) not in (None, side) and mid != "x" and board[tr * 8 + tc] == ".":
                result.append((tr * 8 + tc, mr * 8 + mc))
    else:  # дамка
        for dr, dc in DIRS:
            rr, cc = r + dr, c + dc
            while _inside(rr, cc) and board[rr * 8 + cc] == ".":
                rr, cc = rr + dr, cc + dc
            if not _inside(rr, cc):
                continue
            target = board[rr * 8 + cc]
            if target == "x" or _side(target) in (None, side):
                continue
            victim = rr * 8 + cc
            rr, cc = rr + dr, cc + dc
            landings = []
            while _inside(rr, cc) and board[rr * 8 + cc] == ".":
                landings.append(rr * 8 + cc)
                rr, cc = rr + dr, cc + dc
            # Если с части полей можно продолжить бой — обязаны встать на них
            cont = [l for l in landings if _captures_from(_after_jump(board, sq, l, victim), l)]
            for l in cont or landings:
                result.append((l, victim))
    return result


def _after_jump(board: str, frm: int, to: int, victim: int) -> str:
    b = list(board)
    b[to], b[frm], b[victim] = b[frm], ".", "x"
    return "".join(b)


def _simple_moves_from(board: str, sq: int) -> list[int]:
    piece = board[sq]
    r, c = divmod(sq, 8)
    result = []
    if piece in "wb":
        dr = -1 if piece == "w" else 1
        for dc in (-1, 1):
            tr, tc = r + dr, c + dc
            if _inside(tr, tc) and board[tr * 8 + tc] == ".":
                result.append(tr * 8 + tc)
    else:
        for dr, dc in DIRS:
            rr, cc = r + dr, c + dc
            while _inside(rr, cc) and board[rr * 8 + cc] == ".":
                result.append(rr * 8 + cc)
                rr, cc = rr + dr, cc + dc
    return result


def legal_moves(state: dict) -> dict[int, list[tuple[int, int | None]]]:
    """{откуда: [(куда, побитая_или_None), ...]} для стороны, которая ходит."""
    board, turn = state["board"], state["turn"]
    if state.get("chain") is not None:
        sq = state["chain"]
        caps = _captures_from(board, sq)
        return {sq: [(t, v) for t, v in caps]} if caps else {}
    captures: dict[int, list] = {}
    for sq, p in enumerate(board):
        if _side(p) == turn:
            caps = _captures_from(board, sq)
            if caps:
                captures[sq] = [(t, v) for t, v in caps]
    if captures:
        return captures
    moves: dict[int, list] = {}
    for sq, p in enumerate(board):
        if _side(p) == turn:
            ms = _simple_moves_from(board, sq)
            if ms:
                moves[sq] = [(t, None) for t in ms]
    return moves


def apply_move(state: dict, frm: int, to: int) -> tuple[dict, str | None]:
    """Делает ход. Возвращает (новое_состояние, ошибка)."""
    moves = legal_moves(state)
    options = {t: v for t, v in moves.get(frm, [])}
    if to not in options:
        return state, "Так ходить нельзя"
    victim = options[to]
    b = list(state["board"])
    piece = b[frm]
    b[frm], b[to] = ".", piece
    if victim is not None:
        b[victim] = "x"
    row = to // 8
    if piece == "w" and row == 0:
        b[to] = "W"
    elif piece == "b" and row == 7:
        b[to] = "B"
    new = dict(state)
    new["board"] = "".join(b)
    new["selected"] = None
    new["last"] = [frm, to]
    if victim is not None and _captures_from(new["board"], to):
        new["chain"] = to
        new["selected"] = to
        return new, None
    new["board"] = new["board"].replace("x", ".")
    new["chain"] = None
    new["turn"] = "b" if state["turn"] == "w" else "w"
    return new, None


def winner(state: dict) -> str | None:
    """'w'/'b' — победитель, если у ходящей стороны нет ходов."""
    if legal_moves(state):
        return None
    return "b" if state["turn"] == "w" else "w"


SYMBOLS = {"w": "⚪", "W": "🤍", "b": "⚫", "B": "🖤", "x": "💥", ".": "⠀"}


def render_cell(state: dict, sq: int, targets: set[int]) -> str:
    piece = state["board"][sq]
    if sq == state.get("selected"):
        return "🟢"
    if sq in targets:
        return "🟩"
    if piece == "." and (sq // 8 + sq % 8) % 2 == 1:
        return "▪️" if state.get("last") and sq == state["last"][0] else "⠀"
    return SYMBOLS.get(piece, "⠀")
