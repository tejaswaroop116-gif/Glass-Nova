@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "NEED_INSTALL=0"

if not exist "%VENV_PY%" (
    echo [Glass Nova] Creating virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 (
        echo [Glass Nova] ERROR: Failed to create .venv. Make sure Python 3.11 or 3.12 is installed.
        pause
        exit /b 1
    )
    set "NEED_INSTALL=1"
)

if "%NEED_INSTALL%"=="0" (
    "%VENV_PY%" -c "import PySide6, google.genai, sounddevice, vosk, comtypes, psutil, PIL, pypdf, cv2" >nul 2>&1
    if errorlevel 1 (
        echo [Glass Nova] Missing core dependencies detected. Installing from requirements.txt...
        set "NEED_INSTALL=1"
    )
)

if "%NEED_INSTALL%"=="1" (
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [Glass Nova] ERROR: Dependency installation failed. Check internet connection or run:
        echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
        pause
        exit /b 1
    )
)

if not exist ".env" (
    if exist ".env.example" copy ".env.example" ".env" >nul
)

"%VENV_PY%" ui.py
if errorlevel 1 (
    echo [Glass Nova] Glass Nova exited with error code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)
endlocal
