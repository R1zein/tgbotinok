"""Стили и форматы для автоизменения своих сообщений (ассистент чатов)."""
import random
import re
from html import escape

# Ссылки, упоминания, хэштеги и команды не трогаем
PROTECTED = re.compile(r"^(https?://\S+|www\.\S+|@\w+|#\w+|/\w+)$", re.IGNORECASE)
VOWELS = "аеёиоуыэюяaeiouy"


def _map_words(text: str, fn) -> str:
    parts = re.split(r"(\s+)", text)
    return "".join(p if not p or p.isspace() or PROTECTED.match(p) else fn(p) for p in parts)


def _replace_words(text: str, table: dict[str, str]) -> str:
    def repl(word: str) -> str:
        m = re.match(r"^([^\wё]*)([\wё-]+)([^\wё]*)$", word, re.IGNORECASE)
        if not m:
            return word
        pre, core, post = m.groups()
        new = table.get(core.lower())
        if new is None:
            return word
        if core[0].isupper():
            new = new[0].upper() + new[1:]
        return pre + new + post
    return _map_words(text, repl)


def _strip_end(text: str) -> str:
    return text.rstrip(" .!?…")


# ---------- пикми / kawaii ----------
KAOMOJI = ["(づ｡◕‿‿◕｡)づ", "(｡♥‿♥｡)", "(✿◠‿◠)", "(◕ᴗ◕✿)", "(ﾉ◕ヮ◕)ﾉ*:･ﾟ✧", "(⁄ ⁄•⁄ω⁄•⁄ ⁄)",
           "ʕ•ᴥ•ʔ", "(´｡• ω •｡`)", "(≧◡≦)", ">w<", "uwu", "owo", "(˶ᵔ ᵕ ᵔ˶)", "(っ˘ω˘ς )",
           "(♡˙︶˙♡)", "(╯✧▽✧)╯"]
HEARTS = ["💕", "💖", "💗", "💓", "💞", "🎀", "✨", "🌸", "🐾", "💘"]
ACTIONS = ["*смотрит в пустоту*", "*обнимает за руку*", "*краснеет*", "*прячется под одеялко*",
           "*машет лапкой*", "*мурчит*", "*делает бровки домиком*", "*тыкает в щёчку*",
           "*хлопает глазками*", "*прижимает ушки*"]


def kawaii(text: str) -> str:
    def word(w: str) -> str:
        w = w.lower()
        letters = [i for i, ch in enumerate(w) if ch in VOWELS]
        r = random.random()
        if len(w) > 2 and w[0].isalpha() and r < 0.2:
            w = f"{w[0]}-{w[0]}-{w}"
        elif letters and r < 0.4:
            i = letters[-1]
            w = w[:i + 1] + w[i] * random.randint(1, 2) + w[i + 1:]
        return w.replace("r", "w").replace("l", "w")
    out = _map_words(text, word)
    out = _strip_end(out) + " " + random.choice(KAOMOJI) + random.choice(HEARTS)
    if random.random() < 0.3:
        out = random.choice(["ня~ ", "семпай, ", "эээ… "]) + out
    if random.random() < 0.35:
        out += "\n" + random.choice(ACTIONS) + " " + random.choice(HEARTS)
    return out


# ---------- гопник ----------
GOPNIK = {
    "что": "чё", "чего": "чё", "сейчас": "щас", "вообще": "ваще", "привет": "здарова",
    "здравствуйте": "здарова", "нормально": "норм", "хорошо": "чётко", "понятно": "ясн",
    "спасибо": "респект", "пожалуйста": "плз", "человек": "тип", "друг": "братан",
    "деньги": "бабки", "очень": "жёстко", "круто": "чётко", "девушка": "мадама", "да": "ага",
    "нет": "не-а", "говорю": "базарю", "говорить": "базарить", "смотри": "зырь",
    "телефон": "мобила", "полиция": "мусора", "уходить": "валить", "иди": "давай",
}


def gopnik(text: str) -> str:
    out = _replace_words(text, GOPNIK)
    if random.random() < 0.35:
        out = "Слышь, " + out[:1].lower() + out[1:]
    return _strip_end(out) + random.choice([", братан 🤙", ", чисто по-пацански 🌻", ", слышь 😎",
                                            " 🤙", ", без базара 💪"])


# ---------- пират ----------
PIRATE = {
    "привет": "ахой", "здравствуйте": "ахой", "друг": "матрос", "друзья": "матросы",
    "да": "так точно", "деньги": "дублоны", "дом": "корабль", "еда": "солонина", "вода": "ром",
    "девушка": "морская дева", "парень": "юнга", "нет": "тысяча чертей, нет", "работа": "абордаж",
    "машина": "шхуна", "магазин": "порт", "кто": "какая каракатица", "отлично": "попутного ветра",
}


