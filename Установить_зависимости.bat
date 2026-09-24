@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    goto :install
)
python --version >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    goto :install
)

echo [ОШИБКА] Python не найден. Установите Python 3 и включите Add Python to PATH.
pause
exit /b 1

:install
echo Обновляю pip...
%PY% -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo Устанавливаю основные зависимости...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo Проверяю FFmpeg и ffprobe...
%PY% tools\ensure_ffmpeg_windows.py --install
if errorlevel 1 goto :fail

if /I "%~1"=="ai" (
    echo Устанавливаю локальные AI-движки для вкладки "Переводчики и голоса"...
    %PY% -m pip install -r requirements-optional-ai.txt
    if errorlevel 1 goto :fail
) else (
    echo.
    echo Основные зависимости установлены.
    echo Локальные переводчики и Piper можно поставить из вкладки программы.
    echo Для установки всех AI-движков заранее запустите:
    echo   Установить_зависимости.bat ai
)

echo.
echo Готово.
pause
exit /b 0

:fail
echo.
echo [ОШИБКА] Установка зависимостей завершилась неуспешно.
pause
exit /b 1
