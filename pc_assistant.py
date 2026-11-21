"""Nova backend - ONE persistent Gemini Live session shared by voice and typed chat.

Run by ui.py as a child process, or standalone in a terminal.
Protocol (all UTF-8, one item per line):
  UI -> backend (stdin, JSON):   {"cmd":"text","text":"hello"} | {"cmd":"mic_start"} | {"cmd":"mic_stop"}
                                 {"cmd":"wake_greet"} | {"cmd":"voice_output","on":true} | {"cmd":"shutdown"}
                                 {"cmd":"perm_response",...} | {"cmd":"perm_revoke",...} | {"cmd":"gesture_control",...}
  backend -> UI (stdout):        lines starting with @@NOVA@@ followed by a JSON event
"""
import os, sys, asyncio, socket, json, threading, uuid, time, queue
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv
from google import genai
from google.genai import types

try:
    import sounddevice as sd
    _SD_ERROR = ""
except Exception as _e:
    sd = None
    _SD_ERROR = f"{type(_e).__name__}: {_e}"

from tools import (
    open_app, open_file, open_url, search_web, play_youtube, open_folder, system_status,
    take_screenshot, type_text, press_key, hotkey, click_mouse,
    calculator_operation, write_text_file, read_text_file,
    volume_control, brightness_control, window_control, app_control,
    mouse_control, clipboard_control, create_folder, list_folder, rename_file, copy_file, move_file, delete_file,
    memory_control, task_control
)
from permissions import permissions, CATEGORIES
from screen_agent import analyze_screen
from document_agent import analyze_document
from video_agent import analyze_video
from desktop_agent import (
    click_element,
    skip_youtube_ad,
    close_browser_tab,
    control_youtube_video,
    type_text_into_ui,
    write_in_notepad,
    press_keyboard_hotkey,
    scroll_desktop,
)
from hand_gesture import gesture_manager
from media_ducking import ducker
from nova_action_router import NovaActionRouter, ApprovedActionBroker, validate_proposed_action, ACTION_SCHEMAS, match_offline_command
from nova_walkthrough import Walkthrough, WalkthroughStep, walkthrough_from_plan
from nova_workflows import (
    Workflow,
    WorkflowStep,
    WorkflowRecorder,
    WorkflowStore,
    WorkflowPlayer,
    ALLOWLISTED_WORKFLOW_ACTIONS,
)

HERE = Path(__file__).resolve().parent
WORKFLOWS_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "PC_Complete_Assistant" / "workflows"
workflow_recorder = WorkflowRecorder(allowed_actions=ALLOWLISTED_WORKFLOW_ACTIONS)
workflow_store = WorkflowStore(WORKFLOWS_DIR)
_active_walkthrough: Walkthrough | None = None
_active_workflow_player: WorkflowPlayer | None = None
load_dotenv(HERE / ".env")
API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("MODEL", "gemini-3.8-live")

MARK = "@@NOVA@@"
_EMIT_LOCK = threading.Lock()
IS_TTY = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False


def emit(ev_type: str, **kwargs):
    """Thread-safe structured event prefixed with @@NOVA@@ for ui.py consumption."""
    line = MARK + json.dumps({"t": ev_type, **kwargs}, ensure_ascii=False, default=str)
    with _EMIT_LOCK:
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception:
            pass


if not API_KEY:
    emit("fatal", text="GEMINI_API_KEY is missing from .env")
    raise SystemExit("GEMINI_API_KEY is missing from .env")

client = genai.Client(api_key=API_KEY)

CLOUDY_BRIDGE_HOST = "127.0.0.1"
CLOUDY_BRIDGE_PORT = 47631
_cloudy_warned = False


def _notify_cloudy_state(state: str):
    """Send NOVA's real playback state to the local Cloudy companion."""
    global _cloudy_warned
    if state not in ("speaking", "idle", "listening", "thinking", "waiting_approval", "executing", "paused", "error"):
        return
    mapped = "thinking" if state in ("waiting_approval", "executing") else ("idle" if state in ("paused", "error") else state)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as bridge:
            bridge.sendto(mapped.encode("ascii"), (CLOUDY_BRIDGE_HOST, CLOUDY_BRIDGE_PORT))
    except OSError as exc:
        if not _cloudy_warned:
            _cloudy_warned = True
            emit("log", text=f"[cloudy] state bridge unavailable: {exc}")


# ---- Step approval IPC bridge (for Workflow Replay & Action Broker) ----
_pending_step_approvals = {}
_pending_step_approvals_lock = threading.Lock()


def _request_step_approval_from_ui(step_index: int, total_steps: int, action_name: str, arguments: dict, description: str) -> bool:
    """Ask the user in the UI (or by voice) to approve a workflow replay or brokered action step."""
    req_id = uuid.uuid4().hex[:8]
    ev = threading.Event()
    with _pending_step_approvals_lock:
        _pending_step_approvals[req_id] = {"event": ev, "approved": False}
    emit("state", v="waiting_approval")
    emit(
        "workflow_step_request",
        id=req_id,
        step=step_index,
        total=total_steps,
        action=action_name,
        arguments=arguments,
        description=description,
    )
    ev.wait(timeout=120.0)
    with _pending_step_approvals_lock:
        entry = _pending_step_approvals.pop(req_id, {})
    return bool(entry.get("approved", False))


# ---- Permission prompt callback bridge (UI IPC + Hands-Free Voice Approval) ----
_pending_perms = {}
_pending_perms_lock = threading.Lock()
_perm_audio_queue = queue.Queue(maxsize=45)
_mic_stream_state = {
    "active": False,
    "speaking": False,
    "mute_until": 0.0,
    "voice_hangover_until": 0.0,
    "silent_frames_sent": 0,
    "sensitivity": 68,
    "auto_nearest": False,
    "ambient_rms": 42.0,
    "last_level_emit_ts": 0.0,
    "playback_rms": 0.0,
    "barge_in_hits": 0,
    "barge_in_triggered": False,
}


def _sensitivity_to_gate_rms(sensitivity_pct: int) -> float:
    """Convert a 0..100% mic sensitivity percentage to a 16-bit PCM RMS noise-gate threshold.
    Higher sensitivity % -> lower RMS gate threshold (68% -> ~121 RMS, 100% -> 25 RMS, 0% -> 620 RMS)."""
    s = max(0, min(100, int(round(float(sensitivity_pct)))))
    norm = (100.0 - s) / 100.0
    return round(25.0 + (norm ** 1.6) * 595.0, 1)


def _gate_rms_to_sensitivity(gate_rms: float) -> int:
    """Convert a 16-bit PCM RMS noise-gate threshold back to a 0..100% mic sensitivity percentage."""
    clamped = max(25.0, min(620.0, float(gate_rms)))
    norm = ((clamped - 25.0) / 595.0) ** (1.0 / 1.6)
    return max(0, min(100, int(round(100.0 * (1.0 - norm)))))


MIC_NOISE_GATE_RMS = float(os.getenv("NOVA_MIC_NOISE_GATE_RMS", str(_sensitivity_to_gate_rms(68))))
_vosk_model = None
_vosk_lock = threading.Lock()


def set_mic_sensitivity(sensitivity: int = None, auto_nearest: bool = None, calibrate_now: bool = False) -> dict:
    """Update microphone sensitivity (0..100%) and/or AUTO NEAREST voice isolation mode."""
    global MIC_NOISE_GATE_RMS
    if auto_nearest is not None:
        _mic_stream_state["auto_nearest"] = bool(auto_nearest)
    if sensitivity is not None:
        s = max(0, min(100, int(round(float(sensitivity)))))
        _mic_stream_state["sensitivity"] = s
        if auto_nearest is None:
            _mic_stream_state["auto_nearest"] = False
        MIC_NOISE_GATE_RMS = _sensitivity_to_gate_rms(s)
    if _mic_stream_state.get("auto_nearest") or calibrate_now:
        ambient = float(_mic_stream_state.get("ambient_rms", 42.0) or 42.0)
        target_gate = max(85.0, min(480.0, ambient * 2.2 + 75.0))
        MIC_NOISE_GATE_RMS = round(target_gate, 1)
        _mic_stream_state["sensitivity"] = _gate_rms_to_sensitivity(MIC_NOISE_GATE_RMS)
    cur_sens = int(_mic_stream_state.get("sensitivity", 68))
    cur_auto = bool(_mic_stream_state.get("auto_nearest", False))
    emit(
        "mic_sensitivity",
        sensitivity=cur_sens,
        auto_nearest=cur_auto,
        gate_rms=round(MIC_NOISE_GATE_RMS, 1),
    )
    mode_str = " (AUTO NEAREST enabled)" if cur_auto else ""
    return {
        "ok": True,
        "verified": True,
        "sensitivity": cur_sens,
        "auto_nearest": cur_auto,
        "gate_rms": round(MIC_NOISE_GATE_RMS, 1),
        "message": f"Microphone sensitivity set to {cur_sens}%{mode_str}.",
    }


def mic_sensitivity_control(action: str = "set", value: int = None, amount: int = 10) -> dict:
    """Control Nova's Microphone Sensitivity & AUTO NEAREST noise-gate filter by voice or UI.
    Actions: 'set', 'increase', 'decrease', 'auto_nearest' ('auto', 'nearest'), 'manual', 'get'."""
    act = (action or "set").strip().lower().replace("-", "_").replace(" ", "_")
    cur = int(_mic_stream_state.get("sensitivity", 68))
    if act in ("get", "status", "current", "read"):
        auto = bool(_mic_stream_state.get("auto_nearest", False))
        mode = "AUTO NEAREST" if auto else "Manual"
        return {
            "ok": True,
            "verified": True,
            "sensitivity": cur,
            "auto_nearest": auto,
            "gate_rms": round(MIC_NOISE_GATE_RMS, 1),
            "message": f"Microphone sensitivity is {cur}% ({mode} mode).",
        }
    if act in ("auto_nearest", "auto", "nearest", "enable_auto", "calibrate"):
        return set_mic_sensitivity(auto_nearest=True, calibrate_now=True)
    if act in ("manual", "disable_auto", "auto_off"):
        return set_mic_sensitivity(sensitivity=cur, auto_nearest=False)
    if act in ("toggle_auto", "toggle_nearest"):
        new_auto = not bool(_mic_stream_state.get("auto_nearest", False))
        return set_mic_sensitivity(auto_nearest=new_auto, calibrate_now=new_auto)
    if act in ("up", "increase", "higher", "more"):
        delta = int(amount if amount is not None else (value if value is not None else 10))
        return set_mic_sensitivity(sensitivity=min(100, cur + max(1, delta)), auto_nearest=False)
    if act in ("down", "decrease", "lower", "less"):
        delta = int(amount if amount is not None else (value if value is not None else 10))
        return set_mic_sensitivity(sensitivity=max(0, cur - max(1, delta)), auto_nearest=False)
    target = value if value is not None else (amount if amount is not None else 68)
    return set_mic_sensitivity(sensitivity=int(target), auto_nearest=False)


def _is_mic_muted_for_echo() -> bool:
    """Return True while Nova's speaker output is active or within the post-speech acoustic echo tail,
    unless the user has triggered near-field barge-in."""
    if _mic_stream_state.get("barge_in_triggered"):
        return False
    if _mic_stream_state.get("speaking"):
        return True
    return time.monotonic() < float(_mic_stream_state.get("mute_until", 0.0) or 0.0)


def _check_near_field_barge_in(mic_rms: float) -> bool:
    """Adaptive echo-reference barge-in detector: compares incoming mic RMS against Nova's live speaker playback RMS.
    If the user speaks clearly close to the mic (>2.2x estimated speaker echo and >380 RMS) for 3 consecutive frames,
    triggers instant barge-in so the user can interrupt Nova while she is speaking."""
    play_rms = float(_mic_stream_state.get("playback_rms", 0.0) or 0.0)
    barge_thresh = max(380.0, MIC_NOISE_GATE_RMS * 2.8, play_rms * 0.42 + 180.0)
    if mic_rms >= barge_thresh:
        hits = int(_mic_stream_state.get("barge_in_hits", 0) or 0) + 1
        _mic_stream_state["barge_in_hits"] = hits
        if hits >= 3:
            _mic_stream_state["barge_in_triggered"] = True
            _mic_stream_state["speaking"] = False
            _mic_stream_state["mute_until"] = 0.0
            return True
    else:
        _mic_stream_state["barge_in_hits"] = 0
    return bool(_mic_stream_state.get("barge_in_triggered", False))


def _pcm16_rms(data: bytes) -> float:
    """Compute root-mean-square amplitude of 16-bit signed mono PCM audio."""
    if not data or len(data) < 2:
        return 0.0
    import array
    import math
    samples = array.array("h")
    n = len(data) // 2
    samples.frombytes(data[: n * 2])
    if not samples:
        return 0.0
    total = sum(s * s for s in samples)
    return math.sqrt(total / len(samples))


