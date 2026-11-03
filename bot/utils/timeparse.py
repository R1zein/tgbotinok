import re

_UNITS = {
    "s": 1, "sec": 1, "с": 1, "сек": 1,
    "m": 60, "min": 60, "м": 60, "мин": 60,
    "h": 3600, "ч": 3600, "час": 3600, "часа": 3600, "часов": 3600,
    "d": 86400, "д": 86400, "дн": 86400, "день": 86400, "дня": 86400, "дней": 86400,
    "w": 604800, "н": 604800, "нед": 604800,
}
_RE = re.compile(r"^(\d+)\s*([a-zа-яё]+)$", re.IGNORECASE)


def parse_duration(token: str) -> int | None:
    """'30m', '2ч', '1d' -> секунды. None, если токен не является длительностью."""
    m = _RE.match(token.strip().lower())
    if not m:
        return None
    unit = _UNITS.get(m.group(2))
    if unit is None:
        return None
    return int(m.group(1)) * unit


def human_duration(seconds: int | None) -> str:
    if not seconds:
        return "навсегда"
    parts = []
    for size, name in ((604800, "нед"), (86400, "д"), (3600, "ч"), (60, "мин"), (1, "сек")):
        if seconds >= size:
            parts.append(f"{seconds // size} {name}")
            seconds %= size
    return " ".join(parts[:2])
