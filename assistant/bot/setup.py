"""Мастер первичной настройки: python -m bot.setup

Спрашивает ключи, пишет .env по шаблону .env.example и готовит data/profile.md.
Не импортирует config, чтобы работать до того, как .env появился.
"""

from __future__ import annotations

import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

MODELS = {
    "1": ("claude-opus-5-5", "Opus 5.5 — самый умный, ~$60–90/мес"),
    "2": ("claude-sonnet-5-5", "Sonnet 5.5 — в 2 раза дешевле, ~$30–45/мес"),
    "3": ("claude-haiku-5-5", "Haiku 5.5 — почти бесплатно, ~$2–4/мес, но проще"),
}


def render_env(template: str, values: dict[str, str]) -> str:
    """Подставляет значения в строки KEY=... шаблона, комментарии и порядок сохраняет."""
    out = []
    for line in template.splitlines():
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in values:
            line = f"{key}={values[key]}"
        out.append(line)
    return "\n".join(out) + "\n"


def ask(prompt: str, default: str = "", check: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip() or default
        if not value:
            print("  Нужно заполнить.")
            continue
        if check and not value.startswith(check):
            print(f"  Обычно этот ключ начинается с «{check}». Проверь, что скопировал целиком.")
            if input("  Оставить как есть? (д/н): ").strip().lower() not in ("д", "y", "да", "yes"):
                continue
        return value


def main() -> None:
    env_path = BASE_DIR / ".env"
    print("=== Настройка личного ассистента ===\n")
    if env_path.exists() and input(".env уже есть. Перезаписать? (д/н): ").strip().lower() not in ("д", "y", "да"):
        print("Оставил как есть.")
        return

    values: dict[str, str] = {}
    print("1) Токен бота: Telegram → @BotFather → /newbot")
    while ":" not in (token := ask("   TELEGRAM_BOT_TOKEN")):
        print("   Токен выглядит как 123456789:AAE... — с двоеточием.")
    values["TELEGRAM_BOT_TOKEN"] = token

    print("\n2) Ключ Claude: console.anthropic.com → API Keys (там же поставь месячный лимит)")
    values["ANTHROPIC_API_KEY"] = ask("   ANTHROPIC_API_KEY", check="sk-ant-")

    print("\n3) Ключ распознавания речи: console.groq.com/keys")
    values["STT_API_KEY"] = ask("   STT_API_KEY", check="gsk_")

    print("\n4) Модель:")
    for num, (_, title) in MODELS.items():
        print(f"   {num}. {title}")
    choice = ask("   Номер", "1")
    values["CLAUDE_MODEL"] = MODELS.get(choice, MODELS["1"])[0]

    budget = ask("\n5) Лимит расходов на Claude в месяц, $ (бот остановится сам)", "30")
    values["MONTHLY_BUDGET_USD"] = budget if budget.replace(".", "", 1).isdigit() else "30"

    print("\n6) Твой Telegram ID. Если не знаешь — нажми Enter: при первом запуске напиши боту,")
    print("   он пришлёт ID, впиши его в .env (OWNER_ID=...) и перезапусти.")
    owner = input("   OWNER_ID: ").strip()
    values["OWNER_ID"] = owner if owner.isdigit() else ""

    template = (BASE_DIR / ".env.example").read_text(encoding="utf-8")
    env_path.write_text(render_env(template, values), encoding="utf-8")
    print(f"\n✅ Настройки сохранены: {env_path}")

    profile = BASE_DIR / "data" / "profile.md"
    if not profile.exists():
        profile.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(BASE_DIR / "profile.example.md", profile)
        print(f"📝 Заполни профиль о себе (или вставь готовый): {profile}")
    print("\nДальше бот запустится. Остановить — Ctrl+C.")


if __name__ == "__main__":
    main()
