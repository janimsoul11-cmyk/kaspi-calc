#!/usr/bin/env bash
# Запуск ассистента на macOS / Linux: ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Первый запуск: создаю окружение Python..."
    python3 -m venv .venv
fi
. .venv/bin/activate
python -m pip install -q --disable-pip-version-check -r requirements.txt

[ -f .env ] || python -m bot.setup

echo "Бот запущен. Не закрывай терминал. Остановить — Ctrl+C."
if command -v caffeinate >/dev/null 2>&1; then
    # macOS: не даём компьютеру уснуть, пока бот работает
    exec caffeinate -is python -m bot
fi
exec python -m bot
