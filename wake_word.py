"""Offline, local wake-word listener for Glass Nova.

Uses Vosk with a constrained grammar FST to detect 'Hey Nova' locally with ultra-low CPU.
Coordinates safe microphone handover with Gemini Live to prevent audio hardware conflicts.
No background audio is ever recorded or uploaded.
"""
import os, sys, json, time, threading
from typing import Optional, Callable
import sounddevice as sd

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
        self._model = None
        self._recognizer = None
        self._last_trigger_time = 0.0
        self._cooldown_seconds = 3.0
        self.status = "uninitialized"  # "listening" | "paused" | "error" | "stopped"

    def _notify_status(self, new_status: str):
        self.status = new_status
        if self.on_status_change:
            try:
                self.on_status_change(new_status)
            except Exception:
                pass

    def _init_model(self):
        if self._model is not None:
            return True
        try:
            import vosk
            vosk.SetLogLevel(-1)
            # Use cached model or default small English model
            self._model = vosk.Model(model_name="vosk-model-small-en-us-0.15")
            # Restrict grammar to the keyword and silence/unknown
            grammar = json.dumps(["hey nova", "nova", "hey", "[unk]"])
            self._recognizer = vosk.KaldiRecognizer(self._model, 16000, grammar)
            return True
        except Exception as e:
            self._notify_status("error")
            return False

    def start(self):
        """Starts the wake word background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._paused_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="NovaWakeWord", daemon=True)
        self._thread.start()

    def pause(self):
        """Temporarily release microphone (e.g. while Gemini Live is speaking/listening)."""
        self._paused_event.set()

    def resume(self):
        """Resume local wake-word detection."""
        self._paused_event.clear()

    def stop(self):
        """Completely stop listener and release all resources."""
        self._stop_event.set()
        self._paused_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._notify_status("stopped")

    def _run_loop(self):
        if not self._init_model():
            return

        while not self._stop_event.is_set():
            # Wait if paused
            if self._paused_event.is_set():
                self._notify_status("paused")
                while self._paused_event.is_set() and not self._stop_event.is_set():
                    time.sleep(0.15)
                if self._stop_event.is_set():
                    break

            self._notify_status("listening")
            try:
                from media_ducking import ducker
                ducker.start()
                ducker.set_listening(True)
            except Exception:
                pass
            q = []
            q_lock = threading.Lock()

            def audio_cb(indata, frames, time_info, status):
                if not self._paused_event.is_set() and not self._stop_event.is_set():
                    with q_lock:
                        if len(q) < 10:
                            q.append(bytes(indata))

            wake_hit = False
            try:
                with sd.RawInputStream(samplerate=16000, blocksize=2048, channels=1,
                                       dtype="int16", callback=audio_cb,
                                       device=self.device_index):
                    while not self._paused_event.is_set() and not self._stop_event.is_set():
                        data = None
                        with q_lock:
                            if q:
                                data = q.pop(0)

                        if not data:
                            time.sleep(0.03)
                            continue

                        if self._recognizer.AcceptWaveform(data):
                            res = json.loads(self._recognizer.Result() or "{}")
                            text = res.get("text", "").lower().strip()
                            if self._check_wake_text(text):
                                wake_hit = True
                                break
                        else:
                            partial = json.loads(self._recognizer.PartialResult() or "{}")
                            p_text = partial.get("partial", "").lower().strip()
                            if self._check_wake_text(p_text):
                                self._recognizer.Reset()
                                wake_hit = True
                                break

            except Exception as e:
                time.sleep(0.5)

            if wake_hit and not self._stop_event.is_set():
                # RawInputStream context is now exited and audio device is 100% released!
                self._trigger_wake()

        self._notify_status("stopped")

    def _check_wake_text(self, text: str) -> bool:
        if not text:
            return False
        clean = text.lower().replace(",", " ").replace(".", " ").replace("!", " ").strip()
        tokens = [t for t in clean.split() if t != "[unk]"]
        if not tokens:
            return False
        joined = " ".join(tokens)
        if "hey nova" in joined or tokens[0] == "nova":
            now = time.monotonic()
            if now - self._last_trigger_time >= self._cooldown_seconds:
                self._last_trigger_time = now
                return True
        return False

    def _trigger_wake(self):
        # Automatically pause wake-word microphone capture to handover to Gemini Live
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
