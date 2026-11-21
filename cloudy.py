"""Cloudy companion for NOVA.

Frames live in assets/cloudy/frames/frame_001.png ... frame_086.png (cleaned,
cropped to one common box so the character never jumps between frames).
Optional drinking frames live in assets/cloudy/frames_cloud/cloud_001..040.png.

What each frame is (1-based IDs):

    1-11    standing, facing front       (2, 3 = happy ^^   4, 9 = eyes closed)
    12-22   sitting                      (12/14/17 = eyes open, 13/15/16 = closed,
                                          18-22 = getting sleepy -> asleep)
    23-35   walk cycle                   (side view, faces LEFT)
    36-48   run cycle                    (side view, faces LEFT)
    49-59   jump: crouch 49-52, rise 53-54, air 55-56, land 57, squash 58-59
    60-64   standing, front (getting up)
    65-70   fall: impact 65, lying down 66-70 (70 = flat)
    71-72   standing, front
    73-75   being carried / dragged (swinging, motion arcs)
    76-80   standing reactions (78 = happy ^^, 80 = eyes closed)
    81-86   emotes: 81 happy, 82 heart eyes, 83-84 sweat drop, 85 sparkle eyes,
                    86 happy tears
    drink   no drinking frames in the 86-frame pack -> uses the cloud set
            (can-sipping frames) when assets/cloudy/frames_cloud exists.

If any animation looks wrong, only the FRAME TABLE below needs editing.

Behaviour
---------
* Cursor still        -> Cloudy sits (blinks naturally, drinks / emotes now and
                         then, falls asleep after a long idle).
* Cursor moving       -> Cloudy walks toward it (runs when far away).
* Fast cursor flick   -> Cloudy jumps (with a cooldown).
* Click               -> happy reaction.  Double click -> jump + open NOVA.
* Drag and let go     -> carried while held, falls, lies down, gets up, sits.
* Right-click menu    -> Jump, Drink, Emotes, Follow cursor toggle, size, etc.
"""
import math
import random
import time
import socket
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QEvent, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QPixmap, QPainter
from PySide6.QtWidgets import QMenu

from pet_engine import PetWidget


def _r(a, b):
    return list(range(a, b + 1))


