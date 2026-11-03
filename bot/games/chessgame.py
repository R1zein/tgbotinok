"""Шахматы поверх python-chess. Клетка кнопки sq: 0..63, row 0 — 8-я горизонталь."""
import chess

GLYPHS = {
    "K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
    "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟",
}


def new_state() -> dict:
    return {"fen": chess.STARTING_FEN, "selected": None, "last": None, "draw_offer": None}


def to_square(sq: int) -> int:
    row, col = divmod(sq, 8)
    return chess.square(col, 7 - row)


def from_square(square: int) -> int:
    return (7 - chess.square_rank(square)) * 8 + chess.square_file(square)


def board_of(state: dict) -> chess.Board:
    return chess.Board(state["fen"])


def turn_color(state: dict) -> str:
    return "w" if board_of(state).turn == chess.WHITE else "b"


def targets(state: dict) -> set[int]:
    if state.get("selected") is None:
        return set()
    board = board_of(state)
    frm = to_square(state["selected"])
    return {from_square(m.to_square) for m in board.legal_moves if m.from_square == frm}


def click(state: dict, sq: int) -> tuple[dict, str | None, bool]:
    """Обработка нажатия. Возвращает (state, ошибка, был_ли_сделан_ход)."""
    board = board_of(state)
    square = to_square(sq)
    piece = board.piece_at(square)
    new = dict(state)
    selected = state.get("selected")

    def has_moves(s: int) -> bool:
        return any(m.from_square == s for m in board.legal_moves)

    if selected is None or (piece and piece.color == board.turn and sq != selected):
        if piece and piece.color == board.turn:
            if not has_moves(square):
                return state, "У этой фигуры нет ходов", False
            new["selected"] = sq
            return new, None, False
        return state, "Выберите свою фигуру", False
    if sq == selected:
        new["selected"] = None
        return new, None, False
    frm = to_square(selected)
    move = None
    for m in board.legal_moves:
        if m.from_square == frm and m.to_square == square:
            if m.promotion and m.promotion != chess.QUEEN:
                continue  # авто-превращение в ферзя
            move = m
            break
    if move is None:
        return state, "Так ходить нельзя", False
    san = board.san(move)
    board.push(move)
    new.update(fen=board.fen(), selected=None, last=[selected, sq], last_san=san, draw_offer=None)
    return new, None, True


def outcome(state: dict) -> tuple[str | None, str | None]:
    """(победитель 'w'/'b'/'draw' или None, описание)."""
    board = board_of(state)
    res = board.outcome(claim_draw=True)
    if res is None:
        return None, None
    reasons = {
        chess.Termination.CHECKMATE: "мат",
        chess.Termination.STALEMATE: "пат",
        chess.Termination.INSUFFICIENT_MATERIAL: "недостаточно материала",
        chess.Termination.SEVENTYFIVE_MOVES: "правило 75 ходов",
        chess.Termination.FIVEFOLD_REPETITION: "пятикратное повторение",
        chess.Termination.FIFTY_MOVES: "правило 50 ходов",
        chess.Termination.THREEFOLD_REPETITION: "троекратное повторение",
    }
    reason = reasons.get(res.termination, "конец партии")
    if res.winner is None:
        return "draw", reason
    return ("w" if res.winner == chess.WHITE else "b"), reason


def is_check(state: dict) -> bool:
    return board_of(state).is_check()


def render_cell(state: dict, sq: int, tgts: set[int]) -> str:
    board = board_of(state)
    piece = board.piece_at(to_square(sq))
    glyph = GLYPHS[piece.symbol()] if piece else None
    if sq == state.get("selected"):
        return f"[{glyph}]"
    if sq in tgts:
        return f"×{glyph}" if glyph else "•"
    if glyph:
        return glyph
    last = state.get("last")
    if last and sq == last[0]:
        return "·"
    return "⠀"