def _is_self_echo_transcription(user_text: str, recent_model_text: str, last_model_ts: float) -> bool:
    """Return True if user_text is an acoustic echo of Nova's own recent spoken output."""
    if not user_text or not recent_model_text:
        return False
    if (time.monotonic() - float(last_model_ts or 0.0)) > 12.0 and not _is_mic_muted_for_echo():
        return False
    import re
    u_words = re.findall(r"[a-z0-9]+", user_text.lower())
    m_words = re.findall(r"[a-z0-9]+", recent_model_text.lower())
    if not u_words or not m_words:
        return False
    u_joined = " ".join(u_words)
    m_joined = " ".join(m_words)
    if len(u_words) <= 8 and u_joined in m_joined:
        return True
    if len(u_words) >= 2:
        matched = sum(1 for w in u_words if w in m_words)
        if matched / len(u_words) >= 0.75 and len(u_words) <= 7:
            return True
    return False

PERM_VOICE_GRAMMAR = json.dumps([
    "allow", "allow for session", "allow session", "allow once", "just once", "once",
    "yes", "yeah", "yep", "sure", "okay", "ok", "go ahead",
    "grant", "grant permission", "give permission", "i give permission",
    "you have permission", "permission granted",
    "approve", "accept", "enable",
    "allow camera", "allow screen", "allow mouse", "allow keyboard", "allow all",
    "camera", "screen", "mouse", "keyboard",
    "deny", "no", "nope", "cancel", "reject", "stop", "dont allow", "do not allow",
    "[unk]"
])


def _get_vosk_model():
    global _vosk_model
    if _vosk_model is not None:
        return _vosk_model if _vosk_model is not False else None
    with _vosk_lock:
        if _vosk_model is None:
            try:
                import vosk
                vosk.SetLogLevel(-1)
                _vosk_model = vosk.Model(model_name="vosk-model-small-en-us-0.15")
            except Exception as e:
                emit("log", text=f"[Voice Permission] Vosk model unavailable: {e}")
                _vosk_model = False
    return _vosk_model if _vosk_model is not False else None


def _parse_voice_perm_decision(text: str):
    """Parse spoken approval/denial when a permission request is waiting."""
    if not text:
        return None
    clean = text.lower().replace("'", "").replace(",", " ").replace(".", " ").strip()
    tokens = [t for t in clean.split() if t != "[unk]"]
    if not tokens:
        return None
    words = set(tokens)
    if words & {"deny", "no", "nope", "cancel", "reject", "stop", "dont", "not", "never"}:
        return "deny"
    if "once" in words:
        return "allow_once"
    if words & {
        "allow", "yes", "yeah", "yep", "sure", "okay", "ok", "grant", "granted",
        "give", "permission", "approve", "accept", "enable", "ahead", "session"
    }:
        return "allow_session"
    return None


def _resolve_pending_perm_by_voice(decision: str):
    """Resolve any waiting permission request via voice and instruct UI to close the dialog."""
    with _pending_perms_lock:
        for req_id, info in list(_pending_perms.items()):
            if not info["event"].is_set():
                info["decision"] = decision
                info["voice"] = True
                info["event"].set()
                emit("perm_voice_resolved", id=req_id, category=info.get("category", ""), decision=decision)


def _wait_for_perm_with_voice(req_id: str, category: str, ev: threading.Event, timeout: float = 90.0):
    """Waits for either UI button click OR spoken 'Allow' / 'Yes' / 'Deny' via Vosk."""
    model = _get_vosk_model()
    recognizer = None
    if model is not None:
        try:
            import vosk
            recognizer = vosk.KaldiRecognizer(model, 16000, PERM_VOICE_GRAMMAR)
        except Exception:
            recognizer = None

    while not _perm_audio_queue.empty():
        try:
            _perm_audio_queue.get_nowait()
        except Exception:
            break

    temp_stream = None
    try:
        if recognizer is not None and sd is not None and not _mic_stream_state.get("active"):
            time.sleep(0.12)
            if not _mic_stream_state.get("active") and not ev.is_set():
                def _temp_cb(indata, frames, time_info, status):
                    if not ev.is_set() and not _mic_stream_state.get("speaking"):
                        try:
                            if _perm_audio_queue.full():
                                _perm_audio_queue.get_nowait()
                            _perm_audio_queue.put_nowait(bytes(indata))
                        except Exception:
                            pass
                try:
                    temp_stream = sd.RawInputStream(
                        samplerate=16000, blocksize=1024, channels=1, dtype="int16", callback=_temp_cb
                    )
                    temp_stream.start()
                except Exception:
                    temp_stream = None

        deadline = time.monotonic() + timeout
        last_partial = ""
        partial_since = 0.0

        while not ev.is_set() and time.monotonic() < deadline:
            if recognizer is None:
                ev.wait(timeout=0.2)
                continue
            try:
                data = _perm_audio_queue.get(timeout=0.05)
            except queue.Empty:
                if last_partial and (time.monotonic() - partial_since) >= 0.35:
                    dec = _parse_voice_perm_decision(last_partial)
                    if dec:
                        _resolve_pending_perm_by_voice(dec)
                        break
                continue

            if _mic_stream_state.get("speaking"):
                continue

            if recognizer.AcceptWaveform(data):
                res = json.loads(recognizer.Result() or "{}")
                dec = _parse_voice_perm_decision(res.get("text", ""))
                if dec:
                    _resolve_pending_perm_by_voice(dec)
                    break
                last_partial = ""
            else:
                part = json.loads(recognizer.PartialResult() or "{}")
                p_text = part.get("partial", "").strip()
                if p_text != last_partial:
                    last_partial = p_text
                    partial_since = time.monotonic()
                elif p_text and (time.monotonic() - partial_since) >= 0.35:
                    dec = _parse_voice_perm_decision(p_text)
                    if dec:
                        _resolve_pending_perm_by_voice(dec)
                        break
    finally:
        if temp_stream is not None:
            try:
                temp_stream.stop()
                temp_stream.close()
            except Exception:
                pass


_user_revoked_categories = set()
_ipc_loop_running = False


def _ipc_perm_callback(category: str, action_desc: str, details: dict) -> str:
    """Invoked whenever a visual, file, or desktop control tool requests permission.
    Requires explicit user approval per category/session (or per-action for destructive/continuous categories)."""
    always_prompt = category in ("file_delete", "form_purchase", "continuous_screen")
    if not always_prompt and permissions.is_granted(category) and category not in _user_revoked_categories:
        return "session"

    if os.getenv("NOVA_AUTO_PERMISSION", "").lower() in ("1", "true", "yes"):
        if not always_prompt:
            permissions.grant_session(category)
            emit("perm_sync", active=permissions.get_status())
            emit("perm_notification", category=category, desc=action_desc)
            return "session"
        return "deny"

    # When imported by automated non-UI unit tests (no stdin command reader running),
    # allow non-destructive test calls unless explicitly revoked; in live UI/voice sessions, always prompt.
    if not _ipc_loop_running and not always_prompt and category not in _user_revoked_categories:
        is_tty = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False
        if not is_tty:
            permissions.grant_session(category)
            emit("perm_sync", active=permissions.get_status())
            emit("perm_notification", category=category, desc=action_desc)
            return "session"

    req_id = uuid.uuid4().hex[:8]
    ev = threading.Event()
    with _pending_perms_lock:
        _pending_perms[req_id] = {"event": ev, "decision": "deny", "category": category, "voice": False}

    emit("state", v="waiting_approval")
    emit("perm_request", id=req_id, category=category, desc=action_desc, details=details or {})
    _wait_for_perm_with_voice(req_id, category, ev, timeout=90.0)

    with _pending_perms_lock:
        entry = _pending_perms.pop(req_id, {})
    decision = str(entry.get("decision", "deny")).lower().strip()
    if decision in ("session", "allow_session"):
        _user_revoked_categories.discard(category)
        permissions.grant_session(category)
        emit("perm_sync", active=permissions.get_status())
    if entry.get("voice"):
        emit("perm_notification", category=category, desc=f"{action_desc} ({decision} by voice)")
    return decision


permissions.set_prompt_callback(_ipc_perm_callback)


def manage_permission(action: str, category: str = "screen_view", scope: str = "session"):
    """
    Manage Nova permissions via voice command or chat.
    Actions: 'grant', 'allow', 'revoke', 'status'.
    Categories: 'screen_view', 'camera', 'mouse_keyboard', 'document_access', 'media_access', 'file_modify', 'file_delete', 'all'.
    """
    act = action.lower().strip()
    cat = category.lower().strip()
    if act in ("grant", "allow", "yes", "enable"):
        granted = permissions.grant_by_voice(cat, scope=scope)
        if not granted:
            return {
                "ok": False,
                "verified": False,
                "message": f"Unknown permission category '{category}'. Valid categories: {', '.join(CATEGORIES.keys())}, 'camera', 'mouse', or 'all'."
            }
        for c in granted:
            _user_revoked_categories.discard(c)
        cats_str = ", ".join(CATEGORIES.get(c, {}).get("title", c) for c in granted)
        emit("perm_sync", active=permissions.get_status())
        emit("log", text=f"[Voice Permission] Granted '{cats_str}' for session.")
        return {
            "ok": True,
            "verified": True,
            "message": f"Permission for {cats_str} has been granted for this session.",
            "granted": granted
        }
    elif act in ("revoke", "deny", "disable"):
        if cat in ("all", "everything"):
            _user_revoked_categories.update(CATEGORIES.keys())
            permissions.revoke_all()
            emit("perm_sync", active=permissions.get_status())
            emit("log", text="[Voice Permission] Revoked all session permissions.")
            return {"ok": True, "verified": True, "message": "All session permissions have been revoked."}
        else:
            targets = permissions._resolve_categories(cat)
            if not targets:
                return {"ok": False, "verified": False, "message": f"Unknown permission category '{category}'."}
            for target in targets:
                _user_revoked_categories.add(target)
                permissions.revoke(target)
            cats_str = ", ".join(CATEGORIES.get(t, {}).get("title", t) for t in targets)
            emit("perm_sync", active=permissions.get_status())
            emit("log", text=f"[Voice Permission] Revoked '{cats_str}'.")
            return {"ok": True, "verified": True, "message": f"Permission for '{cats_str}' has been revoked."}
    elif act in ("status", "check", "list"):
        st = permissions.get_status()
        active = [CATEGORIES.get(k, {}).get("title", k) for k, v in st.items() if v]
        if active:
            return {"ok": True, "verified": True, "message": f"Currently active permissions: {', '.join(active)}."}
        return {"ok": True, "verified": True, "message": "No active permissions currently granted."}
    return {"ok": False, "verified": False, "message": f"Unknown permission action '{action}'."}


def _on_gesture_state_change(state: str):
    emit("gesture", status=state)
    emit("indicator", kind="gesture", active=(state in ("starting", "running")))


gesture_manager._on_state_change = _on_gesture_state_change


def hand_gesture_control(action: str = "start", show_preview: bool = True):
    """
    Control the Hand Gesture Mouse Controller via webcam.
    Actions: 'start' ('on', 'enable', 'turn_on'), 'stop' ('off', 'disable', 'turn_off'),
    'pause', 'resume', 'toggle', 'show_preview', 'hide_preview', 'status'.
    """
    act = (action or "start").lower().strip()
    if act in ("start", "on", "enable", "turn_on", "turn on", "open", "activate"):
        if not permissions.request_permission("mouse_keyboard", "Control mouse cursor via hand gestures"):
            return {"ok": False, "message": "Permission for mouse & keyboard control was denied."}
        permissions.grant_session("media_access")
        emit("perm_sync", active=permissions.get_status())
        return gesture_manager.start(show_preview=show_preview)
    elif act in ("stop", "off", "disable", "turn_off", "turn off", "close", "quit", "deactivate"):
        return gesture_manager.stop()
    elif act == "pause":
        return gesture_manager.pause()
    elif act in ("resume", "unpause", "continue"):
        return gesture_manager.resume()
    elif act == "toggle":
        if gesture_manager.is_running:
            return gesture_manager.stop()
        if not permissions.request_permission("mouse_keyboard", "Control mouse cursor via hand gestures"):
            return {"ok": False, "message": "Permission for mouse & keyboard control was denied."}
        permissions.grant_session("media_access")
        emit("perm_sync", active=permissions.get_status())
        return gesture_manager.start(show_preview=show_preview)
    elif act in ("hide_preview", "hide"):
        return gesture_manager.set_preview(False)
    elif act in ("show_preview", "show"):
        return gesture_manager.set_preview(True)
    elif act in ("status", "check"):
        return gesture_manager.get_status()
    return {"ok": False, "message": f"Unknown hand_gesture_control action '{action}'."}


def nova_control(action: str):
    """Control Nova herself: minimize, close/exit Nova, or toggle/show/hide the floating companion bot or hand gesture control."""
    act = action.lower().strip()
    if "gesture" in act or "hand" in act:
        if "off" in act or "stop" in act or "disable" in act:
            return hand_gesture_control("stop")
        elif "pause" in act:
            return hand_gesture_control("pause")
        elif "resume" in act:
            return hand_gesture_control("resume")
        return hand_gesture_control("start")
    if act in ("close", "exit", "quit", "shutdown"):
        emit("ui_cmd", action="close")
        return {"ok": True, "verified": True, "message": "Closing Nova."}
    elif act in ("minimize", "hide"):
        emit("ui_cmd", action="minimize")
        return {"ok": True, "verified": True, "message": "Minimized Nova to tray."}
    elif "floating" in act or "bot" in act or "companion" in act or act == "toggle_bot":
        if "on" in act or "show" in act or "enable" in act or "open" in act:
            emit("ui_cmd", action="show_floating_bot")
            return {"ok": True, "verified": True, "message": "Turned on floating companion bot."}
        elif "off" in act or "hide" in act or "disable" in act:
            emit("ui_cmd", action="hide_floating_bot")
            return {"ok": True, "verified": True, "message": "Turned off floating companion bot."}
        else:
            emit("ui_cmd", action="toggle_floating_bot")
            return {"ok": True, "verified": True, "message": "Toggled floating companion bot."}
    return {"ok": False, "verified": False, "message": f"Unknown action: {action}"}


