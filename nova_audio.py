"""nova_audio.py - Microphone noise-gate, echo/barge-in detection, and non-blocking speaker playback for Glass Nova."""
import array
import math
import os
import queue
import re
import threading
import time

try:
    import sounddevice as sd
    _SD_ERROR = ""
except Exception as _e:
    sd = None
    _SD_ERROR = f"{type(_e).__name__}: {_e}"

_emit_callback = None


def set_audio_emit_callback(fn):
    """Register the IPC emit function from pc_assistant.py without creating circular imports."""
    global _emit_callback
    _emit_callback = fn


def _emit(ev_type: str, **kwargs):
    if _emit_callback is not None:
        try:
            _emit_callback(ev_type, **kwargs)
        except Exception:
            pass


_mic_stream_state = {
    "active": False,
    "speaking": False,
    "mute_until": 0.0,
    "voice_hangover_until": 0.0,
    "silent_frames_sent": 0,
    "sensitivity": 68,
    "auto_nearest": False,
    "ambient_rms": 42.0,
    "last_level_emit_ts": 0.0,
    "playback_rms": 0.0,
    "barge_in_hits": 0,
    "barge_in_triggered": False,
}


def _sensitivity_to_gate_rms(sensitivity_pct: int) -> float:
    """Convert a 0..100% mic sensitivity percentage to a 16-bit PCM RMS noise-gate threshold.
    Higher sensitivity % -> lower RMS gate threshold (68% -> ~121 RMS, 100% -> 25 RMS, 0% -> 620 RMS)."""
    s = max(0, min(100, int(round(float(sensitivity_pct)))))
    norm = (100.0 - s) / 100.0
    return round(25.0 + (norm ** 1.6) * 595.0, 1)


def _gate_rms_to_sensitivity(gate_rms: float) -> int:
    """Convert a 16-bit PCM RMS noise-gate threshold back to a 0..100% mic sensitivity percentage."""
    clamped = max(25.0, min(620.0, float(gate_rms)))
    norm = ((clamped - 25.0) / 595.0) ** (1.0 / 1.6)
    return max(0, min(100, int(round(100.0 * (1.0 - norm)))))


MIC_NOISE_GATE_RMS = float(os.getenv("NOVA_MIC_NOISE_GATE_RMS", str(_sensitivity_to_gate_rms(68))))


def set_mic_sensitivity(sensitivity: int = None, auto_nearest: bool = None, calibrate_now: bool = False) -> dict:
    """Update microphone sensitivity (0..100%) and/or AUTO NEAREST voice isolation mode."""
    global MIC_NOISE_GATE_RMS
    if auto_nearest is not None:
        _mic_stream_state["auto_nearest"] = bool(auto_nearest)
    if sensitivity is not None:
        s = max(0, min(100, int(round(float(sensitivity)))))
        _mic_stream_state["sensitivity"] = s
        if auto_nearest is None:
            _mic_stream_state["auto_nearest"] = False
        MIC_NOISE_GATE_RMS = _sensitivity_to_gate_rms(s)
    if _mic_stream_state.get("auto_nearest") or calibrate_now:
        ambient = float(_mic_stream_state.get("ambient_rms", 42.0) or 42.0)
        target_gate = max(85.0, min(480.0, ambient * 2.2 + 75.0))
        MIC_NOISE_GATE_RMS = round(target_gate, 1)
        _mic_stream_state["sensitivity"] = _gate_rms_to_sensitivity(MIC_NOISE_GATE_RMS)
    cur_sens = int(_mic_stream_state.get("sensitivity", 68))
    cur_auto = bool(_mic_stream_state.get("auto_nearest", False))
    _emit(
        "mic_sensitivity",
        sensitivity=cur_sens,
        auto_nearest=cur_auto,
        gate_rms=round(MIC_NOISE_GATE_RMS, 1),
    )
    mode_str = " (AUTO NEAREST enabled)" if cur_auto else ""
    return {
        "ok": True,
        "verified": True,
        "sensitivity": cur_sens,
        "auto_nearest": cur_auto,
        "gate_rms": round(MIC_NOISE_GATE_RMS, 1),
        "message": f"Microphone sensitivity set to {cur_sens}%{mode_str}.",
    }


