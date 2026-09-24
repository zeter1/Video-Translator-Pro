@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1

echo ================================================
echo   Видео Переводчик PRO
echo ================================================

py -3 --version >nul 2>&1
if not errorlevel 1 (
    py -3 video_translator.py
    set EXITCODE=%ERRORLEVEL%
    goto :done
)

python --version >nul 2>&1
if not errorlevel 1 (
    python video_translator.py
    set EXITCODE=%ERRORLEVEL%
    goto :done
)

echo.
echo [ОШИБКА] Python не найден.
echo Установите Python 3 и включите "Add Python to PATH".
set EXITCODE=1

:done
if not "%EXITCODE%"=="0" (
    echo.
    echo Программа завершилась с кодом %EXITCODE%.
    echo Проверьте папки logs и "Логи проблем".
    pause
)
exit /b %EXITCODE%
