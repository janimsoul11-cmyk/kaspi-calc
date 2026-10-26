@echo off
chcp 65001 >nul
rem Перевыпуск ключей на сервере: токен бота, Anthropic, Groq. Ключи вводятся скрыто, на экране только звёздочки.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rotate_keys.ps1"
pause
