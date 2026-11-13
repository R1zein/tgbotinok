"""Расшифровка голосовых: Whisper на сервере (faster-whisper) или через OpenAI-совместимый API."""
import asyncio
import io
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import aiohttp

# Таймауты скачивания модели с huggingface: без них медленный/заблокированный HF
# вешает расшифровку навсегда. Отдельные запросы оборвутся, и мы увидим ошибку в логе.
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "15")
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
        self._model_lock = asyncio.Lock()        # грузим модель один раз
        # Отдельный поток-одиночка: расшифровки идут по очереди и не занимают общий пул
        # (которым пользуется и скачивание файлов у Telegram).
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
        self._session: aiohttp.ClientSession | None = None

    @property
    def enabled(self) -> bool:
        if self.config.stt_backend == "api":
            return bool(self.config.stt_api_key)
        return self.config.stt_backend == "local"

    async def close(self) -> None:
        if self._session:
            await self._session.close()
        self._executor.shutdown(wait=False)

    async def transcribe(self, data: bytes, filename: str, duration: int | None = None) -> str:
        if not self.enabled:
            raise STTError("Расшифровка голосовых не настроена администратором бота.")
        limit = self.config.stt_max_seconds
        if duration and duration > limit:
            raise STTError(f"Слишком длинное сообщение: расшифровываю до {limit // 60} мин.")
        if self.config.stt_backend == "api":
            text = await self._api(data, filename)
        else:
            text = await self._local(data, duration)
        return " ".join(text.split())

    async def _in_thread(self, fn, *args, timeout: float) -> object:
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self._executor, fn, *args)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError as e:
            raise STTError("Расшифровка заняла слишком долго — попробуйте сообщение короче.") from e

    # ---------- Whisper на сервере ----------
    def _load(self):
        from faster_whisper import WhisperModel  # тяжёлый импорт — только когда нужен
        t0 = time.monotonic()
        log.info("Загружаю модель Whisper «%s» (первый раз — скачивание ~150 МБ)…",
                 self.config.stt_model)
        self._model = WhisperModel(self.config.stt_model, device="cpu", compute_type="int8",
                                   download_root=self.config.stt_model_dir)
        log.info("Модель Whisper готова за %.0f сек", time.monotonic() - t0)

    async def ensure_model(self) -> None:
        """Грузит модель один раз. Можно вызвать при старте, чтобы первый пользователь не ждал."""
        if self._model is not None:
            return
        async with self._model_lock:
            if self._model is not None:
                return
            try:
                await self._in_thread(self._load, timeout=max(900, self.config.stt_timeout))
            except STTError:
                raise
            except Exception as e:  # noqa: BLE001 — нет сети до huggingface, нет места и т.п.
                log.exception("Модель Whisper не загрузилась")
                raise STTError("Не удалось загрузить модель распознавания речи — "
                               "администратору нужно проверить лог (huggingface недоступен?).") from e

    def _transcribe_sync(self, data: bytes) -> str:
        t0 = time.monotonic()
        segments, _ = self._model.transcribe(io.BytesIO(data), language=self.config.stt_language,
                                             beam_size=1, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments)
        log.info("Расшифровка (%d симв.) за %.1f сек", len(text), time.monotonic() - t0)
        return text

    async def _local(self, data: bytes, duration: int | None) -> str:
        await self.ensure_model()
        timeout = max(self.config.stt_timeout, (duration or 0) * 6 + 30)
        return await self._in_thread(self._transcribe_sync, data, timeout=timeout)

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
