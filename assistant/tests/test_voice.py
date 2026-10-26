import asyncio

import edge_tts

from bot import assistant as assistant_mod
from bot import voice
from bot.assistant import Assistant
from bot.config import settings


def _no_ffmpeg(monkeypatch):
    async def none(mp3):
        return None

    monkeypatch.setattr(voice, "_mp3_to_ogg_opus", none)


def test_tts_falls_back_to_next_provider(monkeypatch):
    async def broken(text):
        raise edge_tts.exceptions.NoAudioReceived("No audio was received.")

    async def works(text):
        return b"ID3-fake-mp3"

    monkeypatch.setitem(voice.TTS_PROVIDERS, "edge", broken)
    monkeypatch.setitem(voice.TTS_PROVIDERS, "google", works)
    monkeypatch.setattr(settings, "tts_providers", ["edge", "google"])
    _no_ffmpeg(monkeypatch)

    assert asyncio.run(voice.synthesize("Привет")) == (b"ID3-fake-mp3", "mp3")
    assert "No audio" in voice.last_tts_errors["edge"]
    assert "google" not in voice.last_tts_errors
    # Проверка одного провайдера (для /voicetest) не трогает остальные.
    assert asyncio.run(voice.synthesize("Привет", providers=["edge"])) is None


def test_recently_failed_provider_is_tried_last(monkeypatch):
    calls = []

    async def broken(text):
        calls.append("edge")
        raise edge_tts.exceptions.NoAudioReceived("No audio was received.")

    async def works(text):
        calls.append("google")
        return b"mp3"

    monkeypatch.setitem(voice.TTS_PROVIDERS, "edge", broken)
    monkeypatch.setitem(voice.TTS_PROVIDERS, "google", works)
    monkeypatch.setattr(settings, "tts_providers", ["edge", "google"])
    monkeypatch.setattr(voice, "_failed_until", {})
    _no_ffmpeg(monkeypatch)

    asyncio.run(voice.synthesize("раз"))
    asyncio.run(voice.synthesize("два"))
    assert calls == ["edge", "google", "google"]
    # Штраф прошёл — edge снова первый.
    voice._failed_until["edge"] = 0
    asyncio.run(voice.synthesize("три"))
    assert calls[-2:] == ["edge", "google"]


def test_openai_voice_used_only_with_key_and_costed(store, monkeypatch):
    calls = []

    async def openai(text):
        calls.append(text)
        return b"mp3-openai"

    async def free(text):
        return b"mp3-free"

    monkeypatch.setitem(voice.TTS_PROVIDERS, "openai", openai)
    monkeypatch.setitem(voice.TTS_PROVIDERS, "google", free)
    monkeypatch.setattr(settings, "tts_providers", ["openai", "google"])
    monkeypatch.setattr(voice, "_failed_until", {})
    _no_ffmpeg(monkeypatch)

    monkeypatch.setattr(settings, "openai_api_key", "")
    assert asyncio.run(voice.synthesize("Привет")) == (b"mp3-free", "mp3")
    assert calls == [] and "нет ключа" in voice.last_tts_errors["openai"]

    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    bot = FakeBot()
    asyncio.run(Assistant(bot, store, brain=None, owner_id=42).send_voice("Привет, Дони! " * 10))
    assert len(calls) == 1 and len(bot.voices) == 1
    tts = store.usage_since("2000-01-01 00:00:00")["tts"]
    assert tts["calls"] == 1 and tts["cost_usd"] > 0


def test_openai_tts_request_shape(monkeypatch):
    from fake_services import FakeServices

    async def scenario():
        fake = FakeServices()
        base = await fake.start()
        try:
            monkeypatch.setattr(settings, "openai_base_url", f"{base}/openai")
            monkeypatch.setattr(settings, "openai_api_key", "sk-test")
            audio = await voice._openai_tts("Привет, Дони!")
        finally:
            await fake.stop()
        return audio, fake.tts_requests

    audio, requests = asyncio.run(scenario())
    assert audio == b"ID3-fake-openai-mp3"
    body = requests[0]["body"]
    assert requests[0]["auth"] == "Bearer sk-test"
    assert body["model"] == "gpt-4o-mini-tts" and body["input"] == "Привет, Дони!"
    assert body["response_format"] == "mp3" and body["voice"] and body["instructions"]


def test_long_answer_is_spoken_in_short(monkeypatch):
    spoken = []

    async def provider(text):
        spoken.append(text)
        return b"mp3"

    monkeypatch.setitem(voice.TTS_PROVIDERS, "google", provider)
    monkeypatch.setattr(settings, "tts_providers", ["google"])
    monkeypatch.setattr(settings, "tts_max_chars", 100)
    _no_ffmpeg(monkeypatch)
    asyncio.run(voice.synthesize("Первое предложение. " * 20))
    assert len(spoken[0]) < 130 and spoken[0].endswith("Остальное — в тексте.")


def test_edge_retries_once_when_no_audio(monkeypatch):
    calls = []

    class FlakyCommunicate:
        def __init__(self, text, voice_name, rate):
            calls.append(text)

        async def stream(self):
            if len(calls) == 1:
                raise edge_tts.exceptions.NoAudioReceived("No audio was received.")
            yield {"type": "audio", "data": b"mp3-bytes"}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(voice.edge_tts, "Communicate", FlakyCommunicate)
    monkeypatch.setattr(voice.asyncio, "sleep", no_sleep)
    assert asyncio.run(voice._edge_tts("Привет")) == b"mp3-bytes"
    assert len(calls) == 2


class FakeBot:
    id = 1  # ChatActionSender пишет bot.id в лог

    def __init__(self):
        self.messages, self.voices = [], []

    async def send_chat_action(self, *args, **kwargs):
        return True

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append(text)

    async def send_voice(self, chat_id, voice_file, **kwargs):
        self.voices.append(voice_file)


def test_owner_is_told_once_a_day_when_voice_is_unavailable(store, monkeypatch):
    async def nothing(text):
        return None

    monkeypatch.setattr(assistant_mod, "synthesize", nothing)
    bot = FakeBot()
    a = Assistant(bot, store, brain=None, owner_id=42)
    asyncio.run(a.send_voice("Ответ"))
    asyncio.run(a.send_voice("Ещё ответ"))
    assert len(bot.messages) == 1 and "/voicetest" in bot.messages[0]
    assert bot.voices == []
