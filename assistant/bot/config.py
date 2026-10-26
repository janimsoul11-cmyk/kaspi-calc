"""Настройки бота из переменных окружения (и файла .env, если он есть)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Цены за 1M токенов в USD: (input, output, cache_read, cache_write).
# Нужны только для оценки расходов в /stats и для лимита бюджета.
MODEL_PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

# Модели, для которых включаем серверный fallback при отказе классификатора.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"}


def _load_dotenv(path: Path) -> None:
    """Минимальный загрузчик .env: KEY=VALUE, без перезаписи уже заданных переменных."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _time(name: str, default: str) -> time:
    hh, mm = _env(name, default).split(":")
    return time(int(hh), int(mm))


@dataclass
class Settings:
    telegram_token: str
    telegram_api_base: str
    owner_id: int
    anthropic_api_key: str
    model: str
    chat_effort: str
    planning_effort: str
    rollup_effort: str
    stt_api_key: str
    stt_base_url: str
    stt_model: str
    stt_language: str
    stt_prompt: str
    stt_price_per_hour: float
    tts_voice: str
    tts_rate: str
    tts_lang: str
    tts_providers: list[str]
    tts_max_chars: int
    openai_api_key: str
    openai_base_url: str
    openai_tts_model: str
    openai_tts_voice: str
    openai_tts_instructions: str
    timezone: str
    morning_time: time
    evening_time: time
    weekly_day: int
    weekly_time: time
    rollup_time: time
    history_messages: int
    monthly_budget_usd: float
    data_dir: Path
    profile_file: Path
    areas: list[str] = field(default_factory=list)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "assistant.db"


WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def load_settings() -> Settings:
    _load_dotenv(BASE_DIR / ".env")
    weekly_day, weekly_time = _env("WEEKLY_REVIEW", "sun 20:00").split()
    data_dir = Path(_env("DATA_DIR", str(BASE_DIR / "data")))
    return Settings(
        telegram_token=_env("TELEGRAM_BOT_TOKEN"),
        # Свой Bot API сервер (или эмулятор в тестах); пусто — официальный api.telegram.org.
        telegram_api_base=_env("TELEGRAM_API_BASE"),
        owner_id=int(_env("OWNER_ID", "0") or 0),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        model=_env("CLAUDE_MODEL", "claude-opus-5-5"),
        chat_effort=_env("CHAT_EFFORT", "medium"),
        planning_effort=_env("PLANNING_EFFORT", "high"),
        rollup_effort=_env("ROLLUP_EFFORT", "low"),
        stt_api_key=_env("STT_API_KEY"),
        stt_base_url=_env("STT_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/"),
        stt_model=_env("STT_MODEL", "whisper-large-v3"),
        stt_language=_env("STT_LANGUAGE", "ru"),
        stt_prompt=_env("STT_PROMPT", ""),
        stt_price_per_hour=float(_env("STT_PRICE_PER_HOUR", "0.111")),
        tts_voice=_env("TTS_VOICE", "ru-RU-DmitryNeural"),
        tts_rate=_env("TTS_RATE", "+5%"),
        tts_lang=_env("TTS_LANG", "ru"),
        # openai используется, только если задан OPENAI_API_KEY; без ключа — сразу бесплатные.
        tts_providers=[p.strip() for p in _env("TTS_PROVIDERS", "openai,edge,google").split(",") if p.strip()],
        tts_max_chars=int(_env("TTS_MAX_CHARS", "1200")),
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_base_url=_env("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        openai_tts_model=_env("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        openai_tts_voice=_env("OPENAI_TTS_VOICE", "ash"),
        openai_tts_instructions=_env(
            "OPENAI_TTS_INSTRUCTIONS",
            "Говори на русском естественно и тепло, как близкий личный помощник: спокойный темп, "
            "живые интонации, без дикторского пафоса.",
        ),
        timezone=_env("TIMEZONE", "Asia/Almaty"),
        morning_time=_time("MORNING_TIME", "07:00"),
        evening_time=_time("EVENING_TIME", "21:30"),
        weekly_day=WEEKDAYS[weekly_day.lower()[:3]],
        weekly_time=time(*map(int, weekly_time.split(":"))),
        rollup_time=_time("ROLLUP_TIME", "03:30"),
        history_messages=int(_env("HISTORY_MESSAGES", "30")),
        monthly_budget_usd=float(_env("MONTHLY_BUDGET_USD", "30")),
        data_dir=data_dir,
        profile_file=Path(_env("PROFILE_FILE", str(data_dir / "profile.md"))),
        areas=[a.strip() for a in _env(
            "AREAS", "work,project,family,health,faith,learning,finance,personal"
        ).split(",") if a.strip()],
    )


settings = load_settings()