def point_at_screen(question: str = "What is this region on my screen and how do I use it?") -> dict:
    """Trigger the user-driven screen-region pointing selector in the UI after checking screen_view permission."""
    q = (question or "What is this region on my screen and how do I use it?").strip()
    if not permissions.request_permission("screen_view", f"Select and analyze a screen region: '{q}'"):
        return {
            "ok": False,
            "verified": False,
            "permission_denied": True,
            "message": "Screen access permission was not approved.",
        }
    emit("point_at_screen", question=q)
    return {
        "ok": True,
        "verified": True,
        "message": "Opened the screen region selector. Drag a rectangle around the area you want me to analyze, or press Escape to cancel.",
    }


def _generate_walkthrough_steps_via_ai(goal: str) -> list[dict]:
    """Ask the configured Gemini model for an ordered sequence of guidance-only walkthrough steps."""
    from screen_agent import get_vision_models, get_active_window_title
    active_win = get_active_window_title()
    sys_inst = (
        "You are Nova's step-by-step guided walkthrough planner for Windows 11. "
        "Generate a concise sequence of 3 to 6 ordered walkthrough steps that teach the user how to perform the requested task themselves. "
        "Walkthroughs are guidance-only: the user performs all clicks and typing. "
        "Return ONLY valid JSON as an object: "
        '{"title": str, "steps": [{"title": str, "instruction": str, "target_label": str | null, "caution": str | null}]}. '
        "If a specific visible button or menu item should be highlighted via UI Automation, put its exact label in 'target_label'; otherwise null."
    )
    prompt = f"User goal: {goal}\nCurrently active window: {active_win}"
    for m_name in get_vision_models():
        try:
            resp = client.models.generate_content(
                model=m_name,
                contents=[types.Part.from_text(text=prompt)],
                config=types.GenerateContentConfig(
                    system_instruction=sys_inst,
                    temperature=0.2,
                    max_output_tokens=750,
                ),
            )
            raw = (resp.text or "").strip()
            if not raw:
                continue
            import re
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
            m = re.search(r"\{[\s\S]*\}", cleaned)
            if m:
                parsed = json.loads(m.group(0))
                steps = parsed.get("steps") if isinstance(parsed, dict) else None
                if isinstance(steps, list) and steps:
                    return steps
        except Exception:
            continue
    return [
        {
            "title": "Step 1: Open the target application or menu",
            "instruction": f"Locate the main window or menu needed for: {goal}.",
            "target_label": None,
            "caution": None,
        },
        {
            "title": "Step 2: Locate the relevant setting or control",
            "instruction": "Navigate to the specific section or toolbar button and review the options shown on screen.",
            "target_label": None,
            "caution": "Review any changes carefully before confirming.",
        },
        {
            "title": "Step 3: Complete the action",
            "instruction": "Perform the click or input yourself, then click Next or Stop when finished.",
            "target_label": None,
            "caution": None,
        },
    ]


def _serialize_walkthrough(wt: Walkthrough | None) -> dict:
    if wt is None or not wt.active or wt.current is None:
        return {"active": False, "paused": False, "title": wt.title if wt else "", "step": None, "index": 0, "total": 0}
    cur = wt.current
    idx, total = wt.progress
    target_rect = None
    if cur.target_label:
        try:
            from desktop_agent import find_element_rect_via_uia
            target_rect = find_element_rect_via_uia(cur.target_label)
        except Exception:
            target_rect = None
    return {
        "active": wt.active,
        "paused": getattr(wt, "paused", False),
        "title": wt.title,
        "index": idx,
        "total": total,
        "step": {
            "title": cur.title,
            "instruction": cur.instruction,
            "target_label": cur.target_label,
            "target_rect": list(target_rect) if target_rect else None,
            "caution": cur.caution,
        },
    }


def walkthrough_control(action: str = "start", goal: str = "", steps: list = None) -> dict:
    """Control guided walkthrough mode (guidance-only; never clicks or types).
    Actions: 'start', 'next', 'back', 'pause', 'resume', 'stop', 'status'."""
    global _active_walkthrough
    act = (action or "start").strip().lower()
    if act in ("start", "begin", "create", "show"):
        clean_goal = (goal or "Guided walkthrough").strip()
        raw_steps = steps if (isinstance(steps, list) and steps) else _generate_walkthrough_steps_via_ai(clean_goal)
        _active_walkthrough = walkthrough_from_plan(clean_goal, raw_steps)
        state = _serialize_walkthrough(_active_walkthrough)
        emit("walkthrough_state", **state)
        cur = _active_walkthrough.current
        return {
            "ok": True,
            "verified": True,
            "walkthrough": state,
            "message": f"Started walkthrough '{clean_goal}' (Step 1/{len(_active_walkthrough.steps)}: {cur.title} — {cur.instruction}).",
        }
    if _active_walkthrough is None:
        return {"ok": False, "verified": False, "message": "No walkthrough is currently active."}
    if act in ("next", "forward"):
        step = _active_walkthrough.next()
        state = _serialize_walkthrough(_active_walkthrough)
        emit("walkthrough_state", **state)
        if step is None:
            return {"ok": True, "verified": True, "walkthrough": state, "message": "Walkthrough completed."}
        idx, total = _active_walkthrough.progress
        return {"ok": True, "verified": True, "walkthrough": state, "message": f"Step {idx}/{total}: {step.title} — {step.instruction}"}
    if act in ("back", "prev", "previous"):
        step = _active_walkthrough.back()
        state = _serialize_walkthrough(_active_walkthrough)
        emit("walkthrough_state", **state)
        idx, total = _active_walkthrough.progress
        return {"ok": True, "verified": True, "walkthrough": state, "message": f"Step {idx}/{total}: {step.title} — {step.instruction}"}
    if act == "pause":
        _active_walkthrough.pause()
        state = _serialize_walkthrough(_active_walkthrough)
        emit("walkthrough_state", **state)
        return {"ok": True, "verified": True, "walkthrough": state, "message": "Walkthrough paused."}
    if act in ("resume", "unpause"):
        _active_walkthrough.resume()
        state = _serialize_walkthrough(_active_walkthrough)
        emit("walkthrough_state", **state)
        return {"ok": True, "verified": True, "walkthrough": state, "message": "Walkthrough resumed."}
    if act in ("stop", "end", "cancel", "close"):
        _active_walkthrough.stop()
        state = _serialize_walkthrough(_active_walkthrough)
        emit("walkthrough_state", **state)
        return {"ok": True, "verified": True, "walkthrough": state, "message": "Walkthrough stopped."}
    if act in ("status", "get"):
        state = _serialize_walkthrough(_active_walkthrough)
        return {"ok": True, "verified": True, "walkthrough": state, "message": f"Walkthrough active: {state.get('active')}"}
    return {"ok": False, "verified": False, "message": f"Unknown walkthrough action '{action}'."}


def _emit_workflows_sync():
    try:
        items = []
        for wf in workflow_store.list_workflows():
            items.append({
                "workflow_id": wf.workflow_id,
                "name": wf.name,
                "created_at": wf.created_at,
                "steps": [
                    {
                        "action": s.action,
                        "arguments": s.arguments,
                        "description": s.description,
                        "created_at": s.created_at,
                    }
                    for s in wf.steps
                ],
            })
        emit(
            "workflow_state",
            recording=workflow_recorder.is_recording,
            recorded_steps=len(workflow_recorder.steps),
            workflows=items,
        )
    except Exception as e:
        emit("log", text=f"[Workflow] Sync error: {e}")


def replay_saved_workflow(workflow_id: str) -> dict:
    """Replay a saved semantic workflow through ApprovedActionBroker & WorkflowPlayer with per-step user approval and verification."""
    global _active_workflow_player
    target_wf = None
    for wf in workflow_store.list_workflows():
        if wf.workflow_id == workflow_id or wf.name.lower() == str(workflow_id).lower():
            target_wf = wf
            break
    if target_wf is None:
        return {"ok": False, "verified": False, "message": f"Workflow '{workflow_id}' was not found."}
    if not target_wf.steps:
        return {"ok": False, "verified": False, "message": f"Workflow '{target_wf.name}' has no steps to replay."}

    total = len(target_wf.steps)
    broker = ApprovedActionBroker(
        approval=lambda _act: True,
        permission_checker=lambda cat, desc: permissions.request_permission(cat, desc),
        verifier=lambda name, _args, res: bool(isinstance(res, dict) and res.get("ok") and res.get("verified", True)),
    )
    for act_name in ALLOWLISTED_WORKFLOW_ACTIONS:
        fn = TOOLS.get(act_name)
        if fn:
            broker.register(act_name, fn)

    def _confirm(idx: int, step: WorkflowStep) -> bool:
        return _request_step_approval_from_ui(idx, total, step.action, step.arguments, step.description)

    def _exec(action_name: str, arguments: dict):
        emit("state", v="executing")
        if action_name in ("look_at_screen", "inspect_video", "take_screenshot"):
            emit("indicator", kind="screen", active=True)
        else:
            emit("indicator", kind="control", active=True)
        try:
            broker_res = broker.execute({"action": action_name, "arguments": arguments, "description": f"Workflow step: {action_name}"})
            if not broker_res.get("ok"):
                return _normalize_tool_result(action_name, {"ok": False, "verified": False, "message": broker_res.get("error") or broker_res.get("message") or "Step failed"})
            raw = broker_res.get("result")
            return _normalize_tool_result(action_name, raw)
        finally:
            emit("indicator", kind="screen", active=False)
            emit("indicator", kind="control", active=False)

    def _verify(idx: int, step: WorkflowStep, res) -> bool:
        if not isinstance(res, dict):
            return False
        if not res.get("ok"):
            return False
        if res.get("verified") is True:
            return True
        # When ok=True but verified=False (unverified attempt), ask the user whether the step succeeded
        return _request_step_approval_from_ui(
            idx,
            total,
            f"{step.action} [Unverified Outcome]",
            step.arguments,
            f"Nova executed '{step.action}' ({res.get('message', '')}), but could not independently confirm the visual result. Did this step succeed? Click Approve to continue or Cancel to stop replay.",
        )

    player = WorkflowPlayer(action_executor=_exec, confirm_step=_confirm, verify_step=_verify)
    _active_workflow_player = player
    emit("workflow_replay_status", status="started", workflow_id=target_wf.workflow_id, name=target_wf.name, total=total)
    try:
        outcome = player.play(target_wf)
    finally:
        _active_workflow_player = None
        emit("state", v="idle")
    emit("workflow_replay_status", status="finished", workflow_id=target_wf.workflow_id, name=target_wf.name, outcome=outcome)
    return outcome


def workflow_control(action: str = "list", name: str = "", workflow_id: str = "") -> dict:
    """Record, stop/save, list, or delete semantic reusable workflows.
    Actions: 'start_recording', 'stop_recording', 'cancel_recording', 'list', 'delete', 'cancel_replay'."""
    global _active_workflow_player
    act = (action or "list").strip().lower().replace(" ", "_")
    if act in ("start", "start_recording", "record", "begin"):
        if workflow_recorder.is_recording:
            return {"ok": True, "verified": True, "message": "Workflow recording is already active."}
        workflow_recorder.start()
        _emit_workflows_sync()
        return {
            "ok": True,
            "verified": True,
            "message": "Started recording semantic workflow. Approved tool actions will be recorded until you stop recording.",
        }
    if act in ("stop", "stop_recording", "save", "finish"):
        if not workflow_recorder.is_recording:
            return {"ok": False, "verified": False, "message": "No workflow is currently being recorded."}
        wf = workflow_recorder.stop(name or f"Workflow {time.strftime('%Y-%m-%d %H:%M')}")
        saved_path = workflow_store.save(wf)
        _emit_workflows_sync()
        return {
            "ok": True,
            "verified": True,
            "workflow_id": wf.workflow_id,
            "path": str(saved_path),
            "steps": len(wf.steps),
            "message": f"Saved workflow '{wf.name}' with {len(wf.steps)} step(s).",
        }
    if act in ("cancel_recording", "abort_recording"):
        workflow_recorder.cancel()
        _emit_workflows_sync()
        return {"ok": True, "verified": True, "message": "Cancelled workflow recording."}
    if act in ("cancel_replay", "stop_replay"):
        if _active_workflow_player is not None:
            _active_workflow_player.cancel()
            return {"ok": True, "verified": True, "message": "Cancelled active workflow replay."}
        return {"ok": False, "verified": False, "message": "No workflow replay is currently running."}
    if act in ("delete", "remove"):
        if not workflow_id:
            return {"ok": False, "verified": False, "message": "Please provide a workflow_id to delete."}
        deleted = workflow_store.delete(workflow_id)
        _emit_workflows_sync()
        if deleted:
            return {"ok": True, "verified": True, "message": f"Deleted workflow '{workflow_id}'."}
        return {"ok": False, "verified": False, "message": f"Workflow '{workflow_id}' was not found."}
    if act in ("list", "status", "get"):
        wfs = workflow_store.list_workflows()
        _emit_workflows_sync()
        summary = ", ".join(f"{w.name} ({len(w.steps)} steps)" for w in wfs[:10]) if wfs else "No saved workflows."
        return {
            "ok": True,
            "verified": True,
            "recording": workflow_recorder.is_recording,
            "count": len(wfs),
            "message": f"Recording: {workflow_recorder.is_recording}. Saved workflows ({len(wfs)}): {summary}",
        }
    return {"ok": False, "verified": False, "message": f"Unknown workflow_control action '{action}'."}


