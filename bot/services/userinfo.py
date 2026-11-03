"""Карточка пользователя из открытых данных: ID, примерная дата регистрации, профиль.

Намеренно НЕ собирает связи человека (знакомые, группы, активность) — это карта
окружения и перемещений, то есть слежка. Здесь только публичные факты об аккаунте.
"""
from datetime import datetime, timezone

from ..utils.text import esc

_ANCHORS: list[tuple[int, int]] = [
    (1_000, 1_380_000_000),
    (100_000_000, 1_456_790_000),
    (200_000_000, 1_483_000_000),
    (300_000_000, 1_511_000_000),
    (400_000_000, 1_533_000_000),
    (500_000_000, 1_550_000_000),
    (600_000_000, 1_571_000_000),
    (700_000_000, 1_586_000_000),
    (800_000_000, 1_600_000_000),
    (900_000_000, 1_615_000_000),
    (1_000_000_000, 1_627_000_000),
    (1_200_000_000, 1_643_000_000),
    (1_500_000_000, 1_656_000_000),
    (1_700_000_000, 1_665_000_000),
    (2_000_000_000, 1_680_000_000),
    (5_000_000_000, 1_695_000_000),
    (6_500_000_000, 1_710_000_000),
]


def estimate_registration(user_id: int) -> tuple[str, bool] | None:
    if user_id <= 0 or user_id < _ANCHORS[0][0]:
        return None
    if user_id > _ANCHORS[-1][0]:
        dt = datetime.fromtimestamp(_ANCHORS[-1][1], tz=timezone.utc)
        return f"после {dt.month:02d}.{dt.year}", False
    for (id_a, ts_a), (id_b, ts_b) in zip(_ANCHORS, _ANCHORS[1:]):
        if id_a <= user_id <= id_b:
            frac = (user_id - id_a) / (id_b - id_a)
            ts = ts_a + frac * (ts_b - ts_a)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return f"{dt.month:02d}.{dt.year}", True
    return None


def format_card(
    *,
    user_id: int,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
    is_bot: bool = False,
    is_premium: bool = False,
    language_code: str | None = None,
    # новые опциональные блоки под твой шаблон
    name_history: list[str] | None = None,
    acquaintances: list[tuple[int, str | None]] | None = None,
    groups: list[tuple[str, str | None]] | None = None,
    messages_count: int | None = None,
) -> str:
    name = " ".join(filter(None, [first_name, last_name])) or "—"

    head = f"👤 {user_id}"
    if username:
        head += f" | @{esc(username)}"
    lines = [head]

    reg = estimate_registration(user_id)
    if reg:
        approx, exact = reg
        prefix = "≈ " if exact else ""
        lines.append(f"🗓 Месяц регистрации: {prefix}{approx}")

    if name_history:
        lines.append("\n📝 История имен:")
        for i, n in enumerate(name_history, 1):
            lines.append(f"{i}. {esc(n)}")

    if acquaintances:
        seen: dict[int, str | None] = {}
        for uid, uname in acquaintances:
            seen[uid] = uname
        lines.append("\n👥 Знакомые:")
        for uid, uname in seen.items():
            lines.append(f"- @{esc(uname)} [{uid}]" if uname else f"- [{uid}]")

    if groups is not None:
        lines.append(f"\n👥 Количество групп: {len(groups)}")
    if messages_count is not None:
        lines.append(f"✍️ Количество сообщений в группах: {messages_count}")

    if groups:
        lines.append("\nГруппы:")
        for title, chat_username in groups:
            t = esc(title)
            if chat_username:
                t += f" @{esc(chat_username)}"
            lines.append(f"- {t}")

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
