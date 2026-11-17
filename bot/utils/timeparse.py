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


def parse_duration_prefix(text: str) -> tuple[int | None, str]:
    """Срок в начале аргументов: «1h», «на час», «на два часа», «на полчаса»."""
    parts = text.split(maxsplit=1)
    if parts:
        seconds = parse_duration(parts[0])
        if seconds is not None:
            return seconds, parts[1] if len(parts) > 1 else ""
    counts = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3,
              "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
              "девять": 9, "десять": 10}
    numbers = "|".join(counts)
    match = re.match(
        rf"^(?:на\s+)?(?:(?P<count>[1-9]\d*|{numbers})\s+)?"
        r"(?P<unit>полчаса|полдня|секунду|секунда|секунды|секунд|минуту|минута|минуты|минут|"
        r"часа|часов|час|сутки|суток|день|дня|дней|неделю|недели|недель)\b",
        text.strip(), re.IGNORECASE)
    if not match:
        return None, text
    raw = match.group("count")
    count = int(raw) if raw and raw.isdigit() else counts.get((raw or "").lower(), 1)
    unit = match.group("unit").lower()
    size = (1800 if unit == "полчаса" else 43200 if unit == "полдня" else
            1 if unit.startswith("секунд") else 60 if unit.startswith("минут") else
            3600 if unit.startswith("час") else 604800 if unit.startswith("недел") else 86400)
    return count * size, text.strip()[match.end():].lstrip(" \t\r\n,:;")


def for_duration(seconds: int | None) -> str:
    """'навсегда' или 'на 30 мин' — для фраз вида «заглушен(а) …»."""
    return "навсегда" if not seconds else f"на {human_duration(seconds)}"


def human_duration(seconds: int | None) -> str:
    if not seconds:
        return "навсегда"
    parts = []
    for size, name in ((604800, "нед"), (86400, "д"), (3600, "ч"), (60, "мин"), (1, "сек")):
        if seconds >= size:
            parts.append(f"{seconds // size} {name}")
            seconds %= size
    return " ".join(parts[:2])
