"""Голос: распознавание (Whisper-совместимый API: Groq / OpenAI) и озвучка.

Озвучка бесплатная и идёт по цепочке провайдеров (TTS_PROVIDERS): edge-tts (голоса Microsoft),
затем Google Translate TTS. Оба неофициальные, поэтому если один не отвечает — пробуем следующий.
"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import time

import aiohttp
import edge_tts
from gtts import gTTS

from .config import settings

log = logging.getLogger(__name__)

# Последняя ошибка каждого провайдера — для /voicetest и уведомления владельца.
last_tts_errors: dict[str, str] = {}
# До какого момента (time.monotonic) провайдер считается «недавно падавшим».
_failed_until: dict[str, float] = {}
FAILED_PROVIDER_PENALTY = 3600
# Кто озвучил последний ответ и сколько символов — для учёта расходов на платный голос.
last_spoken: dict = {}


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


async def _edge_tts(text: str, voice: str | None = None) -> bytes:
    """Голоса Microsoft. Сервис иногда отвечает без звука (NoAudioReceived) — одна повторная попытка."""
    for attempt in range(2):
        try:
            communicate = edge_tts.Communicate(text, voice or settings.tts_voice, rate=settings.tts_rate)
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


async def _openai_tts(text: str) -> bytes:
    """Живой голос OpenAI (gpt-4o-mini-tts): платно, ~$0.015 за минуту речи, интонацию задаёт instructions."""
    payload = {
        "model": settings.openai_tts_model,
        "voice": settings.openai_tts_voice,
        "input": text,
        "instructions": settings.openai_tts_instructions,
        "response_format": "mp3",
    }
    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.post(
            f"{settings.openai_base_url}/audio/speech",
            json=payload,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"OpenAI TTS {resp.status}: {(await resp.text())[:200]}")
            return await resp.read()


TTS_PROVIDERS = {"openai": _openai_tts, "edge": _edge_tts, "google": _google_tts}


def tts_configured(name: str) -> bool:
    """Платные провайдеры включаются только когда задан ключ."""
    return name != "openai" or bool(settings.openai_api_key)


async def synthesize(text: str, providers: list[str] | None = None) -> tuple[bytes, str] | None:
    """Текст -> (аудио, 'ogg' | 'mp3'). None, если ни один провайдер не смог озвучить."""
    text = text.strip()
    if not text:
        return None
    if len(text) > settings.tts_max_chars:
        # Голосом — начало ответа, остальное в тексте: длинные списки на слух тяжело воспринимать.
        text = text[:settings.tts_max_chars].rsplit(".", 1)[0] + ". Остальное — в тексте."
    names = providers or settings.tts_providers
    if providers is None:
        # Недавно падавший провайдер пробуем последним, чтобы не ждать его на каждом ответе.
        now = time.monotonic()
        names = sorted(names, key=lambda n: _failed_until.get(n, 0) > now)
    for name in names:
        provider = TTS_PROVIDERS.get(name)
        if provider is None:
            continue
        if not tts_configured(name):
            last_tts_errors[name] = "не настроен: нет ключа в .env"
            continue
        try:
            mp3 = await provider(text)
        except Exception as e:  # оба сервиса неофициальные и могут временно не работать
            last_tts_errors[name] = f"{type(e).__name__}: {e}"[:300]
            _failed_until[name] = time.monotonic() + FAILED_PROVIDER_PENALTY
            log.warning("TTS %s failed: %s", name, last_tts_errors[name])
            continue
        if not mp3:
            last_tts_errors[name] = "пустой ответ"
            _failed_until[name] = time.monotonic() + FAILED_PROVIDER_PENALTY
            continue
        last_tts_errors.pop(name, None)
        _failed_until.pop(name, None)
        last_spoken.update(provider=name, chars=len(text))
        ogg = await _mp3_to_ogg_opus(mp3)
        return (ogg, "ogg") if ogg else (mp3, "mp3")
    return None


# Бесплатные голоса Microsoft для /voices. Multilingual — новое «разговорное» поколение (как в Copilot),
# сами определяют язык текста.
EDGE_VOICE_CHOICES = [
    ("ru-RU-DmitryNeural", "Дмитрий — русский, мужской"),
    ("ru-RU-SvetlanaNeural", "Светлана — русский, женский"),
    ("en-US-AndrewMultilingualNeural", "Andrew — разговорный, мужской"),
    ("en-US-BrianMultilingualNeural", "Brian — разговорный, мужской"),
    ("en-US-AvaMultilingualNeural", "Ava — разговорный, женский"),
    ("en-US-EmmaMultilingualNeural", "Emma — разговорный, женский"),
]


async def edge_sample(text: str, voice: str) -> tuple[bytes, str] | None:
    """Образец конкретного голоса Microsoft (для выбора в /voices)."""
    try:
        mp3 = await _edge_tts(text, voice)
    except Exception as e:
        last_tts_errors[f"edge:{voice}"] = f"{type(e).__name__}: {e}"[:300]
        return None
    if not mp3:
        return None
    ogg = await _mp3_to_ogg_opus(mp3)
    return (ogg, "ogg") if ogg else (mp3, "mp3")


def openai_tts_cost(chars: int) -> float:
    """Оценка: ~900 символов русской речи в минуту, ~$0.015 за минуту."""
    return chars / 900 * 0.015
