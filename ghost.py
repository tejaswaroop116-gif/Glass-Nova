
import sys
import math
import random
import time

from PySide6.QtWidgets import QApplication, QWidget, QMenu, QInputDialog
from PySide6.QtCore import Qt, QTimer, QPoint, QPointF, QRectF, Signal, QEvent
from PySide6.QtGui import (
    QPainter, QPainterPath, QColor, QPen, QBrush,
    QRadialGradient, QLinearGradient, QCursor, QFont, QGuiApplication
)


class GhostWidget(QWidget):
    # Same signals as the mechanical bot, so Nova can use either one.
    open_requested = Signal()
    exit_requested = Signal()
    hidden = Signal()

    DESIGN_SIZE = 500
    MIN_SIZE = 120
    MAX_SIZE = 800

    # Voice-assistant state -> expression held while that state is active
    STATE_EXPRESSIONS = {
        "idle": "idle",
        "listening": "surprised",
        "thinking": "thinking",
        "speaking": "happy",
    }

    EXPRESSIONS = (
        "idle", "happy", "surprised", "sleepy",
        "shy", "angry", "thinking", "love"
    )

    def __init__(self, settings=None):
        super().__init__()

        self.settings = settings
        self.assistant_state = "idle"
        self._placed = False

        self.ghost_size = 300
        if settings is not None:
            try:
                self.ghost_size = max(
                    self.MIN_SIZE,
                    min(self.MAX_SIZE, int(settings.value("ghost_size", 300)))
                )
            except (TypeError, ValueError):
                self.ghost_size = 300

        self.setWindowTitle("Ghost Companion")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setFixedSize(self.ghost_size, self.ghost_size)

        # Animation
        self.frame = 0
        self.bounce = 0.0
        self.squash = 0.0
        self.look_x = 0.0
        self.look_y = 0.0

        # Blinking
        self.blink = 0.0
        self.blinking = False
        self.next_blink = time.monotonic() + 2.5

        # Expressions
        self.expression = "idle"
        self.expression_until = 0.0
        self.expression_next = time.monotonic() + 4.0
        self.manual_expression = False

        # Cursor behaviour
        self.last_cursor = None
        self.cursor_speed = 0.0
        self.cursor_idle_time = 0.0
        self.hover_time = 0.0
        self.pet_motion = 0.0
        self.last_pet_point = None
        self.last_pet_time = 0.0
        self.reaction_cooldown = 0.0
        self.last_cursor_distance = None

        # Interaction effects
        self.heart_particles = []
        self.heart_spawn_timer = 0.0
        self.goodbye_timer = 0.0
        self.goodbye_active = False

        # Mouse
        self.dragging = False
        self.drag_offset = QPoint()
        self.click_times = []

        self.last_tick = time.monotonic()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.setInterval(16)   # started in showEvent, stopped in hideEvent

    # =========================================
    # NOVA INTEGRATION (same API as the mechanical bot)
    # =========================================

    def set_assistant_state(self, state):
        """Called by ui.py: idle | listening | thinking | speaking."""
        if state not in self.STATE_EXPRESSIONS:
            state = "idle"

        previous = self.assistant_state
        self.assistant_state = state
        now = time.monotonic()

        self.manual_expression = False
        self.expression = self.STATE_EXPRESSIONS[state]

        if state == "idle" and previous != "idle":
            self.expression_next = now + random.uniform(5, 8)

        self.update()

    def react(self, duration=1.5):
        self.set_expression("happy", duration, force=True)
        self.bounce = max(self.bounce, 12)

    def _on_screen(self, pos):
        center = pos + QPoint(self.width() // 2, self.height() // 2)
        return any(
            s.availableGeometry().contains(center)
            for s in QGuiApplication.screens()
        )

    def ensure_on_screen(self):
        if not self._on_screen(self.pos()):
            area = QGuiApplication.primaryScreen().availableGeometry()
            self.move(
                area.right() - self.width() - 20,
                area.bottom() - self.height() - 30
            )

    def save_position(self):
        if self.settings is not None:
            self.settings.setValue("ghost_pos", f"{self.x()},{self.y()}")

    def show_bot(self):
        if not self._placed:
            self._placed = True
            area = QGuiApplication.primaryScreen().availableGeometry()
            self.move(
                area.right() - self.width() - 20,
                area.bottom() - self.height() - 30
            )
            try:
                x, y = map(
                    int,
                    str(self.settings.value("ghost_pos", "")).split(",")
                )
                if self._on_screen(QPoint(x, y)):
                    self.move(x, y)
            except (ValueError, AttributeError):
                pass

        self.show()
        self.ensure_on_screen()

    def showEvent(self, event):
        self.last_tick = time.monotonic()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.save_position()
        self.timer.stop()
        event.accept()

    def event(self, event):
        try:
            if event.type() == QEvent.Type.NativeGesture and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                factor = 1.0 + float(event.value())
                self.set_ghost_size(round(self.ghost_size * max(0.80, min(1.20, factor))))
                event.accept()
                return True
        except (AttributeError, TypeError, ValueError):
            pass
        return super().event(event)

    # =========================================
    # SIZE
    # =========================================

    def set_ghost_size(self, size):
        size = max(self.MIN_SIZE, min(self.MAX_SIZE, int(size)))
        center = self.frameGeometry().center()
        self.ghost_size = size
        self.setFixedSize(size, size)
        self.move(
            center.x() - self.rect().center().x(),
            center.y() - self.rect().center().y()
        )
        if self.settings is not None:
            self.settings.setValue("ghost_size", self.ghost_size)
        self.update()

    def increase_size(self):
        self.set_ghost_size(self.ghost_size + 25)

    def decrease_size(self):
        self.set_ghost_size(self.ghost_size - 25)

    def custom_size(self):
        value, ok = QInputDialog.getInt(
            self, "Customize Ghost Size",
            "Enter ghost size in pixels:",
            self.ghost_size, self.MIN_SIZE, self.MAX_SIZE, 10
        )
        if ok:
            self.set_ghost_size(value)

    # =========================================
    # EXPRESSIONS
    # =========================================

    def set_expression(self, expression, duration=2.5, force=False):
        if expression not in self.EXPRESSIONS:
            return

        if self.reaction_cooldown > 0 and not force:
            return

        self.expression = expression
        self.expression_until = time.monotonic() + duration
        self.manual_expression = True
        self.update()

    def update_expression(self, now):
        # While the voice assistant is listening / thinking / speaking, hold
        # that expression (clicks and pets still show briefly, then it returns).
        if self.assistant_state != "idle":
            if not self.manual_expression or now >= self.expression_until:
                self.manual_expression = False
                self.expression = self.STATE_EXPRESSIONS[self.assistant_state]
                self.expression_next = now + random.uniform(5, 8)
            return

        if self.manual_expression and now >= self.expression_until:
            self.manual_expression = False
            self.expression = "idle"
            self.expression_next = now + random.uniform(5, 8)

        if not self.manual_expression and now >= self.expression_next:
            self.expression = random.choice(
                ["idle", "sleepy", "thinking"]
            )
            self.expression_next = now + random.uniform(5, 9)
            self.expression_until = now + random.uniform(2, 3)
            self.manual_expression = True

    # =========================================
    # CURSOR INTERACTIONS
    # =========================================

    def update_cursor(self, now, dt):
        cursor = QCursor.pos()
        local = self.mapFromGlobal(cursor)

        inside = self.rect().contains(local)

        # Map cursor position to original 500 x 500 drawing coordinates
        factor = self.DESIGN_SIZE / max(1, self.ghost_size)
        gx = local.x() * factor
        gy = local.y() * factor

        face_distance = math.hypot(gx - 200, gy - 190)
        near_face = inside and face_distance < 150

        # Cursor speed
        if self.last_cursor is None:
            self.last_cursor = cursor

        distance = math.hypot(
            cursor.x() - self.last_cursor.x(),
            cursor.y() - self.last_cursor.y()
        )

        speed = distance / max(dt, 0.001)
        self.cursor_speed += (speed - self.cursor_speed) * 0.25
        self.last_cursor = cursor

        if distance < 1.5:
            self.cursor_idle_time += dt
        else:
            self.cursor_idle_time = 0

        # Hover tracking
        if near_face:
            self.hover_time += dt
        else:
            self.hover_time = max(0, self.hover_time - dt * 1.5)

        # Accurate cursor tracking:
        # The face is centred around (202, 193) in the original 500x500 artwork.
        # Use the actual face centre (not the widget centre), and normalize in
        # screen pixels so tracking remains consistent at every Ghost size.
        face_local = QPoint(
            int(202 * self.ghost_size / self.DESIGN_SIZE),
            int(193 * self.ghost_size / self.DESIGN_SIZE)
        )
        face_global = self.mapToGlobal(face_local)

        dx = cursor.x() - face_global.x()
        dy = cursor.y() - face_global.y()

        # A 170px movement from the face centre reaches full eye travel.
        # This gives useful movement even when the pointer is outside Ghost.
        sensitivity = 170.0
        tx = max(-1.0, min(1.0, dx / sensitivity))
        ty = max(-1.0, min(1.0, dy / sensitivity))

        # Small dead-zone prevents tiny hand/touchpad jitter from shaking the eyes.
        dead_zone = 0.035
        if abs(tx) < dead_zone:
            tx = 0.0
        else:
            tx = math.copysign((abs(tx) - dead_zone) / (1.0 - dead_zone), tx)
        if abs(ty) < dead_zone:
            ty = 0.0
        else:
            ty = math.copysign((abs(ty) - dead_zone) / (1.0 - dead_zone), ty)

        # Smooth but responsive; slightly faster than the previous sluggish tracking.
        smoothing = 1.0 - math.exp(-dt * 13.0)
        self.look_x += (tx - self.look_x) * smoothing
        self.look_y += (ty - self.look_y) * smoothing

        # 1. Fast cursor movement: surprised
        if near_face and self.cursor_speed > 1100:
            self.set_expression("surprised", 1.3)
            self.bounce = max(self.bounce, 12)
            self.reaction_cooldown = 1.2

        # 2. Hold near face: love
        if near_face and self.hover_time > 2.0:
            if self.expression != "love":
                self.set_expression("love", 3.0, force=True)
                self.heart_spawn_timer = 0
                self.reaction_cooldown = 3.2

        # 3. Pet the hood: happy
        if inside and 65 < gx < 390 and 35 < gy < 220:
            if self.last_pet_point is not None:
                pd = math.hypot(
                    gx - self.last_pet_point[0],
                    gy - self.last_pet_point[1]
                )

                if pd > 2 and pd < 38 and now - self.last_pet_time < 0.25:
                    self.pet_motion += pd

                    if self.pet_motion > 38:
                        self.set_expression("happy", 2.6, force=True)
                        self.bounce = max(self.bounce, 8)
                        self.heart_spawn_timer = 0
                        self.reaction_cooldown = 1.7
                        self.pet_motion = 0

            self.last_pet_point = (gx, gy)
            self.last_pet_time = now

        else:
            self.last_pet_point = None
            self.pet_motion = max(0, self.pet_motion - dt * 30)

        # 4. Idle cursor away: sleepy
        if not inside and self.cursor_idle_time > 9:
            if self.expression != "sleepy":
                self.set_expression("sleepy", 3.0, force=True)
                self.reaction_cooldown = 5
                self.cursor_idle_time = 0

        # 5. Cursor suddenly leaves: goodbye sparkles
        center = self.mapToGlobal(self.rect().center())
        current_distance = math.hypot(
            cursor.x() - center.x(),
            cursor.y() - center.y()
        )

        if self.last_cursor_distance is not None:
            if (
                self.last_cursor_distance < 180
                and current_distance > 280
                and self.cursor_speed > 850
            ):
                self.goodbye_active = True
                self.goodbye_timer = 1.1

        self.last_cursor_distance = current_distance

    # =========================================
    # HEART PARTICLES
    # =========================================

    def update_hearts(self, dt):
        self.heart_spawn_timer -= dt

        if self.expression == "love" and self.heart_spawn_timer <= 0:
            self.heart_particles.append({
                "x": random.uniform(160, 290),
                "y": random.uniform(120, 205),
                "life": 1.0,
                "size": random.uniform(7, 13),
                "drift": random.uniform(-18, 18)
            })
            self.heart_spawn_timer = 0.22

        for h in self.heart_particles:
            h["y"] -= 42 * dt
            h["x"] += h["drift"] * dt
            h["life"] -= dt

        self.heart_particles = [
            h for h in self.heart_particles if h["life"] > 0
        ]

    # =========================================
    # ANIMATION TIMER
    # =========================================

    def animate(self):
        now = time.monotonic()
        dt = min(0.05, max(0.001, now - self.last_tick))
        self.last_tick = now

        self.frame += 1
        self.update_cursor(now, dt)

        self.reaction_cooldown = max(
            0, self.reaction_cooldown - dt
        )

        if not self.blinking and now >= self.next_blink:
            self.blinking = True
            self.blink = 0

        if self.blinking:
            self.blink += dt * 7.5

            if self.blink >= 1:
                self.blinking = False
                self.blink = 0
                self.next_blink = now + random.uniform(2.2, 5)

        self.update_expression(now)
        self.update_hearts(dt)

        self.bounce *= 0.86
        self.squash *= 0.86

        if self.goodbye_active:
            self.goodbye_timer -= dt
            if self.goodbye_timer <= 0:
                self.goodbye_active = False

        self.update()

    # =========================================
    # DRAWING HELPERS
    # =========================================

    def draw_glow(self, p, x, y, radius, color, alpha):
        gradient = QRadialGradient(QPointF(x, y), radius)

        c = QColor(color)
        c.setAlpha(max(0, min(255, int(alpha))))

        middle = QColor(color)
        middle.setAlpha(max(0, min(255, int(alpha * 0.4))))

        gradient.setColorAt(0, c)
        gradient.setColorAt(0.5, middle)
        gradient.setColorAt(1, QColor(255, 255, 255, 0))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(gradient))
        p.drawEllipse(QPointF(x, y), radius, radius)

    def draw_heart(self, p, x, y, size, color):
        path = QPainterPath()
        path.moveTo(x, y + size * 0.42)

        path.cubicTo(
            x - size * 1.15, y - size * 0.20,
            x - size * 0.65, y - size * 1.05,
            x, y - size * 0.48
        )

        path.cubicTo(
            x + size * 0.65, y - size * 1.05,
            x + size * 1.15, y - size * 0.20,
            x, y + size * 0.42
        )

        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(path)

    def draw_sparkle(self, p, x, y, size, rotation, alpha):
        p.save()
        p.translate(x, y)
        p.rotate(rotation)

        path = QPainterPath()
        path.moveTo(0, -size)
        path.quadTo(2, -2, size * 0.65, 0)
        path.quadTo(2, 2, 0, size)
        path.quadTo(-2, 2, -size * 0.65, 0)
        path.quadTo(-2, -2, 0, -size)
        path.closeSubpath()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, int(alpha)))
        p.drawPath(path)
        p.restore()

    # =========================================
    # FACE
    # =========================================

    def draw_face(self, p, t):
        mood = self.expression

        # Dark navy face
        p.setPen(QPen(QColor("#211F2D"), 8))
        p.setBrush(QColor("#090A19"))
        p.drawEllipse(QRectF(105, 95, 195, 195))

        face_gradient = QRadialGradient(195, 185, 115)
        face_gradient.setColorAt(0, QColor("#17172A"))
        face_gradient.setColorAt(0.65, QColor("#0B0B1B"))
        face_gradient.setColorAt(1, QColor("#050611"))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(face_gradient))
        p.drawEllipse(QRectF(112, 102, 181, 181))

        # Love heart eyes
        if mood == "love":
            for x in (155, 250):
                ex = x + self.look_x * 18
                ey = 190 + self.look_y * 16

                self.draw_glow(
                    p, ex, ey, 58, QColor("#FF527A"), 120
                )
                self.draw_heart(
                    p, ex, ey, 23, QColor("#F0445E")
                )

        else:
            width_factor = 1.0
            height_factor = 1.0
            y_offset = 0

            if mood == "surprised":
                width_factor = 1.12
                height_factor = 1.30
            elif mood == "sleepy":
                height_factor = 0.52
            elif mood == "happy":
                height_factor = 0.62
            elif mood == "shy":
                height_factor = 0.88
            elif mood == "thinking":
                height_factor = 0.90
                y_offset = -7
            elif mood == "angry":
                height_factor = 0.68
                y_offset = 3

            blink_scale = (
                max(0.06, abs(math.cos(self.blink * math.pi)))
                if self.blinking else 1.0
            )

            pulse = 1 + math.sin(t * 2.2) * 0.045

            for index, x in enumerate((155, 250)):
                ex = x + self.look_x * 18
                ey = 190 + self.look_y * 16 + y_offset

                if mood == "thinking":
                    ex += 4 if index == 1 else -2
                    ey -= 4

                ew = 43 * pulse * width_factor
                eh = max(
                    3, 59 * pulse * height_factor * blink_scale
                )

                self.draw_glow(
                    p, ex, ey, 62 * pulse,
                    QColor("#A66AFF"), 125
                )

                outer = QRadialGradient(QPointF(ex, ey), 35 * pulse)
                outer.setColorAt(0, QColor("#FFFFFF"))
                outer.setColorAt(0.55, QColor("#E9D9FF"))
                outer.setColorAt(0.82, QColor("#B98AFF"))
                outer.setColorAt(1, QColor("#8250D6"))

                p.setPen(QPen(QColor("#C49BFF"), 1.5))
                p.setBrush(QBrush(outer))
                p.drawEllipse(QRectF(
                    ex - ew / 2, ey - eh / 2, ew, eh
                ))

                inner = QRadialGradient(
                    QPointF(ex - 3, ey - 5), 29 * pulse
                )
                inner.setColorAt(0, QColor("#FFFFFF"))
                inner.setColorAt(0.65, QColor("#F9F4FF"))
                inner.setColorAt(1, QColor("#D9C5FF"))

                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(inner))
                p.drawEllipse(QRectF(
                    ex - ew * 0.33, ey - eh * 0.38,
                    ew * 0.66, eh * 0.76
                ))

                if mood == "sleepy":
                    p.setBrush(QColor("#101020"))
                    p.drawRect(QRectF(
                        ex - ew / 2 - 1, ey - eh / 2 - 1,
                        ew + 2, eh * 0.48
                    ))

                if mood == "happy":
                    happy_eye = QPainterPath()
                    happy_eye.moveTo(ex - 15, ey)
                    happy_eye.quadTo(ex, ey + 13, ex + 15, ey)

                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(
                        QColor("#E9D9FF"), 3.5,
                        Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap
                    ))
                    p.drawPath(happy_eye)

        # Angry / thinking eyebrows
        if mood in ("angry", "thinking"):
            p.setBrush(Qt.BrushStyle.NoBrush)
            brow_color = (
                QColor("#F0DFFF")
                if mood == "angry"
                else QColor("#F4C65B")
            )

            p.setPen(QPen(
                brow_color, 7,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap
            ))

            left = QPainterPath()
            right = QPainterPath()

            if mood == "angry":
                left.moveTo(125, 145)
                left.lineTo(174, 160)
                right.moveTo(232, 160)
                right.lineTo(280, 145)
            else:
                left.moveTo(135, 137)
                left.quadTo(155, 145, 165, 132)
                right.moveTo(238, 132)
                right.quadTo(255, 145, 273, 137)

            p.drawPath(left)
            p.drawPath(right)

        # Mouth
        p.setBrush(Qt.BrushStyle.NoBrush)

        if mood == "surprised":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#E9D9FF"))
            p.drawEllipse(QRectF(194, 258, 13, 19))

        elif mood == "thinking":
            mouth = QPainterPath()
            mouth.moveTo(190, 266)
            mouth.quadTo(200, 260, 211, 266)
            p.setPen(QPen(
                QColor("#E9D9FF"), 3,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap
            ))
            p.drawPath(mouth)

        elif mood == "sleepy":
            mouth = QPainterPath()
            mouth.moveTo(190, 266)
            mouth.quadTo(200, 270, 210, 266)
            p.setPen(QPen(
                QColor("#D9C5FF"), 3,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap
            ))
            p.drawPath(mouth)

        elif mood == "angry":
            mouth = QPainterPath()
            mouth.moveTo(187, 270)
            mouth.quadTo(200, 253, 215, 270)
            p.setPen(QPen(
                QColor("#F3DDFB"), 4,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap
            ))
            p.drawPath(mouth)

            # Anger symbol
            mark = QPainterPath()
            mark.moveTo(280, 120)
            mark.lineTo(292, 132)
            mark.lineTo(305, 120)
            mark.moveTo(292, 132)
            mark.lineTo(285, 145)

            p.setPen(QPen(
                QColor("#F05B78"), 5,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap
            ))
            p.drawPath(mark)

        elif mood == "love":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#F0445E"))
            p.drawRoundedRect(QRectF(181, 253, 38, 27), 8, 8)
            p.setBrush(QColor("#FF8A9B"))
            p.drawEllipse(QRectF(191, 266, 18, 10))

        else:
            mouth = QPainterPath()
            mouth.moveTo(180, 263)

            if mood == "happy":
                mouth.cubicTo(190, 280, 208, 280, 218, 263)
            else:
                mouth.cubicTo(190, 273, 207, 273, 218, 263)

            p.setPen(QPen(
                QColor("#E9D9FF"), 4,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap
            ))
            p.drawPath(mouth)

        # Blush
        if mood in ("shy", "love", "happy"):
            for x in (125, 280):
                self.draw_glow(
                    p, x, 245, 23,
                    QColor("#FF91C8"),
                    100 if mood == "shy" else 75
                )

        # Thinking question mark
        if mood == "thinking":
            p.setPen(QColor("#F4C65B"))
            p.setFont(QFont("Segoe UI", 34, QFont.Weight.Bold))
            p.drawText(QPointF(285, 145), "?")

        self.draw_glow(
            p, 199, 267, 19,
            QColor("#B88AFF"), 65
        )

    # =========================================
    # MAIN DRAWING
    # =========================================

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        scale = self.ghost_size / self.DESIGN_SIZE
        p.scale(scale, scale)
        t = self.frame * 0.035

        bob = math.sin(t * 1.5) * 6
        sway = math.sin(t * 0.8) * 1.5

        p.translate(250, 250)
        p.rotate(sway)
        p.translate(-250, -250)
        p.translate(0, bob - self.bounce)

        if self.squash > 0.1:
            p.translate(250, 360)
            p.scale(
                1 + self.squash * 0.025,
                1 - self.squash * 0.035
            )
            p.translate(-250, -360)

        self.draw_glow(
            p, 260, 230, 210,
            QColor("#A9E9F5"),
            28 + (0.5 + 0.5 * math.sin(t * 2)) * 20
        )

        # Original flowing body
        w1 = math.sin(t * 2.2) * 9
        w2 = math.sin(t * 2.7 + 1.5) * 8
        w3 = math.sin(t * 1.8 + 3) * 7

        body = QPainterPath()
        body.moveTo(95, 275)
        body.cubicTo(55, 300, 65, 355 + w1, 110, 350 + w1)
        body.cubicTo(145, 350 + w2, 160, 340, 170, 330)
        body.cubicTo(175, 380 + w2, 220, 385 + w2, 245, 355)
        body.cubicTo(270, 390 + w3, 330, 385 + w3, 365, 375 + w3)
        body.cubicTo(405, 390 + w1, 435, 365, 445, 340)
        body.cubicTo(405, 350, 390, 325, 385, 305)
        body.cubicTo(420, 300, 410, 270, 390, 265)
        body.cubicTo(420, 245, 395, 225, 365, 220)
        body.lineTo(95, 275)
        body.closeSubpath()

        gradient = QLinearGradient(100, 250, 380, 390)
        gradient.setColorAt(0, QColor("#E8FAFC"))
        gradient.setColorAt(0.5, QColor("#D8F3F8"))
        gradient.setColorAt(1, QColor("#B9E0E8"))

        p.setPen(QPen(
            QColor("#211F24"), 9,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin
        ))
        p.setBrush(QBrush(gradient))
        p.drawPath(body)

        shade = QPainterPath()
        shade.moveTo(95, 285)
        shade.cubicTo(120, 320, 130, 345 + w1, 110, 350 + w1)
        shade.cubicTo(150, 360 + w2, 170, 330, 180, 330)
        shade.cubicTo(195, 370 + w2, 220, 370, 245, 350)
        shade.cubicTo(260, 380 + w3, 330, 380 + w3, 365, 365 + w3)
        shade.lineTo(365, 320)
        shade.closeSubpath()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#A9D4DE"))
        p.drawPath(shade)

        # Fabric folds
        for i in range(5):
            x = 190 + i * 32
            movement = math.sin(t * 2 + i) * 6

            fold = QPainterPath()
            fold.moveTo(x, 285)
            fold.cubicTo(
                x - 20 + movement, 310,
                x + 20 - movement, 340,
                x + movement, 355
            )

            p.setPen(QPen(
                QColor(105, 160, 175, 100), 3,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap
            ))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(fold)

        # Original hood
        p.save()
        p.translate(0, math.sin(t * 1.3) * 2)

        hood = QPainterPath()
        hood.moveTo(85, 260)
        hood.cubicTo(70, 160, 100, 75, 185, 45)
        hood.cubicTo(265, 15, 350, 45, 395, 90)
        hood.cubicTo(425, 115, 450, 125, 465, 125)
        hood.cubicTo(440, 165, 405, 185, 370, 190)
        hood.cubicTo(395, 230, 380, 265, 340, 280)
        hood.cubicTo(270, 315, 150, 315, 85, 260)
        hood.closeSubpath()

        hg = QLinearGradient(100, 40, 400, 290)
        hg.setColorAt(0, QColor("#F1FCFD"))
        hg.setColorAt(0.5, QColor("#D8F3F8"))
        hg.setColorAt(1, QColor("#B5DCE5"))

        p.setPen(QPen(
            QColor("#211F24"), 9,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin
        ))
        p.setBrush(QBrush(hg))
        p.drawPath(hood)

        shine = QPainterPath()
        shine.moveTo(250, 55)
        shine.cubicTo(330, 65, 365, 100, 405, 125)
        shine.cubicTo(375, 145, 345, 150, 320, 150)
        shine.cubicTo(350, 190, 360, 220, 320, 245)
        shine.cubicTo(370, 220, 390, 180, 365, 140)
        shine.closeSubpath()

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#B6E0E9"))
        p.drawPath(shine)
        p.restore()

        self.draw_face(p, t)

        # Floating hearts
        for h in self.heart_particles:
            alpha = int(220 * max(0, min(1, h["life"])))
            self.draw_heart(
                p, h["x"], h["y"], h["size"],
                QColor(255, 80, 125, alpha)
            )

        # Goodbye sparkle gesture
        if self.goodbye_active:
            phase = self.goodbye_timer * 8

            for i in range(3):
                self.draw_sparkle(
                    p,
                    390 + i * 13,
                    245 - i * 18 + math.sin(phase + i) * 5,
                    7, phase * 15,
                    180 * self.goodbye_timer
                )

        # Ambient sparkles
        sparkle_data = [
            (65, 300, 15, 0),
            (420, 260, 12, 1.5),
            (105, 55, 10, 2.5),
            (390, 70, 9, 0.8),
            (70, 180, 8, 3.2),
            (450, 190, 10, 4.0)
        ]

        for x, y, size, phase in sparkle_data:
            fy = math.sin(t * 2 + phase) * 9
            rotation = math.sin(t + phase) * 30
            alpha = 150 + 100 * (
                0.5 + 0.5 * math.sin(t * 3 + phase)
            )
            self.draw_sparkle(
                p, x, y + fy, size, rotation, alpha
            )

        # Floating particles
        for i in range(8):
            angle = t * 0.7 + i * math.tau / 8
            x = 250 + math.cos(angle) * 225
            y = 240 + math.sin(angle) * 180
            alpha = 80 + 100 * (
                0.5 + 0.5 * math.sin(t * 2 + i)
            )

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, int(alpha)))
            p.drawEllipse(QPointF(x, y), 2.5, 2.5)

        p.end()

    # =========================================
    # MOUSE EVENTS
    # =========================================

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            now = time.monotonic()
            self.click_times = [
                t for t in self.click_times if now - t < 1.0
            ]
            self.click_times.append(now)

            self.dragging = True
            self.drag_offset = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )

            if len(self.click_times) >= 2:
                self.set_expression("angry", 2.0, force=True)
                self.bounce = 16
                self.squash = 1.0
                self.click_times.clear()
            else:
                self.set_expression("happy", 2.0, force=True)
                self.bounce = 10

            event.accept()

        elif event.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)

            open_action = menu.addAction("Open Assistant")
            menu.addSeparator()

            size_menu = menu.addMenu("Ghost Size")

            for size in (150, 200, 250, 300, 350, 400, 500, 600):
                action = size_menu.addAction(f"{size} px")
                action.setCheckable(True)
                action.setChecked(self.ghost_size == size)
                action.triggered.connect(
                    lambda checked=False, s=size: self.set_ghost_size(s)
                )

            size_menu.addSeparator()
            size_menu.addAction("Increase Size (+)", self.increase_size)
            size_menu.addAction("Decrease Size (-)", self.decrease_size)
            custom_action = size_menu.addAction("Custom size...")

            expression_menu = menu.addMenu("Expressions")

            labels = {
                "idle": "Normal",
                "happy": "Happy",
                "surprised": "Surprised",
                "sleepy": "Sleepy",
                "shy": "Shy",
                "angry": "Angry",
                "thinking": "Thinking",
                "love": "Love"
            }

            for expression, label in labels.items():
                action = expression_menu.addAction(label)
                action.triggered.connect(
                    lambda checked=False, e=expression:
                    self.set_expression(e, 5.0, force=True)
                )

            menu.addSeparator()
            hide_action = menu.addAction("Hide Ghost")
            exit_action = menu.addAction("Exit Nova")

            selected = menu.exec(event.globalPosition().toPoint())

            if selected is None:
                pass
            elif selected == open_action:
                self.open_requested.emit()
            elif selected == custom_action:
                self.custom_size()
            elif selected == hide_action:
                self.hide()
                self.hidden.emit()
            elif selected == exit_action:
                self.exit_requested.emit()

            event.accept()

    def mouseMoveEvent(self, event):
        if (
            self.dragging
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(
                event.globalPosition().toPoint() - self.drag_offset
            )
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.bounce = max(self.bounce, 8)
            self.squash = 1.0
            self.save_position()
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.click_times.clear()
            self.set_expression("happy", 2.0, force=True)
            self.bounce = 12
            self.open_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.increase_size()
            event.accept()
        elif event.key() in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.decrease_size()
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.hidden.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


# =============================================
# START GHOST
# =============================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Ghost Companion")

    ghost = GhostWidget()
    ghost.exit_requested.connect(app.quit)
    ghost.show_bot()

    sys.exit(app.exec())