def pirate(text: str) -> str:
    return _strip_end(_replace_words(text, PIRATE)) + random.choice(
        ["! Арр! 🏴‍☠️", "! Йо-хо-хо! 🦜", ", тысяча чертей! ⚓", "! Свистать всех наверх! ☠️"])


# ---------- боярин ----------
BOYAR = {
    "привет": "здравия желаю", "здравствуйте": "здрав буди", "да": "истинно", "нет": "отнюдь",
    "очень": "зело", "сейчас": "ныне", "теперь": "ныне", "глаза": "очи", "почему": "пошто",
    "зачем": "пошто", "хорошо": "добро", "спасибо": "благодарствую", "пожалуйста": "будь ласков",
    "друг": "друже", "это": "сие", "этот": "сей", "эта": "сия", "тоже": "такожде",
    "только": "токмо", "если": "аще", "деньги": "злато", "смотри": "зри", "говорить": "молвить",
    "говорю": "молвлю", "сказал": "рёк", "лицо": "лик", "рот": "уста", "палец": "перст",
    "вчера": "намедни", "давно": "издревле", "пока": "доколе", "ладно": "быть посему",
}


def boyar(text: str) -> str:
    out = _replace_words(text, BOYAR)
    if random.random() < 0.4:
        out = random.choice(["Сударь, ", "Внемли, ", "Челом бью, "]) + out[:1].lower() + out[1:]
    return out + " 🏰"


# ---------- милый ----------
SWEET = {
    "привет": "приветик", "здравствуй": "приветик", "пока": "пока-пока", "спасибо": "спасибочки",
    "пожалуйста": "пожалуйстик", "хорошо": "хорошо-хорошо", "ладно": "ладушки", "да": "дааа",
    "нет": "неа", "друг": "дружочек", "подруга": "подружка", "кот": "котик", "кошка": "кисонька",
    "собака": "собачка", "солнце": "солнышко", "утро": "утречко", "ночь": "ноченька",
    "день": "денёчек", "чай": "чаёчек", "кофе": "кофеёчек", "еда": "вкусняшка",
    "спать": "баиньки", "сплю": "баю-бай", "человек": "человечек", "время": "времечко",
    "домой": "домой-домой", "дом": "домик", "окей": "окидоки", "ок": "окидоки",
    "люблю": "очень-очень люблю", "мило": "ми-ми-мило", "грустно": "грустненько",
}
SWEET_TAIL = ["🥰", "💗", "🌷", "☺️", "🤍", "🌸", "💕", "🫶"]


def sweet(text: str) -> str:
    out = _replace_words(text, SWEET)
    if random.random() < 0.3:
        out = random.choice(["Солнышко, ", "Зайка, ", "Котик, "]) + out[:1].lower() + out[1:]
    return _strip_end(out) + random.choice(["~ ", " ", ")) "]) + random.choice(SWEET_TAIL)


# ---------- простые преобразования ----------
def caps(text: str) -> str:
    return _map_words(text, str.upper).rstrip(" .") + "!!!"


def whisper(text: str) -> str:
    return "🤫 " + _strip_end(_map_words(text, str.lower)) + "... *шёпотом*"


def zabor(text: str) -> str:
    i = 0

    def word(w: str) -> str:
        nonlocal i
        res = []
        for ch in w:
            if ch.isalpha():
                res.append(ch.lower() if i % 2 == 0 else ch.upper())
                i += 1
            else:
                res.append(ch)
        return "".join(res)
    return _map_words(text, word)


def reverse(text: str) -> str:
    return text[::-1]


def robot(text: str) -> str:
    return "🤖 БИП-БУП. " + _strip_end(_map_words(text, str.upper)) + ". *БИП*"


def zalgo(text: str) -> str:
    marks = [chr(c) for c in range(0x0300, 0x036F)]

    def word(w: str) -> str:
        return "".join(ch + "".join(random.choices(marks, k=random.randint(1, 3)))
                       if ch.isalpha() else ch for ch in w)
    return _map_words(text, word)


EMOJI = {
    "люблю": "❤️", "любовь": "❤️", "привет": "👋", "пока": "👋", "спасибо": "🙏", "кот": "🐱",
    "кошка": "🐱", "собак": "🐶", "пёс": "🐶", "еда": "🍔", "есть": "🍽", "кушать": "🍽",
    "пить": "🥤", "спать": "😴", "сплю": "😴", "смешно": "😂", "ахах": "😂", "хаха": "😂",
    "грустно": "😢", "плачу": "😭", "круто": "🔥", "деньги": "💰", "дом": "🏠", "работ": "💼",
    "школ": "🏫", "игр": "🎮", "музык": "🎵", "да": "👍", "нет": "🙅", "ок": "👌", "окей": "👌",
    "утро": "☀️", "ночь": "🌙", "солнц": "☀️", "дожд": "🌧", "машин": "🚗", "кофе": "☕",
    "чай": "🍵", "подарок": "🎁", "праздник": "🎉", "день рождения": "🎂", "думаю": "🤔",
}


