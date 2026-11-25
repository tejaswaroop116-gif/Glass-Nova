"""nova_lifecycle.py - Centralized startup/shutdown lifecycle manager and single-instance guard for Glass Nova.

Provides:
- LifecycleManager: ordered, idempotent startup and reverse-order fail-safe shutdown
  with duplicate-init protection and per-component error isolation.
- SingleInstanceGuard: prevents duplicate Glass Nova instances using Qt QLocalServer/QLocalSocket
  (with a pure-Python lockfile fallback when running headless/non-Qt), signaling the existing
  instance to focus its window instead of spawning duplicate mic/wake-word/backend processes.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional


@dataclass
class ManagedComponent:
    name: str
    start_fn: Optional[Callable[[], None]] = None
    stop_fn: Optional[Callable[[], None]] = None
    priority: int = 50  # Lower starts earlier and stops later
    started: bool = False
    last_error: str = ""


class LifecycleManager:
    """Coordinates safe ordered startup and idempotent reverse-order shutdown across Nova components."""

    def __init__(self, on_log: Optional[Callable[[str], None]] = None):
        self._lock = threading.RLock()
        self._components: Dict[str, ManagedComponent] = {}
        self._on_log = on_log
        self.state: str = "idle"  # idle | starting | running | shutting_down | stopped
        self.shutdown_errors: List[str] = []

    def _log(self, msg: str) -> None:
        if self._on_log:
            try:
                self._on_log(msg)
            except Exception:
                pass

    def register(
        self,
        name: str,
        start_fn: Optional[Callable[[], None]] = None,
        stop_fn: Optional[Callable[[], None]] = None,
        priority: int = 50,
    ) -> None:
        """Register (or update) a lifecycle-managed component. Duplicate names update in place."""
        with self._lock:
            existing = self._components.get(name)
            if existing is not None:
                existing.start_fn = start_fn if start_fn is not None else existing.start_fn
                existing.stop_fn = stop_fn if stop_fn is not None else existing.stop_fn
                existing.priority = int(priority)
                return
            self._components[name] = ManagedComponent(
                name=name,
                start_fn=start_fn,
                stop_fn=stop_fn,
                priority=int(priority),
            )

    def start_all(self) -> Dict[str, bool]:
        """Start all registered components in ascending priority order, preventing duplicate initialization."""
        results: Dict[str, bool] = {}
        with self._lock:
            if self.state in ("starting", "running"):
                return {name: c.started for name, c in self._components.items()}
            self.state = "starting"
            ordered = sorted(self._components.values(), key=lambda c: c.priority)

        for comp in ordered:
            with self._lock:
                if self.state in ("shutting_down", "stopped"):
                    break
                if comp.started:
                    results[comp.name] = True
                    continue
            if comp.start_fn is None:
                with self._lock:
                    comp.started = True
                results[comp.name] = True
                continue
            try:
                comp.start_fn()
                with self._lock:
                    comp.started = True
                    comp.last_error = ""
                results[comp.name] = True
            except Exception as exc:
                err_msg = f"[Lifecycle] Failed to start '{comp.name}': {type(exc).__name__}: {exc}"
                with self._lock:
                    comp.started = False
                    comp.last_error = err_msg
                self._log(err_msg)
                results[comp.name] = False

        with self._lock:
            if self.state == "starting":
                self.state = "running"
        return results

    def shutdown_all(self) -> List[str]:
        """Idempotently stop all registered components in reverse priority order.
        Errors in one component never prevent remaining components from cleaning up."""
        with self._lock:
            if self.state in ("shutting_down", "stopped"):
                return list(self.shutdown_errors)
            self.state = "shutting_down"
            self.shutdown_errors = []
            ordered = sorted(self._components.values(), key=lambda c: -c.priority)

        for comp in ordered:
            if comp.stop_fn is None:
                with self._lock:
                    comp.started = False
                continue
            try:
                comp.stop_fn()
            except Exception as exc:
                err_msg = f"[Lifecycle] Error stopping '{comp.name}': {type(exc).__name__}: {exc}"
                self._log(err_msg)
                with self._lock:
                    self.shutdown_errors.append(err_msg)
            finally:
                with self._lock:
                    comp.started = False

        with self._lock:
            self.state = "stopped"
            return list(self.shutdown_errors)

    def status_snapshot(self) -> Dict[str, object]:
        with self._lock:
            return {
                "state": self.state,
                "components": {
                    name: {"started": c.started, "priority": c.priority, "last_error": c.last_error}
                    for name, c in self._components.items()
                },
            }


class SingleInstanceGuard:
    """Prevents duplicate Glass Nova GUI instances and forwards focus requests to the running instance."""

    def __init__(self, app_id: str = "GlassNova_SingleInstance_v1", lock_dir: Optional[Path] = None):
        self.app_id = app_id
        self._lock_dir = Path(lock_dir) if lock_dir else (
            Path(os.environ.get("TEMP") or Path.home()) / "GlassNova"
        )
        self._lock_file = self._lock_dir / f"{app_id}.lock"
        self._server = None
        self._acquired = False
        self._on_focus: Optional[Callable[[], None]] = None

    @property
    def is_primary(self) -> bool:
        return self._acquired

    def acquire(self, on_focus_requested: Optional[Callable[[], None]] = None, use_qt_ipc: bool = True) -> bool:
        """Attempt to become the primary Glass Nova instance.
        Returns True if acquired, False if another live instance is already running."""
        if self._acquired:
            return True
        self._on_focus = on_focus_requested

        if use_qt_ipc:
            try:
                from PySide6.QtNetwork import QLocalServer, QLocalSocket
                sock = QLocalSocket()
                sock.connectToServer(self.app_id)
                if sock.waitForConnected(250):
                    try:
                        sock.write(b"FOCUS\n")
                        sock.flush()
                        sock.waitForBytesWritten(250)
                    finally:
                        sock.disconnectFromServer()
                    return False

                # Remove any stale socket left by an abnormal crash
                QLocalServer.removeServer(self.app_id)
                srv = QLocalServer()
                if srv.listen(self.app_id):
                    def _on_new_conn():
                        while srv.hasPendingConnections():
                            conn = srv.nextPendingConnection()
                            if conn is not None:
                                conn.waitForReadyRead(150)
                                try:
                                    conn.readAll()
                                finally:
                                    conn.disconnectFromServer()
                                    conn.deleteLater()
                                if self._on_focus:
                                    try:
                                        self._on_focus()
                                    except Exception:
                                        pass
                    srv.newConnection.connect(_on_new_conn)
                    self._server = srv
                    self._acquired = True
                    self._write_pid_lock()
                    return True
            except Exception:
                # Fall through to PID-lockfile check when QtNetwork is unavailable
                pass

        # Pure-Python PID lockfile fallback (for headless/sandbox/tests)
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        if self._lock_file.is_file():
            try:
                existing_pid = int(self._lock_file.read_text(encoding="utf-8").strip())
                if existing_pid != os.getpid() and self._is_pid_alive(existing_pid):
                    return False
            except Exception:
                pass
        self._write_pid_lock()
        self._acquired = True
        return True

    def _write_pid_lock(self) -> None:
        try:
            self._lock_dir.mkdir(parents=True, exist_ok=True)
            self._lock_file.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            import psutil
            return psutil.pid_exists(pid)
        except Exception:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    def release(self) -> None:
        """Release the single-instance lock cleanly on shutdown."""
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        if self._acquired:
            try:
                if self._lock_file.is_file():
                    content = self._lock_file.read_text(encoding="utf-8").strip()
                    if content == str(os.getpid()):
                        self._lock_file.unlink(missing_ok=True)
            except Exception:
                pass
        self._acquired = False
