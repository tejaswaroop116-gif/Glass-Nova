"""Permission manager for Glass Nova visual desktop agent.

Manages separate permission categories for screen viewing, document access,
media access, mouse/keyboard control, file operations, and sensitive actions.
Supports Allow Once, Allow for Session, Deny, and instant Revocation.
"""
import os, json, threading, time
from pathlib import Path
from typing import Callable, Optional, Dict, Any, List

CATEGORIES = {
    "screen_view": {
        "title": "Screen View",
        "description": "Capture the desktop screen or active window to analyze visual content.",
        "icon": "👁"
    },
    "continuous_screen": {
        "title": "Continuous Observation",
        "description": "Continuously observe the screen across time.",
        "icon": "📹"
    },
    "document_access": {
        "title": "Document & PDF Access",
        "description": "Inspect, extract text, and analyze documents and PDFs.",
        "icon": "📄"
    },
    "media_access": {
        "title": "Video & Audio Inspection",
        "description": "Analyze video frames, lecture slides, and audio recordings.",
        "icon": "🎞"
    },
    "mouse_keyboard": {
        "title": "Mouse & Keyboard Control",
        "description": "Move the mouse, click UI elements, and type text.",
        "icon": "🖱"
    },
    "file_modify": {
        "title": "File Modification",
        "description": "Create, edit, copy, move, or rename files and folders.",
        "icon": "📝"
    },
    "file_delete": {
        "title": "File Deletion",
        "description": "Permanently delete files or folders.",
        "icon": "🗑"
    },
    "app_install": {
        "title": "Application Installation",
        "description": "Launch installer files or download external software.",
        "icon": "📦"
    },
    "form_purchase": {
        "title": "Purchases & External Forms",
        "description": "Submit online forms, payment screens, or external messages.",
        "icon": "💳"
    }
}

_AUDIT_FILE = Path(os.environ.get("APPDATA") or Path.home()) / "PC_Complete_Assistant" / "permission_audit.json"

