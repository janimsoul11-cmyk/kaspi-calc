"""Инструменты, которые Claude вызывает, чтобы работать с памятью ассистента."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from croniter import croniter

from .config import settings
from .db import Store
from .timeutil import from_utc_str, now_local, parse_date, parse_local, short_dt, to_utc_str

log = logging.getLogger(__name__)

NOTE_KINDS = ["idea", "dream", "goal", "thought", "question", "insight", "plan", "journal"]

_AREA = {"type": "string", "enum": settings.areas, "description": "Сфера жизни"}
_DATE = {"type": "string", "description": "Дата YYYY-MM-DD"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "add_task",
        "description": (
            "Создать задачу (то, что нужно сделать). Перед созданием проверь, нет ли такой же "
            "открытой задачи в контексте, — дубли не плодим."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Коротко и с глаголом: «Оплатить поставщику партию #12»"},
                "area": _AREA,
                "priority": {"type": "integer", "enum": [1, 2, 3], "description": "1 — важно/срочно, 2 — обычная, 3 — когда-нибудь"},
                "due_date": _DATE,
                "due_time": {"type": "string", "description": "Время HH:MM, если привязано ко времени"},
                "notes": {"type": "string", "description": "Детали, контекст, суммы"},
            },
            "required": ["title", "area"],
        },
    },
    {
        "name": "update_task",
        "description": "Изменить задачу: отметить выполненной (status=done), отменить, перенести срок, поменять приоритет.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["open", "done", "cancelled"]},
                "title": {"type": "string"},
                "area": _AREA,
                "priority": {"type": "integer", "enum": [1, 2, 3]},
                "due_date": {"type": "string", "description": "YYYY-MM-DD или 'none', чтобы убрать срок"},
                "due_time": {"type": "string", "description": "HH:MM или 'none'"},
                "notes": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "list_tasks",
        "description": "Получить список задач с фильтрами.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "done", "cancelled", "all"]},
                "area": _AREA,
                "due": {"type": "string", "enum": ["any", "today", "overdue", "today_or_overdue", "week", "none"]},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "add_reminder",
        "description": (
            "Поставить напоминание. Бот пришлёт сообщение в указанное локальное время. "
            "Для повторяющихся — cron-выражение в локальном времени (например '0 9 * * 1-5' — по будням в 9:00)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Что напомнить — так, чтобы было понятно без контекста"},
                "at": {"type": "string", "description": "Локальное время первого срабатывания 'YYYY-MM-DD HH:MM'"},
                "recurrence": {"type": "string", "description": "cron-выражение для повторов; не указывай для разового"},
                "task_id": {"type": "integer", "description": "Связанная задача, если есть"},
            },
            "required": ["text", "at"],
        },
    },
    {
        "name": "list_reminders",
        "description": "Ближайшие активные напоминания.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "cancel_reminder",
        "description": "Отменить напоминание.",
        "input_schema": {
            "type": "object",
            "properties": {"reminder_id": {"type": "integer"}},
            "required": ["reminder_id"],
        },
    },
    {
        "name": "save_note",
        "description": (
            "Сохранить заметку: idea — идея (бизнес, продукт, улучшение), dream — мечта, goal — цель "
            "(желательно с измеримым результатом и сроком), thought — мысль/рефлексия, question — вопрос, "
            "на который нужно найти ответ, insight — вывод/урок, plan — план на день/неделю/месяц, "
            "journal — событие дня, которое стоит запомнить."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": NOTE_KINDS},
                "content": {"type": "string", "description": "Суть своими словами, полно, но без воды"},
                "area": _AREA,
                "tags": {"type": "string", "description": "Теги через запятую"},
            },
            "required": ["kind", "content"],
        },
    },
    {
        "name": "list_notes",
        "description": "Список заметок (идеи, цели, мечты, планы...) с фильтрами.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": NOTE_KINDS},
                "area": _AREA,
                "status": {"type": "string", "enum": ["active", "done", "archived"]},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "update_note",
        "description": "Изменить заметку: дополнить текст, отметить цель достигнутой (done) или убрать в архив.",
        "input_schema": {
            "type": "object",
            "properties": {
                "note_id": {"type": "integer"},
                "content": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "done", "archived"]},
            },
            "required": ["note_id"],
        },
    },
    {
        "name": "add_decision",
        "description": (
            "Зафиксировать решение, которое владельцу нужно принять. Обязательно: срок, критерий "
            "«данных достаточно» и вариант по умолчанию (что делаем, если к сроку не решили)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "default_option": {"type": "string"},
                "enough_data": {"type": "string", "description": "Какие данные нужны, чтобы решить, и не больше"},
                "deadline": _DATE,
                "reversible": {"type": "boolean", "description": "Можно ли дёшево откатить решение"},
            },
            "required": ["question", "options", "default_option", "enough_data", "deadline", "reversible"],
        },
    },
    {
        "name": "resolve_decision",
        "description": "Отметить решение принятым.",
        "input_schema": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "integer"},
                "decision": {"type": "string"},
                "rationale": {"type": "string"},
            },
            "required": ["decision_id", "decision"],
        },
    },
    {
        "name": "list_decisions",
        "description": "Открытые или принятые решения.",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["open", "decided"]}},
        },
    },
    {
        "name": "search_memory",
        "description": (
            "Полнотекстовый поиск по всей памяти: журнал сообщений, заметки, итоги дней/недель. "
            "Используй, когда владелец спрашивает о прошлом («что я говорил про…», «когда мы…»). "
            "Давай несколько ключевых слов и синонимов, а не целую фразу."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_summaries",
        "description": "Итоги прошлых периодов: дни (key YYYY-MM-DD), недели (YYYY-Www), месяцы (YYYY-MM).",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["day", "week", "month"]},
                "since_key": {"type": "string"},
                "until_key": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["period"],
        },
    },
    {
        "name": "save_summary",
        "description": "Сохранить итог периода (используется в ночной обработке).",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["day", "week", "month"]},
                "period_key": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["period", "period_key", "content"],
        },
    },
    {
        "name": "update_core_memory",
        "description": (
            "Переписать целиком «ядро памяти» — профиль владельца, который ты видишь в каждом разговоре: "
            "кто он, семья, работа, проекты, цели, предпочтения, важные люди, устойчивые факты. "
            "Вызывай, только когда узнал новый устойчивый факт или старый устарел. Передай полный новый текст "
            "(старый + изменения), устаревшее — заменяй, а не дописывай. Лимит 4000 символов."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
    },
]

CORE_MEMORY_LIMIT = 4000


class ToolError(Exception):
    pass


def _fmt_task(t: dict) -> str:
    due = ""
    if t.get("due_date"):
        due = f", срок {t['due_date']}" + (f" {t['due_time']}" if t.get("due_time") else "")
    return f"#{t['id']} [{t['status']}] P{t['priority']} {t['area'] or '-'}: {t['title']}{due}" + (
        f" — {t['notes']}" if t.get("notes") else ""
    )


def _fmt_reminder(r: dict) -> str:
    rec = f" (повтор: {r['recurrence']})" if r.get("recurrence") else ""
    return f"#{r['id']} {short_dt(from_utc_str(r['fire_at']))}: {r['text']}{rec}"


def _fmt_note(n: dict) -> str:
    return f"#{n['id']} [{n['kind']}/{n['status']}] {n['area'] or '-'}: {n['content']}"


def _fmt_decision(d: dict) -> str:
    rev = "обратимое" if d.get("reversible") else "необратимое"
    s = (f"#{d['id']} [{d['status']}] {d['question']} | варианты: {'; '.join(d['options'])} | "
         f"по умолчанию: {d['default_option']} | данных достаточно, когда: {d['enough_data']} | "
         f"срок: {d['deadline']} | {rev}")
    if d.get("decision"):
        s += f" | решено: {d['decision']}"
    return s


def next_cron_utc(expr: str, after: datetime) -> str:
    """Следующее срабатывание cron (в локальной зоне) после момента `after` -> UTC-строка."""
    if not croniter.is_valid(expr):
        raise ToolError(f"Некорректное cron-выражение: {expr}")
    nxt = croniter(expr, after.astimezone(now_local().tzinfo)).get_next(datetime)
    return to_utc_str(nxt)


def _validate_date(value: str | None) -> str | None:
    if value in (None, "", "none"):
        return None
    return parse_date(value).isoformat()


def _validate_time(value: str | None) -> str | None:
    if value in (None, "", "none"):
        return None
    return datetime.strptime(value.strip(), "%H:%M").strftime("%H:%M")


def _area(value: str | None) -> str | None:
    if value and value not in settings.areas:
        return "personal" if "personal" in settings.areas else None
    return value


# ---------- исполнители ----------

def _add_task(s: Store, a: dict) -> str:
    t = s.add_task(
        title=a["title"].strip(), area=_area(a.get("area")), priority=int(a.get("priority", 2)),
        due_date=_validate_date(a.get("due_date")), due_time=_validate_time(a.get("due_time")),
        notes=a.get("notes"),
    )
    return "Создана задача " + _fmt_task(t)


def _update_task(s: Store, a: dict) -> str:
    task_id = int(a["task_id"])
    if not s.get_task(task_id):
        raise ToolError(f"Задача #{task_id} не найдена")
    fields: dict[str, Any] = {}
    for key in ("status", "title", "notes", "priority"):
        if key in a:
            fields[key] = a[key]
    if "area" in a:
        fields["area"] = _area(a["area"])
    if "due_date" in a:
        fields["due_date"] = _validate_date(a["due_date"])
    if "due_time" in a:
        fields["due_time"] = _validate_time(a["due_time"])
    return "Обновлено: " + _fmt_task(s.update_task(task_id, **fields))


def _list_tasks(s: Store, a: dict) -> str:
    rows = s.list_tasks(status=a.get("status", "open"), area=a.get("area"), due=a.get("due", "any"),
                        limit=int(a.get("limit", 50)))
    return "\n".join(_fmt_task(t) for t in rows) or "Задач нет."


def _add_reminder(s: Store, a: dict) -> str:
    at = parse_local(a["at"])
    recurrence = (a.get("recurrence") or "").strip() or None
    if recurrence and not croniter.is_valid(recurrence):
        raise ToolError(f"Некорректное cron-выражение: {recurrence}")
    if at < now_local() - timedelta(minutes=1):
        if not recurrence:
            raise ToolError(f"Время {a['at']} уже прошло. Уточни дату.")
        fire_at = next_cron_utc(recurrence, now_local())
    else:
        fire_at = to_utc_str(at)
    r = s.add_reminder(a["text"].strip(), fire_at, recurrence, a.get("task_id"))
    return "Напоминание поставлено " + _fmt_reminder(r)


def _list_reminders(s: Store, a: dict) -> str:
    rows = s.upcoming_reminders(int(a.get("limit", 20)))
    return "\n".join(_fmt_reminder(r) for r in rows) or "Активных напоминаний нет."


def _cancel_reminder(s: Store, a: dict) -> str:
    rid = int(a["reminder_id"])
    if not s.get_reminder(rid):
        raise ToolError(f"Напоминание #{rid} не найдено")
    s.update_reminder(rid, status="cancelled")
    return f"Напоминание #{rid} отменено."


def _save_note(s: Store, a: dict) -> str:
    if a["kind"] not in NOTE_KINDS:
        raise ToolError(f"kind должен быть одним из {NOTE_KINDS}")
    n = s.add_note(a["kind"], a["content"].strip(), _area(a.get("area")), a.get("tags"))
    return "Сохранено: " + _fmt_note(n)


def _list_notes(s: Store, a: dict) -> str:
    rows = s.list_notes(kind=a.get("kind"), area=a.get("area"), status=a.get("status", "active"),
                        limit=int(a.get("limit", 30)))
    return "\n".join(_fmt_note(n) for n in rows) or "Заметок нет."


def _update_note(s: Store, a: dict) -> str:
    note_id = int(a["note_id"])
    if not s.get_note(note_id):
        raise ToolError(f"Заметка #{note_id} не найдена")
    fields = {k: a[k] for k in ("content", "status") if k in a}
    return "Обновлено: " + _fmt_note(s.update_note(note_id, **fields))


def _add_decision(s: Store, a: dict) -> str:
    d = s.add_decision(
        a["question"], list(a.get("options") or []), a.get("default_option"), a.get("enough_data"),
        _validate_date(a.get("deadline")), a.get("reversible"),
    )
    return "Решение зафиксировано: " + _fmt_decision(d)


def _resolve_decision(s: Store, a: dict) -> str:
    did = int(a["decision_id"])
    if not s.get_decision(did):
        raise ToolError(f"Решение #{did} не найдено")
    return "Готово: " + _fmt_decision(s.resolve_decision(did, a["decision"], a.get("rationale")))


def _list_decisions(s: Store, a: dict) -> str:
    rows = s.list_decisions(a.get("status", "open"))
    return "\n".join(_fmt_decision(d) for d in rows) or "Решений нет."


def _search_memory(s: Store, a: dict) -> str:
    rows = s.search(a["query"], int(a.get("limit", 10)))
    if not rows:
        return "Ничего не найдено. Попробуй другие ключевые слова."
    lines = []
    for r in rows:
        when = r["ts"][:10] if r.get("ts") else ""
        lines.append(f"[{r['source']} {r['extra']} {when}] {r['snippet']}")
    return "\n".join(lines)


def _get_summaries(s: Store, a: dict) -> str:
    rows = s.list_summaries(a["period"], a.get("since_key"), a.get("until_key"), int(a.get("limit", 14)))
    return "\n\n".join(f"== {r['period']} {r['period_key']} ==\n{r['content']}" for r in rows) or "Итогов нет."


def _save_summary(s: Store, a: dict) -> str:
    s.save_summary(a["period"], a["period_key"], a["content"].strip())
    return f"Итог {a['period']} {a['period_key']} сохранён."


def _update_core_memory(s: Store, a: dict) -> str:
    content = a["content"].strip()
    if len(content) > CORE_MEMORY_LIMIT:
        raise ToolError(
            f"Слишком длинно ({len(content)} > {CORE_MEMORY_LIMIT}). Сожми: оставь только устойчивое и важное."
        )
    previous = s.kv_get("core_memory", "") or ""
    s.kv_set("core_memory_prev", previous)
    s.kv_set("core_memory", content)
    return "Ядро памяти обновлено."


EXECUTORS: dict[str, Callable[[Store, dict], str]] = {
    "add_task": _add_task,
    "update_task": _update_task,
    "list_tasks": _list_tasks,
    "add_reminder": _add_reminder,
    "list_reminders": _list_reminders,
    "cancel_reminder": _cancel_reminder,
    "save_note": _save_note,
    "list_notes": _list_notes,
    "update_note": _update_note,
    "add_decision": _add_decision,
    "resolve_decision": _resolve_decision,
    "list_decisions": _list_decisions,
    "search_memory": _search_memory,
    "get_summaries": _get_summaries,
    "save_summary": _save_summary,
    "update_core_memory": _update_core_memory,
}


def execute(store: Store, name: str, tool_input: Any) -> tuple[str, bool]:
    """Выполнить инструмент. Возвращает (результат, is_error)."""
    fn = EXECUTORS.get(name)
    if fn is None:
        return f"Неизвестный инструмент: {name}", True
    if not isinstance(tool_input, dict):
        return "Входные данные инструмента должны быть объектом JSON", True
    try:
        result = fn(store, tool_input)
        log.info("tool %s(%s) -> %s", name, json.dumps(tool_input, ensure_ascii=False)[:300], result[:200])
        return result, False
    except KeyError as e:
        return f"Не хватает обязательного поля: {e}", True
    except (ToolError, ValueError, TypeError) as e:
        return f"Ошибка: {e}", True