SYSTEM = """
You are Nova (Glass Nova), a friendly, fast, and reliable Windows 11 desktop voice AI companion and assistant.
The user talks to you by voice and also by typing, in one continuous conversation.
Speak naturally and conversationally. Maintain context across turns, whether a message was spoken or typed.
The user should not need to say 'Nova' or 'Hey Nova' before every follow-up question.
After answering, remain ready for the next user turn.
Use tools when the user asks you to control the computer, view the screen, analyze documents, write in Notepad, play videos, or operate apps.
Action Verification & Honesty Rules:
- Every tool returns {"ok": bool, "verified": bool, "message": str}.
- Confirmed success (ok=true AND verified=true): The intended action was performed and independently verified. Report success concisely.
- Unverified attempt (ok=true AND verified=false): The action was attempted (e.g., shortcut sent or launch requested), but the outcome could not be independently confirmed. NEVER describe an unverified action as completed! Communicate uncertainty naturally and briefly (for example: "I sent the shortcut to..., though I couldn't confirm whether the page reacted" or "I launched..., though I couldn't confirm its window appeared yet").
- Failed action (ok=false, verified=false): The action failed or the expected outcome did not happen. Report clearly and briefly what failed based on the tool message.
Do not launch command shells or execute arbitrary shell commands.
Do not perform destructive or irreversible actions without explicit confirmation.
For arithmetic, use calculator_operation.
Keep spoken replies concise unless the user asks for detail.
Language & Speech-to-Text Robustness:
- DEFAULT LANGUAGE IS ENGLISH. Always speak in English unless the user explicitly asks you to switch to another language (such as "Switch to Telugu", "Speak in Tamil", "Switch to Hindi") or speaks a clear, full sentence in Telugu, Tamil, or Hindi.
- NEVER switch to Spanish, German, French, Bengali, or any other language just because a short voice transcription looks like a foreign word (such as "¿Cómo estás?", "conectar", "gletscher", ".el idioma", "করেছি") — speech recognition frequently mishears short background noises or accented English commands as random foreign words! Stay in English and interpret the sound as English context or ignore isolated noise.
- If an utterance contains a computer/document command mixed with unrelated background speech (e.g. a lecture or aptitude video playing in the room like "eight people sitting in a row..."), ignore the unrelated background audio and execute the user's computer/document command.
Hand Gesture Mouse Control:
- When the user asks to "turn on hand gesture control", "enable hand gesture", "start hand mouse", "control cursor with my hand", or "turn on hand control", call hand_gesture_control with action="start".
- When the user asks to "turn off hand gesture control", "stop hand gesture", "disable hand control", or "stop hand mouse", call hand_gesture_control with action="stop".
- When the user asks to "pause hand gesture" or "resume hand gesture", call hand_gesture_control with action="pause" or action="resume".
- When the user asks to "hide hand camera preview" or "show hand camera preview", call hand_gesture_control with action="hide_preview" or action="show_preview".
YouTube, Video Playback & Browser Tabs:
- When the user asks to play a video, song, or trailer on YouTube (e.g. "Play Endgame trailer in YouTube in Telugu video"), call play_youtube with query="Endgame trailer in Telugu" and action="play".
- When the user asks to "open the first link on YouTube" (including in Telugu/Tamil/Hindi like "Appanna first link open cheyy YouTube di"), call play_youtube with action="play" and index=1.
- When the user asks ONLY to search YouTube without playing (e.g. "Open YouTube and search for Endgame"), call play_youtube with query="Endgame" and action="search".
- When the user asks to "skip ad", "skip the ad", "skip ads", or "click skip ad" in YouTube, ALWAYS call skip_youtube_ad (or click_ui_element with label="Skip Ads"). NEVER call press_key("enter") or mouse_control("move") + press_key("enter") to skip an ad — pressing Enter does NOT click the Skip Ad button!
- When the user asks to move, seek, or jump the song/video to a timestamp (e.g. "move song to 1.45 seconds", "jump to 1:45", "seek to 2 minutes 30 seconds", "fast forward 20 seconds", "rewind 10 seconds"), ALWAYS call control_youtube_video with action="seek" (or "forward" / "rewind") and timestamp="1:45". NEVER say you cannot jump to a specific time in the video!
- When the user asks to close a browser tab (e.g. "close the YouTube tab", "close that YouTube tab", "close the new tab", "close this tab", "close tab"), ALWAYS call close_browser_tab(target="youtube" or "new tab" or "") — or window_control(action="close_tab", target=...). Never claim a tab was closed unless the tool returns ok=true!
Nova & Companion Control:
- When the user asks to "close yourself", "exit Nova", "quit", or "minimize", call nova_control with action="close" or action="minimize".
- When the user asks to "turn on floating bot", "enable floating bot", "show companion", or "toggle floating bot", call nova_control with action="show_floating_bot" or "toggle_floating_bot".
Voice Permission Commands & Explicit User Consent:
- Screen viewing permission ('screen_view') and computer-control permission ('mouse_keyboard', 'file_modify', 'file_delete', 'app_install', 'form_purchase') are separate and enforced by Nova's permission manager.
- When you call a tool that requires screen viewing or computer control, Nova's permission system will prompt the user (via the permission dialog or hands-free voice approval) if not yet granted for this session.
- If a tool returns permission_denied=true or denied=true, respect the user's denial immediately and do not retry without their consent.
- When the user explicitly grants or revokes permissions by voice (e.g. "allow camera", "give permission to mouse", "revoke all permissions"), call manage_permission.
Visual perception, PDF reading, Notepad writing, and desktop control abilities:
- NEVER ask the user to take, send, or upload a screenshot — you can always capture and see the screen automatically via look_at_screen or click_ui_element.
- When the user asks to look at their screen ("can you see my screen?", "look at my screen", "what is on my screen?"), call look_at_screen.
- When the user asks to click or double-click a button, icon, menu, YouTube control, or file on screen (e.g., "Click Skip Ads", "Pause the video", "Open File menu", "Click machine learning PDF on my screen", "Double click the PDF"), call click_ui_element with the exact visible label and clicks=1 (or clicks=2 for double-click). click_ui_element automatically sees the screen, locates the element, clicks it, and verifies the result. Never claim a click succeeded if click_ui_element reports ok=false or unverified!
- When the user asks to explain a PDF, list all modules/units/chapters, read the whole PDF/document, summarize a document, explain a diagram in a document, or generate revision questions, ALWAYS call inspect_document (leave path empty if referring to the currently open or recently referenced PDF, or pass the file name). inspect_document reads the ENTIRE PDF across all pages — NEVER claim you can only see Module 1 or only read it in sections!
- When the user asks to read a PDF/document and write or type the results (such as module names, notes, or a summary) on Notepad, call inspect_document with write_to_notepad=true (or call inspect_document and then write_in_notepad).
- When the user asks to write or type anything into Notepad, call write_in_notepad(text=...) which automatically focuses (or opens) the Windows Notepad window and writes the text directly into it.
- When the user asks to OPEN a PDF, document, or file in a viewer or application (e.g., "open machine learning PDF", "yes, open", "open it", "open it in WPS Office", "open in WS Office", "open the machine learning PDF in this WPS Office"), ALWAYS call open_file(path=<file name or 'it'>, app=<optional app name like 'WPS Office'>). NEVER say you cannot open files or PDFs in a viewer!
- When the user asks to explain a lecture or video, call inspect_video.
- When the user asks to click or tap at the current cursor position (e.g., "tap on the cursor position", "click here", "click at cursor", "left click", "double click here", "right click"), call click_mouse (leave x and y empty to click right at the current mouse cursor position) or mouse_control(action="click"). NEVER say you cannot determine the cursor position!
- When the user asks to type text into another app or press shortcuts (like ctrl+c, ctrl+v, ctrl+a, enter), call type_text_into_ui or press_keyboard_hotkey.
- When the user asks to open an app (e.g. "open PC", "open File Manager", "open File Explorer", "open WPS Office", "open Notepad", "open Chrome"), call open_app.
- When the user asks to close an app (e.g. "close File Manager", "close File Explorer", "close Notepad"), call app_control with action="close" and app=<name>.
Brightness, volume, and microphone sensitivity:
- If the user gives an exact percentage such as 10%, 55%, 68%, or 90%, call the relevant control_tool (brightness_control, volume_control, or mic_sensitivity_control) exactly once with action="set" and value equal to that percentage.
- For "what is my brightness/volume/mic sensitivity", call the relevant control exactly once with action="get".
- For "increase/decrease by X%", call the relevant control exactly once with action="increase" or "decrease" and amount=X.
- When the user asks to "turn on auto nearest mic", "enable auto nearest", "calibrate mic sensitivity", or "filter background micro sounds automatically", call mic_sensitivity_control with action="auto_nearest".
File, folder, clipboard, memory, and tasks:
- Open, create, list, rename, copy, move, and delete files or folders when asked. For deletion, always ask for confirmation before calling delete_file with confirm=true.
- Use clipboard_control, memory_control, and task_control when asked.
Screen Pointing, Guided Walkthroughs, and Reusable Workflows:
- When the user asks to "point at screen", "select a screen region", or "explain a region on my screen", call point_at_screen.
- When the user asks "show me how to...", "walk me through...", or asks for a step-by-step guided walkthrough without you clicking for them, call walkthrough_control(action="start", goal=...). For "next step", "back step", "pause walkthrough", or "stop walkthrough", call walkthrough_control with action="next", "back", "pause", or "stop".
- When the user asks to "start recording a workflow" or "stop recording workflow", call workflow_control with action="start_recording" or "stop_recording".
"""


def schema(props, required=None):
    return types.Schema(type="OBJECT", properties=props, required=required or [])


