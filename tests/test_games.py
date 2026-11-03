from bot.games import checkers, chessgame, tictactoe


def sq(r, c):
    return r * 8 + c


def board_with(pieces: dict[int, str]) -> str:
    b = ["."] * 64
    for k, v in pieces.items():
        b[k] = v
    return "".join(b)


def test_checkers_initial_moves():
    s = checkers.new_state()
    moves = checkers.legal_moves(s)
    assert len(moves) == 4  # 4 белые шашки 6-го ряда (row 5) могут ходить
    assert sum(len(v) for v in moves.values()) == 7


def test_checkers_mandatory_capture_and_chain():
    # белая на (5,0), чёрные на (4,1) и (2,3) — двойное взятие
    s = {"board": board_with({sq(5, 0): "w", sq(4, 1): "b", sq(2, 3): "b", sq(0, 7): "b"}),
         "turn": "w", "selected": None, "chain": None, "last": None}
    moves = checkers.legal_moves(s)
    assert moves == {sq(5, 0): [(sq(3, 2), sq(4, 1))]}
    s, err = checkers.apply_move(s, sq(5, 0), sq(3, 2))
    assert err is None and s["chain"] == sq(3, 2) and s["turn"] == "w"
    s, err = checkers.apply_move(s, sq(3, 2), sq(1, 4))
    assert err is None and s["turn"] == "b" and s["chain"] is None
    assert s["board"].count("b") == 1 and "x" not in s["board"]


def test_checkers_promotion_mid_capture_continues_as_king():
    # белая (2,1) бьёт (1,2) -> встаёт на (0,3) дамкой и продолжает бить (2,5) по диагонали
    s = {"board": board_with({sq(2, 1): "w", sq(1, 2): "b", sq(2, 5): "b", sq(7, 0): "b"}),
         "turn": "w", "selected": None, "chain": None, "last": None}
    s, _ = checkers.apply_move(s, sq(2, 1), sq(0, 3))
    assert s["board"][sq(0, 3)] == "W"
    assert s["chain"] == sq(0, 3)
    targets = [t for t, _ in checkers.legal_moves(s)[sq(0, 3)]]
    assert sq(3, 6) in targets


def test_checkers_king_flying_move():
    s = {"board": board_with({sq(7, 0): "W", sq(0, 1): "b"}), "turn": "w",
         "selected": None, "chain": None, "last": None}
    targets = {t for t, _ in checkers.legal_moves(s)[sq(7, 0)]}
    assert targets == {sq(6, 1), sq(5, 2), sq(4, 3), sq(3, 4), sq(2, 5), sq(1, 6), sq(0, 7)}


def test_checkers_winner_when_no_moves():
    s = {"board": board_with({sq(4, 3): "w"}), "turn": "b", "selected": None, "chain": None,
         "last": None}
    assert checkers.winner(s) == "w"


def test_chess_fools_mate():
    s = chessgame.new_state()

    def mv(a, b):
        nonlocal s
        fa = (8 - int(a[1])) * 8 + "abcdefgh".index(a[0])
        fb = (8 - int(b[1])) * 8 + "abcdefgh".index(b[0])
        s, err, _ = chessgame.click(s, fa)
        assert err is None, err
        s, err, moved = chessgame.click(s, fb)
        assert err is None and moved, err

    mv("f2", "f3")
    mv("e7", "e5")
    mv("g2", "g4")
    mv("d8", "h4")
    assert chessgame.outcome(s) == ("b", "мат")


def test_chess_rejects_illegal():
    s = chessgame.new_state()
    s, err, _ = chessgame.click(s, 6 * 8 + 4)  # e2
    assert err is None and s["selected"] == 52
    s2, err, moved = chessgame.click(s, 3 * 8 + 4)  # e5 — нельзя
    assert err and not moved


def test_ttt():
    s = tictactoe.new_state()
    for c in (0, 3, 1, 4, 2):
        s, err = tictactoe.move(s, c)
        assert err is None
    assert tictactoe.winner(s) == "X"
