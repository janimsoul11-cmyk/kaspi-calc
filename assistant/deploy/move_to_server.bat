@echo off
chcp 65001 >nul
rem Перенос ассистента с этого компьютера на сервер одним запуском.
rem Останавливает бота здесь, отправляет .env и data на сервер (пароль root спросят один раз) и ставит службу.
set "APP=%USERPROFILE%\Desktop\kaspi-calc-claude-telegram-personal-assistant-1j1vux\assistant"
if exist "%~dp0.env" set "APP=%~dp0."
if not exist "%APP%\.env" (echo Не нашёл папку бота с файлом .env: %APP% & pause & exit /b 1)
cd /d "%APP%"

echo Останавливаю бота на этом компьютере...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*-m bot*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"

set "IP="
rem Если в .ssh\config есть Host sellmir (ключ от сервера Sellmir) - по умолчанию подключаемся через него.
set "DEFAULT_HOST="
findstr /b /i /c:"Host sellmir" "%USERPROFILE%\.ssh\config" >nul 2>nul && set "DEFAULT_HOST=sellmir"
set /p IP=Введи IP сервера или имя из .ssh\config [%DEFAULT_HOST%]: 
if "%IP%"=="" set "IP=%DEFAULT_HOST%"
if "%IP%"=="" (echo IP не введён. & pause & exit /b 1)
echo.
echo Сейчас спросит пароль root от сервера. Символы при вводе не видны - это нормально.
echo Если спросит yes/no - напиши yes.
echo.
tar -cf - .env data | ssh -o StrictHostKeyChecking=accept-new root@%IP% "rm -rf /tmp/assistant-upload && mkdir -p /tmp/assistant-upload && tar -xf - -C /tmp/assistant-upload && mv -f /tmp/assistant-upload/.env /tmp/assistant.env && rm -rf /tmp/assistant-data && mv /tmp/assistant-upload/data /tmp/assistant-data && rmdir /tmp/assistant-upload && curl -fsSL https://raw.githubusercontent.com/janimsoul11-cmyk/kaspi-calc/refs/heads/claude/telegram-personal-assistant-1j1vux/assistant/deploy/install.sh | bash"
echo.
echo Если выше есть строка active (running) - бот работает на сервере круглосуточно.
echo start.bat на этом компьютере больше не запускай: бот не может работать в двух местах сразу.
pause
