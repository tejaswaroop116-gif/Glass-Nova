"""bot.py - animated, cursor-aware floating desktop companion for Glass Nova.

An original little hovering robot (pearl shell, navy visor, cyan eyes) painted entirely with
QPainter, so there are no image files and no extra dependencies beyond PySide6.

Behaviour
---------
* Borderless, always-on-top, genuinely transparent window (per-pixel alpha). Fully transparent
  pixels let mouse clicks fall through to the windows underneath.
* It never takes keyboard focus (Qt.WindowDoesNotAcceptFocus + WA_ShowWithoutActivating).
* Idle: floats, breathes, blinks, glances around.
* Cursor aware: polls the real cursor position at ~30 fps (QCursor.pos(), which does not grab
  the mouse), looks toward it, tilts its head, turns curious when the cursor is near, and waves /
  smiles / bounces when the cursor hovers over it. It relaxes back to idle when the cursor leaves
  or stops moving for a few seconds.
* Single click: excited reaction. Double click: open the assistant. Right click: menu.
  Left-drag: move it (kept on-screen, multi-monitor aware, position remembered).
* Assistant states (idle / listening / thinking / speaking) can be pushed in from ui.py with
  set_assistant_state().

Run this file directly to try the bot on its own:  python bot.py
"""
import math
import random
import sys
import time

from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

# ---- tunables ---------------------------------------------------------------------------
W, H = 210, 250            # design size in "character units"; the widget is W*scale x H*scale
FPS = 30                   # animation + cursor polling rate (lower = less CPU)
NEAR_ENTER, NEAR_EXIT = 280, 340   # px: cursor distance that starts / ends "curious"
LOOK_RANGE = 900           # px: beyond this the bot stops following the cursor and glances around
IDLE_AFTER = 6.0           # s: stop following a cursor that has not moved for this long
DRAG_THRESHOLD = 6         # px of movement before a press becomes a drag

# Each state is a set of target values; the live values glide toward them (smooth transitions).
STATES = {
    'idle':        dict(happy=0.00, wide=1.00, curious=0.0, mouth=0.00, wave=0.0, glow=0.55, bob=1.0, spark=0.0, think=0.0),
    'curious':     dict(happy=0.10, wide=1.10, curious=1.0, mouth=0.00, wave=0.0, glow=0.80, bob=0.8, spark=0.0, think=0.0),
    'interacting': dict(happy=1.00, wide=1.00, curious=0.0, mouth=0.00, wave=1.0, glow=0.90, bob=1.3, spark=0.35, think=0.0),
    'excited':     dict(happy=0.90, wide=1.15, curious=0.0, mouth=0.55, wave=1.0, glow=1.00, bob=1.8, spark=1.0, think=0.0),
    'listening':   dict(happy=0.15, wide=1.12, curious=0.6, mouth=0.00, wave=0.0, glow=1.00, bob=0.9, spark=0.0, think=0.0),
    'thinking':    dict(happy=0.00, wide=0.95, curious=0.3, mouth=0.00, wave=0.0, glow=0.85, bob=0.8, spark=0.0, think=1.0),
    'speaking':    dict(happy=0.25, wide=1.00, curious=0.0, mouth=0.40, wave=0.0, glow=0.95, bob=1.1, spark=0.0, think=0.0),
}
ASSISTANT_STATES = ('idle', 'listening', 'thinking', 'speaking')

MENU_STYLE = ("QMenu{background:#0c2038;color:#eaf8ff;border:1px solid #3fb8f0;padding:6px;"
              "font-family:'Segoe UI';font-size:10pt;}"
              "QMenu::item{padding:7px 22px;border-radius:6px;}"
              "QMenu::item:selected{background:#1f6f9f;}"
              "QMenu::separator{height:1px;background:#2c5878;margin:5px 8px;}")