TOOL_DEFS = [types.Tool(function_declarations=[
    types.FunctionDeclaration(name="open_app", description="Open a Windows application by common name (e.g. Chrome, Notepad, WPS Office, File Explorer).", parameters=schema({"app":types.Schema(type="STRING")},["app"])),
    types.FunctionDeclaration(
        name="open_file",
        description="Open any file, PDF, document, image, or media file on Windows in its default viewer or in a specific application (such as WPS Office, Chrome, Edge, VLC, Notepad). Supports partial/spoken names like 'machine learning PDF' or 'it' for the last referenced file.",
        parameters=schema({
            "path":types.Schema(type="STRING"),
            "app":types.Schema(type="STRING")
        }, [])
    ),
    types.FunctionDeclaration(name="open_url", description="Open a website URL.", parameters=schema({"url":types.Schema(type="STRING")},["url"])),
    types.FunctionDeclaration(name="search_web", description="Search the web in the default browser.", parameters=schema({"query":types.Schema(type="STRING")},["query"])),
    types.FunctionDeclaration(
        name="play_youtube",
        description="Play a video on YouTube directly or search YouTube. Use action='play' to open and play the matching video (or index=1 to open the 1st link from the latest YouTube search), or action='search' to only open search results.",
        parameters=schema({
            "query":types.Schema(type="STRING"),
            "action":types.Schema(type="STRING"),
            "index":types.Schema(type="INTEGER")
        }, [])
    ),
    types.FunctionDeclaration(name="open_folder", description="Open a Windows folder.", parameters=schema({"path":types.Schema(type="STRING")},["path"])),
    types.FunctionDeclaration(name="system_status", description="Get CPU, memory and battery status.", parameters=schema({},[])),
    types.FunctionDeclaration(
        name="volume_control",
        description="Control Windows master volume. Use action='set' with value for an exact percentage (0-100). Use action='increase' or 'decrease' with amount for a relative percentage change. Use action='get' to read current percentage, or action='mute' to toggle mute.",
        parameters=schema({
            "action":types.Schema(type="STRING"),
            "value":types.Schema(type="INTEGER"),
            "amount":types.Schema(type="INTEGER"),
            "steps":types.Schema(type="INTEGER")
        },["action"])
    ),
    types.FunctionDeclaration(
        name="brightness_control",
        description="Control Windows display brightness. Use action='set' with value for an exact percentage (0-100). Use action='increase' or 'decrease' with amount for a relative percentage change. Use action='get' to read current percentage.",
        parameters=schema({
            "action":types.Schema(type="STRING"),
            "value":types.Schema(type="INTEGER"),
            "amount":types.Schema(type="INTEGER"),
            "steps":types.Schema(type="INTEGER")
        },["action"])
    ),
    types.FunctionDeclaration(
        name="window_control",
        description="Control the active Windows window or browser tab (minimize, maximize, restore, close, switch, close_tab, new_tab, reopen_tab, next_tab, prev_tab).",
        parameters=schema({"action":types.Schema(type="STRING"), "target":types.Schema(type="STRING")},["action"])
    ),
    types.FunctionDeclaration(name="app_control", description="Open, close, or restart common Windows applications or browser tabs.", parameters=schema({"action":types.Schema(type="STRING"),"app":types.Schema(type="STRING")},["action","app"])),
    types.FunctionDeclaration(name="take_screenshot", description="Take a screenshot of the desktop.", parameters=schema({},[])),
    types.FunctionDeclaration(name="type_text", description="Type text into the focused application.", parameters=schema({"text":types.Schema(type="STRING")},["text"])),
    types.FunctionDeclaration(
        name="write_in_notepad",
        description="Write or paste text (such as module names, summaries, notes, or code) directly into the Windows Notepad window on screen. Automatically focuses Notepad or opens it if not already open.",
        parameters=schema({
            "text":types.Schema(type="STRING"),
            "open_if_closed":types.Schema(type="BOOLEAN")
        },["text"])
    ),
    types.FunctionDeclaration(name="press_key", description="Press one keyboard key.", parameters=schema({"key":types.Schema(type="STRING")},["key"])),
    types.FunctionDeclaration(name="hotkey", description="Press a keyboard shortcut such as ctrl+c.", parameters=schema({"keys":types.Schema(type="STRING")},["keys"])),
    types.FunctionDeclaration(
        name="click_mouse",
        description="Click or double-click the mouse at the current cursor position (leave x and y omitted/empty) or at specific screen coordinates (x, y). Supports button='left'|'right'|'middle' and clicks=1 or 2.",
        parameters=schema({
            "x":types.Schema(type="INTEGER"),
            "y":types.Schema(type="INTEGER"),
            "button":types.Schema(type="STRING"),
            "clicks":types.Schema(type="INTEGER")
        },[])
    ),
    types.FunctionDeclaration(
        name="mouse_control",
        description="Move and control the mouse cursor. Actions: 'click' / 'tap' (click at current cursor position), 'double_click', 'right_click', 'position', 'move', 'move_relative', 'center', 'scroll_up', 'scroll_down', 'drag'.",
        parameters=schema({"action":types.Schema(type="STRING"),"x":types.Schema(type="INTEGER"),"y":types.Schema(type="INTEGER"),"dx":types.Schema(type="INTEGER"),"dy":types.Schema(type="INTEGER"),"button":types.Schema(type="STRING"),"clicks":types.Schema(type="INTEGER"),"amount":types.Schema(type="INTEGER")},["action"])
    ),
    types.FunctionDeclaration(name="clipboard_control", description="Read, set, append, clear, copy, paste, or open Windows clipboard history.", parameters=schema({"action":types.Schema(type="STRING"),"text":types.Schema(type="STRING")},["action"])),
    types.FunctionDeclaration(name="create_folder", description="Create a Windows folder at the requested path. Common shortcuts include Downloads, Documents, Desktop, Pictures, Music, Videos, Home, and Workspace.", parameters=schema({"path":types.Schema(type="STRING")},["path"])),
    types.FunctionDeclaration(name="list_folder", description="List files and folders inside a Windows folder.", parameters=schema({"path":types.Schema(type="STRING")},[])),
    types.FunctionDeclaration(name="rename_file", description="Rename a file or folder without overwriting an existing item.", parameters=schema({"path":types.Schema(type="STRING"),"new_name":types.Schema(type="STRING")},["path","new_name"])),
    types.FunctionDeclaration(name="copy_file", description="Copy a file or folder to another path without overwriting an existing item.", parameters=schema({"source":types.Schema(type="STRING"),"destination":types.Schema(type="STRING")},["source","destination"])),
    types.FunctionDeclaration(name="move_file", description="Move a file or folder to another path without overwriting an existing item.", parameters=schema({"source":types.Schema(type="STRING"),"destination":types.Schema(type="STRING")},["source","destination"])),
    types.FunctionDeclaration(name="delete_file", description="Delete a file or folder. This is destructive; ask the user for confirmation first, then call with confirm=true.", parameters=schema({"path":types.Schema(type="STRING"),"confirm":types.Schema(type="BOOLEAN")},["path"])),
    types.FunctionDeclaration(name="calculator_operation", description="Calculate a basic arithmetic expression safely.", parameters=schema({"expression":types.Schema(type="STRING")},["expression"])),
    types.FunctionDeclaration(name="write_text_file", description="Write a text file inside the PC workspace folder.", parameters=schema({"filename":types.Schema(type="STRING"),"text":types.Schema(type="STRING")},["filename","text"])),
    types.FunctionDeclaration(name="read_text_file", description="Read a text file from the PC workspace folder.", parameters=schema({"filename":types.Schema(type="STRING")},["filename"])),
    types.FunctionDeclaration(
        name="memory_control",
        description="Save, recall, list, forget, or clear non-sensitive user memories stored locally. Never store credentials or secrets; clearing all memories requires confirmation.",
        parameters=schema({
            "action":types.Schema(type="STRING"),
            "key":types.Schema(type="STRING"),
            "value":types.Schema(type="STRING"),
            "confirm":types.Schema(type="BOOLEAN")
        },["action"])
    ),
    types.FunctionDeclaration(
        name="task_control",
        description="Track goals, tasks, and reminders across sessions. Actions: now, create, list, update, complete, delete. Deleting a task requires explicit user confirmation.",
        parameters=schema({
            "action":types.Schema(type="STRING"),
            "title":types.Schema(type="STRING"),
            "details":types.Schema(type="STRING"),
            "task_id":types.Schema(type="STRING"),
            "status":types.Schema(type="STRING"),
            "due_at":types.Schema(type="STRING"),
            "confirm":types.Schema(type="BOOLEAN")
        },["action"])
    ),
    # ---- Visual & Desktop Agent Tools ----
    types.FunctionDeclaration(
        name="look_at_screen",
        description="Capture and visually analyze the current Windows screen or active application window to explain what is showing, identify error dialogs, or locate UI elements.",
        parameters=schema({"query":types.Schema(type="STRING")}, [])
    ),
    types.FunctionDeclaration(
        name="inspect_document",
        description="Read and analyze ALL pages of a PDF or document from start to finish (both native PDF pages and extracted text). Use this to list all modules/units/chapters, summarize the whole PDF, explain diagrams/formulas, or generate revision questions. Set write_to_notepad=true if the user also wants the result written into Notepad.",
        parameters=schema({
            "path":types.Schema(type="STRING"),
            "query":types.Schema(type="STRING"),
            "write_to_notepad":types.Schema(type="BOOLEAN")
        }, [])
    ),
    types.FunctionDeclaration(
        name="inspect_video",
        description="Analyze playing video frames, lecture slides, whiteboard content, or diagrams on screen.",
        parameters=schema({"query":types.Schema(type="STRING")}, [])
    ),
    types.FunctionDeclaration(
        name="click_ui_element",
        description="Locate and physically left-click (clicks=1) or double-click (clicks=2) an interface button, Skip Ads button, menu item, desktop icon, folder, or file on screen by visible label using UIAutomation and visual recognition.",
        parameters=schema({"label":types.Schema(type="STRING"), "button":types.Schema(type="STRING"), "clicks":types.Schema(type="INTEGER")}, ["label"])
    ),
    types.FunctionDeclaration(
        name="skip_youtube_ad",
        description="Skip the currently playing YouTube ad by locating and physically tapping the 'Skip' / 'Skip Ad' / 'Skip Ads' button on the video player and verifying the ad was skipped.",
        parameters=schema({}, [])
    ),
    types.FunctionDeclaration(
        name="close_browser_tab",
        description="Focus the open browser window, locate the requested tab (such as 'YouTube', 'new tab', or the active tab), close it, and verify that the tab actually closed.",
        parameters=schema({"target":types.Schema(type="STRING")}, [])
    ),
    types.FunctionDeclaration(
        name="control_youtube_video",
        description="Control an open YouTube video: seek/move to a specific timestamp (e.g. '1:45' or '1.45' or '2 minutes 30 seconds'), fast-forward, rewind, play/pause, mute, fullscreen, or restart.",
        parameters=schema({
            "action":types.Schema(type="STRING"),
            "timestamp":types.Schema(type="STRING"),
            "seconds":types.Schema(type="INTEGER")
        }, ["action"])
    ),
    types.FunctionDeclaration(
        name="type_text_into_ui",
        description="Type text into the focused Windows application or input field.",
        parameters=schema({"text":types.Schema(type="STRING"), "press_enter":types.Schema(type="BOOLEAN")}, ["text"])
    ),
    types.FunctionDeclaration(
        name="press_keyboard_hotkey",
        description="Press a keyboard shortcut such as ctrl+c, ctrl+v, alt+tab, or win+r.",
        parameters=schema({"keys":types.Schema(type="STRING")}, ["keys"])
    ),
    types.FunctionDeclaration(
        name="scroll_desktop",
        description="Scroll the active document, web page, or application window up or down.",
        parameters=schema({"direction":types.Schema(type="STRING"), "amount":types.Schema(type="INTEGER")}, ["direction"])
    ),
    types.FunctionDeclaration(
        name="nova_control",
        description="Control Nova herself: close/exit Nova, minimize Nova to tray, or turn on, show, hide, or toggle the floating companion bot.",
        parameters=schema({"action":types.Schema(type="STRING")}, ["action"])
    ),
    types.FunctionDeclaration(
        name="hand_gesture_control",
        description="Start, stop, pause, resume, or check the webcam Hand Gesture Mouse Controller so the user can move the cursor, click, drag, scroll, and zoom using hand movements. Actions: 'start', 'stop', 'pause', 'resume', 'toggle', 'show_preview', 'hide_preview', 'status'.",
        parameters=schema({
            "action":types.Schema(type="STRING"),
            "show_preview":types.Schema(type="BOOLEAN")
        }, ["action"])
    ),
    types.FunctionDeclaration(
        name="manage_permission",
        description="Grant, revoke, or check permissions via natural voice command. Categories: 'screen_view' (screen viewing), 'mouse_keyboard' (mouse & keyboard control), 'document_access', 'media_access', or 'all'. Actions: 'grant', 'revoke', 'status'.",
        parameters=schema({"action":types.Schema(type="STRING"), "category":types.Schema(type="STRING")}, ["action"])
    ),
    types.FunctionDeclaration(
        name="mic_sensitivity_control",
        description="Control Nova's Microphone Sensitivity percentage (0-100%) and AUTO NEAREST voice isolation mode. Actions: 'set' (with value=0..100), 'increase', 'decrease', 'auto_nearest' (auto-calibrate threshold to nearest voice above ambient noise), 'manual', 'get'.",
        parameters=schema({
            "action":types.Schema(type="STRING"),
            "value":types.Schema(type="INTEGER"),
            "amount":types.Schema(type="INTEGER")
        }, ["action"])
    ),
    types.FunctionDeclaration(
        name="point_at_screen",
        description="Open a user-driven translucent full-screen region selector so the user can drag a rectangle on screen and have Nova analyze only that selected region. Checks screen_view permission first and never clicks or types.",
        parameters=schema({
            "question":types.Schema(type="STRING")
        }, [])
    ),
    types.FunctionDeclaration(
        name="walkthrough_control",
        description="Start or navigate a step-by-step guided visual walkthrough (guidance-only; never performs clicks or typing). Actions: 'start' (with goal), 'next', 'back', 'pause', 'resume', 'stop', 'status'.",
        parameters=schema({
            "action":types.Schema(type="STRING"),
            "goal":types.Schema(type="STRING")
        }, ["action"])
    ),
    types.FunctionDeclaration(
        name="workflow_control",
        description="Record, stop/save, list, or delete semantic reusable workflows. Actions: 'start_recording', 'stop_recording' (with name), 'cancel_recording', 'list', 'delete' (with workflow_id), 'cancel_replay'.",
        parameters=schema({
            "action":types.Schema(type="STRING"),
            "name":types.Schema(type="STRING"),
            "workflow_id":types.Schema(type="STRING")
        }, ["action"])
    ),
])]

TOOLS = {
    "open_app":open_app,"open_file":open_file,"open_url":open_url,"search_web":search_web,"play_youtube":play_youtube,"open_folder":open_folder,
    "system_status":system_status,"volume_control":volume_control,"brightness_control":brightness_control,
    "window_control":window_control,"app_control":app_control,"take_screenshot":take_screenshot,
    "type_text":type_text,"write_in_notepad":write_in_notepad,"press_key":press_key,"hotkey":hotkey,"click_mouse":click_mouse,
    "mouse_control":mouse_control,"clipboard_control":clipboard_control,
    "create_folder":create_folder,"list_folder":list_folder,"rename_file":rename_file,
    "copy_file":copy_file,"move_file":move_file,"delete_file":delete_file,
    "calculator_operation":calculator_operation,"write_text_file":write_text_file,"read_text_file":read_text_file,
    "memory_control":memory_control,"task_control":task_control,
    "look_at_screen": analyze_screen,
    "inspect_document": analyze_document,
    "inspect_video": analyze_video,
    "click_ui_element": click_element,
    "skip_youtube_ad": skip_youtube_ad,
    "close_browser_tab": close_browser_tab,
    "control_youtube_video": control_youtube_video,
    "type_text_into_ui": type_text_into_ui,
    "press_keyboard_hotkey": press_keyboard_hotkey,
    "scroll_desktop": scroll_desktop,
    "nova_control": nova_control,
    "hand_gesture_control": hand_gesture_control,
    "manage_permission": manage_permission,
    "mic_sensitivity_control": mic_sensitivity_control,
    "point_at_screen": point_at_screen,
    "walkthrough_control": walkthrough_control,
    "workflow_control": workflow_control,
}


