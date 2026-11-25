"""nova_voice_control.py - Independent voice/listening state model and deterministic voice-control command layer.

Supports all 4 independent interaction combinations across Offline, Online, and Hybrid modes:
  1. Mic ON  + Spoken Replies ON  -> Voice conversation
  2. Mic ON  + Spoken Replies OFF -> Nova hears you, replies in text only
  3. Mic OFF + Spoken Replies ON  -> You type, Nova speaks replies
  4. Mic OFF + Spoken Replies OFF -> Text-only conversation

Also keeps Wake-Word Monitoring clearly separated from Conversational Microphone Listening
so Nova never secretly reactivates full listening or falsely claims the mic is fully off
when wake-word monitoring is still active.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Dict, Optional


VOICE_CONTROL_PHRASES = [
    # Start / stop conversational listening
    "nova start listening", "hey nova start listening", "start listening",
    "turn on microphone", "enable microphone", "unmute microphone", "unmute mic",
    "nova stop listening", "hey nova stop listening", "stop listening",
    "turn off microphone", "disable microphone", "mute microphone", "mute mic",
    "pause listening", "stop listening to me",
    # Enable / disable spoken replies
    "nova enable voice replies", "hey nova enable voice replies", "enable voice replies",
    "enable spoken replies", "turn on voice replies", "turn on spoken replies",
    "unmute your voice", "nova unmute your voice", "resume speaking", "nova resume speaking",
    "resume voice", "speak replies", "turn on voice output",
    "nova mute your voice", "hey nova mute your voice", "mute your voice",
    "mute yourself", "disable voice replies", "disable spoken replies",
    "turn off voice replies", "turn off spoken replies", "turn off voice output",
    "silent mode", "respond in text", "reply in text",
    # Immediate speech interruption (does not disable future spoken replies)
    "nova stop speaking", "hey nova stop speaking", "stop speaking",
    "stop talking", "nova stop talking", "be quiet", "cancel speech",
    # Combined shortcuts
    "nova switch to text mode", "hey nova switch to text mode", "switch to text mode",
    "text mode", "text only mode",
    "nova switch to voice mode", "hey nova switch to voice mode", "switch to voice mode",
    "voice mode", "voice conversation mode",
]


def _normalize_control_text(text: str) -> str:
    clean = re.sub(r"[^\w\s']", " ", (text or "").lower())
    clean = " ".join(clean.split())
    # Strip leading polite / wake prefixes ("hey nova ...", "nova ...", "please ...")
    for _ in range(2):
        clean = re.sub(r"^(?:hey\s+nova|ok\s+nova|nova|please|can\s+you|could\s+you)\s+", "", clean).strip()
    return clean


def match_voice_control_command(text: str) -> Optional[Dict[str, str]]:
    """Deterministically detect voice/chat control commands without needing an AI response.

    Returns a dict:
      {"control": "<action>", "label": "<human label>", "ack": "<short confirmation>"}
    or None if text is a normal user message or tool request.
    """
    clean = _normalize_control_text(text)
    if not clean:
        return None

    # 1. Switch to Text Mode (Mic OFF + Spoken Replies OFF)
    if clean in (
        "switch to text mode", "text mode", "text only mode", "go to text mode",
        "use text mode", "text only", "enable text mode",
    ):
        return {
            "control": "text_mode",
            "label": "Switch to Text Mode",
            "ack": "Switched to text mode (microphone listening off, spoken replies off).",
        }

    # 2. Switch to Voice Mode (Mic ON + Spoken Replies ON)
    if clean in (
        "switch to voice mode", "voice mode", "voice conversation mode", "go to voice mode",
        "use voice mode", "enable voice mode",
    ):
        return {
            "control": "voice_mode",
            "label": "Switch to Voice Mode",
            "ack": "Switched to voice mode (microphone listening on, spoken replies on).",
        }

    # 3. Stop Speaking immediately (interrupt current speech WITHOUT turning off future spoken replies)
    if clean in (
        "stop speaking", "stop talking", "be quiet", "hush", "shh", "shut up",
        "cancel speech", "silence now", "stop voice output now",
    ):
        return {
            "control": "stop_speaking",
            "label": "Stop Speaking",
            "ack": "Stopped speaking.",
        }

    # 4. Start conversational microphone listening (independent of spoken replies)
    if clean in (
        "start listening", "resume listening", "listen to me", "listen now",
        "turn on microphone", "turn on mic", "enable microphone", "enable mic",
        "unmute microphone", "unmute mic", "microphone on", "mic on", "listening on",
    ):
        return {
            "control": "start_listening",
            "label": "Start Listening",
            "ack": "Microphone listening is now on.",
        }

    # 5. Stop conversational microphone listening (independent of spoken replies; keeps chat & session alive)
    if clean in (
        "stop listening", "pause listening", "stop listening to me", "don't listen", "do not listen",
        "turn off microphone", "turn off mic", "disable microphone", "disable mic",
        "mute microphone", "mute mic", "microphone off", "mic off", "listening off",
    ):
        return {
            "control": "stop_listening",
            "label": "Stop Listening",
            "ack": "Microphone listening is now off. Chat and your session remain open.",
        }

    # 6. Enable spoken replies (independent of microphone listening)
    if clean in (
        "enable voice replies", "enable spoken replies", "turn on voice replies", "turn on spoken replies",
        "unmute your voice", "unmute voice", "unmute yourself", "resume speaking", "resume voice",
        "speak replies", "spoken replies on", "voice replies on", "turn on voice output", "enable voice output",
    ):
        return {
            "control": "enable_spoken_replies",
            "label": "Enable Spoken Replies",
            "ack": "Spoken replies are now enabled.",
        }

    # 7. Disable spoken replies (independent of microphone listening; stops active speech immediately)
    if clean in (
        "mute your voice", "mute voice", "mute yourself", "disable voice replies", "disable spoken replies",
        "turn off voice replies", "turn off spoken replies", "turn off voice output", "disable voice output",
        "spoken replies off", "voice replies off", "silent mode", "respond in text", "reply in text",
        "text replies only", "stop voice replies",
    ):
        return {
            "control": "disable_spoken_replies",
            "label": "Mute Spoken Replies",
            "ack": "Spoken replies are now muted. I will respond in text.",
        }

    return None


class VoiceControlState:
    """Thread-safe state manager for microphone listening, spoken replies, wake-word monitoring, and AI mode.

    Preserves user choices across Online <-> Offline <-> Hybrid backend transitions.
    """

    def __init__(
        self,
        mic_listening: bool = False,
        spoken_replies: bool = True,
        wake_word_enabled: bool = True,
        text_mode_locked: bool = False,
    ):
        self._lock = threading.RLock()
        self.text_mode_locked: bool = bool(text_mode_locked)
        self.mic_listening: bool = False if self.text_mode_locked else bool(mic_listening)
        self.spoken_replies: bool = False if self.text_mode_locked else bool(spoken_replies)
        self.wake_word_enabled: bool = bool(wake_word_enabled)
        self.wake_word_active: bool = False
        self.speaking: bool = False
        self.ai_mode: str = "hybrid"       # offline | online | hybrid
        self.ai_provider: str = "Local"    # Gemini Live | Ollama (<model>) | Local Commands
        self.user_manually_disabled_mic: bool = bool(self.text_mode_locked)

    def enter_text_mode(self) -> Dict[str, Any]:
        """Lock into Text-Only Mode: mic OFF, spoken replies OFF, wake-word reactivation paused."""
        with self._lock:
            self.text_mode_locked = True
            self.mic_listening = False
            self.spoken_replies = False
            self.speaking = False
            self.wake_word_active = False
            self.user_manually_disabled_mic = True
            return self.snapshot()

    def exit_text_mode(self, mic_on: bool = True, spoken_on: bool = True) -> Dict[str, Any]:
        """Explicitly leave Text Mode (only on manual user action)."""
        with self._lock:
            self.text_mode_locked = False
            self.mic_listening = bool(mic_on)
            self.spoken_replies = bool(spoken_on)
            self.user_manually_disabled_mic = not self.mic_listening
            if self.mic_listening:
                self.wake_word_active = False
            return self.snapshot()

    def set_mic_listening(self, on: bool, manual: bool = False) -> Dict[str, Any]:
        with self._lock:
            self.mic_listening = bool(on)
            if self.mic_listening:
                self.text_mode_locked = False
                self.user_manually_disabled_mic = False
                # Conversational mic owns the audio input while ON
                self.wake_word_active = False
            elif manual:
                self.user_manually_disabled_mic = True
            return self.snapshot()

    def set_spoken_replies(self, on: bool) -> Dict[str, Any]:
        with self._lock:
            self.spoken_replies = bool(on)
            if self.spoken_replies:
                self.text_mode_locked = False
            else:
                self.speaking = False
            return self.snapshot()

    def set_speaking(self, active: bool) -> Dict[str, Any]:
        with self._lock:
            self.speaking = bool(active)
            return self.snapshot()

    def set_wake_word(self, enabled: Optional[bool] = None, active: Optional[bool] = None) -> Dict[str, Any]:
        with self._lock:
            if enabled is not None:
                self.wake_word_enabled = bool(enabled)
                if not self.wake_word_enabled:
                    self.wake_word_active = False
            if active is not None:
                self.wake_word_active = bool(active) and self.wake_word_enabled and not self.mic_listening and not self.text_mode_locked
            return self.snapshot()

    def set_ai_backend(self, mode: str, provider: str) -> Dict[str, Any]:
        """Update AI mode/provider WITHOUT resetting text_mode_locked, mic_listening, or spoken_replies."""
        with self._lock:
            m = (mode or "hybrid").strip().lower()
            if m not in ("offline", "online", "hybrid"):
                m = "hybrid"
            self.ai_mode = m
            self.ai_provider = str(provider or "Local").strip()
            return self.snapshot()

    def interaction_mode_label(self) -> str:
        with self._lock:
            if self.text_mode_locked:
                return "Text-Only Mode"
            if self.mic_listening and self.spoken_replies:
                return "Voice Conversation"
            if self.mic_listening and not self.spoken_replies:
                return "Mic On • Text Replies"
            if not self.mic_listening and self.spoken_replies:
                return "Typed Chat • Spoken Replies"
            return "Text-Only Mode"

    def privacy_mic_status(self) -> str:
        """Honest microphone status that never falsely claims mic is fully off if wake-word monitoring is active."""
        with self._lock:
            if self.text_mode_locked:
                return "Text Mode is active: conversational microphone and wake-word reactivation are paused."
            if self.mic_listening:
                return "Conversational microphone listening is ON."
            if self.wake_word_enabled and self.wake_word_active:
                return "Conversational listening is OFF, but local 'Hey Nova' wake-word monitoring is still active."
            if self.wake_word_enabled:
                return "Conversational listening is OFF (wake-word monitoring configured ON)."
            return "All microphone monitoring is OFF (use the Mic button to reactivate)."

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "text_mode_locked": self.text_mode_locked,
                "mic_listening": self.mic_listening,
                "spoken_replies": self.spoken_replies,
                "wake_word_enabled": self.wake_word_enabled,
                "wake_word_active": self.wake_word_active,
                "speaking": self.speaking,
                "ai_mode": self.ai_mode,
                "ai_provider": self.ai_provider,
                "user_manually_disabled_mic": self.user_manually_disabled_mic,
                "interaction_mode": self.interaction_mode_label(),
                "privacy_mic_status": self.privacy_mic_status(),
            }
