"""Nova - single-file glass UI for the PC voice assistant. Run: python ui.py"""
import sys, math, random, time, os, html, json, winreg
from pathlib import Path
from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer, QSettings, QPointF, QRectF, QPoint, QEvent, Signal, QObject, QThread, QPropertyAnimation, QVariantAnimation, QEasingCurve, QAbstractAnimation
from PySide6.QtGui import QColor as C, QPainter, QPen, QBrush, QRadialGradient, QLinearGradient, QPainterPath, QCursor, QGuiApplication, QIcon, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
                               QGridLayout, QStackedWidget, QTextEdit, QLineEdit, QMessageBox, QInputDialog, QSlider, QSizeGrip, QMenu, QSystemTrayIcon, QDialog, QListWidget, QListWidgetItem, QScrollArea)
from permissions import CATEGORIES, PermissionManager
from wake_word import WakeWordListener
from nova_companion_bridge import CompanionBridge
from nova_screen_pointing import RegionSelector, ScreenPointingService
from nova_walkthrough import Walkthrough, WalkthroughStep, walkthrough_from_plan
from nova_workflows import Workflow, WorkflowStep, WorkflowRecorder, WorkflowStore, WorkflowPlayer

ROOT = Path(__file__).resolve().parent
APP_NAME = 'Nova'   # change the app name here
BOT_NAMES = {'mechanical': 'Original NOVA Robot', 'ghost': 'Ghost', 'mummy': 'Mummy', 'pet': 'NOVA Painter Pet', 'cloudy': 'Cloudy'}   # companion choices
STYLE = """
QWidget{color:#f1fbff;font-family:'Segoe UI';font-size:10pt;background:transparent}
QFrame#panel{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(16,44,74,220),stop:.5 rgba(10,30,54,228),stop:1 rgba(6,19,37,236));border:1px solid rgba(93,210,255,155);border-radius:22px}
QFrame#sidebar{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(14,48,80,224),stop:.5 rgba(9,32,58,232),stop:1 rgba(5,20,38,240));border:1px solid rgba(93,210,255,155);border-radius:22px}
QScrollArea{background:transparent;border:none}
QScrollBar:vertical{background:rgba(6,22,40,120);width:9px;margin:4px 2px 4px 2px;border-radius:4px}
QScrollBar::handle:vertical{background:rgba(70,195,245,165);min-height:26px;border-radius:4px}
QScrollBar::handle:vertical:hover{background:#5efaff}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px}
QLabel#muted{color:#c8e6f5} QLabel#heading{font-size:21pt;font-weight:650} QLabel#brand{font-size:17pt;font-weight:700;color:#a5f1ff}
QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(42,108,152,190),stop:1 rgba(14,48,80,210));border:1px solid rgba(110,210,248,145);border-radius:12px;padding:8px 14px;text-align:left}
QPushButton:hover{background:rgba(32,135,180,215);border-color:#39c6ff} QPushButton:disabled{color:rgba(241,251,255,120)}
QPushButton#primary{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(32,169,232,235),stop:1 rgba(23,85,165,240));border:1px solid #a0efff;font-weight:700}
QPushButton#nav{background:transparent;border:1px solid transparent;padding:12px} QPushButton#nav:hover{background:rgba(34,137,192,45)}
QPushButton#nav:checked{background:transparent;border:1px solid transparent;color:#ffffff}
QPushButton#ctl{background:rgba(35,115,160,140);border:1px solid rgba(100,220,255,120);border-radius:8px;padding:0;text-align:center;font-size:12pt}
QPushButton#ctl:hover{background:rgba(40,165,220,200)}
QTextEdit,QLineEdit{background:rgba(4,18,34,210);border:1px solid rgba(81,190,238,150);border-radius:14px;padding:8px 10px;selection-background-color:#176fa4}
QSlider::groove:horizontal{height:6px;background:rgba(255,255,255,55);border-radius:3px} QSlider::sub-page:horizontal{background:#39c6ff;border-radius:3px}
QSlider::handle:horizontal{background:#a5f1ff;width:16px;margin:-6px 0;border-radius:8px;border:1px solid white}"""
MENU_CSS = ("QMenu{background:#0c2038;color:#eaf8ff;border:1px solid #3fb8f0;padding:6px;font-family:'Segoe UI';font-size:10pt}"
            "QMenu::item{padding:7px 22px;border-radius:6px} QMenu::item:selected{background:#1f6f9f}QMenu::separator{height:1px;background:#2c5878;margin:5px 8px}")

# ---- painting helpers ---------------------------------------------------------------------
cl = lambda v, lo, hi: max(lo, min(hi, v))
ease = lambda a, b, k, dt: a + (b - a) * (1 - math.exp(-k * dt))      # frame-rate independent smoothing

def _g(g, stops):
    for pos, col in stops: g.setColorAt(pos, C(*col))
    return g
def rgrad(x, y, r, *stops, fx=None, fy=None): return _g(QRadialGradient(QPointF(x, y), r, QPointF(x if fx is None else fx, y if fy is None else fy)), stops)
def lgrad(x1, y1, x2, y2, *stops): return _g(QLinearGradient(x1, y1, x2, y2), stops)
def st(p, brush=None, pen=None, w=1.0):      # brush: rgba tuple | gradient | None ; pen: rgba tuple | None
    p.setBrush(C(*brush) if isinstance(brush, tuple) else (brush if brush is not None else Qt.NoBrush))
    p.setPen(QPen(C(*pen), w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin) if pen else Qt.NoPen)
def glow(p, x, y, r, squash, col, a):
    p.save(); p.translate(x, y); p.scale(1, squash)
    st(p, rgrad(0, 0, r, (0, (*col, int(cl(a, 0, 255)))), (1, (*col, 0)))); p.drawEllipse(QPointF(0, 0), r, r); p.restore()
def gold(x1, y1, x2, y2): return lgrad(x1, y1, x2, y2, (0, (112, 72, 26)), (.22, (236, 188, 100)), (.42, (255, 238, 176)), (.68, (198, 142, 54)), (1, (104, 66, 22)))
def pearl(x1, y1, x2, y2): return lgrad(x1, y1, x2, y2, (0, (150, 158, 180)), (.2, (226, 231, 240)), (.42, (255, 255, 255)), (.66, (232, 236, 244)), (1, (132, 140, 164)))
def onyx(x, y, r): return rgrad(x, y, r, (0, (84, 88, 104)), (.4, (26, 28, 38)), (1, (4, 5, 9)), fx=x - .3 * r, fy=y - .35 * r)
def hl(p, x, y, rx, ry, a=170, ang=0, col=(255, 255, 255)):
    p.save(); p.translate(x, y); p.rotate(ang); p.scale(1, ry / rx)
    st(p, rgrad(0, 0, rx, (0, (*col, a)), (.55, (*col, a // 3)), (1, (*col, 0)))); p.drawEllipse(QPointF(0, 0), rx, rx); p.restore()


class GlassRoot(QWidget):
    """Controlled dark-glass root backdrop with proper surface opacity isolation so desktop windows never bleed into UI text."""
    inset, radius, tint = 14, 26, 224
    def set_tint(self, v): self.tint = cl(int(v), 145, 252); self.update()
    def set_flat(self, flat, radius=0): self.inset, self.radius = (0, radius) if flat else (14, 26); self.update()
    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); i, r, t = self.inset, self.radius, self.tint
        b = QRectF(self.rect()).adjusted(i, i, -i, -i)
        for k in range(i, 0, -1):
            st(p, None, (57, 198, 255, int(38 * (1 - k / (i + 1)) ** 2)), 1.6); p.drawRoundedRect(b.adjusted(-k, -k, k, k), r + k, r + k)
        # Controlled deep navy-cyan glass fill that isolates UI panels from desktop clutter
        top_a = cl(int(t * 0.94), 135, 248)
        mid_a = cl(int(t * 0.98), 150, 252)
        bot_a = cl(int(t * 1.03), 165, 255)
        st(p, lgrad(b.left(), b.top(), b.right(), b.bottom(),
                    (0, (14, 42, 74, top_a)),
                    (.5, (8, 26, 50, mid_a)),
                    (1, (4, 15, 32, bot_a))))
        p.drawRoundedRect(b, r, r)
        st(p, None, (140, 230, 255, 185), 1.3); p.drawRoundedRect(b.adjusted(.6, .6, -.6, -.6), r, r)


class NavPill(QWidget):
    """Liquid-glass highlight that glides behind the active sidebar button."""
    boost = 0.0
    def __init__(self, parent):
        super().__init__(parent); self.setAttribute(Qt.WA_TransparentForMouseEvents); self.setAttribute(Qt.WA_TranslucentBackground)
    def set_boost(self, v): self.boost = float(v); self.update()
    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); b = QRectF(self.rect()).adjusted(1, 1, -1, -1); k = self.boost; w = b.width()
        body = QPainterPath(); body.addRoundedRect(b, 13, 13); p.setClipPath(body)
        st(p, lgrad(0, b.top(), 0, b.bottom(), (0, (255, 255, 255, int(64 + 40 * k))), (.55, (140, 215, 255, int(30 + 20 * k))), (1, (255, 255, 255, int(20 + 16 * k))))); p.drawPath(body)
        band = QPainterPath(QPointF(b.left() + w * .50, b.top())); band.lineTo(b.left() + w * .66, b.top()); band.lineTo(b.left() + w * .40, b.bottom()); band.lineTo(b.left() + w * .24, b.bottom()); band.closeSubpath()
        st(p, (255, 255, 255, int(24 + 30 * k))); p.drawPath(band)                                                  # diagonal sheen
        st(p, lgrad(0, b.top(), 0, b.top() + b.height() * .55, (0, (255, 255, 255, int(95 + 50 * k))), (1, (255, 255, 255, 0))))
        p.drawRoundedRect(QRectF(b.left() + 2, b.top() + 1, w - 4, b.height() * .55), 11, 11)                       # top gloss
        p.setClipping(False); p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(lgrad(b.left(), b.top(), b.right(), b.bottom(), (0, (255, 255, 255, 235)), (.45, (160, 225, 255, 90)), (1, (255, 255, 255, 175)))), 1.4)); p.drawRoundedRect(b, 13, 13)   # refracting rim


ORB = {
    'idle': ((35, 150, 255), (150, 235, 255), .8, .025, 1.3),
    'listening': ((40, 215, 255), (170, 245, 255), 1.1, .045, 2.2),
    'thinking': ((95, 125, 255), (175, 190, 255), 1.7, .02, 3.0),
    'speaking': ((60, 235, 230), (190, 255, 245), 1.4, .06, 3.4),
    'waiting_approval': ((245, 175, 45), (255, 225, 130), 1.2, .035, 2.0),
    'executing': ((45, 225, 145), (165, 255, 210), 1.6, .04, 2.8),
    'paused': ((110, 140, 175), (180, 205, 230), .4, .01, .8),
    'error': ((235, 65, 75), (255, 155, 165), 1.3, .03, 2.2),
}

