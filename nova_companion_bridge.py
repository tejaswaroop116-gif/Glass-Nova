"""Bridge for Nova's existing floating companion and wake-word listener.

The current UI already owns the companion widget and WakeWordListener. Pass
callbacks from ui.py rather than creating a second orb or microphone listener.
"""
from __future__ import annotations
from typing import Callable, Optional


class CompanionBridge:
    def __init__(
        self,
        show_companion: Callable[[], None],
        hide_companion: Callable[[], None],
        bring_main_window_forward: Callable[[], None],
    ):
        self._show = show_companion
        self._hide = hide_companion
        self._bring_forward = bring_main_window_forward
        self.visible = False

    def show(self) -> None:
        self._show()
        self.visible = True

    def hide(self) -> None:
        self._hide()
        self.visible = False

    def toggle(self) -> None:
        self.hide() if self.visible else self.show()

    def on_wake_word(self) -> None:
        """Connect to the existing WakeWordListener(on_detected=...)."""
        self.show()
        self._bring_forward()

    def attach_wake_listener(self, listener) -> None:
        """Attach callbacks to an already-created WakeWordListener."""
        listener.on_detected = self.on_wake_word
