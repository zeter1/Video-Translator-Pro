@echo off
setlocal
pushd "%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
    py -3 "video_translator.py"
    goto end
)

where python >nul 2>nul
if errorlevel 1 (
    echo Python not found. Install Python 3.10 or 3.11 and enable "Add Python to PATH".
    goto end
)

python "video_translator.py"

:end
popd
pause
