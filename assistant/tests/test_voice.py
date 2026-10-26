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
