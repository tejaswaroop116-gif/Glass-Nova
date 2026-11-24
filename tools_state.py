"""tools_state.py - Shared tool response helpers, persistent local memory, and task/reminder management."""
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

from permissions import permissions

WORKSPACE = Path.home() / "Downloads" / "PC_Workspace"
try:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
except OSError:
    pass


def _check_perm(category: str, action_desc: str, details: dict = None) -> bool:
    """Enforce permission checks when a permission prompt handler is active."""
    if permissions.prompt_callback is None and not permissions.is_granted(category):
        return True
    return permissions.request_permission(category, action_desc, details or {})


def _ok(message, verified: bool = True, **extra):
    return {"ok": True, "verified": bool(verified), "message": message, **extra}


def _unverified(message, **extra):
    return {"ok": True, "verified": False, "message": message, **extra}


def _err(message, **extra):
    return {"ok": False, "verified": False, "message": message, **extra}


# Persistent assistant state is stored locally under the user's roaming profile.
# The JSON file is plain text; users can inspect or delete it themselves.
_STATE_DIR = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "PC_Complete_Assistant"
_STATE_FILE = _STATE_DIR / "assistant_state.json"
_STATE_LOCK = threading.RLock()


def _empty_assistant_state():
    return {"version": 1, "memories": {}, "tasks": []}


def _load_assistant_state():
    if not _STATE_FILE.exists():
        return _empty_assistant_state()
    try:
        state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Saved assistant data could not be read; no changes were made: {e}")
    if not isinstance(state, dict) or not isinstance(state.get("memories"), dict) or not isinstance(state.get("tasks"), list):
        raise ValueError("Saved assistant data has an invalid format; no changes were made.")
    return state


