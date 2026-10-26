import asyncio
from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest

from bot import brain as brain_mod
from bot.brain import Brain, BudgetExceeded, build_history
from bot.config import settings
from bot.formatting import speech_text, split_message
from bot.scheduler import Scheduler, is_due
from bot.timeutil import from_utc_str, now_local, to_utc_str


def usage(inp=1000, out=200):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0)


def response(content, stop_reason):
    return SimpleNamespace(content=content, stop_reason=stop_reason, model=settings.model,
                           usage=usage(), stop_details=None)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        # Снимок: Brain дописывает в тот же список messages между вызовами.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def test_agent_loop_runs_tools_and_returns_text(store):
    tool_use = SimpleNamespace(type="tool_use", id="tu_1", name="add_task",
                               input={"title": "Позвонить партнёру", "area": "work"})
    client = FakeClient([
        response([tool_use], "tool_use"),
        response([SimpleNamespace(type="text", text="✅ Задача #1: Позвонить партнёру")], "end_turn"),
    ])
    store.add_entry("user", "text", "старое сообщение")
    store.add_entry("assistant", "text", "старый ответ")
    entry_id = store.add_entry("user", "voice", "надо позвонить партнёру")

    answer = asyncio.run(Brain(store, client).run("надо позвонить партнёру", entry_id=entry_id))

    assert "Позвонить партнёру" in answer
    assert store.get_task(1)["title"] == "Позвонить партнёру"
    first, second = client.calls
    # История — до текущей записи; текущее сообщение — последним блоком вместе с контекстом.
    assert [m["role"] for m in first["messages"]] == ["user", "assistant", "user"]
    last = first["messages"][-1]["content"]
    assert last[0]["text"].startswith("<context>") and last[-1]["text"] == "надо позвонить партнёру"
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["output_config"] == {"effort": settings.chat_effort}
    # Ответ на tool_use: история только дописывается.
    assert second["messages"][-1]["content"][0]["tool_use_id"] == "tu_1"
    assert second["messages"][:3] == first["messages"]
    assert store.usage_since("2000-01-01 00:00:00")["llm"]["calls"] == 2


def test_fallback_only_for_supported_models(store, monkeypatch):
    b = Brain(store, FakeClient([]))
    monkeypatch.setattr(settings, "model", "claude-opus-5-5")
    kw = b._request_kwargs("medium")
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    monkeypatch.setattr(settings, "model", "claude-haiku-5-5")
    assert "fallbacks" not in b._request_kwargs("medium")


def test_refusal_and_budget(store, monkeypatch):
    client = FakeClient([response([], "refusal")])
    assert "отказалась" in asyncio.run(Brain(store, client).run("привет"))

    monkeypatch.setattr(settings, "monthly_budget_usd", 0.0001)
    with pytest.raises(BudgetExceeded):
        asyncio.run(Brain(store, FakeClient([])).run("привет"))


def test_history_merges_roles_and_starts_with_user(store):
    store.add_entry("assistant", "reminder", "[Напоминание #1] вода")
    store.add_entry("user", "text", "a")
    store.add_entry("user", "text", "b")
    store.add_entry("assistant", "text", "c")
    history = build_history(store, 10)
    assert history == [{"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"}]


def test_estimate_cost():
    cost = brain_mod.estimate_cost("claude-opus-5-5", usage(1_000_000, 100_000))
    assert cost == pytest.approx(4.0 + 2.0)


def test_is_due_window():
    now = datetime(2026, 10, 25, 7, 30, tzinfo=now_local().tzinfo)
    assert is_due(time(7, 0), None, now)
    assert not is_due(time(7, 0), "2026-10-25", now)
    assert not is_due(time(8, 0), None, now)
    assert not is_due(time(2, 0), None, now)  # пропущено больше чем на 3 часа


class FakeAssistant:
    def __init__(self, store):
        self.store = store
        self.sent = []
        self.jobs = []

    async def send_text(self, text, reply_markup=None):
        self.sent.append(text)

    async def run_job(self, marker, prompt, effort, *, speak=False, notify=True):
        self.jobs.append((marker, notify))


def test_reminders_fire_and_recurring_reschedules(store):
    past = now_local() - timedelta(minutes=1)
    store.add_reminder("Разовое", to_utc_str(past))
    store.add_reminder("Каждый день", to_utc_str(past), "0 9 * * *")
    store.add_reminder("Будущее", to_utc_str(now_local() + timedelta(hours=1)))
    fake = FakeAssistant(store)
    asyncio.run(Scheduler(fake).fire_reminders())

    assert len(fake.sent) == 2
    assert store.get_reminder(1)["status"] == "fired"
    recurring = store.get_reminder(2)
    assert recurring["status"] == "active" and from_utc_str(recurring["fire_at"]) > now_local()
    assert store.get_reminder(3)["status"] == "active"
    asyncio.run(Scheduler(fake).fire_reminders())
    assert len(fake.sent) == 2  # повторно не шлём


def test_rollup_summarizes_missed_days_once(store, monkeypatch):
    monkeypatch.setattr(settings, "rollup_time", time(0, 0))
    yesterday = (now_local() - timedelta(days=1)).date().isoformat()
    store.conn.execute(
        "INSERT INTO entries(ts, local_date, role, kind, content) VALUES ('2026-01-01 00:00:00', ?, 'user', 'text', 'x')",
        (yesterday,),
    )
    fake = FakeAssistant(store)
    sched = Scheduler(fake)
    asyncio.run(sched.run_rollups())
    asyncio.run(sched.run_rollups())
    day_jobs = [j for j in fake.jobs if j[0] == f"[rollup {yesterday}]"]
    assert day_jobs == [(f"[rollup {yesterday}]", False)]


def test_formatting_helpers():
    parts = split_message("строка\n" * 2000, limit=4000)
    assert all(len(p) <= 4000 for p in parts) and len(parts) > 1
    spoken = speech_text("<b>План</b>\n• #12 Оплатить &amp; отправить 🔴\n\n• Тренировка")
    assert spoken == "План\nОплатить & отправить.\nТренировка"
