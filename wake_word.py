"""Offline, local wake-word and single-breath voice command listener for Glass Nova.

Uses Vosk locally with ultra-low CPU to detect 'Hey Nova' (plus any immediate trailing
offline command such as 'Hey Nova open calculator') and coordinates safe microphone
handover with pc_assistant.py.
"""
import json
import os
import re
import sys
import threading
import time
from typing import Callable, Optional
import sounddevice as sd

from nova_action_router import OFFLINE_VOICE_PHRASES
from nova_voice_control import VOICE_CONTROL_PHRASES

_SHARED_VOSK_MODEL = None
_SHARED_VOSK_LOCK = threading.Lock()


class WakeWordListener:
    """Continuous local wake-word listener running in a dedicated background thread."""
    def __init__(self,
                 on_detected: Optional[Callable[[], None]] = None,
                 on_status_change: Optional[Callable[[str], None]] = None,
                 device_index: Optional[int] = None):
        self.on_detected = on_detected
        self.on_status_change = on_status_change
        self.device_index = device_index

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._paused_event = threading.Event()
        self._stream_active = threading.Event()
        self._state_lock = threading.RLock()
        self._model = None
        self._recognizer = None
        self._last_trigger_time = 0.0
        self._cooldown_seconds = 2.2
        self._error_count = 0
        self.last_command: str = ""
        self.status = "uninitialized"  # "listening" | "paused" | "error" | "stopped"

    def _notify_status(self, new_status: str):
        self.status = new_status
        if self.on_status_change:
            try:
                self.on_status_change(new_status)
            except Exception:
                pass

    def _init_model(self):
        global _SHARED_VOSK_MODEL
        if self._model is not None:
            return True
        try:
            import vosk
            vosk.SetLogLevel(-1)
            with _SHARED_VOSK_LOCK:
                if _SHARED_VOSK_MODEL is None:
                    _SHARED_VOSK_MODEL = vosk.Model(model_name="vosk-model-small-en-us-0.15")
                self._model = _SHARED_VOSK_MODEL
            combined_phrases = list(dict.fromkeys(list(OFFLINE_VOICE_PHRASES) + list(VOICE_CONTROL_PHRASES)))
            grammar = json.dumps(combined_phrases)
            self._recognizer = vosk.KaldiRecognizer(self._model, 16000, grammar)
            return True
        except Exception:
            self._notify_status("error")
            return False

    def _reset_recognizer(self):
        if self._recognizer is not None:
            try:
                self._recognizer.Reset()
            except Exception:
                pass

    def _set_ducker_listening(self, active: bool, stop_ducker: bool = False):
        try:
            from media_ducking import ducker
            if active:
                ducker.start()
                ducker.set_listening(True)
            else:
                ducker.set_listening(False)
                if stop_ducker:
                    ducker.stop()
        except Exception:
            pass

    def start(self):
        """Starts the wake word background thread."""
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._paused_event.clear()
            self._error_count = 0
            self._thread = threading.Thread(target=self._run_loop, name="NovaWakeWord", daemon=True)
            self._thread.start()

    def pause(self, wait_timeout: float = 0.6):
        """Temporarily release microphone (e.g. while Gemini Live / offline voice command loop is active)."""
        self._paused_event.set()
        self._set_ducker_listening(False)
        # Wait briefly for the input stream to close cleanly before handing the mic to another audio system
        if wait_timeout > 0 and threading.current_thread() is not self._thread:
            deadline = time.monotonic() + wait_timeout
            while self._stream_active.is_set() and time.monotonic() < deadline:
                time.sleep(0.02)
        self._reset_recognizer()

    def resume(self):
        """Resume local wake-word detection."""
        with self._state_lock:
            if self._stop_event.is_set():
                return
            self._reset_recognizer()
            self._paused_event.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run_loop, name="NovaWakeWord", daemon=True)
                self._thread.start()

    def stop(self):
        """Completely stop listener and release all resources."""
        with self._state_lock:
            self._stop_event.set()
            self._paused_event.set()
            t = self._thread
        if t and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=2.0)
        with self._state_lock:
            self._thread = None
            self._stream_active.clear()
        self._reset_recognizer()
        self._set_ducker_listening(False, stop_ducker=True)
        self._notify_status("stopped")

    def _run_loop(self):
        if not self._init_model():
            return
        if self._stop_event.is_set():
            self._notify_status("stopped")
            return

        while not self._stop_event.is_set():
            if self._paused_event.is_set():
                self._set_ducker_listening(False)
                self._notify_status("paused")
                while self._paused_event.is_set() and not self._stop_event.is_set():
                    time.sleep(0.08)
                if self._stop_event.is_set():
                    break
                self._reset_recognizer()

            q = []
            q_lock = threading.Lock()

            def audio_cb(indata, frames, time_info, status):
                if not self._paused_event.is_set() and not self._stop_event.is_set():
                    with q_lock:
                        if len(q) < 16:
                            q.append(bytes(indata))

            wake_hit = False
            pending_wake_since = 0.0
            last_partial_text = ""
            last_partial_change_ts = 0.0
            try:
                with sd.RawInputStream(samplerate=16000, blocksize=2048, channels=1,
                                       dtype="int16", callback=audio_cb,
                                       device=self.device_index):
                    self._stream_active.set()
                    self._error_count = 0
                    self._notify_status("listening")
                    self._set_ducker_listening(True)
                    while not self._paused_event.is_set() and not self._stop_event.is_set():
                        data = None
                        with q_lock:
                            if q:
                                data = q.pop(0)

                        if not data:
                            time.sleep(0.025)
                            continue

                        now = time.monotonic()
                        if self._recognizer.AcceptWaveform(data):
                            res = json.loads(self._recognizer.Result() or "{}")
                            text = res.get("text", "").lower().strip()
                            if self._check_wake_text(text):
                                self._reset_recognizer()
                                wake_hit = True
                                break
                            elif pending_wake_since > 0.0:
                                # Wake phrase was detected in partial just before finalization
                                self._last_trigger_time = now
                                self._extract_trailing_command(text or last_partial_text)
                                self._reset_recognizer()
                                wake_hit = True
                                break
                        else:
                            partial = json.loads(self._recognizer.PartialResult() or "{}")
                            p_text = partial.get("partial", "").lower().strip()
                            if p_text != last_partial_text:
                                last_partial_text = p_text
                                last_partial_change_ts = now
                            if pending_wake_since == 0.0:
                                if self._has_wake_tokens(p_text) and (now - self._last_trigger_time >= self._cooldown_seconds):
                                    pending_wake_since = now
                            else:
                                # Wait briefly (0.48s after last new word, or max 1.8s total) to capture single-breath commands
                                if (now - last_partial_change_ts >= 0.48) or (now - pending_wake_since >= 1.8):
                                    self._last_trigger_time = now
                                    self._extract_trailing_command(last_partial_text)
                                    self._reset_recognizer()
                                    wake_hit = True
                                    break

            except Exception:
                self._error_count += 1
                self._reset_recognizer()
                if self._error_count >= 2:
                    self._notify_status("error")
                backoff = min(4.0, 0.5 * (2 ** min(self._error_count - 1, 3)))
                deadline = time.monotonic() + backoff
                while not self._stop_event.is_set() and not self._paused_event.is_set() and time.monotonic() < deadline:
                    time.sleep(0.08)
            finally:
                self._stream_active.clear()
                with q_lock:
                    q.clear()

            if wake_hit and not self._stop_event.is_set():
                self._trigger_wake()

        self._set_ducker_listening(False)
        self._notify_status("stopped")

    def _has_wake_tokens(self, text: str) -> bool:
        if not text:
            return False
        clean = text.lower().replace(",", " ").replace(".", " ").replace("!", " ").strip()
        tokens = [t for t in clean.split() if t != "[unk]"]
        if not tokens:
            return False
        joined = " ".join(tokens)
        return "hey nova" in joined or tokens[0] == "nova"

    def _extract_trailing_command(self, text: str) -> str:
        clean = (text or "").lower().replace("[unk]", " ").strip()
        clean = " ".join(clean.split())
        m = re.search(r"(?:hey\s+nova|nova)\s+(.+)$", clean)
        if m:
            cmd = m.group(1).strip()
            # Filter out bare wake echoes
            if cmd and cmd not in ("hey", "nova", "hey nova"):
                self.last_command = cmd
                return cmd
        self.last_command = ""
        return ""

    def _check_wake_text(self, text: str) -> bool:
        if not self._has_wake_tokens(text):
            return False
        now = time.monotonic()
        if now - self._last_trigger_time >= self._cooldown_seconds:
            self._last_trigger_time = now
            self._extract_trailing_command(text)
            return True
        return False

    def _trigger_wake(self):
        self.pause()
        try:
            from media_ducking import ducker
            ducker.pulse(8.0)
        except Exception:
            pass
        if self.on_detected:
            try:
                self.on_detected()
            except Exception:
                pass