def _live_action_approval(action: dict) -> bool:
    """Per-action approval check for ApprovedActionBroker during live tool calls.
    High-impact or multi-step destructive actions require explicit confirmation."""
    name = str(action.get("action") or "")
    args = action.get("arguments") or {}
    if name == "delete_file":
        return _request_step_approval_from_ui(
            1, 1, name, args, f"Permanently delete file or folder: {args.get('path', '')}"
        )
    if name == "app_control" and str(args.get("action", "")).lower() in ("close", "restart"):
        # Check if mouse_keyboard permission is granted (handled by permission_checker)
        return True
    return True


live_action_broker = ApprovedActionBroker(
    approval=_live_action_approval,
    permission_checker=lambda cat, desc: permissions.request_permission(cat, desc),
    verifier=lambda name, _args, res: bool(isinstance(res, dict) and res.get("ok") and res.get("verified", False)),
)
for _t_name, _t_fn in TOOLS.items():
    live_action_broker.register(_t_name, _t_fn)


def _normalize_tool_result(tool_name: str, raw_result) -> dict:
    """Ensure every tool response follows the strict verification contract:
    {'ok': bool, 'verified': bool, 'message': str, ...}
    Never converts a missing 'verified' field into confirmed success."""
    if isinstance(raw_result, dict):
        out = dict(raw_result)
        ok = bool(out.get("ok", False))
        # Do NOT auto-convert missing verification into verified=True
        verified = bool(out["verified"]) if ("verified" in out and ok) else False
        msg = str(
            out.get("message")
            or out.get("result")
            or (
                "Action completed and verified."
                if (ok and verified)
                else ("Action attempted, but outcome could not be confirmed." if ok else "Action failed.")
            )
        )
        out["ok"] = ok
        out["verified"] = verified
        out["verification_status"] = "confirmed" if (ok and verified) else ("unverified" if ok else "failed")
        out["message"] = msg
        return out

    if raw_result is None:
        return {
            "ok": False,
            "verified": False,
            "verification_status": "failed",
            "message": f"Tool '{tool_name}' returned no result.",
        }

    text = str(raw_result).strip()
    is_err = text.lower().startswith(("error:", "failed:", "could not", "permission denied"))
    return {
        "ok": not is_err,
        "verified": False,
        "verification_status": "unverified" if not is_err else "failed",
        "message": text or f"Tool '{tool_name}' completed without verification data.",
    }


def _is_transient_ws_error(exc: Exception) -> bool:
    """Return True if exc is a normal/transient Gemini Live WebSocket disconnect or keepalive timeout."""
    name = type(exc).__name__
    msg = str(exc).lower()
    return (
        "connectionclosed" in name.lower()
        or "keepalive ping timeout" in msg
        or "1011" in msg
        or "1006" in msg
        or "1001" in msg
        or "going away" in msg
    )


def _build_live_config(resume_state: dict) -> types.LiveConnectConfig:
    """Build LiveConnectConfig with optional session resumption and sliding-window context compression."""
    kwargs = dict(
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=SYSTEM,
        tools=TOOL_DEFS,
    )
    if resume_state and resume_state.get("use_extras", True):
        try:
            kwargs["session_resumption"] = types.SessionResumptionConfig(
                handle=resume_state.get("handle")
            )
            kwargs["context_window_compression"] = types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow()
            )
        except Exception:
            pass
    return types.LiveConnectConfig(**kwargs)


