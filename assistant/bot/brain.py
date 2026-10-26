"""«Мозг» ассистента: цикл Claude + инструменты, сборка контекста и учёт расходов."""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from . import prompts
from .config import FALLBACK_MODELS, MODEL_PRICES, settings
from .db import Store, month_start_utc
from .timeutil import from_utc_str, human_dt, now_local, short_dt
from .tools import TOOLS, execute

log = logging.getLogger(__name__)

MAX_STEPS = 15


class BudgetExceeded(Exception):
    pass


def estimate_cost(model: str, usage: Any) -> float:
    price_in, price_out, price_read, price_write = MODEL_PRICES.get(model, MODEL_PRICES["claude-opus-5-5"])
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        (usage.input_tokens or 0) * price_in
        + (usage.output_tokens or 0) * price_out
        + cache_read * price_read
        + cache_write * price_write
    ) / 1_000_000


def build_system(store: Store) -> list[dict]:
    core = (store.kv_get("core_memory") or "").strip() or prompts.EMPTY_PROFILE
    return [
        # Неизменный блок + инструменты (они идут раньше system) кешируются.
        {"type": "text", "text": prompts.SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": prompts.PROFILE_HEADER + core},
    ]


def build_context(store: Store) -> str:
    """Короткий снимок «что сейчас происходит» — кладётся в каждое сообщение пользователя."""
    now = now_local()
    lines = [f"Сейчас: {human_dt(now)} ({settings.timezone})."]

    def task_line(t: dict) -> str:
        due = f" (срок {t['due_date']}{' ' + t['due_time'] if t.get('due_time') else ''})" if t.get("due_date") else ""
        return f"• #{t['id']} P{t['priority']} [{t['area'] or '-'}] {t['title']}{due}"

    overdue = store.list_tasks(due="overdue", limit=15)
    today = store.list_tasks(due="today", limit=20)
    upcoming = [t for t in store.list_tasks(due="week", limit=30) if t["due_date"] and t["due_date"] > now.date().isoformat()]
    undated = store.list_tasks(due="none", limit=15)
    if overdue:
        lines.append("Просроченные задачи:\n" + "\n".join(map(task_line, overdue)))
    if today:
        lines.append("Задачи на сегодня:\n" + "\n".join(map(task_line, today)))
    if upcoming:
        lines.append("Ближайшие 7 дней:\n" + "\n".join(map(task_line, upcoming[:15])))
    if undated:
        lines.append("Без срока (по приоритету):\n" + "\n".join(map(task_line, sorted(undated, key=lambda t: t["priority"]))))
    if not (overdue or today or upcoming or undated):
        lines.append("Открытых задач нет.")

    reminders = store.upcoming_reminders(8)
    if reminders:
        lines.append("Ближайшие напоминания:\n" + "\n".join(
            f"• #{r['id']} {short_dt(from_utc_str(r['fire_at']))} — {r['text']}"
            + (f" (повтор {r['recurrence']})" if r.get("recurrence") else "")
            for r in reminders
        ))
    decisions = store.list_decisions("open", limit=8)
    if decisions:
        lines.append("Открытые решения:\n" + "\n".join(
            f"• #{d['id']} {d['question']} — срок {d['deadline'] or '?'}, по умолчанию: {d['default_option'] or '?'}"
            for d in decisions
        ))
    return "<context>\n" + "\n\n".join(lines) + "\n</context>"


def build_history(store: Store, limit: int, before_id: int | None = None) -> list[dict]:
    """Последние сообщения журнала как диалог (только текст, без служебных блоков).

    История каждый раз собирается заново и не содержит thinking-блоков, поэтому
    между ходами нечего «ломать»; внутри одного хода сообщения только дописываются.
    """
    messages: list[dict] = []
    for e in store.recent_entries(limit, before_id):
        role = "assistant" if e["role"] == "assistant" else "user"
        text = e["content"]
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n\n" + text
        else:
            messages.append({"role": role, "content": text})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


class Brain:
    def __init__(self, store: Store, client: Any | None = None):
        self.store = store
        self.client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)

    def month_cost(self) -> float:
        return self.store.cost_since(month_start_utc())

    def check_budget(self) -> None:
        if settings.monthly_budget_usd > 0 and self.month_cost() >= settings.monthly_budget_usd:
            raise BudgetExceeded(
                f"Месячный лимит ${settings.monthly_budget_usd:.0f} исчерпан "
                f"(потрачено ${self.month_cost():.2f}). Поднимите MONTHLY_BUDGET_USD или дождитесь нового месяца."
            )

    def _request_kwargs(self, effort: str) -> dict:
        kwargs: dict[str, Any] = {
            "model": settings.model,
            "max_tokens": 16000,
            "output_config": {"effort": effort},
            "cache_control": {"type": "ephemeral"},
        }
        if settings.model in FALLBACK_MODELS:
            # При отказе классификатора безопасности запрос автоматически
            # повторяется на рекомендованной Anthropic модели.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        return kwargs

    async def run(self, user_text: str, *, effort: str | None = None, with_history: bool = True,
                  attachments: list[dict] | None = None, entry_id: int | None = None) -> str:
        """Один «ход»: сообщение пользователя -> (инструменты)* -> текстовый ответ.

        entry_id — id уже сохранённой в журнал записи этого сообщения: история берётся до неё.
        """
        self.check_budget()
        history = build_history(self.store, settings.history_messages, entry_id) if with_history else []
        content: list[dict] = [{"type": "text", "text": build_context(self.store)}]
        content.extend(attachments or [])
        content.append({"type": "text", "text": user_text})
        messages: list[dict] = history + [{"role": "user", "content": content}]
        system = build_system(self.store)
        kwargs = self._request_kwargs(effort or settings.chat_effort)

        for _ in range(MAX_STEPS):
            response = await self.client.beta.messages.create(
                system=system, tools=TOOLS, messages=messages, **kwargs
            )
            cost = estimate_cost(response.model or settings.model, response.usage)
            self.store.add_usage(
                "llm", response.model, response.usage.input_tokens, response.usage.output_tokens,
                getattr(response.usage, "cache_read_input_tokens", 0) or 0,
                getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
                cost_usd=cost,
            )

            if response.stop_reason == "refusal":
                category = getattr(getattr(response, "stop_details", None), "category", None)
                log.warning("refusal, category=%s", category)
                return "Модель отказалась отвечать на этот запрос. Попробуй сформулировать иначе."

            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "tool_use" and tool_uses:
                messages.append({"role": "assistant", "content": response.content})
                results = []
                for block in tool_uses:
                    output, is_error = execute(self.store, block.name, block.input)
                    result = {"type": "tool_result", "tool_use_id": block.id, "content": output}
                    if is_error:
                        result["is_error"] = True
                    results.append(result)
                messages.append({"role": "user", "content": results})
                continue

            text = "\n".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "max_tokens":
                text += "\n\n<i>(ответ обрезан по длине)</i>"
            return text or "Готово."

        return "Слишком много шагов подряд — остановился. Уточни, что нужно сделать."
