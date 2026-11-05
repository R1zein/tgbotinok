"""Простое сопоставление имён с учётом падежей: «паша», «паше», «пашу», «пашей» → «паш».

Отсекаем типичные русские окончания, если после них остаётся основа хотя бы из 3 букв.
Уменьшительные суффиксы (Пашка) специально не трогаем — так меньше ложных совпадений
(«Катя» не будет срабатывать на «катка»). Нужные варианты можно добавить отдельным именем.
"""
import re

_ENDINGS = sorted(
    ["ами", "ями", "ого", "его", "ому", "ему", "ой", "ей", "ою", "ею", "ом", "ем", "ам", "ям",
     "ах", "ях", "ых", "их", "ов", "ев", "а", "я", "ы", "и", "е", "у", "ю", "о", "ь", "й"],
    key=len, reverse=True,
)
_WORD = re.compile(r"[a-zа-яё]+", re.IGNORECASE)


def norm(word: str) -> str:
    return word.lower().replace("ё", "е")


def stem(word: str) -> str:
    w = norm(word)
    if not re.search(r"[а-я]", w):  # латиница: только «s» в конце
        return w[:-1] if len(w) > 3 and w.endswith("s") else w
    for e in _ENDINGS:
        if w.endswith(e) and len(w) - len(e) >= 3:
            return w[:-len(e)]
    return w


def alias_key(alias: str) -> tuple[str, ...]:
    """Ключ для сравнения: основы слов имени («паша иванов» → ('паш', 'иванов'))."""
    return tuple(stem(w) for w in _WORD.findall(alias))


def text_keys(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text or "")]


def contains(keys: list[str], alias: tuple[str, ...]) -> bool:
    if not alias:
        return False
    n = len(alias)
    return any(tuple(keys[i:i + n]) == alias for i in range(len(keys) - n + 1))
