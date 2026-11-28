"""Sprite-based floating companion for NOVA (PySide6 only).

Asset setup:
    <project>/assets/nova_pet/idle/idle_01.png
    <project>/assets/nova_pet/smile/smile_01.png
    ...

The loader also accepts the extracted ZIP's extra
`Nova_Robot_Animation_Frames/` parent folder and the old frame_00.png layout.

Behaviour (same idea as Cloudy)
-------------------------------
* Cursor moving        -> the pet WALKS toward it (RUNS when it is far away) and
                          stops next to it.
* Cursor still         -> idle.  Now and then it does a little flourish
                          (smile / think / draw / happy).
* Nobody using the PC  -> falls asleep after SLEEP_AFTER seconds (sit, yawn, zzz).
                          Moving the mouse wakes it up (surprised) and it
                          comes over again.
* Poking (left click)  -> 1st poke: surprised, 2nd: frustrated,
                          3rd and more: ANGRY (and it shakes).  Poke counter
                          resets after POKE_RESET seconds of being left alone.
* Double click         -> happy + opens NOVA.
* Drag and drop        -> surprised while carried, then idle again.
* Right-click menu     -> follow-cursor toggle, flip direction, sleep now,
                          size, play any animation.
"""
from __future__ import annotations

import math
import random
import time
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPoint, Signal, QEvent
from PySide6.QtGui import QPainter, QPixmap, QCursor, QGuiApplication
from PySide6.QtWidgets import QWidget, QMenu