class OrbWidget(QWidget):
    """Luminous orb with per-state rings."""
    def __init__(self, parent=None):
        super().__init__(parent); self.setAttribute(Qt.WA_TranslucentBackground); self.state, self.t = 'idle', 0.0
        self._timer = QTimer(self, interval=33, timeout=self._tick)
    def set_state(self, s): self.state = s if s in ORB else self.state; self.update()
    def showEvent(self, e): self._timer.start(); super().showEvent(e)
    def hideEvent(self, e): self._timer.stop(); super().hideEvent(e)
    def _tick(self): self.t += .033 * ORB[self.state][2]; self.update()
    def paintEvent(self, _):
        base, rim, _s, amp, rate = ORB[self.state]; R = min(self.width(), self.height()) / 2
        if R < 10: return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); t = self.t; x, y = self.width() / 2, self.height() / 2
        c = QPointF(x, y); r = R * .5 * (1 + amp * math.sin(t * rate * 2)); mid = tuple((a + b) // 2 for a, b in zip(base, (20, 70, 200)))
        st(p, rgrad(x, y, R, (0, (*base, 120)), (.5, (*base, 80)), (.68, (*base, 34)), (1, (*base, 0)))); p.drawEllipse(c, R, R)
        st(p, rgrad(x, y, r, (0, (225, 255, 255)), (.2, (95, 225, 255)), (.5, mid), (.82, (18, 52, 150)), (1, (8, 24, 88)), fx=x - .32 * r, fy=y - .38 * r)); p.drawEllipse(c, r, r)
        sp = QPainterPath(); sp.addEllipse(c, r, r); p.save(); p.setClipPath(sp)
        for k in range(3):
            d = r * (.42 + .17 * k); st(p, None, (210, 248, 255, 60 + 25 * k), max(1, r * .045))
            p.drawArc(QRectF(x - d, y - d, 2 * d, 2 * d), int((t * (26 + 14 * k) * (-1 if k % 2 else 1) + k * 70) % 360 * 16), (55 + 20 * k) * 16)
        p.restore()
        st(p, rgrad(x, y, r * 1.04, (.84, (*rim, 0)), (.93, (*rim, 140)), (.955, (235, 255, 255, 240)), (.98, (*rim, 90)), (1, (*rim, 0)))); p.drawEllipse(c, r * 1.04, r * 1.04)
        p.save(); p.translate(x - .3 * r, y - .48 * r); p.rotate(-28)
        st(p, lgrad(0, -.2 * r, 0, .2 * r, (0, (255, 255, 255, 215)), (1, (255, 255, 255, 0)))); p.drawEllipse(QPointF(0, 0), .36 * r, .19 * r); p.restore()
        if self.state == 'listening':
            for k in range(3):
                ph = (t * .35 + k / 3) % 1; d = r * (1.06 + .9 * ph); st(p, None, (*rim, int(150 * (1 - ph) ** 1.5)), 1.6); p.drawEllipse(c, d, d)
        elif self.state == 'speaking':
            st(p, None, (*rim, 190), 2)
            p.drawPolyline([QPointF(x + r * (1.22 + .07 * math.sin(5 * a + t * 5) + .04 * math.sin(9 * a - t * 7)) * math.cos(a),
                                    y + r * (1.22 + .07 * math.sin(5 * a + t * 5) + .04 * math.sin(9 * a - t * 7)) * math.sin(a)) for a in (i * math.pi / 45 for i in range(91))])
        elif self.state == 'thinking':
            d = r * 1.2
            for j in range(3): st(p, None, (*rim, 200 - j * 55), 2.4); p.drawArc(QRectF(x - d, y - d, 2 * d, 2 * d), int((-t * 140 + j * 120) % 360 * 16), 50 * 16)


class MicWaveformCanvas(QWidget):
    """Sci-fi HUD oscilloscope box with live scrolling cyan waveform and dashed noise-gate threshold line."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(58)
        self.setMaximumHeight(72)
        self.sensitivity = 68
        self.auto_nearest = False
        self.mic_live = False
        self._phase = 0.0
        self._last_rms = 0.0
        self._last_push_ts = 0.0
        self._samples = [0.08 + 0.03 * math.sin(i * 0.45) for i in range(64)]
        self._timer = QTimer(self, interval=40, timeout=self._tick)

    def showEvent(self, e):
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def set_sensitivity(self, pct: int, auto_nearest: bool = False):
        self.sensitivity = max(0, min(100, int(pct)))
        self.auto_nearest = bool(auto_nearest)
        self.update()

    def push_level(self, rms: float, gate_rms: float = 120.0, active: bool = False):
        self._last_rms = float(rms or 0.0)
        self._last_push_ts = time.monotonic()
        self.mic_live = True
        # Map RMS (0..900+) to normalized waveform height (0.04..0.95)
        norm = min(0.95, max(0.04, (self._last_rms / 680.0) ** 0.72))
        self._samples.append(norm)
        if len(self._samples) > 64:
            del self._samples[:-64]
        self.update()

    def _tick(self):
        self._phase += 0.22
        now = time.monotonic()
        if (now - self._last_push_ts) > 0.14:
            # Gentle low-level ambient floor ripple when idle or between packets
            base = 0.07 + 0.025 * math.sin(self._phase) + 0.018 * math.cos(self._phase * 1.9)
            if self.mic_live and (now - self._last_push_ts) < 0.6:
                prev = self._samples[-1] if self._samples else base
                val = prev * 0.72 + base * 0.28
            else:
                val = base + random.uniform(-0.012, 0.015)
            self._samples.append(cl(val, 0.03, 0.95))
            if len(self._samples) > 64:
                del self._samples[:-64]
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        w, h = r.width(), r.height()
        if w < 20 or h < 16:
            return

        # Dark teal oscilloscope box background + subtle cyan frame
        st(p, lgrad(r.left(), r.top(), r.left(), r.bottom(), (0, (6, 32, 42, 195)), (1, (3, 18, 26, 225))), (55, 225, 245, 135), 1.1)
        p.drawRoundedRect(r, 4, 4)

        clip = QPainterPath()
        clip.addRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        p.save()
        p.setClipPath(clip)

        # Build scrolling waveform path
        n = len(self._samples)
        if n >= 2:
            pts = []
            step_x = w / float(n - 1)
            for i, val in enumerate(self._samples):
                px = r.left() + i * step_x
                # Add subtle high-frequency acoustic texture like the HUD reference
                jitter = 0.018 * math.sin(i * 1.1 + self._phase * 2.0)
                norm_y = cl(val + jitter, 0.03, 0.94)
                py = r.bottom() - norm_y * (h - 6.0) - 2.0
                pts.append(QPointF(px, py))

            fill_path = QPainterPath(QPointF(r.left(), r.bottom()))
            for pt in pts:
                fill_path.lineTo(pt)
            fill_path.lineTo(QPointF(r.right(), r.bottom()))
            fill_path.closeSubpath()

            st(
                p,
                lgrad(
                    0, r.top(), 0, r.bottom(),
                    (0, (64, 248, 255, 165)),
                    (0.65, (28, 195, 225, 105)),
                    (1, (10, 90, 115, 45)),
                ),
                None,
            )
            p.drawPath(fill_path)

            # Bright glowing cyan waveform top stroke
            line_path = QPainterPath(pts[0])
            for pt in pts[1:]:
                line_path.lineTo(pt)
            st(p, None, (64, 248, 255, 90), 3.4)
            p.drawPath(line_path)
            st(p, None, (135, 252, 255, 245), 1.6)
            p.drawPath(line_path)

        # Horizontal dashed noise-gate threshold line (higher sensitivity % -> lower gate threshold line)
        thresh_norm = cl(0.14 + ((100.0 - self.sensitivity) / 100.0) * 0.72, 0.12, 0.88)
        ty = r.bottom() - thresh_norm * (h - 6.0) - 2.0
        dash_pen = QPen(C(165, 250, 255, 215), 1.35, Qt.DashLine)
        dash_pen.setDashPattern([3.5, 3.5])
        p.setPen(dash_pen)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(r.left() + 4, ty), QPointF(r.right() - 4, ty))
        p.restore()


class MicSensitivityWidget(QFrame):
    """Sci-fi HUD 'MIC SENSITIVITY' panel with live waveform, threshold line, percentage slider, and AUTO NEAREST."""
    sensitivityChanged = Signal(int, bool, bool)  # (sensitivity_pct, auto_nearest, calibrate_now)

    def __init__(self, initial_sensitivity: int = 68, initial_auto: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("micHudPanel")
        self._updating = False
        self.sensitivity = max(0, min(100, int(initial_sensitivity)))
        self.auto_nearest = bool(initial_auto)

        self.setStyleSheet("""
            QFrame#micHudPanel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(12, 54, 68, 175),
                    stop:0.5 rgba(7, 34, 48, 195),
                    stop:1 rgba(4, 20, 32, 215));
                border: 1.4px solid rgba(64, 232, 255, 185);
                border-radius: 12px;
            }
            QSlider#micSensSlider::groove:horizontal {
                height: 7px;
                background: rgba(130, 165, 175, 90);
                border-radius: 3px;
            }
            QSlider#micSensSlider::sub-page:horizontal {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #26cbe8, stop:1 #5efaff);
                border-radius: 3px;
            }
            QSlider#micSensSlider::handle:horizontal {
                background: #5efaff;
                border: 2px solid #e0ffff;
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 9px;
            }
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 13)
        lay.setSpacing(7)

        # Header: MIC SENSITIVITY
        hdr = QLabel("MIC SENSITIVITY")
        hdr.setStyleSheet("font-size: 10.5pt; font-weight: 700; letter-spacing: 2px; color: #b8f8ff;")
        lay.addWidget(hdr)

        # Thin cyan horizontal divider line
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet("background: rgba(64, 232, 255, 130); border: none;")
        lay.addWidget(div)

        # Live waveform & dashed threshold visualizer
        self.waveform = MicWaveformCanvas(self)
        self.waveform.set_sensitivity(self.sensitivity, self.auto_nearest)
        lay.addWidget(self.waveform)

        # Row: "Sensitivity" on left, "68%" on right
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        lbl = QLabel("Sensitivity")
        lbl.setStyleSheet("font-size: 10.5pt; font-weight: 700; color: #e8fcff;")
        self.pct_label = QLabel(f"{self.sensitivity}%")
        self.pct_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pct_label.setStyleSheet("font-size: 10.5pt; font-weight: 700; color: #45f3ff;")
        row.addWidget(lbl)
        row.addStretch(1)
        row.addWidget(self.pct_label)
        lay.addLayout(row)

        # Horizontal sensitivity slider
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setObjectName("micSensSlider")
        self.slider.setRange(0, 100)
        self.slider.setValue(self.sensitivity)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.valueChanged.connect(self._on_slider_changed)
        lay.addWidget(self.slider)

        # AUTO NEAREST button
        self.auto_btn = QPushButton("AUTO NEAREST")
        self.auto_btn.setCursor(Qt.PointingHandCursor)
        self.auto_btn.setFixedHeight(32)
        self.auto_btn.clicked.connect(self._on_auto_clicked)
        lay.addWidget(self.auto_btn)
        self._refresh_auto_btn_style()

    def _refresh_auto_btn_style(self):
        if self.auto_nearest:
            self.auto_btn.setText("AUTO NEAREST  ●  ACTIVE")
            self.auto_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(38, 205, 235, 115);
                    border: 1.4px solid #66fbff;
                    border-radius: 5px;
                    padding: 4px 10px;
                    text-align: center;
                    font-size: 9pt;
                    font-weight: 700;
                    letter-spacing: 1.5px;
                    color: #ffffff;
                }
                QPushButton:hover {
                    background: rgba(52, 225, 255, 150);
                }
            """)
        else:
            self.auto_btn.setText("AUTO NEAREST")
            self.auto_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(12, 46, 60, 145);
                    border: 1px solid rgba(72, 228, 250, 155);
                    border-radius: 5px;
                    padding: 4px 10px;
                    text-align: center;
                    font-size: 9pt;
                    font-weight: 700;
                    letter-spacing: 1.5px;
                    color: #b8f8ff;
                }
                QPushButton:hover {
                    background: rgba(28, 125, 155, 155);
                    border-color: #5efaff;
                    color: #ffffff;
                }
            """)

    def _on_slider_changed(self, val: int):
        if self._updating:
            return
        self.sensitivity = int(val)
        self.auto_nearest = False
        self.pct_label.setText(f"{self.sensitivity}%")
        self.waveform.set_sensitivity(self.sensitivity, self.auto_nearest)
        self._refresh_auto_btn_style()
        self.sensitivityChanged.emit(self.sensitivity, False, False)

    def _on_auto_clicked(self):
        self.auto_nearest = not self.auto_nearest
        self.waveform.set_sensitivity(self.sensitivity, self.auto_nearest)
        self._refresh_auto_btn_style()
        self.sensitivityChanged.emit(self.sensitivity, self.auto_nearest, self.auto_nearest)

    def sync_state(self, sensitivity: int, auto_nearest: bool):
        self._updating = True
        try:
            self.sensitivity = max(0, min(100, int(sensitivity)))
            self.auto_nearest = bool(auto_nearest)
            self.slider.setValue(self.sensitivity)
            self.pct_label.setText(f"{self.sensitivity}%")
            self.waveform.set_sensitivity(self.sensitivity, self.auto_nearest)
            self._refresh_auto_btn_style()
        finally:
            self._updating = False

    def push_audio_level(self, rms: float, gate_rms: float = 120.0, active: bool = False):
        self.waveform.push_level(rms, gate_rms, active)


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
        self.setAttribute(Qt.WA_TranslucentBackground); self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True); self.setCursor(Qt.PointingHandCursor)
        self.bot_scale = 1.0
        try: self.bot_scale = max(0.65, min(2.2, float(settings.value('mechanical_bot_scale', 1.0)))) if settings is not None else 1.0
        except (TypeError, ValueError): self.bot_scale = 1.0
        self.setFixedSize(int(self.W * self.bot_scale), int(self.H * self.bot_scale))
        self.settings, self.t = settings, 0.0; self._last = self._last_move = time.monotonic()
        self._p = dict(STATES['idle']); self._look_x = self._look_y = self._tilt = self._float_y = self._by = self._bv = self._ant = 0.0
        self._lid, self._blink, self._next_blink, self._wander, self._wander_next = 1.0, None, self._last + 2, (0.0, 0.0), 0.0
        self._animate = settings is None or str(settings.value('bot_animate', 'true')).lower() != 'false'
        self.assistant_state = 'idle'; self._react_until = self._bounce_next = 0.0
        self._hover = self._near = self._menu_open = self._dragging = self._placed = False
        self._press = self._geo = None; self._last_cur = QPoint(-1, -1)
        self._timer = QTimer(self, interval=33, timeout=self._tick)
        self._click = QTimer(self, singleShot=True, timeout=lambda: self.react(1.5))      # waits to see if a click is a double-click
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

    def increase_size(self): self.set_bot_scale(self.bot_scale + 0.12)
    def decrease_size(self): self.set_bot_scale(self.bot_scale - 0.12)

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
    def set_assistant_state(self, s): self.assistant_state = s if s in ('listening', 'thinking', 'speaking', 'waiting_approval', 'executing', 'paused', 'error') else 'idle'
    def react(self, d=1.5): self._react_until = time.monotonic() + d; self._bounce_next = time.monotonic() + .45; self._bv = -200.0
    def set_animation_enabled(self, on):
        self._animate = bool(on)
        if self.settings is not None: self.settings.setValue('bot_animate', 'true' if on else 'false')
        if on and self.isVisible(): self._last = time.monotonic(); self._timer.start()
        elif not on:                                                                      # freeze in a calm idle pose
            self._timer.stop(); self._p = dict(STATES['idle']); self._look_x = self._look_y = self._tilt = self._by = self._bv = self._float_y = 0.0
            self._hover = self._near = False; self.update()
    def show_bot(self):
        if not self._placed:
            self._placed = True; g = QGuiApplication.primaryScreen().availableGeometry(); self.move(g.right() - self.W - 20, g.bottom() - self.H - 30)
            try:
                x, y = map(int, str(self.settings.value('bot_pos', '')).split(','))
                if self._on_screen(QPoint(x, y)): self.move(x, y)
            except (ValueError, AttributeError): pass
        self.show(); self.ensure_on_screen()

    # -- position / monitors
    def _area(self, pt):
        s = QGuiApplication.screenAt(pt)
        self._geo = s.availableGeometry() if s else (self._geo or QGuiApplication.primaryScreen().availableGeometry())
        return self._geo
    def _clamp_pos(self, pos, ref):
        g = self._area(ref); return QPoint(cl(pos.x(), g.left() - 14, g.right() - self.W + 15), cl(pos.y(), g.top() - 14, g.bottom() - self.H + 15))
    def _on_screen(self, pos):
        c = pos + QPoint(self.W // 2, self.H // 2); return any(s.availableGeometry().contains(c) for s in QGuiApplication.screens())
    def ensure_on_screen(self):                                                           # e.g. after a monitor is unplugged
        if not self._on_screen(self.pos()):
            c = self.pos() + QPoint(self.W // 2, self.H // 2); self._geo = None; self._area(c); self.move(self._clamp_pos(self.pos(), c))
    def _save(self):
        if self.settings is not None: self.settings.setValue('bot_pos', f'{self.x()},{self.y()}')

    # -- events
    def showEvent(self, e):
        if self._animate: self._last = time.monotonic(); self._timer.start()
        super().showEvent(e)
    def hideEvent(self, e): self._timer.stop(); super().hideEvent(e)
    def closeEvent(self, e): self._save(); self._timer.stop(); e.accept()
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton: g = e.globalPosition().toPoint(); self._press = (g, g - self.pos()); self._dragging = False
    def mouseMoveEvent(self, e):
        if self._press and e.buttons() & Qt.LeftButton:
            g = e.globalPosition().toPoint()
            if (g - self._press[0]).manhattanLength() >= 6: self._dragging = True; self._click.stop()
            if self._dragging: self.move(self._clamp_pos(g - self._press[1], g))
    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press:
            if self._dragging: self._save()
            else: self._click.start(QApplication.doubleClickInterval())
            self._press, self._dragging = None, False
    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton: self._click.stop(); self.react(1.8); self.open_requested.emit()
    def contextMenuEvent(self, e): self._show_menu(e.globalPos())
    def _show_menu(self, pos):
        m = QMenu(self); m.setStyleSheet(MENU_CSS)
        size_menu = m.addMenu('Bot Size')
        size_menu.addAction('Increase Size (+)', self.increase_size)
        size_menu.addAction('Decrease Size (-)', self.decrease_size)
        size_menu.addSeparator()
        size_menu.addAction('Reset Size', lambda: self.set_bot_scale(1.0))
        m.addSeparator()
        for n in ('Open Assistant', 'Toggle Animation', 'Hide Bot', None, f'Exit {APP_NAME}'):
            if n is None: m.addSeparator(); continue
            a = m.addAction(n)
            if n == 'Toggle Animation': a.setCheckable(True); a.setChecked(self._animate)
        self._menu_open = True
        try: a = m.exec(pos)
        finally: self._menu_open = False
        t = a.text() if a else ''
        if t == 'Open Assistant': self.open_requested.emit()
        elif t == 'Toggle Animation': self.set_animation_enabled(not self._animate)
        elif t == 'Hide Bot': self.hide(); self.hidden.emit()
        elif t == f'Exit {APP_NAME}': self.exit_requested.emit()

    # -- animation
    def _pick_state(self, now):
        if now < self._react_until: return 'excited'
        if self._hover: return 'interacting'
        if self.assistant_state != 'idle': return self.assistant_state
        return 'curious' if self._near else 'idle'
    def _tick(self):
        now = time.monotonic(); dt = cl(now - self._last, .001, .05); self._last = now; self.t += dt; t = self.t
        cur = QCursor.pos()                                                               # read-only poll, never grabs the mouse
        if cur != self._last_cur: self._last_cur, self._last_move = cur, now
        cx, cy = self.x() + self.W / 2, self.y() + self.H / 2
        dx, dy = cur.x() - cx, cur.y() - (cy - 22); dist = math.hypot(dx, dy)
        self._near = dist < (340 if self._near else 280)
        hover = not self._menu_open and ((cur.x() - cx) / 72) ** 2 + ((cur.y() - cy + 2) / 102) ** 2 <= 1
        if hover and not self._hover: self._bv, self._blink = -110.0, 0.0                 # cursor just landed: bounce + blink
        self._hover = hover; state = self._pick_state(now)
        for k, v in STATES[state].items():
            if k == 'mouth' and state == 'speaking': v = .12 + .6 * abs(math.sin(t * 9) * math.sin(t * 2.3 + 1))
            self._p[k] = ease(self._p[k], v, 16 if k == 'mouth' else 9 if k == 'happy' else 6, dt)
        P = self._p
        if state == 'thinking': tx, ty = math.cos(t * 2.1) * .8, math.sin(t * 2.1) * .45 - .35
        elif (now - self._last_move < 6 or dist < 280) and 1 < dist < 900: m = min(1, dist / 150); tx, ty = dx / dist * m, dy / dist * m
        else:                                                                             # no cursor to follow: glance around
            if now >= self._wander_next: self._wander = (random.uniform(-.55, .55), random.uniform(-.3, .3)); self._wander_next = now + random.uniform(1.8, 4.2)
            tx, ty = self._wander
        self._look_x, self._look_y = ease(self._look_x, tx, 9, dt), ease(self._look_y, ty, 9, dt)
        self._tilt = ease(self._tilt, self._look_x * 7 + P['curious'] * 5 * (1 if self._look_x >= 0 else -1) + math.sin(t * 1.1) * 1.2 * P['bob'], 6, dt)
        if self._blink is None and now >= self._next_blink:
            self._blink = 0.0; self._next_blink = now + (.28 if random.random() < .15 else random.uniform(2.4, 5.5))
        if self._blink is not None:
            self._blink += dt / .17
            if self._blink >= 1: self._blink, self._lid = None, 1.0
            else: self._lid = 1 - math.sin(math.pi * self._blink)
        if state == 'excited' and now >= self._bounce_next: self._bv, self._bounce_next = -150.0, now + .45
        self._bv += (-70 * self._by - 6.5 * self._bv) * dt; self._by += self._bv * dt     # bounce spring
        self._float_y = -4 + math.sin(t * 1.7) * 4 * P['bob']; self.update()

    # -- drawing: chibi mech (gold metal, ceramic white, black glass, glowing ring eyes)
    def _arm(self, p, side, ang, fy):
        p.save(); p.translate(side * 31, 44 + fy); p.rotate(ang)
        st(p, pearl(-6.5, 0, 6.5, 0), self.SEAM, .8); p.drawRoundedRect(QRectF(-6.5, 14, 13, 16), 5, 5)
        st(p, gold(-6.5, 0, 6.5, 0)); p.drawRoundedRect(QRectF(-6.5, 24.5, 13, 3.5), 1.5, 1.5)
        st(p, pearl(-7, 0, 7, 0), self.SEAM, .8); p.drawRoundedRect(QRectF(-7, -2, 14, 17), 6, 6)
        st(p, onyx(0, 15, 7), (190, 140, 60, 220), 1.1); p.drawEllipse(QPointF(0, 15), 6.2, 6.2); hl(p, -1.5, 12.5, 3.2, 2, 200)
        st(p, onyx(0, 36, 11), (90, 96, 116, 160), .9); p.drawEllipse(QPointF(0, 36), 8.2, 9.2)
        for fx in (-4.6, 0, 4.6): p.drawEllipse(QPointF(fx, 44 - abs(fx) * .35), 3, 3.8)
        p.drawEllipse(QPointF(-side * 7.5, 33), 3, 4.5); hl(p, -2, 31, 4, 2.6, 150)
        st(p, onyx(0, 0, 9), (190, 140, 60, 230), 1.3); p.drawEllipse(QPointF(0, 0), 7.8, 7.8); hl(p, -2.2, -2.6, 4, 2.4, 210)
        p.restore()
    def _pod(self, p, sx):                                                                # gold ear pod; narrows on the side that turns away
        x, y = sx * 56, -20; rx = 11 * cl(1 - sx * self._look_x * .25, .55, 1.3)
        glow(p, x, y, 26, 1, (16, 20, 40), 120)
        st(p, gold(x - rx, 0, x + rx, 0), (96, 62, 22, 230), 1.1); p.drawEllipse(QPointF(x, y), rx, 17)
        st(p, lgrad(0, y - 13, 0, y + 13, (0, (255, 255, 255)), (1, (196, 202, 218))), (150, 110, 50, 200), .8); p.drawEllipse(QPointF(x, y), rx * .76, 13.8)
        st(p, rgrad(x, y, rx, (0, (30, 60, 130)), (.6, (6, 12, 36)), (1, (2, 4, 14))), (20, 24, 40, 220), 1); p.drawEllipse(QPointF(x, y), rx * .58, 10.8)
        hl(p, x - rx * .22, y - 4, 5, 3.4, 210, -20, (190, 220, 255)); st(p, (70, 140, 255, 190)); p.drawEllipse(QPointF(x + rx * .15, y + 5), 2.2, 1.6)
        st(p, None, (255, 244, 200, 190), 1.3); p.drawArc(QRectF(x - rx + 1.5, y - 15.5, rx * 2 - 3, 31), 105 * 16, 70 * 16)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.translate(self.W / 2, self.H / 2)
        P, t, G, SEAM = self._p, self.t, self.GOLD, self.SEAM; fy = self._float_y + self._by
        glow(p, 0, 106, 34 * cl(1 + fy / 90, .55, 1.2), .2, (0, 10, 30), 80)                                    # ground shadow
        glow(p, 0, 92 + fy, 30, .3, (60, 150, 255), 130 * P['glow'] * (.85 + .15 * math.sin(t * 11)))           # hover jet
        for sx in (-1, 1):                                                                                      # legs
            x = sx * 13
            st(p, pearl(x - 8, 0, x + 8, 0), SEAM, .8); p.drawRoundedRect(QRectF(x - 8, 70 + fy, 16, 17), 7, 7)
            st(p, gold(x - 8, 0, x + 8, 0)); p.drawRoundedRect(QRectF(x - 8, 76 + fy, 16, 3), 1.2, 1.2)
            st(p, onyx(x, 87 + fy, 10), (190, 140, 60, 210), 1.1); p.drawEllipse(QPointF(x, 87 + fy), 9.5, 5); hl(p, x - 2.5, 85.5 + fy, 4, 1.8, 170)
        body = QPainterPath(); body.addRoundedRect(QRectF(-28, 30 + fy, 56, 50), 22, 22)                        # torso
        st(p, pearl(-28, 0, 28, 0), SEAM, 1.1); p.drawPath(body)
        p.save(); p.setClipPath(body)
        st(p, lgrad(0, 30 + fy, 0, 80 + fy, (0, (0, 0, 0, 0)), (1, (60, 74, 110, 120)))); p.drawRect(QRectF(-30, 28 + fy, 60, 54))
        for sx in (-1, 1): st(p, onyx(sx * 27, 50 + fy, 14)); p.drawEllipse(QPointF(sx * 27, 50 + fy), 7.5, 13)
        st(p, onyx(0, 80 + fy, 22)); p.drawEllipse(QPointF(0, 80 + fy), 20, 7)
        for sx in (-1, 1):
            ln = QPainterPath(QPointF(sx * 18, 38 + fy)); ln.quadTo(QPointF(sx * 12, 58 + fy), QPointF(sx * 4, 63 + fy))
            st(p, None, (70, 50, 20, 130), 2); p.drawPath(ln); st(p, None, (232, 184, 96, 220), 1.1); p.drawPath(ln)
        hl(p, -12, 40 + fy, 12, 5, 110); p.restore()
        st(p, None, (200, 150, 64, 150), 1.2); p.drawPath(body)
        gy = 55 + fy                                                                                            # chest gem
        glow(p, 0, gy, 24, 1, (50, 150, 255), 150 * P['glow'] * (.8 + .2 * math.sin(t * 3)))
        st(p, onyx(0, gy, 11), (214, 160, 70, 235), 1.7); p.drawEllipse(QPointF(0, gy), 10, 10)
        st(p, rgrad(0, gy + 1, 8, (0, (30, 110, 235)), (.6, (50, 160, 255)), (1, (190, 235, 255))), (170, 225, 255, 200), 1); p.drawEllipse(QPointF(0, gy), 6.5, 6.5)
        hl(p, -2, gy - 3, 3.6, 2.4, 230)
        sway, lift = math.sin(t * 1.7 + 1) * 2.5, P['spark'] * (70 + 15 * math.sin(t * 13 + 1))                 # left arm lifts when excited, right waves
        self._arm(p, -1, 18 + sway + lift, fy); self._arm(p, 1, -18 - sway - P['wave'] * (80 + 20 * math.sin(t * 13)), fy)
        glow(p, 0, 36 + fy, 32, .35, (14, 18, 34), 130)
        st(p, onyx(0, 33 + fy, 12), (60, 64, 80, 160), 1); p.drawEllipse(QPointF(0, 32 + fy), 11, 5)            # neck
        p.save(); p.translate(self._look_x * 2, fy + math.sin(t * 1.7 - .7) * 1.6 * P['bob'] + self._look_y * 1.5)   # head
        p.translate(0, 32); p.rotate(self._tilt); p.translate(0, -32)
        shell = QPainterPath(); shell.addEllipse(QPointF(0, -24), 57, 55)
        st(p, rgrad(0, -24, 66, (0, (255, 255, 255)), (.38, (246, 248, 252)), (.72, (214, 220, 232)), (1, (150, 160, 184)), fx=-20, fy=-48), (112, 120, 146, 170), 1.2); p.drawPath(shell)
        p.save(); p.setClipPath(shell)
        st(p, lgrad(-30, -60, 40, 30, (0, (0, 0, 0, 0)), (1, (60, 70, 100, 150)))); p.drawRect(QRectF(-60, -82, 120, 112))
        st(p, rgrad(34, 6, 40, (0, (150, 200, 255, 95)), (1, (150, 200, 255, 0)))); p.drawEllipse(QPointF(34, 6), 40, 40)
        st(p, rgrad(-34, 8, 30, (0, (255, 205, 130, 70)), (1, (255, 205, 130, 0)))); p.drawEllipse(QPointF(-34, 8), 30, 30)
        for a0, sp_ in ((100, 80), (285, 70)):
            st(p, None, SEAM, 1.2); p.drawArc(QRectF(-53, -71, 106, 98), a0 * 16, sp_ * 16); st(p, None, (222, 172, 84, 170), .8); p.drawArc(QRectF(-52, -70, 104, 96), a0 * 16, sp_ * 16)
        cap = QPainterPath(); cap.addEllipse(QPointF(8, -86), 36, 20)                                           # black top plate
        st(p, lgrad(0, -106, 0, -66, (0, (70, 74, 92)), (.5, (20, 22, 30)), (1, (6, 7, 12))), G, 1.2); p.drawPath(cap); hl(p, 2, -72, 18, 5, 150)
        sw = QPainterPath(QPointF(16, -70)); sw.quadTo(QPointF(28, -73), QPointF(29, -62)); st(p, None, (232, 184, 96, 220), 1.4); p.drawPath(sw)
        hl(p, -28, -58, 22, 12, 190, -28); st(p, (255, 255, 255, 235)); p.save(); p.translate(-31, -62); p.rotate(-30); p.drawEllipse(QPointF(0, 0), 6, 2.6); p.restore()
        p.restore()
        for sx in (-1, 1): self._pod(p, sx)
        glow(p, 0, -22, 56, 1, (8, 12, 28), 150)
        st(p, None, (214, 160, 70, 190), 1.3); p.drawEllipse(QPointF(0, -22), 45.5, 41.5)                       # gold bezel
        visor = QPainterPath(); visor.addEllipse(QPointF(0, -22), 42, 38)
        st(p, rgrad(0, -22, 46, (0, (26, 32, 52)), (.5, (7, 10, 22)), (1, (1, 2, 6)), fx=-10, fy=-40), (30, 36, 52), 1.4); p.drawPath(visor)
        p.save(); p.setClipPath(visor); p.translate(self._look_x * 6, self._look_y * 4.5); self._face(p); p.restore()   # face slides inside visor
        p.save(); p.setClipPath(visor)                                                                          # glass reflections + rim
        hl(p, 18, -46, 24, 14, 115, 20, (220, 228, 240)); hl(p, -22, -42, 16, 22, 60, 0, (255, 255, 255)); hl(p, 0, 10, 30, 8, 55, 0, (60, 120, 220))
        st(p, lgrad(-40, -58, 20, -12, (0, (255, 255, 255, 38)), (1, (255, 255, 255, 0)))); p.drawEllipse(QPointF(-6, -48), 38, 12)
        st(p, None, (150, 190, 255, 120), 1.6); p.drawArc(QRectF(-41, -60, 82, 76), 200 * 16, 80 * 16); p.restore(); p.restore()
        if P['spark'] > .02:                                                                                    # sparkles
            for i, (x, y) in enumerate(((-86, -62), (86, -54), (-72, -96))):
                tw = .5 + .5 * math.sin(t * 6 + i * 2); r = 5 + 3 * tw; y += fy * .6; star = QPainterPath(QPointF(x, y - r))
                for k in range(1, 8): a = -math.pi / 2 + k * math.pi / 4; q = r if k % 2 == 0 else r * .28; star.lineTo(QPointF(x + q * math.cos(a), y + q * math.sin(a)))
                star.closeSubpath(); st(p, (255, 226, 150, int(235 * P['spark'] * (.4 + .6 * tw)))); p.drawPath(star)
    def _face(self, p):
        P = self._p; hp, cu, lx, ly = P['happy'], P['curious'], self._look_x, self._look_y
        for sx, sc in ((-1, 1 - .08 * cu), (1, 1 + .12 * cu)):                                                  # ring eyes: round <-> happy arc
            x, y = sx * 22 + lx * 2.5, -20 + ly * 2; R = 14.5 * sc; ry = max(1.5, R * P['wide'] * self._lid * (1 - .4 * hp))
            glow(p, x, y, 34, 1, (40, 120, 255), 170 * P['glow']); p.save(); p.setOpacity(cl(1 - hp * 1.15, 0, 1))
            st(p, None, (40, 110, 255, 80), 9); p.drawEllipse(QPointF(x, y), R, ry)
            st(p, rgrad(x, y, R, (0, (10, 36, 112)), (.55, (14, 58, 170)), (.85, (30, 100, 230)), (1, (70, 160, 255))), (70, 150, 255, 190), 5); p.drawEllipse(QPointF(x, y), R, ry)
            st(p, None, (238, 249, 255), 2.2); p.drawEllipse(QPointF(x, y), R, ry)
            if ry > 7:                                                                                          # iris + glints track the cursor
                ix, iy = x + lx * 3.2, y + ly * 2.6
                st(p, rgrad(ix, iy, R * .6, (0, (3, 12, 56)), (1, (12, 50, 160)))); p.drawEllipse(QPointF(ix, iy), R * .56, min(ry * .56, R * .56))
                st(p, (215, 238, 255, 230)); p.drawEllipse(QPointF(ix - R * .25, iy - ry * .3), 2.8, 2.8); st(p, (150, 200, 255, 150)); p.drawEllipse(QPointF(ix + R * .22, iy + ry * .28), 1.6, 1.6)
            p.setOpacity(cl(hp * 1.2, 0, 1)); arc = QRectF(x - 9, y - 5, 18, 14)
            st(p, None, (80, 160, 255, 120), 7.5); p.drawArc(arc, 25 * 16, 130 * 16); st(p, None, (225, 245, 255), 3.4); p.drawArc(arc, 25 * 16, 130 * 16); p.restore()
        mo = P['mouth']
        if mo > .06: st(p, (70, 160, 255, 230), (205, 235, 255), 1.2); p.drawEllipse(QPointF(0, 8), 5 + 3 * mo, 1.5 + 7 * mo)
        elif hp > .25:
            sm = QPainterPath(QPointF(-6, 6)); sm.quadTo(QPointF(0, 6 + 7 * hp), QPointF(6, 6)); st(p, None, (150, 205, 255, int(220 * hp)), 2.4); p.drawPath(sm)


def apply_backdrop(win, frost):
    """Frosted: strong acrylic blur (Windows 10/11) clipped to the rounded glass body. Clear: no native effect. Returns a message or ''."""
    if sys.platform != 'win32': return 'Frosted blur is only available on Windows; showing clear glass.' if frost else ''
    try:
        import ctypes; from ctypes import wintypes as wt
        class ACCENT(ctypes.Structure): _fields_ = [('state', ctypes.c_int), ('flags', ctypes.c_int), ('color', ctypes.c_uint), ('anim', ctypes.c_int)]
        class WCA(ctypes.Structure): _fields_ = [('attr', ctypes.c_int), ('data', ctypes.c_void_p), ('size', ctypes.c_size_t)]
        u32, gdi = ctypes.WinDLL('user32'), ctypes.WinDLL('gdi32')
        u32.SetWindowCompositionAttribute.argtypes = [wt.HWND, ctypes.POINTER(WCA)]; u32.SetWindowCompositionAttribute.restype = wt.BOOL
        u32.SetWindowRgn.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
        gdi.CreateRoundRectRgn.restype = ctypes.c_void_p; gdi.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        hwnd, rgn = wt.HWND(int(win.winId())), None
        dwm = ctypes.WinDLL('dwmapi'); dwm.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
        for attr, val in ((33, 2 if frost and not win.isMaximized() else 1), (34, 0xFFFFFFFE)):      # Windows 11: round corners, no system border (ignored elsewhere)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ctypes.c_uint(val)), 4)
        if frost:
            d, m = win.devicePixelRatioF(), win.isMaximized(); i = 0 if m else round(win.root.inset * d); rad = 0 if m else round(win.root.radius * 2 * d)
            rgn = gdi.CreateRoundRectRgn(i, i, int(win.width() * d) - i + 1, int(win.height() * d) - i + 1, rad, rad)
        u32.SetWindowRgn(hwnd, rgn, True)                                                  # window owns the region afterwards
        acc = ACCENT(4 if frost else 0, 2 if frost else 0, 0x99502810, 0)                  # 4 = acrylic blur; colour is AABBGGRR (dark blue tint)
        data = WCA(19, ctypes.cast(ctypes.pointer(acc), ctypes.c_void_p), ctypes.sizeof(acc))
        if not u32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)): return 'Frosted blur is not supported by this Windows version; showing clear glass.' if frost else ''
        if frost:
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as k: on = bool(winreg.QueryValueEx(k, 'EnableTransparency')[0])
            except OSError: on = True
            if not on: return 'Windows "Transparency effects" is off (Settings > Personalization > Colors), so the blur will not show. Clear glass still works.'
    except Exception as e: return f'Native backdrop setup failed ({e}); showing clear glass.' if frost else ''
    return ''


