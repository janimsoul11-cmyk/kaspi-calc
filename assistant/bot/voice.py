"""Голос: распознавание (Whisper-совместимый API: Groq / OpenAI) и озвучка.

Озвучка бесплатная и идёт по цепочке провайдеров (TTS_PROVIDERS): edge-tts (голоса Microsoft),
затем Google Translate TTS. Оба неофициальные, поэтому если один не отвечает — пробуем следующий.
"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil

import aiohttp
import edge_tts
from gtts import gTTS

from .config import settings

log = logging.getLogger(__name__)

TTS_MAX_CHARS = 2500

# Последняя ошибка каждого провайдера — для /voicetest и уведомления владельца.
last_tts_errors: dict[str, str] = {}


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


async def _edge_tts(text: str) -> bytes:
    """Голоса Microsoft. Сервис иногда отвечает без звука (NoAudioReceived) — одна повторная попытка."""
    for attempt in range(2):
        try:
            communicate = edge_tts.Communicate(text, settings.tts_voice, rate=settings.tts_rate)
            mp3 = bytearray()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3.extend(chunk["data"])
            return bytes(mp3)
        except edge_tts.exceptions.NoAudioReceived:
            if attempt:
                raise
            await asyncio.sleep(1.5)
    return b""


async def _google_tts(text: str) -> bytes:
    """Google Translate TTS: проще звучит, зато работает там, где edge-tts не отвечает."""
    def run() -> bytes:
        buf = io.BytesIO()
        gTTS(text, lang=settings.tts_lang).write_to_fp(buf)
        return buf.getvalue()

    return await asyncio.wait_for(asyncio.to_thread(run), timeout=60)


TTS_PROVIDERS = {"edge": _edge_tts, "google": _google_tts}


async def synthesize(text: str, providers: list[str] | None = None) -> tuple[bytes, str] | None:
    """Текст -> (аудио, 'ogg' | 'mp3'). None, если ни один провайдер не смог озвучить."""
    text = text.strip()
    if not text:
        return None
    if len(text) > TTS_MAX_CHARS:
        text = text[:TTS_MAX_CHARS].rsplit(".", 1)[0] + ". Остальное — в тексте."
    for name in providers or settings.tts_providers:
        provider = TTS_PROVIDERS.get(name)
        if provider is None:
            continue
        try:
            mp3 = await provider(text)
        except Exception as e:  # оба сервиса неофициальные и могут временно не работать
            last_tts_errors[name] = f"{type(e).__name__}: {e}"[:300]
            log.warning("TTS %s failed: %s", name, last_tts_errors[name])
            continue
        if not mp3:
            last_tts_errors[name] = "пустой ответ"
            continue
        last_tts_errors.pop(name, None)
        ogg = await _mp3_to_ogg_opus(mp3)
        return (ogg, "ogg") if ogg else (mp3, "mp3")
    return None