def _approach(cur, target, rate, dt):
    """Exponential smoothing: frame-rate independent glide of cur toward target."""
    return cur + (target - cur) * (1.0 - math.exp(-rate * dt))


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class DesktopBot(QWidget):
    open_requested = Signal()   # double-click or menu "Open Assistant"
    exit_requested = Signal()   # menu "Exit Glass Nova"
    hidden = Signal()           # menu "Hide Bot"

    def __init__(self, settings=None, scale=1.0):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.settings = settings
        self._scale = scale
        self.setFixedSize(int(W * scale), int(H * scale))

        # animation state
        self.t = 0.0
        self._last = time.monotonic()
        self._p = dict(STATES['idle'])           # live (smoothed) parameters
        self._look_x = self._look_y = 0.0        # -1..1 where the eyes are pointing
        self._tilt = 0.0                         # head tilt, degrees
        self._float_y = 0.0
        self._by = self._bv = 0.0                # bounce spring (position / velocity)
        self._ant = self._ant_v = 0.0            # antenna sway spring
        self._lid = 1.0                          # eyelid openness (blink)
        self._blink_phase = None
        self._next_blink = time.monotonic() + 2.0
        self._wander = (0.0, 0.0)
        self._wander_next = 0.0

        # interaction state
        self._animate = True
        if settings is not None:
            self._animate = str(settings.value('bot_animate', 'true')).lower() != 'false'
        self.assistant_state = 'idle'
        self._react_until = 0.0
        self._bounce_next = 0.0
        self._hover = False
        self._near = False
        self._menu_open = False
        self._last_cur = QPoint(-1, -1)
        self._last_move = time.monotonic()
        self._press_global = None
        self._grab_offset = QPoint()
        self._dragging = False
        self._last_geo = None
        self._positioned = False
        self._screen_signals = False

        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / FPS))
        self._timer.timeout.connect(self._tick)
        self._click_timer = QTimer(self)         # waits to see whether a click becomes a double-click
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(lambda: self.react(1.5))

    # ---- public API ---------------------------------------------------------------------
    def set_assistant_state(self, state):
        """Called by ui.py: 'idle' | 'listening' | 'thinking' | 'speaking'."""
        self.assistant_state = state if state in ASSISTANT_STATES else 'idle'

    def react(self, duration=1.5):
        """Friendly excited reaction: bounce, wave, sparkles."""
        now = time.monotonic()
        self._react_until = now + duration
        self._bv = -200.0
        self._bounce_next = now + 0.45

    def set_animation_enabled(self, on):
        self._animate = bool(on)
        if self.settings is not None:
            self.settings.setValue('bot_animate', 'true' if on else 'false')
        if on:
            if self.isVisible():
                self._last = time.monotonic()
                self._timer.start()
        else:                                    # freeze in a calm idle pose
            self._timer.stop()
            self._p = dict(STATES['idle'])
            self._look_x = self._look_y = self._tilt = self._by = self._bv = 0.0
            self._float_y = 0.0
            self._hover = self._near = False
            self.update()

    def show_bot(self):
        if not self._positioned:
            self.restore_position()
            self._positioned = True
        self.show()
        self.ensure_on_screen()

    # ---- window position / screens ------------------------------------------------------
    def _geo_at(self, point):
        scr = QGuiApplication.screenAt(point)
        if scr is not None:
            self._last_geo = scr.availableGeometry()
        if self._last_geo is None:
            self._last_geo = QGuiApplication.primaryScreen().availableGeometry()
        return self._last_geo

    def _clamp_pos(self, pos, ref_point):
        geo = self._geo_at(ref_point)
        pad = int(14 * self._scale)              # the transparent margin may hang over the edge
        x = _clamp(pos.x(), geo.left() - pad, geo.right() - self.width() + pad + 1)
        y = _clamp(pos.y(), geo.top() - pad, geo.bottom() - self.height() + pad + 1)
        return QPoint(x, y)

    def _centre_on_a_screen(self, pos):
        c = pos + QPoint(self.width() // 2, self.height() // 2)
        return any(s.availableGeometry().contains(c) for s in QGuiApplication.screens())

    def place_default(self):
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.width() - 20, geo.bottom() - self.height() - 30)

    def restore_position(self):
        pos = None
        if self.settings is not None:
            try:
                x, y = (int(v) for v in str(self.settings.value('bot_pos', '')).split(','))
                pos = QPoint(x, y)
            except (ValueError, TypeError):
                pos = None
        if pos is not None and self._centre_on_a_screen(pos):
            self.move(pos)
        else:
            self.place_default()

    def ensure_on_screen(self):
        """If a monitor was unplugged (or the saved spot is gone) bring the bot back into view."""
        if not self._centre_on_a_screen(self.pos()):
            centre = self.pos() + QPoint(self.width() // 2, self.height() // 2)
            self._last_geo = None
            scr = QGuiApplication.screenAt(centre) or QGuiApplication.primaryScreen()
            self._last_geo = scr.availableGeometry()
            self.move(self._clamp_pos(self.pos(), centre))

    def _save_position(self):
        if self.settings is not None:
            self.settings.setValue('bot_pos', f'{self.x()},{self.y()}')

    def _schedule_ensure(self, *_):
        QTimer.singleShot(600, self.ensure_on_screen)

    # ---- Qt events ----------------------------------------------------------------------
    def showEvent(self, e):
        if not self._screen_signals:
            app = QGuiApplication.instance()
            app.screenAdded.connect(self._schedule_ensure)
            app.screenRemoved.connect(self._schedule_ensure)
            app.primaryScreenChanged.connect(self._schedule_ensure)
            self._screen_signals = True
        if self._animate:
            self._last = time.monotonic()
            self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def closeEvent(self, e):
        self._save_position()
        self._timer.stop()
        e.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_global = e.globalPosition().toPoint()
            self._grab_offset = self._press_global - self.pos()
            self._dragging = False
            e.accept()

    def mouseMoveEvent(self, e):
        if (e.buttons() & Qt.LeftButton) and self._press_global is not None:
            g = e.globalPosition().toPoint()
            if not self._dragging and (g - self._press_global).manhattanLength() >= DRAG_THRESHOLD:
                self._dragging = True
                self._click_timer.stop()
            if self._dragging:
                self.move(self._clamp_pos(g - self._grab_offset, g))
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press_global is not None:
            if self._dragging:
                self._save_position()
            else:                                # a click: wait to see if it becomes a double-click
                self._click_timer.start(QApplication.doubleClickInterval())
            self._press_global = None
            self._dragging = False
            e.accept()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._click_timer.stop()
            self.react(1.8)
            self.open_requested.emit()
            e.accept()

    def contextMenuEvent(self, e):
        self._show_menu(e.globalPos())

    def _show_menu(self, global_pos):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        a_open = menu.addAction('Open Assistant')
        a_anim = menu.addAction('Toggle Animation')
        a_anim.setCheckable(True)
        a_anim.setChecked(self._animate)
        a_hide = menu.addAction('Hide Bot')
        menu.addSeparator()
        a_exit = menu.addAction('Exit Glass Nova')
        self._menu_open = True
        try:
            chosen = menu.exec(global_pos)
        finally:
            self._menu_open = False
        if chosen is a_open:
            self.open_requested.emit()
        elif chosen is a_anim:
            self.set_animation_enabled(not self._animate)
        elif chosen is a_hide:
            self.hide()
            self.hidden.emit()
        elif chosen is a_exit:
            self.exit_requested.emit()

    # ---- animation tick -----------------------------------------------------------------
    def _cursor_pos(self):
        return QCursor.pos()                     # read-only poll; never grabs the mouse

    def _pick_state(self, now):
        if now < self._react_until:
            return 'excited'
        if self._hover:
            return 'interacting'
        if self.assistant_state != 'idle':
            return self.assistant_state
        if self._near:
            return 'curious'
        return 'idle'

    def _tick(self):
        now = time.monotonic()
        dt = _clamp(now - self._last, 0.001, 0.05)
        self._last = now
        self.t += dt
        t = self.t
        s = self._scale

        # --- cursor geometry
        cur = self._cursor_pos()
        if cur != self._last_cur:
            self._last_cur = cur
            self._last_move = now
        gx = self.x() + self.width() / 2.0
        gy = self.y() + self.height() / 2.0 - 22 * s          # head centre on screen
        dx, dy = cur.x() - gx, cur.y() - gy
        dist = math.hypot(dx, dy)
        self._near = dist < (NEAR_EXIT if self._near else NEAR_ENTER)
        lx = (cur.x() - (self.x() + self.width() / 2.0)) / s
        ly = (cur.y() - (self.y() + self.height() / 2.0)) / s
        hover = (not self._menu_open) and ((lx / 72.0) ** 2 + ((ly + 2) / 102.0) ** 2 <= 1.0)
        if hover and not self._hover:                         # cursor just landed on the bot
            self._bv = -110.0
            self._blink_phase = 0.0
        self._hover = hover

        state = self._pick_state(now)
        target = STATES[state]
        for k, v in target.items():
            if k == 'mouth' and state == 'speaking':
                v = 0.12 + 0.6 * abs(math.sin(t * 9.0) * math.sin(t * 2.3 + 1.0))
            rate = 16.0 if k == 'mouth' else (9.0 if k == 'happy' else 6.0)
            self._p[k] = _approach(self._p[k], v, rate, dt)
        P = self._p

        # --- where to look
        following = (now - self._last_move < IDLE_AFTER or dist < NEAR_ENTER) and dist < LOOK_RANGE
        if state == 'thinking':
            tx, ty = math.cos(t * 2.1) * 0.8, math.sin(t * 2.1) * 0.45 - 0.35
        elif following and dist > 1.0:
            mag = min(1.0, dist / 150.0)
            tx, ty = dx / dist * mag, dy / dist * mag
        else:
            if now >= self._wander_next:
                self._wander = (random.uniform(-0.55, 0.55), random.uniform(-0.3, 0.3))
                self._wander_next = now + random.uniform(1.8, 4.2)
            tx, ty = self._wander
        self._look_x = _approach(self._look_x, tx, 9.0, dt)
        self._look_y = _approach(self._look_y, ty, 9.0, dt)
        side = 1.0 if self._look_x >= 0 else -1.0
        tilt_t = self._look_x * 7.0 + P['curious'] * 5.0 * side + math.sin(t * 1.1) * 1.2 * P['bob']
        self._tilt = _approach(self._tilt, tilt_t, 6.0, dt)

        # --- blinking
        if self._blink_phase is None and now >= self._next_blink:
            self._blink_phase = 0.0
            self._next_blink = now + (0.28 if random.random() < 0.15 else random.uniform(2.4, 5.5))
        if self._blink_phase is not None:
            self._blink_phase += dt / 0.17
            if self._blink_phase >= 1.0:
                self._blink_phase = None
                self._lid = 1.0
            else:
                self._lid = 1.0 - math.sin(math.pi * self._blink_phase)

        # --- springs: bounce + antenna
        if state == 'excited' and now >= self._bounce_next:
            self._bv = -150.0
            self._bounce_next = now + 0.45
        self._bv += (-70.0 * self._by - 6.5 * self._bv) * dt
        self._by += self._bv * dt
        a_target = _clamp(-self._tilt * 1.4 - self._bv * 0.04, -9.0, 9.0)
        self._ant_v += (-90.0 * (self._ant - a_target) - 7.0 * self._ant_v) * dt
        self._ant += self._ant_v * dt

        self._float_y = -4.0 + math.sin(t * 1.7) * 4.0 * P['bob']
        self.update()

    # ---- painting: original red/navy/gold mechanical spider companion -----------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        p.translate(self.width() / 2.0, self.height() / 2.0)
        p.scale(self._scale, self._scale)
        fy = self._float_y + self._by
        self._paint_shadow(p, fy)
        self._paint_thruster(p, fy)
        self._paint_spider_legs(p, fy)
        self._paint_body(p, fy)
        self._paint_head(p, fy)
        self._paint_sparkles(p, fy)

    @staticmethod
    def _flat_glow(p, cx, cy, radius, squash, color, alpha):
        p.save()
        p.translate(cx, cy)
        p.scale(1.0, squash)
        g = QRadialGradient(QPointF(0, 0), radius)
        g.setColorAt(0.0, QColor(color[0], color[1], color[2], int(_clamp(alpha, 0, 255))))
        g.setColorAt(1.0, QColor(color[0], color[1], color[2], 0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 0), radius, radius)
        p.restore()

    def _paint_shadow(self, p, fy):
        k = _clamp(1.0 + fy / 90.0, 0.55, 1.2)
        self._flat_glow(p, 0, 104, 34 * k, 0.2, (0, 10, 30), 70 * k)

    def _paint_thruster(self, p, fy):
        flick = 0.82 + 0.18 * math.sin(self.t * 11.0)
        self._flat_glow(p, 0, 77 + fy, 28, 0.32, (50, 175, 255), 90 * self._p['glow'] * flick)

    def _joint(self, p, x, y, radius=5):
        g = QRadialGradient(QPointF(x - 1.5, y - 2), radius * 1.8)
        g.setColorAt(0, QColor(255, 231, 164))
        g.setColorAt(0.45, QColor(210, 145, 49))
        g.setColorAt(1, QColor(92, 52, 25))
        p.setPen(QPen(QColor(57, 32, 24), 1.6))
        p.setBrush(g)
        p.drawEllipse(QPointF(x, y), radius, radius)

    def _draw_mechanical_arm(self, p, points, phase=0.0, wave=0.0):
        # Gold segmented limbs with dark outlines, piston details and articulated joints.
        pts = [QPointF(x, y) for x, y in points]
        p.save()
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(35, 25, 31, 220), 12, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        gold = QLinearGradient(0, min(pt.y() for pt in pts) - 5, 0, max(pt.y() for pt in pts) + 5)
        gold.setColorAt(0.0, QColor(255, 225, 142))
        gold.setColorAt(0.35, QColor(226, 164, 65))
        gold.setColorAt(0.7, QColor(163, 99, 37))
        gold.setColorAt(1.0, QColor(255, 210, 111))
        p.setPen(QPen(QColor(255, 222, 147), 1.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(gold, 8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
        # Thin bright highlight down each segment.
        p.setPen(QPen(QColor(255, 241, 194, 210), 1.6, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(path)
        for i, pt in enumerate(pts):
            self._joint(p, pt.x(), pt.y(), 5.2 if i else 4.4)
        # Small piston marks between joints.
        for a, b in zip(pts, pts[1:]):
            mid = QPointF((a.x() * .42 + b.x() * .58), (a.y() * .42 + b.y() * .58))
            p.setPen(QPen(QColor(80, 47, 28), 2.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(mid.x() - 2, mid.y() - 2), QPointF(mid.x() + 2, mid.y() + 2))
        p.restore()

    def _paint_spider_legs(self, p, fy):
        t, P = self.t, self._p
        sway = math.sin(t * 1.5) * 2.0
        wave = P['wave'] * (8 + 8 * math.sin(t * 10.0))
        # Four articulated gold appendages, two upper and two lower.
        paths = [
            [(-20, 19 + fy), (-47, 1 + fy + sway), (-78, -18 + fy + sway), (-93, -5 + fy + sway)],
            [(20, 19 + fy), (47, 1 + fy - sway), (78, -18 + fy - sway), (93, -5 + fy - sway)],
            [(-21, 39 + fy), (-47, 51 + fy - sway), (-75, 66 + fy - sway), (-88, 53 + fy - sway)],
            [(21, 39 + fy), (47, 51 + fy + sway), (75, 66 + fy + sway), (88, 53 + fy + sway)],
        ]
        for i, path in enumerate(paths):
            adjusted = list(path)
            if i in (0, 1):
                tip_x, tip_y = adjusted[-1]
                adjusted[-1] = (tip_x + (wave if i == 1 else -wave), tip_y - abs(wave) * .35)
            self._draw_mechanical_arm(p, adjusted, phase=i * 1.3, wave=wave)
        # Small gold shoulder housings where the arms attach.
        for sx in (-1, 1):
            self._joint(p, sx * 22, 20 + fy, 7)

    def _paint_body(self, p, fy):
        t, P = self.t, self._p
        # Compact navy armored torso.
        torso = QPainterPath()
        torso.moveTo(-22, 19 + fy)
        torso.quadTo(-31, 23 + fy, -29, 39 + fy)
        torso.lineTo(-23, 69 + fy)
        torso.quadTo(0, 80 + fy, 23, 69 + fy)
        torso.lineTo(29, 39 + fy)
        torso.quadTo(31, 23 + fy, 22, 19 + fy)
        torso.closeSubpath()
        g = QLinearGradient(-25, 20 + fy, 25, 73 + fy)
        g.setColorAt(0, QColor(28, 54, 91))
        g.setColorAt(.45, QColor(11, 27, 52))
        g.setColorAt(1, QColor(3, 12, 31))
        p.setPen(QPen(QColor(35, 15, 27), 2.2))
        p.setBrush(g)
        p.drawPath(torso)
        # Red chest plate with gold V-shaped trim (no web pattern).
        chest = QPainterPath()
        chest.moveTo(-18, 24 + fy); chest.lineTo(0, 36 + fy)
        chest.lineTo(18, 24 + fy); chest.lineTo(13, 51 + fy)
        chest.lineTo(0, 60 + fy); chest.lineTo(-13, 51 + fy); chest.closeSubpath()
        p.setPen(QPen(QColor(255, 205, 103), 3.3, Qt.SolidLine, Qt.RoundJoin))
        p.setBrush(QColor(174, 27, 50))
        p.drawPath(chest)
        p.setPen(QPen(QColor(255, 226, 146), 1.2))
        p.drawLine(QPointF(-12, 29 + fy), QPointF(0, 39 + fy))
        p.drawLine(QPointF(12, 29 + fy), QPointF(0, 39 + fy))
        # Belt and tiny cyan reactor.
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(5, 15, 31))
        p.drawRoundedRect(QRectF(-21, 61 + fy, 42, 10), 4, 4)
        pulse = .75 + .25 * math.sin(t * 4.0)
        self._flat_glow(p, 0, 45 + fy, 12, 1.0, (35, 190, 255), 90 * P['glow'] * pulse)
        rg = QRadialGradient(QPointF(-1.5, 43.5 + fy), 7)
        rg.setColorAt(0, QColor(231, 255, 255))
        rg.setColorAt(1, QColor(28, 154, 235))
        p.setPen(QPen(QColor(255, 218, 129), 1.5))
        p.setBrush(rg)
        p.drawEllipse(QPointF(0, 45 + fy), 5.2, 5.2)
        # Short armored legs and boots.
        for sx in (-1, 1):
            p.save()
            p.translate(sx * 11, 68 + fy)
            leg = QPainterPath()
            leg.moveTo(-7, 0); leg.lineTo(-8, 20); leg.quadTo(-7, 27, -2, 28)
            leg.lineTo(6, 28); leg.quadTo(10, 26, 8, 20); leg.lineTo(7, 0); leg.closeSubpath()
            p.setPen(QPen(QColor(35, 15, 27), 2))
            p.setBrush(QColor(154, 24, 47))
            p.drawPath(leg)
            p.setPen(QPen(QColor(255, 204, 98), 2.4))
            p.drawLine(QPointF(-7, 4), QPointF(7, 4))
            p.setPen(QPen(QColor(36, 108, 158), 2))
            p.drawLine(QPointF(-5, 14), QPointF(5, 14))
            p.restore()

    def _paint_head(self, p, fy):
        t, P = self.t, self._p
        p.save()
        p.translate(self._look_x * 2.0, fy + math.sin(t * 1.7 - .7) * 1.6 * P['bob'])
        p.translate(0, -25)
        p.rotate(self._tilt)
        p.translate(0, 25)
        # Rounded red helmet with dark navy lower edge.
        head = QPainterPath()
        head.moveTo(0, -73)
        head.cubicTo(34, -74, 43, -50, 39, -26)
        head.cubicTo(36, -5, 18, 9, 0, 12)
        head.cubicTo(-18, 9, -36, -5, -39, -26)
        head.cubicTo(-43, -50, -34, -74, 0, -73)
        head.closeSubpath()
        hg = QLinearGradient(-25, -72, 25, 8)
        hg.setColorAt(0, QColor(255, 67, 83))
        hg.setColorAt(.4, QColor(194, 24, 49))
        hg.setColorAt(1, QColor(93, 13, 39))
        p.setPen(QPen(QColor(32, 15, 28), 2.4))
        p.setBrush(hg)
        p.drawPath(head)
        # Navy faceplate inset.
        visor = QPainterPath()
        visor.moveTo(-31, -47); visor.quadTo(0, -62, 31, -47)
        visor.lineTo(29, -25); visor.quadTo(22, -2, 0, 5)
        visor.quadTo(-22, -2, -29, -25); visor.closeSubpath()
        p.setPen(QPen(QColor(8, 24, 48), 2.0))
        p.setBrush(QColor(5, 22, 43))
        p.drawPath(visor)
        # Eye shape and eye tracking; expressive white-blue lenses, no web motifs.
        eye_open = max(.12, self._lid)
        eye_y = -30
        for sx in (-1, 1):
            p.save()
            p.translate(sx * 16 + self._look_x * 3.0, eye_y + self._look_y * 2.0)
            p.rotate(sx * -12 + self._tilt * .35)
            eye = QPainterPath()
            eye.moveTo(-10, -7 * eye_open)
            eye.quadTo(-2, -13 * eye_open, 10, -8 * eye_open)
            eye.quadTo(7, 2 * eye_open, 1, 8 * eye_open)
            eye.quadTo(-7, 5 * eye_open, -10, -7 * eye_open)
            p.setPen(QPen(QColor(34, 205, 255), 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(QColor(245, 253, 255))
            p.drawPath(eye)
            # Small blue reflection.
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(135, 230, 255, 180))
            p.drawEllipse(QPointF(-3, -4 * eye_open), 2.2, 1.2)
            p.restore()
        # Gold brow accents and subtle red armor shine.
        p.setPen(QPen(QColor(255, 208, 100), 2.0, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(-23, -54), QPointF(-11, -58))
        p.drawLine(QPointF(23, -54), QPointF(11, -58))
        p.setPen(QPen(QColor(255, 167, 157, 150), 2.0, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(-24, -67, 35, 24), 20 * 16, 100 * 16)
        # Tiny mouth indicator changes with expression.
        p.setPen(QPen(QColor(74, 205, 255, 210), 1.8, Qt.SolidLine, Qt.RoundCap))
        if P['mouth'] > .25:
            p.drawArc(QRectF(-5, -2, 10, 6), 190 * 16, 160 * 16)
        else:
            p.drawLine(QPointF(-3, 1), QPointF(3, 1))
        p.restore()

    def _paint_sparkles(self, p, fy):
        P, t = self._p, self.t
        amount = P['spark']
        if amount <= .01:
            return
        p.setPen(QPen(QColor(117, 225, 255, int(180 * amount)), 1.5, Qt.SolidLine, Qt.RoundCap))
        for i in range(4):
            a = t * 1.6 + i * math.pi / 2
            x = math.cos(a) * (51 + 5 * math.sin(t * 4 + i))
            y = fy - 20 + math.sin(a) * 39
            s = 3.0 + 2.0 * math.sin(t * 7 + i)
            p.drawLine(QPointF(x - s, y), QPointF(x + s, y))
            p.drawLine(QPointF(x, y - s), QPointF(x, y + s))

