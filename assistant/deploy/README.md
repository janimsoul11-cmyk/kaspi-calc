# Переезд ассистента на сервер (Ubuntu 24.04, рядом с Sellmir)

Бот работает через long polling: домен, Caddy и открытые порты не нужны. Памяти занимает ~100 МБ.
Служба `assistant` живёт в `/opt/assistant` отдельно от Sellmir и с ним не пересекается.

## Самый простой путь (Windows)

Дважды кликните `deploy\move_to_server.bat`, введите IP сервера и пароль root. Файл остановит бота
на ПК, отправит `.env` и `data` на сервер одним архивом и запустит установку (шаг 2 ниже).

## 1. На ПК: остановить бота и отправить настройки и память

Закройте окно бота. Один токен нельзя слушать из двух мест: Telegram отдаст ошибку Conflict.
Затем в Git Bash:

```
cd "/c/Users/User/Desktop/kaspi-calc-claude-telegram-personal-assistant-1j1vux/assistant"
scp .env root@СЕРВЕР:/tmp/assistant.env
scp -r data root@СЕРВЕР:/tmp/assistant-data
```

## 2. На сервере: одна команда

```
ssh root@СЕРВЕР
curl -fsSL https://raw.githubusercontent.com/janimsoul11-cmyk/kaspi-calc/refs/heads/claude/telegram-personal-assistant-1j1vux/assistant/deploy/install.sh | bash
```

Скрипт делает следующее:
- ставит `git`, `python3-venv` и `ffmpeg`;
- создаёт пользователя `assistant` и скачивает код в `/opt/assistant/repo`;
- переносит `.env` и `data/` из `/tmp`, убирает переносы строк Windows;
- включает службу и показывает её статус.

В статусе должно быть `active (running)`. Проверка: отправьте боту `/today`.

## Обслуживание

| Что | Команда |
|---|---|
| Логи в реальном времени | `journalctl -u assistant -f` |
| Перезапуск | `systemctl restart assistant` |
| Обновление кода | `bash /opt/assistant/repo/assistant/deploy/install.sh` |
| Поменять настройки | `nano /opt/assistant/.env`, затем `systemctl restart assistant` |
| Бэкап | `/export` в боте или скопировать `/opt/assistant/data` |

Писать служба может только в `/opt/assistant/data`: код и окружение принадлежат root.
Медленные исходящие запросы из-за DNS хостера уже лечит `sellmir-dns-fix.sh` из Sellmir. Боту он тоже помогает.
