"""Записывает новые ключи в /opt/assistant/.env и перезапускает бота. Выполняется на сервере под root.

Запускает его rotate_keys.ps1 с ПК через stdin ssh: первая строка — этот код в base64, дальше ключи в JSON.
Поэтому ключей нет ни в списке процессов, ни в истории команд, ни в выводе: всё, что печатается,
проходит через mask(). Сначала каждый ключ проверяется запросом к его сервису: если хоть один не подошёл,
.env не меняется и бот не перезапускается.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ENV_PATH = "/opt/assistant/.env"
SERVICE = "assistant"
NAMES = ("TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "STT_API_KEY")
VALUE_RE = re.compile(r"[A-Za-z0-9_:.\-]{20,300}")
LOG_ALARMS = ("Unauthorized", "Conflict", "Traceback", "invalid x-api-key", "authentication_error")


def env_value(text: str, name: str, default: str = "") -> str:
    """Значение переменной из .env. Как и в systemd, при повторах побеждает последняя строка."""
    value = default
    for line in text.splitlines():
        key, sep, val = line.partition("=")
        if sep and key.strip() == name:
            value = val.strip().strip("\"'")
    return value


def update_env_text(text: str, new: dict[str, str]) -> str:
    """Заменяет значения во всех строках с этими именами, недостающие дописывает в конец."""
    lines = text.splitlines()
    seen = set()
    for i, line in enumerate(lines):
        key, sep, _ = line.partition("=")
        if sep and key.strip() in new:
            lines[i] = f"{key.strip()}={new[key.strip()]}"
            seen.add(key.strip())
    lines += [f"{name}={value}" for name, value in new.items() if name not in seen]
    return "\n".join(lines) + "\n"


def mask(text: str, secrets) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def http_json(url: str, headers: dict[str, str]) -> tuple[int, dict]:
    request = urllib.request.Request(url, headers={"User-Agent": "assistant-key-check", **headers})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def problem(status: int) -> str:
    return "сервис отклонил ключ" if status in (401, 403, 404) else f"сервис ответил ошибкой {status}"


def check_telegram(token: str, old_token: str) -> str:
    status, body = http_json(f"https://api.telegram.org/bot{token}/getMe", {})
    if status != 200 or not body.get("ok"):
        raise ValueError(problem(status))
    bot_id, username = str(body["result"]["id"]), body["result"].get("username", "?")
    old_id = old_token.split(":", 1)[0]
    if old_id and old_id != bot_id:
        raise ValueError(f"это токен другого бота (@{username}); токен ассистента начинается с {old_id}:")
    return f"@{username}"


def check_anthropic(key: str) -> str:
    status, _ = http_json("https://api.anthropic.com/v1/models",
                          {"x-api-key": key, "anthropic-version": "2023-06-01"})
    if status != 200:
        raise ValueError(problem(status))
    return "ключ принят"


def check_stt(key: str, base_url: str) -> str:
    status, _ = http_json(f"{base_url.rstrip('/')}/models", {"Authorization": f"Bearer {key}"})
    if status != 200:
        raise ValueError(problem(status))
    return f"ключ принят ({base_url})"


def write_env(path: str, text: str) -> None:
    """Атомарная запись с прежними владельцем и правами: файл ни на миг не бывает доступен всем."""
    st = os.stat(path)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o777)
    os.replace(tmp, path)


def restart(secrets) -> bool:
    since = time.strftime("%Y-%m-%d %H:%M:%S")
    subprocess.run(["systemctl", "restart", SERVICE], check=True)
    time.sleep(10)
    state = subprocess.run(["systemctl", "is-active", SERVICE], capture_output=True, text=True).stdout.strip()
    logs = subprocess.run(["journalctl", "-u", SERVICE, "--since", since, "--no-pager", "-o", "cat", "-n", "15"],
                          capture_output=True, text=True).stdout
    print(f"\nСлужба {SERVICE}: {state}. Журнал после перезапуска:")
    print(mask(logs, secrets).rstrip() or "(пусто)")
    alarms = [word for word in LOG_ALARMS if word in logs]
    if alarms:
        print(f"\n!! В журнале есть {', '.join(alarms)} — бот, возможно, не работает. Пришли этот вывод Claude.")
    return state == "active" and not alarms


def main() -> int:
    payload = json.loads(sys.stdin.read().lstrip("﻿") or "{}")
    new = {name: value.strip() for name, value in payload.items() if value.strip()}
    secrets = list(new.values())
    unknown = sorted(set(new) - set(NAMES))
    if unknown or not new:
        print(f"Нечего записывать или неизвестные имена: {unknown}")
        return 1
    bad = [name for name, value in new.items() if not VALUE_RE.fullmatch(value)]
    if bad:
        print(f"Недопустимые символы или длина: {', '.join(bad)}. Ничего не изменено.")
        return 1

    with open(ENV_PATH, encoding="utf-8") as f:
        current = f.read()
    checks = {
        "TELEGRAM_BOT_TOKEN": lambda v: check_telegram(v, env_value(current, "TELEGRAM_BOT_TOKEN")),
        "ANTHROPIC_API_KEY": check_anthropic,
        "STT_API_KEY": lambda v: check_stt(v, env_value(current, "STT_BASE_URL", "https://api.groq.com/openai/v1")),
    }
    print("Проверяю ключи на сервере:")
    failed = False
    for name, value in new.items():
        try:
            print(f"  {name}: {checks[name](value)}")
        except Exception as e:  # сеть, JSON, отказ сервиса — всё одинаково блокирует запись
            print(f"  {name}: НЕ ПОДОШЁЛ — {mask(str(e), secrets)}")
            failed = True
    if failed:
        print("\nНичего не изменено: .env прежний, бот работает со старыми ключами.")
        return 1

    write_env(ENV_PATH, update_env_text(current, new))
    print(f"\nЗаписано в {ENV_PATH}: {', '.join(new)}. Перезапускаю бота...")
    return 0 if restart(secrets) else 2


if __name__ == "__main__":
    sys.exit(main())
