"""Карточка пользователя из открытых данных: ID, примерная дата регистрации, профиль.

Намеренно НЕ собирает связи человека (знакомые, группы, активность) — это карта
окружения и перемещений, то есть слежка. Здесь только публичные факты об аккаунте.
"""
from datetime import datetime, timezone

from ..utils.text import esc

# Опорные точки «ID ≈ дата регистрации». Значения приблизительные: ID выдаются
# почти по возрастанию во времени, поэтому дату можно оценить лишь грубо (±несколько месяцев).
_ANCHORS: list[tuple[int, int]] = [
    (1_000, 1_380_000_000),          # ~2013-09
    (100_000_000, 1_456_790_000),    # ~2016-03
    (200_000_000, 1_483_000_000),    # ~2017-01
    (300_000_000, 1_511_000_000),    # ~2017-11
    (400_000_000, 1_533_000_000),    # ~2018-08
    (500_000_000, 1_550_000_000),    # ~2019-02
    (600_000_000, 1_571_000_000),    # ~2019-10
    (700_000_000, 1_586_000_000),    # ~2020-04
    (800_000_000, 1_600_000_000),    # ~2020-09
    (900_000_000, 1_615_000_000),    # ~2021-03
    (1_000_000_000, 1_627_000_000),  # ~2021-07
    (1_200_000_000, 1_643_000_000),  # ~2022-01
    (1_500_000_000, 1_656_000_000),  # ~2022-06
    (1_700_000_000, 1_665_000_000),  # ~2022-10
    (2_000_000_000, 1_680_000_000),  # ~2023-04
    (5_000_000_000, 1_695_000_000),  # ~2023-09
    (6_500_000_000, 1_710_000_000),  # ~2024-03
]

MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


def estimate_registration(user_id: int) -> tuple[str, bool] | None:
    """(примерная дата регистрации 'месяц YYYY', точная_ли_оценка). None — оценить нельзя."""
    if user_id <= 0 or user_id < _ANCHORS[0][0]:
        return None
    if user_id > _ANCHORS[-1][0]:
        # За пределами таблицы точность падает; большие ID часто у ботов
        dt = datetime.fromtimestamp(_ANCHORS[-1][1], tz=timezone.utc)
        return f"после {MONTHS[dt.month]} {dt.year}", False
    for (id_a, ts_a), (id_b, ts_b) in zip(_ANCHORS, _ANCHORS[1:]):
        if id_a <= user_id <= id_b:
            frac = (user_id - id_a) / (id_b - id_a)
            ts = ts_a + frac * (ts_b - ts_a)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return f"{MONTHS[dt.month]} {dt.year}", True
    return None


def format_card(*, user_id: int, first_name: str | None = None, last_name: str | None = None,
                username: str | None = None, is_bot: bool = False, is_premium: bool = False,
                language_code: str | None = None) -> str:
    name = " ".join(filter(None, [first_name, last_name])) or "—"
    lines = ["ℹ️ <b>Информация</b>\n",
             f"👤 Имя: {esc(name)}",
             f"🔗 Юзернейм: @{esc(username)}" if username else "🔗 Юзернейм: —",
             f"🆔 ID: <code>{user_id}</code>"]
    reg = estimate_registration(user_id)
    if reg:
        approx, exact = reg
        prefix = "📅 Регистрация: ≈ " if exact else "📅 Регистрация: "
        lines.append(f"{prefix}{approx}")
    flags = []
    if is_bot:
        flags.append("🤖 бот")
    if is_premium:
        flags.append("⭐ Premium")
    if flags:
        lines.append("🏷 " + ", ".join(flags))
    if language_code:
        lines.append(f"🌐 Язык клиента: {esc(language_code)}")
    return "\n".join(lines)
