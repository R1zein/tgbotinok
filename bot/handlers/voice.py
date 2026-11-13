"""Расшифровка голосовых в текст.

- в группе: ответить на голосовое/кружок командой «текст» (или «расшифруй»);
- в личке с ботом: прислать или переслать голосовое/кружок — бот ответит текстом.
В бизнес-чатах (ЛС с людьми) — команда «.текст», см. handlers/business.py.
"""
from aiogram import Bot, F, Router
from aiogram.types import Message

from ..db import Database
from ..services.stt import STTError, Transcriber, fetch_audio, media_of
from ..utils.filters import Cmd
from ..utils.text import esc

router = Router(name="voice")
STT_CMDS = ("текст", "расшифруй", "расшифровка", "транскрипт", "text", "stt")


def transcript_text(text: str) -> str:
    if not text:
        return "🤷 Не удалось разобрать речь."
    return f"🗣 <i>{esc(text[:3900])}</i>"


async def transcribe_to(placeholder: Message, bot: Bot, stt: Transcriber, media) -> None:
    file_id, name, duration = media
    try:
        text = await stt.transcribe(await fetch_audio(bot, file_id), name, duration)
        await placeholder.edit_text(transcript_text(text))
    except STTError as e:
        await placeholder.edit_text(f"⚠️ {esc(str(e))}")
    except Exception:  # noqa: BLE001 — например, файл больше 20 МБ
        await placeholder.edit_text("⚠️ Не получилось расшифровать это сообщение.")


@router.message(F.chat.type.in_({"group", "supergroup"}), Cmd(*STT_CMDS))
async def cmd_group_stt(message: Message, bot: Bot, db: Database, stt: Transcriber):
    if not await db.group_feature(message.chat.id, "stt"):
        return
    media = media_of(message.reply_to_message)
    if not media:
        return await message.reply("🗣 Ответьте этой командой на голосовое или кружок.")
    placeholder = await message.reply_to_message.reply("⏳ Расшифровываю…")
    await transcribe_to(placeholder, bot, stt, media)


@router.message(F.chat.type == "private", F.voice | F.video_note | F.audio)
async def private_stt(message: Message, bot: Bot, db: Database, stt: Transcriber):
    if not db.feature_enabled("stt"):
        return
    placeholder = await message.reply("⏳ Расшифровываю…")
    await transcribe_to(placeholder, bot, stt, media_of(message))
