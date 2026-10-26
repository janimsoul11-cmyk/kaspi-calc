"""Точка входа: python -m bot"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError

from .assistant import Assistant
from .brain import Brain
from .config import settings
from .db import Store
from .handlers import COMMANDS, build_router, setup_router
from .scheduler import Scheduler

log = logging.getLogger("bot")


def seed_core_memory(store: Store) -> None:
    """Первый запуск: берём профиль из profile.md, если ядро памяти ещё пустое."""
    if store.kv_get("core_memory"):
        return
    if settings.profile_file.exists():
        text = settings.profile_file.read_text(encoding="utf-8").strip()
        if text:
            store.kv_set("core_memory", text[:4000])
            log.info("core memory seeded from %s", settings.profile_file)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    if not settings.telegram_token:
        sys.exit("Не задан TELEGRAM_BOT_TOKEN (см. .env.example)")

    session = None
    if settings.telegram_api_base:
        session = AiohttpSession(api=TelegramAPIServer.from_base(settings.telegram_api_base))
    bot = Bot(settings.telegram_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    if not settings.owner_id:
        log.warning("OWNER_ID не задан — режим настройки: бот покажет ваш Telegram ID")
        dp.include_router(setup_router())
        await dp.start_polling(bot)
        return

    if not settings.anthropic_api_key:
        sys.exit("Не задан ANTHROPIC_API_KEY (см. .env.example)")

    store = Store(settings.db_path)
    seed_core_memory(store)
    if voice := store.kv_get("tts_voice"):  # голос, выбранный через /voices
        settings.tts_voice = voice
    assistant = Assistant(bot, store, Brain(store), settings.owner_id)
    dp.include_router(build_router(assistant))
    await bot.set_my_commands(COMMANDS)

    scheduler_task = asyncio.create_task(Scheduler(assistant).run_forever())
    log.info("bot started: model=%s, tz=%s", settings.model, settings.timezone)
    try:
        await dp.start_polling(bot)
    finally:
        scheduler_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except TelegramUnauthorizedError:
        sys.exit("Telegram отклонил токен — проверьте TELEGRAM_BOT_TOKEN в .env")
    except KeyboardInterrupt:
        pass
