LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
SYMBOLS = {"X": "❌", "O": "⭕", ".": "⠀"}


def new_state() -> dict:
    return {"board": "." * 9, "turn": "X"}


def move(state: dict, cell: int) -> tuple[dict, str | None]:
    if state["board"][cell] != ".":
        return state, "Клетка занята"
    b = list(state["board"])
    b[cell] = state["turn"]
    return {"board": "".join(b), "turn": "O" if state["turn"] == "X" else "X"}, None


def winner(state: dict) -> str | None:
    """'X'/'O' — победитель, 'draw' — ничья, None — игра продолжается."""
    b = state["board"]
    for a, c, d in LINES:
        if b[a] != "." and b[a] == b[c] == b[d]:
            return b[a]
    return "draw" if "." not in b else None
