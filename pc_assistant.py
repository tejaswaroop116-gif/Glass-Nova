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
from nova_action_router import NovaActionRouter, ApprovedActionBroker, validate_proposed_action, ACTION_SCHEMAS, match_offline_command, OFFLINE_VOICE_PHRASES
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


client = genai.Client(api_key=API_KEY) if API_KEY else None

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

import nova_audio
from nova_audio import (
    AudioPlayer,
    MIC_NOISE_GATE_RMS,
    _check_near_field_barge_in,
    _gate_rms_to_sensitivity,
    _is_mic_muted_for_echo,
    _is_self_echo_transcription,
    _mic_stream_state,
    _pcm16_rms,
    _sensitivity_to_gate_rms,
    mic_sensitivity_control,
    set_audio_emit_callback,
    set_mic_sensitivity,
)
from nova_tool_defs import SYSTEM, TOOL_DEFS, schema

set_audio_emit_callback(emit)

_vosk_model = None
_vosk_lock = threading.Lock()

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
    High-impact or destructive actions require explicit action-specific confirmation."""
    name = str(action.get("action") or "")
    args = action.get("arguments") or {}
    if name == "delete_file":
        return _request_step_approval_from_ui(
            1, 1, name, args, f"Permanently delete file or folder: {args.get('path', '')}"
        )
    if name == "memory_control" and str(args.get("action", "")).lower().strip() in ("clear", "clear_all"):
        return _request_step_approval_from_ui(
            1, 1, name, args, "Permanently clear all saved memories"
        )
    if name == "task_control" and str(args.get("action", "")).lower().strip() in ("delete", "remove"):
        return _request_step_approval_from_ui(
            1, 1, name, args, f"Permanently delete tracked task '{args.get('task_id', '')}'"
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
        self._offline_vosk_rec = None
        self._offline_vosk_failed = False
        self._offline_partial = ""
        self._offline_partial_ts = 0.0
        self._offline_last_speech_ts = 0.0
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
        if self.client is None:
            emit("status", v="offline")
            emit("log", text="[Offline Mode] No GEMINI_API_KEY configured — Offline Local Commands (voice & chat) are active.")
            await self.stop_evt.wait()
            return
        delay, first = 0.0, True
        while not self.closing:
            connected, started = False, 0.0
            emit("status", v="connecting" if first else "offline")
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
                    emit("status", v="offline")
                    emit("log", text="Gemini rejected the API key. Running in Offline Local Mode.")
                    await self.stop_evt.wait()
                    return
                if not connected and any(m in up for m in MODEL_MARKERS):
                    emit("status", v="offline")
                    emit("log", text=f"The Gemini Live model '{MODEL}' is not available ({e}). Running in Offline Local Mode.")
                    await self.stop_evt.wait()
                    return
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
                        emit("log", text=f"[Offline Mode] Gemini Live unreachable ({e}) — Offline Local Commands (voice & chat) are active.")
            finally:
                self.session = None
                self.ready.clear()
                self.player.interrupt()
                self._reset_turn()
                if self.state in ("speaking", "thinking"):
                    self.set_state(self._rest_state())
            if self.closing:
                break
            emit("status", v="offline")
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

    def _speak_offline_tts(self, text: str):
        """Speak a reply locally using Windows SAPI when offline and voice_output is enabled."""
        if not self.voice_output or not text or IS_TTY:
            return
        clean = str(text).replace("[Offline Local Fallback]", "").strip()
        if not clean:
            return

        def _tts_worker(msg_text: str):
            try:
                import comtypes.client
            except Exception:
                comtypes = None
            try:
                _mic_stream_state["speaking"] = True
                _mic_stream_state["mute_until"] = time.monotonic() + max(1.5, min(6.0, len(msg_text) * 0.065))
                if sys.platform == "win32":
                    try:
                        import win32com.client
                        import pythoncom
                        pythoncom.CoInitialize()
                        try:
                            voice = win32com.client.Dispatch("SAPI.SpVoice")
                            voice.Rate = 1
                            voice.Speak(msg_text)
                        finally:
                            pythoncom.CoUninitialize()
                    except Exception:
                        pass
            finally:
                _mic_stream_state["speaking"] = False
                _mic_stream_state["mute_until"] = time.monotonic() + 0.35

        threading.Thread(target=_tts_worker, args=(clean,), daemon=True).start()

    def execute_offline_action_sync(self, text: str, from_voice: bool = False) -> bool:
        """Synchronous execution of deterministic offline commands or offline greeting.
        Returns True if handled, False if unrecognized."""
        raw_clean = " ".join((text or "").strip().lower().split())
        if not raw_clean:
            return False

        # Offline conversational greeting
        if raw_clean in ("hi", "hello", "hey", "hey nova", "nova", "are you there", "help", "offline help", "what can you do offline"):
            reply = (
                "Hey! I'm running in Offline Local Mode. You can ask me by voice or chat to:\n"
                "• Open or close apps (Calculator, Notepad, File Explorer, Paint, Task Manager, Settings, Chrome, Edge, VS Code)\n"
                "• Open folders (Downloads, Documents, Desktop, Pictures, Music, Videos)\n"
                "• Control volume (mute, unmute, volume up/down, set volume to 50%) & brightness\n"
                "• Manage windows (minimize, maximize, close window, switch window, show desktop)\n"
                "• Take screenshots, check system/battery status, check time/date, list tasks/memories, or toggle the companion & hand gestures."
            )
            emit("model", text=reply)
            emit("turn_end", interrupted=False)
            if from_voice:
                self._speak_offline_tts("I am here in offline mode. What can I do for you?")
            self.set_state(self._rest_state())
            return True

        offline_act = match_offline_command(text)
        if offline_act is None:
            return False

        self.set_state("executing")
        act_name = offline_act.get("action", "")
        b_res = live_action_broker.execute(offline_act)
        raw_out = b_res.get("result") if b_res.get("ok") else b_res

        msg = ""
        if isinstance(raw_out, dict):
            # Format human-readable responses for time, tasks, memories, and system status
            if act_name == "task_control" and offline_act.get("arguments", {}).get("action") == "now" and raw_out.get("local_time"):
                msg = f"Current local date & time: {raw_out.get('local_time')} ({raw_out.get('timezone', 'local')})."
            elif act_name == "task_control" and "tasks" in raw_out:
                t_list = raw_out.get("tasks") or []
                if not t_list:
                    msg = "You have no saved tasks."
                else:
                    titles = ", ".join(f"{t.get('title', 'Task')} [{t.get('status', 'active')}]" for t in t_list[:8])
                    msg = f"{raw_out.get('message', '')} {titles}"
            elif act_name == "memory_control" and "memories" in raw_out:
                m_list = raw_out.get("memories") or []
                if not m_list:
                    msg = "You have no saved memories."
                else:
                    items_str = ", ".join(f"{m.get('key')}: {m.get('value')}" for m in m_list[:8])
                    msg = f"{raw_out.get('message', '')} {items_str}"
            else:
                msg = str(raw_out.get("message") or raw_out.get("error") or "")
        else:
            msg = str(raw_out or "")

        if not msg:
            msg = f"Completed {offline_act.get('description') or act_name}."

        emit("tool", name=act_name, result=msg)
        emit("model", text=msg)
        emit("turn_end", interrupted=False)
        if from_voice or not self.ready.is_set():
            self._speak_offline_tts(msg)
        self.set_state(self._rest_state())
        return True

    async def _execute_offline_action(self, text: str, from_voice: bool = False) -> bool:
        """Async wrapper around execute_offline_action_sync."""
        return await asyncio.to_thread(self.execute_offline_action_sync, text, from_voice)

    def _get_offline_vosk_recognizer(self):
        if self._offline_vosk_rec is not None:
            return self._offline_vosk_rec
        if self._offline_vosk_failed:
            return None
        try:
            import vosk
            vosk.SetLogLevel(-1)
            model = vosk.Model(model_name="vosk-model-small-en-us-0.15")
            grammar = json.dumps(OFFLINE_VOICE_PHRASES)
            self._offline_vosk_rec = vosk.KaldiRecognizer(model, 16000, grammar)
            return self._offline_vosk_rec
        except Exception as e:
            self._offline_vosk_failed = True
            emit("log", text=f"[Offline Voice] Vosk model unavailable: {e}")
            return None

    async def _process_offline_voice_pcm(self, raw_pcm: bytes, is_speech: bool, now: float):
        rec = self._get_offline_vosk_recognizer()
        if rec is None:
            return
        if is_speech:
            self._offline_last_speech_ts = now

        spoken = ""
        if rec.AcceptWaveform(raw_pcm):
            res = json.loads(rec.Result() or "{}")
            spoken = (res.get("text") or "").replace("[unk]", " ").strip()
            self._offline_partial = ""
        else:
            part = json.loads(rec.PartialResult() or "{}")
            p_txt = (part.get("partial") or "").replace("[unk]", " ").strip()
            if p_txt:
                if p_txt != self._offline_partial:
                    self._offline_partial = p_txt
                    self._offline_partial_ts = now
                elif (not is_speech) and (now - self._offline_partial_ts >= 0.55):
                    spoken = self._offline_partial
                    self._offline_partial = ""
                    rec.Reset()

        spoken = " ".join(spoken.split())
        if not spoken or spoken in ("hey", "nova", "hey nova"):
            return

        emit("user", text=spoken)
        handled = await self._execute_offline_action(spoken, from_voice=True)
        if not handled:
            emit("model", text=f"I heard '{spoken}', which requires an online Gemini Live connection. Try an offline command like 'open calculator', 'mute volume', 'take screenshot', or 'system status'.")
            emit("turn_end", interrupted=False)

    async def text_worker(self):
        while not self.closing:
            text = await self.text_q.get()
            # If Gemini Live is not connected right now, immediately try deterministic offline execution (0ms wait)
            if not self.ready.is_set() or self.session is None:
                if await self._execute_offline_action(text, from_voice=False):
                    continue
            sent = False
            for _attempt in range(2):
                try:
                    await asyncio.wait_for(self.ready.wait(), 2.0)
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
                    await asyncio.sleep(0.25)
            if not sent:
                if not await self._execute_offline_action(text, from_voice=False):
                    self.set_state(self._rest_state())
                    emit("error", text="Gemini Live is currently offline. Local commands (e.g., 'open calculator', 'open downloads', 'mute volume', 'take screenshot', 'system status', 'what time is it') work offline right now!")

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
        raw_input_pcm = data
        is_speech_frame = False
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
            if rms < nova_audio.MIC_NOISE_GATE_RMS * 1.15:
                alpha = 0.14 if rms < ambient else 0.03
                ambient = max(12.0, min(320.0, (1.0 - alpha) * ambient + alpha * rms))
                _mic_stream_state["ambient_rms"] = ambient
            if _mic_stream_state.get("auto_nearest"):
                target_gate = max(85.0, min(480.0, ambient * 2.2 + 75.0))
                if abs(target_gate - nova_audio.MIC_NOISE_GATE_RMS) >= 12.0:
                    nova_audio.MIC_NOISE_GATE_RMS = round(target_gate, 1)
                    MIC_NOISE_GATE_RMS = nova_audio.MIC_NOISE_GATE_RMS
                    _mic_stream_state["sensitivity"] = _gate_rms_to_sensitivity(nova_audio.MIC_NOISE_GATE_RMS)

            if (now - float(_mic_stream_state.get("last_level_emit_ts", 0.0) or 0.0)) >= 0.06:
                _mic_stream_state["last_level_emit_ts"] = now
                emit(
                    "mic_level",
                    rms=round(rms, 1),
                    gate=round(nova_audio.MIC_NOISE_GATE_RMS, 1),
                    sensitivity=int(_mic_stream_state.get("sensitivity", 68)),
                    auto_nearest=bool(_mic_stream_state.get("auto_nearest", False)),
                    active=bool(rms >= nova_audio.MIC_NOISE_GATE_RMS),
                )

            if rms >= nova_audio.MIC_NOISE_GATE_RMS:
                is_speech_frame = True
                _mic_stream_state["voice_hangover_until"] = now + 0.65
                _mic_stream_state["silent_frames_sent"] = 0
            elif now < float(_mic_stream_state.get("voice_hangover_until", 0.0) or 0.0):
                is_speech_frame = True
                _mic_stream_state["silent_frames_sent"] = 0
            else:
                # Filter out quiet micro-sounds, static, and fan noise so they don't trigger false VAD interruptions
                data = b"\x00" * len(data)

        sess = self.session
        if sess is None or not self.ready.is_set():
            # Offline Voice Mode: transcribe & execute local voice commands via Vosk!
            await self._process_offline_voice_pcm(raw_input_pcm if is_speech_frame else data, is_speech_frame, now)
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
                        # Fast-path: if Gemini Live is not connected right now, execute offline commands in 0ms!
                        if not self.ready.is_set() or self.session is None:
                            if await self._execute_offline_action(text, from_voice=False):
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
                    trailing_cmd = str(msg.get("command", "") or "").strip()
                    if trailing_cmd:
                        emit("user", text=trailing_cmd)
                        if not self.ready.is_set() or self.session is None or match_offline_command(trailing_cmd) is not None:
                            if await self._execute_offline_action(trailing_cmd, from_voice=True):
                                continue
                        self.text_q.put_nowait(trailing_cmd)
                    elif not self.ready.is_set() or self.session is None:
                        emit("model", text="Hey! I'm here (Offline Local Mode). What can I do for you?")
                        emit("turn_end", interrupted=False)
                        self._speak_offline_tts("Hey! I'm here. What can I do for you?")
                    else:
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
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    MODEL = os.getenv("MODEL", "gemini-3.8-live")
    client = genai.Client(api_key=key) if key else None
    if not key:
        emit("log", text="GEMINI_API_KEY is missing from .env — starting in Offline Local Mode.")

    mgr = LiveManager(client)
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
