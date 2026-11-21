# Glass Nova — Windows 11 AI Voice & Visual Desktop Assistant

Glass Nova is a frameless holographic PySide6 desktop AI assistant for Windows 11 powered by a persistent Gemini Live voice/chat session, offline Vosk `"Hey Nova"` wake-word detection, Windows UIAutomation + Gemini Vision screen interaction, PyCAW automatic background media ducking, webcam hand-gesture mouse control, and the NŪS-inspired safety & guidance feature pack.

---

## Key Features

- **Unified Gemini Live Voice & Chat (`pc_assistant.py`, `ui.py`)**:
  - One persistent bidirectional Gemini Live session shared across voice and typed chat.
  - Adaptive near-field barge-in (`_check_near_field_barge_in`) and self-echo suppression (`_is_self_echo_transcription`).
  - Automatic background media ducking (`media_ducking.py`) via PyCAW when Nova is listening or speaking.
  - Interactive **MIC SENSITIVITY & AUTO NEAREST** oscilloscope HUD widget with live waveform and noise-gate threshold calibration.
- **Offline Wake Word & Floating Companions (`wake_word.py`, `nova_companion_bridge.py`)**:
  - Local Vosk grammar FST wake-word listener for `"Hey Nova"` with safe microphone handover.
  - 5 selectable always-on-top companions (`Original NOVA Robot`, `Ghost`, `Mummy`, `NOVA Painter Pet`, `Cloudy`) bridged via `CompanionBridge`.
  - Global Windows hotkeys (`Ctrl+Shift+N` to toggle companion, `Ctrl+Shift+P` to point at screen) via Win32 `RegisterHotKey`.
- **Verified Desktop & Browser Automation (`desktop_agent.py`, `tools.py`)**:
  - Action-specific verification returning `{"ok": bool, "verified": bool, "message": str}` (Confirmed, Unverified, or Failed).
  - DPI-aware physical coordinate mapping (`screen_agent.py`) + Windows `IUIAutomation` (`UIAutomationCore.dll`) + Gemini Vision fallback.
  - Reliable YouTube ad skipping (`skip_youtube_ad`), timestamp seeking (`control_youtube_video`), and verified browser tab closing (`close_browser_tab`).
- **NŪS-Inspired Safety, Guidance & Workflow Pack**:
  - **Point at Screen (`nova_screen_pointing.py`)**: Permission-gated translucent multi-monitor region selector (`RegionSelector`) that captures and explains only the user-selected rectangle without clicking or typing.
  - **Guided Walkthroughs (`nova_walkthrough.py`)**: Step-by-step interactive guidance (`Next`, `Back`, `Pause`, `Stop`) with non-intrusive `WalkthroughHighlightOverlay` around UIAutomation targets.
  - **Reusable Semantic Workflows (`nova_workflows.py`)**: Records only allow-listed semantic actions (never raw keystrokes, passwords, tokens, or clipboard contents), with a structured `WorkflowEditorDialog` and per-step confirmed replay.
  - **Action Router & Broker (`nova_action_router.py`)**: Deterministic routing, strict `ACTION_SCHEMAS` validation, and `ApprovedActionBroker` gating all live tool calls.

---

## Quick Start on Windows 11

1. Copy `.env.example` to `.env` and add your `GEMINI_API_KEY`:
   ```env
   GEMINI_API_KEY=your_api_key_here
   ```
2. Double-click `Start_Glass_Nova.bat` (or run `.venv\Scripts\python.exe ui.py`).

---

## Running the Test Suites

```powershell
.venv\Scripts\python.exe -B test_nus_feature_pack.py -v
.venv\Scripts\python.exe -B test_screen_interaction.py -v
.venv\Scripts\python.exe -B test_upgrade_integration.py
.venv\Scripts\python.exe -B test_pipeline_e2e.py
```
