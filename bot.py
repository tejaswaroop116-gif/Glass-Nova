"""bot.py - animated, cursor-aware floating desktop companion for Glass Nova.

An original hovering robot (pearl shell, gold trim, black visor, glowing ring eyes)
painted entirely with QPainter. Shared between ui.py and standalone execution (`python bot.py`).
"""
import math
import random
import sys
import time

from PySide6.QtCore import Qt, QEvent, QPoint, QPointF, QRectF, QSettings, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor as C, QCursor, QGuiApplication, QLinearGradient,
                           QPainter, QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

APP_NAME = 'Nova'
MENU_CSS = (
    "QMenu{background:#0c2038;color:#eaf8ff;border:1px solid #3fb8f0;padding:6px;font-family:'Segoe UI';font-size:10pt}"
    "QMenu::item{padding:7px 22px;border-radius:6px} QMenu::item:selected{background:#1f6f9f}"
    "QMenu::separator{height:1px;background:#2c5878;margin:5px 8px}"
)

# ---- painting helpers ---------------------------------------------------------------------
cl = lambda v, lo, hi: max(lo, min(hi, v))
ease = lambda a, b, k, dt: a + (b - a) * (1 - math.exp(-k * dt))


def _g(g, stops):
    for pos, col in stops:
        g.setColorAt(pos, C(*col))
    return g


def rgrad(x, y, r, *stops, fx=None, fy=None):
    return _g(QRadialGradient(QPointF(x, y), r, QPointF(x if fx is None else fx, y if fy is None else fy)), stops)


def lgrad(x1, y1, x2, y2, *stops):
    return _g(QLinearGradient(x1, y1, x2, y2), stops)


def st(p, brush=None, pen=None, w=1.0):
    p.setBrush(C(*brush) if isinstance(brush, tuple) else (brush if brush is not None else Qt.NoBrush))
    p.setPen(QPen(C(*pen), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin) if pen else Qt.NoPen)


def glow(p, x, y, r, squash, col, a):
    p.save()
    p.translate(x, y)
    p.scale(1, squash)
    st(p, rgrad(0, 0, r, (0, (*col, int(cl(a, 0, 255)))), (1, (*col, 0))))
    p.drawEllipse(QPointF(0, 0), r, r)
    p.restore()


def gold(x1, y1, x2, y2):
    return lgrad(x1, y1, x2, y2, (0, (112, 72, 26)), (.22, (236, 188, 100)), (.42, (255, 238, 176)), (.68, (198, 142, 54)), (1, (104, 66, 22)))


def pearl(x1, y1, x2, y2):
    return lgrad(x1, y1, x2, y2, (0, (150, 158, 180)), (.2, (226, 231, 240)), (.42, (255, 255, 255)), (.66, (232, 236, 244)), (1, (132, 140, 164)))


def onyx(x, y, r):
    return rgrad(x, y, r, (0, (84, 88, 104)), (.4, (26, 28, 38)), (1, (4, 5, 9)), fx=x - .3 * r, fy=y - .35 * r)


