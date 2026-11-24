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
}

DANGEROUS_PATTERNS = (
    "cmd.exe", "powershell", "pwsh", "wscript", "cscript", "reg.exe", "regedit",
    "format ", "del /", "rm -rf", "shutdown /", "taskkill /f /im explorer",
)


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
    serialized = str(args).lower()
    for pat in DANGEROUS_PATTERNS:
        if pat in serialized:
            return False, f"Rejected dangerous argument pattern '{pat}' in action '{name}'."
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
        name = str(action.get("action", ""))
        args = action.get("arguments", {})
        if name not in self.handlers:
            return {"ok": False, "verified": False, "error": f"Action is not registered: {name}"}
        if not isinstance(args, dict):
            return {"ok": False, "verified": False, "error": "Action arguments must be an object."}
        if name in ACTION_SCHEMAS:
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
    "open calculator", "open notepad", "open file explorer", "open explorer",
    "open downloads", "open documents", "open desktop", "open pictures", "open music", "open videos",
    "open paint", "open task manager", "open settings", "open windows settings",
    "open chrome", "open google chrome", "open edge", "open microsoft edge", "open vs code",
    "close calculator", "close notepad", "close file explorer", "close explorer", "close chrome", "close edge", "close window",
    "minimize window", "maximize window", "restore window", "switch window", "show desktop",
    "mute", "mute volume", "unmute", "unmute volume", "volume up", "increase volume", "louder",
    "volume down", "decrease volume", "quieter", "lower volume", "check volume", "what is the volume",
    "set volume to ten", "set volume to twenty", "set volume to thirty", "set volume to forty",
    "set volume to fifty", "set volume to sixty", "set volume to seventy", "set volume to eighty",
    "set volume to ninety", "set volume to one hundred",
    "brightness up", "increase brightness", "brighter", "brightness down", "decrease brightness", "dimmer",
    "take screenshot", "take a screenshot", "capture screenshot", "screenshot",
    "system status", "check system status", "battery status", "cpu usage", "memory usage",
    "what time is it", "what is the time", "current time", "what is today's date", "date", "time",
    "list tasks", "show tasks", "my tasks", "list memories", "show memories",
    "start hand gesture", "enable hand gesture", "turn on hand gesture",
    "stop hand gesture", "disable hand gesture", "turn off hand gesture",
    "show companion", "show floating bot", "show bot", "hide companion", "hide floating bot", "hide bot",
    "minimize nova", "close nova",
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

    # Convert spoken number words ("fifty") to digits ("50") for volume/brightness
    for w_num, d_num in sorted(_WORD_TO_NUM.items(), key=lambda kv: -len(kv[0])):
        clean = re.sub(rf"\b{re.escape(w_num)}\b", str(d_num), clean)

    # 1. Floating companion & Nova window control
    if any(p in clean for p in ("show companion", "show floating bot", "show bot", "enable floating bot", "turn on floating bot")):
        return {"action": "nova_control", "arguments": {"action": "show_floating_bot"}, "description": "Show floating companion bot"}
    if any(p in clean for p in ("hide companion", "hide floating bot", "hide bot", "disable floating bot", "turn off floating bot")):
        return {"action": "nova_control", "arguments": {"action": "hide_floating_bot"}, "description": "Hide floating companion bot"}
    if any(p in clean for p in ("toggle companion", "toggle floating bot", "toggle bot")):
        return {"action": "nova_control", "arguments": {"action": "toggle_bot"}, "description": "Toggle floating companion bot"}
    if clean in ("minimize nova", "hide nova"):
        return {"action": "nova_control", "arguments": {"action": "minimize"}, "description": "Minimize Nova to system tray"}
    if clean in ("close nova", "exit nova", "quit nova"):
        return {"action": "nova_control", "arguments": {"action": "close"}, "description": "Close Nova"}

    # 2. Hand gesture mouse control
    if any(p in clean for p in ("start hand gesture", "enable hand gesture", "turn on hand gesture", "hand gesture on", "start gesture")):
        return {"action": "hand_gesture_control", "arguments": {"action": "start"}, "description": "Start hand gesture mouse control"}
    if any(p in clean for p in ("stop hand gesture", "disable hand gesture", "turn off hand gesture", "hand gesture off", "stop gesture")):
        return {"action": "hand_gesture_control", "arguments": {"action": "stop"}, "description": "Stop hand gesture mouse control"}

    # 3. Write text in Notepad ("write hello world in notepad")
    m_np = re.match(r"^(?:write|type)\s+(.+?)\s+(?:in|into)\s+notepad$", clean)
    if m_np and m_np.group(1).strip():
        txt = m_np.group(1).strip()
        return {
            "action": "write_in_notepad",
            "arguments": {"text": txt, "open_if_closed": True},
            "description": f"Write '{txt}' in Notepad",
        }

    # 4. Close specific application ("close calculator", "close notepad", "close chrome", etc.)
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

    # 5. Window controls (minimize, maximize, restore, close active window, switch window, show desktop)
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

    # 6. Open common folders
    for folder_key in ("downloads", "documents", "desktop", "pictures", "music", "videos"):
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

    # 7. Open common apps (uses required argument 'app' matching ACTION_SCHEMAS['open_app']!)
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

    # 8. Volume controls (uses 'value' matching ACTION_SCHEMAS['volume_control']!)
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

    # 9. Brightness controls (uses 'value' matching ACTION_SCHEMAS['brightness_control']!)
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

    # 10. Screenshot & system status
    if any(p in clean for p in ("take screenshot", "take a screenshot", "capture screenshot", "capture screen")) or clean == "screenshot":
        return {"action": "take_screenshot", "arguments": {}, "description": "Take a desktop screenshot"}
    if any(p in clean for p in ("system status", "battery status", "battery level", "cpu usage", "ram usage", "memory usage", "disk space", "check system status", "system info")) or clean in ("status", "battery"):
        return {"action": "system_status", "arguments": {}, "description": "Check system CPU, RAM, disk, and battery status"}

    # 11. Time, date, local tasks & local memories
    if any(p in clean for p in ("what time is it", "what is the time", "current time", "tell me the time", "what is today's date", "today's date", "current date", "what day is it")) or clean in ("time", "date"):
        return {"action": "task_control", "arguments": {"action": "now"}, "description": "Get current local date and time"}
    if any(p in clean for p in ("list tasks", "show tasks", "my tasks", "show my tasks", "show reminders", "list reminders")):
        return {"action": "task_control", "arguments": {"action": "list"}, "description": "List saved local tasks and reminders"}
    if any(p in clean for p in ("list memories", "show memories", "my memories", "what do you remember")):
        return {"action": "memory_control", "arguments": {"action": "list"}, "description": "List saved local memories"}

    # 12. Calculator math expressions ("calculate 25 * 4", "what is 120 + 350")
    m_calc = re.match(r"^(?:calculate|compute|what\s+is|solve)\s+([\d\s\.\+\-\*\/\(\)%^]+)$", clean)
    if m_calc and any(op in m_calc.group(1) for op in ("+", "-", "*", "/", "%", "^")):
        expr = m_calc.group(1).strip().replace("^", "**")
        return {"action": "calculator_operation", "arguments": {"expression": expr}, "description": f"Calculate {expr}"}

    return None
