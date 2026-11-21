"""User-approved workflow recording and replay for Nova.

Records semantic, allow-listed action descriptions, never raw keystrokes,
passwords, clipboard contents, or arbitrary Python. Replay asks approval
before every step and can be cancelled between steps.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Optional
import json
import time
import uuid


# Strict allow-list of semantic Nova action names that may be recorded or replayed
ALLOWLISTED_WORKFLOW_ACTIONS = frozenset({
    "open_app",
    "open_file",
    "open_url",
    "search_web",
    "play_youtube",
    "open_folder",
    "system_status",
    "volume_control",
    "brightness_control",
    "window_control",
    "app_control",
    "take_screenshot",
    "write_in_notepad",
    "click_ui_element",
    "skip_youtube_ad",
    "close_browser_tab",
    "control_youtube_video",
    "scroll_desktop",
    "create_folder",
    "list_folder",
    "rename_file",
    "copy_file",
    "move_file",
    "delete_file",
    "calculator_operation",
    "write_text_file",
    "read_text_file",
    "look_at_screen",
    "inspect_document",
    "inspect_video",
    "mic_sensitivity_control",
})

FORBIDDEN_SECRET_KEYS = frozenset({
    "password", "passwd", "pwd", "token", "api_key", "apikey",
    "secret", "credit_card", "card_number", "cvv", "cvc", "pin",
    "ssn", "private_key", "bearer", "authorization", "clipboard",
})

_SECRET_VALUE_PATTERNS = (
    "api_key=", "apikey=", "password=", "passwd=", "bearer ", "sk-", "aiza",
)


def _contains_sensitive_data(arguments: dict[str, Any], description: str = "") -> bool:
    for k, v in arguments.items():
        key_low = str(k).lower().strip()
        if key_low in FORBIDDEN_SECRET_KEYS or any(f in key_low for f in ("password", "passwd", "token", "api_key", "secret", "cvv", "credit_card")):
            return True
        val_low = str(v).lower()
        if any(pat in val_low for pat in _SECRET_VALUE_PATTERNS):
            return True
    desc_low = (description or "").lower()
    if any(pat in desc_low for pat in _SECRET_VALUE_PATTERNS):
        return True
    return False


@dataclass
class WorkflowStep:
    action: str
    arguments: dict[str, Any]
    description: str
    created_at: float


@dataclass
class Workflow:
    workflow_id: str
    name: str
    created_at: float
    steps: list[WorkflowStep]
    version: int = 1


class WorkflowRecorder:
    def __init__(self, allowed_actions: Optional[set[str] | frozenset[str]] = None):
        self._active = False
        self._steps: list[WorkflowStep] = []
        self._allowed = allowed_actions

    @property
    def is_recording(self) -> bool:
        return self._active

    @property
    def steps(self) -> list[WorkflowStep]:
        return list(self._steps)

    def start(self) -> None:
        if self._active:
            raise RuntimeError("A workflow is already being recorded.")
        self._steps = []
        self._active = True

    def record(self, action: str, arguments: dict[str, Any], description: str) -> None:
        if not self._active:
            raise RuntimeError("Start recording before adding steps.")
        if not action or not isinstance(arguments, dict):
            raise ValueError("Invalid workflow step.")
        # Block raw keystroke/clipboard/arbitrary execution actions
        if action in ("press_key", "hotkey", "press_keyboard_hotkey", "type_text", "type_text_into_ui", "clipboard_control", "click_mouse", "mouse_control"):
            raise ValueError(f"Raw input or clipboard action '{action}' cannot be recorded in semantic workflows.")
        if self._allowed is not None and action not in self._allowed:
            raise ValueError(f"Action '{action}' is not in the workflow allow-list.")
        # Block likely secret-bearing fields or values.
        if _contains_sensitive_data(arguments, description):
            raise ValueError("This step appears to contain sensitive data and was not recorded.")
        self._steps.append(WorkflowStep(action, dict(arguments), description, time.time()))

    def stop(self, name: str) -> Workflow:
        if not self._active:
            raise RuntimeError("No workflow is being recorded.")
        self._active = False
        clean_name = (name or "Untitled workflow").strip()[:100]
        return Workflow(str(uuid.uuid4()), clean_name, time.time(), list(self._steps))

    def cancel(self) -> None:
        self._active = False
        self._steps = []


class WorkflowStore:
    """JSON store restricted to a caller-selected workflows directory."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser().resolve()
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            import tempfile
            fallback = Path(tempfile.gettempdir()) / "PC_Complete_Assistant" / "workflows"
            fallback.mkdir(parents=True, exist_ok=True)
            self.directory = fallback.resolve()

    def save(self, workflow: Workflow) -> Path:
        path = self.directory / f"{workflow.workflow_id}.json"
        payload = {
            "version": getattr(workflow, "version", 1),
            "workflow_id": workflow.workflow_id,
            "name": workflow.name,
            "created_at": workflow.created_at,
            "steps": [asdict(step) for step in workflow.steps],
        }
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
        return path

    def load(self, path: str | Path) -> Workflow:
        target = Path(path).expanduser().resolve()
        if target.parent != self.directory:
            raise ValueError("Workflow must be loaded from Nova's workflows directory.")
        data = json.loads(target.read_text(encoding="utf-8"))
        steps = [WorkflowStep(**item) for item in data.get("steps", [])]
        return Workflow(
            data["workflow_id"],
            data["name"],
            data["created_at"],
            steps,
            version=int(data.get("version", 1)),
        )

    def list_workflows(self) -> list[Workflow]:
        out: list[Workflow] = []
        for p in sorted(self.directory.glob("*.json")):
            try:
                out.append(self.load(p))
            except Exception:
                continue
        out.sort(key=lambda w: w.created_at, reverse=True)
        return out

    def delete(self, workflow_id: str) -> bool:
        clean_id = Path(str(workflow_id)).name
        if clean_id.endswith(".json"):
            target = (self.directory / clean_id).resolve()
        else:
            target = (self.directory / f"{clean_id}.json").resolve()
        if target.parent != self.directory or not target.exists():
            return False
        target.unlink()
        return True


class WorkflowPlayer:
    """Replays semantic steps with confirmation, cancellation, and verification."""

    def __init__(
        self,
        action_executor: Callable[[str, dict[str, Any]], Any],
        confirm_step: Callable[[int, WorkflowStep], bool],
        verify_step: Optional[Callable[[int, WorkflowStep, Any], bool]] = None,
    ):
        self.action_executor = action_executor
        self.confirm_step = confirm_step
        self.verify_step = verify_step
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def play(self, workflow: Workflow) -> dict[str, Any]:
        self._cancelled = False
        results = []
        for index, step in enumerate(workflow.steps, start=1):
            if self._cancelled:
                return {"ok": False, "cancelled": True, "completed": len(results), "results": results}
            if not self.confirm_step(index, step):
                return {"ok": False, "denied": True, "stopped_at": index, "results": results}
            if self._cancelled:
                return {"ok": False, "cancelled": True, "completed": len(results), "results": results}
            try:
                result = self.action_executor(step.action, dict(step.arguments))
                verified = self.verify_step(index, step, result) if self.verify_step else None
                results.append({"step": index, "result": result, "verified": verified})
                if verified is False:
                    return {"ok": False, "verification_failed_at": index, "results": results}
            except Exception as exc:
                return {"ok": False, "error": str(exc), "failed_at": index, "results": results}
        return {"ok": True, "completed": len(results), "results": results}