def hl(p, x, y, rx, ry, a=170, ang=0, col=(255, 255, 255)):
    p.save()
    p.translate(x, y)
    p.rotate(ang)
    p.scale(1, ry / rx)
    st(p, rgrad(0, 0, rx, (0, (*col, a)), (.55, (*col, a // 3)), (1, (*col, 0))))
    p.drawEllipse(QPointF(0, 0), rx, rx)
    p.restore()


STATES = {   # target pose per state; live values glide toward these
    'idle':             dict(happy=0, wide=1, curious=0, mouth=0, wave=0, glow=.55, bob=1, spark=0),
    'curious':          dict(happy=.1, wide=1.1, curious=1, mouth=0, wave=0, glow=.8, bob=.8, spark=0),
    'interacting':      dict(happy=1, wide=1, curious=0, mouth=0, wave=1, glow=.9, bob=1.3, spark=.35),
    'excited':          dict(happy=.9, wide=1.15, curious=0, mouth=.55, wave=1, glow=1, bob=1.8, spark=1),
    'listening':        dict(happy=.15, wide=1.12, curious=.6, mouth=0, wave=0, glow=1, bob=.9, spark=0),
    'thinking':         dict(happy=0, wide=.95, curious=.3, mouth=0, wave=0, glow=.85, bob=.8, spark=0),
    'speaking':         dict(happy=.25, wide=1, curious=0, mouth=.4, wave=0, glow=.95, bob=1.1, spark=0),
    'waiting_approval': dict(happy=.1, wide=1.08, curious=.8, mouth=0, wave=0, glow=.9, bob=.7, spark=.2),
    'executing':        dict(happy=.35, wide=1.05, curious=.2, mouth=0, wave=.4, glow=1.0, bob=1.2, spark=.5),
    'paused':           dict(happy=0, wide=.85, curious=0, mouth=0, wave=0, glow=.35, bob=.3, spark=0),
    'error':            dict(happy=0, wide=1.18, curious=.4, mouth=.15, wave=0, glow=.9, bob=.5, spark=0),
}


class DesktopBot(QWidget):
    """Transparent always-on-top companion: follows the cursor, reacts to hover/click, drag, right-click menu."""
    open_requested, exit_requested, hidden = Signal(), Signal(), Signal()
    W, H = 210, 250
    GOLD, SEAM = (214, 160, 70, 235), (88, 94, 116, 150)

    def __init__(self, settings=None):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.bot_scale = 1.0
        try:
            self.bot_scale = max(0.65, min(2.2, float(settings.value('mechanical_bot_scale', 1.0)))) if settings is not None else 1.0
        except (TypeError, ValueError):
            self.bot_scale = 1.0
        self.setFixedSize(int(self.W * self.bot_scale), int(self.H * self.bot_scale))
        self.settings, self.t = settings, 0.0
        self._last = self._last_move = time.monotonic()
        self._p = dict(STATES['idle'])
        self._look_x = self._look_y = self._tilt = self._float_y = self._by = self._bv = self._ant = 0.0
        self._lid, self._blink, self._next_blink, self._wander, self._wander_next = 1.0, None, self._last + 2, (0.0, 0.0), 0.0
        self._animate = settings is None or str(settings.value('bot_animate', 'true')).lower() != 'false'
        self.assistant_state = 'idle'
        self._react_until = self._bounce_next = 0.0
        self._hover = self._near = self._menu_open = self._dragging = self._placed = False
        self._press = self._geo = None
        self._last_cur = QPoint(-1, -1)
        self._timer = QTimer(self, interval=33, timeout=self._tick)
        self._click = QTimer(self, singleShot=True, timeout=lambda: self.react(1.5))
        QGuiApplication.instance().screenRemoved.connect(lambda *_: QTimer.singleShot(600, self.ensure_on_screen))

    # -- size controls
    def set_bot_scale(self, scale):
        scale = max(0.65, min(2.2, float(scale)))
        centre = self.frameGeometry().center()
        self.bot_scale = scale
        self.setFixedSize(int(self.W * scale), int(self.H * scale))
        self.move(centre - QPoint(self.width() // 2, self.height() // 2))
        if self.settings is not None:
            self.settings.setValue('mechanical_bot_scale', scale)
        self.update()

    def increase_size(self):
        self.set_bot_scale(self.bot_scale + 0.12)

    def decrease_size(self):
        self.set_bot_scale(self.bot_scale - 0.12)

    def event(self, e):
        try:
            if e.type() == QEvent.Type.NativeGesture and e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                self.set_bot_scale(self.bot_scale * (1.0 + float(e.value())))
                e.accept()
                return True
        except (AttributeError, TypeError, ValueError):
            pass
        return super().event(e)

    # -- public API
    def set_assistant_state(self, s):
        self.assistant_state = s if s in ('listening', 'thinking', 'speaking', 'waiting_approval', 'executing', 'paused', 'error') else 'idle'

    def react(self, d=1.5):
        self._react_until = time.monotonic() + d
        self._bounce_next = time.monotonic() + .45
        self._bv = -200.0

    def set_animation_enabled(self, on):
        self._animate = bool(on)
        if self.settings is not None:
            self.settings.setValue('bot_animate', 'true' if on else 'false')
        if on and self.isVisible():
            self._last = time.monotonic()
            self._timer.start()
        elif not on:
            self._timer.stop()
            self._p = dict(STATES['idle'])
            self._look_x = self._look_y = self._tilt = self._by = self._bv = self._float_y = 0.0
            self._hover = self._near = False
            self.update()

    def show_bot(self):
        if not self._placed:
            self._placed = True
            g = QGuiApplication.primaryScreen().availableGeometry()
            self.move(g.right() - self.W - 20, g.bottom() - self.H - 30)
            try:
                x, y = map(int, str(self.settings.value('bot_pos', '')).split(','))
                if self._on_screen(QPoint(x, y)):
                    self.move(x, y)
            except (ValueError, AttributeError):
                pass
        self.show()
        self.ensure_on_screen()

    # -- position / monitors
    def _area(self, pt):
        s = QGuiApplication.screenAt(pt)
        self._geo = s.availableGeometry() if s else (self._geo or QGuiApplication.primaryScreen().availableGeometry())
        return self._geo

    def _clamp_pos(self, pos, ref):
        g = self._area(ref)
        return QPoint(cl(pos.x(), g.left() - 14, g.right() - self.W + 15), cl(pos.y(), g.top() - 14, g.bottom() - self.H + 15))

    def _on_screen(self, pos):
        c = pos + QPoint(self.W // 2, self.H // 2)
        return any(s.availableGeometry().contains(c) for s in QGuiApplication.screens())

    def ensure_on_screen(self):
        if not self._on_screen(self.pos()):
            c = self.pos() + QPoint(self.W // 2, self.H // 2)
            self._geo = None
            self._area(c)
            self.move(self._clamp_pos(self.pos(), c))

    def _save(self):
        if self.settings is not None:
            self.settings.setValue('bot_pos', f'{self.x()},{self.y()}')

    # -- events
    def showEvent(self, e):
        if self._animate:
            self._last = time.monotonic()
            self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def closeEvent(self, e):
        self._save()
        self._timer.stop()
        e.accept()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            g = e.globalPosition().toPoint()
            self._press = (g, g - self.pos())
            self._dragging = False

    def mouseMoveEvent(self, e):
        if self._press and e.buttons() & Qt.LeftButton:
            g = e.globalPosition().toPoint()
            if (g - self._press[0]).manhattanLength() >= 6:
                self._dragging = True
                self._click.stop()
            if self._dragging:
                self.move(self._clamp_pos(g - self._press[1], g))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press:
            if self._dragging:
                self._save()
            else:
                self._click.start(QApplication.doubleClickInterval())
            self._press, self._dragging = None, False

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._click.stop()
            self.react(1.8)
            self.open_requested.emit()

    def contextMenuEvent(self, e):
        self._show_menu(e.globalPos())

    def _show_menu(self, pos):
        m = QMenu(self)
        m.setStyleSheet(MENU_CSS)
        size_menu = m.addMenu('Bot Size')
        size_menu.addAction('Increase Size (+)', self.increase_size)
        size_menu.addAction('Decrease Size (-)', self.decrease_size)
        size_menu.addSeparator()
        size_menu.addAction('Reset Size', lambda: self.set_bot_scale(1.0))
        m.addSeparator()
        for n in ('Open Assistant', 'Toggle Animation', 'Hide Bot', None, f'Exit {APP_NAME}'):
            if n is None:
                m.addSeparator()
                continue
            a = m.addAction(n)
            if n == 'Toggle Animation':
                a.setCheckable(True)
                a.setChecked(self._animate)
        self._menu_open = True
        try:
            a = m.exec(pos)
        finally:
            self._menu_open = False
        t = a.text() if a else ''
        if t == 'Open Assistant':
            self.open_requested.emit()
        elif t == 'Toggle Animation':
            self.set_animation_enabled(not self._animate)
        elif t == 'Hide Bot':
            self.hide()
            self.hidden.emit()
        elif t == f'Exit {APP_NAME}':
            self.exit_requested.emit()

    # -- animation
    def _pick_state(self, now):
        if now < self._react_until:
            return 'excited'
        if self._hover:
            return 'interacting'
        if self.assistant_state != 'idle':
            return self.assistant_state
        return 'curious' if self._near else 'idle'

    def _tick(self):
        now = time.monotonic()
        dt = cl(now - self._last, .001, .05)
        self._last = now
        self.t += dt
        t = self.t
        cur = QCursor.pos()
        if cur != self._last_cur:
            self._last_cur, self._last_move = cur, now
        cx, cy = self.x() + self.W / 2, self.y() + self.H / 2
        dx, dy = cur.x() - cx, cur.y() - (cy - 22)
        dist = math.hypot(dx, dy)
        self._near = dist < (340 if self._near else 280)
        hover = not self._menu_open and ((cur.x() - cx) / 72) ** 2 + ((cur.y() - cy + 2) / 102) ** 2 <= 1
        if hover and not self._hover:
            self._bv, self._blink = -110.0, 0.0
        self._hover = hover
        state = self._pick_state(now)
        for k, v in STATES[state].items():
            if k == 'mouth' and state == 'speaking':
                v = .12 + .6 * abs(math.sin(t * 9) * math.sin(t * 2.3 + 1))
            self._p[k] = ease(self._p[k], v, 16 if k == 'mouth' else 9 if k == 'happy' else 6, dt)
        P = self._p
        if state == 'thinking':
            tx, ty = math.cos(t * 2.1) * .8, math.sin(t * 2.1) * .45 - .35
        elif (now - self._last_move < 6 or dist < 280) and 1 < dist < 900:
            m = min(1, dist / 150)
            tx, ty = dx / dist * m, dy / dist * m
        else:
            if now >= self._wander_next:
                self._wander = (random.uniform(-.55, .55), random.uniform(-.3, .3))
                self._wander_next = now + random.uniform(1.8, 4.2)
            tx, ty = self._wander
        self._look_x, self._look_y = ease(self._look_x, tx, 9, dt), ease(self._look_y, ty, 9, dt)
        self._tilt = ease(self._tilt, self._look_x * 7 + P['curious'] * 5 * (1 if self._look_x >= 0 else -1) + math.sin(t * 1.1) * 1.2 * P['bob'], 6, dt)
        if self._blink is None and now >= self._next_blink:
            self._blink = 0.0
            self._next_blink = now + (.28 if random.random() < .15 else random.uniform(2.4, 5.5))
        if self._blink is not None:
            self._blink += dt / .17
            if self._blink >= 1:
                self._blink, self._lid = None, 1.0
            else:
                self._lid = 1 - math.sin(math.pi * self._blink)
        if state == 'excited' and now >= self._bounce_next:
            self._bv, self._bounce_next = -150.0, now + .45
        self._bv += (-70 * self._by - 6.5 * self._bv) * dt
        self._by += self._bv * dt
        self._float_y = -4 + math.sin(t * 1.7) * 4 * P['bob']
        self.update()

    # -- drawing: chibi mech (gold metal, ceramic white, black glass, glowing ring eyes)
    def _arm(self, p, side, ang, fy):
        p.save()
        p.translate(side * 31, 44 + fy)
        p.rotate(ang)
        st(p, pearl(-6.5, 0, 6.5, 0), self.SEAM, .8)
        p.drawRoundedRect(QRectF(-6.5, 14, 13, 16), 5, 5)
        st(p, gold(-6.5, 0, 6.5, 0))
        p.drawRoundedRect(QRectF(-6.5, 24.5, 13, 3.5), 1.5, 1.5)
        st(p, pearl(-7, 0, 7, 0), self.SEAM, .8)
        p.drawRoundedRect(QRectF(-7, -2, 14, 17), 6, 6)
        st(p, onyx(0, 15, 7), (190, 140, 60, 220), 1.1)
        p.drawEllipse(QPointF(0, 15), 6.2, 6.2)
        hl(p, -1.5, 12.5, 3.2, 2, 200)
        st(p, onyx(0, 36, 11), (90, 96, 116, 160), .9)
        p.drawEllipse(QPointF(0, 36), 8.2, 9.2)
        for fx in (-4.6, 0, 4.6):
            p.drawEllipse(QPointF(fx, 44 - abs(fx) * .35), 3, 3.8)
        p.drawEllipse(QPointF(-side * 7.5, 33), 3, 4.5)
        hl(p, -2, 31, 4, 2.6, 150)
        st(p, onyx(0, 0, 9), (190, 140, 60, 230), 1.3)
        p.drawEllipse(QPointF(0, 0), 7.8, 7.8)
        hl(p, -2.2, -2.6, 4, 2.4, 210)
        p.restore()

    def _pod(self, p, sx):
        x, y = sx * 56, -20
        rx = 11 * cl(1 - sx * self._look_x * .25, .55, 1.3)
        glow(p, x, y, 26, 1, (16, 20, 40), 120)
        st(p, gold(x - rx, 0, x + rx, 0), (96, 62, 22, 230), 1.1)
        p.drawEllipse(QPointF(x, y), rx, 17)
        st(p, lgrad(0, y - 13, 0, y + 13, (0, (255, 255, 255)), (1, (196, 202, 218))), (150, 110, 50, 200), .8)
        p.drawEllipse(QPointF(x, y), rx * .76, 13.8)
        st(p, rgrad(x, y, rx, (0, (30, 60, 130)), (.6, (6, 12, 36)), (1, (2, 4, 14))), (20, 24, 40, 220), 1)
        p.drawEllipse(QPointF(x, y), rx * .58, 10.8)
        hl(p, x - rx * .22, y - 4, 5, 3.4, 210, -20, (190, 220, 255))
        st(p, (70, 140, 255, 190))
        p.drawEllipse(QPointF(x + rx * .15, y + 5), 2.2, 1.6)
        st(p, None, (255, 244, 200, 190), 1.3)
        p.drawArc(QRectF(x - rx + 1.5, y - 15.5, rx * 2 - 3, 31), 105 * 16, 70 * 16)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.bot_scale != 1.0:
            p.scale(self.bot_scale, self.bot_scale)
        p.translate(self.W / 2, self.H / 2)
        P, t, G, SEAM = self._p, self.t, self.GOLD, self.SEAM
        fy = self._float_y + self._by
        glow(p, 0, 106, 34 * cl(1 + fy / 90, .55, 1.2), .2, (0, 10, 30), 80)
        glow(p, 0, 92 + fy, 30, .3, (60, 150, 255), 130 * P['glow'] * (.85 + .15 * math.sin(t * 11)))
        for sx in (-1, 1):
            x = sx * 13
            st(p, pearl(x - 8, 0, x + 8, 0), SEAM, .8)
            p.drawRoundedRect(QRectF(x - 8, 70 + fy, 16, 17), 7, 7)
            st(p, gold(x - 8, 0, x + 8, 0))
            p.drawRoundedRect(QRectF(x - 8, 76 + fy, 16, 3), 1.2, 1.2)
            st(p, onyx(x, 87 + fy, 10), (190, 140, 60, 210), 1.1)
            p.drawEllipse(QPointF(x, 87 + fy), 9.5, 5)
            hl(p, x - 2.5, 85.5 + fy, 4, 1.8, 170)
        body = QPainterPath()
        body.addRoundedRect(QRectF(-28, 30 + fy, 56, 50), 22, 22)
        st(p, pearl(-28, 0, 28, 0), SEAM, 1.1)
        p.drawPath(body)
        p.save()
        p.setClipPath(body)
        st(p, lgrad(0, 30 + fy, 0, 80 + fy, (0, (0, 0, 0, 0)), (1, (60, 74, 110, 120))))
        p.drawRect(QRectF(-30, 28 + fy, 60, 54))
        for sx in (-1, 1):
            st(p, onyx(sx * 27, 50 + fy, 14))
            p.drawEllipse(QPointF(sx * 27, 50 + fy), 7.5, 13)
        st(p, onyx(0, 80 + fy, 22))
        p.drawEllipse(QPointF(0, 80 + fy), 20, 7)
        for sx in (-1, 1):
            ln = QPainterPath(QPointF(sx * 18, 38 + fy))
            ln.quadTo(QPointF(sx * 12, 58 + fy), QPointF(sx * 4, 63 + fy))
            st(p, None, (70, 50, 20, 130), 2)
            p.drawPath(ln)
            st(p, None, (232, 184, 96, 220), 1.1)
            p.drawPath(ln)
        hl(p, -12, 40 + fy, 12, 5, 110)
        p.restore()
        st(p, None, (200, 150, 64, 150), 1.2)
        p.drawPath(body)
        gy = 55 + fy
        glow(p, 0, gy, 24, 1, (50, 150, 255), 150 * P['glow'] * (.8 + .2 * math.sin(t * 3)))
        st(p, onyx(0, gy, 11), (214, 160, 70, 235), 1.7)
        p.drawEllipse(QPointF(0, gy), 10, 10)
        st(p, rgrad(0, gy + 1, 8, (0, (30, 110, 235)), (.6, (50, 160, 255)), (1, (190, 235, 255))), (170, 225, 255, 200), 1)
        p.drawEllipse(QPointF(0, gy), 6.5, 6.5)
        hl(p, -2, gy - 3, 3.6, 2.4, 230)
        sway, lift = math.sin(t * 1.7 + 1) * 2.5, P['spark'] * (70 + 15 * math.sin(t * 13 + 1))
        self._arm(p, -1, 18 + sway + lift, fy)
        self._arm(p, 1, -18 - sway - P['wave'] * (80 + 20 * math.sin(t * 13)), fy)
        glow(p, 0, 36 + fy, 32, .35, (14, 18, 34), 130)
        st(p, onyx(0, 33 + fy, 12), (60, 64, 80, 160), 1)
        p.drawEllipse(QPointF(0, 32 + fy), 11, 5)
        p.save()
        p.translate(self._look_x * 2, fy + math.sin(t * 1.7 - .7) * 1.6 * P['bob'] + self._look_y * 1.5)
        p.translate(0, 32)
        p.rotate(self._tilt)
        p.translate(0, -32)
        shell = QPainterPath()
        shell.addEllipse(QPointF(0, -24), 57, 55)
        st(p, rgrad(0, -24, 66, (0, (255, 255, 255)), (.38, (246, 248, 252)), (.72, (214, 220, 232)), (1, (150, 160, 184)), fx=-20, fy=-48), (112, 120, 146, 170), 1.2)
        p.drawPath(shell)
        p.save()
        p.setClipPath(shell)
        st(p, lgrad(-30, -60, 40, 30, (0, (0, 0, 0, 0)), (1, (60, 70, 100, 150))))
        p.drawRect(QRectF(-60, -82, 120, 112))
        st(p, rgrad(34, 6, 40, (0, (150, 200, 255, 95)), (1, (150, 200, 255, 0))))
        p.drawEllipse(QPointF(34, 6), 40, 40)
        st(p, rgrad(-34, 8, 30, (0, (255, 205, 130, 70)), (1, (255, 205, 130, 0))))
        p.drawEllipse(QPointF(-34, 8), 30, 30)
        for a0, sp_ in ((100, 80), (285, 70)):
            st(p, None, SEAM, 1.2)
            p.drawArc(QRectF(-53, -71, 106, 98), a0 * 16, sp_ * 16)
            st(p, None, (222, 172, 84, 170), .8)
            p.drawArc(QRectF(-52, -70, 104, 96), a0 * 16, sp_ * 16)
        cap = QPainterPath()
        cap.addEllipse(QPointF(8, -86), 36, 20)
        st(p, lgrad(0, -106, 0, -66, (0, (70, 74, 92)), (.5, (20, 22, 30)), (1, (6, 7, 12))), G, 1.2)
        p.drawPath(cap)
        hl(p, 2, -72, 18, 5, 150)
        sw = QPainterPath(QPointF(16, -70))
        sw.quadTo(QPointF(28, -73), QPointF(29, -62))
        st(p, None, (232, 184, 96, 220), 1.4)
        p.drawPath(sw)
        hl(p, -28, -58, 22, 12, 190, -28)
        st(p, (255, 255, 255, 235))
        p.save()
        p.translate(-31, -62)
        p.rotate(-30)
        p.drawEllipse(QPointF(0, 0), 6, 2.6)
        p.restore()
        p.restore()
        for sx in (-1, 1):
            self._pod(p, sx)
        glow(p, 0, -22, 56, 1, (8, 12, 28), 150)
        st(p, None, (214, 160, 70, 190), 1.3)
        p.drawEllipse(QPointF(0, -22), 45.5, 41.5)
        visor = QPainterPath()
        visor.addEllipse(QPointF(0, -22), 42, 38)
        st(p, rgrad(0, -22, 46, (0, (26, 32, 52)), (.5, (7, 10, 22)), (1, (1, 2, 6)), fx=-10, fy=-40), (30, 36, 52), 1.4)
        p.drawPath(visor)
        p.save()
        p.setClipPath(visor)
        p.translate(self._look_x * 6, self._look_y * 4.5)
        self._face(p)
        p.restore()
        p.save()
        p.setClipPath(visor)
        hl(p, 18, -46, 24, 14, 115, 20, (220, 228, 240))
        hl(p, -22, -42, 16, 22, 60, 0, (255, 255, 255))
        hl(p, 0, 10, 30, 8, 55, 0, (60, 120, 220))
        st(p, lgrad(-40, -58, 20, -12, (0, (255, 255, 255, 38)), (1, (255, 255, 255, 0))))
        p.drawEllipse(QPointF(-6, -48), 38, 12)
        st(p, None, (150, 190, 255, 120), 1.6)
        p.drawArc(QRectF(-41, -60, 82, 76), 200 * 16, 80 * 16)
        p.restore()
        p.restore()
        if P['spark'] > .02:
            for i, (x, y) in enumerate(((-86, -62), (86, -54), (-72, -96))):
                tw = .5 + .5 * math.sin(t * 6 + i * 2)
                r = 5 + 3 * tw
                y += fy * .6
                star = QPainterPath(QPointF(x, y - r))
                for k in range(1, 8):
                    a = -math.pi / 2 + k * math.pi / 4
                    q = r if k % 2 == 0 else r * .28
                    star.lineTo(QPointF(x + q * math.cos(a), y + q * math.sin(a)))
                star.closeSubpath()
                st(p, (255, 226, 150, int(235 * P['spark'] * (.4 + .6 * tw))))
                p.drawPath(star)

    def _face(self, p):
        P = self._p
        hp, cu, lx, ly = P['happy'], P['curious'], self._look_x, self._look_y
        for sx, sc in ((-1, 1 - .08 * cu), (1, 1 + .12 * cu)):
            x, y = sx * 22 + lx * 2.5, -20 + ly * 2
            R = 14.5 * sc
            ry = max(1.5, R * P['wide'] * self._lid * (1 - .4 * hp))
            glow(p, x, y, 34, 1, (40, 120, 255), 170 * P['glow'])
            p.save()
            p.setOpacity(cl(1 - hp * 1.15, 0, 1))
            st(p, None, (40, 110, 255, 80), 9)
            p.drawEllipse(QPointF(x, y), R, ry)
            st(p, rgrad(x, y, R, (0, (10, 36, 112)), (.55, (14, 58, 170)), (.85, (30, 100, 230)), (1, (70, 160, 255))), (70, 150, 255, 190), 5)
            p.drawEllipse(QPointF(x, y), R, ry)
            st(p, None, (238, 249, 255), 2.2)
            p.drawEllipse(QPointF(x, y), R, ry)
            if ry > 7:
                ix, iy = x + lx * 3.2, y + ly * 2.6
                st(p, rgrad(ix, iy, R * .6, (0, (3, 12, 56)), (1, (12, 50, 160))))
                p.drawEllipse(QPointF(ix, iy), R * .56, min(ry * .56, R * .56))
                st(p, (215, 238, 255, 230))
                p.drawEllipse(QPointF(ix - R * .25, iy - ry * .3), 2.8, 2.8)
                st(p, (150, 200, 255, 150))
                p.drawEllipse(QPointF(ix + R * .22, iy + ry * .28), 1.6, 1.6)
            p.setOpacity(cl(hp * 1.2, 0, 1))
            arc = QRectF(x - 9, y - 5, 18, 14)
            st(p, None, (80, 160, 255, 120), 7.5)
            p.drawArc(arc, 25 * 16, 130 * 16)
            st(p, None, (225, 245, 255), 3.4)
            p.drawArc(arc, 25 * 16, 130 * 16)
            p.restore()
        mo = P['mouth']
        if mo > .06:
            st(p, (70, 160, 255, 230), (205, 235, 255), 1.2)
            p.drawEllipse(QPointF(0, 8), 5 + 3 * mo, 1.5 + 7 * mo)
        elif hp > .25:
            sm = QPainterPath(QPointF(-6, 6))
            sm.quadTo(QPointF(0, 6 + 7 * hp), QPointF(6, 6))
            st(p, None, (150, 205, 255, int(220 * hp)), 2.4)
            p.drawPath(sm)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    bot = DesktopBot(QSettings('Nova', 'Nova'))
    bot.exit_requested.connect(app.quit)
    bot.show_bot()
    sys.exit(app.exec())
