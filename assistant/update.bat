@echo off
chcp 65001 >nul
rem Обновление кода бота с GitHub. Настройки (.env) и память (data\) не трогаются.
rem Перед запуском закрой окно бота.
cd /d "%~dp0"
set "URL=https://github.com/janimsoul11-cmyk/kaspi-calc/archive/refs/heads/claude/telegram-personal-assistant-1j1vux.zip"
set "UPD=%TEMP%\assistant-update"
if exist "%UPD%" rmdir /s /q "%UPD%"
mkdir "%UPD%"

echo Скачиваю обновление...
curl.exe -fsSL -o "%UPD%\update.zip" "%URL%" || (echo Не удалось скачать обновление. & pause & exit /b 1)
tar.exe -xf "%UPD%\update.zip" -C "%UPD%" || (echo Не удалось распаковать обновление. & pause & exit /b 1)
set "SRC="
for /d %%D in ("%UPD%\*") do set "SRC=%%D\assistant"
if not exist "%SRC%\bot\__main__.py" (echo В архиве не нашёлся бот. & pause & exit /b 1)

robocopy "%SRC%\bot" "bot" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (echo Не удалось скопировать файлы. & pause & exit /b 1)
for %%F in (requirements.txt start.bat .env.example profile.example.md README.md) do copy /y "%SRC%\%%F" "%%F" >nul
rmdir /s /q "%UPD%"

echo Готово. Запусти start.bat — он сам доустановит новые библиотеки.
pause
