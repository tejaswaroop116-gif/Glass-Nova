"""Step-by-step visual walkthrough model for Nova.

The UI layer can render each instruction as a card/highlight. The user advances
manually. This module never clicks, types, or changes application state.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class WalkthroughStep:
    title: str
    instruction: str
    target_label: Optional[str] = None
    caution: Optional[str] = None


class Walkthrough:
    def __init__(self, title: str, steps: list[WalkthroughStep]):
        if not steps:
            raise ValueError("A walkthrough needs at least one step.")
        self.title = title
        self.steps = steps
        self.index = 0
        self.active = True
        self.paused = False

    @property
    def current(self) -> Optional[WalkthroughStep]:
        if not self.active:
            return None
        return self.steps[self.index]

    @property
    def progress(self) -> tuple[int, int]:
        return self.index + 1, len(self.steps)

    def next(self) -> Optional[WalkthroughStep]:
        if not self.active:
            return None
        if self.index + 1 >= len(self.steps):
            self.active = False
            return None
        self.index += 1
        return self.current

    def back(self) -> Optional[WalkthroughStep]:
        if self.active:
            self.index = max(0, self.index - 1)
        return self.current

    def pause(self) -> None:
        if self.active:
            self.paused = True

    def resume(self) -> None:
        if self.active:
            self.paused = False

    def stop(self) -> None:
        self.active = False
        self.paused = False


def walkthrough_from_plan(title: str, plan: list[dict]) -> Walkthrough:
    """Convert AI-generated guidance into display-only steps."""
    steps = []
    for item in plan:
        steps.append(WalkthroughStep(
            title=str(item.get("title") or "Next step"),
            instruction=str(item.get("instruction") or item.get("description") or ""),
            target_label=item.get("target_label"),
            caution=item.get("caution"),
        ))
    return Walkthrough(title, steps)
