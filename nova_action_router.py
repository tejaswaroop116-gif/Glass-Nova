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