class CloudyWidget(PetWidget):
    # ------------------------------------------------------------------
    # FRAME TABLE (edit here if an animation looks wrong)
    # ------------------------------------------------------------------
    TOTAL_FRAMES = 86

    STAND_FRAME = 1
    SIT_FRAME = 12
    THINK_FRAME = 17                       # sitting, eyes lowered

    BLINK_SIT = [12, 13, 15, 13, 12]       # open -> closed -> open
    SLEEP_FRAMES = [17, 18, 20, 22]        # drifting off, then held on 22

    WALK_FRAMES = _r(23, 35)
    RUN_FRAMES = _r(36, 48)
    JUMP_FRAMES = _r(49, 59)
    # impact, lie down, stay flat for a moment, then get back up
    FALL_FRAMES = [65, 66, 67, 68, 69, 70, 70, 70, 69, 68, 67, 66, 65, 64, 63, 62]
    CLICK_FRAMES = [79, 78, 78, 79]
    DRAG_FRAMES = [73, 74, 75, 74]
    EMOTES = {
        "Happy": [81] * 4,
        "Love": [82] * 4,
        "Sparkle": [85] * 4,
        "Nervous": [83, 84, 83, 84],
        "Teary": [86] * 4,
    }

    # Fallback if the cloud set is missing: a happy face (no can in these frames).
    DRINK_FRAMES = [78, 78, 81, 81]

    # ------------------------------------------------------------------
    # DRINKING FRAMES (assets/cloudy/frames_cloud/cloud_001..040.png)
    # Sheet of 8 x 5: rows 1, 4, 5 = sipping from the red can.
    # Stored internally as CLOUD_BASE + number.
    # ------------------------------------------------------------------
    CLOUD_BASE = 1000
    CLOUD_COUNT = 40
    CLOUD_DRINK = [1, 2, 3, 7, 3, 7, 4, 8, 2, 1]

    # The walk / run artwork faces LEFT. Set to False if your frames face right.
    NATIVE_FACES_LEFT = True

    # Default window size at scale 1.0 (the frames are cropped tight, so this is
    # also roughly the on-screen size of Cloudy in pixels).
    BASE_W = 120
    BASE_H = 125

    # Modes that play once and must not be interrupted by cursor behaviour.
    ACTION_MODES = ("blink", "click", "jump", "drink", "emote", "fall", "talk")
    TALK_FRAMES = list(range(2, 10))  # start/rise/peak/fall/settle; idle frames excluded

    def __init__(self, settings=None):
        super().__init__(settings)
        self.setWindowTitle("Cloudy — NOVA Companion")

        self.cloudy_scale = 1.0
        self.follow_cursor = True
        if settings is not None:
            try:
                self.cloudy_scale = max(0.65, min(2.2, float(settings.value("cloudy_scale", 1.0))))
            except (TypeError, ValueError):
                pass
            self.follow_cursor = str(settings.value("cloudy_follow", "true")).lower() != "false"
        self.resize(round(self.BASE_W * self.cloudy_scale), round(self.BASE_H * self.cloudy_scale))

        assets = Path(__file__).resolve().parent / "assets" / "cloudy"
        self.frames = {}
        for i in range(1, self.TOTAL_FRAMES + 1):
            pix = QPixmap(str(assets / "frames" / f"frame_{i:03d}.png"))
            if not pix.isNull():
                self.frames[i] = pix

        # Drinking frames come from the cloud set (the 86-frame pack has none).
        cloud = {}
        for i in range(1, self.CLOUD_COUNT + 1):
            pix = QPixmap(str(assets / "frames_cloud" / f"cloud_{i:03d}.png"))
            if not pix.isNull():
                cloud[self.CLOUD_BASE + i] = pix
        self.has_drink = len(cloud) == self.CLOUD_COUNT
        if self.has_drink:
            self.frames.update(cloud)
            self.DRINK_FRAMES = [self.CLOUD_BASE + n for n in self.CLOUD_DRINK]

        # Dedicated mouth voice-pulse frames (same canvas size as Cloudy's frames).
        self.talk_frames = {}
        talk_dir = assets / "talk_frames"
        for i in range(1, 11):
            pix = QPixmap(str(talk_dir / f"talk_{i:03d}.png"))
            if not pix.isNull():
                self.talk_frames[i] = pix
        self.TALK_FRAMES = [i for i in range(2, 10) if i in self.talk_frames]
        self._preview_talking = False

        now = time.monotonic()
        self.mode = "sit"
        self.mode_started = now
        self.mode_until = 0.0
        self.current_frame = self._valid_frame(self.SIT_FRAME)
        self._sequence = []
        self._sequence_started = now
        self._sequence_rate = 0.12
        self._sequence_loop = False

        self._last_tick = now
        self._last_cursor = QCursor.pos()
        self._last_cursor_move = now
        self._next_blink = now + random.uniform(2.5, 5.0)
        self._next_idle_action = now + random.uniform(45, 90)
        self._jump_cooldown = 0.0
        self._facing_right = False

        self._drag_pressed = False
        self._dragging_cloudy = False
        self._drag_offset = QPoint()
        self._press_global = QPoint()
        self._placed = False

        # Local UDP receiver for NOVA voice-state events.
        self._cloudy_bridge = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 47631))
            sock.setblocking(False)
            self._cloudy_bridge = sock
        except OSError:
            self._cloudy_bridge = None

        # PetWidget runs one QTimer that calls _tick. We override _tick and
        # set_assistant_state so its generic sequence player never touches Cloudy.
        self.sequence = [self.current_frame]
        self.frame_index = 0
        self.elapsed = 0.0

    # ------------------------------------------------------------------
    # Frame / sequence helpers
    # ------------------------------------------------------------------
    def _valid_frame(self, frame):
        if frame in self.frames:
            return frame
        return next(iter(self.frames), 0)

    def _valid_sequence(self, frames):
        valid = [f for f in frames if f in self.frames]
        return valid or [self._valid_frame(self.SIT_FRAME)]

    def _hold(self, mode, frame):
        """Show one still frame until something changes the mode."""
        self.mode = mode
        self.mode_started = time.monotonic()
        self.mode_until = 0.0
        self._sequence = []
        self._sequence_loop = False
        self.current_frame = self._valid_frame(frame)

    def _play_once(self, mode, frames, rate=0.10):
        """Play frames once; the mode ends automatically after the last frame."""
        seq = self._valid_sequence(frames)
        self.mode = mode
        self.mode_started = time.monotonic()
        self._sequence_rate = max(0.035, rate)
        self.mode_until = self.mode_started + len(seq) * self._sequence_rate
        self._sequence = seq
        self._sequence_started = self.mode_started
        self._sequence_loop = False
        self.current_frame = seq[0]

    def _play_loop(self, mode, frames, rate=0.10):
        seq = self._valid_sequence(frames)
        self.mode = mode
        self.mode_started = time.monotonic()
        self.mode_until = 0.0
        self._sequence = seq
        self._sequence_started = self.mode_started
        self._sequence_rate = max(0.035, rate)
        self._sequence_loop = True
        self.current_frame = seq[0]

    # ------------------------------------------------------------------
    # Actions (also used by the right-click menu)
    # ------------------------------------------------------------------
    def play_jump(self):
        self._play_once("jump", self.JUMP_FRAMES, 0.07)

    def play_drink(self):
        self._play_once("drink", self.DRINK_FRAMES, 0.20)

    def play_emote(self, name):
        self._play_once("emote", self.EMOTES.get(name, self.CLICK_FRAMES), 0.45)

    def play_fall(self):
        self._play_once("fall", self.FALL_FRAMES, 0.11)

    def play_blink(self):
        frames = self.BLINK_SIT
        if random.random() < 0.2:            # occasional double blink
            frames = frames + frames
        self._play_once("blink", frames, 0.05)

    def set_assistant_state(self, state):
        """Sync Cloudy's animation with NOVA's live assistant/TTS state.

        Call with 'speaking' while audio is playing and 'idle' when playback ends.
        Repeated 'speaking' updates do not restart the animation loop.
        """
        self._preview_talking = False
        self.assistant_state = state if state in ("idle", "listening", "thinking", "speaking") else "idle"

        if self._dragging_cloudy:
            self.update()
            return

        if self.assistant_state == "speaking":
            if self.TALK_FRAMES and self.mode != "talk":
                self._play_loop("talk", self.TALK_FRAMES, 0.085)
        elif self.mode == "talk":
            self._hold("sit", self.SIT_FRAME)
        elif self.assistant_state == "thinking" and self.mode not in self.ACTION_MODES:
            self._hold("sit", self.SIT_FRAME)

        self.update()

    def preview_talking(self):
        """Preview the voice-pulse animation for four seconds from Cloudy's menu."""
        if not self.TALK_FRAMES or self._dragging_cloudy:
            return
        self._preview_talking = True
        self.assistant_state = "speaking"
        if self.mode != "talk":
            self._play_loop("talk", self.TALK_FRAMES, 0.085)
        QTimer.singleShot(4000, self._finish_talking_preview)
        self.update()

    def _finish_talking_preview(self):
        if self._preview_talking:
            self._preview_talking = False
            self.assistant_state = "idle"
            if self.mode == "talk":
                self._hold("sit", self.SIT_FRAME)
            self.update()

    def set_follow_cursor(self, enabled):
        self.follow_cursor = bool(enabled)
        if self.settings is not None:
            self.settings.setValue("cloudy_follow", "true" if self.follow_cursor else "false")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def _poll_nova_voice_bridge(self):
        """Apply the newest voice state sent by pc_assistant.py."""
        if self._cloudy_bridge is None:
            return
        try:
            while True:
                payload, _address = self._cloudy_bridge.recvfrom(64)
                state = payload.decode("ascii", errors="ignore").strip().lower()
                if state in ("speaking", "idle", "listening", "thinking"):
                    self.set_assistant_state(state)
        except BlockingIOError:
            pass
        except OSError:
            pass

    def _tick(self):
        now = time.monotonic()
        self._poll_nova_voice_bridge()
        dt = min(0.1, now - self._last_tick)
        self._last_tick = now

        cursor = QCursor.pos()
        moved = (cursor - self._last_cursor).manhattanLength()
        self._last_cursor = cursor
        if moved >= 5:
            self._last_cursor_move = now

        if self._dragging_cloudy:
            pass                                    # drag owns the animation
        elif self.mode in self.ACTION_MODES:
            if self.mode_until and now >= self.mode_until:
                self._hold("sit", self.SIT_FRAME)   # one-shot finished
        else:
            self._cursor_behaviour(now, dt, cursor, moved)

        self._advance_frame(now)
        self.update()

    def _cursor_behaviour(self, now, dt, cursor, moved):
        idle_for = now - self._last_cursor_move
        cursor_moving = idle_for < 0.45

        if cursor_moving:
            self._next_idle_action = max(self._next_idle_action, now + 30)

            # Fast flick -> jump
            if moved > 65 and now >= self._jump_cooldown:
                self.play_jump()
                self._jump_cooldown = now + 4.0
                return

            if self.follow_cursor:
                self._walk_toward(cursor, dt)
            elif self.mode != "walk":
                self._play_loop("walk", self.WALK_FRAMES, 0.09)
            return

        # ---- cursor is still: sit, blink, idle actions, sleep ----
        if self.mode in ("walk", "run"):
            self._hold("sit", self.SIT_FRAME)
            self._next_blink = now + random.uniform(1.5, 3.0)

        if self.mode == "sit":
            if idle_for > 90:
                self._play_loop("sleep", self.SLEEP_FRAMES, 0.8)
                self._sequence_loop = False        # drift off, stay on last frame
                return
            if now >= self._next_idle_action:
                self._next_idle_action = now + random.uniform(45, 90)
                if self.has_drink and random.random() < 0.6:
                    self.play_drink()
                else:
                    self.play_emote(random.choice(list(self.EMOTES)))
                return
            if now >= self._next_blink:
                self.play_blink()
                self._next_blink = now + random.uniform(2.8, 5.5)

    def _walk_toward(self, cursor, dt):
        centre = self.frameGeometry().center()
        dx = cursor.x() - centre.x()
        dy = cursor.y() - centre.y()
        dist = math.hypot(dx, dy)
        stop_dist = max(self.width(), self.height()) * 0.75

        if dist <= stop_dist:
            if self.mode in ("walk", "run", "sleep"):
                self._hold("sit", self.SIT_FRAME)
            return

        running = dist > 450
        want = "run" if running else "walk"
        if self.mode != want:
            if running:
                self._play_loop("run", self.RUN_FRAMES, 0.065)
            else:
                self._play_loop("walk", self.WALK_FRAMES, 0.09)

        speed = (330 if running else 140) * self.cloudy_scale
        step = min(speed * dt, dist - stop_dist)
        self.move(round(self.x() + dx / dist * step), round(self.y() + dy / dist * step))
        if abs(dx) > 8:
            self._facing_right = dx > 0

    def _advance_frame(self, now):
        if self._sequence:
            index = int(max(0.0, now - self._sequence_started) / self._sequence_rate)
            if self._sequence_loop:
                self.current_frame = self._sequence[index % len(self._sequence)]
            else:
                self.current_frame = self._sequence[min(index, len(self._sequence) - 1)]
        elif self.mode == "sit" and getattr(self, "assistant_state", "idle") == "thinking":
            self.current_frame = self._valid_frame(self.THINK_FRAME)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        if self.mode == "talk":
            talk_index = self.current_frame
            pix = self.talk_frames.get(talk_index)
        else:
            pix = self.frames.get(self.current_frame)
        if pix is None or pix.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        scale = min((self.width() - 10) / pix.width(), (self.height() - 10) / pix.height())
        w, h = pix.width() * scale, pix.height() * scale
        x, y = (self.width() - w) / 2, (self.height() - h) / 2

        now = time.monotonic()
        bob = 0.0
        if self.mode == "sit":
            bob = 2 + math.sin(now * 1.3)          # gentle breathing
        elif self.mode == "walk":
            bob = math.sin(now * 14) * 1.2
        elif self.mode == "run":
            bob = math.sin(now * 22) * 1.8

        # Only the side-view walk / run frames face left; mirror them when Cloudy
        # heads right. Front-facing poses are never mirrored.
        if self.mode in ("walk", "run") and self._facing_right == self.NATIVE_FACES_LEFT:
            painter.translate(self.width(), 0)
            painter.scale(-1, 1)
        painter.drawPixmap(int(x), int(y + bob), int(w), int(h), pix)
        painter.end()

    # ------------------------------------------------------------------
    # Size
    # ------------------------------------------------------------------
    def set_pet_scale(self, scale):
        self.cloudy_scale = max(0.65, min(2.2, float(scale)))
        centre = self.frameGeometry().center()
        self.resize(round(self.BASE_W * self.cloudy_scale), round(self.BASE_H * self.cloudy_scale))
        self.move(centre - QPoint(self.width() // 2, self.height() // 2))
        if self.settings is not None:
            self.settings.setValue("cloudy_scale", self.cloudy_scale)

    def increase_size(self):
        self.set_pet_scale(self.cloudy_scale + 0.12)

    def decrease_size(self):
        self.set_pet_scale(self.cloudy_scale - 0.12)

    def event(self, event):
        try:
            if event.type() == QEvent.Type.NativeGesture and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                self.set_pet_scale(self.cloudy_scale * (1.0 + float(event.value())))
                event.accept()
                return True
        except (AttributeError, TypeError, ValueError):
            pass
        return super().event(event)

    def show_bot(self):
        if not self._placed:
            self._placed = True
            screen = QGuiApplication.primaryScreen()
            if screen:
                g = screen.availableGeometry()
                self.move(g.right() - self.width() - 30, g.bottom() - self.height() - 50)
                if self.settings is not None:
                    try:
                        x, y = map(int, str(self.settings.value("cloudy_pos", "")).split(","))
                        self.move(x, y)
                    except (ValueError, TypeError):
                        pass
        self.show()
        self.raise_()
        self.ensure_on_screen()

    # ------------------------------------------------------------------
    # Mouse: click = react, drag = carried, release = fall + get up
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pressed = True
            self._dragging_cloudy = False
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = self._press_global - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pressed and event.buttons() & Qt.LeftButton:
            pos = event.globalPosition().toPoint()
            if not self._dragging_cloudy and (pos - self._press_global).manhattanLength() >= 7:
                self._dragging_cloudy = True
                self._play_loop("drag", self.DRAG_FRAMES, 0.14)
            if self._dragging_cloudy:
                self.move(pos - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_pressed:
            was_dragging = self._dragging_cloudy
            self._drag_pressed = False
            self._dragging_cloudy = False
            if was_dragging:
                self.play_fall()
                if self.settings is not None:
                    self.settings.setValue("cloudy_pos", f"{self.x()},{self.y()}")
            else:
                self._play_once("click", self.CLICK_FRAMES, 0.15)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.play_jump()
            self.open_requested.emit()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Open NOVA", self.open_requested.emit)
        menu.addSeparator()
        menu.addAction("Jump!", self.play_jump)
        menu.addAction("Drink now", self.play_drink)
        talk_action = menu.addAction("Preview talking / voice pulse")
        talk_action.setEnabled(bool(self.TALK_FRAMES))
        talk_action.triggered.connect(self.preview_talking)
        emote_menu = menu.addMenu("Emotes")
        for name in self.EMOTES:
            emote_menu.addAction(name, lambda n=name: self.play_emote(n))
        follow = menu.addAction("Follow cursor (walk / run)")
        follow.setCheckable(True)
        follow.setChecked(self.follow_cursor)
        follow.toggled.connect(self.set_follow_cursor)
        menu.addSeparator()
        size_menu = menu.addMenu("Cloudy Size")
        size_menu.addAction("Increase Size (+)", self.increase_size)
        size_menu.addAction("Decrease Size (-)", self.decrease_size)
        size_menu.addAction("Reset Size", lambda: self.set_pet_scale(1.0))
        menu.addSeparator()
        menu.addAction("Hide Cloudy", self._hide_pet)
        menu.addAction("Exit NOVA", self.exit_requested.emit)
        menu.exec(event.globalPos())

    def closeEvent(self, event):
        if self.settings is not None:
            self.settings.setValue("cloudy_pos", f"{self.x()},{self.y()}")
        try:
            self._cloudy_bridge.close()
        except (AttributeError, OSError):
            pass
        super().closeEvent(event)
