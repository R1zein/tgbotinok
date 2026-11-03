from html import escape

from aiogram.types import User


def esc(text: str | None) -> str:
    return escape(text or "", quote=False)


def full_name(user: User) -> str:
    return user.full_name or user.username or str(user.id)


def mention(user_id: int, name: str | None) -> str:
    return f'<a href="tg://user?id={user_id}">{esc(name or str(user_id))}</a>'


def user_mention(user: User) -> str:
    return mention(user.id, full_name(user))


def fmt_num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def quote(text: str | None, limit: int = 3500) -> str:
    text = text or ""
    if len(text) > limit:
        text = text[:limit] + "…"
    return f"<blockquote>{esc(text)}</blockquote>" if text else "<i>(без текста)</i>"


def chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]
