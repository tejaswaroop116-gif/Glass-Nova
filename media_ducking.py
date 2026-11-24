"""Automatic media ducking and mic-safe listening attenuation for Glass Nova.

Problem: when a song plays at 100% volume (e.g. YouTube in Chrome, especially with audio boosters
like FxSound), the laptop speakers overwhelm the built-in microphone so Nova cannot hear the user
while the song is playing.

Fix:
1. While Nova's microphone is actively listening (`listening=True`), loud background media/browser
   audio sessions (Chrome, Edge, Brave, Firefox, Spotify, VLC, etc.) are gently attenuated to a
   mic-friendly level (`LISTEN_FACTOR = 0.28`) so you can still hear the music AND Nova can clearly
   hear your voice at any time.
2. During an active voice turn (wake word, user speaking, Nova thinking/speaking, or running a tool),
   background media sessions are ducked further (`DUCK_FACTOR = 0.10`) for crystal-clear recognition,
   then smoothly returned to the listening level (or original 100% level when the mic is off).
3. Nova's own process audio is never reduced.
"""
import os
import threading
import time
from typing import Dict, Optional, Set

DUCK_FACTOR = float(os.getenv("NOVA_DUCK_FACTOR", "0.10"))        # 10% during active speech/turn
LISTEN_FACTOR = float(os.getenv("NOVA_LISTEN_FACTOR", "0.28"))    # 28% while mic is listening
RESTORE_AFTER_S = float(os.getenv("NOVA_DUCK_RESTORE_S", "4.0"))  # quiet time after turn before returning to listen level
SKIP_PROCESSES = {"fxsound.exe", "textinputhost.exe", "audiodg.exe", "svchost.exe", "system"}


class MediaDucker:
    def __init__(self):
        self._lock = threading.RLock()
        self._duck_until = 0.0
        self._hold = 0                           # active turn holds (speaking/thinking/tool)
        self._listening = False                  # True while Nova's microphone is on
        self._originals: Dict[int, float] = {}   # session pid -> original un-ducked volume
        self._applied: Dict[int, float] = {}     # session pid -> last volume we set
        self._own_pids: Set[int] = {os.getpid()}
        try:
            self._own_pids.add(os.getppid())
        except Exception:
            pass
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.enabled = os.getenv("NOVA_AUTO_DUCK", "1").lower() not in ("0", "false", "no")
        self.available = True

    # ---- public triggers -------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="nova-media-duck", daemon=True)
        self._thread.start()

    def stop(self):
        with self._lock:
            self._listening = False
            self._hold = 0
            self._duck_until = 0.0
        self._stop.set()
        self._restore_all()

    def set_listening(self, active: bool):
        """Notify ducker whether Nova's microphone is actively listening."""
        with self._lock:
            self._listening = bool(active)

    def pulse(self, seconds: float = RESTORE_AFTER_S):
        """Conversation activity: deeply duck media for at least `seconds` from now."""
        with self._lock:
            self._duck_until = max(self._duck_until, time.monotonic() + seconds)

    def hold(self):
        with self._lock:
            self._hold += 1
            self._duck_until = max(self._duck_until, time.monotonic() + RESTORE_AFTER_S)

    def release(self):
        with self._lock:
            self._hold = max(0, self._hold - 1)
            self._duck_until = max(self._duck_until, time.monotonic() + RESTORE_AFTER_S)

    def reset_holds(self):
        with self._lock:
            self._hold = 0

    @property
    def is_ducked(self) -> bool:
        return bool(self._applied)

    # ---- worker ------------------------------------------------------------
    def _target_factor(self) -> Optional[float]:
        with self._lock:
            if not self.enabled:
                return None
            if self._hold > 0 or time.monotonic() < self._duck_until:
                return DUCK_FACTOR
            if self._listening:
                return LISTEN_FACTOR
            return None

    def _run(self):
        try:
            import comtypes
            comtypes.CoInitializeEx(0)
        except Exception:
            pass
        while not self._stop.is_set():
            try:
                factor = self._target_factor()
                if factor is not None:
                    self._apply_factor(factor)
                elif self._applied:
                    self._restore_all()
            except Exception:
                pass
            self._stop.wait(0.22)
        self._restore_all()

    def _sessions(self):
        try:
            from pycaw.pycaw import AudioUtilities
            return AudioUtilities.GetAllSessions()
        except Exception:
            self.available = False
            return []

    def _apply_factor(self, factor: float):
        for s in self._sessions():
            try:
                pid = int(s.ProcessId or 0)
                proc = s.Process
                if not pid or proc is None or pid in self._own_pids:
                    continue
                pname = (proc.name() or "").lower()
                if pname in SKIP_PROCESSES or "python" in pname:
                    continue
                vol = s.SimpleAudioVolume
                cur = float(vol.GetMasterVolume())
                with self._lock:
                    if pid not in self._originals:
                        if cur <= factor + 0.02:
                            continue
                        self._originals[pid] = cur
                    orig = self._originals[pid]
                    prev_applied = self._applied.get(pid)
                    # If user manually lowered volume below our target while ducked, respect it
                    if prev_applied is not None and abs(cur - prev_applied) > 0.05 and cur < factor:
                        self._originals[pid] = cur
                        self._applied[pid] = cur
                        continue
                    target = round(min(orig, max(0.05, orig * factor)), 3)
                    if prev_applied is not None and abs(cur - target) <= 0.015:
                        continue
                    self._applied[pid] = target
                vol.SetMasterVolume(target, None)
            except Exception:
                continue

    def _restore_all(self):
        with self._lock:
            originals = dict(self._originals)
            applied = dict(self._applied)
            self._originals.clear()
            self._applied.clear()
        if not originals:
            return
        for s in self._sessions():
            try:
                pid = int(s.ProcessId or 0)
                if pid not in originals:
                    continue
                vol = s.SimpleAudioVolume
                cur = float(vol.GetMasterVolume())
                if abs(cur - applied.get(pid, cur)) <= 0.06:
                    start, end = cur, originals[pid]
                    for i in range(1, 5):
                        vol.SetMasterVolume(round(start + (end - start) * i / 4.0, 3), None)
                        time.sleep(0.04)
            except Exception:
                continue


ducker = MediaDucker()
