# Glass Nova — AI Desktop Assistant for Windows

[![Glass Nova Tests](https://github.com/tejaswaroop30/Glass-Nova/actions/workflows/tests.yml/badge.svg)](https://github.com/tejaswaroop30/Glass-Nova/actions/workflows/tests.yml)
![Platform: Windows 10/11](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-0078D6?logo=windows)
![Python: 3.11–3.12](https://img.shields.io/badge/Python-3.11%E2%80%933.12-3776AB?logo=python&logoColor=white)
![UI: PySide6](https://img.shields.io/badge/UI-PySide6-41CD52)

**Glass Nova** is an open-source, AI-powered Windows desktop assistant built in Python and PySide6. It combines real-time **Gemini Live** voice and chat interaction, offline `"Hey Nova"` wake-word activation, **Offline Local Mode**, verified **Windows desktop automation**, **screen and document understanding**, **webcam hand-gesture mouse control**, and **animated desktop companions** behind a fail-closed permission system.

> **Website:** https://tejaswaroop30.github.io/Glass-Nova/
> **Repository:** https://github.com/tejaswaroop30/Glass-Nova

---

## Table of Contents

- [Key Features](#key-features)
- [Architecture Overview](#architecture-overview)
- [Requirements & Compatibility](#requirements--compatibility)
- [Installation & Quick Start](#installation--quick-start)
- [API Key Setup](#api-key-setup)
- [Offline Local Mode & Local AI (Ollama)](#offline-local-mode--local-ai-ollama)
- [Security, Privacy & Permissions](#security-privacy--permissions)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Frequently Asked Questions (FAQ)](#frequently-asked-questions-faq)
- [Current Limitations](#current-limitations)
- [Roadmap](#roadmap)
- [License](#license)
- [Contributing](#contributing)

---

## Key Features

### Voice & Conversational AI
- **Unified Gemini Live Voice & Chat**: One persistent bidirectional Gemini Live session shared across spoken voice and typed chat.
- **Adaptive Near-Field Barge-In & Echo Suppression**: Interrupt Nova naturally while she is speaking, with self-echo filtering and automatic background media ducking via PyCAW.
- **Mic Sensitivity & AUTO NEAREST HUD**: Live oscilloscope waveform and automatic ambient noise-gate calibration.

### Offline Wake Word & Local Mode
- **Local `"Hey Nova"` Wake-Word Detection**: Runs locally on CPU using Vosk grammar recognition with clean microphone handover and automatic error recovery.
- **Offline Local Mode (0ms Execution)**: Works without an internet connection or API key for common Windows tasks — opening/closing apps and folders, volume/brightness control, window and browser tab controls, clipboard, screenshots, system status, local tasks & memories, calculator math, mic sensitivity, companion toggling, hand gestures, and voice/chat permission management, with Windows SAPI offline speech replies.

### Verified Desktop & Browser Automation
- **Action Verification Contract**: Tools return structured `{"ok": bool, "verified": bool, "message": str}` results so Nova distinguishes confirmed actions from unverified attempts or failures.
- **Windows UIAutomation + Gemini Vision**: DPI-aware, multi-monitor coordinate mapping using `UIAutomationCore.dll` with Gemini Vision fallback.
- **Browser & YouTube Controls**: Verified browser tab management (`close_browser_tab`, `browser_tab_action`), YouTube ad skipping (`skip_youtube_ad`), and timestamp seeking (`control_youtube_video`).

### Screen, Document & Video Understanding
- **Screen Inspection**: Permission-gated active window and multi-monitor screenshot analysis (`screen_agent.py`).
- **Point at Screen**: Translucent multi-monitor region selector (`Ctrl+Shift+P`) that captures and explains only the selected rectangle without clicking or typing.
- **Document & PDF Analysis**: Multi-page PDF/document reading with optional writing to Notepad (`document_agent.py`).
- **Video / Lecture Frame Analysis**: Explains visible slides, whiteboards, and diagrams (`video_agent.py`).

### Safety, Guidance & Companions
- **Guided Walkthroughs**: Step-by-step interactive guidance (`Next`, `Back`, `Pause`, `Stop`) with non-intrusive visual highlights around UI elements.
- **Reusable Semantic Workflows**: Records only allow-listed semantic actions (never raw keystrokes, passwords, API keys, or clipboard secrets), with a structured step editor and per-step user confirmation on replay.
- **Webcam Hand-Gesture Mouse Control**: Move the cursor, click, drag, and scroll using hand landmarks (`hand_gesture.py`).
- **4 Animated Desktop Companions**: `Original NOVA Robot`, `Ghost`, `NOVA Painter Pet`, and `Cloudy` (toggle anytime with `Ctrl+Shift+N`).

---

## Architecture Overview

Glass Nova runs as a two-process desktop architecture: a frameless PySide6 glass UI (`ui.py`) communicating over structured JSON IPC (`@@NOVA@@...`) with the backend engine (`pc_assistant.py`).

| Layer | Modules | Responsibility |
| :--- | :--- | :--- |
| **Desktop UI & Theme** | `ui.py`, `ui_theme.py`, `ui_dialogs.py` | Frameless glass window, system tray, permission dialogs, workflow editor, mic HUD |
| **Companions & Bridge** | `bot.py`, `cloudy.py`, `ghost.py`, `pet_engine.py`, `nova_companion_bridge.py` | Animated always-on-top desktop companions and wake-word integration |
| **Backend & Audio** | `pc_assistant.py`, `nova_audio.py`, `nova_tool_defs.py`, `wake_word.py`, `media_ducking.py` | Gemini Live session, offline local mode, non-blocking audio playback, Vosk wake word, PyCAW ducking |
| **Routing & Safety** | `nova_action_router.py`, `permissions.py` | Allow-listed `ACTION_SCHEMAS`, regex/structural argument validation, `ApprovedActionBroker`, fail-closed permissions |
| **Desktop & Media Agents** | `desktop_agent.py`, `desktop_agent_browser.py`, `screen_agent.py`, `document_agent.py`, `video_agent.py`, `hand_gesture.py` | UIAutomation, browser/YouTube controls, multi-monitor capture, PDF/video analysis, gesture mouse |
| **System & File Tools** | `tools.py`, `tools_state.py`, `tools_files.py` | App/window/volume/brightness tools, safe AST calculator, file/folder tools, native Windows clipboard, local tasks & memories |
| **Offline AI & Knowledge** | `nova_offline_ai.py`, `nova_offline_agent.py`, `nova_local_knowledge.py` | Local Ollama runtime (4 GB VRAM-safe), rolling context manager, bounded offline agent, opt-in local knowledge index, AST-restricted Python sandbox |
| **Guidance & Workflows** | `nova_screen_pointing.py`, `nova_walkthrough.py`, `nova_workflows.py` | Region pointing selector, step-by-step walkthroughs, semantic workflow recorder & player |

---

## Requirements & Compatibility

- **Operating System**: Windows 10 or Windows 11 (Windows-specific Win32, UIAutomation, PyCAW, and SAPI APIs are used).
- **Python**: Python 3.11 or 3.12 (recommended).
- **Hardware (Optional)**:
  - Microphone & speakers for voice chat and `"Hey Nova"` wake-word detection.
  - Webcam for hand-gesture mouse control.
- **API Key**: A Google Gemini API key is required for online Gemini Live voice/chat and vision features. Local offline commands work without an API key.

---

## Installation & Quick Start

### Option 1: One-Click Setup & Launch (Windows)
1. Clone the repository:
   ```powershell
   git clone https://github.com/tejaswaroop30/Glass-Nova.git
   cd Glass-Nova
   ```
2. Double-click **`Start_Glass_Nova.bat`** (automatically creates `.venv`, installs dependencies from `requirements.txt`, copies `.env.example` to `.env`, and launches `ui.py`).

### Option 2: Manual Setup (PowerShell)
```powershell
git clone https://github.com/tejaswaroop30/Glass-Nova.git
cd Glass-Nova
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
python ui.py
```

### Optional: Build a Standalone Windows `.exe`
```powershell
python build_exe.py
```
Outputs a bundled application to `dist\GlassNova\GlassNova.exe`.

---

## API Key Setup

1. Copy `.env.example` to `.env` (never commit `.env` — it is ignored by `.gitignore`).
2. Add your Google Gemini API key:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   MODEL=gemini-3.8-live
   VISION_MODEL=gemini-3.5-flash-lite
   ```
3. Restart Glass Nova. If `GEMINI_API_KEY` is blank or unreachable, Glass Nova starts automatically in **Offline Local Mode**.

---

## Offline Local Mode & Local AI (Ollama)

When offline or without an API key, Glass Nova automatically switches into **Hybrid Offline Mode**:

1. **Instant Deterministic Commands (0ms, no model required)** via chat or offline Vosk voice:
   - **Nova & Companion**: `"close yourself"`, `"minimize nova"`, `"turn on bot"`, `"turn off bot"`, `"toggle bot"`
   - **Permissions**: `"give permission for hand gesture"`, `"allow mouse"`, `"allow screen"`, `"revoke all permissions"`, `"check permissions"`
   - **Hand Gestures & Mic**: `"start/stop/pause/resume hand gesture"`, `"show/hide hand preview"`, `"enable auto nearest"`, `"set mic sensitivity to 70"`
   - **Apps & Folders**: `"open calculator"`, `"close notepad"`, `"open downloads"`, `"list desktop"`, `"create folder demo on desktop"`, `"search files for <name>"`
   - **System & Productivity**: `"set volume to 50"`, `"mute"`, `"brightness up"`, `"take screenshot"`, `"system status"`, `"what time is it"`, `"calculate 25 * 4"`, `"add task ..."`, `"remember ... is ..."`, `"read clipboard"`, `"new tab"`, `"close tab"`, `"scroll down"`
   - **Offline Diagnostics & Local Knowledge**: `"offline capabilities"`, `"list indexed folders"`, `"index folder <path>"`, `"search local knowledge for <query>"`, `"stop task"`
2. **Local Ollama AI & Bounded Offline Agent** (when [Ollama](https://ollama.com/) is running locally):
   - Supports local Q&A, coding assistance, reasoning, summarization, and bounded multi-step Windows plans routed through `ApprovedActionBroker` (max 5 steps, fail-closed permissions, instant `"stop task"` cancellation).
   - Tuned for **16 GB RAM + 4 GB VRAM GPUs** (e.g., RTX 3050 Laptop): single-model VRAM residency (unloads previous model on switch), `num_ctx=4096` rolling context compression, and automatic CPU fallback (`"offline cpu mode"` / `"offline gpu mode"`).
   - Recommended lightweight models for 4 GB VRAM:
     ```powershell
     ollama pull qwen2.5:3b      # fast general reasoning & coding (~2.2 GB VRAM)
     ollama pull moondream       # optional local vision model (~1.7 GB VRAM)
     ```
   - **Opt-In Local Knowledge**: Never auto-indexes private folders. Users explicitly choose folders (`"index folder <path>"`) to search `.txt`, `.md`, `.py`, `.json`, `.csv`, `.pdf`, and `.docx` locally.

---

## Security, Privacy & Permissions

Glass Nova is designed around **fail-closed permission enforcement**:

- **9 Separate Permission Categories** (`permissions.py`): `screen_view`, `continuous_screen`, `document_access`, `media_access`, `mouse_keyboard`, `file_modify`, `file_delete`, `app_install`, and `form_purchase`.
- **Fail-Closed by Default**: If no permission prompt handler is active or a permission cannot be verified, protected tools deny the action instead of silently allowing it.
- **Always-Prompt Sensitive Categories**: `file_delete`, `form_purchase`, and `continuous_screen` always require explicit user confirmation.
- **Allow-Listed Action Broker**: Live actions pass through `validate_proposed_action()` and `ApprovedActionBroker`, blocking unlisted tools, shell metacharacters, dangerous system commands, and unsafe URL schemes (`javascript:`, `file:///`).
- **Local Privacy**: Local tasks, memories, and permission audit logs are stored as plain JSON files under `%APPDATA%\PC_Complete_Assistant\`. Workflow recording strips secrets and rejects raw keystrokes.

---

## Testing

Glass Nova includes 7 automated test suites verified on every push via GitHub Actions (`.github/workflows/tests.yml`):

```powershell
# Syntax check across all Python modules
.venv\Scripts\python.exe -B -c "import ast, pathlib; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in sorted(pathlib.Path('.').glob('*.py'))]; print('AST OK')"

# Run automated test suites
.venv\Scripts\python.exe -B test_security_audit.py -v
.venv\Scripts\python.exe -B test_offline_capability.py -v
.venv\Scripts\python.exe -B test_website.py -v
.venv\Scripts\python.exe -B test_nus_feature_pack.py -v
.venv\Scripts\python.exe -B test_screen_interaction.py -v
.venv\Scripts\python.exe -B test_upgrade_integration.py
.venv\Scripts\python.exe -B test_pipeline_e2e.py
```

---

## Troubleshooting

- **Nova starts in Offline Mode**: Confirm `GEMINI_API_KEY` is set in `.env` and that your network can reach Google's Gemini API.
- **"Hey Nova" wake word takes a moment on first run**: Vosk downloads the lightweight `vosk-model-small-en-us-0.15` model (~40 MB) once and caches it locally.
- **Microphone busy or not responding**: Toggle voice off/on in the UI; `wake_word.py` releases its input stream cleanly on pause and retries automatically with backoff if a mic error occurs.
- **Frosted glass blur not visible**: Enable **Settings → Personalization → Colors → Transparency effects** in Windows. Clear glass mode works even when transparency is off.
- **Screen clicks seem offset on high-DPI monitors**: Glass Nova enables Windows Per-Monitor DPI Awareness V2 automatically; ensure display scaling changes are applied before launching Nova.

---

## Frequently Asked Questions (FAQ)

**Does Glass Nova work without a Gemini API key?**
Yes. Offline Local Mode supports local apps, folders, volume, brightness, window/tab controls, clipboard, screenshots, tasks, memories, calculator math, companion controls, hand gestures, and permission commands without an API key.

**Does Glass Nova monitor my screen in the background?**
No. Screen capture and continuous observation require explicit `screen_view` / `continuous_screen` permission and display an active visual indicator badge in the top bar whenever screen observation is active.

**Does it work on macOS or Linux?**
No. Glass Nova is built specifically for Windows 10/11 and relies on native Win32, UIAutomation, PyCAW, and SAPI APIs.

---

## Current Limitations

- **Windows-only**: Not cross-platform for macOS or Linux.
- **Offline voice vocabulary**: Offline voice recognition uses a fixed Vosk phrase grammar; open-ended Q&A and vision tasks require an online Gemini API key.
- **Executable packaging**: `build_exe.py` is included for PyInstaller builds, but signed installers / auto-updaters are not yet provided.
- **Large modules**: `pc_assistant.py` (~2,030 lines) and `desktop_agent.py` (~1,465 lines) still contain core orchestration loops that can be further modularized over time.

---

## Roadmap

- [ ] Signed Windows installer / portable release artifact via GitHub Releases.
- [ ] Optional local LLM backend (e.g., Ollama) for open-ended offline Q&A.
- [ ] Expanded multi-step workflow templates and visual workflow debugging.
- [ ] Additional unit tests for live audio handover and PyInstaller smoke checks.

---

## License

No open-source license file (`LICENSE`) is currently present in this repository. Until a license (such as MIT or Apache-2.0) is added by the repository owner, default copyright applies (all rights reserved).

---

## Contributing

Contributions, bug reports, and feature ideas are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.
