"""Nova action routing primitives.

Separates deterministic voice commands from AI planning. This module never
executes an action itself. The caller must show the plan and obtain approval
before handing approved steps to an action broker.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable, Optional
import re
import uuid


# Strict allow-listed schema for planner / broker actions
ACTION_SCHEMAS: dict[str, dict[str, Any]] = {
    "open_app": {"required": ["app"], "optional": [], "permission": None},
    "open_file": {"required": [], "optional": ["path", "app"], "permission": None},
    "open_url": {"required": ["url"], "optional": [], "permission": None},
    "search_web": {"required": ["query"], "optional": [], "permission": None},
    "play_youtube": {"required": [], "optional": ["query", "action", "index"], "permission": None},
    "open_folder": {"required": ["path"], "optional": [], "permission": None},
    "system_status": {"required": [], "optional": [], "permission": None},
    "volume_control": {"required": ["action"], "optional": ["value", "amount", "steps"], "permission": None},
    "brightness_control": {"required": ["action"], "optional": ["value", "amount", "steps"], "permission": None},
    "window_control": {"required": ["action"], "optional": ["target"], "permission": "mouse_keyboard"},
    "app_control": {"required": ["action", "app"], "optional": [], "permission": "mouse_keyboard"},
    "take_screenshot": {"required": [], "optional": [], "permission": "screen_view"},
    "write_in_notepad": {"required": ["text"], "optional": ["open_if_closed"], "permission": "mouse_keyboard"},
    "click_ui_element": {"required": ["label"], "optional": ["button", "clicks"], "permission": "mouse_keyboard"},
    "skip_youtube_ad": {"required": [], "optional": [], "permission": "mouse_keyboard"},
    "close_browser_tab": {"required": [], "optional": ["target"], "permission": "mouse_keyboard"},
    "control_youtube_video": {"required": ["action"], "optional": ["timestamp", "seconds"], "permission": "mouse_keyboard"},
    "type_text_into_ui": {"required": ["text"], "optional": ["press_enter"], "permission": "mouse_keyboard"},
    "press_keyboard_hotkey": {"required": ["keys"], "optional": [], "permission": "mouse_keyboard"},
    "scroll_desktop": {"required": ["direction"], "optional": ["amount"], "permission": "mouse_keyboard"},
    "create_folder": {"required": ["path"], "optional": [], "permission": "file_modify"},
    "list_folder": {"required": [], "optional": ["path"], "permission": None},
    "rename_file": {"required": ["path", "new_name"], "optional": [], "permission": "file_modify"},
    "copy_file": {"required": ["source", "destination"], "optional": [], "permission": "file_modify"},
    "move_file": {"required": ["source", "destination"], "optional": [], "permission": "file_modify"},
    "delete_file": {"required": ["path"], "optional": ["confirm"], "permission": "file_delete"},
    "calculator_operation": {"required": ["expression"], "optional": [], "permission": None},
    "write_text_file": {"required": ["filename", "text"], "optional": [], "permission": "file_modify"},
    "read_text_file": {"required": ["filename"], "optional": [], "permission": "document_access"},
    "look_at_screen": {"required": [], "optional": ["query"], "permission": "screen_view"},
    "inspect_document": {"required": [], "optional": ["path", "query", "write_to_notepad"], "permission": "document_access"},
    "inspect_video": {"required": [], "optional": ["query"], "permission": "media_access"},
    "mic_sensitivity_control": {"required": ["action"], "optional": ["value", "amount"], "permission": None},
    "press_key": {"required": ["key"], "optional": [], "permission": "mouse_keyboard"},
    "hotkey": {"required": ["keys"], "optional": [], "permission": "mouse_keyboard"},
    "type_text": {"required": ["text"], "optional": [], "permission": "mouse_keyboard"},
    "click_mouse": {"required": [], "optional": ["x", "y", "button", "clicks"], "permission": "mouse_keyboard"},
    "mouse_control": {"required": ["action"], "optional": ["x", "y", "dx", "dy", "button", "clicks", "amount"], "permission": "mouse_keyboard"},
    "clipboard_control": {"required": ["action"], "optional": ["text"], "permission": "mouse_keyboard"},
    "memory_control": {"required": ["action"], "optional": ["key", "value", "confirm"], "permission": None},
    "task_control": {"required": ["action"], "optional": ["title", "details", "task_id", "status", "due_at", "confirm"], "permission": None},
    "nova_control": {"required": ["action"], "optional": [], "permission": None},
    "hand_gesture_control": {"required": ["action"], "optional": ["show_preview"], "permission": "mouse_keyboard"},
    "manage_permission": {"required": ["action"], "optional": ["category", "scope"], "permission": None},
    "walkthrough_control": {"required": ["action"], "optional": ["goal"], "permission": None},
    "workflow_control": {"required": ["action"], "optional": ["name", "workflow_id"], "permission": None},
    "show_companion": {"required": [], "optional": [], "permission": None},
    "hide_companion": {"required": [], "optional": [], "permission": None},
    "point_at_screen": {"required": [], "optional": ["question"], "permission": "screen_view"},
    "inspect_screen": {"required": [], "optional": ["query"], "permission": "screen_view"},
    "workflow_record_start": {"required": [], "optional": [], "permission": None},
    "workflow_record_stop": {"required": [], "optional": ["name"], "permission": None},
    "walkthrough_start": {"required": [], "optional": ["goal"], "permission": None},
    "walkthrough_next": {"required": [], "optional": [], "permission": None},
    "walkthrough_back": {"required": [], "optional": [], "permission": None},
    "walkthrough_stop": {"required": [], "optional": [], "permission": None},
    "cancel_operation": {"required": [], "optional": [], "permission": None},
    "offline_capabilities": {"required": [], "optional": [], "permission": None},
    "local_knowledge_control": {"required": ["action"], "optional": ["path", "query"], "permission": None},
    "search_local_files": {"required": ["query"], "optional": ["folder", "max_results"], "permission": None},
    "run_safe_python": {"required": ["code"], "optional": ["timeout_seconds"], "permission": None},
}

DANGEROUS_PATTERNS = (
    "cmd.exe", "powershell", "pwsh", "wscript", "cscript", "mshta",
    "rundll32", "regsvr32", "certutil", "bitsadmin", "schtasks",
    "reg.exe", "regedit", "bcdedit", "diskpart", "icacls", "takeown",
    "format ", "del /", "erase /", "rmdir /s", "rd /s", "rm -rf",
    "remove-item", "shutdown /", "restart-computer", "taskkill /f /im explorer",
    "net user", "cipher /w",
)

_DANGEROUS_REGEXES = (
    re.compile(r"\b(?:cmd|powershell|pwsh|wscript|cscript|mshta|rundll32|regsvr32|certutil|bitsadmin|schtasks|bcdedit|diskpart|regedit)\b", re.I),
    re.compile(r"\b(?:rm\s+-rf|del\s+/[a-z]|rmdir\s+/s|rd\s+/s|format\s+[a-z]:|shutdown\s+/[srt]|taskkill\s+/f)", re.I),
)

_SHELL_META_RE = re.compile(r"(?:&&|\|\||[;|`<>]|\$\()")


def validate_proposed_action(action: dict[str, Any], allowed_schemas: Optional[dict[str, dict[str, Any]]] = None) -> tuple[bool, str]:
    """Validate a proposed action dictionary against the strict allow-list and argument schema."""
    if not isinstance(action, dict):
        return False, "Proposed action must be a dictionary."
    name = str(action.get("action") or "").strip()
    if not name:
        return False, "Action name is missing."
    schemas = allowed_schemas if allowed_schemas is not None else ACTION_SCHEMAS
    if name not in schemas:
        return False, f"Action '{name}' is not in the allow-listed schema."
    args = action.get("arguments", {})
    if not isinstance(args, dict):
        return False, "Action arguments must be an object."
    schema = schemas[name]
    for req in schema.get("required", []):
        if req not in args or args[req] is None or (isinstance(args[req], str) and not args[req].strip()):
            return False, f"Missing required argument '{req}' for action '{name}'."
    allowed_keys = set(schema.get("required", [])) | set(schema.get("optional", []))
    unknown_keys = set(args.keys()) - allowed_keys
    if unknown_keys:
        return False, f"Unknown arguments {sorted(unknown_keys)} for action '{name}'."

    # Structural per-action argument checks (beyond simple substring matching)
    if name == "open_url":
        raw_url = str(args.get("url") or "").strip()
        m_scheme = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", raw_url)
        if m_scheme and m_scheme.group(1).lower() not in ("http", "https"):
            return False, f"Rejected unsupported URL scheme '{m_scheme.group(1)}:' in action 'open_url'."
    if name in ("open_app", "app_control", "open_folder"):
        for field in ("app", "path"):
            val = str(args.get(field) or "")
            if val and _SHELL_META_RE.search(val):
                return False, f"Rejected shell metacharacters in '{field}' for action '{name}'."
    if name in ("volume_control", "brightness_control") and "value" in args and args["value"] is not None:
        try:
            v = int(args["value"])
            if v < 0 or v > 100:
                return False, f"Argument 'value' for '{name}' must be between 0 and 100."
        except (TypeError, ValueError):
            return False, f"Argument 'value' for '{name}' must be an integer."

    serialized = " ".join(str(v) for v in args.values()).lower()
    for pat in DANGEROUS_PATTERNS:
        if pat in serialized:
            return False, f"Rejected dangerous argument pattern '{pat}' in action '{name}'."
    for rx in _DANGEROUS_REGEXES:
        m = rx.search(serialized)
        if m:
            return False, f"Rejected dangerous command pattern '{m.group(0)}' in action '{name}'."
    return True, "Valid"


@dataclass
class ActionIntent:
    intent_id: str
    source: str                 # "voice", "text", "ai"
    kind: str                   # "open_app", "search_web", "explain_screen", etc.
    arguments: dict[str, Any]
    explanation: str
    requires_confirmation: bool = True


@dataclass
class RouteResult:
    route: str                  # "direct", "planner", "conversation", "unknown"
    response: str
    intent: Optional[ActionIntent] = None
    plan: Optional[list[dict[str, Any]]] = None


class NovaActionRouter:
    """Routes simple commands deterministically; delegates complex requests to AI."""

    def __init__(self, ai_planner: Optional[Callable[[str], list[dict[str, Any]]]] = None):
        self.ai_planner = ai_planner

    def route(self, utterance: str, source: str = "voice") -> RouteResult:
        text = (utterance or "").strip()
        low = text.lower()

        if not text:
            return RouteResult("unknown", "I didn't catch that.")

        # Wake phrase is stripped here only; wake-word detection remains in wake_word.py.
        low = re.sub(r"^\s*(hey\s+)?nova[\s,:-]*", "", low).strip()
        if not low:
            return RouteResult("conversation", "I'm here.")

        patterns = [
            (r"^(?:show|bring|restore)\s+(?:the\s+)?(?:nova\s+)?(?:floating\s+)?(?:bot|companion|orb)$",
             "show_companion", {}),
            (r"^(?:hide|minimize)\s+(?:the\s+)?(?:nova\s+)?(?:floating\s+)?(?:bot|companion|orb)$",
             "hide_companion", {}),
            (r"^(?:point\s+at\s+(?:the\s+|my\s+)?screen|select\s+(?:a\s+)?screen\s+region|screen\s+pointing)$",
             "point_at_screen", {}),
            (r"^(?:look at|inspect|explain)\s+(?:my\s+)?screen$",
             "inspect_screen", {}),
            (r"^(?:start|begin)\s+(?:recording|record)\s+(?:a\s+)?workflow$",
             "workflow_record_start", {}),
            (r"^(?:stop|finish)\s+(?:recording|record)\s+(?:the\s+)?workflow$",
             "workflow_record_stop", {}),
            (r"^(?:next\s+step|walkthrough\s+next)$",
             "walkthrough_next", {}),
            (r"^(?:previous\s+step|back\s+step|walkthrough\s+back)$",
             "walkthrough_back", {}),
            (r"^(?:stop\s+walkthrough|end\s+walkthrough|exit\s+walkthrough)$",
             "walkthrough_stop", {}),
            (r"^(?:cancel|stop\s+replay|cancel\s+workflow|abort)$",
             "cancel_operation", {}),
        ]
        for pattern, kind, args in patterns:
            if re.match(pattern, low):
                requires_conf = kind in ("inspect_screen", "point_at_screen")
                intent = ActionIntent(
                    str(uuid.uuid4()), source, kind, args,
                    f"Recognized direct command: {kind}", requires_conf
                )
                return RouteResult("direct", "I understood the command. I'll ask before taking action.", intent)

        # Keep normal conversation out of the action pipeline.
        action_verbs = ("open ", "launch ", "click ", "type ", "write ", "delete ",
                        "install ", "send ", "submit ", "buy ", "move ", "rename ")
        if not low.startswith(action_verbs) and not any(x in low for x in (
            "on my screen", "screen pointing", "point at screen", "walk me through",
            "show me how to", "repeat these steps"
        )):
            return RouteResult("conversation", "Treat this as conversation, not a computer action.")

        if self.ai_planner:
            try:
                plan = self.ai_planner(text)
                if not isinstance(plan, list):
                    raise TypeError("Planner must return a list of action dictionaries.")
                for step in plan:
                    valid, reason = validate_proposed_action(step)
                    if not valid:
                        return RouteResult("unknown", f"Rejected unsafe or invalid proposed action: {reason}")
                # Planner output is a proposal only. It must never be executed here.
                return RouteResult("planner", "I prepared a proposed plan. Review and approve it before execution.",
                                   plan=plan)
            except Exception as exc:
                return RouteResult("unknown", f"I couldn't prepare a safe action plan: {exc}")

        return RouteResult("unknown", "This needs a planner, but no AI planner is connected yet.")


class ApprovedActionBroker:
    """Executes only registered actions after schema validation, permission check, and per-action approval callback."""

    def __init__(
        self,
        approval: Callable[[dict[str, Any]], bool],
        permission_checker: Optional[Callable[[str, str], bool]] = None,
        verifier: Optional[Callable[[str, dict[str, Any], Any], bool]] = None,
    ):
        self.approval = approval
        self.permission_checker = permission_checker
        self.verifier = verifier
        self.handlers: dict[str, Callable[..., Any]] = {}

    def register(self, action_name: str, handler: Callable[..., Any]) -> None:
        if not action_name or not callable(handler):
            raise ValueError("A valid action name and callable handler are required.")
        self.handlers[action_name] = handler

    def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(action, dict):
            return {"ok": False, "verified": False, "error": "Proposed action must be a dictionary."}
        name = str(action.get("action", "")).strip()
        args = action.get("arguments", {})
        if name not in self.handlers:
            return {"ok": False, "verified": False, "error": f"Action is not registered: {name}"}
        if not isinstance(args, dict):
            return {"ok": False, "verified": False, "error": "Action arguments must be an object."}
        valid, reason = validate_proposed_action(action)
        if not valid:
            return {"ok": False, "verified": False, "error": reason}
        if not self.approval(action):
            return {"ok": False, "verified": False, "denied": True, "message": "User declined this action."}
        if self.permission_checker and name in ACTION_SCHEMAS:
            perm_cat = ACTION_SCHEMAS[name].get("permission")
            if perm_cat:
                desc = str(action.get("description") or f"Execute {name}")
                if not self.permission_checker(perm_cat, desc):
                    return {
                        "ok": False,
                        "verified": False,
                        "denied": True,
                        "permission_denied": True,
                        "message": f"Permission '{perm_cat}' was denied.",
                    }
        try:
            result = self.handlers[name](**args)
            if self.verifier:
                verified = bool(self.verifier(name, args, result))
            elif isinstance(result, dict) and "verified" in result:
                verified = bool(result.get("ok")) and bool(result.get("verified"))
            elif isinstance(result, dict) and "ok" in result:
                verified = bool(result.get("ok"))
            else:
                verified = True
            return {"ok": True, "verified": verified, "result": result}
        except Exception as exc:
            return {"ok": False, "verified": False, "error": str(exc)}


# Spoken vocabulary list for local Vosk recognition when offline or after 'Hey Nova'
OFFLINE_VOICE_PHRASES: list[str] = [
    "hey nova", "nova", "hey",
    # Nova self-control & floating companion bot
    "close yourself", "close nova", "exit nova", "quit nova", "shut down nova", "goodbye nova",
    "minimize nova", "hide nova",
    "show companion", "show floating bot", "show bot", "turn on bot", "enable bot", "enable floating bot",
    "hide companion", "hide floating bot", "hide bot", "turn off bot", "disable bot", "disable floating bot",
    "toggle companion", "toggle floating bot", "toggle bot",
    # Offline permission management by voice
    "give permission for hand gesture", "allow hand gesture", "allow hand control", "allow gesture",
    "give permission for camera", "allow camera", "allow screen", "allow screen viewing",
    "give permission for mouse", "allow mouse", "allow mouse and keyboard", "allow keyboard",
    "allow documents", "allow media", "allow files", "allow file modification",
    "allow all permissions", "give all permissions", "grant all permissions",
    "revoke all permissions", "remove all permissions", "clear permissions",
    "revoke hand gesture", "revoke camera", "revoke mouse", "revoke screen",
    "check permissions", "permission status", "show permissions",
    # Hand gesture mouse control
    "start hand gesture", "enable hand gesture", "turn on hand gesture", "turn on hand control", "start hand mouse",
    "stop hand gesture", "disable hand gesture", "turn off hand gesture", "turn off hand control", "stop hand mouse",
    "pause hand gesture", "resume hand gesture", "hand gesture status",
    "show hand preview", "hide hand preview", "show camera preview", "hide camera preview",
    # Microphone sensitivity & auto-nearest
    "enable auto nearest", "turn on auto nearest", "calibrate mic", "manual mic",
    "mic sensitivity up", "mic sensitivity down", "check mic sensitivity",
    # Apps & folders
    "open calculator", "open notepad", "open file explorer", "open explorer",
    "open downloads", "open documents", "open desktop", "open pictures", "open music", "open videos", "open workspace",
    "list downloads", "list documents", "list desktop", "list workspace",
    "open paint", "open task manager", "open settings", "open windows settings",
    "open chrome", "open google chrome", "open edge", "open microsoft edge", "open vs code", "open wps office",
    "close calculator", "close notepad", "close file explorer", "close explorer", "close chrome", "close edge", "close window",
    # Windows, browser tabs & scrolling
    "minimize window", "maximize window", "restore window", "switch window", "show desktop",
    "new tab", "close tab", "reopen tab", "next tab", "previous tab",
    "scroll up", "scroll down",
    # Clipboard & common hotkeys
    "read clipboard", "clear clipboard", "clipboard history", "copy", "paste", "undo", "redo", "select all", "save",
    # Volume & brightness
    "mute", "mute volume", "unmute", "unmute volume", "volume up", "increase volume", "louder",
    "volume down", "decrease volume", "quieter", "lower volume", "check volume", "what is the volume",
    "set volume to ten", "set volume to twenty", "set volume to thirty", "set volume to forty",
    "set volume to fifty", "set volume to sixty", "set volume to seventy", "set volume to eighty",
    "set volume to ninety", "set volume to one hundred",
    "brightness up", "increase brightness", "brighter", "brightness down", "decrease brightness", "dimmer",
    "check brightness", "what is the brightness",
    # Screenshots, system info, time, tasks & memories
    "take screenshot", "take a screenshot", "capture screenshot", "screenshot",
    "system status", "check system status", "battery status", "cpu usage", "memory usage",
    "what time is it", "what is the time", "current time", "what is today's date", "date", "time",
    "list tasks", "show tasks", "my tasks", "list memories", "show memories",
    "offline capabilities", "offline status", "check offline capabilities", "list indexed folders", "stop task",
    "cancel task", "offline cpu mode", "offline gpu mode",
    "look at screen", "what is on my screen", "analyze screen", "inspect screen",
    "inspect document", "read document", "summarize document", "summarize pdf", "explain pdf",
    "reindex local knowledge",
    "help", "offline help",
    "[unk]",
]

_WORD_TO_NUM = {
    "zero": 0, "ten": 10, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "one hundred": 100,
}


def match_offline_command(user_text: str) -> dict[str, Any] | None:
    """Deterministic local intent matcher for common system commands when offline or Gemini Live is unavailable.
    Returns a schema-valid action dict {'action': ..., 'arguments': ..., 'description': ...} or None."""
    clean = " ".join((user_text or "").strip().lower().split())
    if not clean:
        return None

    # Strip optional leading wake phrase ("hey nova ...", "nova ...", "please ...")
    clean = re.sub(r"^\s*(?:hey\s+nova|nova|please|can\s+you|could\s+you)[\s,:-]*", "", clean).strip()
    clean = re.sub(r"^\s*(?:please|can\s+you|could\s+you)[\s,:-]*", "", clean).strip()
    if not clean:
        return None

    # Convert spoken number words ("fifty") to digits ("50") for volume/brightness/mic
    for w_num, d_num in sorted(_WORD_TO_NUM.items(), key=lambda kv: -len(kv[0])):
        clean = re.sub(rf"\b{re.escape(w_num)}\b", str(d_num), clean)

    # 1. Permission management offline (grant, revoke, status — works for hand gesture, camera, mouse, screen, files, all)
    if any(p in clean for p in ("check permission", "permission status", "show permission", "what permissions", "list permissions")):
        return {"action": "manage_permission", "arguments": {"action": "status"}, "description": "Check current permission status"}
    m_revoke = re.match(r"^(?:revoke|remove|clear|disable|deny)\s+(?:the\s+)?(?:permission(?:s)?\s+(?:for|to)\s+)?(.+?)(?:\s+permission(?:s)?)?$", clean)
    if m_revoke and any(w in clean for w in ("permission", "permissions", "revoke")):
        cat = m_revoke.group(1).strip() or "all"
        if cat in ("permission", "permissions"):
            cat = "all"
        return {"action": "manage_permission", "arguments": {"action": "revoke", "category": cat}, "description": f"Revoke permission for {cat}"}
    m_grant = re.match(
        r"^(?:give|grant|allow|enable)\s+(?:me\s+|nova\s+)?(?:permission(?:s)?\s+(?:for|to)\s+)?(.+?)(?:\s+permission(?:s)?)?$",
        clean,
    )
    if m_grant:
        target_cat = m_grant.group(1).strip()
        is_perm_phrase = (
            "permission" in clean
            or clean.startswith(("allow ", "grant "))
        )
        # Avoid stealing "enable hand gesture" / "enable floating bot" / "enable auto nearest"
        if is_perm_phrase and target_cat and not any(
            x in target_cat for x in ("bot", "companion", "auto nearest", "nearest")
        ):
            if target_cat in ("permission", "permissions", "everything"):
                target_cat = "all"
            return {
                "action": "manage_permission",
                "arguments": {"action": "grant", "category": target_cat},
                "description": f"Grant permission for {target_cat}",
            }

    # 2. Floating companion & Nova self-control (closing itself, minimizing, showing/hiding/toggling bot)
    if any(p in clean for p in (
        "show companion", "show floating bot", "show bot", "show the bot",
        "enable floating bot", "turn on floating bot", "turn on bot", "enable bot", "bring back bot",
    )):
        return {"action": "nova_control", "arguments": {"action": "show_floating_bot"}, "description": "Show floating companion bot"}
    if any(p in clean for p in (
        "hide companion", "hide floating bot", "hide bot", "hide the bot",
        "disable floating bot", "turn off floating bot", "turn off bot", "disable bot",
    )):
        return {"action": "nova_control", "arguments": {"action": "hide_floating_bot"}, "description": "Hide floating companion bot"}
    if any(p in clean for p in ("toggle companion", "toggle floating bot", "toggle bot")):
        return {"action": "nova_control", "arguments": {"action": "toggle_bot"}, "description": "Toggle floating companion bot"}
    if any(p in clean for p in ("minimize nova", "hide nova", "minimize yourself", "go to tray")):
        return {"action": "nova_control", "arguments": {"action": "minimize"}, "description": "Minimize Nova to system tray"}
    if any(p in clean for p in (
        "close nova", "exit nova", "quit nova", "shut down nova", "shutdown nova",
        "close yourself", "exit yourself", "quit yourself", "shut yourself down",
        "close glass nova", "exit glass nova", "goodbye nova", "bye nova",
    )):
        return {"action": "nova_control", "arguments": {"action": "close"}, "description": "Close Nova"}

    # 3. Hand gesture mouse control (start, stop, pause, resume, preview, status)
    if any(p in clean for p in ("show hand preview", "show camera preview", "show gesture preview")):
        return {"action": "hand_gesture_control", "arguments": {"action": "show_preview"}, "description": "Show hand gesture camera preview"}
    if any(p in clean for p in ("hide hand preview", "hide camera preview", "hide gesture preview")):
        return {"action": "hand_gesture_control", "arguments": {"action": "hide_preview"}, "description": "Hide hand gesture camera preview"}
    if any(p in clean for p in ("pause hand gesture", "pause gesture", "pause hand control")):
        return {"action": "hand_gesture_control", "arguments": {"action": "pause"}, "description": "Pause hand gesture mouse control"}
    if any(p in clean for p in ("resume hand gesture", "resume gesture", "resume hand control")):
        return {"action": "hand_gesture_control", "arguments": {"action": "resume"}, "description": "Resume hand gesture mouse control"}
    if any(p in clean for p in ("hand gesture status", "gesture status", "is hand gesture running")):
        return {"action": "hand_gesture_control", "arguments": {"action": "status"}, "description": "Check hand gesture status"}
    if any(p in clean for p in (
        "start hand gesture", "enable hand gesture", "turn on hand gesture", "hand gesture on",
        "start gesture", "turn on hand control", "enable hand control", "start hand mouse",
    )):
        return {"action": "hand_gesture_control", "arguments": {"action": "start"}, "description": "Start hand gesture mouse control"}
    if any(p in clean for p in (
        "stop hand gesture", "disable hand gesture", "turn off hand gesture", "hand gesture off",
        "stop gesture", "turn off hand control", "disable hand control", "stop hand mouse",
    )):
        return {"action": "hand_gesture_control", "arguments": {"action": "stop"}, "description": "Stop hand gesture mouse control"}

    # 4. Microphone sensitivity & AUTO NEAREST controls
    if any(p in clean for p in ("enable auto nearest", "turn on auto nearest", "auto nearest on", "calibrate mic", "calibrate microphone")):
        return {"action": "mic_sensitivity_control", "arguments": {"action": "auto_nearest"}, "description": "Enable AUTO NEAREST microphone mode"}
    if any(p in clean for p in ("disable auto nearest", "turn off auto nearest", "manual mic", "manual microphone")):
        return {"action": "mic_sensitivity_control", "arguments": {"action": "manual"}, "description": "Switch microphone to manual sensitivity"}
    if any(p in clean for p in ("check mic sensitivity", "what is mic sensitivity", "mic sensitivity status")):
        return {"action": "mic_sensitivity_control", "arguments": {"action": "get"}, "description": "Check microphone sensitivity"}
    m_mic = re.search(r"mic(?:rophone)?\s+sensitivity\s+(?:to\s+|at\s+)?(\d{1,3})\s*%?", clean)
    if m_mic:
        lvl = max(0, min(100, int(m_mic.group(1))))
        return {"action": "mic_sensitivity_control", "arguments": {"action": "set", "value": lvl}, "description": f"Set microphone sensitivity to {lvl}%"}
    if any(p in clean for p in ("mic sensitivity up", "increase mic sensitivity", "raise mic sensitivity")):
        return {"action": "mic_sensitivity_control", "arguments": {"action": "increase", "amount": 10}, "description": "Increase microphone sensitivity"}
    if any(p in clean for p in ("mic sensitivity down", "decrease mic sensitivity", "lower mic sensitivity")):
        return {"action": "mic_sensitivity_control", "arguments": {"action": "decrease", "amount": 10}, "description": "Decrease microphone sensitivity"}

    # 5. Write text in Notepad ("write hello world in notepad")
    m_np = re.match(r"^(?:write|type)\s+(.+?)\s+(?:in|into)\s+notepad$", clean)
    if m_np and m_np.group(1).strip():
        txt = m_np.group(1).strip()
        return {
            "action": "write_in_notepad",
            "arguments": {"text": txt, "open_if_closed": True},
            "description": f"Write '{txt}' in Notepad",
        }

    # 6. Clipboard & common keyboard / browser tab / scroll controls
    if any(p in clean for p in ("read clipboard", "what is on my clipboard", "what's on my clipboard", "show clipboard")):
        return {"action": "clipboard_control", "arguments": {"action": "read"}, "description": "Read Windows clipboard"}
    if any(p in clean for p in ("clear clipboard", "empty clipboard")):
        return {"action": "clipboard_control", "arguments": {"action": "clear"}, "description": "Clear Windows clipboard"}
    if any(p in clean for p in ("clipboard history", "open clipboard history")):
        return {"action": "clipboard_control", "arguments": {"action": "history"}, "description": "Open Windows Clipboard History"}
    if clean in ("copy", "copy that", "copy selected", "copy selection"):
        return {"action": "clipboard_control", "arguments": {"action": "copy"}, "description": "Copy selected text"}
    if clean in ("paste", "paste that", "paste clipboard"):
        return {"action": "clipboard_control", "arguments": {"action": "paste"}, "description": "Paste clipboard"}
    if clean in ("undo", "undo that"):
        return {"action": "press_keyboard_hotkey", "arguments": {"keys": "ctrl+z"}, "description": "Undo (Ctrl+Z)"}
    if clean in ("redo", "redo that"):
        return {"action": "press_keyboard_hotkey", "arguments": {"keys": "ctrl+y"}, "description": "Redo (Ctrl+Y)"}
    if clean in ("select all",):
        return {"action": "press_keyboard_hotkey", "arguments": {"keys": "ctrl+a"}, "description": "Select all (Ctrl+A)"}
    if clean in ("save", "save file"):
        return {"action": "press_keyboard_hotkey", "arguments": {"keys": "ctrl+s"}, "description": "Save (Ctrl+S)"}
    if clean in ("new tab", "open new tab"):
        return {"action": "window_control", "arguments": {"action": "new_tab"}, "description": "Open a new browser tab"}
    if clean in ("close tab", "close this tab", "close current tab"):
        return {"action": "window_control", "arguments": {"action": "close_tab"}, "description": "Close current browser tab"}
    if clean in ("reopen tab", "restore tab", "reopen closed tab"):
        return {"action": "window_control", "arguments": {"action": "reopen_tab"}, "description": "Reopen last closed browser tab"}
    if clean in ("next tab", "switch tab"):
        return {"action": "window_control", "arguments": {"action": "next_tab"}, "description": "Switch to next browser tab"}
    if clean in ("previous tab", "prev tab"):
        return {"action": "window_control", "arguments": {"action": "prev_tab"}, "description": "Switch to previous browser tab"}
    if clean in ("scroll down", "page down"):
        return {"action": "scroll_desktop", "arguments": {"direction": "down", "amount": 5}, "description": "Scroll down"}
    if clean in ("scroll up", "page up"):
        return {"action": "scroll_desktop", "arguments": {"direction": "up", "amount": 5}, "description": "Scroll up"}

    # 7. Close specific application ("close calculator", "close notepad", "close chrome", etc.)
    for app_key, close_phrases in (
        ("calculator", ("close calculator", "close calc", "quit calculator", "exit calculator")),
        ("notepad", ("close notepad", "quit notepad", "exit notepad")),
        ("file explorer", ("close file explorer", "close explorer", "close folder", "close folders")),
        ("paint", ("close paint", "quit paint")),
        ("task manager", ("close task manager",)),
        ("chrome", ("close chrome", "close google chrome")),
        ("edge", ("close edge", "close microsoft edge")),
        ("vscode", ("close vs code", "close vscode", "close visual studio code")),
    ):
        if any(p in clean for p in close_phrases):
            return {
                "action": "app_control",
                "arguments": {"action": "close", "app": app_key},
                "description": f"Close {app_key}",
            }

    # 8. Window controls (minimize, maximize, restore, close active window, switch window, show desktop)
    if any(p in clean for p in ("minimize window", "minimize current window", "minimize active window", "minimize this window")):
        return {"action": "window_control", "arguments": {"action": "minimize"}, "description": "Minimize active window"}
    if any(p in clean for p in ("maximize window", "maximize current window", "maximize active window", "maximize this window")):
        return {"action": "window_control", "arguments": {"action": "maximize"}, "description": "Maximize active window"}
    if any(p in clean for p in ("restore window", "unmaximize window")):
        return {"action": "window_control", "arguments": {"action": "restore"}, "description": "Restore active window"}
    if any(p in clean for p in ("close window", "close current window", "close active window", "close this window")):
        return {"action": "window_control", "arguments": {"action": "close"}, "description": "Close active window"}
    if any(p in clean for p in ("switch window", "next window", "alt tab")):
        return {"action": "window_control", "arguments": {"action": "switch"}, "description": "Switch active window"}
    if any(p in clean for p in ("show desktop", "minimize all windows", "go to desktop")):
        return {"action": "window_control", "arguments": {"action": "show_desktop"}, "description": "Show desktop"}

    # 9. List or open common folders & create folder offline
    m_mkdir = re.match(r"^(?:create|make|new)\s+folder\s+(.+)$", clean)
    if m_mkdir and m_mkdir.group(1).strip():
        folder_spec = m_mkdir.group(1).strip()
        m_on = re.match(r"^(.+?)\s+(?:on|in)\s+(desktop|downloads|documents|pictures|music|videos|workspace)$", folder_spec)
        target_path = f"{m_on.group(2)}\\{m_on.group(1).strip()}" if m_on else folder_spec
        return {"action": "create_folder", "arguments": {"path": target_path}, "description": f"Create folder '{target_path}'"}
    for folder_key in ("downloads", "documents", "desktop", "pictures", "music", "videos", "workspace"):
        if clean in (f"list {folder_key}", f"list {folder_key} folder", f"what is in {folder_key}"):
            return {
                "action": "list_folder",
                "arguments": {"path": folder_key},
                "description": f"List files in {folder_key}",
            }
        if (
            f"open {folder_key}" in clean
            or f"show {folder_key}" in clean
            or f"go to {folder_key}" in clean
            or clean in (folder_key, f"{folder_key} folder")
        ):
            return {
                "action": "open_folder",
                "arguments": {"path": folder_key},
                "description": f"Open {folder_key} folder (offline local command)",
            }

    # 10. Open common apps (uses required argument 'app' matching ACTION_SCHEMAS['open_app']!)
    for app_key, aliases in (
        ("calculator", ("open calculator", "launch calculator", "start calculator", "open calc", "calculator", "calc")),
        ("notepad", ("open notepad", "launch notepad", "start notepad", "notepad")),
        ("file explorer", ("open file explorer", "open explorer", "launch explorer", "file explorer", "file manager", "open files", "open this pc", "this pc", "my computer")),
        ("paint", ("open paint", "launch paint", "start paint", "mspaint")),
        ("settings", ("open windows settings", "open system settings", "open settings", "windows settings", "system settings")),
        ("task manager", ("open task manager", "launch task manager", "task manager", "taskmgr")),
        ("chrome", ("open chrome", "open google chrome", "launch chrome", "start chrome")),
        ("edge", ("open edge", "open microsoft edge", "launch edge", "start edge")),
        ("vs code", ("open vs code", "open vscode", "open visual studio code", "launch vs code")),
        ("wps office", ("open wps", "open wps office", "launch wps office")),
    ):
        if clean in aliases or any(a in clean for a in aliases if a.startswith(("open ", "launch ", "start "))):
            return {
                "action": "open_app",
                "arguments": {"app": app_key},
                "description": f"Open {app_key} (offline local command)",
            }

    # 11. Volume controls (uses 'value' matching ACTION_SCHEMAS['volume_control']!)
    if any(p in clean for p in ("mute volume", "mute audio", "mute sound", "mute speaker")) or clean == "mute":
        return {"action": "volume_control", "arguments": {"action": "mute"}, "description": "Mute system audio"}
    if any(p in clean for p in ("unmute volume", "unmute audio", "unmute sound", "unmute speaker")) or clean == "unmute":
        return {"action": "volume_control", "arguments": {"action": "unmute"}, "description": "Unmute system audio"}
    if any(p in clean for p in ("check volume", "what is the volume", "current volume", "get volume")):
        return {"action": "volume_control", "arguments": {"action": "get"}, "description": "Check current system volume"}
    m_vol = re.search(r"volume\s+(?:to\s+|at\s+)?(\d{1,3})\s*%?", clean)
    if m_vol:
        lvl = max(0, min(100, int(m_vol.group(1))))
        return {"action": "volume_control", "arguments": {"action": "set", "value": lvl}, "description": f"Set volume to {lvl}%"}
    if any(p in clean for p in ("volume up", "increase volume", "turn up volume", "raise volume", "louder")):
        return {"action": "volume_control", "arguments": {"action": "up", "amount": 10}, "description": "Increase volume"}
    if any(p in clean for p in ("volume down", "decrease volume", "turn down volume", "lower volume", "quieter")):
        return {"action": "volume_control", "arguments": {"action": "down", "amount": 10}, "description": "Decrease volume"}

    # 12. Brightness controls (uses 'value' matching ACTION_SCHEMAS['brightness_control']!)
    if any(p in clean for p in ("check brightness", "what is the brightness", "current brightness", "get brightness")):
        return {"action": "brightness_control", "arguments": {"action": "get"}, "description": "Check current screen brightness"}
    m_bri = re.search(r"brightness\s+(?:to\s+|at\s+)?(\d{1,3})\s*%?", clean)
    if m_bri:
        lvl = max(0, min(100, int(m_bri.group(1))))
        return {"action": "brightness_control", "arguments": {"action": "set", "value": lvl}, "description": f"Set brightness to {lvl}%"}
    if any(p in clean for p in ("brightness up", "increase brightness", "raise brightness", "brighter")):
        return {"action": "brightness_control", "arguments": {"action": "up", "amount": 10}, "description": "Increase brightness"}
    if any(p in clean for p in ("brightness down", "decrease brightness", "lower brightness", "dimmer", "darker")):
        return {"action": "brightness_control", "arguments": {"action": "down", "amount": 10}, "description": "Decrease brightness"}

    # 13. Screenshot, screen/document understanding, system status, offline capabilities & local knowledge/file search
    if any(p in clean for p in ("look at screen", "look at my screen", "what is on my screen", "what's on my screen", "analyze screen", "inspect screen", "describe my screen")):
        return {"action": "look_at_screen", "arguments": {"query": "Explain what is on the screen and identify key UI elements or text."}, "description": "Inspect active screen"}
    if clean in ("inspect document", "read document", "summarize document", "summarize pdf", "explain pdf", "read pdf", "explain document"):
        return {"action": "inspect_document", "arguments": {"query": "Summarize this document and list its key sections."}, "description": "Inspect open or recent document"}
    m_doc = re.match(r"^(?:inspect|read|summarize|explain)\s+(?:the\s+)?(?:document|pdf|file)\s+(.+)$", clean)
    if m_doc and m_doc.group(1).strip():
        d_path = m_doc.group(1).strip()
        return {"action": "inspect_document", "arguments": {"path": d_path, "query": f"Summarize and explain {d_path}."}, "description": f"Inspect document '{d_path}'"}
    if any(p in clean for p in ("offline capabilities", "offline status", "check offline capabilities", "what models are installed", "list local models")):
        return {"action": "offline_capabilities", "arguments": {}, "description": "Inspect offline AI models, hardware, and local capabilities"}
    if any(p in clean for p in ("list indexed folders", "show indexed folders", "local knowledge status")):
        return {"action": "local_knowledge_control", "arguments": {"action": "list"}, "description": "List user-indexed local knowledge folders"}
    if any(p in clean for p in ("reindex local knowledge", "refresh local knowledge", "reindex folders")):
        return {"action": "local_knowledge_control", "arguments": {"action": "reindex"}, "description": "Re-index user-selected local knowledge folders"}
    m_idx = re.match(r"^(?:index|add)\s+folder\s+(.+)$", clean)
    if m_idx and m_idx.group(1).strip():
        return {"action": "local_knowledge_control", "arguments": {"action": "add_folder", "path": m_idx.group(1).strip()}, "description": f"Index local folder '{m_idx.group(1).strip()}'"}
    m_unidx = re.match(r"^(?:remove|unindex)\s+(?:indexed\s+)?folder\s+(.+)$", clean)
    if m_unidx and m_unidx.group(1).strip():
        return {"action": "local_knowledge_control", "arguments": {"action": "remove_folder", "path": m_unidx.group(1).strip()}, "description": f"Remove folder '{m_unidx.group(1).strip()}' from local knowledge"}
    m_ksearch = re.match(r"^(?:search\s+local\s+knowledge|search\s+knowledge|search\s+docs)\s+(?:for\s+)?(.+)$", clean)
    if m_ksearch and m_ksearch.group(1).strip():
        return {"action": "local_knowledge_control", "arguments": {"action": "search", "query": m_ksearch.group(1).strip()}, "description": f"Search local knowledge for '{m_ksearch.group(1).strip()}'"}
    m_fsearch = re.match(r"^(?:search|find)\s+(?:local\s+)?files?\s+(?:for\s+|named\s+)?(.+?)(?:\s+in\s+(downloads|documents|desktop|pictures|music|videos|workspace))?$", clean)
    if m_fsearch and m_fsearch.group(1).strip():
        f_args: dict[str, Any] = {"query": m_fsearch.group(1).strip()}
        if m_fsearch.group(2):
            f_args["folder"] = m_fsearch.group(2)
        return {"action": "search_local_files", "arguments": f_args, "description": f"Search local files for '{f_args['query']}'"}
    if any(p in clean for p in ("take screenshot", "take a screenshot", "capture screenshot", "capture screen")) or clean == "screenshot":
        return {"action": "take_screenshot", "arguments": {}, "description": "Take a desktop screenshot"}
    if any(p in clean for p in ("system status", "battery status", "battery level", "cpu usage", "ram usage", "memory usage", "disk space", "check system status", "system info")) or clean in ("status", "battery"):
        return {"action": "system_status", "arguments": {}, "description": "Check system CPU, RAM, disk, and battery status"}

    # 14. Time, date, local tasks & local memories (create/list tasks, remember/recall/forget/list memories)
    if any(p in clean for p in ("what time is it", "what is the time", "current time", "tell me the time", "what is today's date", "today's date", "current date", "what day is it")) or clean in ("time", "date"):
        return {"action": "task_control", "arguments": {"action": "now"}, "description": "Get current local date and time"}
    if any(p in clean for p in ("list tasks", "show tasks", "my tasks", "show my tasks", "show reminders", "list reminders")):
        return {"action": "task_control", "arguments": {"action": "list"}, "description": "List saved local tasks and reminders"}
    m_task = re.match(r"^(?:add\s+task|create\s+task|new\s+task|remind\s+me\s+to)\s+(.+)$", clean)
    if m_task and m_task.group(1).strip():
        t_title = m_task.group(1).strip()
        return {"action": "task_control", "arguments": {"action": "create", "title": t_title}, "description": f"Add local task '{t_title}'"}
    if any(p in clean for p in ("list memories", "show memories", "my memories", "what do you remember")):
        return {"action": "memory_control", "arguments": {"action": "list"}, "description": "List saved local memories"}
    m_rem = re.match(r"^remember\s+(?:that\s+)?(?:my\s+)?(.+?)\s+(?:is|are)\s+(.+)$", clean)
    if m_rem and m_rem.group(1).strip() and m_rem.group(2).strip():
        k, v = m_rem.group(1).strip(), m_rem.group(2).strip()
        return {"action": "memory_control", "arguments": {"action": "remember", "key": k, "value": v}, "description": f"Save memory '{k}'"}
    m_rec = re.match(r"^(?:recall|get\s+memory|what\s+is\s+my)\s+(.+?)\??$", clean)
    if m_rec and m_rec.group(1).strip():
        k = m_rec.group(1).strip()
        return {"action": "memory_control", "arguments": {"action": "get", "key": k}, "description": f"Recall memory '{k}'"}
    m_fgt = re.match(r"^forget\s+(?:my\s+)?(.+)$", clean)
    if m_fgt and m_fgt.group(1).strip():
        k = m_fgt.group(1).strip()
        return {"action": "memory_control", "arguments": {"action": "forget", "key": k}, "description": f"Forget memory '{k}'"}

    # 15. Calculator math expressions ("calculate 25 * 4", "what is 120 + 350")
    m_calc = re.match(r"^(?:calculate|compute|what\s+is|solve)\s+([\d\s\.\+\-\*\/\(\)%^]+)$", clean)
    if m_calc and any(op in m_calc.group(1) for op in ("+", "-", "*", "/", "%", "^")):
        expr = m_calc.group(1).strip().replace("^", "**")
        return {"action": "calculator_operation", "arguments": {"expression": expr}, "description": f"Calculate {expr}"}

    return None
