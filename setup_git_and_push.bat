@echo off
title Glass Nova — Git & GitHub Setup
cd /d "%~dp0"

echo ========================================================
echo   Glass Nova — Local Git Init ^& GitHub Push Helper
echo ========================================================
echo.

where git >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [1/4] Git was not found on PATH. Installing Git for Windows via winget...
    winget install --id Git.Git -e --source winget
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo Could not auto-install Git via winget.
        echo Please download and install Git from: https://git-scm.com/download/win
        echo Then re-run this script.
        pause
        exit /b 1
    )
    set "PATH=%PATH%;C:\Program Files\Git\cmd;C:\Program Files\Git\bin;%LOCALAPPDATA%\Programs\Git\cmd"
)

where git >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    if exist "C:\Program Files\Git\cmd\git.exe" (
        set "PATH=%PATH%;C:\Program Files\Git\cmd"
    ) else (
        echo Git is installed, please close and reopen this window so PATH refreshes, then run again.
        pause
        exit /b 1
    )
)

echo [2/4] Initializing local Git repository in %CD%...
if not exist ".git" (
    git init -b main
) else (
    echo Local .git repository already exists.
)

echo [3/4] Staging project files (excluding .env, .venv, and backups via .gitignore)...
git add .
git status -s

git diff --cached --quiet
if %ERRORLEVEL% NEQ 0 (
    git commit -m "Initial commit: Glass Nova with verified desktop actions, mic HUD, and NUS feature pack"
    echo Created commit on branch main.
) else (
    echo Working tree is already committed.
)

echo.
echo [4/4] Push to GitHub:
where gh >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo GitHub CLI (gh) detected!
    set /p REPO_NAME="Enter new GitHub repository name (default: Glass-Nova): "
    if "%REPO_NAME%"=="" set "REPO_NAME=Glass-Nova"
    gh repo create "%REPO_NAME%" --private --source=. --remote=origin --push
    echo.
    echo Done! Your repository is live on GitHub.
    pause
    exit /b 0
)

echo.
echo Enter your GitHub repository URL (for example: https://github.com/YOUR_USERNAME/Glass-Nova.git)
echo (If you haven't created an empty repo on GitHub yet, create one at https://github.com/new first)
set /p REMOTE_URL="GitHub Remote URL (or press Enter to skip remote push for now): "

if not "%REMOTE_URL%"=="" (
    git remote remove origin >nul 2>nul
    git remote add origin "%REMOTE_URL%"
    git branch -M main
    git push -u origin main
    echo.
    echo Successfully pushed Glass Nova to %REMOTE_URL% !
) else (
    echo.
    echo Local Git repository is initialized and committed on branch 'main'.
    echo Whenever you are ready to push to GitHub, run:
    echo   git remote add origin https://github.com/YOUR_USERNAME/Glass-Nova.git
    echo   git push -u origin main
)

echo.
pause