class AudioPlayer:
    """Dedicated non-blocking speaker output stream on a background worker thread."""
    RATE = 24000

    def __init__(self, on_idle):
        self.on_idle = on_idle
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.stream = None
        self.thread = None
        self.failed = False
        self.epoch = 0
        self.pending_ends = 0
        self.need_restart = False
        self.closed = False

    def _open(self):
        if self.stream is not None:
            return True
        if self.failed or self.closed:
            return False
        if sd is None:
            self.failed = True
            emit("error", text=f"Spoken replies are unavailable (audio library problem: {_SD_ERROR}). Text replies still work.")
            return False
        try:
            s = sd.RawOutputStream(samplerate=self.RATE, blocksize=0, channels=1, dtype="int16")
            s.start()
        except Exception as e:
            self.failed = True
            emit("error", text=f"Spoken replies are unavailable (no usable speaker: {e}). Text replies still work.")
            return False
        self.stream = s
        self.thread = threading.Thread(target=self._run, name="nova-audio-out", daemon=True)
        self.thread.start()
        return True

    def write(self, data):
        with self.lock:
            if not self._open():
                return False
            _mic_stream_state["barge_in_triggered"] = False
            _mic_stream_state["barge_in_hits"] = 0
            _mic_stream_state["speaking"] = True
            _mic_stream_state["mute_until"] = time.monotonic() + 0.65
            self.q.put(("a", self.epoch, data))
        return True

    def end_turn(self):
        with self.lock:
            if self.stream is None:
                _mic_stream_state["speaking"] = False
                _mic_stream_state["playback_rms"] = 0.0
                _mic_stream_state["mute_until"] = time.monotonic() + 0.35
                return False
            self.pending_ends += 1
            self.q.put(("end", self.epoch, None))
        return True

    def interrupt(self):
        """Discard queued and buffered audio immediately (barge-in or mute)."""
        with self.lock:
            self.epoch += 1
            self.pending_ends = 0
            _mic_stream_state["speaking"] = False
            _mic_stream_state["playback_rms"] = 0.0
            _mic_stream_state["mute_until"] = time.monotonic() + 0.25
            try:
                while True:
                    self.q.get_nowait()
            except queue.Empty:
                pass
            s = self.stream
            if s is not None:
                self.need_restart = True
                try:
                    s.abort()
                except Exception:
                    pass

    def _restart_if_needed(self):
        with self.lock:
            if not self.need_restart:
                return
            self.need_restart = False
            try:
                self.stream.start()
            except Exception:
                pass

    def _run(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            kind, ep, data = item
            if ep != self.epoch:
                continue
            try:
                self._restart_if_needed()
                s = self.stream
                if kind == "a":
                    _mic_stream_state["speaking"] = True
                    _mic_stream_state["playback_rms"] = _pcm16_rms(data)
                    _mic_stream_state["mute_until"] = time.monotonic() + 0.65
                    s.write(data)
                    _mic_stream_state["mute_until"] = time.monotonic() + 0.55
                else:
                    s.stop()
                    s.start()
                    with self.lock:
                        if ep == self.epoch:
                            self.pending_ends = max(0, self.pending_ends - 1)
                            idle = self.pending_ends == 0
                        else:
                            idle = False
                    if idle:
                        _mic_stream_state["playback_rms"] = 0.0
                        _mic_stream_state["mute_until"] = time.monotonic() + 0.48
                        _mic_stream_state["speaking"] = False
                        self.on_idle()
            except Exception as e:
                if ep != self.epoch or self.closed:
                    continue
                emit("log", text=f"[audio] playback error: {type(e).__name__}: {e}")
                with self.lock:
                    self.need_restart = True

    def close(self):
        with self.lock:
            self.closed = True
            self.epoch += 1
            _mic_stream_state["speaking"] = False
            _mic_stream_state["mute_until"] = 0.0
            s, t = self.stream, self.thread
            try:
                while True:
                    self.q.get_nowait()
            except queue.Empty:
                pass
            self.q.put(None)
        if s is not None:
            try:
                s.abort()
            except Exception:
                pass
        if t is not None:
            t.join(timeout=2)
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
        with self.lock:
            self.stream = None


FATAL_MARKERS = ("API KEY", "API_KEY_INVALID", "PERMISSION_DENIED", "UNAUTHENTICATED")
MODEL_MARKERS = ("IS NOT FOUND", "NOT SUPPORTED FOR", "MODEL NOT FOUND")


class LiveManager:
    """Owns the single Gemini Live session, the mic, the speaker, and every command from the UI."""
    def __init__(self, api_client):
        self.client = api_client
        self.session = None
        self.ready = asyncio.Event()
        self.stop_evt = asyncio.Event()
        self.send_lock = asyncio.Lock()
        self.closing = False
        self.fatal = False
        self.resume_handle = None
        self.use_extras = True
        self.mic_task = None
        self.mic_on = False
        self.voice_output = True
        self.player = AudioPlayer(self._playback_idle)
        self.text_q = asyncio.Queue()
        self.tool_tasks = set()
        self.state = "idle"
        self._outage_reported = False
        self._wake_greet_until = 0.0
        self._last_model_text = ""
        self._last_model_ts = 0.0
        self._reset_turn()
        ducker.start()

    def _rest_state(self):
        return "listening" if self.mic_on else "idle"

    def set_state(self, s):
        if s == self.state:
            return
        self.state = s
        if s in ("speaking", "thinking"):
            ducker.reset_holds()
            ducker.hold()
        else:
            ducker.reset_holds()
            ducker.pulse(2.5)
        emit("state", v=s)
        _notify_cloudy_state(s)

    def _playback_idle(self):
        self._wake_greet_until = 0.0
        if self.state == "speaking":
            self.set_state(self._rest_state())

    def _reset_turn(self):
        self.turn_active = False
        self.turn_has_out_text = False
        self.fallback_text = ""
        self.turn_model_text = ""
        self.audio_in_turn = False

    def build_config(self):
        return _build_live_config({"handle": self.resume_handle, "use_extras": self.use_extras})

    async def _sleep_or_stop(self, seconds):
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self.stop_evt.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    async def session_loop(self):
        delay, first = 0.0, True
        while not self.closing:
            connected, started = False, 0.0
            emit("status", v="connecting" if first else "reconnecting")
            first = False
            try:
                async with self.client.aio.live.connect(model=MODEL, config=self.build_config()) as session:
                    self.session = session
                    connected, started = True, time.monotonic()
                    self.ready.set()
                    self._outage_reported = False
                    emit("status", v="ready")
                    self.set_state(self._rest_state())
                    await self._receive_loop(session)
                delay = 0.0 if time.monotonic() - started > 3 else 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                text = f"{type(e).__name__}: {e}"
                up = text.upper()
                code = getattr(e, "code", None)
                if code in (401, 403) or any(m in up for m in FATAL_MARKERS):
                    emit("fatal", text="Gemini rejected the API key. Check GEMINI_API_KEY in the .env file.")
                    self.closing = self.fatal = True
                    break
                if not connected and any(m in up for m in MODEL_MARKERS):
                    emit("fatal", text=f"The Gemini Live model '{MODEL}' is not available to this API key. Check MODEL in the .env file. ({e})")
                    self.closing = self.fatal = True
                    break
                emit("log", text=f"[connection] {text}")
                if not connected and self.use_extras and any(k in up for k in ("RESUMPTION", "COMPRESSION", "CONTEXT_WINDOW", "INVALID ARGUMENT", "UNKNOWN NAME", "1007")):
                    self.use_extras = False
                    self.resume_handle = None
                    emit("log", text="[connection] retrying without session resumption")
                    delay = 0.0
                elif not connected and self.resume_handle:
                    self.resume_handle = None
                    delay = 0.0
                elif _is_transient_ws_error(e):
                    delay = 0.2 if connected else min(max(delay, 0.5) * 1.5, 5.0)
                else:
                    delay = 1.0 if delay == 0 else min(delay * 2, 10.0)
                    if not self._outage_reported:
                        self._outage_reported = True
                        emit("error", text=f"Connection to Gemini Live lost or unavailable ({e}). Retrying automatically…")
            finally:
                self.session = None
                self.ready.clear()
                self.player.interrupt()
                self._reset_turn()
                if self.state in ("speaking", "thinking"):
                    self.set_state(self._rest_state())
            if self.closing:
                break
            emit("status", v="reconnecting")
            await self._sleep_or_stop(delay)
        self.stop_evt.set()

    async def _receive_loop(self, session):
        while not self.closing:
            got = False
            async for response in session.receive():
                got = True
                self._handle_response(session, response)
            if not got:
                return

    def _handle_response(self, session, response):
        upd = getattr(response, "session_resumption_update", None)
        if upd and getattr(upd, "resumable", False) and getattr(upd, "new_handle", None):
            self.resume_handle = upd.new_handle
        if getattr(response, "go_away", None):
            emit("log", text="[connection] server asked to reconnect soon; the conversation will resume automatically.")

        tc = getattr(response, "tool_call", None)
        if tc and getattr(tc, "function_calls", None):
            task = asyncio.create_task(self._run_tool_call(session, tc))
            self.tool_tasks.add(task)
            task.add_done_callback(self.tool_tasks.discard)

        sc = getattr(response, "server_content", None)
        if not sc:
            return

        if getattr(sc, "interrupted", False):
            # Ignore false server-side interruptions caused by speaker echo unless user triggered near-field barge-in
            if not _mic_stream_state.get("barge_in_triggered") and (self.audio_in_turn or _is_mic_muted_for_echo()):
                return
            _mic_stream_state["barge_in_triggered"] = False
            self.player.interrupt()
            self._wake_greet_until = 0.0
            if self.turn_active:
                emit("turn_end", interrupted=True)
            self._reset_turn()
            self.set_state(self._rest_state())
            emit("log", text="[audio] playback interrupted")
            return

        it = getattr(sc, "input_transcription", None)
        if it and getattr(it, "text", None):
            txt = it.text
            if _is_mic_muted_for_echo() or _is_self_echo_transcription(txt, self._last_model_text, self._last_model_ts):
                emit("log", text=f"[audio] suppressed self-echo transcription: {txt!r}")
            else:
                ducker.pulse(5.0)
                emit("user", text=txt)
                if IS_TTY:
                    print(f"You: {txt}", flush=True)
                if _pending_perms:
                    dec = _parse_voice_perm_decision(txt)
                    if dec:
                        _resolve_pending_perm_by_voice(dec)

        mt = getattr(sc, "model_turn", None)
        if mt:
            for part in (mt.parts or []):
                text = getattr(part, "text", None)
                if text and not getattr(part, "thought", False):
                    self.turn_active = True
                    self.fallback_text += text
                    self.turn_model_text += text
                    self._last_model_text = self.turn_model_text[-450:]
                    self._last_model_ts = time.monotonic()
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    self.turn_active = True
                    self._on_audio(inline.data)

        ot = getattr(sc, "output_transcription", None)
        if ot and getattr(ot, "text", None):
            self.turn_active = True
            self.turn_has_out_text = True
            self.turn_model_text += ot.text
            self._last_model_text = self.turn_model_text[-450:]
            self._last_model_ts = time.monotonic()
            _mic_stream_state["mute_until"] = max(
                float(_mic_stream_state.get("mute_until", 0.0) or 0.0),
                time.monotonic() + 0.90,
            )
            emit("model", text=ot.text)
            if IS_TTY:
                print(f"Nova: {ot.text}", flush=True)
            if not self.voice_output:
                self.set_state("speaking")

        done = bool(getattr(sc, "turn_complete", False))
        status = getattr(sc, "interaction_status", None)
        if status is not None and str(status).upper().endswith("IDLE"):
            done = True
        if done:
            self._finish_turn()

    def _on_audio(self, data):
        if not self.voice_output:
            return
        if self.player.write(data):
            self.audio_in_turn = True
            self.set_state("speaking")

    def _finish_turn(self):
        self._wake_greet_until = 0.0
        if self.turn_active:
            if not self.turn_has_out_text and self.fallback_text.strip():
                emit("model", text=self.fallback_text)
                if IS_TTY:
                    print(f"Nova: {self.fallback_text}", flush=True)
            emit("turn_end", interrupted=False)
        sent_end = self.audio_in_turn and self.player.end_turn()
        self._reset_turn()
        if not sent_end and self.state in ("speaking", "thinking"):
            self.set_state(self._rest_state())

    async def _run_tool_call(self, session, tool_call):
        if self.state != "speaking":
            self.set_state("thinking")
        results = []
        ordered_calls = sorted(
            tool_call.function_calls,
            key=lambda c: 0 if c.name == "manage_permission" else 1
        )
        has_click_tool = any(c.name in ("skip_youtube_ad", "click_ui_element", "click_mouse") for c in ordered_calls)
        for call in ordered_calls:
            if self.closing:
                return
            if has_click_tool and call.name == "press_key" and str((call.args or {}).get("key", "")).lower().strip() in ("enter", "return"):
                result = _normalize_tool_result(
                    call.name,
                    {"ok": True, "verified": True, "message": "Skipped Enter key press because the button was already clicked directly."},
                )
                emit("tool", name=call.name, result=result)
                results.append(types.FunctionResponse(name=call.name, id=call.id, response=result))
                continue
            fn = TOOLS.get(call.name)
            if call.name in ("look_at_screen", "inspect_video", "take_screenshot", "point_at_screen"):
                emit("indicator", kind="screen", active=True)
            elif call.name in ("click_ui_element", "skip_youtube_ad", "close_browser_tab", "control_youtube_video", "type_text_into_ui", "write_in_notepad", "press_keyboard_hotkey", "scroll_desktop", "click_mouse", "mouse_control", "type_text", "press_key", "hotkey"):
                emit("indicator", kind="control", active=True)

            try:
                if fn is None:
                    raw_res = {"ok": False, "verified": False, "message": f"Unknown tool {call.name}"}
                else:
                    call_args = dict(call.args or {})
                    valid, reason = validate_proposed_action({"action": call.name, "arguments": call_args})
                    if not valid:
                        raw_res = {"ok": False, "verified": False, "message": f"Blocked unsafe or invalid tool proposal: {reason}"}
                    else:
                        self.set_state("executing")
                        broker_res = await asyncio.to_thread(
                            live_action_broker.execute,
                            {
                                "action": call.name,
                                "arguments": call_args,
                                "description": f"Execute {call.name}({', '.join(f'{k}={v!r}' for k, v in list(call_args.items())[:3])})",
                            },
                        )
                        if not broker_res.get("ok"):
                            raw_res = {
                                "ok": False,
                                "verified": False,
                                "denied": bool(broker_res.get("denied", False)),
                                "permission_denied": bool(broker_res.get("permission_denied", False)),
                                "message": str(broker_res.get("message") or broker_res.get("error") or f"Action {call.name} was not permitted."),
                            }
                        else:
                            raw_res = broker_res.get("result")
                result = _normalize_tool_result(call.name, raw_res)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                result = _normalize_tool_result(
                    call.name,
                    {"ok": False, "verified": False, "message": f"{type(e).__name__}: {e}"},
                )
            finally:
                emit("indicator", kind="screen", active=False)
                emit("indicator", kind="control", active=False)

            emit("tool", name=call.name, result=result)
            if (
                workflow_recorder.is_recording
                and result.get("ok")
                and call.name in ALLOWLISTED_WORKFLOW_ACTIONS
                and call.name != "workflow_control"
            ):
                try:
                    workflow_recorder.record(
                        call.name,
                        dict(call.args or {}),
                        str(result.get("message") or f"Executed {call.name}")[:160],
                    )
                    _emit_workflows_sync()
                except Exception as rec_err:
                    emit("log", text=f"[Workflow Recorder] Skipped recording step '{call.name}': {rec_err}")
            results.append(types.FunctionResponse(name=call.name, id=call.id, response=result))

        if results and session is self.session and not self.closing:
            try:
                async with self.send_lock:
                    await session.send_tool_response(function_responses=results)
            except Exception as e:
                emit("log", text=f"[tool] could not return results to Gemini: {type(e).__name__}: {e}")

    async def text_worker(self):
        while not self.closing:
            text = await self.text_q.get()
            sent = False
            for _attempt in range(2):
                try:
                    await asyncio.wait_for(self.ready.wait(), 20)
                except asyncio.TimeoutError:
                    break
                sess = self.session
                if sess is None:
                    continue
                try:
                    self.set_state("thinking")
                    async with self.send_lock:
                        try:
                            await sess.send_realtime_input(text=text)
                        except TypeError:
                            await sess.send_client_content(
                                turns=types.Content(role="user", parts=[types.Part.from_text(text=text)]),
                                turn_complete=True
                            )
                    sent = True
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    emit("log", text=f"[chat] send failed: {type(e).__name__}: {e}")
                    await asyncio.sleep(0.3)
            if not sent:
                offline_act = match_offline_command(text)
                if offline_act is not None:
                    self.set_state("executing")
                    b_res = await asyncio.to_thread(live_action_broker.execute, offline_act)
                    raw_out = b_res.get("result") if b_res.get("ok") else b_res
                    msg = (raw_out.get("message") if isinstance(raw_out, dict) else str(raw_out or "")) or "Offline command executed."
                    emit("model", text=f"[Offline Local Fallback] {msg}")
                    emit("turn_end")
                    self.set_state(self._rest_state())
                else:
                    self.set_state(self._rest_state())
                    emit("error", text="Your message could not be delivered because Gemini Live is not connected. Check your internet connection and API key, then send it again.")

    async def mic_start(self):
        if self.mic_task is not None and not self.mic_task.done():
            emit("mic", v="on" if self.mic_on else "starting")
            return
        if sd is None:
            emit("mic", v="error", text=f"Microphone unavailable (audio library problem: {_SD_ERROR}).")
            return
        emit("mic", v="starting")
        self.mic_task = asyncio.create_task(self._mic_main())

    async def mic_stop(self):
        task, self.mic_task = self.mic_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.mic_on = False
        _mic_stream_state["active"] = False
        ducker.set_listening(False)
        emit("mic", v="off")
        sess = self.session
        if sess is not None:
            try:
                async with self.send_lock:
                    await sess.send_realtime_input(audio_stream_end=True)
            except Exception:
                pass
        if self.state == "listening":
            self.set_state("idle")

    async def _send_audio(self, data):
        global MIC_NOISE_GATE_RMS
        if _pending_perms:
            return
        now = time.monotonic()
        if not _mic_stream_state.get("barge_in_triggered") and (
            (self._wake_greet_until and now < self._wake_greet_until)
            or _is_mic_muted_for_echo()
            or self.state in ("speaking", "thinking")
        ):
            # Send a few frames of digital silence to cleanly close any active server VAD turn, then suppress mic upload
            sent_silent = int(_mic_stream_state.get("silent_frames_sent", 0) or 0)
            if sent_silent >= 3:
                return
            _mic_stream_state["silent_frames_sent"] = sent_silent + 1
            data = b"\x00" * len(data)
        else:
            rms = _pcm16_rms(data)
            ambient = float(_mic_stream_state.get("ambient_rms", 42.0) or 42.0)
            if rms < MIC_NOISE_GATE_RMS * 1.15:
                alpha = 0.14 if rms < ambient else 0.03
                ambient = max(12.0, min(320.0, (1.0 - alpha) * ambient + alpha * rms))
                _mic_stream_state["ambient_rms"] = ambient
            if _mic_stream_state.get("auto_nearest"):
                target_gate = max(85.0, min(480.0, ambient * 2.2 + 75.0))
                if abs(target_gate - MIC_NOISE_GATE_RMS) >= 12.0:
                    MIC_NOISE_GATE_RMS = round(target_gate, 1)
                    _mic_stream_state["sensitivity"] = _gate_rms_to_sensitivity(MIC_NOISE_GATE_RMS)

            if (now - float(_mic_stream_state.get("last_level_emit_ts", 0.0) or 0.0)) >= 0.06:
                _mic_stream_state["last_level_emit_ts"] = now
                emit(
                    "mic_level",
                    rms=round(rms, 1),
                    gate=round(MIC_NOISE_GATE_RMS, 1),
                    sensitivity=int(_mic_stream_state.get("sensitivity", 68)),
                    auto_nearest=bool(_mic_stream_state.get("auto_nearest", False)),
                    active=bool(rms >= MIC_NOISE_GATE_RMS),
                )

            if rms >= MIC_NOISE_GATE_RMS:
                _mic_stream_state["voice_hangover_until"] = now + 0.65
                _mic_stream_state["silent_frames_sent"] = 0
            elif now < float(_mic_stream_state.get("voice_hangover_until", 0.0) or 0.0):
                _mic_stream_state["silent_frames_sent"] = 0
            else:
                # Filter out quiet micro-sounds, static, and fan noise so they don't trigger false VAD interruptions
                data = b"\x00" * len(data)

        sess = self.session
        if sess is None:
            return
        try:
            async with self.send_lock:
                await sess.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _mic_main(self):
        loop = asyncio.get_running_loop()
        q = asyncio.Queue(maxsize=32)

        def clear_q():
            try:
                while True:
                    q.get_nowait()
            except asyncio.QueueEmpty:
                pass

        def push(data):
            if _is_mic_muted_for_echo():
                clear_q()
                return
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(data)

        def cb(indata, frames, time_info, status):
            raw = bytes(indata)
            if _is_mic_muted_for_echo():
                mic_rms = _pcm16_rms(raw)
                if _check_near_field_barge_in(mic_rms):
                    self.player.interrupt()
                else:
                    try:
                        loop.call_soon_threadsafe(clear_q)
                    except RuntimeError:
                        pass
                    return
            if _pending_perms and not _is_mic_muted_for_echo():
                try:
                    if _perm_audio_queue.full():
                        _perm_audio_queue.get_nowait()
                    _perm_audio_queue.put_nowait(raw)
                except Exception:
                    pass
            try:
                loop.call_soon_threadsafe(push, raw)
            except RuntimeError:
                pass

        failures = 0
        try:
            while not self.closing:
                try:
                    with sd.RawInputStream(samplerate=16000, blocksize=1024, channels=1, dtype="int16", callback=cb) as stream:
                        self.mic_on, failures = True, 0
                        _mic_stream_state["active"] = True
                        ducker.set_listening(True)
                        emit("mic", v="on")
                        if self.state == "idle":
                            self.set_state("listening")
                        while not self.closing:
                            try:
                                data = await asyncio.wait_for(q.get(), 1.0)
                            except asyncio.TimeoutError:
                                if not stream.active:
                                    raise OSError("the microphone stopped delivering audio")
                                continue
                            await self._send_audio(data)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.mic_on = False
                    _mic_stream_state["active"] = False
                    failures += 1
                    emit("log", text=f"[microphone] {type(e).__name__}: {e}")
                    if failures >= 3:
                        emit("mic", v="error", text=f"Microphone unavailable: {e}")
                        if self.state == "listening":
                            self.set_state("idle")
                        return
                    await asyncio.sleep(0.5)
        finally:
            self.mic_on = False
            _mic_stream_state["active"] = False
            ducker.set_listening(False)

    async def reminder_loop(self):
        while not self.closing:
            await self.ready.wait()
            try:
                result = await asyncio.to_thread(task_control, action="due")
                if result.get("ok"):
                    for task in result.get("tasks", []):
                        sess = self.session
                        if sess is None or self.closing:
                            break
                        notice = (
                            "[PC reminder scheduler] A saved reminder is due. Tell the user briefly: "
                            f"{task.get('title', 'Untitled task')}. "
                            "Do not take any action on the task unless the user asks."
                        )
                        async with self.send_lock:
                            await sess.send_realtime_input(text=notice)
                        await asyncio.to_thread(task_control, action="mark_reminded", task_id=task.get("id", ""))
                        emit("log", text=f"[reminder] due: {task.get('title', 'Untitled task')}")
                        await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                emit("log", text=f"[reminder] {type(e).__name__}: {e}")
            await asyncio.sleep(15)

    async def command_loop(self, cmd_q):
        while not self.closing:
            msg = await cmd_q.get()
            cmd = msg.get("cmd", "")
            try:
                if cmd == "text":
                    text = str(msg.get("text", "")).strip()[:8000]
                    if text:
                        route_res = NovaActionRouter().route(text, source="text")
                        if route_res.route == "direct" and route_res.intent is not None:
                            kind = route_res.intent.kind
                            if kind == "show_companion":
                                emit("ui_cmd", action="show_floating_bot")
                                emit("model", text="Showing the floating companion.")
                                emit("turn_end", interrupted=False)
                                continue
                            elif kind == "hide_companion":
                                emit("ui_cmd", action="hide_floating_bot")
                                emit("model", text="Hid the floating companion.")
                                emit("turn_end", interrupted=False)
                                continue
                            elif kind == "point_at_screen":
                                res = await asyncio.to_thread(point_at_screen)
                                emit("model", text=res.get("message", ""))
                                emit("turn_end", interrupted=False)
                                continue
                            elif kind == "workflow_record_start":
                                res = workflow_control(action="start_recording")
                                emit("model", text=res.get("message", ""))
                                emit("turn_end", interrupted=False)
                                continue
                            elif kind == "workflow_record_stop":
                                res = workflow_control(action="stop_recording")
                                emit("model", text=res.get("message", ""))
                                emit("turn_end", interrupted=False)
                                continue
                            elif kind == "walkthrough_next":
                                res = walkthrough_control(action="next")
                                emit("model", text=res.get("message", ""))
                                emit("turn_end", interrupted=False)
                                continue
                            elif kind == "walkthrough_back":
                                res = walkthrough_control(action="back")
                                emit("model", text=res.get("message", ""))
                                emit("turn_end", interrupted=False)
                                continue
                            elif kind == "walkthrough_stop":
                                res = walkthrough_control(action="stop")
                                emit("model", text=res.get("message", ""))
                                emit("turn_end", interrupted=False)
                                continue
                            elif kind == "cancel_operation":
                                if _active_workflow_player is not None:
                                    _active_workflow_player.cancel()
                                if workflow_recorder.is_recording:
                                    workflow_recorder.cancel()
                                    _emit_workflows_sync()
                                if _active_walkthrough is not None and _active_walkthrough.active:
                                    walkthrough_control(action="stop")
                                emit("model", text="Cancelled active operation.")
                                emit("turn_end", interrupted=False)
                                continue
                        self.text_q.put_nowait(text)
                elif cmd == "walkthrough_cmd":
                    w_act = msg.get("action", "status")
                    w_goal = msg.get("goal", "")
                    w_steps = msg.get("steps")
                    res = await asyncio.to_thread(walkthrough_control, action=w_act, goal=w_goal, steps=w_steps)
                    emit("log", text=f"[Walkthrough] {res.get('message', '')}")
                elif cmd == "workflow_cmd":
                    wf_act = msg.get("action", "list")
                    wf_name = msg.get("name", "")
                    wf_id = msg.get("workflow_id", "")
                    if wf_act == "replay":
                        threading.Thread(target=replay_saved_workflow, args=(wf_id,), daemon=True).start()
                    elif wf_act == "update_steps":
                        # Allow editing/updating a saved workflow's name and steps from the review/edit UI
                        target_id = str(wf_id).strip()
                        new_steps_raw = msg.get("steps", [])
                        for existing in workflow_store.list_workflows():
                            if existing.workflow_id == target_id:
                                valid_steps = []
                                for s in new_steps_raw:
                                    if isinstance(s, dict) and s.get("action") in ALLOWLISTED_WORKFLOW_ACTIONS:
                                        valid_steps.append(
                                            WorkflowStep(
                                                action=str(s["action"]),
                                                arguments=dict(s.get("arguments") or {}),
                                                description=str(s.get("description") or s["action"]),
                                                created_at=float(s.get("created_at") or time.time()),
                                            )
                                        )
                                updated = Workflow(
                                    workflow_id=existing.workflow_id,
                                    name=(wf_name or existing.name).strip()[:100],
                                    created_at=existing.created_at,
                                    steps=valid_steps,
                                )
                                workflow_store.save(updated)
                                break
                        _emit_workflows_sync()
                    else:
                        res = workflow_control(action=wf_act, name=wf_name, workflow_id=wf_id)
                        emit("log", text=f"[Workflow] {res.get('message', '')}")
                elif cmd == "point_at_screen":
                    q = str(msg.get("question", "")).strip()
                    await asyncio.to_thread(point_at_screen, question=q)
                elif cmd in ("mic_start", "voice_start"):
                    await self.mic_start()
                elif cmd in ("mic_stop", "voice_stop"):
                    await self.mic_stop()
                elif cmd == "wake_greet":
                    ducker.pulse(8.0)
                    await self.mic_start()
                    self._wake_greet_until = time.monotonic() + 2.8
                    self.text_q.put_nowait(
                        "[System notification: The user just said 'Hey Nova' to wake you up. Greet the user warmly and concisely in English with 'Hey! I'm here. What can I do for you?', and wait for their command.]"
                    )
                elif cmd == "voice_output":
                    self.voice_output = bool(msg.get("on", msg.get("enabled", True)))
                    if not self.voice_output:
                        self.player.interrupt()
                        if self.state == "speaking":
                            self.set_state(self._rest_state())
                elif cmd == "perm_response":
                    req_id = msg.get("id")
                    decision = msg.get("decision", "deny")
                    with _pending_perms_lock:
                        if req_id in _pending_perms:
                            _pending_perms[req_id]["decision"] = decision
                            _pending_perms[req_id]["event"].set()
                elif cmd == "perm_revoke":
                    cat = msg.get("category", "all")
                    if cat == "all":
                        _user_revoked_categories.update(CATEGORIES.keys())
                        permissions.revoke_all()
                        emit("perm_sync", active=permissions.get_status())
                        emit("log", text="All session permissions have been revoked.")
                    else:
                        _user_revoked_categories.add(cat)
                        permissions.revoke(cat)
                        emit("perm_sync", active=permissions.get_status())
                        emit("log", text=f"Permission for '{cat}' has been revoked.")
                elif cmd == "gesture_control":
                    g_act = msg.get("action", "toggle")
                    res = await asyncio.to_thread(hand_gesture_control, action=g_act)
                    emit("log", text=f"[Hand Gesture] {res.get('message', '')}")
                elif cmd == "mic_sensitivity":
                    set_mic_sensitivity(
                        sensitivity=msg.get("sensitivity"),
                        auto_nearest=msg.get("auto_nearest"),
                        calibrate_now=bool(msg.get("calibrate_now", False)),
                    )
                elif cmd in ("shutdown", "quit"):
                    self.stop_evt.set()
                    return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                emit("log", text=f"[command] {cmd} failed: {type(e).__name__}: {e}")

    async def shutdown(self):
        self.closing = True
        self.stop_evt.set()
        gesture_manager.stop()
        task, self.mic_task = self.mic_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for t in list(self.tool_tasks):
            t.cancel()
        if self.tool_tasks:
            await asyncio.gather(*list(self.tool_tasks), return_exceptions=True)
        await asyncio.to_thread(self.player.close)
        ducker.stop()
        self.mic_on = False
        _mic_stream_state["active"] = False
        self.set_state("idle")


def _stdin_reader(loop, cmd_q):
    """Blocking stdin reader thread with fast-path resolution for permission and gesture commands."""
    try:
        stream = sys.stdin.buffer if hasattr(sys.stdin, "buffer") else sys.stdin
        while True:
            line = stream.readline()
            if not line:
                loop.call_soon_threadsafe(cmd_q.put_nowait, {"cmd": "shutdown"})
                return
            if isinstance(line, bytes):
                line = line.decode("utf-8", "replace")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                if not isinstance(msg, dict):
                    msg = {"cmd": "text", "text": line}
            except ValueError:
                msg = {"cmd": "text", "text": line}

            if msg.get("cmd") == "perm_response":
                req_id = msg.get("id")
                decision = msg.get("decision", "deny")
                with _pending_perms_lock:
                    if req_id in _pending_perms:
                        _pending_perms[req_id]["decision"] = decision
                        _pending_perms[req_id]["event"].set()
                continue

            if msg.get("cmd") == "workflow_step_response":
                req_id = msg.get("id")
                approved = bool(msg.get("approved", False))
                cancelled = bool(msg.get("cancelled", False))
                if cancelled and _active_workflow_player is not None:
                    _active_workflow_player.cancel()
                with _pending_step_approvals_lock:
                    if req_id in _pending_step_approvals:
                        _pending_step_approvals[req_id]["approved"] = approved and not cancelled
                        _pending_step_approvals[req_id]["event"].set()
                continue

            if msg.get("cmd") == "workflow_cmd" and msg.get("action") == "cancel_replay":
                if _active_workflow_player is not None:
                    _active_workflow_player.cancel()
                with _pending_step_approvals_lock:
                    for info in _pending_step_approvals.values():
                        info["approved"] = False
                        info["event"].set()
                continue

            if msg.get("cmd") == "mic_sensitivity":
                try:
                    set_mic_sensitivity(
                        sensitivity=msg.get("sensitivity"),
                        auto_nearest=msg.get("auto_nearest"),
                        calibrate_now=bool(msg.get("calibrate_now", False)),
                    )
                except Exception:
                    pass
                continue

            if msg.get("cmd") == "gesture_control":
                g_act = msg.get("action", "toggle")
                def _run_g(a=g_act):
                    try:
                        res = hand_gesture_control(action=a)
                        emit("log", text=f"[Hand Gesture] {res.get('message', '')}")
                    except Exception as err:
                        emit("log", text=f"[Hand Gesture] Error: {err}")
                threading.Thread(target=_run_g, daemon=True).start()
                continue

            loop.call_soon_threadsafe(cmd_q.put_nowait, msg)
    except (RuntimeError, OSError, ValueError):
        return


async def amain():
    global MODEL, _ipc_loop_running
    _ipc_loop_running = True
    load_dotenv(HERE / ".env")
    key = os.getenv("GEMINI_API_KEY")
    MODEL = os.getenv("MODEL", "gemini-3.8-live")
    if not key:
        emit("fatal", text="GEMINI_API_KEY is missing from the .env file.")
        return 2

    mgr = LiveManager(genai.Client(api_key=key))
    loop = asyncio.get_running_loop()
    cmd_q = asyncio.Queue()
    threading.Thread(target=_stdin_reader, args=(loop, cmd_q), name="nova-stdin", daemon=True).start()

    tasks = [
        asyncio.create_task(c)
        for c in (mgr.session_loop(), mgr.text_worker(), mgr.reminder_loop(), mgr.command_loop(cmd_q))
    ]
    emit("log", text="Nova backend started.")

    if "--mic-active" in sys.argv or (IS_TTY and "--mic-paused" not in sys.argv):
        await mgr.mic_start()

    try:
        await mgr.stop_evt.wait()
    finally:
        await mgr.shutdown()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        emit("status", v="stopped")
    return 2 if mgr.fatal else 0


async def run_session(*args, **kwargs):
    """Compatibility wrapper around amain()."""
    return await amain()


async def main():
    return await amain()


if __name__ == "__main__":
    code = 0
    try:
        code = asyncio.run(amain())
    except KeyboardInterrupt:
        code = 0
    except Exception as exc:
        emit("fatal", text=f"Backend crashed: {type(exc).__name__}: {exc}")
        code = 1
    try:
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(code or 0)
