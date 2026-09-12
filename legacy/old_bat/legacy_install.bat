@echo off
setlocal
pushd "%~dp0"

set PYTHON_CMD=
where py >nul 2>nul
if not errorlevel 1 set PYTHON_CMD=py -3

if "%PYTHON_CMD%"=="" (
    where python >nul 2>nul
    if not errorlevel 1 set PYTHON_CMD=python
)

if "%PYTHON_CMD%"=="" (
    echo Python not found. Install Python 3.10 or 3.11 and enable "Add Python to PATH".
    goto end
)

%PYTHON_CMD% -m pip install --upgrade pip
%PYTHON_CMD% -m pip install -r requirements.txt

:end
popd
pause
