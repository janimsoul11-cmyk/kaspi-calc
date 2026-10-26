"""Сквозной тест: настоящий бот (bot.__main__.main) против локальных заглушек Telegram, STT и Claude.

Проверяет всю цепочку: запуск и команды меню, утренний брифинг с голосом, ночной итог дня,
текст → задача, чужой пользователь → тишина, голосовое → расшифровка → напоминание → голосовой ответ,
срабатывание напоминания с кнопками, «отложить» кнопкой, /today, /stats, /export, режим настройки.
"""

import asyncio
import contextlib
import json
import sqlite3
from datetime import time, timedelta

from bot import __main__ as bot_main
from bot import assistant as assistant_mod
from bot import scheduler as scheduler_mod
from bot.config import settings
from bot.db import Store
from bot.prompts import MORNING_PROMPT
from bot.timeutil import from_utc_str, now_local

from fake_services import FakeServices

OWNER = 42
STRANGER = 7


def _configure(monkeypatch, tmp_path, base: str, owner_id: int) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"{base}/anthropic")
    for name, value in {
        "telegram_token": "123456:TEST",
        "telegram_api_base": base,
        "owner_id": owner_id,
        "anthropic_api_key": "test-key",
        "model": "claude-opus-5-5",
        "stt_api_key": "stt-key",
        "stt_base_url": f"{base}/stt",
        "data_dir": tmp_path,
        "profile_file": tmp_path / "profile.md",
        "rollup_time": time(0, 0),
        "monthly_budget_usd": 30.0,
    }.items():
        monkeypatch.setattr(settings, name, value)
    # Ускоряем планировщик и оставляем один автозапуск — утренний брифинг, «пора» сразу.
    monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.3)
    monkeypatch.setattr(scheduler_mod, "default_jobs", lambda: [scheduler_mod.Job(
        "morning", time(0, 0), lambda d: True, "[Автозапуск: утренний брифинг]", MORNING_PROMPT, speak=True)])
    monkeypatch.setattr(scheduler_mod, "is_due",
                        lambda job_at, last_run, now, grace=None: last_run != now.date().isoformat())

    async def fake_synthesize(text):
        return b"OggS-fake-voice", "ogg"

    monkeypatch.setattr(assistant_mod, "synthesize", fake_synthesize)


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, SystemExit):
        await task


def test_full_flow(tmp_path, monkeypatch):
    asyncio.run(_full_flow(tmp_path, monkeypatch))