def _save_assistant_state(state):
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_FILE.with_name(f".{_STATE_FILE.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _STATE_FILE)
    finally:
        if tmp.exists():
            tmp.unlink()


def _local_now():
    return datetime.now().astimezone()


def _normalize_due_at(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Use an ISO 8601 date and time, such as 2026-10-24T17:30:00+05:30.")
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat(timespec="seconds")


def memory_control(action: str, key: str = "", value: str = "", confirm: bool = False):
    """Store and retrieve user-approved, non-sensitive memories in a local JSON file."""
    try:
        a = str(action).lower().strip().replace("-", "_").replace(" ", "_")
        with _STATE_LOCK:
            state = _load_assistant_state()
            memories = state["memories"]
            if a in ("list", "show", "all"):
                items = sorted(memories.values(), key=lambda item: item.get("key", "").lower())
                return _ok(f"Found {len(items)} saved memories.", memories=items)
            if a in ("remember", "save", "set"):
                label = str(key or "").strip()
                content = str(value or "").strip()
                if not label or not content:
                    return _err("A memory key and value are both required.")
                if len(label) > 100 or len(content) > 2000:
                    return _err("Memory key or value is too long.")
                lowered = label.lower()
                if any(term in lowered for term in ("password", "api key", "token", "secret", "credential", "passcode")):
                    return _err("I do not store passwords, API keys, tokens, or secrets.")
                memories[lowered] = {
                    "key": label,
                    "value": content,
                    "updated_at": _local_now().isoformat(timespec="seconds"),
                }
                _save_assistant_state(state)
                return _ok(f"Saved memory: {label}.", key=label)
            if a in ("get", "recall", "read"):
                item = memories.get(str(key or "").strip().lower())
                if item is None:
                    return _err(f"No saved memory was found for {key}.")
                return _ok(f"Retrieved memory: {item.get('key', key)}.", memory=item)
            if a in ("forget", "remove", "delete"):
                normalized = str(key or "").strip().lower()
                if normalized not in memories:
                    return _err(f"No saved memory was found for {key}.")
                removed = memories.pop(normalized)
                _save_assistant_state(state)
                return _ok(f"Forgot memory: {removed.get('key', key)}.")
            if a in ("clear", "clear_all"):
                if not confirm:
                    return {"ok": False, "confirmation_required": True, "message": "Clearing every saved memory is destructive. Ask the user for confirmation first."}
                count = len(memories)
                memories.clear()
                _save_assistant_state(state)
                return _ok(f"Cleared {count} saved memories.")
            return _err("Unknown memory action. Use remember, get, list, forget, or clear.")
    except Exception as e:
        return _err(f"Could not access saved memory: {e}")


def task_control(action: str, title: str = "", details: str = "", task_id: str = "",
                 status: str = "", due_at: str = "", confirm: bool = False):
    """Persist goals and tasks locally, with optional due-time reminders."""
    try:
        a = str(action).lower().strip().replace("-", "_").replace(" ", "_")
        with _STATE_LOCK:
            state = _load_assistant_state()
            tasks = state["tasks"]
            if a in ("now", "time"):
                now = _local_now()
                return _ok("Local time.", local_time=now.isoformat(timespec="seconds"), timezone=now.tzname())
            if a in ("list", "show", "all"):
                ordered = sorted(tasks, key=lambda item: (item.get("status") == "completed", item.get("due_at") or "9999", item.get("created_at", "")))
                return _ok(f"Found {len(ordered)} tracked tasks.", tasks=ordered[:100])
            if a in ("due", "due_reminders"):
                now = _local_now()
                due = []
                for item in tasks:
                    if item.get("status") == "completed" or item.get("reminder_sent_at") or not item.get("due_at"):
                        continue
                    try:
                        due_time = datetime.fromisoformat(item["due_at"].replace("Z", "+00:00"))
                        if due_time.tzinfo is None:
                            due_time = due_time.astimezone()
                        if due_time <= now:
                            due.append(item)
                    except (TypeError, ValueError):
                        continue
                return _ok(f"Found {len(due)} due reminders.", tasks=due)
            if a in ("create", "add", "new"):
                task_title = str(title or "").strip()
                if not task_title:
                    return _err("A task title is required.")
                if len(task_title) > 200:
                    return _err("Task title is too long.")
                task_details = str(details or "").strip()
                if len(task_details) > 4000:
                    return _err("Task details are too long.")
                normalized_due = _normalize_due_at(due_at)
                now = _local_now().isoformat(timespec="seconds")
                item = {
                    "id": uuid.uuid4().hex[:8],
                    "title": task_title,
                    "details": task_details,
                    "status": "active",
                    "due_at": normalized_due or None,
                    "created_at": now,
                    "updated_at": now,
                    "reminder_sent_at": None,
                }
                tasks.append(item)
                _save_assistant_state(state)
                return _ok(f"Started tracking: {task_title}.", task=item)
            if a in ("update", "edit"):
                item = next((t for t in tasks if t.get("id") == str(task_id)), None)
                if item is None:
                    return _err(f"No task was found with ID {task_id}.")
                if title:
                    if len(str(title).strip()) > 200:
                        return _err("Task title is too long.")
                    item["title"] = str(title).strip()
                if details:
                    if len(str(details).strip()) > 4000:
                        return _err("Task details are too long.")
                    item["details"] = str(details).strip()
                if status:
                    normalized_status = str(status).lower().strip()
                    if normalized_status not in ("active", "waiting", "completed"):
                        return _err("Status must be active, waiting, or completed.")
                    item["status"] = normalized_status
                if due_at:
                    if str(due_at).strip().lower() in ("clear", "none", "remove"):
                        item["due_at"] = None
                        item["reminder_sent_at"] = None
                    else:
                        item["due_at"] = _normalize_due_at(due_at)
                        item["reminder_sent_at"] = None
                item["updated_at"] = _local_now().isoformat(timespec="seconds")
                _save_assistant_state(state)
                return _ok(f"Updated task {item['id']}.", task=item)
            if a in ("complete", "done", "mark_complete"):
                item = next((t for t in tasks if t.get("id") == str(task_id)), None)
                if item is None:
                    return _err(f"No task was found with ID {task_id}.")
                item["status"] = "completed"
                item["updated_at"] = _local_now().isoformat(timespec="seconds")
                _save_assistant_state(state)
                return _ok(f"Completed: {item.get('title', task_id)}.", task=item)
            if a in ("mark_reminded", "reminded"):
                item = next((t for t in tasks if t.get("id") == str(task_id)), None)
                if item is None:
                    return _err(f"No task was found with ID {task_id}.")
                item["reminder_sent_at"] = _local_now().isoformat(timespec="seconds")
                _save_assistant_state(state)
                return _ok(f"Reminder recorded for task {item['id']}.")
            if a in ("delete", "remove"):
                item = next((t for t in tasks if t.get("id") == str(task_id)), None)
                if item is None:
                    return _err(f"No task was found with ID {task_id}.")
                if not confirm:
                    return {"ok": False, "confirmation_required": True, "message": f"Deleting task {item.get('title', task_id)} is destructive. Ask the user for confirmation first."}
                tasks.remove(item)
                _save_assistant_state(state)
                return _ok(f"Deleted task {task_id}.")
            return _err("Unknown task action. Use create, list, update, complete, or delete.")
    except Exception as e:
        return _err(f"Could not manage tracked tasks: {e}")
