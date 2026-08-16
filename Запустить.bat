@echo off
setlocal
pushd "%~dp0"

set "PYTHON_EXE="
set "PYTHON_ARGS="

where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_EXE=py"
    set "PYTHON_ARGS=-3"
    goto run
)

where python >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_EXE=python"
    goto run
)

call :try_python "%LocalAppData%\Programs\Python\Python313\python.exe"
call :try_python "%LocalAppData%\Programs\Python\Python312\python.exe"
call :try_python "%LocalAppData%\Programs\Python\Python311\python.exe"
call :try_python "%LocalAppData%\Programs\Python\Python310\python.exe"
call :try_python "%ProgramFiles%\Python313\python.exe"
call :try_python "%ProgramFiles%\Python312\python.exe"
call :try_python "%ProgramFiles%\Python311\python.exe"
call :try_python "%ProgramFiles%\Python310\python.exe"
call :try_python "%ProgramFiles(x86)%\Python313\python.exe"
call :try_python "%ProgramFiles(x86)%\Python312\python.exe"
call :try_python "%ProgramFiles(x86)%\Python311\python.exe"
call :try_python "%ProgramFiles(x86)%\Python310\python.exe"
if defined PYTHON_EXE goto run

echo Python not found. Install Python 3.10-3.13 and enable "Add Python to PATH".
goto end

:run
"%PYTHON_EXE%" %PYTHON_ARGS% "video_translator.py"
goto end

:try_python
if not defined PYTHON_EXE if exist "%~1" set "PYTHON_EXE=%~1"
exit /b

:end
popd
pause