def mic_sensitivity_control(action: str = "set", value: int = None, amount: int = 10) -> dict:
    """Control Nova's Microphone Sensitivity & AUTO NEAREST noise-gate filter by voice or UI.
    Actions: 'set', 'increase', 'decrease', 'auto_nearest' ('auto', 'nearest'), 'manual', 'get'."""
    act = (action or "set").strip().lower().replace("-", "_").replace(" ", "_")
    cur = int(_mic_stream_state.get("sensitivity", 68))
    if act in ("get", "status", "current", "read"):
        auto = bool(_mic_stream_state.get("auto_nearest", False))
        mode = "AUTO NEAREST" if auto else "Manual"
        return {
            "ok": True,
            "verified": True,
            "sensitivity": cur,
            "auto_nearest": auto,
            "gate_rms": round(MIC_NOISE_GATE_RMS, 1),
            "message": f"Microphone sensitivity is {cur}% ({mode} mode).",
        }
    if act in ("auto_nearest", "auto", "nearest", "enable_auto", "calibrate"):
        return set_mic_sensitivity(auto_nearest=True, calibrate_now=True)
    if act in ("manual", "disable_auto", "auto_off"):
        return set_mic_sensitivity(sensitivity=cur, auto_nearest=False)
    if act in ("toggle_auto", "toggle_nearest"):
        new_auto = not bool(_mic_stream_state.get("auto_nearest", False))
        return set_mic_sensitivity(auto_nearest=new_auto, calibrate_now=new_auto)
    if act in ("up", "increase", "higher", "more"):
        delta = int(amount if amount is not None else (value if value is not None else 10))
        return set_mic_sensitivity(sensitivity=min(100, cur + max(1, delta)), auto_nearest=False)
    if act in ("down", "decrease", "lower", "less"):
        delta = int(amount if amount is not None else (value if value is not None else 10))
        return set_mic_sensitivity(sensitivity=max(0, cur - max(1, delta)), auto_nearest=False)
    target = value if value is not None else (amount if amount is not None else 68)
    return set_mic_sensitivity(sensitivity=int(target), auto_nearest=False)


def _is_mic_muted_for_echo() -> bool:
    """Return True while Nova's speaker output is active or within the post-speech acoustic echo tail,
    unless the user has triggered near-field barge-in."""
    if _mic_stream_state.get("barge_in_triggered"):
        return False
    if _mic_stream_state.get("speaking"):
        return True
    return time.monotonic() < float(_mic_stream_state.get("mute_until", 0.0) or 0.0)


def _check_near_field_barge_in(mic_rms: float) -> bool:
    """Adaptive echo-reference barge-in detector: compares incoming mic RMS against Nova's live speaker playback RMS."""
    play_rms = float(_mic_stream_state.get("playback_rms", 0.0) or 0.0)
    barge_thresh = max(380.0, MIC_NOISE_GATE_RMS * 2.8, play_rms * 0.42 + 180.0)
    if mic_rms >= barge_thresh:
        hits = int(_mic_stream_state.get("barge_in_hits", 0) or 0) + 1
        _mic_stream_state["barge_in_hits"] = hits
        if hits >= 3:
            _mic_stream_state["barge_in_triggered"] = True
            _mic_stream_state["speaking"] = False
            _mic_stream_state["mute_until"] = 0.0
            return True
    else:
        _mic_stream_state["barge_in_hits"] = 0
    return bool(_mic_stream_state.get("barge_in_triggered", False))


def _pcm16_rms(data: bytes) -> float:
    """Compute root-mean-square amplitude of 16-bit signed mono PCM audio."""
    if not data or len(data) < 2:
        return 0.0
    samples = array.array("h")
    n = len(data) // 2
    samples.frombytes(data[: n * 2])
    if not samples:
        return 0.0
    total = sum(s * s for s in samples)
    return math.sqrt(total / len(samples))


def _is_self_echo_transcription(user_text: str, recent_model_text: str, last_model_ts: float) -> bool:
    """Return True if user_text is an acoustic echo of Nova's own recent spoken output."""
    if not user_text or not recent_model_text:
        return False
    if (time.monotonic() - float(last_model_ts or 0.0)) > 12.0 and not _is_mic_muted_for_echo():
        return False
    u_words = re.findall(r"[a-z0-9]+", user_text.lower())
    m_words = re.findall(r"[a-z0-9]+", recent_model_text.lower())
    if not u_words or not m_words:
        return False
    u_joined = " ".join(u_words)
    m_joined = " ".join(m_words)
    if len(u_words) <= 8 and u_joined in m_joined:
        return True
    if len(u_words) >= 2:
        matched = sum(1 for w in u_words if w in m_words)
        if matched / len(u_words) >= 0.75 and len(u_words) <= 7:
            return True
    return False


