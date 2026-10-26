# Личный ассистент в Telegram — контекст для Claude

Отвечай владельцу по-русски, коротко и по делу. Репозиторий **публичный**: секреты, личные данные,
IP сервера и содержимое `.env` в git и в ответы не попадают.

## Что это и где работает

Личный бот `@DONI_ASSISTANT_AI`. Владелец надиктовывает голосовые; бот раскладывает их на задачи,
напоминания, заметки и решения, присылает брифинги и отвечает голосом. Подробно — `README.md`.

- **Код:** репозиторий `janimsoul11-cmyk/kaspi-calc`, ветка `claude/telegram-personal-assistant-1j1vux`,
  папка `assistant/`. Draft PR #2.
- **Прод:** сервер Sellmir (hoster.kz, Ubuntu 24.04). Доступ только по ключу: SSH-алиас `sellmir` из
  `~/.ssh/config` на ПК владельца (Windows, `C:\Users\User\.ssh`). Пароль root по SSH не принимается.
- **Служба:** systemd `assistant`, пользователь `assistant`, код `/opt/assistant/repo` (принадлежит root),
  venv `/opt/assistant/venv`, писать можно только в `/opt/assistant/data` (SQLite `assistant.db`, `profile.md`).
  Настройки и ключи — `/opt/assistant/.env` (root:assistant 640). Его содержимое никогда не выводить.
- Sellmir работает на том же сервере (`/opt/sellmir-app`, служба `sellmir-app`, Caddy). Его не трогаем.

## Частые операции (с ПК владельца)

| Что | Команда |
|---|---|
| Обновить бота на сервере | `ssh sellmir "bash /opt/assistant/repo/assistant/deploy/install.sh"` |
| Логи | `ssh sellmir "journalctl -u assistant -n 100 --no-pager"` |
| Статус | `ssh sellmir "systemctl status assistant --no-pager"` |
| Перезапуск после правки `.env` | `ssh sellmir "systemctl restart assistant"` |

`install.sh` идемпотентен: тянет ветку, ставит зависимости, переносит `.env`/`data` из `/tmp` при первом
запуске, чинит испорченный `/dev/null`, перезапускает службу.

## Порядок работы

1. Изменения в `assistant/bot/…`, тесты в `assistant/tests/…`.
2. `cd assistant && python -m pytest -q` — все тесты должны проходить (есть сквозные тесты против
   заглушек Telegram/STT/Claude/OpenAI в `tests/fake_services.py`). CI гоняет их на Ubuntu и Windows.
3. Коммит и push в ветку, затем обновить сервер командой выше и проверить логи.

## Устройство (кратко)

- `bot/brain.py` — цикл Claude с 16 инструментами из `bot/tools.py`, сборка контекста, учёт расходов,
  лимит бюджета. Модель из `CLAUDE_MODEL` (сейчас `claude-haiku-5-5`, у Haiku без `fallbacks`).
- `bot/db.py` — SQLite + FTS5: журнал, задачи, напоминания, заметки, решения, итоги, kv, usage.
- `bot/scheduler.py` — напоминания, брифинг 07:00, разбор 21:30, обзор вс 20:00, план 1-го числа,
  ночное сжатие 03:30.
- `bot/voice.py` — STT через Groq Whisper; TTS-цепочка `openai → edge → google`; openai включается только
  с `OPENAI_API_KEY`; недавно упавший провайдер пробуется последним.
- `bot/handlers.py` — Telegram. Команды: /today /plan /tasks /reminders /ideas /review /week /memory
  /voice /voices /voicetest /stats /export.
- `bot/prompts.py` — системный промпт (неизменный, кешируется) и промпты автозапусков.

## Принятые решения и почему

- **Модель Haiku 5.5:** ~$0.001 за сообщение, на живом тесте разбирала голосовые корректно. Критерий
  перехода на Sonnet 5.5: плохо понимает больше 1 голосового из 5.
- **Голос:** Microsoft edge-tts, выбран Emma (`en-US-EmmaMultilingualNeural`) через /voices; хранится в
  kv `tts_voice`. С рабочей сети владельца edge-tts не отвечает (`NoAudioReceived`), с сервера работает.
  OpenAI TTS готов (`deploy/set_openai_key.bat`), но не подключён: нужен отдельный баланс OpenAI.
  Gemini TTS не реализован: бесплатно, но на free tier Google может использовать тексты — решение за владельцем.
- **Сервер вместо ПК:** работает круглосуточно; перенос сделан `deploy/move_to_server.bat`.
- `/dev/null` на сервере был испорчен 20.10.2026 (`crw-r--r-- sellmir`), из-за этого не работал apt.
  `install.sh` его чинит; если испортится снова — искать причину в задачах Sellmir.

## Открытые вопросы

- Ключи (токен бота, Anthropic, Groq) владелец показывал в чате — нужно перевыпустить и вписать в
  `/opt/assistant/.env`.
- Решить про голос (Emma / Gemini / OpenAI) и модель (Haiku / Sonnet) после пары дней использования.
- Идеи развития: Google Calendar, учёт оплат поставщикам, время намаза (источник muftyat.kz не проверен).
