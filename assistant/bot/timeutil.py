"""Работа со временем: в базе всё хранится в UTC, пользователю показываем локальное."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .config import settings

UTC_FMT = "%Y-%m-%d %H:%M:%S"

WEEKDAY_NAMES = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTH_NAMES = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def now_local() -> datetime:
    return datetime.now(tz())


def now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime(UTC_FMT)


def to_utc_str(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz())
    return dt.astimezone(timezone.utc).strftime(UTC_FMT)


def from_utc_str(value: str) -> datetime:
    """UTC-строка из базы -> aware datetime в локальной зоне."""
    return datetime.strptime(value, UTC_FMT).replace(tzinfo=timezone.utc).astimezone(tz())


def parse_local(value: str) -> datetime:
    """'YYYY-MM-DD HH:MM' (или с 'T') в локальной зоне -> aware datetime."""
    value = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=tz())
        except ValueError:
            continue
    raise ValueError(f"Неверный формат времени: {value!r}, нужен 'YYYY-MM-DD HH:MM'")


def parse_date(value: str) -> date:
    return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()


def human_dt(dt: datetime) -> str:
    return f"{WEEKDAY_NAMES[dt.weekday()]}, {dt.day} {MONTH_NAMES[dt.month - 1]} {dt.year}, {dt:%H:%M}"


def short_dt(dt: datetime) -> str:
    return f"{dt:%d.%m %H:%M}"
