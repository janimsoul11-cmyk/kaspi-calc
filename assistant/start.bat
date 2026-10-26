@echo off
chcp 65001 >nul
rem Запуск ассистента на Windows: двойной клик по этому файлу.
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")

if not exist .venv (
    echo Первый запуск: создаю окружение Python...
    %PY% -m venv .venv || (echo Нужен Python 3.10+ с python.org — при установке отметь "Add to PATH". & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q --disable-pip-version-check -r requirements.txt

if not exist .env python -m bot.setup

echo Бот запущен. Не закрывай это окно. Остановить — Ctrl+C.
python -m bot
pause
