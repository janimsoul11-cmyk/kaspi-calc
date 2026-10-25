from datetime import timedelta

from bot.db import fts_query
from bot.timeutil import from_utc_str, now_local
from bot.tools import TOOLS, EXECUTORS, execute


def test_every_tool_has_executor():
    assert {t["name"] for t in TOOLS} == set(EXECUTORS)


def test_fts_query_stems_russian_words():
    assert fts_query("поставщику оплату") == '"поставщ"* OR "опла"*'
    assert fts_query("  ") == ""


def test_search_finds_other_word_forms(store):
    store.add_entry("user", "voice", "Надо оплатить поставщику из Гуанчжоу 5000 долларов")
    store.add_note("idea", "Сделать в продукте алерт на изменение цены конкурента")
    store.save_summary("day", "2026-10-24", "Обсуждали оплату поставщиков и рекламу")
    hits = store.search("поставщик оплата")
    sources = {h["source"] for h in hits}
    assert {"journal", "summary"} <= sources
    assert store.search("конкурентов")[0]["source"] == "note"


def test_summary_upsert_keeps_single_row(store):
    store.save_summary("day", "2026-10-24", "первый вариант")
    store.save_summary("day", "2026-10-24", "второй вариант")
    rows = store.list_summaries("day")
    assert len(rows) == 1 and rows[0]["content"] == "второй вариант"
    assert store.search("первый") == []


def test_task_lifecycle(store):
    today = now_local().date()
    out, err = execute(store, "add_task", {
        "title": "Оплатить партию", "area": "work", "priority": 1, "due_date": today.isoformat(),
    })
    assert not err and "#1" in out
    execute(store, "add_task", {"title": "Старое дело", "area": "work",
                                "due_date": (today - timedelta(days=2)).isoformat()})
    execute(store, "add_task", {"title": "Когда-нибудь", "area": "unknown-area"})
    assert [t["title"] for t in store.list_tasks(due="today")] == ["Оплатить партию"]
    assert [t["title"] for t in store.list_tasks(due="overdue")] == ["Старое дело"]
    assert store.list_tasks(due="none")[0]["area"] == "personal"

    out, err = execute(store, "update_task", {"task_id": 1, "status": "done"})
    assert not err and "[done]" in out
    assert store.get_task(1)["done_at"]
    out, err = execute(store, "update_task", {"task_id": 2, "due_date": "none"})
    assert store.get_task(2)["due_date"] is None

    out, err = execute(store, "update_task", {"task_id": 99, "status": "done"})
    assert err and "не найдена" in out


def test_reminders(store):
    soon = (now_local() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    out, err = execute(store, "add_reminder", {"text": "Созвон с поставщиком", "at": soon})
    assert not err
    r = store.get_reminder(1)
    assert from_utc_str(r["fire_at"]).strftime("%Y-%m-%d %H:%M") == soon

    past = (now_local() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    out, err = execute(store, "add_reminder", {"text": "Прошлое", "at": past})
    assert err and "прошло" in out

    # Повторяющееся с прошедшей датой старта переносится на ближайшее срабатывание.
    out, err = execute(store, "add_reminder", {"text": "Таблица продаж", "at": past, "recurrence": "0 9 * * 1-5"})
    assert not err
    r = store.get_reminder(2)
    nxt = from_utc_str(r["fire_at"])
    assert nxt > now_local() and nxt.hour == 9 and nxt.weekday() < 5

    out, err = execute(store, "add_reminder", {"text": "x", "at": soon, "recurrence": "каждый день"})
    assert err and "cron" in out

    out, err = execute(store, "cancel_reminder", {"reminder_id": 1})
    assert not err and store.get_reminder(1)["status"] == "cancelled"


def test_notes_decisions_and_core_memory(store):
    out, err = execute(store, "save_note", {"kind": "dream", "content": "Свой дом за городом", "area": "family"})
    assert not err
    out, err = execute(store, "save_note", {"kind": "wish", "content": "x"})
    assert err

    out, err = execute(store, "add_decision", {
        "question": "Брать ли второй склад?", "options": ["Да", "Нет", "Через 3 месяца"],
        "default_option": "Через 3 месяца", "enough_data": "Оборот склада за 2 месяца",
        "deadline": "2026-11-15", "reversible": False,
    })
    assert not err and "необратимое" in out
    out, err = execute(store, "resolve_decision", {"decision_id": 1, "decision": "Через 3 месяца"})
    assert not err and store.list_decisions("open") == []

    out, err = execute(store, "update_core_memory", {"content": "Имя: Тест"})
    assert not err and store.kv_get("core_memory") == "Имя: Тест"
    out, err = execute(store, "update_core_memory", {"content": "x" * 5000})
    assert err and store.kv_get("core_memory") == "Имя: Тест"


def test_execute_rejects_bad_input(store):
    assert execute(store, "nope", {})[1]
    out, err = execute(store, "add_task", {"area": "work"})
    assert err and "title" in out
    assert execute(store, "add_task", "not a dict")[1]


def test_backup_contains_latest_writes(tmp_path):
    import sqlite3

    from bot.db import Store

    s = Store(tmp_path / "a.db")
    s.add_task("Свежая задача", "work")
    backup = tmp_path / "copy.db"
    backup.write_bytes(s.backup_bytes())
    row = sqlite3.connect(backup).execute("SELECT title FROM tasks").fetchone()
    assert row == ("Свежая задача",)


def test_setup_wizard_renders_env_from_template():
    from pathlib import Path

    from bot.setup import render_env

    template = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    env = render_env(template, {"TELEGRAM_BOT_TOKEN": "1:abc", "CLAUDE_MODEL": "claude-sonnet-5-5", "OWNER_ID": ""})
    lines = env.splitlines()
    assert "TELEGRAM_BOT_TOKEN=1:abc" in lines and "CLAUDE_MODEL=claude-sonnet-5-5" in lines
    assert "OWNER_ID=" in lines
    # Комментарии с примерами не трогаем
    assert any(line.startswith("# Альтернатива — OpenAI") for line in lines)
    assert len(lines) == len(template.splitlines())
