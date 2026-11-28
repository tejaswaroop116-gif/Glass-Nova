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
        from nova_dpapi import dump_protected_json
        dump_protected_json(path, payload, indent=2)
        return path

    def load(self, path: str | Path) -> Workflow:
        target = Path(path).expanduser().resolve()
        if target.parent != self.directory:
            raise ValueError("Workflow must be loaded from Nova's workflows directory.")
        from nova_dpapi import load_protected_json
        data = load_protected_json(target)
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



# =============================================================================
# EXPANDED MULTI-STEP WORKFLOW TEMPLATES & VISUAL WORKFLOW DEBUGGER (DRY-RUN)
# =============================================================================

from nova_action_router import ACTION_SCHEMAS, validate_proposed_action

# Derived directly from authoritative nova_action_router.ACTION_SCHEMAS (single source of truth)
ACTION_PERMISSION_MAP: dict[str, Optional[str]] = {
    act: ACTION_SCHEMAS.get(act, {}).get("permission")
    for act in sorted(ALLOWLISTED_WORKFLOW_ACTIONS)
}


@dataclass(frozen=True)
class WorkflowTemplate:
    template_id: str
    name: str
    category: str
    summary: str
    steps: tuple[dict[str, Any], ...]


WORKFLOW_TEMPLATES: tuple[WorkflowTemplate, ...] = (
    WorkflowTemplate(
        template_id="morning_workspace",
        name="Morning Workspace Setup",
        category="Productivity",
        summary="Check PC health, set comfortable volume, open Notepad for daily priorities, and launch your workspace.",
        steps=(
            {"action": "system_status", "arguments": {}, "description": "Check CPU, RAM, battery, and disk health"},
            {"action": "volume_control", "arguments": {"action": "set", "value": 40}, "description": "Set system volume to 40%"},
            {"action": "open_app", "arguments": {"app": "notepad"}, "description": "Open Notepad for daily tasks"},
            {"action": "open_url", "arguments": {"url": "https://calendar.google.com"}, "description": "Open calendar in browser"},
        ),
    ),
    WorkflowTemplate(
        template_id="focus_quiet_mode",
        name="Deep Focus & Quiet Mode",
        category="Focus",
        summary="Lower audio distractions, tune display brightness, calibrate microphone sensitivity, and report system status.",
        steps=(
            {"action": "volume_control", "arguments": {"action": "mute"}, "description": "Mute system audio for focus"},
            {"action": "brightness_control", "arguments": {"action": "set", "value": 65}, "description": "Set screen brightness to comfortable 65%"},
            {"action": "mic_sensitivity_control", "arguments": {"action": "auto_nearest"}, "description": "Enable AUTO NEAREST mic noise gate"},
            {"action": "system_status", "arguments": {}, "description": "Confirm clean background system load"},
        ),
    ),
    WorkflowTemplate(
        template_id="study_lecture_capture",
        name="Study & Lecture Capture",
        category="Study",
        summary="Capture the current slide or lecture window, inspect visible content, and open Notepad for structured notes.",
        steps=(
            {"action": "take_screenshot", "arguments": {}, "description": "Capture current lecture slide screenshot"},
            {"action": "look_at_screen", "arguments": {"query": "Summarize the key points visible on this slide."}, "description": "Analyze visible lecture content"},
            {"action": "open_app", "arguments": {"app": "notepad"}, "description": "Open Notepad for study notes"},
        ),
    ),
    WorkflowTemplate(
        template_id="system_health_check",
        name="System Health Snapshot",
        category="Diagnostics",
        summary="Run a quick local diagnostic: check system metrics, list recent Downloads, and capture a status screenshot.",
        steps=(
            {"action": "system_status", "arguments": {}, "description": "Inspect CPU, memory, and battery status"},
            {"action": "list_folder", "arguments": {"path": "Downloads"}, "description": "List files in Downloads folder"},
            {"action": "take_screenshot", "arguments": {}, "description": "Save a visual desktop snapshot"},
        ),
    ),
    WorkflowTemplate(
        template_id="quick_web_research",
        name="Quick Web Research Brief",
        category="Research",
        summary="Search the web for a topic and open Notepad ready to collect notes.",
        steps=(
            {"action": "search_web", "arguments": {"query": "Python PySide6 latest release notes"}, "description": "Search the web for your research topic"},
            {"action": "open_app", "arguments": {"app": "notepad"}, "description": "Open Notepad to draft research notes"},
        ),
    ),
    WorkflowTemplate(
        template_id="media_break_youtube",
        name="Media Break & Lo-Fi Setup",
        category="Media",
        summary="Set listening volume and launch ambient music on YouTube.",
        steps=(
            {"action": "volume_control", "arguments": {"action": "set", "value": 50}, "description": "Set comfortable listening volume (50%)"},
            {"action": "play_youtube", "arguments": {"query": "lofi hip hop radio beats to relax study to"}, "description": "Play ambient study music on YouTube"},
        ),
    ),
)


def list_workflow_templates() -> list[dict[str, Any]]:
    """Return serializable metadata and steps for all built-in curated workflow templates."""
    out: list[dict[str, Any]] = []
    for tpl in WORKFLOW_TEMPLATES:
        perms = sorted({
            p for s in tpl.steps
            for p in (ACTION_PERMISSION_MAP.get(s["action"]),)
            if p
        })
        out.append({
            "template_id": tpl.template_id,
            "name": tpl.name,
            "category": tpl.category,
            "summary": tpl.summary,
            "required_permissions": perms,
            "steps": [dict(s) for s in tpl.steps],
        })
    return out