def set_run_at_startup(enable: bool) -> bool:
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "GlassNova"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
            if enable:
                exe_path = sys.executable
                script_path = str(ROOT / "ui.py")
                cmd = f'"{exe_path}" "{script_path}"'
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
            else:
                try: winreg.DeleteValue(key, app_name)
                except FileNotFoundError: pass
        return True
    except Exception:
        return False

def is_run_at_startup() -> bool:
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "GlassNova"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, app_name)
            return True
    except Exception:
        return False

def _make_tray_icon():
    pix = QPixmap(32, 32)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(C(32, 169, 232, 220))
    p.setPen(QPen(C(160, 240, 255), 2))
    p.drawEllipse(2, 2, 28, 28)
    p.setBrush(C(255, 255, 255))
    p.drawEllipse(11, 11, 10, 10)
    p.end()
    return QIcon(pix)

class WakeSignal(QObject):
    detected = Signal()
    status = Signal(str)

class PermissionDialog(QDialog):
    def __init__(self, category: str, details: str, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.category = category
        self.details = details
        self.selected_decision = "deny"

        info = CATEGORIES.get(category, {
            "title": category.replace("_", " ").title(),
            "description": "Nova requires permission for this action.",
            "icon": "🔒"
        })

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        panel = QFrame()
        panel.setObjectName("panel")
        panel.setStyleSheet("""
            QFrame#panel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(14, 42, 70, 245),
                    stop:0.5 rgba(8, 28, 52, 250),
                    stop:1 rgba(4, 16, 32, 255));
                border: 1.5px solid rgba(80, 210, 255, 180);
                border-radius: 20px;
            }
        """)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(24, 22, 24, 22)
        pl.setSpacing(12)

        header = QLabel(f"🔒  Security & Permission Request")
        header.setStyleSheet("font-size: 14pt; font-weight: 700; color: #a5f1ff;")
        pl.addWidget(header)

        cat_badge = QLabel(f"<b>Category:</b> {info.get('title')} <span style='color:#a0efff'>[{category}]</span>")
        cat_badge.setStyleSheet("font-size: 10pt; color: #ffffff; padding: 6px 12px; background: rgba(30, 100, 150, 120); border-radius: 8px; border: 1px solid rgba(93, 210, 255, 120);")
        pl.addWidget(cat_badge)

        desc_lbl = QLabel(info.get("description", ""))
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: #b8e2f8; font-size: 10pt;")
        pl.addWidget(desc_lbl)

        if details:
            details_lbl = QLabel(f"<b>Target / Action:</b> {details}")
            details_lbl.setWordWrap(True)
            details_lbl.setStyleSheet("color: #e6f7ff; font-size: 10pt; background: rgba(6, 20, 35, 160); padding: 8px; border-radius: 6px; border: 1px dashed rgba(80, 200, 255, 100);")
            pl.addWidget(details_lbl)

        warn_lbl = QLabel("🎙 Voice Active: Say \"Allow\", \"Yes\", \"Allow Once\", or \"Deny\" — or click a button below.")
        warn_lbl.setWordWrap(True)
        warn_lbl.setStyleSheet("color: #a5f1ff; font-size: 9.5pt; font-weight: 600; background: rgba(20, 90, 140, 110); padding: 6px 10px; border-radius: 6px;")
        pl.addWidget(warn_lbl)

        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)

        allow_session_btn = QPushButton("Allow for Session")
        allow_session_btn.setCursor(Qt.PointingHandCursor)
        allow_session_btn.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(32,169,232,225), stop:1 rgba(23,85,165,232)); border: 1px solid #a0efff; font-weight: 700; border-radius: 12px; padding: 10px 14px;")
        allow_session_btn.clicked.connect(lambda: self._choose("allow_session"))
        btn_box.addWidget(allow_session_btn)

        allow_once_btn = QPushButton("Allow Once")
        allow_once_btn.setCursor(Qt.PointingHandCursor)
        allow_once_btn.setStyleSheet("background: rgba(40,110,160,140); border: 1px solid rgba(110,210,248,150); border-radius: 12px; padding: 10px 14px;")
        allow_once_btn.clicked.connect(lambda: self._choose("allow_once"))
        btn_box.addWidget(allow_once_btn)

        deny_btn = QPushButton("Deny")
        deny_btn.setCursor(Qt.PointingHandCursor)
        deny_btn.setStyleSheet("background: rgba(160, 40, 40, 160); border: 1px solid rgba(255, 100, 100, 160); border-radius: 12px; padding: 10px 14px;")
        deny_btn.clicked.connect(lambda: self._choose("deny"))
        btn_box.addWidget(deny_btn)

        pl.addLayout(btn_box)
        layout.addWidget(panel)
        self.resize(520, 320)

    def _choose(self, decision: str):
        self.selected_decision = decision
        self.accept()

    def reject(self):
        self.selected_decision = "deny"
        super().reject()


