import sys
import math
import time
import random

from PySide6.QtWidgets import QApplication, QWidget, QMenu, QInputDialog
from PySide6.QtCore import Qt, QTimer, QPoint, QPointF, QRectF, QEvent
from PySide6.QtGui import (
    QPainter, QPainterPath, QColor, QPen, QBrush,
    QRadialGradient, QLinearGradient, QCursor, QFont
)


class MummyBot(QWidget):
    W, H = 420, 520
    MIN_SIZE, MAX_SIZE = 180, 840

    EXPRESSIONS = (
        "idle", "happy", "sad",
        "surprised", "love", "sleepy"
    )

    IVORY = "#F2E5CE"
    LIGHT = "#FFF8E9"
    SHADOW = "#AD9878"
    OUTLINE = "#28212B"
    GOLD = "#E9B64F"
    GOLD_LIGHT = "#FFE6A0"
    BLUE = "#123B69"
    BLUE_LIGHT = "#276D99"
    TEAL = "#28C5B8"
    AMBER = "#FFD33D"

    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings

        self.setWindowTitle("NOVA Egyptian Guardian")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self.setMouseTracking(True)
        self.resize(420, 520)
        if self.settings is not None:
            try:
                saved_w = max(self.MIN_SIZE, min(self.MAX_SIZE, int(self.settings.value('mummy_width', 420))))
                self.resize(saved_w, round(saved_w * self.H / self.W))
            except (TypeError, ValueError): pass

        self.elapsed = 0.0
        self.last_time = time.monotonic()

        self.expression = "idle"
        self.expression_until = 0.0

        self.look_x = 0.0
        self.look_y = 0.0
        self.target_x = 0.0
        self.target_y = 0.0

        self.bounce = 0.0
        self.squash = 0.0

        self.dragging = False
        self.drag_offset = QPoint()

        self.particles = [
            (
                random.uniform(10, self.W - 10),
                random.uniform(20, self.H - 10),
                random.uniform(1, 3),
                random.uniform(0, math.tau)
            )
            for _ in range(35)
        ]

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(16)

    # ==================================================
    # NOVA INTEGRATION
    # ==================================================

    def show_bot(self):
        self.show()
        self.raise_()

    def set_expression(self, expression, duration=2.5, force=False):
        expression = str(expression).lower().strip()

        if expression not in self.EXPRESSIONS:
            expression = "idle"

        if self.expression == expression and not force:
            return

        self.expression = expression
        self.expression_until = (
            time.monotonic() + duration
            if duration and duration > 0 else 0
        )
        self.update()

    def set_assistant_state(self, state):
        mapping = {
            "idle": "idle",
            "listening": "surprised",
            "thinking": "idle",
            "speaking": "happy",
            "error": "sad",
            "sleeping": "sleepy"
        }

        self.set_expression(
            mapping.get(str(state).lower(), "idle"),
            duration=0,
            force=True
        )

    # ==================================================
    # ANIMATION
    # ==================================================

    def animate(self):
        now = time.monotonic()
        dt = min(now - self.last_time, 0.05)

        self.last_time = now
        self.elapsed += dt

        self.bounce *= math.exp(-dt * 5)
        self.squash *= math.exp(-dt * 7)

        self.look_x += (
            self.target_x - self.look_x
        ) * min(1, dt * 6)

        self.look_y += (
            self.target_y - self.look_y
        ) * min(1, dt * 6)

        if self.expression_until and now >= self.expression_until:
            self.expression = "idle"
            self.expression_until = 0

        self.update_cursor_tracking()
        self.update()

    def update_cursor_tracking(self):
        cursor = QCursor.pos()
        center = self.mapToGlobal(
            QPoint(self.width() // 2, self.height() // 2)
        )

        self.target_x = max(
            -1, min(1, (cursor.x() - center.x()) / 350)
        )
        self.target_y = max(
            -1, min(1, (cursor.y() - center.y()) / 450)
        )

    # ==================================================
    # DRAWING HELPERS
    # ==================================================

    def pen(self, color=None, width=3):
        return QPen(
            QColor(color or self.OUTLINE),
            width,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin
        )

    def gradient(self, x1, y1, x2, y2, stops):
        g = QLinearGradient(x1, y1, x2, y2)

        for position, color in stops:
            g.setColorAt(position, QColor(color))

        return QBrush(g)

    def cubic(self, p, start, c1, c2, end, color, width):
        path = QPainterPath()
        path.moveTo(*start)
        path.cubicTo(*c1, *c2, *end)

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(self.pen(color, width))
        p.drawPath(path)

    def bandage(self, p, start, c1, c2, end, width=9):
        path = QPainterPath()
        path.moveTo(*start)
        path.cubicTo(*c1, *c2, *end)

        # Cloth shadow
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(self.pen("#A18C70", width + 3))
        p.drawPath(path)

        # Main ivory cloth
        p.setPen(self.pen(self.IVORY, width))
        p.drawPath(path)

        # Cloth highlight
        p.setPen(self.pen("#FFFDF5", max(1, width * 0.16)))
        p.drawPath(path)

    def diamond(self, p, x, y, size, color=None):
        path = QPainterPath()
        path.moveTo(x, y - size)
        path.lineTo(x + size * 0.7, y)
        path.lineTo(x, y + size)
        path.lineTo(x - size * 0.7, y)
        path.closeSubpath()

        p.setBrush(QColor(color or self.GOLD))
        p.setPen(self.pen("#80552A", 1.5))
        p.drawPath(path)

    def sparkle(self, p, x, y, size, color):
        p.setPen(self.pen(color, 2))
        p.drawLine(QPointF(x - size, y), QPointF(x + size, y))
        p.drawLine(QPointF(x, y - size), QPointF(x, y + size))

    # ==================================================
    # AMBIENCE
    # ==================================================

    def draw_aura(self, p):
        glow = QRadialGradient(210, 250, 215)
        glow.setColorAt(0, QColor(26, 165, 169, 22))
        glow.setColorAt(0.65, QColor(20, 100, 135, 12))
        glow.setColorAt(1, QColor(20, 100, 135, 0))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QRectF(0, 10, 420, 500))

    def draw_particles(self, p):
        p.setPen(Qt.PenStyle.NoPen)

        for x, y, size, phase in self.particles:
            offset = math.sin(
                self.elapsed * 1.1 + phase
            ) * 7

            alpha = int(
                25 + 35 * (
                    0.5 + 0.5 * math.sin(
                        self.elapsed * 1.5 + phase
                    )
                )
            )

            p.setBrush(QColor(244, 193, 95, alpha))
            p.drawEllipse(
                QPointF(x, y + offset), size, size
            )

    # ==================================================
    # ROYAL SCARF AND HOOD
    # ==================================================

    def draw_scarf(self, p):
        # Left flowing royal scarf
        left = QPainterPath()
        left.moveTo(112, 145)
        left.cubicTo(73, 188, 40, 237, 24, 300)
        left.cubicTo(38, 286, 65, 288, 86, 306)
        left.cubicTo(72, 248, 104, 198, 142, 173)
        left.closeSubpath()

        p.setBrush(self.gradient(
            20, 150, 125, 300,
            [(0, self.BLUE), (0.55, "#087B83"), (1, self.GOLD)]
        ))
        p.setPen(self.pen(self.GOLD, 4))
        p.drawPath(left)

        # Gold edge
        self.cubic(
            p, (112, 150), (79, 197),
            (37, 255), (24, 300), self.GOLD_LIGHT, 3
        )

        # Right trailing scarf
        right = QPainterPath()
        right.moveTo(288, 151)
        right.cubicTo(337, 191, 374, 235, 392, 285)
        right.cubicTo(361, 270, 345, 277, 329, 300)
        right.cubicTo(325, 239, 296, 202, 270, 180)
        right.closeSubpath()

        p.setBrush(self.gradient(
            290, 160, 390, 300,
            [(0, "#087D85"), (0.6, self.BLUE), (1, self.GOLD)]
        ))
        p.setPen(self.pen(self.GOLD, 4))
        p.drawPath(right)

        # Egyptian motifs on the scarf
        self.diamond(p, 48, 275, 7, self.GOLD_LIGHT)
        self.diamond(p, 370, 266, 7, self.GOLD_LIGHT)

        self.sparkle(p, 65, 240, 6, self.TEAL)
        self.sparkle(p, 355, 238, 6, self.GOLD_LIGHT)

    def draw_hood(self, p):
        # Back silhouette of royal headdress
        hood = QPainterPath()
        hood.moveTo(210, 20)
        hood.cubicTo(117, 12, 65, 66, 57, 143)
        hood.cubicTo(38, 178, 65, 215, 102, 228)
        hood.lineTo(117, 168)
        hood.cubicTo(95, 112, 126, 64, 210, 53)
        hood.cubicTo(294, 64, 325, 112, 303, 168)
        hood.lineTo(318, 228)
        hood.cubicTo(355, 215, 382, 178, 363, 143)
        hood.cubicTo(355, 66, 303, 12, 210, 20)
        hood.closeSubpath()

        p.setBrush(self.gradient(
            60, 30, 355, 225,
            [(0, "#102D56"), (0.42, "#174E7D"), (0.75, "#0B315B"), (1, "#071E3D")]
        ))
        p.setPen(self.pen(self.OUTLINE, 5))
        p.drawPath(hood)

        # Alternating gold stripes on the hood
        for side in ("left", "right"):
            if side == "left":
                self.bandage(
                    p, (72, 80), (56, 115),
                    (63, 170), (99, 207), 13
                )
                self.bandage(
                    p, (104, 39), (77, 68),
                    (70, 100), (74, 139), 9
                )
            else:
                self.bandage(
                    p, (348, 80), (364, 115),
                    (357, 170), (321, 207), 13
                )
                self.bandage(
                    p, (316, 39), (343, 68),
                    (350, 100), (346, 139), 9
                )

        # Gold borders
        self.cubic(
            p, (78, 62), (43, 100),
            (52, 186), (104, 218), self.GOLD, 5
        )
        self.cubic(
            p, (342, 62), (377, 100),
            (368, 186), (316, 218), self.GOLD, 5
        )

    # ==================================================
    # PHARAOH CROWN
    # ==================================================

    def draw_crown(self, p):
        # Gold forehead band
        band = QPainterPath()
        band.moveTo(83, 83)
        band.cubicTo(128, 49, 292, 49, 337, 83)
        band.lineTo(330, 105)
        band.cubicTo(268, 77, 152, 77, 90, 105)
        band.closeSubpath()

        p.setBrush(self.gradient(
            80, 75, 340, 105,
            [(0, "#9D642C"), (0.35, self.GOLD_LIGHT),
             (0.7, self.GOLD), (1, "#A96C31")]
        ))
        p.setPen(self.pen("#80552A", 3))
        p.drawPath(band)

        # Blue inlay
        self.cubic(
            p, (94, 83), (143, 57),
            (276, 57), (326, 83), self.BLUE, 6
        )

        # Center royal ornament
        ornament = QPainterPath()
        ornament.moveTo(210, 38)
        ornament.cubicTo(177, 25, 163, 48, 175, 72)
        ornament.lineTo(210, 104)
        ornament.lineTo(245, 72)
        ornament.cubicTo(257, 48, 243, 25, 210, 38)
        ornament.closeSubpath()

        p.setBrush(QColor(self.GOLD))
        p.setPen(self.pen("#79512D", 3))
        p.drawPath(ornament)

        self.diamond(p, 210, 59, 23, self.TEAL)
        self.diamond(p, 210, 59, 12, "#9CF9E9")

        # Side gems
        self.diamond(p, 112, 80, 9, self.GOLD_LIGHT)
        self.diamond(p, 308, 80, 9, self.GOLD_LIGHT)

        # Small crest
        crest = QPainterPath()
        crest.moveTo(196, 37)
        crest.cubicTo(182, 13, 202, 3, 210, 15)
        crest.cubicTo(218, 3, 238, 13, 224, 37)

        p.setBrush(QColor(self.TEAL))
        p.setPen(self.pen("#875B2C", 2))
        p.drawPath(crest)

        self.diamond(p, 210, 22, 8, self.GOLD_LIGHT)

    # ==================================================
    # MUMMY BODY AND ARMOUR
    # ==================================================

    def draw_body(self, p):
        # Legs
        p.setBrush(self.gradient(
            140, 400, 290, 500,
            [(0, self.LIGHT), (0.6, self.IVORY), (1, self.SHADOW)]
        ))
        p.setPen(self.pen(self.OUTLINE, 4))

        p.drawRoundedRect(QRectF(145, 408, 53, 82), 23, 23)
        p.drawRoundedRect(QRectF(222, 408, 53, 82), 23, 23)

        self.bandage(
            p, (147, 436), (159, 445),
            (184, 445), (196, 436), 8
        )
        self.bandage(
            p, (224, 436), (236, 445),
            (260, 445), (273, 435), 8
        )

        self.bandage(
            p, (150, 463), (165, 470),
            (182, 469), (194, 461), 7
        )
        self.bandage(
            p, (226, 462), (242, 470),
            (258, 469), (270, 460), 7
        )

        # Body
        body = QPainterPath()
        body.moveTo(139, 282)
        body.cubicTo(125, 305, 136, 383, 151, 421)
        body.cubicTo(178, 441, 242, 441, 269, 421)
        body.cubicTo(284, 383, 295, 305, 281, 282)
        body.closeSubpath()

        p.setBrush(self.gradient(
            135, 290, 280, 425,
            [(0, "#FFF9EB"), (0.5, self.IVORY), (1, "#C6B190")]
        ))
        p.setPen(self.pen(self.OUTLINE, 4))
        p.drawPath(body)

        # Chest wraps
        self.bandage(
            p, (139, 312), (174, 333),
            (246, 333), (281, 310), 10
        )
        self.bandage(
            p, (143, 348), (175, 366),
            (242, 366), (277, 346), 10
        )
        self.bandage(
            p, (147, 382), (179, 398),
            (240, 398), (273, 380), 9
        )

        # Gold and turquoise shoulder collar
        collar = QPainterPath()
        collar.moveTo(125, 275)
        collar.cubicTo(155, 303, 265, 303, 295, 275)
        collar.lineTo(275, 318)
        collar.cubicTo(242, 341, 178, 341, 145, 318)
        collar.closeSubpath()

        p.setBrush(self.gradient(
            130, 275, 290, 326,
            [(0, "#855325"), (0.4, self.GOLD_LIGHT),
             (0.75, self.GOLD), (1, "#80532A")]
        ))
        p.setPen(self.pen("#5F421F", 3))
        p.drawPath(collar)

        # Collar stripes
        for x in range(150, 275, 15):
            self.diamond(p, x, 310 + abs(210 - x) // 5, 6, self.TEAL)

        # Chest amulet
        medallion = QRectF(183, 326, 54, 57)
        p.setBrush(QColor(self.GOLD))
        p.setPen(self.pen("#754D26", 3))
        p.drawEllipse(medallion)

        p.setBrush(QColor(self.TEAL))
        p.setPen(self.pen("#095A59", 2))
        p.drawEllipse(QRectF(192, 335, 36, 38))

        self.diamond(p, 210, 354, 16, "#9AF5DE")

        # Belt
        belt = QPainterPath()
        belt.moveTo(137, 386)
        belt.cubicTo(174, 405, 246, 405, 283, 386)
        belt.lineTo(277, 411)
        belt.cubicTo(240, 431, 180, 431, 143, 411)
        belt.closeSubpath()

        p.setBrush(QColor(self.BLUE))
        p.setPen(self.pen(self.GOLD, 4))
        p.drawPath(belt)

        self.diamond(p, 210, 407, 16, self.GOLD_LIGHT)
        self.diamond(p, 210, 407, 9, self.TEAL)

        # Arms
        p.setBrush(QColor(self.IVORY))
        p.setPen(self.pen(self.OUTLINE, 4))
        p.drawEllipse(QRectF(93, 288, 55, 65))
        p.drawEllipse(QRectF(272, 288, 55, 65))

        self.bandage(
            p, (97, 305), (110, 317),
            (129, 323), (144, 329), 8
        )
        self.bandage(
            p, (276, 307), (292, 319),
            (310, 324), (323, 330), 8
        )

        # Golden arm bracers
        p.setBrush(QColor(self.GOLD))
        p.setPen(self.pen("#79512D", 2))
        p.drawRoundedRect(QRectF(101, 327, 43, 13), 5, 5)
        p.drawRoundedRect(QRectF(276, 327, 43, 13), 5, 5)

        self.diamond(p, 122, 333, 5, self.TEAL)
        self.diamond(p, 298, 333, 5, self.TEAL)

    # ==================================================
    # HEAD AND FACE
    # ==================================================

    def draw_head(self, p):
        head = QPainterPath()
        head.moveTo(210, 63)
        head.cubicTo(120, 52, 65, 103, 63, 190)
        head.cubicTo(60, 263, 111, 313, 174, 318)
        head.cubicTo(246, 325, 310, 293, 342, 243)
        head.cubicTo(364, 193, 350, 105, 296, 76)
        head.cubicTo(267, 61, 236, 59, 210, 63)
        head.closeSubpath()

        p.setBrush(self.gradient(
            65, 65, 350, 315,
            [(0, "#FFF9EA"), (0.5, self.IVORY), (1, "#C2AE90")]
        ))
        p.setPen(self.pen(self.OUTLINE, 5))
        p.drawPath(head)

        # Head bandages clipped to silhouette
        p.save()
        p.setClipPath(head)

        self.bandage(
            p, (45, 108), (120, 48),
            (282, 48), (373, 115), 17
        )
        self.bandage(
            p, (40, 145), (117, 91),
            (277, 89), (374, 149), 15
        )

        self.bandage(
            p, (42, 255), (112, 304),
            (294, 320), (376, 248), 17
        )
        self.bandage(
            p, (61, 281), (137, 325),
            (274, 331), (355, 279), 11
        )

        # Fine linen seams
        self.cubic(
            p, (70, 112), (140, 79),
            (276, 78), (344, 113),
            self.SHADOW, 1.5
        )
        self.cubic(
            p, (67, 269), (138, 307),
            (276, 313), (346, 266),
            self.SHADOW, 1.5
        )

        p.restore()

        # Dark eye opening
        face = QPainterPath()
        face.moveTo(64, 187)
        face.cubicTo(115, 151, 303, 149, 357, 190)
        face.cubicTo(306, 231, 119, 234, 64, 195)
        face.closeSubpath()

        p.setBrush(self.gradient(
            0, 150, 0, 235,
            [(0, "#302634"), (0.48, "#07060B"), (1, "#29212C")]
        ))
        p.setPen(self.pen("#312834", 3))
        p.drawPath(face)

        # Upper edge gold details
        self.cubic(
            p, (66, 186), (124, 149),
            (293, 147), (355, 187),
            "#8A6335", 1.5
        )

        # Pharaoh head ornament
        self.draw_crown(p)

        # Side hanging royal ornaments
        for x in (76, 344):
            p.setPen(self.pen(self.GOLD, 3))
            p.drawLine(QPointF(x, 145), QPointF(x - 4, 174))
            self.diamond(p, x - 4, 179, 7, self.TEAL)

    # ==================================================
    # EYE ANIMATION ONLY
    # NO MOUTH OR NOSE
    # ==================================================

    def draw_heart(self, p, x, y, size):
        path = QPainterPath()
        path.moveTo(x, y + size * 0.45)

        path.cubicTo(
            x - size * 1.15, y - size * 0.2,
            x - size * 0.6, y - size * 0.95,
            x, y - size * 0.4
        )
        path.cubicTo(
            x + size * 0.6, y - size * 0.95,
            x + size * 1.15, y - size * 0.2,
            x, y + size * 0.45
        )
        path.closeSubpath()

        p.setPen(self.pen("#8A303C", 1.5))
        p.setBrush(QColor("#FF6984"))
        p.drawPath(path)

    def eye_glow(self, p, x, y, color=None):
        color = QColor(color or self.AMBER)

        glow = QRadialGradient(x, y, 43)
        c = QColor(color)
        c.setAlpha(150)
        glow.setColorAt(0, c)

        c = QColor(color)
        c.setAlpha(60)
        glow.setColorAt(0.5, c)

        c = QColor(color)
        c.setAlpha(0)
        glow.setColorAt(1, c)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QRectF(x - 43, y - 43, 86, 86))

    def draw_eye(self, p, x, y):
        exp = self.expression

        gx = self.look_x * 6
        gy = self.look_y * 5

        # HAPPY
        if exp == "happy":
            p.setPen(self.pen(self.AMBER, 8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(
                QRectF(x - 24, y - 11, 48, 32),
                0, 180 * 16
            )
            return

        # SLEEPY
        if exp == "sleepy":
            blink = 0.5 + 0.5 * math.sin(self.elapsed * 2)
            p.setPen(self.pen("#E2C18B", 7))
            p.drawLine(
                QPointF(x - 23, y + blink * 5),
                QPointF(x + 23, y + blink * 5)
            )
            return

        # LOVE
        if exp == "love":
            self.eye_glow(p, x, y, "#FF547A")
            self.draw_heart(p, x, y, 20)
            return

        # SURPRISED
        if exp == "surprised":
            pulse = 1 + math.sin(self.elapsed * 5) * 0.05
            w, h = 29 * pulse, 43 * pulse

            self.eye_glow(p, x, y)

            p.setPen(self.pen("#C78B2D", 2))
            p.setBrush(QColor("#FFF0C5"))
            p.drawEllipse(
                QRectF(x - w / 2, y - h / 2, w, h)
            )

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#C78B27"))
            p.drawEllipse(
                QPointF(x + gx, y + gy), 7, 11
            )
            return

        # SAD
        if exp == "sad":
            p.setPen(self.pen("#E7CDA4", 2))
            p.setBrush(QColor("#F4C55B"))
            p.drawEllipse(QRectF(x - 15, y - 12, 30, 29))

            p.setPen(self.pen("#D9B9A3", 4))
            p.drawArc(
                QRectF(x - 21, y - 20, 42, 27),
                0, 150 * 16
            )

            # Animated tear drops
            tear_y = 0 + (self.elapsed * 48) % 30

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#65D4ED"))

            drop = QPainterPath()
            drop.moveTo(x + 18, y + 8 + tear_y)
            drop.cubicTo(
                x + 34, y + 20 + tear_y,
                x + 29, y + 35 + tear_y,
                x + 18, y + 37 + tear_y
            )
            drop.cubicTo(
                x + 7, y + 31 + tear_y,
                x + 6, y + 18 + tear_y,
                x + 18, y + 8 + tear_y
            )
            p.drawPath(drop)
            return

        # NORMAL WITH AUTOMATIC BLINK
        blink = (self.elapsed % 4.5) > 4.36

        if blink:
            p.setPen(self.pen(self.GOLD_LIGHT, 6))
            p.drawLine(
                QPointF(x - 22, y),
                QPointF(x + 22, y)
            )
            return

        pulse = 1 + math.sin(self.elapsed * 3) * 0.07
        eye_w = 19 * pulse
        eye_h = 28 * pulse

        self.eye_glow(p, x, y)

        # Amber iris
        p.setPen(self.pen("#B87920", 1.5))
        p.setBrush(self.gradient(
            x, y - 17, x, y + 17,
            [(0, "#FFF29A"), (0.45, "#FFDD42"), (1, "#F2A715")]
        ))
        p.drawEllipse(
            QRectF(
                x - eye_w / 2 + gx,
                y - eye_h / 2 + gy,
                eye_w, eye_h
            )
        )

        # Eye sparkle
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(
            QPointF(x - 5 + gx, y - 9 + gy), 4, 5
        )

    def draw_eyes(self, p):
        self.draw_eye(p, 154, 192)
        self.draw_eye(p, 266, 192)

    # ==================================================
    # FINAL PAINT
    # ==================================================

    def paintEvent(self, event):
        p = QPainter(self)

        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        p.scale(
            self.width() / self.W,
            self.height() / self.H
        )

        bob = math.sin(self.elapsed * 1.7) * 4

        p.translate(
            math.sin(self.elapsed * 0.8) * 2,
            bob - self.bounce
        )

        p.translate(210, 260)
        p.scale(
            1 + self.squash * 0.025,
            1 - self.squash * 0.025
        )
        p.translate(-210, -260)

        self.draw_aura(p)
        self.draw_particles(p)
        self.draw_scarf(p)
        self.draw_hood(p)
        self.draw_body(p)
        self.draw_head(p)
        self.draw_eyes(p)

        # Magical sparkle effects
        if self.expression in ("happy", "love"):
            self.sparkle(p, 65, 165, 9, self.GOLD_LIGHT)
            self.sparkle(p, 350, 265, 7, self.TEAL)

        if self.expression == "sleepy":
            p.setPen(QColor("#FFE4A1"))
            p.setFont(QFont("Segoe UI", 17, QFont.Weight.Bold))
            p.drawText(QRectF(326, 90, 50, 25), "Z")
            p.drawText(QRectF(350, 65, 40, 25), "z")

        p.end()

    # ==================================================
    # DRAGGING
    # ==================================================

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True

            self.drag_offset = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )

            self.bounce = 9
            self.squash = 1
            event.accept()
            return

        if event.button() == Qt.MouseButton.RightButton:
            self.show_context_menu(
                event.globalPosition().toPoint()
            )
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self.dragging
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(
                event.globalPosition().toPoint()
                - self.drag_offset
            )
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.bounce = 5
            self.squash = 1
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def event(self, event):
        try:
            if event.type() == QEvent.Type.NativeGesture and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                factor = 1.0 + float(event.value())
                self.set_mummy_width(round(self.width() * max(0.80, min(1.20, factor))))
                event.accept()
                return True
        except (AttributeError, TypeError, ValueError):
            pass
        return super().event(event)

    # ==================================================
    # RESIZING
    # ==================================================

    def set_mummy_width(self, width):
        width = max(self.MIN_SIZE, min(self.MAX_SIZE, int(width)))
        self.resize(width, round(width * self.H / self.W))
        if self.settings is not None: self.settings.setValue('mummy_width', width)

    def increase_size(self):
        self.set_mummy_width(self.width() + 30)

    def decrease_size(self):
        self.set_mummy_width(self.width() - 30)

    def custom_size(self):
        width, ok = QInputDialog.getInt(
            self, "Guardian Size", "Choose width:",
            self.width(), self.MIN_SIZE, self.MAX_SIZE, 10
        )

        if ok:
            self.set_mummy_width(width)

    # ==================================================
    # RIGHT-CLICK MENU
    # ==================================================

    def show_context_menu(self, position):
        menu = QMenu(self)

        menu.setStyleSheet("""
            QMenu {
                background: #111B2A;
                color: #FFF5E6;
                border: 1px solid #DDAA50;
                padding: 7px;
            }
            QMenu::item {
                padding: 9px 25px;
                border-radius: 5px;
            }
            QMenu::item:selected {
                background: #155B72;
            }
        """)

        emotions = menu.addMenu("Guardian Emotions")

        labels = {
            "idle": "Normal / Idle",
            "happy": "Happy",
            "sad": "Sad",
            "surprised": "Surprised",
            "love": "Love",
            "sleepy": "Sleepy"
        }

        for expression, label in labels.items():
            action = emotions.addAction(label)
            action.triggered.connect(
                lambda checked=False, e=expression:
                self.set_expression(e, 0, force=True)
            )

        sizes = menu.addMenu("Guardian Size")
        sizes.addAction("Increase Size (+)", self.increase_size)
        sizes.addAction("Decrease Size (-)", self.decrease_size)
        sizes.addAction("Custom Size...", self.custom_size)

        menu.addSeparator()
        hide_action = menu.addAction("Hide Guardian")

        if menu.exec(position) == hide_action:
            self.hide()

    # ==================================================
    # KEYBOARD CONTROLS
    # ==================================================

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.increase_size()
            event.accept()
            return

        if event.key() in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.decrease_size()
            event.accept()
            return

        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return

        super().keyPressEvent(event)


def main():
    app = QApplication.instance()

    if app is None:
        app = QApplication(sys.argv)

    app.setApplicationName("NOVA Egyptian Guardian")

    guardian = MummyBot()
    guardian.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())