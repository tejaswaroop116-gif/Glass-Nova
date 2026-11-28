@echo off
setlocal EnableExtensions
title Glass Nova Launcher

set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

set "VENV_DIR=%APP_DIR%.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "REQ_FILE=%APP_DIR%requirements.txt"
set "NEED_INSTALL=0"

echo.
echo ============================================================
echo   Glass Nova - Windows Launcher
echo ============================================================
echo.

REM 1. If .venv already exists, skip straight to dependency verification
if exist "%VENV_PY%" goto :check_deps

REM 2. Detect compatible Python (prefers 3.12 / 3.11, supports 3.10-3.14)
set "PY_CMD="
if /i "%NOVA_FORCE_NO_PYTHON%"=="1" goto :no_python
call :find_python
if not defined PY_CMD goto :no_python

echo [Glass Nova] First-time setup: creating isolated environment ^(.venv^)...
call %PY_CMD% -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo.
    echo [Glass Nova] ERROR: Could not create the .venv folder.
    echo Make sure you extracted the ZIP into a normal folder ^(such as Desktop or Documents^)
    echo and that Python 3.11 or 3.12 is installed properly.
    echo.
    pause
    exit /b 1
)
set "NEED_INSTALL=1"
goto :maybe_install

:no_python
echo [Glass Nova] Python 3.11 or 3.12 was not found on this PC.
echo.
echo To run Glass Nova:
echo   1. Download Python 3.12 from:
echo      https://www.python.org/downloads/windows/
echo   2. Run the installer and CHECK the box:
echo      "Add python.exe to PATH"
echo   3. Restart this file ^(Start_Glass_Nova.bat^).
echo.
pause
exit /b 1

:check_deps
"%VENV_PY%" -c "import PySide6, google.genai, sounddevice, vosk, comtypes, psutil, PIL, pypdf, cv2" >nul 2>&1
if errorlevel 1 (
    echo [Glass Nova] Missing core packages detected. Installing from requirements.txt...
    set "NEED_INSTALL=1"
)

:maybe_install
if not "%NEED_INSTALL%"=="1" goto :ensure_env
if not exist "%REQ_FILE%" (
    echo [Glass Nova] ERROR: requirements.txt is missing from "%APP_DIR%".
    echo Please re-extract the Glass Nova ZIP archive and try again.
    pause
    exit /b 1
)
echo [Glass Nova] Installing required packages ^(first run only, may take 1-3 minutes^)...
"%VENV_PY%" -m pip install -r "%REQ_FILE%"
if errorlevel 1 (
    echo.
    echo [Glass Nova] ERROR: Package installation failed.
    echo Possible causes:
    echo   - No internet connection during first-time setup.
    echo   - Unsupported Python version ^(Python 3.11 or 3.12 is recommended^).
    echo.
    echo After fixing the issue, double-click Start_Glass_Nova.bat again.
    echo.
    pause
    exit /b 1
)

:ensure_env
if not exist "%APP_DIR%.env" (
    if exist "%APP_DIR%.env.example" (
        copy "%APP_DIR%.env.example" "%APP_DIR%.env" >nul
        echo [Glass Nova] Created .env from .env.example.
        echo              ^(Optional: open .env in Notepad to add your GEMINI_API_KEY for Online Mode.^)
    )
)

if /i "%NOVA_LAUNCHER_DRY_RUN%"=="1" (
    echo [Glass Nova] Dry run complete. Ready to launch ui.py.
    exit /b 0
)

echo [Glass Nova] Starting Glass Nova...
"%VENV_PY%" "%APP_DIR%ui.py"
if errorlevel 1 (
    set "EXIT_CODE=%ERRORLEVEL%"
    echo.
    echo [Glass Nova] Glass Nova closed with an error ^(code %EXIT_CODE%^).
    echo Check the message above for details.
    echo.
    pause
    exit /b %EXIT_CODE%
)

endlocal
exit /b 0

:find_python
for %%C in ("py -3.12" "py -3.11" "py -3" "python") do (
    call %%~C -c "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PY_CMD=%%~C"
        exit /b 0
    )
)
exit /b 1