class AudioPlayer:
    """Dedicated non-blocking speaker output stream on a background worker thread."""
    RATE = 24000

    def __init__(self, on_idle):
        self.on_idle = on_idle
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.stream = None
        self.thread = None
        self.failed = False
        self.epoch = 0
        self.pending_ends = 0
        self.need_restart = False
        self.closed = False

    def _open(self):
        if self.stream is not None:
            return True
        if self.failed or self.closed:
            return False
        if sd is None:
            self.failed = True
            _emit("error", text=f"Spoken replies are unavailable (audio library problem: {_SD_ERROR}). Text replies still work.")
            return False
        try:
            s = sd.RawOutputStream(samplerate=self.RATE, blocksize=0, channels=1, dtype="int16")
            s.start()
        except Exception as e:
            self.failed = True
            _emit("error", text=f"Spoken replies are unavailable (no usable speaker: {e}). Text replies still work.")
            return False
        self.stream = s
        self.thread = threading.Thread(target=self._run, name="nova-audio-out", daemon=True)
        self.thread.start()
        return True

    def write(self, data):
        with self.lock:
            if not self._open():
                return False
            _mic_stream_state["barge_in_triggered"] = False
            _mic_stream_state["barge_in_hits"] = 0
            _mic_stream_state["speaking"] = True
            _mic_stream_state["mute_until"] = time.monotonic() + 0.65
            self.q.put(("a", self.epoch, data))
        return True

    def end_turn(self):
        with self.lock:
            if self.stream is None:
                _mic_stream_state["speaking"] = False
                _mic_stream_state["playback_rms"] = 0.0
                _mic_stream_state["mute_until"] = time.monotonic() + 0.35
                return False
            self.pending_ends += 1
            self.q.put(("end", self.epoch, None))
        return True

    def interrupt(self):
        """Discard queued and buffered audio immediately (barge-in or mute)."""
        with self.lock:
            self.epoch += 1
            self.pending_ends = 0
            _mic_stream_state["speaking"] = False
            _mic_stream_state["playback_rms"] = 0.0
            _mic_stream_state["mute_until"] = time.monotonic() + 0.25
            try:
                while True:
                    self.q.get_nowait()
            except queue.Empty:
                pass
            s = self.stream
            if s is not None:
                self.need_restart = True
                try:
                    s.abort()
                except Exception:
                    pass

    def _restart_if_needed(self):
        with self.lock:
            if not self.need_restart:
                return
            self.need_restart = False
            try:
                self.stream.start()
            except Exception:
                pass

    def _run(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            kind, ep, data = item
            if ep != self.epoch:
                continue
            try:
                self._restart_if_needed()
                s = self.stream
                if kind == "a":
                    _mic_stream_state["speaking"] = True
                    _mic_stream_state["playback_rms"] = _pcm16_rms(data)
                    _mic_stream_state["mute_until"] = time.monotonic() + 0.65
                    s.write(data)
                    _mic_stream_state["mute_until"] = time.monotonic() + 0.55
                else:
                    s.stop()
                    s.start()
                    with self.lock:
                        if ep == self.epoch:
                            self.pending_ends = max(0, self.pending_ends - 1)
                            idle = self.pending_ends == 0
                        else:
                            idle = False
                    if idle:
                        _mic_stream_state["playback_rms"] = 0.0
                        _mic_stream_state["mute_until"] = time.monotonic() + 0.48
                        _mic_stream_state["speaking"] = False
                        self.on_idle()
            except Exception as e:
                if ep != self.epoch or self.closed:
                    continue
                _emit("log", text=f"[audio] playback error: {type(e).__name__}: {e}")
                with self.lock:
                    self.need_restart = True

    def close(self):
        with self.lock:
            self.closed = True
            self.epoch += 1
            _mic_stream_state["speaking"] = False
            _mic_stream_state["mute_until"] = 0.0
            s, t = self.stream, self.thread
            try:
                while True:
                    self.q.get_nowait()
            except queue.Empty:
                pass
            self.q.put(None)
        if s is not None:
            try:
                s.abort()
            except Exception:
                pass
        if t is not None:
            t.join(timeout=2)
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
        with self.lock:
            self.stream = None
