@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MuzFlowers - Telegram-бот

echo.
echo  ==================================================
echo   MuzFlowers: запускаю Telegram-бота
echo   (в первый раз это займёт 2-3 минуты - ничего не закрывайте)
echo  ==================================================
echo.

rem Временные файлы держим в папке проекта, а не на диске C
if not exist ".tmp" mkdir ".tmp"
set "TEMP=%~dp0.tmp"
set "TMP=%~dp0.tmp"
set "PIP_CACHE_DIR=%~dp0.tmp\pip"
set "PYTHONUTF8=1"

rem Ищем Python (нужен 3.11 или новее)
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY goto no_python
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto old_python

rem Рабочая папка с программами: если её нет или она сломалась (например, папку переносили) - создаём заново
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>nul || rmdir /s /q ".venv"
)
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Готовлю рабочую папку для программ...
  %PY% -m venv .venv
  if errorlevel 1 goto err
)
set "VPY=%~dp0.venv\Scripts\python.exe"

echo [2/3] Проверяю нужные программы (нужен интернет, если что-то не установлено)...
"%VPY%" -m pip install -q --disable-pip-version-check --no-input -r requirements.txt
if errorlevel 1 goto err

rem Секретный файл с ключом бота: в первый раз создаём из образца
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo.
  echo  ==================================================
  echo   Нужно один раз вписать ключ бота.
  echo   Сейчас откроется файл .env - вставьте ключ после BOT_TOKEN=
  echo   Где взять ключ: Telegram - @BotFather - /mybots - ваш бот - API Token.
  echo   Сохраните файл клавишами Ctrl+S, закройте Блокнот и запустите start.bat ещё раз.
  echo  ==================================================
  echo.
  start "" notepad ".env"
  pause
  goto :eof
)

echo [3/3] Запускаю бота. Окно не закрывайте, пока бот должен работать.
echo       Остановить бота: закройте это окно или нажмите Ctrl + C.
echo.
"%VPY%" -m flowerbot
set "CODE=%errorlevel%"
if "%CODE%"=="0" goto :eof
if "%CODE%"=="2" goto settings
goto err

:settings
echo  Исправьте то, что написано выше (файлы .env, shop.toml или catalog.toml),
echo  сохраните их и запустите start.bat ещё раз.
echo.
pause
goto :eof

:no_python
echo.
echo  Не найден Python. Установите его с https://www.python.org/downloads/
echo  (на первом экране установщика поставьте галочку "Add Python to PATH")
echo  и запустите start.bat ещё раз.
echo.
pause
goto :eof

:old_python
echo.
echo  Нужен Python 3.11 или новее. Установите свежую версию с https://www.python.org/downloads/
echo  (на первом экране установщика поставьте галочку "Add Python to PATH")
echo  и запустите start.bat ещё раз.
echo.
pause
goto :eof

:err
echo.
echo  Что-то пошло не так (текст ошибки - выше). Проверьте интернет и запустите start.bat ещё раз.
echo  Если не помогло - пришлите фото этого окна.
echo.
pause
