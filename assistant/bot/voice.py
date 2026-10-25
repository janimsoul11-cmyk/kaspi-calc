"""Голос: распознавание (Whisper-совместимый API: Groq / OpenAI) и озвучка (edge-tts, бесплатно)."""

from __future__ import annotations

import asyncio
import logging
import shutil

import aiohttp
import edge_tts

from .config import settings

log = logging.getLogger(__name__)

TTS_MAX_CHARS = 2500


class STTError(Exception):
    pass


async def transcribe(audio: bytes, filename: str = "voice.ogg", content_type: str = "audio/ogg") -> str:
    """Отправляет аудио в /audio/transcriptions (формат OpenAI; Groq его повторяет)."""
    if not settings.stt_api_key:
        raise STTError("Не задан STT_API_KEY — голосовые распознавать нечем.")
    form = aiohttp.FormData()
    form.add_field("file", audio, filename=filename, content_type=content_type)
    form.add_field("model", settings.stt_model)
    form.add_field("response_format", "json")
    if settings.stt_language:
        form.add_field("language", settings.stt_language)
    if settings.stt_prompt:
        # Подсказка со словарём: имена, бренды, термины — сильно улучшает распознавание.
        form.add_field("prompt", settings.stt_prompt)
    timeout = aiohttp.ClientTimeout(total=180)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.post(
            f"{settings.stt_base_url}/audio/transcriptions",
            data=form,
            headers={"Authorization": f"Bearer {settings.stt_api_key}"},
        ) as resp:
            if resp.status != 200:
                raise STTError(f"STT {resp.status}: {(await resp.text())[:300]}")
            data = await resp.json()
    return (data.get("text") or "").strip()


async def _mp3_to_ogg_opus(mp3: bytes) -> bytes | None:
    """Telegram показывает голосовые как «кружок с волной» для OGG/Opus. Нужен ffmpeg."""
    if not shutil.which("ffmpeg"):
        return None
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
        "-c:a", "libopus", "-b:a", "32k", "-application", "voip", "-f", "ogg", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(mp3)
    if proc.returncode != 0:
        log.warning("ffmpeg failed: %s", err.decode(errors="ignore")[:300])
        return None
    return out


async def synthesize(text: str) -> tuple[bytes, str] | None:
    """Текст -> (аудио, 'ogg' | 'mp3'). None, если озвучить не удалось."""
    text = text.strip()
    if not text:
        return None
    if len(text) > TTS_MAX_CHARS:
        text = text[:TTS_MAX_CHARS].rsplit(".", 1)[0] + ". Остальное — в тексте."
    try:
        communicate = edge_tts.Communicate(text, settings.tts_voice, rate=settings.tts_rate)
        mp3 = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3.extend(chunk["data"])
    except Exception:  # edge-tts — неофициальный сервис, может временно не работать
        log.exception("TTS failed")
        return None
    if not mp3:
        return None
    ogg = await _mp3_to_ogg_opus(bytes(mp3))
    return (ogg, "ogg") if ogg else (bytes(mp3), "mp3")
