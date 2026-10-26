@echo off
chcp 65001 >nul
rem Подключает живой голос OpenAI на сервере: записывает OPENAI_API_KEY в /opt/assistant/.env и обновляет бота.
set "HOST=sellmir"
set "KEY="
set /p KEY=Вставь ключ OpenAI (sk-...) правой кнопкой мыши и нажми Enter: 
if "%KEY%"=="" (echo Ключ не введён. & pause & exit /b 1)
echo.
echo Записываю ключ на сервер и обновляю бота, это 1-2 минуты...
ssh %HOST% "sed -i -e '/^OPENAI_API_KEY=/d' -e '$a\' /opt/assistant/.env && printf 'OPENAI_API_KEY=%%s\n' '%KEY%' >> /opt/assistant/.env && bash /opt/assistant/repo/assistant/deploy/install.sh"
echo.
echo Если выше есть active (running) - отправь боту /voicetest: в строке openai должна быть галочка.
pause
