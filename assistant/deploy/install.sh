#!/usr/bin/env bash
# Установка и обновление ассистента на Ubuntu-сервере. Запускать под root; повторный запуск = обновление.
#   curl -fsSL https://raw.githubusercontent.com/janimsoul11-cmyk/kaspi-calc/refs/heads/claude/telegram-personal-assistant-1j1vux/assistant/deploy/install.sh | bash
# Перенос с ПК: заранее положить .env в /tmp/assistant.env и папку data в /tmp/assistant-data (см. deploy/README.md).
set -euo pipefail

REPO=${REPO:-https://github.com/janimsoul11-cmyk/kaspi-calc}
BRANCH=${BRANCH:-claude/telegram-personal-assistant-1j1vux}
ROOT=/opt/assistant

[ "$(id -u)" -eq 0 ] || { echo "Нужен root: sudo bash install.sh"; exit 1; }

# /dev/null должен быть символьным устройством с правами 666. Если его испортили (заменили обычным
# файлом или сменили права), apt не может проверять подписи. Пересоздаём атомарно: сервисам без простоя.
if [ ! -c /dev/null ] || [ "$(stat -c %a /dev/null)" != "666" ]; then
    echo "== /dev/null испорчен ($(ls -l /dev/null)) — восстанавливаю"
    rm -f /dev/null.new
    mknod -m 666 /dev/null.new c 1 3
    mv -f /dev/null.new /dev/null
    ls -l /dev/null
fi

echo "== Пакеты"
if ! apt-get update -qq; then
    echo "!! apt-get update завершился с ошибками — пробую поставить пакеты из кеша"
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3-venv ffmpeg >/dev/null

echo "== Пользователь и папки"
id assistant >/dev/null 2>&1 || useradd -r -m -d "$ROOT" -s /usr/sbin/nologin assistant
mkdir -p "$ROOT/data"

echo "== Код ($BRANCH)"
if [ -d "$ROOT/repo/.git" ]; then
    git -C "$ROOT/repo" fetch -q --depth 1 origin "$BRANCH"
    git -C "$ROOT/repo" reset -q --hard FETCH_HEAD
else
    git clone -q --depth 1 -b "$BRANCH" "$REPO" "$ROOT/repo"
fi
[ -d "$ROOT/venv" ] || python3 -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" install -q --disable-pip-version-check -r "$ROOT/repo/assistant/requirements.txt"

echo "== Настройки и память"
if [ -f /tmp/assistant.env ] && [ ! -f "$ROOT/.env" ]; then
    mv /tmp/assistant.env "$ROOT/.env"
fi
if [ -d /tmp/assistant-data ] && [ ! -f "$ROOT/data/assistant.db" ]; then
    cp -r /tmp/assistant-data/. "$ROOT/data/"
    rm -rf /tmp/assistant-data
fi
if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/repo/assistant/.env.example" "$ROOT/.env"
    echo "!! Заполни $ROOT/.env (nano $ROOT/.env) и запусти скрипт ещё раз."
fi
sed -i 's/\r$//' "$ROOT/.env"   # .env с Windows приходит с CRLF
# Код и venv принадлежат root (сервис их не меняет), писать можно только в data/.
chown -R assistant: "$ROOT/data"
chown root:assistant "$ROOT/.env"
chmod 640 "$ROOT/.env"

echo "== Служба"
cp "$ROOT/repo/assistant/deploy/assistant.service" /etc/systemd/system/assistant.service
systemctl daemon-reload
systemctl enable -q assistant
systemctl restart assistant
sleep 5
systemctl --no-pager --lines=20 status assistant || true
echo "Готово. Логи: journalctl -u assistant -f"
