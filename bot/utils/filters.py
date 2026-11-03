from aiogram.filters import Filter
from aiogram.types import Message

PREFIXES = ("/", "!", ".")


class Cmd(Filter):
    """Команда в стиле чат-ботов: '/mute', '!мут', '.мут', 'мут' (без префикса).

    Передаёт в хендлер `args: str` — текст после команды.
    """

    def __init__(self, *names: str, allow_bare: bool = True):
        self.names = {n.lower() for n in names}
        self.allow_bare = allow_bare

    async def __call__(self, message: Message, bot) -> bool | dict:
        text = message.text or message.caption
        if not text:
            return False
        parts = text.strip().split(maxsplit=1)
        if not parts:
            return False
        word = parts[0].lower()
        prefixed = word[:1] in PREFIXES
        if prefixed:
            word = word[1:]
        elif not self.allow_bare:
            return False
        if "@" in word:
            word, _, mention = word.partition("@")
            me = await bot.me()
            if mention != (me.username or "").lower():
                return False
        if word not in self.names:
            return False
        return {"args": parts[1].strip() if len(parts) > 1 else ""}
