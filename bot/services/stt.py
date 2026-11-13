"""Расшифровка голосовых: Whisper на сервере (faster-whisper) или через OpenAI-совместимый API."""
import asyncio
import io
import logging

import aiohttp
from aiogram import Bot

from ..config import Config
from ..proxy import aiohttp_connector

log = logging.getLogger(__name__)


class STTError(Exception):
    """Ошибка с понятным пользователю текстом."""


async def fetch_audio(bot: Bot, file_id: str) -> bytes:
    """Скачивает файл голосового/кружка у Telegram (лимит Bot API — 20 МБ)."""
    buf = await bot.download(file_id)
    return buf.read()


def media_of(message) -> tuple[str, str, int | None] | None:
    """(file_id, имя файла, длительность) голосового, кружка или аудио в сообщении."""
    if message is None:
        return None
    if message.voice:
        return message.voice.file_id, "voice.ogg", message.voice.duration
    if message.video_note:
        return message.video_note.file_id, "video_note.mp4", message.video_note.duration
    if message.audio:
        return message.audio.file_id, message.audio.file_name or "audio.mp3", message.audio.duration
    return None


class Transcriber:
    def __init__(self, config: Config):
        self.config = config
        self._model = None
        self._lock = asyncio.Lock()  # одна расшифровка за раз — процессор у сервера один
        self._session: aiohttp.ClientSession | None = None

    @property
    def enabled(self) -> bool:
        if self.config.stt_backend == "api":
            return bool(self.config.stt_api_key)
        return self.config.stt_backend == "local"

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def transcribe(self, data: bytes, filename: str, duration: int | None = None) -> str:
        if not self.enabled:
            raise STTError("Расшифровка голосовых не настроена администратором бота.")
        limit = self.config.stt_max_seconds
        if duration and duration > limit:
            raise STTError(f"Слишком длинное сообщение: расшифровываю до {limit // 60} мин.")
        if self.config.stt_backend == "api":
            text = await self._api(data, filename)
        else:
            async with self._lock:
                text = await asyncio.get_running_loop().run_in_executor(None, self._local, data)
        return " ".join(text.split())

    # ---------- Whisper на сервере ----------
    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # тяжёлый импорт — только когда нужен
            log.info("Загружаю модель Whisper «%s»…", self.config.stt_model)
            self._model = WhisperModel(self.config.stt_model, device="cpu", compute_type="int8",
                                       download_root=self.config.stt_model_dir)
        return self._model

    def _local(self, data: bytes) -> str:
        try:
            model = self._load()
        except Exception as e:  # noqa: BLE001 — нет сети до huggingface, нет места и т.п.
            log.exception("Модель Whisper не загрузилась")
            raise STTError("Не удалось загрузить модель распознавания речи — "
                           "администратору нужно проверить лог бота.") from e
        segments, _ = model.transcribe(io.BytesIO(data), language=self.config.stt_language,
                                       beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segments)

    # ---------- OpenAI-совместимый API ----------
    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp_connector(self.config.proxy) or aiohttp.TCPConnector(
                keepalive_timeout=10)
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120, sock_connect=20),
                connector=connector, trust_env=not self.config.proxy)
        return self._session

    async def _api(self, data: bytes, filename: str) -> str:
        status, body = None, None
        for attempt in range(1, 4):
            form = aiohttp.FormData()
            form.add_field("file", data, filename=filename,
                           content_type="application/octet-stream")
            form.add_field("model", self.config.stt_model)
            if self.config.stt_language:
                form.add_field("language", self.config.stt_language)
            try:
                session = await self._http()
                async with session.post(
                    f"{self.config.stt_base_url.rstrip('/')}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.config.stt_api_key}"}, data=form,
                ) as resp:
                    status = resp.status
                    body = await resp.json(content_type=None)
            except Exception as e:  # noqa: BLE001
                status = None
                log.warning("STT: попытка %s/3 не прошла: %s: %s", attempt, type(e).__name__, e)
            else:
                if status not in (500, 502, 503, 504):
                    break
            if attempt < 3:
                await asyncio.sleep(1.5 * attempt)
        if status is None:
            raise STTError("Сервис расшифровки недоступен, попробуйте позже.")
        if status == 401:
            raise STTError("Ключ сервиса расшифровки неверный.")
        if status == 403:
            raise STTError("Сервис расшифровки не пускает этот сервер (регион или доступ).")
        if status != 200:
            log.warning("STT: ответ %s: %s", status, str(body)[:300])
            raise STTError("Сервис расшифровки ответил ошибкой.")
        return (body or {}).get("text", "")
