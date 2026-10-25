"""Хранилище на SQLite: журнал сообщений, задачи, напоминания, заметки, решения, итоги, расходы.

Принцип (как в iva / Hermes Agent): всё входящее сначала пишется в журнал дословно,
а сжатие в итоги дня/недели/месяца делается позже по расписанию.
"""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .timeutil import now_local, now_utc_str

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    local_date TEXT NOT NULL,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_date ON entries(local_date);
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(content, content='entries', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    notes TEXT,
    area TEXT,
    priority INTEGER NOT NULL DEFAULT 2,
    due_date TEXT,
    due_time TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    done_at TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    fire_at TEXT NOT NULL,
    recurrence TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    task_id INTEGER,
    created_at TEXT NOT NULL,
    last_fired_at TEXT
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders(status, fire_at);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    area TEXT,
    tags TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(content, content='notes', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE OF content ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO notes_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    question TEXT NOT NULL,
    options TEXT,
    default_option TEXT,
    enough_data TEXT,
    deadline TEXT,
    reversible INTEGER,
    status TEXT NOT NULL DEFAULT 'open',
    decision TEXT,
    rationale TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS summaries (
    id INTEGER PRIMARY KEY,
    period TEXT NOT NULL,
    period_key TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(period, period_key)
);
CREATE VIRTUAL TABLE IF NOT EXISTS summaries_fts USING fts5(content, content='summaries', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS summaries_ai AFTER INSERT ON summaries BEGIN
    INSERT INTO summaries_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS summaries_ad AFTER DELETE ON summaries BEGIN
    INSERT INTO summaries_fts(summaries_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read INTEGER DEFAULT 0,
    cache_write INTEGER DEFAULT 0,
    seconds REAL DEFAULT 0,
    cost_usd REAL DEFAULT 0
);
"""

TASK_STATUSES = ("open", "done", "cancelled")
NOTE_STATUSES = ("active", "done", "archived")


def fts_query(text: str) -> str:
    """Запрос для FTS5 с грубым «стеммингом» для русского: обрезаем окончания и ищем по префиксу.

    'поставщику оплату' -> '"постав"* OR "оплат"*'
    """
    words = re.findall(r"[\w-]+", text.lower())
    terms = []
    for word in words:
        word = word.strip("-")
        if len(word) < 2:
            continue
        stem = word if len(word) <= 4 else word[: max(4, len(word) - 3)]
        terms.append(f'"{stem}"*')
    return " OR ".join(dict.fromkeys(terms))


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    # ---------- журнал ----------
    def add_entry(self, role: str, kind: str, content: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO entries(ts, local_date, role, kind, content) VALUES (?,?,?,?,?)",
            (now_utc_str(), now_local().date().isoformat(), role, kind, content),
        )
        return int(cur.lastrowid)

    def recent_entries(self, limit: int, before_id: int | None = None) -> list[dict]:
        rows = _rows(self.conn.execute(
            "SELECT * FROM entries WHERE id < ? ORDER BY id DESC LIMIT ?",
            (before_id if before_id is not None else 2**62, limit),
        ))
        return list(reversed(rows))

    def entries_for_date(self, day: str) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM entries WHERE local_date = ? ORDER BY id", (day,)
        ))

    def dates_with_entries(self, since: str, until: str) -> list[str]:
        cur = self.conn.execute(
            "SELECT DISTINCT local_date FROM entries WHERE local_date >= ? AND local_date < ? "
            "AND role = 'user' AND kind != 'job' ORDER BY local_date",
            (since, until),
        )
        return [r[0] for r in cur.fetchall()]

    # ---------- задачи ----------
    def add_task(self, title: str, area: str | None = None, priority: int = 2,
                 due_date: str | None = None, due_time: str | None = None,
                 notes: str | None = None) -> dict:
        now = now_utc_str()
        cur = self.conn.execute(
            "INSERT INTO tasks(title, notes, area, priority, due_date, due_time, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (title, notes, area, priority, due_date, due_time, now, now),
        )
        return self.get_task(int(cur.lastrowid))

    def get_task(self, task_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def update_task(self, task_id: int, **fields: Any) -> dict | None:
        allowed = {"title", "notes", "area", "priority", "due_date", "due_time", "status"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return self.get_task(task_id)
        if "status" in fields:
            if fields["status"] not in TASK_STATUSES:
                raise ValueError(f"status должен быть одним из {TASK_STATUSES}")
            fields["done_at"] = now_utc_str() if fields["status"] == "done" else None
        fields["updated_at"] = now_utc_str()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (*fields.values(), task_id))
        return self.get_task(task_id)

    def list_tasks(self, status: str = "open", area: str | None = None, due: str = "any",
                   limit: int = 50) -> list[dict]:
        today = now_local().date().isoformat()
        where, params = [], []
        if status != "all":
            where.append("status = ?")
            params.append(status)
        if area:
            where.append("area = ?")
            params.append(area)
        if due == "today":
            where.append("due_date = ?")
            params.append(today)
        elif due == "overdue":
            where.append("due_date < ?")
            params.append(today)
        elif due == "today_or_overdue":
            where.append("due_date <= ?")
            params.append(today)
        elif due == "week":
            where.append("due_date <= date(?, '+7 day')")
            params.append(today)
        elif due == "none":
            where.append("due_date IS NULL")
        sql = "SELECT * FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        order = "done_at DESC" if status == "done" else (
            "due_date IS NULL, due_date, due_time IS NULL, due_time, priority, id"
        )
        sql += f" ORDER BY {order} LIMIT ?"
        params.append(limit)
        return _rows(self.conn.execute(sql, params))

    def tasks_done_between(self, start_utc: str, end_utc: str) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM tasks WHERE status = 'done' AND done_at >= ? AND done_at < ? ORDER BY done_at",
            (start_utc, end_utc),
        ))

    # ---------- напоминания ----------
    def add_reminder(self, text: str, fire_at_utc: str, recurrence: str | None = None,
                     task_id: int | None = None) -> dict:
        cur = self.conn.execute(
            "INSERT INTO reminders(text, fire_at, recurrence, task_id, created_at) VALUES (?,?,?,?,?)",
            (text, fire_at_utc, recurrence, task_id, now_utc_str()),
        )
        return self.get_reminder(int(cur.lastrowid))

    def get_reminder(self, reminder_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return dict(row) if row else None

    def due_reminders(self, now_utc: str) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM reminders WHERE status = 'active' AND fire_at <= ? ORDER BY fire_at",
            (now_utc,),
        ))

    def upcoming_reminders(self, limit: int = 20) -> list[dict]:
        return _rows(self.conn.execute(
            "SELECT * FROM reminders WHERE status = 'active' ORDER BY fire_at LIMIT ?", (limit,)
        ))

    def update_reminder(self, reminder_id: int, **fields: Any) -> dict | None:
        allowed = {"text", "fire_at", "recurrence", "status", "last_fired_at"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.conn.execute(f"UPDATE reminders SET {sets} WHERE id = ?", (*fields.values(), reminder_id))
        return self.get_reminder(reminder_id)

    # ---------- заметки (идеи, мечты, цели, мысли) ----------
    def add_note(self, kind: str, content: str, area: str | None = None,
                 tags: str | None = None) -> dict:
        now = now_utc_str()
        cur = self.conn.execute(
            "INSERT INTO notes(kind, content, area, tags, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (kind, content, area, tags, now, now),
        )
        return self.get_note(int(cur.lastrowid))

    def get_note(self, note_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return dict(row) if row else None

    def update_note(self, note_id: int, **fields: Any) -> dict | None:
        allowed = {"content", "status", "area", "tags", "kind"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if "status" in fields and fields["status"] not in NOTE_STATUSES:
            raise ValueError(f"status должен быть одним из {NOTE_STATUSES}")
        if fields:
            fields["updated_at"] = now_utc_str()
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.conn.execute(f"UPDATE notes SET {sets} WHERE id = ?", (*fields.values(), note_id))
        return self.get_note(note_id)

    def list_notes(self, kind: str | None = None, area: str | None = None,
                   status: str = "active", limit: int = 30) -> list[dict]:
        where, params = ["status = ?"], [status]
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if area:
            where.append("area = ?")
            params.append(area)
        params.append(limit)
        return _rows(self.conn.execute(
            f"SELECT * FROM notes WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", params
        ))

    # ---------- решения ----------
    def add_decision(self, question: str, options: list[str], default_option: str | None,
                     enough_data: str | None, deadline: str | None, reversible: bool | None) -> dict:
        cur = self.conn.execute(
            "INSERT INTO decisions(question, options, default_option, enough_data, deadline, reversible, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (question, json.dumps(options, ensure_ascii=False), default_option, enough_data, deadline,
             None if reversible is None else int(reversible), now_utc_str()),
        )
        return self.get_decision(int(cur.lastrowid))

    def get_decision(self, decision_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["options"] = json.loads(d["options"] or "[]")
        return d

    def resolve_decision(self, decision_id: int, decision: str, rationale: str | None) -> dict | None:
        self.conn.execute(
            "UPDATE decisions SET status = 'decided', decision = ?, rationale = ?, decided_at = ? WHERE id = ?",
            (decision, rationale, now_utc_str(), decision_id),
        )
        return self.get_decision(decision_id)

    def list_decisions(self, status: str = "open", limit: int = 20) -> list[dict]:
        rows = _rows(self.conn.execute(
            "SELECT * FROM decisions WHERE status = ? ORDER BY deadline IS NULL, deadline, id LIMIT ?",
            (status, limit),
        ))
        for r in rows:
            r["options"] = json.loads(r["options"] or "[]")
        return rows

    # ---------- итоги периодов ----------
    def save_summary(self, period: str, period_key: str, content: str) -> None:
        self.conn.execute("DELETE FROM summaries WHERE period = ? AND period_key = ?", (period, period_key))
        self.conn.execute(
            "INSERT INTO summaries(period, period_key, content, created_at) VALUES (?,?,?,?)",
            (period, period_key, content, now_utc_str()),
        )

    def get_summary(self, period: str, period_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM summaries WHERE period = ? AND period_key = ?", (period, period_key)
        ).fetchone()
        return dict(row) if row else None

    def list_summaries(self, period: str, since_key: str | None = None, until_key: str | None = None,
                       limit: int = 14) -> list[dict]:
        where, params = ["period = ?"], [period]
        if since_key:
            where.append("period_key >= ?")
            params.append(since_key)
        if until_key:
            where.append("period_key <= ?")
            params.append(until_key)
        params.append(limit)
        rows = _rows(self.conn.execute(
            f"SELECT * FROM summaries WHERE {' AND '.join(where)} ORDER BY period_key DESC LIMIT ?", params
        ))
        return list(reversed(rows))

    # ---------- поиск по памяти ----------
    def search(self, query: str, limit: int = 10) -> list[dict]:
        q = fts_query(query)
        if not q:
            return []
        results: list[dict] = []
        sources = [
            ("journal", "entries", "entries_fts", "e.ts AS ts, e.role AS extra"),
            ("note", "notes", "notes_fts", "e.created_at AS ts, e.kind AS extra"),
            ("summary", "summaries", "summaries_fts", "e.created_at AS ts, e.period || ':' || e.period_key AS extra"),
        ]
        for source, table, fts, cols in sources:
            sql = (
                f"SELECT e.id AS id, {cols}, snippet({fts}, 0, '[', ']', '…', 24) AS snippet, "
                f"bm25({fts}) AS score FROM {fts} JOIN {table} e ON e.id = {fts}.rowid "
                f"WHERE {fts} MATCH ? ORDER BY score LIMIT ?"
            )
            try:
                for row in self.conn.execute(sql, (q, limit)):
                    results.append({"source": source, **dict(row)})
            except sqlite3.OperationalError:
                continue
        results.sort(key=lambda r: r["score"])
        return results[:limit]

    def backup_bytes(self) -> bytes:
        """Консистентная копия базы (с учётом WAL) — для /export."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backup.db"
            dest = sqlite3.connect(str(path))
            try:
                self.conn.backup(dest)
            finally:
                dest.close()
            return path.read_bytes()

    # ---------- key-value ----------
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def kv_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ---------- расходы ----------
    def add_usage(self, kind: str, model: str | None = None, input_tokens: int = 0,
                  output_tokens: int = 0, cache_read: int = 0, cache_write: int = 0,
                  seconds: float = 0.0, cost_usd: float = 0.0) -> None:
        self.conn.execute(
            "INSERT INTO usage(ts, kind, model, input_tokens, output_tokens, cache_read, cache_write, seconds, cost_usd) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (now_utc_str(), kind, model, input_tokens, output_tokens, cache_read, cache_write, seconds, cost_usd),
        )

    def usage_since(self, since_utc: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for row in self.conn.execute(
            "SELECT kind, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
            "SUM(cache_read) AS cache_read, SUM(seconds) AS seconds, SUM(cost_usd) AS cost_usd "
            "FROM usage WHERE ts >= ? GROUP BY kind",
            (since_utc,),
        ):
            out[row["kind"]] = dict(row)
        return out

    def cost_since(self, since_utc: str) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE ts >= ?", (since_utc,)).fetchone()
        return float(row[0])


def month_start_utc() -> str:
    from .timeutil import to_utc_str

    local = now_local()
    return to_utc_str(datetime(local.year, local.month, 1, tzinfo=local.tzinfo))


def iso_week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"
