"""Оркестрация: принять ввод -> сохранить в журнал -> Claude -> ответ текстом и голосом."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup
from aiogram.utils.chat_action import ChatActionSender

from .brain import Brain, BudgetExceeded
from .db import Store
from .formatting import speech_text, split_message, strip_html
from .timeutil import now_local
from .voice import synthesize

log = logging.getLogger(__name__)

VOICE_MODES = ("auto", "on", "off")


class Assistant:
    def __init__(self, bot: Bot, store: Store, brain: Brain, owner_id: int):
        self.bot = bot
        self.store = store
        self.brain = brain
        self.owner_id = owner_id
        # Один владелец — ходы обрабатываем строго по очереди, чтобы журнал не перемешивался.
        self.lock = asyncio.Lock()

    @property
    def voice_mode(self) -> str:
        return self.store.kv_get("voice_mode", "auto") or "auto"

    @property
    def show_transcript(self) -> bool:
        return (self.store.kv_get("show_transcript", "on") or "on") == "on"

    def should_speak(self, spoken_input: bool) -> bool:
        mode = self.voice_mode
        return mode == "on" or (mode == "auto" and spoken_input)

    async def send_text(self, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        parts = split_message(text)
        for i, part in enumerate(parts):
            markup = reply_markup if i == len(parts) - 1 else None
            try:
                await self.bot.send_message(self.owner_id, part, reply_markup=markup)
            except TelegramBadRequest:
                # Модель иногда ошибается с HTML — шлём как обычный текст.
                await self.bot.send_message(self.owner_id, strip_html(part), parse_mode=None, reply_markup=markup)

    async def send_voice(self, text: str) -> None:
        async with ChatActionSender.record_voice(chat_id=self.owner_id, bot=self.bot):
            audio = await synthesize(speech_text(text))
        if not audio:
            # Не молчим: раз в день сообщаем, что голос недоступен.
            today = now_local().date().isoformat()
            if self.store.kv_get("tts_fail_notice") != today:
                self.store.kv_set("tts_fail_notice", today)
                await self.send_text("🔇 Озвучка сейчас не работает, отвечаю текстом. Проверить — /voicetest")
            return
        data, fmt = audio
        try:
            await self.bot.send_voice(self.owner_id, BufferedInputFile(data, filename=f"reply.{fmt}"))
        except TelegramBadRequest:
            # Без ffmpeg голос получается в MP3; если Telegram не примет его как голосовое — шлём аудиофайлом.
            log.warning("send_voice rejected %s, sending as audio", fmt)
            await self.bot.send_audio(self.owner_id, BufferedInputFile(data, filename=f"reply.{fmt}"))

    async def reply(self, text: str, *, speak: bool) -> None:
        await self.send_text(text)
        if speak:
            await self.send_voice(text)

    async def handle_input(self, text: str, kind: str, *, spoken: bool = False,
                           attachments: list[dict] | None = None) -> None:
        """Главный сценарий для любого сообщения владельца."""
        async with self.lock:
            entry_id = self.store.add_entry("user", kind, text)
            try:
                async with ChatActionSender.typing(chat_id=self.owner_id, bot=self.bot):
                    answer = await self.brain.run(text, attachments=attachments, entry_id=entry_id)
            except BudgetExceeded as e:
                await self.send_text(f"💸 {e}\nСообщение сохранено в журнал — ничего не потеряно.")
                return
            except Exception:
                log.exception("brain.run failed")
                await self.send_text(
                    "⚠️ Не получилось обработать сообщение (ошибка API или сети). "
                    "Оно сохранено в журнал — повтори позже или напиши «обработай последнее»."
                )
                return
            self.store.add_entry("assistant", "text", answer)
        await self.reply(answer, speak=self.should_speak(spoken))

    async def run_job(self, marker: str, prompt: str, effort: str, *, speak: bool = False,
                      notify: bool = True) -> str | None:
        """Автозапуск (брифинг, разбор, обзор). marker — короткая пометка для журнала."""
        async with self.lock:
            entry_id = self.store.add_entry("user", "job", marker) if notify else None
            try:
                answer = await self.brain.run(prompt, effort=effort, entry_id=entry_id, with_history=notify)
            except BudgetExceeded as e:
                if notify:
                    await self.send_text(f"💸 {marker}: пропущено. {e}")
                return None
            except Exception:
                log.exception("job failed: %s", marker)
                if notify:
                    await self.send_text(f"⚠️ {marker}: не получилось выполнить (ошибка API или сети).")
                return None
            if notify:
                self.store.add_entry("assistant", "text", answer)
        if notify:
            await self.reply(answer, speak=speak and self.voice_mode != "off")
        return answer