def instantiate_template(template_id: str, custom_name: Optional[str] = None) -> Workflow:
    """Create a validated Workflow instance from a built-in template ID."""
    tid = str(template_id or "").strip()
    match = next((t for t in WORKFLOW_TEMPLATES if t.template_id == tid), None)
    if match is None:
        raise ValueError(f"Unknown workflow template '{tid}'.")

    if custom_name and _contains_sensitive_data({}, str(custom_name)):
        raise ValueError("Custom workflow name appears to contain sensitive data and was rejected.")

    now = time.time()
    steps: list[WorkflowStep] = []
    for raw in match.steps:
        act = str(raw.get("action", "")).strip()
        args = dict(raw.get("arguments") or {})
        desc = str(raw.get("description") or act)
        if act not in ALLOWLISTED_WORKFLOW_ACTIONS:
            raise ValueError(f"Template '{tid}' contains disallowed action '{act}'.")
        if _contains_sensitive_data(args, desc):
            raise ValueError(f"Template '{tid}' step '{act}' contains sensitive data.")
        ok_schema, reason = validate_proposed_action({"action": act, "arguments": args})
        if not ok_schema:
            raise ValueError(f"Template '{tid}' step '{act}' failed schema validation: {reason}")
        steps.append(WorkflowStep(action=act, arguments=args, description=desc, created_at=now))

    final_name = (custom_name or match.name).strip()[:100] or match.name
    return Workflow(
        workflow_id=str(uuid.uuid4()),
        name=final_name,
        created_at=now,
        steps=steps,
        version=1,
    )


@dataclass
class WorkflowDebugTrace:
    step_index: int
    action: str
    description: str
    arguments: dict[str, Any]
    required_permission: Optional[str]
    status: str  # "verified", "warning", "blocked", "paused_breakpoint"
    message: str
    duration_ms: float = 0.0


class WorkflowDebugger:
    """Preflight validator & non-destructive Dry-Run visual debugger for semantic workflows."""

    @staticmethod
    def preflight_validate(workflow: Workflow) -> list[WorkflowDebugTrace]:
        """Validate every step against allow-list, secret hygiene, ACTION_SCHEMAS, and dangerous patterns."""
        traces: list[WorkflowDebugTrace] = []
        for idx, step in enumerate(workflow.steps, start=1):
            t0 = time.perf_counter()
            act = str(step.action or "").strip()
            args = dict(step.arguments) if isinstance(step.arguments, dict) else {}
            desc = str(step.description or "")
            perm = ACTION_PERMISSION_MAP.get(act)

            if act not in ALLOWLISTED_WORKFLOW_ACTIONS:
                status = "blocked"
                msg = f"Action '{act}' is not in ALLOWLISTED_WORKFLOW_ACTIONS."
            elif _contains_sensitive_data(args, desc):
                status = "blocked"
                msg = "Step appears to contain sensitive credentials/secrets and is blocked."
            else:
                ok_schema, reason = validate_proposed_action({"action": act, "arguments": args})
                if not ok_schema:
                    status = "blocked"
                    msg = f"Schema / safety validation failed: {reason}"
                elif perm in ("file_delete", "form_purchase", "continuous_screen"):
                    status = "warning"
                    msg = f"High-impact step: requires explicit UI button click for '{perm}'."
                else:
                    perm_note = f"Requires '{perm}' permission" if perm else "No privileged permission required"
                    status = "verified"
                    msg = f"Preflight OK · {perm_note}."

            dt_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            traces.append(
                WorkflowDebugTrace(
                    step_index=idx,
                    action=act,
                    description=desc,
                    arguments=args,
                    required_permission=perm,
                    status=status,
                    message=msg,
                    duration_ms=dt_ms,
                )
            )
        return traces

    @classmethod
    def dry_run(
        cls,
        workflow: Workflow,
        breakpoints: Optional[set[int]] = None,
        start_step: int = 1,
        skip_breakpoint_on_start: bool = False,
        on_trace: Optional[Callable[[WorkflowDebugTrace], None]] = None,
    ) -> dict[str, Any]:
        """Simulate workflow execution without invoking real OS actions.

        Pauses cleanly at any 1-based step index in `breakpoints` (including Step 1 unless
        `skip_breakpoint_on_start=True` is passed when resuming from a paused breakpoint),
        or halts immediately if a step is `blocked`.
        """
        bp = set(breakpoints or ())
        preflight = cls.preflight_validate(workflow)
        traces: list[WorkflowDebugTrace] = []

        for item in preflight:
            if item.step_index < start_step:
                continue
            should_pause = item.step_index in bp and not (skip_breakpoint_on_start and item.step_index == start_step)
            if should_pause:
                paused = WorkflowDebugTrace(
                    step_index=item.step_index,
                    action=item.action,
                    description=item.description,
                    arguments=item.arguments,
                    required_permission=item.required_permission,
                    status="paused_breakpoint",
                    message=f"Paused at breakpoint before step {item.step_index} ({item.action}).",
                    duration_ms=item.duration_ms,
                )
                traces.append(paused)
                if on_trace:
                    on_trace(paused)
                return {
                    "ok": True,
                    "paused": True,
                    "paused_at": item.step_index,
                    "next_step": item.step_index,
                    "traces": [asdict(t) for t in traces],
                }

            traces.append(item)
            if on_trace:
                on_trace(item)

            if item.status == "blocked":
                return {
                    "ok": False,
                    "paused": False,
                    "blocked_at": item.step_index,
                    "traces": [asdict(t) for t in traces],
                }

        return {
            "ok": True,
            "paused": False,
            "completed": len(traces),
            "traces": [asdict(t) for t in traces],
        }
