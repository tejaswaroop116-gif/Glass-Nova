# Glass Nova — Windows Installer Packaging & GitHub Release Guide

This guide explains how to build and publish a beginner-friendly Windows installer (`GlassNova-Setup-<version>.exe`) using **PyInstaller** + **Inno Setup 6**.

## 1. Prerequisites (Build Machine Only — End Users Need Nothing)
1. **Python 3.11 or 3.12** (64-bit Windows) with Glass Nova dependencies installed in `.venv`:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip.exe install -r requirements.txt pyinstaller
   ```
2. **Inno Setup 6** (free, open-source): install from https://jrsoftware.org/isdl.php (default install path `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`).

## 2. Verify Configuration Before Building
```powershell
.\.venv\Scripts\python.exe build_installer.py --check
```
Confirm:
- `Version` matches `APP_VERSION` in `nova_updater.py`.
- `Inno Setup Script`, `User Data Guard`, `Secret .env Guard`, and `ISCC.exe Compiler` all report `OK`.

## 3. Build the Windows Installer
```powershell
.\.venv\Scripts\python.exe build_installer.py
```
This runs two reproducible steps:
1. Bundles Glass Nova into `dist/GlassNova/GlassNova.exe` via `build_exe.py` (excludes tests, caches, `.env`, and unused dev modules; supports `--backend` child-process dispatch so users never need Python installed).
2. Compiles `installer/GlassNovaSetup.iss` with Inno Setup 6 into:
   ```text
   dist/installer/GlassNova-Setup-<version>.exe
   ```

## 4. User Data & Security Guarantees
- **Install directory:** Per-user `%LOCALAPPDATA%\Programs\Glass Nova` by default (no admin UAC needed) or system-wide `Program Files` if chosen.
- **User data location:** `%APPDATA%\PC_Complete_Assistant\` stores `.env` (optional `GEMINI_API_KEY`), DPAPI-encrypted workflows, tasks, memories, permissions, and Ollama preferences.
- **Upgrades:** Overwrite only application files; user data is **never touched**.
- **Uninstall:** Removes application files and shortcuts; asks explicit user confirmation (defaulting to **No**) before removing `%APPDATA%\PC_Complete_Assistant\`.
- **Code Signing Note:** Unsigned `.exe` installers may trigger a Windows SmartScreen prompt on first download until signed with an Authenticode certificate. Never bypass Windows security controls.

## 5. Publishing to GitHub Releases
1. Bump `APP_VERSION` in `nova_updater.py` (e.g., `1.1.0-beta` or `1.1.0`) and commit to `main`.
2. Create a matching GitHub Release tag (e.g., `v1.1.0-beta`) at `https://github.com/tejaswaroop30/Glass-Nova/releases/new`.
3. Upload `dist/installer/GlassNova-Setup-<version>.exe` (and optionally `dist/GlassNova-Windows-Source-Launcher.zip`) as release assets.
4. Once published, Glass Nova's in-app **Settings → Software Updates → Check for Updates** will automatically detect the new release and display its release notes + verified download link.
