"""Форматирование: разбиение длинных сообщений, текст для озвучки, списки без участия LLM."""

from __future__ import annotations

import html
import re

from .db import Store
from .timeutil import from_utc_str, human_dt, now_local, short_dt

TG_LIMIT = 4000

_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0000FE0F\U0000200D]+"
)


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Режет по абзацам/строкам, чтобы уложиться в лимит Telegram."""
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def speech_text(text: str) -> str:
    """HTML-ответ -> текст для озвучки: без тегов, эмодзи, номеров #12 и маркеров списков."""
    text = strip_html(text)
    text = _EMOJI.sub("", text)
    text = re.sub(r"#\d+[ \t]*", "", text)
    text = re.sub(r"^[ \t]*[•\-–*][ \t]*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    # Пустая строка между абзацами -> точка, чтобы голос делал паузу.
    text = re.sub(r"([^.!?:;\n])\n{2,}", r"\1.\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


PRIORITY_MARK = {1: "🔴", 2: "🟡", 3: "⚪️"}


def _task_line(t: dict) -> str:
    due = ""
    if t.get("due_date"):
        due = f" <i>({t['due_date'][8:10]}.{t['due_date'][5:7]}{' ' + t['due_time'] if t.get('due_time') else ''})</i>"
    return f"{PRIORITY_MARK.get(t['priority'], '')} #{t['id']} {esc(t['title'])}{due}"


def render_today(store: Store) -> str:
    now = now_local()
    out = [f"<b>{esc(human_dt(now).capitalize())}</b>"]
    overdue = store.list_tasks(due="overdue")
    today = store.list_tasks(due="today")
    if overdue:
        out.append("\n<b>Просрочено:</b>\n" + "\n".join(map(_task_line, overdue)))
    if today:
        out.append("\n<b>На сегодня:</b>\n" + "\n".join(map(_task_line, today)))
    if not overdue and not today:
        out.append("\nНа сегодня задач со сроком нет.")
    today_iso = now.date().isoformat()
    reminders = [r for r in store.upcoming_reminders(30) if from_utc_str(r["fire_at"]).date().isoformat() == today_iso]
    if reminders:
        out.append("\n<b>Напоминания сегодня:</b>\n" + "\n".join(
            f"⏰ {from_utc_str(r['fire_at']):%H:%M} {esc(r['text'])}" for r in reminders
        ))
    decisions = [d for d in store.list_decisions("open") if d["deadline"] and d["deadline"] <= today_iso]
    if decisions:
        out.append("\n<b>Решения со сроком сегодня/просрочены:</b>\n" + "\n".join(
            f"⚖️ #{d['id']} {esc(d['question'])} — по умолчанию: {esc(d['default_option'] or '?')}" for d in decisions
        ))
    return "\n".join(out)


def render_tasks(store: Store) -> str:
    tasks = store.list_tasks(limit=200)
    if not tasks:
        return "Открытых задач нет."
    by_area: dict[str, list[dict]] = {}
    for t in tasks:
        by_area.setdefault(t["area"] or "other", []).append(t)
    out = [f"<b>Открытые задачи: {len(tasks)}</b>"]
    for area, items in by_area.items():
        out.append(f"\n<b>{esc(area)}</b>\n" + "\n".join(map(_task_line, items)))
    return "\n".join(out)


def render_reminders(store: Store) -> str:
    rows = store.upcoming_reminders(30)
    if not rows:
        return "Активных напоминаний нет."
    return "<b>Напоминания:</b>\n" + "\n".join(
        f"⏰ #{r['id']} {short_dt(from_utc_str(r['fire_at']))} {esc(r['text'])}"
        + (f" <i>(повтор {esc(r['recurrence'])})</i>" if r.get("recurrence") else "")
        for r in rows
    )


def render_notes(store: Store, kinds: tuple[str, ...] = ("idea", "dream", "goal")) -> str:
    titles = {"idea": "💡 Идеи", "dream": "✨ Мечты", "goal": "🎯 Цели", "question": "❓ Вопросы", "plan": "🗓 Планы"}
    out = []
    for kind in kinds:
        rows = store.list_notes(kind=kind, limit=15)
        if rows:
            out.append(f"<b>{titles.get(kind, kind)}</b>\n" + "\n".join(
                f"#{n['id']} {esc(n['content'][:300])}" for n in rows
            ))
    return "\n\n".join(out) or "Заметок пока нет."


def render_export(store: Store) -> str:
    """Markdown-выгрузка всей структурированной памяти (для бэкапа и чтения глазами)."""
    lines = [f"# Экспорт ассистента — {now_local():%Y-%m-%d %H:%M}", ""]
    lines += ["## Ядро памяти", store.kv_get("core_memory", "") or "(пусто)", ""]
    lines.append("## Открытые задачи")
    lines += [f"- #{t['id']} [{t['area']}] P{t['priority']} {t['title']} (срок: {t['due_date'] or '—'})"
              for t in store.list_tasks(limit=1000)]
    lines += ["", "## Заметки"]
    for kind in ("goal", "dream", "idea", "plan", "question", "insight", "thought", "journal"):
        rows = store.list_notes(kind=kind, limit=1000)
        if rows:
            lines.append(f"### {kind}")
            lines += [f"- #{n['id']} {n['content']}" for n in rows]
    lines += ["", "## Решения"]
    for status in ("open", "decided"):
        lines += [f"- #{d['id']} [{status}] {d['question']} → {d['decision'] or d['default_option']}"
                  for d in store.list_decisions(status, limit=1000)]
    lines += ["", "## Итоги дней"]
    lines += [f"### {s['period_key']}\n{s['content']}\n" for s in store.list_summaries("day", limit=100000)]
    return "\n".join(lines)