ROOT = (
    Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
ASSET_DIR = ROOT / "assets" / "nova_pet"


class PetWidget(QWidget):
    open_requested = Signal()
    exit_requested = Signal()
    hidden = Signal()

    # Legacy frame IDs retained for compatibility with the old sprite sheet.
    IDLE = [0, 1, 2, 3]
    BLINK = [4]
    TURN = [5, 6, 7, 8, 9, 10, 11, 12, 13]
    WAVE = [22, 23, 24, 25]
    WALK = [34, 35, 36, 37, 38]
    PAINT = [51, 52, 53, 54, 55, 56]

    ANIMATION_FPS = {
        "idle": 5, "smile": 5, "walk": 8, "run": 10,
        "sad": 5, "angry": 5, "cry": 5, "surprised": 5,
        "think": 5, "sleep": 4, "laugh": 7, "talking": 10, "frustrated": 5,
        "happy": 6, "work_type": 6, "draw_write": 6,
    }

    # ------------------------------------------------------------------
    # BEHAVIOUR SETTINGS (edit here to tune the pet)
    # ------------------------------------------------------------------
    SLEEP_AFTER = 5.0          # seconds without cursor movement/poking -> sleep
    POKE_RESET = 3.0            # seconds of calm before the poke counter resets
    WALK_SPEED = 140            # pixels / second at size 1.0
    RUN_SPEED = 330             # pixels / second at size 1.0
    RUN_DISTANCE = 520          # farther than this (x size) -> run
    WALK_DISTANCE = 320         # closer than this (x size) -> back to walking
    STOP_FACTOR = 0.60          # stop this many window-sizes away from the cursor
    DRAG_THRESHOLD = 6          # pixels of mouse movement that count as a drag
    IDLE_FLOURISHES = ("smile", "think", "draw_write", "happy")
    FLOURISH_EVERY = (25.0, 50.0)   # random seconds between idle flourishes

    # The walk / run artwork faces LEFT. Set to False if your frames face right.
    # (The right-click menu "Flip walk direction" toggles this without editing.)
    NATIVE_FACES_LEFT = True

    def __init__(self, settings=None):
        super().__init__(
            None,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.settings = settings
        self.setWindowTitle("NOVA Painter Pet")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)

        self.pet_scale = 1.0
        self.pet_follow = True          # walk / run to the cursor
        self.pet_flip = False           # flip walk / run direction
        try:
            if settings is not None:
                self.pet_scale = max(0.65, min(2.2, float(settings.value("pet_scale", 1.0))))
        except (TypeError, ValueError):
            self.pet_scale = 1.0
        if settings is not None:
            self.pet_follow = str(settings.value("pet_follow", "true")).lower() != "false"
            self.pet_flip = str(settings.value("pet_flip", "false")).lower() == "true"
        self.resize(int(220 * self.pet_scale), int(230 * self.pet_scale))

        # New named animation folders, e.g. assets/nova_pet/walk/*.png.
        self.animations: dict[str, list[QPixmap]] = {}
        self._load_animation_folders()

        # Backward compatibility with the previous flat frame_00.png atlas.
        self.frames: dict[int, QPixmap] = {}
        self._load_legacy_frames()

        self.assistant_state = "idle"
        self.current_animation = "idle"
        self.current_frames: list[QPixmap] = []
        self.sequence = self.IDLE  # Legacy attribute retained for integrations.
        self.frame_index = 0
        self.frame_rate = 0.20
        self.animation_elapsed = 0.0
        self.animation_loop = True
        self.animation_loop_start = 0
        self.animation_deadline = 0.0
        self.elapsed = 0.0
        self.last_tick = time.monotonic()
        self.next_blink = self.last_tick + random.uniform(3.0, 5.0)
        self.blink_until = 0.0
        self.reaction_until = 0.0
        self.bounce = 0.0
        self.dragging = False           # left button is held down on the pet
        self.drag_offset = QPoint()
        self._placed = False
        self.follow_anchor = QPoint()   # kept for compatibility
        self.follow_offset = QPoint()   # kept for compatibility

        # --- behaviour state (all prefixed _pt_ so subclasses never clash) ---
        now = self.last_tick
        self._pt_mode = "idle"          # idle | walk | run | sleep | action | drag
        self._pt_chasing = False
        self._pt_facing_right = False
        self._pt_last_cursor = QCursor.pos()
        self._pt_last_activity = now
        self._pt_next_flourish = now + random.uniform(*self.FLOURISH_EVERY)
        self._pt_action_interruptible = False
        self._pt_poke_count = 0
        self._pt_last_poke = 0.0
        self._pt_shake_until = 0.0
        self._pt_drag_active = False
        self._pt_press_global = QPoint()
        self._pt_ignore_release = False

        self._activate_initial_animation()

        self.timer = QTimer(self)
        self.timer.setInterval(33)  # ~30 FPS while visible.
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ assets
    def _asset_roots(self):
        """Support both direct extraction and a nested ZIP root folder."""
        candidates = [ASSET_DIR, ASSET_DIR / "Nova_Robot_Animation_Frames"]
        return [path for path in candidates if path.is_dir()]

    def _load_animation_folders(self):
        for root in self._asset_roots():
            for name in self.ANIMATION_FPS:
                folder = root / name
                if not folder.is_dir():
                    continue
                pixmaps = []
                for path in sorted(folder.glob("*.png")):
                    pix = QPixmap(str(path))
                    if not pix.isNull():
                        pixmaps.append(pix)
                if pixmaps:
                    self.animations[name] = pixmaps

    def _load_legacy_frames(self):
        for root in self._asset_roots():
            for i in range(57):
                path = root / f"frame_{i:02d}.png"
                pix = QPixmap(str(path))
                if not pix.isNull():
                    self.frames[i] = pix

    def _activate_initial_animation(self):
        if "idle" in self.animations:
            self.play_animation("idle")
        elif self.frames:
            self._play(self.IDLE, 0.20)
        else:
            self.current_frames = []

    def available_animations(self):
        """Return the names of successfully loaded animation folders."""
        return sorted(self.animations)

    # -------------------------------------------------------------- appearance
    def set_pet_scale(self, scale):
        scale = max(0.65, min(2.2, float(scale)))
        centre = self.frameGeometry().center()
        self.pet_scale = scale
        self.resize(int(220 * scale), int(230 * scale))
        self.move(centre - QPoint(self.width() // 2, self.height() // 2))
        if self.settings is not None:
            self.settings.setValue("pet_scale", scale)

    def increase_size(self):
        self.set_pet_scale(self.pet_scale + 0.12)

    def decrease_size(self):
        self.set_pet_scale(self.pet_scale - 0.12)

    def event(self, event):
        try:
            if (
                event.type() == QEvent.Type.NativeGesture
                and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
            ):
                self.set_pet_scale(self.pet_scale * (1.0 + float(event.value())))
                event.accept()
                return True
        except (AttributeError, TypeError, ValueError):
            pass
        return super().event(event)

    # -------------------------------------------------------------- animations
    def play_animation(self, name, fps=None, loop=True, duration=None, loop_start=0):
        """Play a named animation folder (e.g. pet.play_animation('walk')).

        `duration` is optional and is mainly useful for short reactions.
        `loop_start` is the frame the loop jumps back to (lets 'sleep' play its
        yawn once and then keep looping only the zzz frames).
        Unknown names safely fall back to idle.
        """
        name = str(name).strip().lower()
        if name not in self.animations:
            if name in self.ANIMATION_FPS:
                # Missing folder: keep current appearance rather than crash.
                name = "idle" if "idle" in self.animations else name
            else:
                name = "idle"
        pixmaps = self.animations.get(name, [])
        if not pixmaps:
            # The older flat sprite layout remains usable as a fallback.
            legacy = {
                "idle": self.IDLE, "walk": self.WALK,
                "draw_write": self.PAINT, "work_type": self.PAINT,
                "think": self.TURN, "happy": self.WAVE,
                "smile": self.WAVE, "laugh": self.WAVE,
            }.get(name, self.IDLE)
            self._play(legacy, 0.16)
        else:
            self.current_animation = name
            self.current_frames = pixmaps
            self.sequence = []
            self.frame_index = 0
            self.animation_elapsed = 0.0
            selected_fps = fps or self.ANIMATION_FPS.get(name, 6)
            self.frame_rate = 1.0 / max(1, float(selected_fps))
            self.animation_loop = bool(loop)
            self.animation_loop_start = max(0, min(int(loop_start), len(pixmaps) - 1))
        self.animation_deadline = time.monotonic() + float(duration) if duration else 0.0
        self.update()

    def _play(self, sequence, rate=0.18):
        """Play old numeric frame IDs; retained for compatibility."""
        self.sequence = list(sequence)
        self.current_frames = [self.frames[i] for i in self.sequence if i in self.frames]
        self.current_animation = "legacy"
        self.frame_index = 0
        self.frame_rate = max(0.02, float(rate))
        self.animation_elapsed = 0.0
        self.animation_loop = True
        self.animation_loop_start = 0
        self.animation_deadline = 0.0
        self.update()

    def _busy_state(self):
        return self.assistant_state in ("thinking", "speaking")

    def set_assistant_state(self, state):
        """Connect NOVA's normal states to appropriate pet animations."""
        state = str(state).strip().lower()
        new = state if state in {"idle", "listening", "thinking", "speaking"} else "idle"
        old = self.assistant_state
        self.assistant_state = new
        if new == old:
            return
        now = time.monotonic()
        self._pt_last_activity = now
        if self._busy_state():
            # Thinking / speaking: stand still and show the matching animation.
            self._pt_chasing = False
            self._pt_mode = "idle"
            self._pt_action_interruptible = False
            if new == "thinking":
                self.play_animation("think", loop=True)
            else:
                # Prefer the dedicated mouth-sync talking loop; keep laugh as a
                # compatibility fallback if its asset folder is not installed.
                speaking_animation = "talking" if "talking" in self.animations else "laugh"
                self.play_animation(speaking_animation, loop=True)
        elif old in ("thinking", "speaking"):
            self._pt_set_idle(now)
        elif self._pt_mode == "sleep":
            self._pt_start_action("surprised", 0.9)

    def react(self, duration=1.5):
        self.reaction_until = time.monotonic() + max(0.1, float(duration))
        self.bounce = 1.0
        self._pt_start_action("happy", duration)

    # Handy triggers for Nova's event handlers.
    def show_smile(self, duration=1.5):
        self._pt_start_action("smile", duration)

    def show_happy(self, duration=1.5):
        self._pt_start_action("happy", duration)

    def show_sad(self, duration=2.0):
        self._pt_start_action("sad", duration)

    def show_angry(self, duration=2.0):
        self._pt_start_action("angry", duration)

    def show_cry(self, duration=2.0):
        self._pt_start_action("cry", duration)

    def walk(self):
        self._pt_mode = "walk"
        self.play_animation("walk", loop=True)

    def run(self):
        self._pt_mode = "run"
        self.play_animation("run", loop=True)

    def think(self):
        self._pt_mode = "idle"
        self.play_animation("think", loop=True)

    def sleep(self):
        self._pt_go_sleep()

    def work(self):
        self._pt_mode = "idle"
        self.play_animation("work_type", loop=True)

    def draw(self):
        self._pt_mode = "idle"
        self.play_animation("draw_write", loop=True)

    def set_follow_cursor(self, enabled):
        self.pet_follow = bool(enabled)
        if not self.pet_follow:
            self._pt_chasing = False
        if self.settings is not None:
            self.settings.setValue("pet_follow", "true" if self.pet_follow else "false")

    def set_flip_direction(self, enabled):
        self.pet_flip = bool(enabled)
        if self.settings is not None:
            self.settings.setValue("pet_flip", "true" if self.pet_flip else "false")
        self.update()

    # ------------------------------------------------------- behaviour helpers
    def _pt_start_action(self, name, duration, interruptible=False, fps=None):
        """Play a timed animation that blocks cursor behaviour until it ends."""
        self._pt_mode = "action"
        self._pt_action_interruptible = bool(interruptible)
        self._pt_chasing = self._pt_chasing and self.pet_follow
        self.play_animation(name, fps=fps, loop=True, duration=duration)
        self._pt_last_activity = time.monotonic()

    def _pt_set_idle(self, now=None):
        now = time.monotonic() if now is None else now
        self._pt_mode = "idle"
        self._pt_action_interruptible = False
        self.play_animation("idle", loop=True)
        self.next_blink = now + random.uniform(3.0, 5.0)

    def _pt_go_sleep(self):
        self._pt_mode = "sleep"
        self._pt_chasing = False
        self._pt_action_interruptible = False
        count = len(self.animations.get("sleep", []))
        # Play sit -> yawn once, then keep looping only the last (zzz) frames.
        loop_start = max(0, count - 4) if count > 4 else 0
        self.play_animation("sleep", fps=3, loop=True, loop_start=loop_start)

    def _pt_wake(self, now):
        self._pt_last_activity = now
        self._pt_start_action("surprised", 0.9)
        self._pt_chasing = self.pet_follow

    def _pt_poke(self):
        """Left click without dragging: escalate surprised -> frustrated -> angry."""
        now = time.monotonic()
        if now - self._pt_last_poke > self.POKE_RESET:
            self._pt_poke_count = 0
        self._pt_poke_count += 1
        self._pt_last_poke = now
        self._pt_last_activity = now
        self._pt_chasing = False
        self.bounce = 1.0
        if self._pt_mode == "sleep":
            self._pt_poke_count = 1
            self._pt_start_action("surprised", 1.0)
        elif self._pt_poke_count == 1:
            self._pt_start_action("surprised", 1.0)
        elif self._pt_poke_count == 2:
            self._pt_start_action("frustrated", 1.6)
        else:
            self._pt_start_action("angry", 2.6)
            self._pt_shake_until = now + 0.6

    def _pt_facing_left_art(self):
        return self.NATIVE_FACES_LEFT != self.pet_flip

    # ------------------------------------------------------------------- window
    def show_bot(self):
        if not self._placed:
            self._placed = True
            screen = QGuiApplication.primaryScreen()
            if screen:
                geometry = screen.availableGeometry()
                self.move(
                    geometry.right() - self.width() - 28,
                    geometry.bottom() - self.height() - 45,
                )
                if self.settings is not None:
                    try:
                        x, y = map(int, str(self.settings.value("pet_pos", "")).split(","))
                        self.move(x, y)
                    except (ValueError, TypeError):
                        pass
        self.follow_anchor = self.pos()
        self.show()
        self.raise_()
        self.ensure_on_screen()

    def ensure_on_screen(self):
        screens = QGuiApplication.screens()
        if not screens:
            return
        centre = self.frameGeometry().center()
        if not any(screen.availableGeometry().contains(centre) for screen in screens):
            geometry = QGuiApplication.primaryScreen().availableGeometry()
            self.move(geometry.right() - self.width() - 24,
                      geometry.bottom() - self.height() - 35)

    def showEvent(self, event):
        now = time.monotonic()
        self.last_tick = now
        self._pt_last_cursor = QCursor.pos()
        self._pt_last_activity = now
        self._pt_chasing = False
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.timer.stop()
        if self.settings is not None:
            self.settings.setValue("pet_pos", f"{self.x()},{self.y()}")
        event.accept()

    # ---------------------------------------------------------------- animation
    def _tick(self):
        now = time.monotonic()
        dt = min(0.1, max(0.0, now - self.last_tick))
        self.last_tick = now
        self.elapsed += dt
        self.animation_elapsed += dt

        cursor = QCursor.pos()
        moved = (cursor - self._pt_last_cursor).manhattanLength()
        self._pt_last_cursor = cursor

        self._pt_behaviour(now, dt, cursor, moved)

        self.bounce *= math.exp(-dt * 3.8)

        # Advance the selected animation without restarting it on every tick.
        if self.current_frames and self.animation_elapsed >= self.frame_rate:
            steps = int(self.animation_elapsed / self.frame_rate)
            self.animation_elapsed -= steps * self.frame_rate
            next_index = self.frame_index + steps
            count = len(self.current_frames)
            if next_index >= count:
                if self.animation_loop:
                    start = min(self.animation_loop_start, count - 1)
                    self.frame_index = start + (next_index - start) % (count - start)
                else:
                    self.frame_index = count - 1
            else:
                self.frame_index = next_index

        # Timed reactions end -> back to the assistant's current state / idle.
        if self.animation_deadline and now >= self.animation_deadline:
            self.animation_deadline = 0.0
            self._restore_assistant_animation()
            self.reaction_until = 0.0

        # Blink only if an actual blink animation folder exists.
        if ("blink" in self.animations and self.assistant_state == "idle"
                and self._pt_mode == "idle"
                and now >= self.next_blink and now >= self.reaction_until):
            self.blink_until = now + 0.16
            self.next_blink = now + random.uniform(3.0, 5.5)
            self.play_animation("blink", loop=False, duration=0.16)
        elif self.blink_until and now >= self.blink_until:
            self.blink_until = 0.0
            self._restore_assistant_animation()

        self.update()

    def _pt_behaviour(self, now, dt, cursor, moved):
        """Cursor following, idle flourishes and sleeping."""
        if moved >= 3:
            self._pt_last_activity = now

        if self._pt_drag_active:
            return
        if self._busy_state():
            self._pt_chasing = False
            self._pt_last_activity = now
            return

        mode = self._pt_mode
        if mode == "action":
            if self._pt_action_interruptible and moved >= 6:
                self.animation_deadline = 0.0
                self._pt_set_idle(now)          # cancel the little flourish
                mode = "idle"
            else:
                return

        if moved >= 3:
            if mode == "sleep":
                self._pt_wake(now)
                return
            if self.pet_follow:
                self._pt_chasing = True

        if mode in ("idle", "walk", "run") and self._pt_chasing and self.pet_follow:
            self._pt_chase(now, dt, cursor)
            return
        if mode in ("walk", "run"):
            self._pt_set_idle(now)              # follow switched off
            mode = "idle"

        if mode == "idle":
            if now - self._pt_last_activity > self.SLEEP_AFTER:
                self._pt_go_sleep()
            elif now >= self._pt_next_flourish:
                self._pt_next_flourish = now + random.uniform(*self.FLOURISH_EVERY)
                options = [n for n in self.IDLE_FLOURISHES if n in self.animations]
                if options:
                    self._pt_start_action(random.choice(options), 2.6, interruptible=True)

    def _pt_chase(self, now, dt, cursor):
        centre = self.frameGeometry().center()
        dx = cursor.x() - centre.x()
        dy = cursor.y() - centre.y()
        dist = math.hypot(dx, dy)
        stop_dist = max(self.width(), self.height()) * self.STOP_FACTOR

        # +4 px tolerance: tiny final steps round to 0 and would walk in place.
        if dist <= stop_dist + 4:
            self._pt_chasing = False
            if self._pt_mode in ("walk", "run"):
                self._pt_set_idle(now)
            return

        # Walk when near, run when far (with hysteresis so it doesn't flicker).
        if self._pt_mode == "run":
            running = dist > self.WALK_DISTANCE * self.pet_scale
        else:
            running = dist > self.RUN_DISTANCE * self.pet_scale
        want = "run" if running else "walk"
        if self._pt_mode != want:
            self._pt_mode = want
            self.play_animation(want, loop=True)

        speed = (self.RUN_SPEED if running else self.WALK_SPEED) * self.pet_scale
        step = min(speed * dt, dist - stop_dist)
        nx = self.x() + dx / dist * step
        ny = self.y() + dy / dist * step
        self.move(round(nx), round(ny))
        if abs(dx) > 8:
            self._pt_facing_right = dx > 0

    def _restore_assistant_animation(self):
        now = time.monotonic()
        self._pt_last_activity = now
        if self._busy_state():
            self._pt_mode = "idle"
            if self.assistant_state == "thinking":
                self.play_animation("think", loop=True)
            else:
                speaking_animation = "talking" if "talking" in self.animations else "laugh"
                self.play_animation(speaking_animation, loop=True)
        else:
            self._pt_set_idle(now)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.Antialiasing, True)

        pix = None
        if self.current_frames:
            pix = self.current_frames[self.frame_index % len(self.current_frames)]
        if pix is not None and not pix.isNull():
            now = time.monotonic()
            available_w = max(1, self.width() - 16)
            available_h = max(1, self.height() - 18)
            scale = min(available_w / pix.width(), available_h / pix.height())
            width = pix.width() * scale
            height = pix.height() * scale
            x = (self.width() - width) / 2
            y = (self.height() - height) / 2 + math.sin(self.elapsed * 2.1) * 3 - self.bounce * 5
            if self._pt_mode == "walk":
                y += math.sin(now * 14) * 1.5
            elif self._pt_mode == "run":
                y += math.sin(now * 22) * 2.2
            if now < self._pt_shake_until:          # angry shake
                x += math.sin(now * 70) * 4

            # Walk / run art faces one way; mirror it when heading the other way.
            if (self._pt_mode in ("walk", "run")
                    and self._pt_facing_right == self._pt_facing_left_art()):
                painter.translate(self.width(), 0)
                painter.scale(-1, 1)
            painter.drawPixmap(int(x), int(y), int(width), int(height), pix)
        painter.end()

    # ------------------------------------------------------------------- input
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self._pt_drag_active = False
            self._pt_ignore_release = False
            self._pt_press_global = event.globalPosition().toPoint()
            self.drag_offset = self._pt_press_global - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.dragging and event.buttons() & Qt.LeftButton:
            pos = event.globalPosition().toPoint()
            if (not self._pt_drag_active
                    and (pos - self._pt_press_global).manhattanLength() >= self.DRAG_THRESHOLD):
                self._pt_drag_active = True
                self._pt_mode = "drag"
                self._pt_chasing = False
                self._pt_action_interruptible = False
                self.play_animation("surprised", loop=True)
            if self._pt_drag_active:
                self.move(pos - self.drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        was_pressed = self.dragging
        was_drag = self._pt_drag_active
        self.dragging = False
        self._pt_drag_active = False
        if self._pt_ignore_release:             # 2nd release of a double click
            self._pt_ignore_release = False
            return
        if was_drag:
            self.follow_anchor = self.pos()
            self._pt_last_activity = time.monotonic()
            self._pt_last_cursor = QCursor.pos()
            self._pt_set_idle()
            if self.settings is not None:
                self.settings.setValue("pet_pos", f"{self.x()},{self.y()}")
        elif was_pressed:
            self._pt_poke()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            # The first click of the double click already counted as a poke.
            self._pt_poke_count = max(0, self._pt_poke_count - 1)
            self._pt_shake_until = 0.0
            self._pt_ignore_release = True
            self.react(1.8)
            self.open_requested.emit()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Open NOVA", self.open_requested.emit)
        menu.addSeparator()
        follow = menu.addAction("Follow cursor (walk / run)")
        follow.setCheckable(True)
        follow.setChecked(self.pet_follow)
        follow.toggled.connect(self.set_follow_cursor)
        flip = menu.addAction("Flip walk direction")
        flip.setCheckable(True)
        flip.setChecked(self.pet_flip)
        flip.toggled.connect(self.set_flip_direction)
        menu.addAction("Sleep now", self._pt_go_sleep)
        size_menu = menu.addMenu("Pet Size")
        size_menu.addAction("Increase Size (+)", self.increase_size)
        size_menu.addAction("Decrease Size (-)", self.decrease_size)
        size_menu.addAction("Reset Size", lambda: self.set_pet_scale(1.0))
        menu.addSeparator()
        animation_menu = menu.addMenu("Pet Animation")
        for name in self.ANIMATION_FPS:
            animation_menu.addAction(name.replace("_", " ").title(),
                                     lambda checked=False, anim=name: self._pt_preview(anim))
        menu.addSeparator()
        menu.addAction("Hide Pet", self._hide_pet)
        menu.addAction("Exit NOVA", self.exit_requested.emit)
        menu.exec(event.globalPos())

    def _pt_preview(self, name):
        """Menu preview of an animation; returns to normal behaviour afterwards."""
        if name == "sleep":
            self._pt_go_sleep()
        else:
            self._pt_chasing = False
            self._pt_start_action(name, 3.0)

    def _hide_pet(self):
        self.hide()
        self.hidden.emit()
