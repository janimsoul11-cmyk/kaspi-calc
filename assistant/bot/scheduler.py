"""Фоновый цикл: напоминания, утренний брифинг, вечерний разбор, обзоры, ночное сжатие памяти."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable

from . import prompts
from .assistant import Assistant
from .config import settings
from .db import iso_week_key
from .formatting import esc
from .handlers import reminder_keyboard
from .timeutil import from_utc_str, now_local, now_utc_str, short_dt
from .tools import next_cron_utc

log = logging.getLogger(__name__)

TICK_SECONDS = 20
GRACE = timedelta(hours=3)  # если бот был выключен, брифинг догоняем не позже чем через 3 часа
JOURNAL_LIMIT_CHARS = 60_000


@dataclass
class Job:
    name: str
    at: time
    when: Callable[[date], bool]
    marker: str
    prompt: str
    speak: bool = False


def default_jobs() -> list[Job]:
    return [
        Job("morning", settings.morning_time, lambda d: True,
            "[Автозапуск: утренний брифинг]", prompts.MORNING_PROMPT, speak=True),
        Job("monthly", settings.morning_time, lambda d: d.day == 1,
            "[Автозапуск: план на месяц]", prompts.MONTHLY_PROMPT),
        Job("weekly", settings.weekly_time, lambda d: d.weekday() == settings.weekly_day,
            "[Автозапуск: недельный обзор]", prompts.WEEKLY_PROMPT),
        Job("evening", settings.evening_time, lambda d: True,
            "[Автозапуск: вечерний разбор]", prompts.EVENING_PROMPT),
    ]


def is_due(job_at: time, last_run: str | None, now: datetime, grace: timedelta = GRACE) -> bool:
    scheduled = now.replace(hour=job_at.hour, minute=job_at.minute, second=0, microsecond=0)
    return last_run != now.date().isoformat() and scheduled <= now <= scheduled + grace


class Scheduler:
    def __init__(self, assistant: Assistant):
        self.assistant = assistant
        self.store = assistant.store
        self.jobs = default_jobs()

    async def run_forever(self) -> None:
        log.info("scheduler started")
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(TICK_SECONDS)

    async def tick(self) -> None:
        await self.fire_reminders()
        await self.run_daily_jobs()
        await self.run_rollups()

    # ---------- напоминания ----------
    async def fire_reminders(self) -> None:
        now = now_local()
        for r in self.store.due_reminders(now_utc_str()):
            fire_at = from_utc_str(r["fire_at"])
            text = f"⏰ <b>Напоминание</b>\n{esc(r['text'])}"
            if now - fire_at > timedelta(minutes=10):
                text += f"\n<i>(должно было сработать {short_dt(fire_at)})</i>"
            try:
                await self.assistant.send_text(text, reply_markup=reminder_keyboard(r["id"]))
            except Exception:
                log.exception("failed to send reminder #%s", r["id"])
                continue
            self.store.add_entry("assistant", "reminder", f"[Напоминание #{r['id']}] {r['text']}")
            if r.get("recurrence"):
                nxt = next_cron_utc(r["recurrence"], max(now, fire_at))
                self.store.update_reminder(r["id"], fire_at=nxt, last_fired_at=now_utc_str())
            else:
                self.store.update_reminder(r["id"], status="fired", last_fired_at=now_utc_str())

    # ---------- брифинги и обзоры ----------
    async def run_daily_jobs(self) -> None:
        now = now_local()
        for job in self.jobs:
            key = f"job:{job.name}"
            if not job.when(now.date()) or not is_due(job.at, self.store.kv_get(key), now):
                continue
            # Отмечаем до запуска: при ошибке не будем повторять каждые 20 секунд и жечь деньги.
            self.store.kv_set(key, now.date().isoformat())
            log.info("running job %s", job.name)
            await self.assistant.run_job(job.marker, job.prompt, settings.planning_effort, speak=job.speak)

    # ---------- ночное сжатие памяти ----------
    async def run_rollups(self) -> None:
        now = now_local()
        today = now.date()
        if now.time() < settings.rollup_time or self.store.kv_get("job:rollup") == today.isoformat():
            return
        self.store.kv_set("job:rollup", today.isoformat())

        # Дни за последнюю неделю, по которым ещё нет итога (если бот был выключен — догоняем).
        since = (today - timedelta(days=7)).isoformat()
        pending = [d for d in self.store.dates_with_entries(since, today.isoformat())
                   if not self.store.get_summary("day", d)]
        for day in pending[-3:]:
            await self.rollup_day(day)

        if today.weekday() == 0:
            last_monday = today - timedelta(days=7)
            await self.rollup_period("week", iso_week_key(last_monday), "day",
                                     last_monday.isoformat(), (today - timedelta(days=1)).isoformat())
        if today.day == 1:
            prev_month_end = today - timedelta(days=1)
            await self.rollup_period("month", prev_month_end.strftime("%Y-%m"), "day",
                                     prev_month_end.replace(day=1).isoformat(), prev_month_end.isoformat())

    async def rollup_day(self, day: str) -> None:
        lines = []
        for e in self.store.entries_for_date(day):
            who = "Владелец" if e["role"] == "user" else "Ассистент"
            lines.append(f"{from_utc_str(e['ts']):%H:%M} {who}: {e['content']}")
        journal = "\n".join(lines)[-JOURNAL_LIMIT_CHARS:]
        prompt = prompts.DAY_ROLLUP_PROMPT.format(day=day, journal=journal)
        log.info("rollup day %s (%d chars)", day, len(journal))
        await self.assistant.run_job(f"[rollup {day}]", prompt, settings.rollup_effort, notify=False)

    async def rollup_period(self, period: str, key: str, source: str, since: str, until: str) -> None:
        if self.store.get_summary(period, key):
            return
        rows = self.store.list_summaries(source, since, until, limit=62)
        if not rows:
            return
        summaries = "\n\n".join(f"== {r['period_key']} ==\n{r['content']}" for r in rows)
        name = {"week": "недели", "month": "месяца"}[period]
        prompt = prompts.PERIOD_ROLLUP_PROMPT.format(period=period, period_name=name, key=key, summaries=summaries)
        log.info("rollup %s %s from %d summaries", period, key, len(rows))
        await self.assistant.run_job(f"[rollup {period} {key}]", prompt, settings.rollup_effort, notify=False)