def emoji(text: str) -> str:
    def word(w: str) -> str:
        core = re.sub(r"[^\wё]", "", w.lower())
        for key, e in EMOJI.items():
            if (len(key) <= 3 and core == key) or (len(key) > 3 and core.startswith(key)):
                return f"{w} {e}"
        return w
    return _map_words(text, word) + " " + random.choice(["✨", "😜", "🤩", "💫", "🥳"])


def spaced(text: str) -> str:
    parts = re.split(r"(\s+)", text)
    return "".join(p.replace(" ", "   ") if p.isspace() else p if PROTECTED.match(p) else " ".join(p)
                   for p in parts)


STYLES: dict[str, tuple[str, object]] = {
    "kawaii": ("💞 Пикми (kawaii)", kawaii),
    "sweet": ("🥰 Милый", sweet),
    "gopnik": ("🤙 Гопник", gopnik),
    "pirate": ("🏴‍☠️ Пират", pirate),
    "boyar": ("🏰 Боярин", boyar),
    "caps": ("📢 КАПС", caps),
    "whisper": ("🤫 Шёпот", whisper),
    "zabor": ("🔀 ЗаБоРчИк", zabor),
    "reverse": ("🔄 Наоборот", reverse),
    "robot": ("🤖 Робот", robot),
    "zalgo": ("👹 Проклятый", zalgo),
    "emoji": ("😜 Эмодзи", emoji),
    "spaced": ("↔️ Р а з р я д к а", spaced),
}

FORMATS: dict[str, tuple[str, str, str]] = {
    "bold": ("𝐁 Жирный", "<b>", "</b>"),
    "italic": ("𝐼 Курсив", "<i>", "</i>"),
    "underline": ("U̲ Подчёркнутый", "<u>", "</u>"),
    "strike": ("S̶ Зачёркнутый", "<s>", "</s>"),
    "mono": ("⌨️ Моно", "<code>", "</code>"),
    "spoiler": ("🫣 Спойлер", "<tg-spoiler>", "</tg-spoiler>"),
    "quote": ("❝ Цитата", "<blockquote>", "</blockquote>"),
}

# Алиасы для команд в чате: .пикми, .стиль пират, .жирный, .формат курсив …
STYLE_ALIASES = {
    "милый": "sweet", "милая": "sweet", "мило": "sweet", "sweet": "sweet", "cute": "sweet",
    "пикми": "kawaii", "kawaii": "kawaii", "кавай": "kawaii", "каваи": "kawaii", "uwu": "kawaii",
    "гопник": "gopnik", "пацан": "gopnik", "пират": "pirate", "pirate": "pirate",
    "боярин": "boyar", "старорусский": "boyar", "капс": "caps", "caps": "caps", "крик": "caps",
    "шепот": "whisper", "шёпот": "whisper", "заборчик": "zabor", "наоборот": "reverse",
    "реверс": "reverse", "робот": "robot", "robot": "robot", "проклятый": "zalgo",
    "зальго": "zalgo", "zalgo": "zalgo", "эмодзи": "emoji", "emoji": "emoji",
    "разрядка": "spaced",
}
FORMAT_ALIASES = {
    "жирный": "bold", "bold": "bold", "курсив": "italic", "italic": "italic",
    "подчеркнутый": "underline", "подчёркнутый": "underline", "underline": "underline",
    "зачеркнутый": "strike", "зачёркнутый": "strike", "strike": "strike", "моно": "mono",
    "mono": "mono", "код": "mono", "спойлер": "spoiler", "spoiler": "spoiler",
    "цитата": "quote", "quote": "quote",
}
OFF_WORDS = {"выкл", "off", "нет", "сброс", "убрать", "0"}


def render(plain: str, html: str, style: str, fmt: str) -> str | None:
    """HTML нового текста или None, если менять нечего.

    plain — текст сообщения, html — он же с сохранённой разметкой (ссылки, премиум-эмодзи):
    если стиль не выбран, оборачиваем исходную разметку, иначе стилизуем чистый текст.
    """
    if style in STYLES:
        body = escape(STYLES[style][1](plain), quote=False)
    elif fmt in FORMATS:
        body = html
    else:
        return None
    if fmt in FORMATS:
        _, open_tag, close_tag = FORMATS[fmt]
        body = f"{open_tag}{body}{close_tag}"
    return body