class PermissionManager:
    """Thread-safe permission manager with session state and audit trail."""
    _instance = None
    _lock = threading.RLock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        self.session_grants: Dict[str, bool] = {}
        self.once_grants_until: Dict[str, float] = {}
        self.prompt_callback: Optional[Callable[[str, str, dict], str]] = None
        self.audit_log: List[dict] = []
        self._load_audit()

    def set_prompt_callback(self, cb: Callable[[str, str, dict], str]):
        """Sets the UI callback function: cb(category, action_desc, details) -> 'once' | 'session' | 'deny'"""
        self.prompt_callback = cb

    def is_granted(self, category: str) -> bool:
        """Check if category is currently granted for this session."""
        with self._lock:
            return bool(self.session_grants.get(category, False))

    def grant_session(self, category: str):
        """Grant permission for the remainder of this session."""
        with self._lock:
            self.session_grants[category] = True
            self._record_audit(category, "Granted for current session", "allow_session")

    def _resolve_categories(self, category: str) -> List[str]:
        """Resolve a category name or natural voice phrase into one or more CATEGORIES keys."""
        cat_clean = (category or "").lower().strip()
        if not cat_clean:
            return []

        if cat_clean in ("all", "everything", "all permissions", "full access", "every permission"):
            return list(CATEGORIES.keys())

        if cat_clean in CATEGORIES:
            return [cat_clean]

        aliases = {
            "screen": ["screen_view"],
            "screen viewing": ["screen_view"],
            "see screen": ["screen_view"],
            "screen view": ["screen_view"],
            "look at screen": ["screen_view"],
            "camera": ["screen_view", "media_access", "continuous_screen"],
            "webcam": ["screen_view", "media_access", "continuous_screen"],
            "cam": ["screen_view", "media_access", "continuous_screen"],
            "camera access": ["screen_view", "media_access", "continuous_screen"],
            "vision": ["screen_view", "media_access"],
            "continuous": ["continuous_screen", "screen_view"],
            "continuous screen": ["continuous_screen", "screen_view"],
            "mouse": ["mouse_keyboard"],
            "control mouse": ["mouse_keyboard"],
            "mouse control": ["mouse_keyboard"],
            "keyboard": ["mouse_keyboard"],
            "mouse and keyboard": ["mouse_keyboard"],
            "hand": ["mouse_keyboard", "media_access", "screen_view"],
            "gesture": ["mouse_keyboard", "media_access", "screen_view"],
            "hand gesture": ["mouse_keyboard", "media_access", "screen_view"],
            "hand control": ["mouse_keyboard", "media_access", "screen_view"],
            "document": ["document_access"],
            "documents": ["document_access"],
            "pdf": ["document_access"],
            "media": ["media_access"],
            "video": ["media_access"],
            "file": ["file_modify"],
            "files": ["file_modify"],
            "file modify": ["file_modify"],
            "file modification": ["file_modify"],
            "file delete": ["file_delete"],
            "file deletion": ["file_delete"],
            "delete": ["file_delete"],
        }
        if cat_clean in aliases:
            return aliases[cat_clean]

        # Natural phrase / multi-keyword matching (e.g. "access the camera or mouse")
        import re
        words = set(re.findall(r"[a-z0-9_]+", cat_clean))
        if words & {"all", "everything"}:
            return list(CATEGORIES.keys())

        matched = []
        def _add(*cats):
            for c in cats:
                if c in CATEGORIES and c not in matched:
                    matched.append(c)

        if words & {"hand", "gesture", "gestures"}:
            _add("mouse_keyboard", "media_access", "screen_view")
        if words & {"camera", "webcam", "cam", "vision"}:
            _add("screen_view", "media_access", "continuous_screen")
        if words & {"screen", "display", "monitor", "view", "viewing", "see", "look", "screenshot", "window"}:
            _add("screen_view")
        if words & {"continuous", "stream", "observe", "watch"}:
            _add("continuous_screen", "screen_view")
        if words & {"mouse", "cursor", "click", "clicking", "keyboard", "type", "typing", "hotkey", "scroll", "control"}:
            _add("mouse_keyboard")
        if words & {"document", "documents", "pdf", "pdfs", "paper", "notes"}:
            _add("document_access")
        if words & {"media", "video", "videos", "lecture", "audio"}:
            _add("media_access")
        if words & {"file", "files", "folder", "folders", "modify", "rename", "copy", "move", "write"}:
            _add("file_modify")
        if words & {"delete", "deletion", "remove", "erase"}:
            _add("file_delete")
        if words & {"install", "installer"}:
            _add("app_install")
        if words & {"purchase", "buy", "payment", "form"}:
            _add("form_purchase")

        return matched

    def grant_by_voice(self, category: str, scope: str = "session"):
        """Grant permission by voice command for a specific category, phrase, or 'all'."""
        granted = []
        with self._lock:
            targets = self._resolve_categories(category)
            for t in targets:
                self.session_grants[t] = True
                granted.append(t)
            if granted:
                label = "all" if len(granted) == len(CATEGORIES) else ",".join(granted)
                self._record_audit(label, f"Granted by user voice command ({category})", f"voice_{scope}")
        return granted

    def revoke(self, category: str):
        """Immediately revoke a permission category or natural phrase."""
        with self._lock:
            targets = self._resolve_categories(category)
            if not targets and category in self.session_grants:
                targets = [category]
            for t in targets:
                self.session_grants[t] = False
                self.once_grants_until.pop(t, None)
                self._record_audit(t, "Permission revoked by user", "revoked")

    def revoke_all(self):
        """Immediately revoke all session permissions."""
        with self._lock:
            self.session_grants.clear()
            self.once_grants_until.clear()
            self._record_audit("all", "All permissions revoked", "revoked_all")

    def request_permission(self, category: str, action_desc: str, details: Optional[dict] = None) -> bool:
        """
        Requests permission for a specific category.
        If already granted for this session (and not continuous or destructive), allows immediately.
        Otherwise triggers the registered UI prompt callback.
        """
        # Always require explicit confirmation for file deletion and form purchases
        always_prompt = category in ("file_delete", "form_purchase", "continuous_screen")

        with self._lock:
            if not always_prompt and self.session_grants.get(category):
                self._record_audit(category, action_desc, "auto_session_allow")
                return True
            once_deadline = float(self.once_grants_until.get(category, 0.0) or 0.0)
            if once_deadline and time.monotonic() <= once_deadline:
                # Consume the single-action grace window so broker + inner tool don't double-prompt
                self.once_grants_until.pop(category, None)
                self._record_audit(category, action_desc, "allow_once_grace")
                return True

        if not self.prompt_callback:
            # Default to deny if no UI prompt handler is wired
            self._record_audit(category, action_desc, "deny_no_ui")
            return False

        decision = self.prompt_callback(category, action_desc, details or {})
        decision_str = str(decision).lower().strip()

        with self._lock:
            if decision_str in ("session", "allow_session"):
                self.session_grants[category] = True
                self._record_audit(category, action_desc, "allow_session")
                return True
            elif decision_str in ("once", "allow_once", "true", "yes"):
                self.once_grants_until[category] = time.monotonic() + 4.0
                self._record_audit(category, action_desc, "allow_once")
                return True
            else:
                self._record_audit(category, action_desc, "deny")
                return False

    # Alias so both permissions.request_permission(...) and permissions.request(...) work
    request = request_permission

    def get_status(self) -> Dict[str, bool]:
        with self._lock:
            return {k: self.session_grants.get(k, False) for k in CATEGORIES}

    def _record_audit(self, category: str, action: str, outcome: str):
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "category": category,
            "action": action,
            "outcome": outcome
        }
        self.audit_log.append(entry)
        if len(self.audit_log) > 200:
            self.audit_log = self.audit_log[-200:]
        try:
            _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _AUDIT_FILE.write_text(json.dumps(self.audit_log, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _load_audit(self):
        if _AUDIT_FILE.exists():
            try:
                self.audit_log = json.loads(_AUDIT_FILE.read_text(encoding="utf-8"))[-200:]
            except Exception:
                self.audit_log = []

permissions = PermissionManager()
