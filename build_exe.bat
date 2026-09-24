@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

echo ================================================
echo   Сборка VideoTranslatorPRO.exe
echo   По умолчанию: надёжный ONEDIR build
echo ================================================

py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    goto :deps
)
python --version >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    goto :deps
)

echo [ОШИБКА] Python не найден.
pause
exit /b 1

:deps
%PY% -m pip install --upgrade pip
if errorlevel 1 goto :fail
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo Проверяю FFmpeg и ffprobe...
%PY% tools\ensure_ffmpeg_windows.py --install
if errorlevel 1 goto :fail
%PY% -m pip install -r requirements-optional-ai.txt
if errorlevel 1 goto :fail
%PY% -m pip install "pyinstaller>=6,<7"
if errorlevel 1 goto :fail

echo.
echo Запускаю сборку...
%PY% tools\build_exe.py %*
if errorlevel 1 goto :fail

echo.
echo Готово. Результат находится в dist\VideoTranslatorPRO\
pause
exit /b 0

:fail
echo.
echo [ОШИБКА] Сборка не завершена. Смотрите вывод выше.
pause
exit /b 1