class WalkthroughHighlightOverlay(QWidget):
    """Non-interactive translucent screen overlay that highlights a UIAutomation target rectangle during a walkthrough.
    Never intercepts clicks or keyboard input (WA_TransparentForMouseEvents)."""
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.target_rect = None
        self.target_label = ""
        self._phase = 0.0
        self._timer = QTimer(self, interval=40, timeout=self._tick)

    def show_target(self, rect_tuple, label: str = ""):
        if not rect_tuple or len(rect_tuple) < 4:
            self.hide_target()
            return
        x, y, w, h = [int(v) for v in rect_tuple[:4]]
        if w <= 4 or h <= 4:
            self.hide_target()
            return
        pad = 24
        self.setGeometry(x - pad, y - pad, w + pad * 2, h + pad * 2)
        self.target_rect = QRectF(pad, pad, w, h)
        self.target_label = str(label or "")
        if not self._timer.isActive():
            self._timer.start()
        self.show()
        self.raise_()
        self.update()

    def hide_target(self):
        self._timer.stop()
        self.target_rect = None
        self.hide()

    def _tick(self):
        self._phase += 0.18
        self.update()

    def paintEvent(self, _):
        if self.target_rect is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pulse = 0.5 + 0.5 * math.sin(self._phase)
        r = self.target_rect
        st(p, (57, 215, 255, int(22 + 18 * pulse)), (57, 230, 255, int(190 + 60 * pulse)), 2.4)
        p.drawRoundedRect(r, 8, 8)
        if self.target_label:
            badge_rect = QRectF(r.left(), max(2.0, r.top() - 22.0), min(260.0, max(90.0, len(self.target_label) * 7.5 + 18)), 20.0)
            st(p, (8, 36, 62, 230), (90, 235, 255, 220), 1.1)
            p.drawRoundedRect(badge_rect, 5, 5)
            p.setPen(C(225, 252, 255))
            p.drawText(badge_rect.adjusted(6, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft, self.target_label)


class WorkflowStepConfirmDialog(QDialog):
    """Modal confirmation prompt shown before each workflow replay step. Allows Approve Step or Cancel Replay."""
    def __init__(self, step_index: int, total_steps: int, action_name: str, arguments: dict, description: str, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.approved = False
        self.cancelled = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setStyleSheet("""
            QFrame#panel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(14, 42, 70, 245),
                    stop:0.5 rgba(8, 28, 52, 250),
                    stop:1 rgba(4, 16, 32, 255));
                border: 1.5px solid rgba(80, 210, 255, 180);
                border-radius: 20px;
            }
        """)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(22, 20, 22, 20)
        pl.setSpacing(10)

        hdr = QLabel(f"▶  Workflow Replay — Step {step_index} of {total_steps}")
        hdr.setStyleSheet("font-size: 13.5pt; font-weight: 700; color: #a5f1ff;")
        pl.addWidget(hdr)

        act_lbl = QLabel(f"<b>Action:</b> <span style='color:#5efaff'>{html.escape(action_name)}</span>")
        act_lbl.setStyleSheet("font-size: 10.5pt; color: #ffffff;")
        pl.addWidget(act_lbl)

        desc_lbl = QLabel(f"<b>Description:</b> {html.escape(description or action_name)}")
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("color: #c8e6f5; font-size: 10pt;")
        pl.addWidget(desc_lbl)

        args_txt = json.dumps(arguments or {}, ensure_ascii=False, indent=2)
        args_lbl = QLabel(f"<b>Arguments:</b><br><pre style='margin:4px 0;color:#a0efff'>{html.escape(args_txt)}</pre>")
        args_lbl.setWordWrap(True)
        args_lbl.setStyleSheet("background: rgba(6, 20, 35, 175); padding: 8px; border-radius: 8px; border: 1px dashed rgba(80, 200, 255, 110);")
        pl.addWidget(args_lbl)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        approve_btn = QPushButton("✓  Approve & Execute Step")
        approve_btn.setCursor(Qt.PointingHandCursor)
        approve_btn.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(32,169,232,225), stop:1 rgba(23,85,165,232)); border: 1px solid #a0efff; font-weight: 700; border-radius: 12px; padding: 10px 14px;")
        approve_btn.clicked.connect(self._on_approve)
        btns.addWidget(approve_btn)

        cancel_btn = QPushButton("✕  Cancel Workflow Replay")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet("background: rgba(160, 40, 40, 165); border: 1px solid rgba(255, 100, 100, 165); border-radius: 12px; padding: 10px 14px;")
        cancel_btn.clicked.connect(self._on_cancel)
        btns.addWidget(cancel_btn)

        pl.addLayout(btns)
        layout.addWidget(panel)
        self.resize(500, 300)

    def _on_approve(self):
        self.approved = True
        self.cancelled = False
        self.accept()

    def _on_cancel(self):
        self.approved = False
        self.cancelled = True
        self.reject()


class WorkflowEditorDialog(QDialog):
    """Structured glass UI dialog for reviewing, reordering, editing, or deleting steps in a saved workflow."""
    def __init__(self, workflow_dict: dict, parent=None):
        super().__init__(parent, Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.workflow_name = str(workflow_dict.get("name", "Untitled Workflow"))
        self.steps = [dict(s) for s in (workflow_dict.get("steps") or [])]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setStyleSheet("""
            QFrame#panel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(14, 42, 70, 245),
                    stop:0.5 rgba(8, 28, 52, 250),
                    stop:1 rgba(4, 16, 32, 255));
                border: 1.5px solid rgba(80, 210, 255, 180);
                border-radius: 20px;
            }
            QListWidget {
                background: rgba(6, 22, 42, 210);
                border: 1px solid rgba(81, 190, 238, 135);
                border-radius: 12px;
                padding: 6px;
                color: #eaf8ff;
            }
            QListWidget::item {
                padding: 8px;
                border-radius: 6px;
                margin-bottom: 3px;
            }
            QListWidget::item:selected {
                background: rgba(32, 145, 205, 165);
                border: 1px solid #5efaff;
            }
        """)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(22, 20, 22, 20)
        pl.setSpacing(10)

        hdr = QLabel("✎  Review & Edit Workflow")
        hdr.setStyleSheet("font-size: 13.5pt; font-weight: 700; color: #a5f1ff;")
        pl.addWidget(hdr)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("<b>Workflow Name:</b>"))
        self.name_input = QLineEdit(self.workflow_name)
        name_row.addWidget(self.name_input, 1)
        pl.addLayout(name_row)

        pl.addWidget(QLabel("<b>Semantic Steps (select a step to reorder, edit description, or remove):</b>"))
        self.list_widget = QListWidget()
        pl.addWidget(self.list_widget, 1)

        ctl_row = QHBoxLayout()
        ctl_row.setSpacing(8)
        up_btn = QPushButton("▲ Move Up")
        up_btn.clicked.connect(self._move_up)
        down_btn = QPushButton("▼ Move Down")
        down_btn.clicked.connect(self._move_down)
        desc_btn = QPushButton("✎ Edit Description")
        desc_btn.clicked.connect(self._edit_desc)
        del_btn = QPushButton("🗑 Remove Step")
        del_btn.clicked.connect(self._remove_step)
        for b in (up_btn, down_btn, desc_btn, del_btn):
            b.setCursor(Qt.PointingHandCursor)
            ctl_row.addWidget(b)
        pl.addLayout(ctl_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)
        save_btn = QPushButton("✓  Save Changes")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet("background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(32,169,232,225), stop:1 rgba(23,85,165,232)); border: 1px solid #a0efff; font-weight: 700; border-radius: 12px; padding: 10px 14px;")
        save_btn.clicked.connect(self._on_save)
        bottom_row.addWidget(save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        bottom_row.addWidget(cancel_btn)

        pl.addLayout(bottom_row)
        layout.addWidget(panel)
        self.resize(560, 420)
        self._refresh_list()

    def _refresh_list(self, select_idx: int = -1):
        self.list_widget.clear()
        for idx, s in enumerate(self.steps, 1):
            act = s.get("action", "")
            desc = s.get("description", "")
            args = json.dumps(s.get("arguments") or {}, ensure_ascii=False)
            item = QListWidgetItem(f"{idx}. [{act}]  {desc}\n    Args: {args}")
            self.list_widget.addItem(item)
        if 0 <= select_idx < len(self.steps):
            self.list_widget.setCurrentRow(select_idx)

    def _move_up(self):
        row = self.list_widget.currentRow()
        if row > 0:
            self.steps[row - 1], self.steps[row] = self.steps[row], self.steps[row - 1]
            self._refresh_list(row - 1)

    def _move_down(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.steps) - 1:
            self.steps[row + 1], self.steps[row] = self.steps[row], self.steps[row + 1]
            self._refresh_list(row + 1)

    def _edit_desc(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.steps):
            cur = str(self.steps[row].get("description", ""))
            txt, ok = QInputDialog.getText(self, "Edit Step Description", "Step description:", text=cur)
            if ok and txt.strip():
                self.steps[row]["description"] = txt.strip()
                self._refresh_list(row)

    def _remove_step(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self.steps):
            del self.steps[row]
            self._refresh_list(min(row, len(self.steps) - 1))

    def _on_save(self):
        clean = self.name_input.text().strip()
        if clean:
            self.workflow_name = clean
        self.accept()


class Nova(QMainWindow):
    def __init__(self):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint); self.setWindowTitle(f'{APP_NAME} — Personal AI'); self.resize(1240, 800); self.setMinimumSize(980, 650)
        self.setAttribute(Qt.WA_TranslucentBackground); self.setAttribute(Qt.WA_NoSystemBackground); self.setAutoFillBackground(False)
        self.settings = QSettings('Nova', 'Nova'); self.glass_mode = 'clear' if str(self.settings.value('glass_mode', 'frost')) == 'clear' else 'frost'
        try: self.tint = cl(int(self.settings.value('tint', 224)), 145, 252)
        except (TypeError, ValueError): self.tint = 224
        self.wake_enabled = str(self.settings.value('wake_enabled', 'true')).lower() != 'false'
        self.minimize_to_tray = str(self.settings.value('minimize_to_tray', 'true')).lower() != 'false'
        try: self.mic_sensitivity = max(0, min(100, int(self.settings.value('mic_sensitivity', 68))))
        except (TypeError, ValueError): self.mic_sensitivity = 68
        self.mic_auto_nearest = str(self.settings.value('mic_auto_nearest', 'false')).lower() == 'true'
        self._mic_sens_widgets = []
        self._force_quit = False
        self.gesture_active = False
        self._active_perm_dialogs = {}
        self.chat_entries, self._awaiting_reply = [], False                  # one shared transcript for voice + typed chat
        self._chat_timer = QTimer(self, singleShot=True, timeout=self._render_chat)
        self.voice_output = str(self.settings.value('voice_output', 'true')).lower() != 'false'
        self.mic_on = self.mic_starting = self._stopping = self._fatal_shown = False
        self.backend_state, self._pending, self._out_raw, self._err_raw, self._err_tail = 'stopped', [], b'', b'', []
        self.process = QProcess(self); self.process.setWorkingDirectory(str(ROOT))
        env = QProcessEnvironment.systemEnvironment(); env.insert('PYTHONIOENCODING', 'utf-8'); env.insert('PYTHONUTF8', '1'); env.insert('PYTHONUNBUFFERED', '1')
        self.process.setProcessEnvironment(env)
        self.process.started.connect(self._backend_started)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self.backend_finished)
        self.process.errorOccurred.connect(self._backend_error)
        self.bots, self.assistant_state, self._bot_error = {}, 'idle', ''
        self.bot_kind = str(self.settings.value('bot_kind', 'mechanical'))
        if self.bot_kind not in BOT_NAMES: self.bot_kind = 'mechanical'
        # If settings contain an unsupported companion choice, use NOVA's original robot.
        self.orb = self._make_bot(self.bot_kind)
        if self.orb is None:
            self.bot_kind = 'mechanical'; self.settings.setValue('bot_kind', self.bot_kind)
            self.orb = self._make_bot('mechanical')
        self.companion_bridge = CompanionBridge(
            show_companion=self._show_companion_only,
            hide_companion=self._hide_companion_only,
            bring_main_window_forward=self.open_main_window,
        )
        self.walkthrough_overlay = WalkthroughHighlightOverlay()
        self._active_region_selector = None
        self._saved_workflows_cache = []
        self._workflow_recording = False
        self.screen_pointing_service = ScreenPointingService(
            request_screen_permission=self._request_local_screen_permission,
            capture_region_jpeg=self._capture_selected_region_jpeg,
            analyze_region=self._analyze_selected_region,
        )
        self.root = GlassRoot(); self.root.set_tint(self.tint); self.setCentralWidget(self.root)
        self.lay = QVBoxLayout(self.root); self.lay.setContentsMargins(32, 26, 32, 32); self.lay.setSpacing(10)
        tb = QHBoxLayout(); tb.setContentsMargins(8, 2, 2, 0); tb.setSpacing(8)
        mark = QLabel(f'◉  {APP_NAME.upper()}'); mark.setStyleSheet('font-weight:700;color:#b7f4ff'); tb.addWidget(mark)
        self.wake_badge = QLabel('🎙 Hey Nova: Active')
        self.wake_badge.setStyleSheet('padding:3px 10px;background:rgba(30,120,180,90);border:1px solid rgba(80,210,255,140);border-radius:10px;font-size:9pt;font-weight:600;color:#a5f1ff')
        self.wake_badge.setToolTip('Offline wake-word listener status')
        tb.addWidget(self.wake_badge)
        self.screen_badge = QLabel('👁 Screen Active')
        self.screen_badge.setStyleSheet('padding:3px 10px;background:rgba(20,160,180,140);border:1px solid #39e5ff;border-radius:10px;font-size:9pt;font-weight:700;color:#ffffff')
        self.screen_badge.setToolTip('Nova is actively observing or capturing your screen')
        self.screen_badge.hide()
        tb.addWidget(self.screen_badge)
        self.control_badge = QLabel('🖱 Control Active')
        self.control_badge.setStyleSheet('padding:3px 10px;background:rgba(220,110,20,150);border:1px solid #ffa339;border-radius:10px;font-size:9pt;font-weight:700;color:#ffffff')
        self.control_badge.setToolTip('Nova is interacting with your mouse or keyboard')
        self.control_badge.hide()
        tb.addWidget(self.control_badge)
        self.gesture_badge = QLabel('🖐 Gesture Active')
        self.gesture_badge.setStyleSheet('padding:3px 10px;background:rgba(40,180,100,150);border:1px solid #55ff99;border-radius:10px;font-size:9pt;font-weight:700;color:#ffffff')
        self.gesture_badge.setToolTip('Hand Gesture Mouse Control is active via webcam')
        self.gesture_badge.hide()
        tb.addWidget(self.gesture_badge)
        self.state_badge = QLabel('● Idle')
        self.state_badge.setStyleSheet('padding:3px 10px;background:rgba(25,95,145,110);border:1px solid rgba(90,210,255,130);border-radius:10px;font-size:9pt;font-weight:600;color:#b8f4ff')
        self.state_badge.setToolTip('Current assistant runtime state')
        tb.addWidget(self.state_badge)
        tb.addStretch(1)
        for txt, fn in (('—', self.showMinimized), ('□', self._toggle_max), ('×', self.close)):
            b = self.button(txt, fn, 'ctl'); b.setFixedSize(34, 28); tb.addWidget(b)
        self.lay.addLayout(tb); content = QHBoxLayout(); content.setSpacing(16); self.lay.addLayout(content, 1)
        side = QFrame(); side.setObjectName('sidebar'); side.setFixedWidth(218); sl = QVBoxLayout(side); sl.setContentsMargins(16, 18, 16, 18); sl.setSpacing(8)
        brand = QLabel(f'◉  {APP_NAME.upper()}'); brand.setObjectName('brand'); sl.addWidget(brand)
        sub = QLabel('PERSONAL AI ASSISTANT'); sub.setObjectName('muted'); sub.setStyleSheet('font-size:8pt;letter-spacing:1px'); sl.addWidget(sub); sl.addSpacing(20)
        self.stack, self.nav_buttons = QStackedWidget(), []
        for i, (name, icon) in enumerate((('Overview', '⌂'), ('Chat && Voice', '◉'), ('Tasks && Memory', '▤'), ('Computer Tools', '▦'), ('Settings', '⚙'))):
            b = self.button(f'{icon}   {name}', lambda _=False, i=i: self.change_page(i), 'nav'); b.setCheckable(True); sl.addWidget(b); self.nav_buttons.append(b)
        sl.addStretch(1); self.status_dot = QLabel('●  Assistant stopped'); self.status_dot.setObjectName('muted'); sl.addWidget(self.status_dot)
        self.sidebar_gesture_btn = self.button('🖐  Hand Gesture: Off', self.toggle_hand_gesture)
        sl.addWidget(self.sidebar_gesture_btn)
        sl.addWidget(self.button('◉  Toggle Floating Bot', self.toggle_orb)); content.addWidget(side)
        right = QVBoxLayout(); right.setSpacing(14); content.addLayout(right, 1); top = QHBoxLayout()
        title = QLabel('Your personal AI workspace'); title.setObjectName('heading'); top.addWidget(title); top.addStretch()
        self.voice_btn = self.button('▶  Start voice assistant', self.toggle_voice, 'primary'); top.addWidget(self.voice_btn); right.addLayout(top)
        for pg in (self._overview(), self._chat(), self._tasks(), self._tools(), self._settings()): self.stack.addWidget(pg)
        right.addWidget(self.stack, 1); self.change_page(0); self.setStyleSheet(STYLE)
        self.grip = QSizeGrip(self.root); self.grip.setFixedSize(16, 16); self._glass_text()
        self.pill = NavPill(side); self.pill.lower(); self.pill.hide(); curve = QEasingCurve(QEasingCurve.OutBack); curve.setOvershoot(1.1)
        self.pill_anim = QPropertyAnimation(self.pill, b'geometry', self); self.pill_anim.setDuration(430); self.pill_anim.setEasingCurve(curve)
        self.boost_anim = QVariantAnimation(self, startValue=1.0, endValue=0.0, duration=560, valueChanged=self.pill.set_boost); self.boost_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._layout_mode()
        # System Tray Icon
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(_make_tray_icon())
        self.tray_icon.setToolTip(f'{APP_NAME} — Personal AI Assistant')

        tray_menu = QMenu()
        tray_menu.setStyleSheet(MENU_CSS)
        tray_menu.addAction('Open Glass Nova', self.open_main_window)
        self.tray_gesture_act = tray_menu.addAction('Hand Gesture Mouse: Off', self.toggle_hand_gesture)
        self.tray_wake_act = tray_menu.addAction('Wake Word: Active', self.toggle_wake_word)
        self.tray_voice_out_act = tray_menu.addAction('Spoken Replies: On', self.toggle_voice_output)
        self.tray_startup_act = tray_menu.addAction('Start with Windows', self.toggle_startup)
        self.tray_startup_act.setCheckable(True)
        self.tray_startup_act.setChecked(is_run_at_startup())
        tray_menu.addSeparator()
        tray_menu.addAction(f'Exit {APP_NAME}', self.quit_app)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

        # Wake-word listener & signals
        self._wake_signal = WakeSignal()
        self._wake_signal.detected.connect(self._handle_wake_word_qt)
        self._wake_signal.status.connect(self._handle_wake_status_qt)

        self.wake_listener = WakeWordListener(
            on_detected=lambda: self._wake_signal.detected.emit(),
            on_status_change=lambda s: self._wake_signal.status.emit(s)
        )
        if self.wake_enabled:
            self.wake_listener.start()
        else:
            self._handle_wake_status_qt('stopped')
        self.shortcut_key_str = str(self.settings.value('companion_shortcut', 'Ctrl+Shift+N') or 'Ctrl+Shift+N')
        self._companion_shortcut = QShortcut(QKeySequence(self.shortcut_key_str), self)
        self._companion_shortcut.setContext(Qt.ApplicationShortcut)
        self._companion_shortcut.activated.connect(self.toggle_orb)
        self._point_shortcut = QShortcut(QKeySequence('Ctrl+Shift+P'), self)
        self._point_shortcut.setContext(Qt.ApplicationShortcut)
        self._point_shortcut.activated.connect(self.start_screen_pointing_ui)
        self._global_hotkeys_registered = False
        QTimer.singleShot(300, self._register_win32_global_hotkeys)
        QTimer.singleShot(200, self._auto_start_backend)

    # -- builders
    def button(self, text, fn, name=None):
        b = QPushButton(text); b.clicked.connect(fn)
        if name: b.setObjectName(name)
        if name != 'ctl': b.setMinimumHeight(34)
        return b
    def textbox(self, ph):
        t = QTextEdit(); t.setReadOnly(True); t.setPlaceholderText(ph); return t
    def panel(self, title, desc=None):
        f = QFrame(); f.setObjectName('panel'); v = QVBoxLayout(f); v.setContentsMargins(18, 16, 18, 16); v.setSpacing(8)
        h = QLabel(title); h.setStyleSheet('font-size:13pt;font-weight:650'); v.addWidget(h)
        if desc: d = QLabel(desc); d.setObjectName('muted'); d.setWordWrap(True); d.setMinimumWidth(120); v.addWidget(d)
        return f, v
    def _create_mic_sensitivity_widget(self):
        w = MicSensitivityWidget(self.mic_sensitivity, self.mic_auto_nearest, self)
        w.sensitivityChanged.connect(self._on_ui_mic_sensitivity_changed)
        self._mic_sens_widgets.append(w)
        return w

    def _on_ui_mic_sensitivity_changed(self, sensitivity: int, auto_nearest: bool, calibrate_now: bool):
        self.mic_sensitivity = max(0, min(100, int(sensitivity)))
        self.mic_auto_nearest = bool(auto_nearest)
        self.settings.setValue('mic_sensitivity', self.mic_sensitivity)
        self.settings.setValue('mic_auto_nearest', 'true' if self.mic_auto_nearest else 'false')
        for w in self._mic_sens_widgets:
            w.sync_state(self.mic_sensitivity, self.mic_auto_nearest)
        if self.backend_running():
            self.send_cmd(
                cmd='mic_sensitivity',
                sensitivity=self.mic_sensitivity,
                auto_nearest=self.mic_auto_nearest,
                calibrate_now=bool(calibrate_now),
            )

    def _overview(self):
        w = QWidget(); v = QVBoxLayout(w); v.setSpacing(14); hero, h = self.panel('Welcome back', 'A calmer, clearer home for your AI assistant.')
        self.hero_orb = OrbWidget(); self.hero_orb.setMinimumHeight(240); h.addWidget(self.hero_orb, 1)
        self.big_status = QLabel('Ready when you are'); self.big_status.setAlignment(Qt.AlignCenter); self.big_status.setStyleSheet('font-size:15pt;font-weight:600'); h.addWidget(self.big_status)
        self.overview_voice_btn = self.button('▶  Start voice assistant', self.toggle_voice, 'primary'); h.addWidget(self.overview_voice_btn); v.addWidget(hero, 1); row = QHBoxLayout()
        self.mic_sens_overview = self._create_mic_sensitivity_widget()
        b, bv = self.panel('Activity monitor', 'Recent status messages appear here.'); self.activity = self.textbox('Assistant activity will appear here when started.'); self.activity.setMaximumHeight(115); bv.addWidget(self.activity)
        row.addWidget(self.mic_sens_overview, 1); row.addWidget(b, 1); v.addLayout(row); return w
    def _chat(self):
        w = QWidget(); v = QVBoxLayout(w); pnl, p = self.panel('Chat & Voice', 'Type a message or use the live voice assistant. Chat replies appear here.')
        self.chatlog = self.textbox(f'Type a message below to chat with {APP_NAME}. Voice transcriptions also appear here.'); p.addWidget(self.chatlog, 1); row = QHBoxLayout()
        self.chat_input = QLineEdit(); self.chat_input.setPlaceholderText(f'Message {APP_NAME}…'); self.chat_input.returnPressed.connect(self.send_chat); row.addWidget(self.chat_input, 1)
        self.send_button = self.button('Send', self.send_chat, 'primary'); row.addWidget(self.send_button)
        p.addLayout(row); v.addWidget(pnl, 1); return w
    def _tasks(self):
        w = QWidget(); v = QVBoxLayout(w); v.setSpacing(14)
        tp, tv = self.panel('Tasks & Reminders', 'Manage tracked goals, tasks, and scheduled reminders stored locally.')
        self.tasks_view = self.textbox('No tracked tasks yet. Add one below or ask Nova by voice.')
        tv.addWidget(self.tasks_view, 1)
        t_btns = QHBoxLayout()
        t_btns.addWidget(self.button('＋  Add Task', self._ui_add_task, 'primary'))
        t_btns.addWidget(self.button('✓  Complete Task', self._ui_complete_task))
        t_btns.addWidget(self.button('🗑  Delete Task', self._ui_delete_task))
        t_btns.addWidget(self.button('↻  Refresh', self._refresh_tasks_and_memory))
        tv.addLayout(t_btns)
        v.addWidget(tp, 1)

        mp, mv = self.panel('Persistent Local Memory', 'Manage approved non-sensitive facts and preferences Nova remembers across sessions.')
        self.memories_view = self.textbox('No saved memories yet. Save one below or ask Nova to remember something.')
        mv.addWidget(self.memories_view, 1)
        m_btns = QHBoxLayout()
        m_btns.addWidget(self.button('＋  Save Memory', self._ui_add_memory, 'primary'))
        m_btns.addWidget(self.button('🗑  Forget Memory', self._ui_forget_memory))
        m_btns.addWidget(self.button('🧹  Clear All Memories', self._ui_clear_memories))
        mv.addLayout(m_btns)
        v.addWidget(mp, 1)
        QTimer.singleShot(100, self._refresh_tasks_and_memory)
        return w

    def _refresh_tasks_and_memory(self):
        try:
            import tools
            t_res = tools.task_control(action='list')
            tasks = t_res.get('tasks', []) if t_res.get('ok') else []
            if not tasks:
                self.tasks_view.setHtml('<i style="color:#9cb5c6">No tracked tasks yet.</i>')
            else:
                lines = []
                for t in tasks:
                    tid = html.escape(str(t.get('id', '')))
                    title = html.escape(str(t.get('title', '')))
                    status = str(t.get('status', 'active'))
                    due = html.escape(str(t.get('due_at') or ''))
                    details = html.escape(str(t.get('details') or ''))
                    badge = '✅ Completed' if status == 'completed' else '⏳ Active'
                    due_str = f' &nbsp;<span style="color:#7dc4ea">(Due: {due})</span>' if due else ''
                    det_str = f'<br><span style="color:#b8e2f8;font-size:9pt">{details}</span>' if details else ''
                    lines.append(f'<p style="margin:0 0 8px 0"><b>[{tid}]</b> {badge} — <b>{title}</b>{due_str}{det_str}</p>')
                self.tasks_view.setHtml(''.join(lines))

            m_res = tools.memory_control(action='list')
            mems = m_res.get('memories', []) if m_res.get('ok') else []
            if not mems:
                self.memories_view.setHtml('<i style="color:#9cb5c6">No saved memories yet.</i>')
            else:
                mlines = []
                for m in mems:
                    k = html.escape(str(m.get('key', '')))
                    val = html.escape(str(m.get('value', '')))
                    mlines.append(f'<p style="margin:0 0 6px 0"><b>• {k}:</b> {val}</p>')
                self.memories_view.setHtml(''.join(mlines))
        except Exception as e:
            self.append_log(f'[Tasks/Memory UI] Refresh error: {e}')

    def _ui_add_task(self):
        import tools
        title, ok = QInputDialog.getText(self, 'Add Task', 'Task title:')
        if not ok or not title.strip():
            return
        details, _ = QInputDialog.getText(self, 'Task Details', 'Optional details (or leave blank):')
        due, _ = QInputDialog.getText(self, 'Due Time (Optional)', 'Optional ISO 8601 due time (e.g. 2026-11-15T18:00:00) or leave blank:')
        res = tools.task_control(action='create', title=title.strip(), details=details.strip(), due_at=due.strip())
        if not res.get('ok'):
            QMessageBox.warning(self, 'Could not add task', str(res.get('message', 'Error')))
        self._refresh_tasks_and_memory()

    def _ui_complete_task(self):
        import tools
        tid, ok = QInputDialog.getText(self, 'Complete Task', 'Enter Task ID (shown in [brackets]):')
        if not ok or not tid.strip():
            return
        res = tools.task_control(action='complete', task_id=tid.strip().strip('[]'))
        if not res.get('ok'):
            QMessageBox.warning(self, 'Could not complete task', str(res.get('message', 'Error')))
        self._refresh_tasks_and_memory()

    def _ui_delete_task(self):
        import tools
        tid, ok = QInputDialog.getText(self, 'Delete Task', 'Enter Task ID to delete (shown in [brackets]):')
        if not ok or not tid.strip():
            return
        res = tools.task_control(action='delete', task_id=tid.strip().strip('[]'), confirm=True)
        if not res.get('ok'):
            QMessageBox.warning(self, 'Could not delete task', str(res.get('message', 'Error')))
        self._refresh_tasks_and_memory()

    def _ui_add_memory(self):
        import tools
        key, ok = QInputDialog.getText(self, 'Save Memory', 'Memory key / topic (e.g. Favorite IDE):')
        if not ok or not key.strip():
            return
        val, ok2 = QInputDialog.getText(self, 'Save Memory', f'Value to remember for "{key.strip()}":')
        if not ok2 or not val.strip():
            return
        res = tools.memory_control(action='remember', key=key.strip(), value=val.strip())
        if not res.get('ok'):
            QMessageBox.warning(self, 'Could not save memory', str(res.get('message', 'Error')))
        self._refresh_tasks_and_memory()

    def _ui_forget_memory(self):
        import tools
        key, ok = QInputDialog.getText(self, 'Forget Memory', 'Enter memory key to remove:')
        if not ok or not key.strip():
            return
        res = tools.memory_control(action='forget', key=key.strip())
        if not res.get('ok'):
            QMessageBox.warning(self, 'Could not forget memory', str(res.get('message', 'Error')))
        self._refresh_tasks_and_memory()

    def _ui_clear_memories(self):
        import tools
        ans = QMessageBox.question(self, 'Clear All Memories', 'Are you sure you want to delete all saved memories?', QMessageBox.Yes | QMessageBox.No)
        if ans == QMessageBox.Yes:
            tools.memory_control(action='clear', confirm=True)
            self._refresh_tasks_and_memory()
    def _tools(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(7)
        for i, (label, kind) in enumerate((('🎯 Point at Screen', 'point_screen'), ('🖐 Toggle Hand Gesture Mouse', 'gesture'), ('Open Calculator', 'calc'), ('Open Notepad', 'notepad'), ('Open File Explorer', 'explorer'), ('Open Downloads', 'downloads'), ('Search Web', 'web'), ('System Status', 'status'))):
            b = self.button(label, lambda _=False, k=kind: self.quick_action(k)); grid.addWidget(b, i // 2, i % 2)
        p, pv = self.panel('Computer tools & Point at Screen', 'Quick actions run only when clicked. Point at Screen captures only your user-selected rectangle after permission approval.'); pv.addLayout(grid)
        pt_row = QHBoxLayout()
        self.point_question_input = QLineEdit()
        self.point_question_input.setPlaceholderText('Question for Point at Screen (e.g. "What does this error dialog mean?")…')
        self.point_question_input.returnPressed.connect(self.start_screen_pointing_ui)
        pt_row.addWidget(self.point_question_input, 1)
        pt_row.addWidget(self.button('🎯  Select Region & Explain', self.start_screen_pointing_ui, 'primary'))
        pv.addLayout(pt_row)
        self.point_result_view = self.textbox('Point at Screen explanation will appear here. Guidance only — Nova never clicks or types during screen pointing.')
        self.point_result_view.setMinimumHeight(46)
        self.point_result_view.setMaximumHeight(64)
        pv.addWidget(self.point_result_view)
        v.addWidget(p)

        bottom_row = QHBoxLayout(); bottom_row.setSpacing(10)

        # Guided Walkthroughs compact panel
        wt_p, wt_v = self.panel('Guided Walkthroughs', 'Step-by-step visual guidance (Next / Back / Pause / Stop). Nova highlights UI targets without clicking or typing.')
        wt_top = QHBoxLayout()
        self.walkthrough_goal_input = QLineEdit()
        self.walkthrough_goal_input.setPlaceholderText('Ask "Show me how to…" (e.g. change Windows display scale)…')
        self.walkthrough_goal_input.returnPressed.connect(self._ui_start_walkthrough)
        wt_top.addWidget(self.walkthrough_goal_input, 1)
        wt_top.addWidget(self.button('▶  Guide Me', self._ui_start_walkthrough, 'primary'))
        wt_v.addLayout(wt_top)
        self.walkthrough_view = self.textbox('No active walkthrough. Enter a topic above or ask Nova "show me how to do this".')
        self.walkthrough_view.setMinimumHeight(54)
        self.walkthrough_view.setMaximumHeight(82)
        wt_v.addWidget(self.walkthrough_view)
        wt_btns = QHBoxLayout()
        wt_btns.addWidget(self.button('◀  Back', lambda: self._ui_walkthrough_nav('back')))
        wt_btns.addWidget(self.button('⏸  Pause / Resume', self._ui_walkthrough_toggle_pause))
        wt_btns.addWidget(self.button('▶  Next', lambda: self._ui_walkthrough_nav('next'), 'primary'))
        wt_btns.addWidget(self.button('■  Stop', lambda: self._ui_walkthrough_nav('stop')))
        wt_v.addLayout(wt_btns)
        bottom_row.addWidget(wt_p, 1)

        # Reusable Semantic Workflows compact panel
        wf_p, wf_v = self.panel('Reusable Workflows', 'Record approved semantic actions (no raw keys or secrets). Review, edit, delete, or replay with per-step approval.')
        self.workflow_view = self.textbox('No saved workflows yet. Start recording or refresh.')
        self.workflow_view.setMinimumHeight(54)
        self.workflow_view.setMaximumHeight(82)
        wf_v.addWidget(self.workflow_view)
        wf_btns1 = QHBoxLayout()
        self.wf_rec_btn = self.button('●  Start Recording', self._ui_toggle_workflow_recording, 'primary')
        wf_btns1.addWidget(self.wf_rec_btn)
        wf_btns1.addWidget(self.button('▶  Replay Workflow', self._ui_replay_workflow))
        wf_btns1.addWidget(self.button('■  Cancel Replay', self._ui_cancel_workflow_replay))
        wf_v.addLayout(wf_btns1)
        wf_btns2 = QHBoxLayout()
        wf_btns2.addWidget(self.button('✎  Review / Edit', self._ui_edit_workflow))
        wf_btns2.addWidget(self.button('🗑  Delete', self._ui_delete_workflow))
        wf_btns2.addWidget(self.button('↻  Refresh', self._ui_refresh_workflows))
        wf_v.addLayout(wf_btns2)
        bottom_row.addWidget(wf_p, 1)

        v.addLayout(bottom_row, 1)
        QTimer.singleShot(250, self._ui_refresh_workflows)
        return w

    def _settings(self):
        w = QWidget(); outer = QVBoxLayout(w); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget(); v = QVBoxLayout(container); v.setContentsMargins(0, 0, 6, 0); v.setSpacing(10)
        p, pv = self.panel('Settings', 'Keep your Gemini API key in the local .env file; never paste it into chat.')
        self.orb_check = self.button(
            'Disable floating bot' if self.orb.isVisible() else 'Enable floating bot',
            self.toggle_orb)
        pv.addWidget(self.orb_check)
        self.gesture_btn = self.button('', self.toggle_hand_gesture); pv.addWidget(self.gesture_btn); self._update_gesture_ui()
        self.bot_btn = self.button('', self.choose_bot); pv.addWidget(self.bot_btn); self._bot_text()
        self.voice_out_btn = self.button('', self.toggle_voice_output); pv.addWidget(self.voice_out_btn); self._voice_out_text()

        # Wake word toggle
        self.wake_btn = self.button('', self.toggle_wake_word); pv.addWidget(self.wake_btn); self._update_wake_btn_text()

        # Start with Windows toggle
        self.startup_btn = self.button('', self.toggle_startup); pv.addWidget(self.startup_btn); self._update_startup_btn_text()

        # Minimize to Tray toggle
        self.tray_toggle_btn = self.button('', self.toggle_minimize_to_tray); pv.addWidget(self.tray_toggle_btn); self._update_tray_btn_text()

        self.glass_btn = self.button('', self.cycle_glass); pv.addWidget(self.glass_btn)
        tl = QLabel('Glass tint — left is clearest, right is easier to read'); tl.setObjectName('muted'); pv.addWidget(tl)
        self.tint_slider = QSlider(Qt.Horizontal); self.tint_slider.setRange(145, 252); self.tint_slider.setValue(cl(self.tint, 145, 252)); self.tint_slider.valueChanged.connect(self.set_tint); pv.addWidget(self.tint_slider)

        # Microphone Sensitivity & AUTO NEAREST HUD section
        self.mic_sens_settings = self._create_mic_sensitivity_widget()
        pv.addWidget(self.mic_sens_settings)

        # Security & Permissions section
        sec_box, sec_v = self.panel('Security & Permissions', 'Manage active session permissions for visual screen and desktop controls.')
        self.perm_status_lbl = QLabel(self._get_permissions_summary()); self.perm_status_lbl.setObjectName('muted'); self.perm_status_lbl.setWordWrap(True)
        sec_v.addWidget(self.perm_status_lbl)
        self.revoke_btn = self.button('🔒  Revoke All Active Permissions', self.revoke_all_permissions)
        sec_v.addWidget(self.revoke_btn)
        pv.addWidget(sec_box)

        info = QLabel('API key: .env file\nVoice engine: Gemini Live backend\nTheme: Glass Futuristic'); info.setObjectName('muted'); pv.addWidget(info)
        self.log = self.textbox('Backend logs…')
        self.log.setMinimumHeight(110)
        pv.addWidget(self.log, 1)
        v.addWidget(p, 1)
        scroll.setWidget(container)
        outer.addWidget(scroll, 1)
        return w
    def change_page(self, i):
        self.stack.setCurrentIndex(i)
        for j, b in enumerate(self.nav_buttons): b.setChecked(i == j)
        if i == 2 and hasattr(self, 'tasks_view'):
            self._refresh_tasks_and_memory()
        self._move_pill(True)
    def _move_pill(self, animate=True):
        if not hasattr(self, 'pill_anim'): return
        r = self.nav_buttons[self.stack.currentIndex()].geometry(); a = self.pill_anim
        if r.width() <= 100: return                                                          # layout not ready yet
        if not animate:
            if a.state() != QAbstractAnimation.Running: self.pill.setGeometry(r); self.pill.show()
        elif self.pill.isVisible():
            a.stop(); a.setStartValue(self.pill.geometry()); a.setEndValue(r); a.start(); self.boost_anim.stop(); self.boost_anim.start()
        else: self.pill.setGeometry(r); self.pill.show()
    def showEvent(self, e):
        super().showEvent(e)
        for ms in (0, 150): QTimer.singleShot(ms, lambda: self._move_pill(False))
    def _place_grip(self): self.grip.move(self.width() - 22 - self.root.inset, self.height() - 22 - self.root.inset); self.grip.raise_()
    def _layout_mode(self):                      # frosted/maximized: glass fills the whole window, so the blur has no stray frame around it
        m = self.isMaximized(); flat = m or self.glass_mode == 'frost'; self.root.set_flat(flat, 0 if m else 10)
        self.lay.setContentsMargins(*((18, 12, 18, 18) if flat else (32, 26, 32, 32))); self.grip.setVisible(not m); self._place_grip()

    # -- window behaviour
    def _toggle_max(self): self.showNormal() if self.isMaximized() else self.showMaximized()
    def _in_titlebar(self, e): return e.position().y() <= self.root.inset + 46
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._in_titlebar(e) and not self.isMaximized() and self.windowHandle() and self.windowHandle().startSystemMove(): return e.accept()
        super().mousePressEvent(e)
    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and self._in_titlebar(e): return self._toggle_max()
        super().mouseDoubleClickEvent(e)
    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, 'grip'): self._place_grip()
        if self.glass_mode == 'frost' and self.isVisible(): apply_backdrop(self, True)           # keep blur region matched
    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange and hasattr(self, 'grip'):
            self._layout_mode()
            if self.glass_mode == 'frost': apply_backdrop(self, True)
        super().changeEvent(e)
    def open_main_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _register_win32_global_hotkeys(self):
        """Register OS-wide Windows hotkeys via user32.RegisterHotKey on Nova's HWND (no background polling thread)."""
        if sys.platform != 'win32' or self._global_hotkeys_registered:
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            # MOD_CONTROL (0x0002) | MOD_SHIFT (0x0004) | MOD_NOREPEAT (0x4000) = 0x4006
            # 0x4E = 'N' (ID 9001: Toggle Floating Companion), 0x50 = 'P' (ID 9002: Point at Screen)
            r1 = bool(user32.RegisterHotKey(hwnd, 9001, 0x4006, 0x4E))
            r2 = bool(user32.RegisterHotKey(hwnd, 9002, 0x4006, 0x50))
            self._global_hotkeys_registered = r1 or r2
        except Exception:
            pass

    def _unregister_win32_global_hotkeys(self):
        if sys.platform != 'win32' or not getattr(self, '_global_hotkeys_registered', False):
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            user32.UnregisterHotKey(hwnd, 9001)
            user32.UnregisterHotKey(hwnd, 9002)
        except Exception:
            pass
        self._global_hotkeys_registered = False

    def nativeEvent(self, eventType, message):
        if sys.platform == 'win32':
            try:
                import ctypes
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0312:  # WM_HOTKEY
                    hk_id = int(msg.wParam)
                    if hk_id == 9001:
                        QTimer.singleShot(0, self.toggle_orb)
                        return True, 0
                    elif hk_id == 9002:
                        QTimer.singleShot(0, self.start_screen_pointing_ui)
                        return True, 0
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def closeEvent(self, e):
        if self.minimize_to_tray and not self._force_quit:
            e.ignore()
            self.hide()
            if hasattr(self, 'tray_icon') and self.tray_icon.isVisible():
                self.tray_icon.showMessage(
                    APP_NAME,
                    'Nova is running in the background. Say "Hey Nova" or click this icon to open.',
                    QSystemTrayIcon.Information,
                    2500
                )
            return
        self.quit_app()
        e.accept()

    def quit_app(self):
        self._force_quit = True
        self._unregister_win32_global_hotkeys()
        if hasattr(self, 'walkthrough_overlay') and self.walkthrough_overlay:
            try:
                self.walkthrough_overlay.hide_target()
                self.walkthrough_overlay.close()
            except Exception:
                pass
        if hasattr(self, 'wake_listener') and self.wake_listener:
            try:
                self.wake_listener.stop()
            except Exception:
                pass
        self.shutdown_backend()
        for b in self.bots.values():
            try:
                b.close()
            except Exception:
                pass
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
        QApplication.quit()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_main_window()

    def _handle_wake_word_qt(self):
        self.append_log('[Wake Word] "Hey Nova" detected!')
        if hasattr(self, 'companion_bridge') and self.companion_bridge:
            self.companion_bridge.on_wake_word()
        else:
            self.open_main_window()
        if hasattr(self, 'orb') and self.orb and hasattr(self.orb, 'react'):
            self.orb.react(2.0)
        if self.wake_listener:
            self.wake_listener.pause()
        self._update_wake_badge(paused=True)
        if not (self.mic_on or self.mic_starting):
            if self.ensure_backend():
                self.mic_starting = True
                self._voice_btn_text()
                QTimer.singleShot(150, lambda: self.send_cmd(cmd='wake_greet'))

    def _handle_wake_status_qt(self, status):
        self._update_wake_badge(status=status)

    def _update_wake_badge(self, paused=False, status=None):
        st = status or (self.wake_listener.status if hasattr(self, 'wake_listener') and self.wake_listener else 'stopped')
        if hasattr(self, 'wake_badge') and self.wake_badge is not None:
            if not self.wake_enabled:
                self.wake_badge.setText('🎙 Hey Nova: Off')
                self.wake_badge.setStyleSheet('padding:3px 10px;background:rgba(40,50,60,90);border:1px solid rgba(120,130,140,100);border-radius:10px;font-size:9pt;font-weight:600;color:#9cb5c6')
            elif paused or st == 'paused':
                self.wake_badge.setText('🎙 Hey Nova: Paused')
                self.wake_badge.setStyleSheet('padding:3px 10px;background:rgba(180,120,30,90);border:1px solid rgba(255,180,60,140);border-radius:10px;font-size:9pt;font-weight:600;color:#ffe4a0')
            elif st == 'error':
                self.wake_badge.setText('🎙 Hey Nova: Error')
                self.wake_badge.setStyleSheet('padding:3px 10px;background:rgba(180,40,40,90);border:1px solid rgba(255,80,80,140);border-radius:10px;font-size:9pt;font-weight:600;color:#ffa0a0')
            else:
                self.wake_badge.setText('🎙 Hey Nova: Active')
                self.wake_badge.setStyleSheet('padding:3px 10px;background:rgba(30,120,180,90);border:1px solid rgba(80,210,255,140);border-radius:10px;font-size:9pt;font-weight:600;color:#a5f1ff')
        self._update_wake_btn_text()

    def _update_wake_btn_text(self):
        txt = 'Wake Word ("Hey Nova"): Active   (click to pause)' if self.wake_enabled else 'Wake Word ("Hey Nova"): Paused   (click to resume)'
        if hasattr(self, 'wake_btn'):
            self.wake_btn.setText(txt)
        if hasattr(self, 'tray_wake_act'):
            self.tray_wake_act.setText('Wake Word: Active' if self.wake_enabled else 'Wake Word: Paused')

    def toggle_wake_word(self):
        self.wake_enabled = not self.wake_enabled
        self.settings.setValue('wake_enabled', 'true' if self.wake_enabled else 'false')
        if self.wake_enabled:
            if self.wake_listener:
                self.wake_listener.start()
                self.wake_listener.resume()
            self.append_log('Wake word "Hey Nova" resumed.')
        else:
            if self.wake_listener:
                self.wake_listener.pause()
            self.append_log('Wake word "Hey Nova" paused.')
        self._update_wake_badge()

    def toggle_startup(self):
        now = is_run_at_startup()
        set_run_at_startup(not now)
        self._update_startup_btn_text()

    def _update_startup_btn_text(self):
        active = is_run_at_startup()
        txt = 'Start with Windows: Enabled   (click to disable)' if active else 'Start with Windows: Disabled   (click to enable)'
        if hasattr(self, 'startup_btn'):
            self.startup_btn.setText(txt)
        if hasattr(self, 'tray_startup_act'):
            self.tray_startup_act.setChecked(active)

    def toggle_minimize_to_tray(self):
        self.minimize_to_tray = not self.minimize_to_tray
        self.settings.setValue('minimize_to_tray', 'true' if self.minimize_to_tray else 'false')
        self._update_tray_btn_text()

    def _update_tray_btn_text(self):
        txt = 'Close to Tray: Enabled   (click to disable)' if self.minimize_to_tray else 'Close to Tray: Disabled   (click to enable)'
        if hasattr(self, 'tray_toggle_btn'):
            self.tray_toggle_btn.setText(txt)

    def _prompt_permission(self, req_id: str, cat: str, details: str) -> str:
        self.open_main_window()
        was_wake_listening = bool(
            hasattr(self, 'wake_listener') and self.wake_listener and self.wake_listener.status == 'listening'
        )
        if was_wake_listening:
            self.wake_listener.pause()
        dlg = PermissionDialog(cat, details, self)
        self._active_perm_dialogs[req_id] = dlg
        try:
            dlg.exec()
        finally:
            self._active_perm_dialogs.pop(req_id, None)
            if was_wake_listening and self.wake_enabled and not (self.mic_on or self.mic_starting):
                self.wake_listener.resume()
        decision = dlg.selected_decision
        if decision in ('session', 'allow_session'):
            PermissionManager().grant_session(cat)
        self.append_log(f'[Permission] {cat} -> {decision}')
        self._refresh_perms_ui()
        return decision

    def revoke_all_permissions(self):
        self.send_cmd(cmd='perm_revoke', category='all')
        PermissionManager().revoke_all()
        self.append_log('[Permission] Revoked all active session permissions.')
        self._refresh_perms_ui()

    def _get_permissions_summary(self) -> str:
        grants = PermissionManager().get_status()
        active_grants = [k for k, v in grants.items() if v]
        if not active_grants:
            return 'No session permissions currently granted. Nova will prompt when needed.'
        lines = ['Active Session Permissions:']
        for k in active_grants:
            info = CATEGORIES.get(k, {})
            title = info.get('title', k)
            lines.append(f'  • {title} ({k})')
        return '\n'.join(lines)

    def _refresh_perms_ui(self):
        if hasattr(self, 'perm_status_lbl'):
            self.perm_status_lbl.setText(self._get_permissions_summary())

    # -- glass + orb settings
    def apply_backdrop(self):
        msg = apply_backdrop(self, self.glass_mode == 'frost')
        if msg: self.append_log(msg)
    def _glass_text(self): self.glass_btn.setText('Glass: Clear — 100% see-through   (click for Frosted blur)' if self.glass_mode == 'clear' else 'Glass: Frosted blur   (click for Clear)')
    def cycle_glass(self):
        self.glass_mode = 'frost' if self.glass_mode == 'clear' else 'clear'; self.settings.setValue('glass_mode', self.glass_mode); self._glass_text(); self._layout_mode(); self.apply_backdrop()
    def set_tint(self, v): self.tint = cl(int(v), 145, 252); self.root.set_tint(self.tint); self.settings.setValue('tint', self.tint)
    def set_orb_state(self, s):
        self.assistant_state = s
        self.hero_orb.set_state(s)
        self.orb.set_assistant_state(s)
        if hasattr(self, 'state_badge') and self.state_badge is not None:
            labels = {
                'idle': ('● Idle', 'rgba(25,95,145,110)', 'rgba(90,210,255,130)', '#b8f4ff'),
                'listening': ('🎙 Listening', 'rgba(20,145,195,140)', '#45e6ff', '#ffffff'),
                'thinking': ('✦ Thinking', 'rgba(85,95,215,145)', '#a8b8ff', '#ffffff'),
                'speaking': ('🔊 Speaking', 'rgba(25,175,175,145)', '#65fff0', '#ffffff'),
                'waiting_approval': ('⏳ Waiting Approval', 'rgba(195,130,20,165)', '#ffd060', '#fff8e0'),
                'executing': ('⚙ Executing', 'rgba(30,170,105,165)', '#60ffaa', '#ffffff'),
                'paused': ('⏸ Paused', 'rgba(70,85,105,135)', 'rgba(150,175,200,130)', '#d0e2f2'),
                'error': ('⚠ Error', 'rgba(185,40,50,165)', '#ff8088', '#ffe6e8'),
            }
            txt, bg, border, col = labels.get(s, labels['idle'])
            self.state_badge.setText(txt)
            self.state_badge.setStyleSheet(f'padding:3px 10px;background:{bg};border:1px solid {border};border-radius:10px;font-size:9pt;font-weight:700;color:{col}')

    def _show_companion_only(self):
        if hasattr(self, 'orb') and self.orb:
            self.orb.show_bot()
            if hasattr(self, 'orb_check'):
                self.orb_check.setText('Disable floating bot')

    def _hide_companion_only(self):
        if hasattr(self, 'orb') and self.orb:
            self.orb.hide()
            if hasattr(self, 'orb_check'):
                self.orb_check.setText('Enable floating bot')
    def _make_bot(self, kind):                   # bots are created once (Ghost only when first chosen) and reused
        if kind in self.bots: return self.bots[kind]
        try:
            if kind == 'ghost':
                from ghost import GhostWidget
                b = GhostWidget(self.settings)
            elif kind == 'mummy':
                from mummy import MummyBot
                b = MummyBot()
            elif kind == 'pet':
                from pet_engine import PetWidget
                b = PetWidget(self.settings)
            elif kind == 'cloudy':
                from cloudy import CloudyWidget
                b = CloudyWidget(self.settings)
            else: b = DesktopBot(self.settings)
        except Exception as e: self._bot_error = f'{type(e).__name__}: {e}'; return None
        if hasattr(b, 'open_requested'): b.open_requested.connect(self.open_main_window)
        if hasattr(b, 'exit_requested'): b.exit_requested.connect(self.quit_app)
        if hasattr(b, 'hidden'): b.hidden.connect(lambda: self.orb_check.setText('Enable floating bot'))
        self.bots[kind] = b; return b
    def _bot_text(self):
        self.bot_btn.setText(f"Companion: {BOT_NAMES[self.bot_kind]}   (click to switch)")
    def choose_bot(self):
        m = QMenu(self); m.setStyleSheet(MENU_CSS)
        for kind, name in BOT_NAMES.items(): m.addAction(('●   ' if kind == self.bot_kind else '      ') + name).setData(kind)
        a = m.exec(self.bot_btn.mapToGlobal(QPoint(0, self.bot_btn.height())))
        if a is not None: self.set_bot(a.data())
    def set_bot(self, kind):
        if kind not in BOT_NAMES or kind == self.bot_kind: return
        old = self.orb
        centre = old.pos() + QPoint(old.width() // 2, old.height() // 2)
        new = self._make_bot(kind)
        if new is None:
            self.append_log(f'Could not load the {kind} bot: {self._bot_error}')
            return
        old.hide()
        self.orb, self.bot_kind = new, kind
        self.settings.setValue('bot_kind', kind)
        new.set_assistant_state(self.assistant_state)
        new.show_bot()
        if centre: new.move(centre - QPoint(new.width() // 2, new.height() // 2))
        if hasattr(new, 'ensure_on_screen'): new.ensure_on_screen()
        self.orb_check.setText('Disable floating bot')
        self._bot_text()

    def toggle_orb(self):
        on = not self.orb.isVisible()
        if hasattr(self, 'companion_bridge') and self.companion_bridge:
            self.companion_bridge.show() if on else self.companion_bridge.hide()
        else:
            self.orb.show_bot() if on else self.orb.hide()
        self.orb_check.setText('Disable floating bot' if on else 'Enable floating bot')

    # -- assistant backend: pc_assistant.py runs ONE Gemini Live session; voice and typed chat both talk to it
    MARK = '@@NOVA@@'
    def backend_running(self): return self.process.state() != QProcess.NotRunning
    def ensure_backend(self):
        """Start the single backend process if it is not running. Returns False if it cannot be started."""
        if self.backend_running(): return True
        if not (ROOT / '.env').exists():
            QMessageBox.information(self, 'API key needed', 'Create a .env file beside ui.py and add your key:\n\nGEMINI_API_KEY=your_key_here\n\nSee .env.example for the template.'); self.change_page(4); return False
        self._out_raw = self._err_raw = b''; self._stopping = self._fatal_shown = False; self.backend_state = 'connecting'
        self.process.setProgram(sys.executable); self.process.setArguments(['-u', str(ROOT / 'pc_assistant.py')]); self.process.start()
        self._refresh_status(); self.set_orb_state('thinking'); self.append_log('Starting assistant…')
        return True
    def send_cmd(self, **cmd):
        data = (json.dumps(cmd, ensure_ascii=False) + '\n').encode('utf-8')
        if self.process.state() == QProcess.Running: self.process.write(data)
        elif self.process.state() == QProcess.Starting: self._pending.append(data)        # sent as soon as the process is up
    def _backend_started(self):
        self.process.write((json.dumps({'cmd': 'voice_output', 'on': self.voice_output}) + '\n').encode('utf-8'))
        self.process.write((json.dumps({
            'cmd': 'mic_sensitivity',
            'sensitivity': self.mic_sensitivity,
            'auto_nearest': self.mic_auto_nearest,
            'calibrate_now': False,
        }) + '\n').encode('utf-8'))
        for data in self._pending: self.process.write(data)
        self._pending.clear()
    def shutdown_backend(self):
        """Ask the backend to close its session, mic and speaker; force-stop it only if it does not exit."""
        if self.process.state() == QProcess.NotRunning: return
        self._stopping = True
        if self.process.state() == QProcess.Starting and not self.process.waitForStarted(2000): self.process.kill(); self.process.waitForFinished(1500); return
        self.process.write(b'{"cmd": "shutdown"}\n'); self.process.closeWriteChannel()
        if not self.process.waitForFinished(3500): self.process.kill(); self.process.waitForFinished(1500)

    def toggle_voice(self):                       # Start/Stop VOICE only: the Live session and typed chat stay alive
        if self.mic_on or self.mic_starting:
            self.mic_starting = False; self.send_cmd(cmd='mic_stop')
            if self.wake_enabled and self.wake_listener:
                self.wake_listener.resume()
                self._update_wake_badge(paused=False)
            return
        if not self.ensure_backend(): return
        if self.wake_listener:
            self.wake_listener.pause()
            self._update_wake_badge(paused=True)
        self.mic_starting = True; self._voice_btn_text(); self.send_cmd(cmd='mic_start')
    def _voice_btn_text(self):
        txt = '■  Stop voice assistant' if (self.mic_on or self.mic_starting) else '▶  Start voice assistant'
        self.voice_btn.setText(txt)
        if hasattr(self, 'overview_voice_btn'): self.overview_voice_btn.setText(txt)
    def _auto_start_backend(self):
        env_file = ROOT / '.env'
        if env_file.exists():
            try:
                content = env_file.read_text(encoding='utf-8')
                for line in content.splitlines():
                    line = line.strip()
                    if line.startswith('GEMINI_API_KEY=') and len(line.split('=', 1)[1].strip()) > 5:
                        self.ensure_backend()
                        break
            except Exception: pass
    def _refresh_status(self):
        if not self.backend_running(): s, b = '●  Assistant stopped', 'Ready when you are'
        elif self.backend_state == 'reconnecting': s, b = '●  Reconnecting…', 'Reconnecting to Gemini Live…'
        elif self.backend_state != 'ready': s, b = '●  Connecting…', 'Connecting to Gemini Live…'
        elif self.mic_on: s, b = '●  Assistant running', 'Listening and ready'
        else: s, b = '●  Connected (voice off)', 'Connected — type a message or start voice'
        self.status_dot.setText(s); self.big_status.setText(b)
    def _read_stdout(self):
        self._out_raw += bytes(self.process.readAllStandardOutput().data())
        while b'\n' in self._out_raw:
            line, self._out_raw = self._out_raw.split(b'\n', 1); self._handle_line(line.decode('utf-8', 'replace').rstrip('\r'))
    def _read_stderr(self):
        self._err_raw += bytes(self.process.readAllStandardError().data())
        while b'\n' in self._err_raw:
            line, self._err_raw = self._err_raw.split(b'\n', 1); line = line.decode('utf-8', 'replace').rstrip('\r')
            if line.strip(): self._err_tail = (self._err_tail + [line])[-6:]; self.append_log(line)
    def _handle_line(self, line):
        if not line.strip(): return
        if line.startswith(self.MARK):
            try: ev = json.loads(line[len(self.MARK):])
            except ValueError: return self.append_log(line)
            if isinstance(ev, dict): return self._on_event(ev)
        self.append_log(line)
    def _on_event(self, ev):
        t, text = ev.get('t'), str(ev.get('text', ''))
        if t == 'user': self._chat_stream('voice', text)
        elif t == 'model': self._awaiting_reply = False; self._chat_stream('model', text)
        elif t == 'turn_end': self._awaiting_reply = False; self._chat_close('voice'); self._chat_close('model'); self._chat_dirty()
        elif t == 'state': self.set_orb_state(str(ev.get('v', 'idle')))
        elif t == 'status':
            self.backend_state = str(ev.get('v', '')); self._refresh_status()
            if self.backend_state == 'ready': self.append_log('Connected to Gemini Live.')
        elif t == 'mic':
            v = ev.get('v'); self.mic_on = v == 'on'; self.mic_starting = v == 'starting'
            if v == 'error': self._chat_error(text or 'The microphone could not be started.')
            self._voice_btn_text(); self._refresh_status()
            if not self.mic_on and not self.mic_starting:
                if self.wake_enabled and self.wake_listener:
                    self.wake_listener.resume()
                    self._update_wake_badge(paused=False)
        elif t == 'mic_level':
            rms = float(ev.get('rms', 0.0) or 0.0)
            gate = float(ev.get('gate', 120.0) or 120.0)
            act = bool(ev.get('active', False))
            sens = ev.get('sensitivity')
            auto_n = ev.get('auto_nearest')
            if sens is not None and auto_n is not None and (int(sens) != self.mic_sensitivity or bool(auto_n) != self.mic_auto_nearest):
                self.mic_sensitivity = max(0, min(100, int(sens)))
                self.mic_auto_nearest = bool(auto_n)
                self.settings.setValue('mic_sensitivity', self.mic_sensitivity)
                self.settings.setValue('mic_auto_nearest', 'true' if self.mic_auto_nearest else 'false')
                for w in self._mic_sens_widgets:
                    w.sync_state(self.mic_sensitivity, self.mic_auto_nearest)
            for w in self._mic_sens_widgets:
                w.push_audio_level(rms, gate, act)
        elif t == 'mic_sensitivity':
            sens = int(ev.get('sensitivity', self.mic_sensitivity))
            auto_n = bool(ev.get('auto_nearest', self.mic_auto_nearest))
            self.mic_sensitivity = max(0, min(100, sens))
            self.mic_auto_nearest = auto_n
            self.settings.setValue('mic_sensitivity', self.mic_sensitivity)
            self.settings.setValue('mic_auto_nearest', 'true' if self.mic_auto_nearest else 'false')
            for w in self._mic_sens_widgets:
                w.sync_state(self.mic_sensitivity, self.mic_auto_nearest)
        elif t == 'indicator':
            kind = ev.get('kind')
            active = bool(ev.get('active', False))
            if kind == 'screen': self.screen_badge.setVisible(active)
            elif kind == 'control': self.control_badge.setVisible(active)
            elif kind == 'gesture': self._update_gesture_ui(active=active)
        elif t == 'gesture':
            st = str(ev.get('status', 'stopped'))
            self._update_gesture_ui(status=st)
        elif t == 'ui_cmd':
            act = ev.get('action')
            if act in ('close', 'exit', 'quit'):
                QTimer.singleShot(100, self.quit_app)
            elif act == 'minimize':
                self.showMinimized()
            elif act == 'toggle_floating_bot':
                self.toggle_orb()
            elif act == 'show_floating_bot':
                if hasattr(self, 'orb') and self.orb and not self.orb.isVisible():
                    self.toggle_orb()
            elif act == 'hide_floating_bot':
                if hasattr(self, 'orb') and self.orb and self.orb.isVisible():
                    self.toggle_orb()
            elif act == 'switch_bot':
                target_bot = str(ev.get('bot', '')).strip()
                if target_bot in BOT_NAMES:
                    self.set_bot(target_bot)
        elif t == 'perm_sync':
            active = ev.get('active', {})
            for k, v in active.items():
                if v:
                    PermissionManager().grant_session(k)
                else:
                    PermissionManager().revoke(k)
            self._refresh_perms_ui()
        elif t == 'perm_notification':
            cat = ev.get('category', '')
            desc = ev.get('desc', '')
            self.append_log(f"[Voice Permission Granted] {cat}: {desc}")
            self._refresh_perms_ui()
        elif t == 'perm_voice_resolved':
            req_id = ev.get('id')
            decision = ev.get('decision', 'allow_session')
            cat = ev.get('category', '')
            if decision in ('session', 'allow_session') and cat:
                PermissionManager().grant_session(cat)
            dlg = self._active_perm_dialogs.get(req_id)
            if dlg is not None:
                dlg._choose(decision)
            self._refresh_perms_ui()
        elif t == 'perm_request':
            req_id = ev.get('id')
            cat = ev.get('category', '')
            details = ev.get('desc') or ev.get('details') or ''
            if isinstance(details, dict):
                details = ', '.join(f"{k}: {v}" for k, v in details.items())
            def _run_perm_dialog(r=req_id, c=cat, d=str(details)):
                decision = self._prompt_permission(r, c, d)
                self.send_cmd(cmd='perm_response', id=r, decision=decision)
            QTimer.singleShot(0, _run_perm_dialog)
        elif t == 'tool':
            self._chat_close('voice')
            t_name = ev.get('name')
            self.append_log(f"[tool] {t_name}: {ev.get('result')}")
            if t_name in ('task_control', 'memory_control') and hasattr(self, 'tasks_view'):
                self._refresh_tasks_and_memory()
            if t_name == 'workflow_control':
                self._ui_refresh_workflows()
        elif t == 'point_at_screen':
            q = str(ev.get('question', '') or 'What is this region on my screen and how do I use it?')
            QTimer.singleShot(0, lambda q=q: self.start_screen_pointing_ui(question=q))
        elif t == 'walkthrough_state':
            self._on_walkthrough_state_event(ev)
        elif t == 'workflow_state':
            self._on_workflow_state_event(ev)
        elif t == 'workflow_step_request':
            QTimer.singleShot(0, lambda e=dict(ev): self._on_workflow_step_request(e))
        elif t == 'workflow_replay_status':
            st = ev.get('status')
            wf_name = ev.get('name', 'Workflow')
            if st == 'started':
                self.append_log(f"[Workflow Replay] Started '{wf_name}' ({ev.get('total', 0)} steps).")
            elif st == 'finished':
                outcome = ev.get('outcome', {})
                self.append_log(f"[Workflow Replay] Finished '{wf_name}': {outcome}")
                self._ui_refresh_workflows()
        elif t == 'log': self.append_log(text)
        elif t == 'error': self.append_log(text); self._chat_error(text)
        elif t == 'fatal': self.append_log(text); self._fatal_shown = True; self._chat_error(text)
    def append_log(self, text): self.activity.append(text.rstrip()); self.log.append(text.rstrip())
    def _backend_error(self, err):
        if err == QProcess.FailedToStart:
            self._chat_error('The assistant process could not be started. Check that Python and the packages from requirements.txt are installed.')
            self.backend_finished(1)
    def backend_finished(self, code=0, *_):
        self._read_stdout(); self._read_stderr()
        if self._out_raw.strip(): self._handle_line(self._out_raw.decode('utf-8', 'replace').strip()); self._out_raw = b''
        crashed = not self._stopping and not self._fatal_shown and code not in (0, None)
        self.backend_state, self.mic_on, self.mic_starting, self._awaiting_reply = 'stopped', False, False, False; self._pending.clear()
        self._voice_btn_text(); self._refresh_status(); self.set_orb_state('idle'); self.append_log('Assistant stopped.')
        if crashed: self._chat_error(f'The assistant stopped unexpectedly (exit code {code}). Your next message will start it again. See Settings → logs for details.')
        else: self._chat_dirty()

    # -- shared transcript (voice transcripts, typed messages and replies all live here, so nothing is shown twice)
    def _chat_stream(self, role, text):
        self._chat_close('model' if role == 'voice' else 'voice')
        last = self.chat_entries[-1] if self.chat_entries else None
        if last and last['role'] == role and last['open']: last['text'] += text
        else: self.chat_entries.append({'role': role, 'text': text, 'open': True}); del self.chat_entries[:-300]
        self._chat_dirty()
    def _chat_close(self, role):
        if self.chat_entries and self.chat_entries[-1]['role'] == role: self.chat_entries[-1]['open'] = False
    def _chat_error(self, text):
        self._awaiting_reply = False; self._chat_close('model'); self._chat_close('voice')
        self.chat_entries.append({'role': 'error', 'text': text, 'open': False}); del self.chat_entries[:-300]; self._chat_dirty()
    def _chat_dirty(self):
        if not self._chat_timer.isActive(): self._chat_timer.start(40)
    def _render_chat(self):
        out = []
        for e in self.chat_entries:
            txt = html.escape(e['text'].strip()).replace('\n', '<br>')
            if not txt: continue
            r = e['role']
            if r == 'error': out.append(f'<span style="color:#ff9aa8">{txt}</span>')
            else: out.append({'you': '<b>You:</b> ', 'voice': '<b>You (voice):</b> ', 'model': f'<b>{APP_NAME}:</b> '}.get(r, '') + txt)
        if self._awaiting_reply: out.append(f'<i>{APP_NAME} is thinking…</i>')
        self.chatlog.setHtml(''.join(f'<p style="margin:0 0 8px 0">{o}</p>' for o in out))
        bar = self.chatlog.verticalScrollBar(); bar.setValue(bar.maximum())

    # -- typed chat: sent into the SAME live session the microphone uses
    def send_chat(self):
        msg = self.chat_input.text().strip()
        if not msg or not self.ensure_backend(): return
        self.chat_input.clear(); self._chat_close('voice'); self._chat_close('model')
        self.chat_entries.append({'role': 'you', 'text': msg, 'open': False}); del self.chat_entries[:-300]
        self._awaiting_reply = True; self._chat_dirty(); self.set_orb_state('thinking')
        self.send_cmd(cmd='text', text=msg); self.chat_input.setFocus()
    def _voice_out_text(self):
        txt = 'Spoken replies: On   (click to turn off)' if self.voice_output else 'Spoken replies: Off   (click to turn on)'
        if hasattr(self, 'voice_out_btn'): self.voice_out_btn.setText(txt)
        if hasattr(self, 'tray_voice_out_act'): self.tray_voice_out_act.setText('Spoken Replies: On' if self.voice_output else 'Spoken Replies: Off')
    def toggle_voice_output(self):
        self.voice_output = not self.voice_output; self.settings.setValue('voice_output', 'true' if self.voice_output else 'false'); self._voice_out_text()
        if self.backend_running(): self.send_cmd(cmd='voice_output', on=self.voice_output)

    def toggle_hand_gesture(self):
        if self.ensure_backend():
            self.send_cmd(cmd='gesture_control', action='toggle')

    def _update_gesture_ui(self, status=None, active=None):
        if status is not None:
            self.gesture_active = status in ('starting', 'running', 'paused')
            paused = (status == 'paused')
        elif active is not None:
            self.gesture_active = bool(active)
            paused = False
        else:
            paused = False

        if hasattr(self, 'gesture_badge') and self.gesture_badge is not None:
            if paused:
                self.gesture_badge.setText('🖐 Gesture Paused')
            else:
                self.gesture_badge.setText('🖐 Gesture Active')
            self.gesture_badge.setVisible(self.gesture_active)
        if hasattr(self, 'sidebar_gesture_btn'):
            self.sidebar_gesture_btn.setText('🖐  Hand Gesture: Active' if self.gesture_active else '🖐  Hand Gesture: Off')
        if hasattr(self, 'gesture_btn'):
            self.gesture_btn.setText(
                'Hand Gesture Mouse Control: Active   (click to stop)'
                if self.gesture_active
                else 'Hand Gesture Mouse Control: Off   (click to start)'
            )
        if hasattr(self, 'tray_gesture_act'):
            self.tray_gesture_act.setText('Hand Gesture Mouse: Active' if self.gesture_active else 'Hand Gesture Mouse: Off')

    # -- Feature 2: Screen Pointing (user-selected region only, permission-gated, guidance only)
    def _request_local_screen_permission(self, description: str) -> bool:
        pm = PermissionManager()
        if pm.is_granted('screen_view'):
            return True
        decision = self._prompt_permission('point_screen_req', 'screen_view', description)
        return decision in ('session', 'allow_session', 'once', 'allow_once')

    def _capture_selected_region_jpeg(self, rect):
        from screen_agent import capture_region_jpeg
        self.screen_badge.setVisible(True)
        try:
            return capture_region_jpeg(rect)
        finally:
            QTimer.singleShot(800, lambda: self.screen_badge.setVisible(False))

    def _analyze_selected_region(self, image_bytes: bytes, question: str, rect) -> str:
        from screen_agent import analyze_region_bytes
        self.set_orb_state('thinking')
        try:
            return analyze_region_bytes(image_bytes, question, rect)
        finally:
            self.set_orb_state('idle')

    def start_screen_pointing_ui(self, question: str = None):
        q = (question if isinstance(question, str) and question.strip() else None)
        if not q and hasattr(self, 'point_question_input'):
            q = self.point_question_input.text().strip()
        if not q:
            q = 'Explain what is in this selected screen region and identify key controls or messages.'

        def _on_point_result(answer: str):
            if hasattr(self, 'point_result_view'):
                self.point_result_view.setHtml(f"<b>Region Analysis:</b><br>{html.escape(answer).replace(chr(10), '<br>')}")
            self.append_log(f"[Point at Screen] {answer}")
            self.chat_entries.append({'role': 'model', 'text': f"[Point at Screen] {answer}", 'open': False})
            self._chat_dirty()

        self._active_region_selector = self.screen_pointing_service.select_and_explain(q, _on_point_result)

    # -- Feature 3: Guided Walkthroughs (manual Next / Back / Pause / Stop, guidance only)
    def _ui_start_walkthrough(self):
        goal = self.walkthrough_goal_input.text().strip() if hasattr(self, 'walkthrough_goal_input') else ''
        if not goal:
            goal, ok = QInputDialog.getText(self, 'Guided Walkthrough', 'What would you like a step-by-step walkthrough for?')
            if not ok or not goal.strip():
                return
            goal = goal.strip()
        if hasattr(self, 'walkthrough_goal_input'):
            self.walkthrough_goal_input.clear()
        if self.ensure_backend():
            self.send_cmd(cmd='walkthrough_cmd', action='start', goal=goal)

    def _ui_walkthrough_nav(self, action: str):
        if self.backend_running():
            self.send_cmd(cmd='walkthrough_cmd', action=action)
        elif action == 'stop' and hasattr(self, 'walkthrough_overlay'):
            self.walkthrough_overlay.hide_target()

    def _ui_walkthrough_toggle_pause(self):
        paused = getattr(self, '_walkthrough_paused', False)
        act = 'resume' if paused else 'pause'
        if self.backend_running():
            self.send_cmd(cmd='walkthrough_cmd', action=act)

    def _on_walkthrough_state_event(self, ev: dict):
        active = bool(ev.get('active', False))
        paused = bool(ev.get('paused', False))
        self._walkthrough_paused = paused
        if not active or not ev.get('step'):
            if hasattr(self, 'walkthrough_overlay'):
                self.walkthrough_overlay.hide_target()
            if hasattr(self, 'walkthrough_view'):
                self.walkthrough_view.setHtml('<i style="color:#9cb5c6">Walkthrough finished or stopped.</i>')
            if self.assistant_state == 'paused':
                self.set_orb_state('idle')
            return
        if paused:
            self.set_orb_state('paused')
        step = ev.get('step') or {}
        idx = ev.get('index', 1)
        total = ev.get('total', 1)
        title = html.escape(str(step.get('title', 'Step')))
        inst = html.escape(str(step.get('instruction', '')))
        target_lbl = step.get('target_label')
        target_rect = step.get('target_rect')
        caution = step.get('caution')
        pause_badge = ' <span style="color:#ffd060">[PAUSED]</span>' if paused else ''
        target_html = f'<br><span style="color:#5efaff">🎯 UI Target: {html.escape(str(target_lbl))}</span>' if target_lbl else ''
        caution_html = f'<br><span style="color:#ffb366">⚠ Caution: {html.escape(str(caution))}</span>' if caution else ''
        if hasattr(self, 'walkthrough_view'):
            self.walkthrough_view.setHtml(
                f"<b>Step {idx} of {total}: {title}</b>{pause_badge}<br>{inst}{target_html}{caution_html}"
                f"<br><small style='color:#9cb5c6'>Guidance only — perform the step yourself, then click Next.</small>"
            )
        if target_rect and not paused and hasattr(self, 'walkthrough_overlay'):
            self.walkthrough_overlay.show_target(target_rect, str(target_lbl or title))
        elif hasattr(self, 'walkthrough_overlay'):
            self.walkthrough_overlay.hide_target()

    # -- Feature 4: Reusable Semantic Workflows (Record / Review / Edit / Delete / Replay with per-step confirmation)
    def _ui_toggle_workflow_recording(self):
        if not self.ensure_backend():
            return
        if self._workflow_recording:
            name, ok = QInputDialog.getText(self, 'Save Recorded Workflow', 'Enter a name for this workflow:')
            if not ok:
                return
            self.send_cmd(cmd='workflow_cmd', action='stop_recording', name=name.strip() or 'Saved Workflow')
        else:
            self.send_cmd(cmd='workflow_cmd', action='start_recording')

    def _ui_refresh_workflows(self):
        if self.backend_running():
            self.send_cmd(cmd='workflow_cmd', action='list')
            return
        try:
            wf_dir = Path(os.environ.get('APPDATA') or Path.home()) / 'PC_Complete_Assistant' / 'workflows'
            store = WorkflowStore(wf_dir)
            items = []
            for wf in store.list_workflows():
                items.append({
                    'workflow_id': wf.workflow_id,
                    'name': wf.name,
                    'created_at': wf.created_at,
                    'steps': [{'action': s.action, 'arguments': s.arguments, 'description': s.description, 'created_at': s.created_at} for s in wf.steps],
                })
            self._on_workflow_state_event({'recording': False, 'recorded_steps': 0, 'workflows': items})
        except Exception:
            pass

    def _on_workflow_state_event(self, ev: dict):
        self._workflow_recording = bool(ev.get('recording', False))
        rec_steps = int(ev.get('recorded_steps', 0))
        self._saved_workflows_cache = list(ev.get('workflows') or [])
        if hasattr(self, 'wf_rec_btn'):
            self.wf_rec_btn.setText(f'■  Stop & Save ({rec_steps} steps)' if self._workflow_recording else '●  Start Recording')
        if not hasattr(self, 'workflow_view'):
            return
        lines = []
        if self._workflow_recording:
            lines.append(f"<p style='margin:0 0 6px 0;color:#5efaff'><b>● Recording active</b> ({rec_steps} approved semantic steps captured)</p>")
        if not self._saved_workflows_cache:
            lines.append('<i style="color:#9cb5c6">No saved workflows yet.</i>')
        else:
            for wf in self._saved_workflows_cache:
                wid = html.escape(str(wf.get('workflow_id', ''))[:8])
                wname = html.escape(str(wf.get('name', 'Untitled')))
                steps = wf.get('steps') or []
                step_names = ', '.join(html.escape(str(s.get('action', ''))) for s in steps[:5])
                lines.append(f"<p style='margin:0 0 6px 0'><b>[{wid}] {wname}</b> ({len(steps)} steps): <span style='color:#b8e2f8'>{step_names}</span></p>")
        self.workflow_view.setHtml(''.join(lines))

    def _pick_workflow_from_cache(self, title: str):
        if not self._saved_workflows_cache:
            QMessageBox.information(self, title, 'No saved workflows available.')
            return None
        choices = [f"{w.get('name', 'Untitled')} [{str(w.get('workflow_id', ''))[:8]}] ({len(w.get('steps') or [])} steps)" for w in self._saved_workflows_cache]
        choice, ok = QInputDialog.getItem(self, title, 'Select workflow:', choices, 0, False)
        if not ok or not choice:
            return None
        idx = choices.index(choice)
        return self._saved_workflows_cache[idx]

    def _ui_replay_workflow(self):
        wf = self._pick_workflow_from_cache('Replay Workflow')
        if not wf:
            return
        steps = wf.get('steps') or []
        step_lines = '\n'.join(f"  {i}. {s.get('action')} — {s.get('description', '')}" for i, s in enumerate(steps, 1))
        ans = QMessageBox.question(
            self,
            'Confirm Workflow Replay',
            f"Workflow: {wf.get('name')}\n\nSteps to replay:\n{step_lines or '  (no steps)'}\n\nEach step will also ask for your confirmation before executing. Start replay?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans == QMessageBox.Yes and self.ensure_backend():
            self.send_cmd(cmd='workflow_cmd', action='replay', workflow_id=wf.get('workflow_id'))

    def _ui_cancel_workflow_replay(self):
        if self.backend_running():
            self.send_cmd(cmd='workflow_cmd', action='cancel_replay')
            self.append_log('[Workflow] Requested replay cancellation.')

    def _ui_edit_workflow(self):
        wf = self._pick_workflow_from_cache('Review / Edit Workflow')
        if not wf:
            return
        dlg = WorkflowEditorDialog(wf, self)
        if dlg.exec() == QDialog.Accepted and self.ensure_backend():
            self.send_cmd(
                cmd='workflow_cmd',
                action='update_steps',
                workflow_id=wf.get('workflow_id'),
                name=dlg.workflow_name,
                steps=dlg.steps,
            )

    def _ui_delete_workflow(self):
        wf = self._pick_workflow_from_cache('Delete Workflow')
        if not wf:
            return
        ans = QMessageBox.question(
            self,
            'Delete Workflow',
            f"Delete saved workflow '{wf.get('name')}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans == QMessageBox.Yes and self.ensure_backend():
            self.send_cmd(cmd='workflow_cmd', action='delete', workflow_id=wf.get('workflow_id'))

    def _on_workflow_step_request(self, ev: dict):
        req_id = ev.get('id')
        step_idx = int(ev.get('step', 1))
        total = int(ev.get('total', 1))
        act = str(ev.get('action', ''))
        args = ev.get('arguments') or {}
        desc = str(ev.get('description', ''))
        dlg = WorkflowStepConfirmDialog(step_idx, total, act, args, desc, self)
        dlg.exec()
        self.send_cmd(
            cmd='workflow_step_response',
            id=req_id,
            approved=bool(dlg.approved),
            cancelled=bool(dlg.cancelled),
        )

    def quick_action(self, kind):
        try:
            import tools
            if kind == 'point_screen':
                self.start_screen_pointing_ui()
                return
            elif kind == 'gesture':
                self.toggle_hand_gesture()
                return
            elif kind == 'web':
                q, ok = QInputDialog.getText(self, 'Search the web', 'What should I search for?')
                if not ok or not q.strip(): return
                r = tools.search_web(q.strip())
            elif kind == 'downloads': r = tools.open_folder(str(Path.home() / 'Downloads'))
            elif kind == 'status': r = tools.system_status()
            else: r = tools.open_app({'calc': 'calculator', 'notepad': 'notepad', 'explorer': 'file explorer'}[kind])
            self.append_log(str(r))
        except Exception as e: self.append_log(f'Action failed: {e}')


def main():
    app = QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setQuitOnLastWindowClosed(False); app.setStyle('Fusion'); win = Nova()
    win.winId(); win.show(); win.apply_backdrop(); sys.exit(app.exec())   # native handle must exist before DWM calls
if __name__ == '__main__': main()