async def _full_flow(tmp_path, monkeypatch):
    fake = FakeServices()
    base = await fake.start()
    _configure(monkeypatch, tmp_path, base, OWNER)
    (tmp_path / "profile.md").write_text("Имя: Тест. Работа: продажи на маркетплейсе.", encoding="utf-8")

    # Вчерашняя запись в журнале — ночная обработка должна сделать по ней итог дня.
    yesterday = (now_local() - timedelta(days=1)).date().isoformat()
    Store(settings.db_path).conn.execute(
        "INSERT INTO entries(ts, local_date, role, kind, content) VALUES ('2026-01-01 00:00:00', ?, 'user', 'voice', 'вчера обсуждали поставки')",
        (yesterday,),
    )

    task = asyncio.create_task(bot_main.main())
    try:
        # 1. Запуск: меню команд и профиль из файла.
        await fake.wait_for(lambda: fake.sent("setMyCommands"), what="setMyCommands")
        commands = json.loads(fake.sent("setMyCommands")[0]["params"]["commands"])
        assert {c["command"] for c in commands} >= {"today", "plan", "tasks", "voice", "stats", "export"}
        store = Store(settings.db_path)
        assert store.kv_get("core_memory").startswith("Имя: Тест")

        # 2. Утренний брифинг: текст + голос.
        await fake.wait_for(lambda: any("План дня" in c["params"].get("text", "") for c in fake.sent("sendMessage", OWNER)),
                            what="утренний брифинг")
        await fake.wait_for(lambda: fake.sent("sendVoice", OWNER), what="голос брифинга")
        assert fake.file_of(fake.sent("sendVoice", OWNER)[0], "voice")["filename"] == "reply.ogg"

        # 3. Ночной итог вчерашнего дня через инструмент save_summary.
        await fake.wait_for(lambda: store.get_summary("day", yesterday), what="итог дня")

        # 4. Текст → задача.
        fake.push(fake.message(OWNER, "Создай задачу: протестировать бота"))
        await fake.wait_for(lambda: store.list_tasks(), what="задача в базе")
        await fake.wait_for(lambda: any("Создана задача" in c["params"].get("text", "")
                                        for c in fake.sent("sendMessage", OWNER)), what="подтверждение задачи")
        assert store.list_tasks()[0]["title"] == "Протестировать бота"

        # 5. Чужой пользователь — бот молчит.
        fake.push(fake.message(STRANGER, "Привет, создай задачу"))
        await asyncio.sleep(1.5)
        assert not fake.sent("sendMessage", STRANGER)
        assert len(store.list_tasks()) == 1

        # 6. Голосовое → расшифровка → напоминание → ответ текстом и голосом.
        voices_before = len(fake.sent("sendVoice", OWNER))
        fake.files["voice-1"] = b"OggS" + b"\x00" * 2000
        fake.push(fake.message(OWNER, voice={"file_id": "voice-1", "file_unique_id": "uv1",
                                             "duration": 7, "mime_type": "audio/ogg"}))
        await fake.wait_for(lambda: any(c["params"].get("text", "").startswith("🎙")
                                        for c in fake.sent("sendMessage", OWNER)), what="расшифровка")
        stt = fake.stt_requests[0]
        assert stt["auth"] == "Bearer stt-key" and stt["language"] == "ru"
        assert stt["file"]["filename"] == "voice.ogg" and stt["file"]["size"] == 2004
        await fake.wait_for(lambda: store.upcoming_reminders() or store.get_reminder(1), what="напоминание в базе")
        await fake.wait_for(lambda: len(fake.sent("sendVoice", OWNER)) > voices_before, what="голосовой ответ")

        # 7. Напоминание срабатывает с кнопками.
        reminder_msg = await fake.wait_for(
            lambda: [c for c in fake.sent("sendMessage", OWNER) if c["params"].get("text", "").startswith("⏰")],
            what="сработавшее напоминание")
        markup = json.loads(reminder_msg[0]["params"]["reply_markup"])
        buttons = [b["callback_data"] for b in markup["inline_keyboard"][0]]
        assert buttons == ["r:done:1", "r:snz:1:15", "r:snz:1:60", "r:snz:1:tomorrow"]
        assert store.get_reminder(1)["status"] == "fired"

        # 8. Кнопка «+15 мин».
        fake.push({"callback_query": {
            "id": "cb-1", "from": {"id": OWNER, "is_bot": False, "first_name": "Тест"}, "chat_instance": "ci",
            "data": "r:snz:1:15",
            "message": {"message_id": 5, "date": 0, "chat": {"id": OWNER, "type": "private"}, "text": "⏰"},
        }})
        await fake.wait_for(lambda: fake.sent("answerCallbackQuery"), what="ответ на кнопку")
        await fake.wait_for(lambda: fake.sent("editMessageReplyMarkup"), what="кнопки убраны")
        reminder = store.get_reminder(1)
        assert reminder["status"] == "active"
        delay = from_utc_str(reminder["fire_at"]) - now_local()
        assert timedelta(minutes=13) < delay <= timedelta(minutes=15)

        # 9. Быстрые команды без ИИ.
        for command, expected in (("/today", "На сегодня"), ("/stats", "Расходы"), ("/tasks", "Протестировать бота")):
            before = len(fake.sent("sendMessage", OWNER))
            fake.push(fake.message(OWNER, command))
            await fake.wait_for(lambda: len(fake.sent("sendMessage", OWNER)) > before, what=command)
            assert expected in fake.sent("sendMessage", OWNER)[-1]["params"]["text"]
        fake.push(fake.message(OWNER, "/export"))
        await fake.wait_for(lambda: len(fake.sent("sendDocument", OWNER)) == 2, what="/export")
        db_file = fake.file_of(fake.sent("sendDocument", OWNER)[1], "document")["data"]
        (tmp_path / "export.db").write_bytes(db_file)
        assert sqlite3.connect(tmp_path / "export.db").execute("SELECT COUNT(*) FROM tasks").fetchone() == (1,)

        # 10. Запросы к Claude: бета-заголовок fallback, кеш, инструменты, thinking возвращается как есть.
        first = fake.claude_requests[0]
        assert "server-side-fallback-2026-07-01" in first["headers"].get("anthropic-beta", "")
        assert first["body"]["fallbacks"] == "default"
        assert first["body"]["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert len(first["body"]["tools"]) == 16
        followups = [r["body"] for r in fake.claude_requests
                     if isinstance(r["body"]["messages"][-1]["content"], list)
                     and r["body"]["messages"][-1]["content"][0].get("type") == "tool_result"]
        assert followups and followups[0]["messages"][-2]["content"][0]["type"] == "thinking"
        assert "Имя: Тест" in first["body"]["system"][1]["text"]
    finally:
        await _stop(task)
        await fake.stop()


def test_setup_mode_reports_id(tmp_path, monkeypatch):
    asyncio.run(_setup_mode(tmp_path, monkeypatch))


async def _setup_mode(tmp_path, monkeypatch):
    fake = FakeServices()
    base = await fake.start()
    _configure(monkeypatch, tmp_path, base, owner_id=0)
    task = asyncio.create_task(bot_main.main())
    try:
        fake.push(fake.message(STRANGER, "привет"))
        reply = await fake.wait_for(lambda: fake.sent("sendMessage", STRANGER), what="ответ в режиме настройки")
        assert f"<code>{STRANGER}</code>" in reply[0]["params"]["text"]
        assert not fake.claude_requests
    finally:
        await _stop(task)
        await fake.stop()


def test_voice_picker(tmp_path, monkeypatch):
    asyncio.run(_voice_picker(tmp_path, monkeypatch))


async def _voice_picker(tmp_path, monkeypatch):
    from bot import handlers as handlers_mod

    async def fake_sample(text, voice):
        return (b"OggS-" + voice.encode(), "ogg")

    fake = FakeServices()
    base = await fake.start()
    _configure(monkeypatch, tmp_path, base, OWNER)
    monkeypatch.setattr(handlers_mod, "edge_sample", fake_sample)
    monkeypatch.setattr(settings, "tts_voice", "ru-RU-DmitryNeural")
    task = asyncio.create_task(bot_main.main())
    try:
        await fake.wait_for(lambda: fake.sent("setMyCommands"), what="запуск")
        fake.push(fake.message(OWNER, "/voices"))
        samples = await fake.wait_for(lambda: len(fake.sent("sendVoice", OWNER)) >= 6 and fake.sent("sendVoice", OWNER),
                                      what="6 образцов голосов")
        andrew = next(c for c in samples if "Andrew" in c["params"].get("caption", ""))
        button = json.loads(andrew["params"]["reply_markup"])["inline_keyboard"][0][0]
        assert button["callback_data"] == "voice:en-US-AndrewMultilingualNeural"

        fake.push({"callback_query": {
            "id": "cb-v", "from": {"id": OWNER, "is_bot": False, "first_name": "Тест"}, "chat_instance": "ci",
            "data": button["callback_data"],
            "message": {"message_id": 7, "date": 0, "chat": {"id": OWNER, "type": "private"}},
        }})
        await fake.wait_for(lambda: settings.tts_voice == "en-US-AndrewMultilingualNeural", what="голос выбран")
        assert Store(settings.db_path).kv_get("tts_voice") == "en-US-AndrewMultilingualNeural"
    finally:
        await _stop(task)
        await fake.stop()
