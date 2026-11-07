"""ИИ-ответы через API, совместимый с OpenAI (по умолчанию DeepSeek)."""
import asyncio
import logging
import time
from collections import deque

import aiohttp

from ..config import Config
from ..proxy import aiohttp_connector

log = logging.getLogger(__name__)

PERSONAS = {
    "normal": ("🙂 Обычный", "Отвечай дружелюбно и по делу."),
    "sassy": ("😏 Дерзкий", "Отвечай с юмором и лёгкой дерзостью, подкалывай, но без грубых "
                           "оскорблений."),
    "sweet": ("🥰 Милый", "Отвечай очень мило и ласково, с уменьшительными словами и эмодзи."),
    "short": ("⚡ Краткий", "Отвечай максимально коротко — одной-двумя фразами."),
}
MEMORY = 30          # сколько последних сообщений чата помним для контекста
MAX_TOKENS = 400     # потолок длины ответа
ATTEMPTS = 3         # попыток на один вопрос при сбоях сети/сервера
RETRY_STATUSES = {500, 502, 503, 504}


class AIError(Exception):
    """Ошибка с понятным пользователю текстом."""


class ChatMemory:
    """Последние сообщения каждой группы — контекст для ответа. Хранится только в памяти."""

    def __init__(self, size: int = MEMORY):
        self.size = size
        self._chats: dict[int, deque] = {}

    def add(self, chat_id: int, author: str, text: str) -> None:
        self._chats.setdefault(chat_id, deque(maxlen=self.size)).append((author, text[:500]))

    def get(self, chat_id: int) -> list[tuple[str, str]]:
        return list(self._chats.get(chat_id, ()))


def system_prompt(bot_name: str, persona: str) -> str:
    style = PERSONAS.get(persona, PERSONAS["normal"])[1]
    return (
        f"Ты — {bot_name}, бот-участник группового чата в Telegram. {style} "
        "Пиши по-русски, если тебе пишут по-русски. Обычно 1–4 предложения, без длинных списков "
        "и без Markdown. Не притворяйся человеком, если спрашивают прямо. Не оскорбляй людей по "
        "национальности, полу, внешности и т.п., не помогай с опасными или незаконными вещами."
    )


def build_messages(bot_name: str, persona: str, history: list[tuple[str, str]],
                   author: str, question: str) -> list[dict]:
    lines = "\n".join(f"{a}: {t}" for a, t in history[-MEMORY:])
    user = (f"Последние сообщения чата:\n{lines}\n\n" if lines else "")
    user += f"Сейчас тебе пишет {author}: {question}\nОтветь ему."
    return [{"role": "system", "content": system_prompt(bot_name, persona)},
            {"role": "user", "content": user}]


class AIClient:
    def __init__(self, config: Config):
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._day = ""
        self._used = 0

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # DeepSeek закрывает простаивающие соединения; держим их открытыми недолго,
            # иначе первый вопрос после паузы падает на «мёртвом» соединении
            connector = aiohttp_connector(self.config.proxy) or aiohttp.TCPConnector(
                keepalive_timeout=10)
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.config.ai_timeout, sock_connect=20),
                connector=connector,
                trust_env=not self.config.proxy,
            )
        return self._session

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    def budget_left(self) -> bool:
        day = time.strftime("%Y-%m-%d")
        if day != self._day:
            self._day, self._used = day, 0
        return self._used < self.config.ai_daily_limit

    async def complete(self, messages: list[dict]) -> str:
        if not self.config.ai_configured:
            raise AIError("ИИ не настроен: администратор бота не добавил ключ API.")
        if not self.budget_left():
            raise AIError("На сегодня лимит ИИ-ответов исчерпан, попробуйте завтра.")
        self._used += 1
        status, data = None, None
        for attempt in range(1, ATTEMPTS + 1):
            session = await self._http()
            try:
                async with session.post(
                    f"{self.config.ai_base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.ai_api_key}"},
                    json={"model": self.config.ai_model, "messages": messages,
                          "max_tokens": MAX_TOKENS, "temperature": 1.0},
                ) as resp:
                    status = resp.status
                    data = await resp.json(content_type=None)
            except Exception as e:  # noqa: BLE001 — сеть, таймаут, не-JSON в ответе
                status, data = None, None
                log.warning("ИИ: попытка %s/%s не прошла: %s: %s", attempt, ATTEMPTS,
                            type(e).__name__, e)
            else:
                if status not in RETRY_STATUSES:
                    break
                log.warning("ИИ: попытка %s/%s — сервер ответил %s", attempt, ATTEMPTS, status)
            if attempt < ATTEMPTS:
                await asyncio.sleep(1.5 * attempt)
        if status is None:
            raise AIError("ИИ сейчас недоступен, попробуйте позже.")
        if status in RETRY_STATUSES:
            raise AIError("Сервер ИИ перегружен, попробуйте чуть позже.")
        if status == 401:
            raise AIError("Ключ ИИ неверный — администратору бота нужно его проверить.")
        if status == 402:
            raise AIError("На балансе ИИ закончились деньги — нужно пополнить.")
        if status == 429:
            raise AIError("ИИ перегружен, попробуйте через минуту.")
        if status != 200:
            log.warning("ИИ: ответ %s: %s", status, str(data)[:300])
            raise AIError("ИИ ответил ошибкой, попробуйте позже.")
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as e:
            raise AIError("ИИ прислал пустой ответ.") from e
