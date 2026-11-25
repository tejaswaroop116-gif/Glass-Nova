"""ui_theme.py - Glass Nova stylesheet, custom glass/orb widgets, Windows backdrop, tray icon, and startup helpers."""
import math
import sys
import winreg
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor as C, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from bot import cl, lgrad, rgrad, st

ROOT = Path(__file__).resolve().parent
APP_NAME = "Nova"
BOT_NAMES = {
    "mechanical": "Original NOVA Robot",
    "ghost": "Ghost",
    "pet": "NOVA Painter Pet",
    "cloudy": "Cloudy",
}

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

MENU_CSS = (
    "QMenu{background:#0c2038;color:#eaf8ff;border:1px solid #3fb8f0;padding:6px;font-family:'Segoe UI';font-size:10pt}"
    "QMenu::item{padding:7px 22px;border-radius:6px} QMenu::item:selected{background:#1f6f9f}"
    "QMenu::separator{height:1px;background:#2c5878;margin:5px 8px}"
)


class GlassRoot(QWidget):
    """Controlled dark-glass root backdrop with proper surface opacity isolation so desktop windows never bleed into UI text."""
    inset, radius, tint = 14, 26, 224

    def set_tint(self, v):
        self.tint = cl(int(v), 145, 252)
        self.update()

    def set_flat(self, flat, radius=0):
        self.inset, self.radius = (0, radius) if flat else (14, 26)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        i, r, t = self.inset, self.radius, self.tint
        b = QRectF(self.rect()).adjusted(i, i, -i, -i)
        for k in range(i, 0, -1):
            st(p, None, (57, 198, 255, int(38 * (1 - k / (i + 1)) ** 2)), 1.6)
            p.drawRoundedRect(b.adjusted(-k, -k, k, k), r + k, r + k)
        top_a = cl(int(t * 0.94), 135, 248)
        mid_a = cl(int(t * 0.98), 150, 252)
        bot_a = cl(int(t * 1.03), 165, 255)
        st(p, lgrad(b.left(), b.top(), b.right(), b.bottom(),
                    (0, (14, 42, 74, top_a)),
                    (.5, (8, 26, 50, mid_a)),
                    (1, (4, 15, 32, bot_a))))
        p.drawRoundedRect(b, r, r)
        st(p, None, (140, 230, 255, 185), 1.3)
        p.drawRoundedRect(b.adjusted(.6, .6, -.6, -.6), r, r)


class NavPill(QWidget):
    """Liquid-glass highlight that glides behind the active sidebar button."""
    boost = 0.0

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_boost(self, v):
        self.boost = float(v)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        b = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        k = self.boost
        w = b.width()
        body = QPainterPath()
        body.addRoundedRect(b, 13, 13)
        p.setClipPath(body)
        st(p, lgrad(0, b.top(), 0, b.bottom(),
                    (0, (255, 255, 255, int(64 + 40 * k))),
                    (.55, (140, 215, 255, int(30 + 20 * k))),
                    (1, (255, 255, 255, int(20 + 16 * k)))))
        p.drawPath(body)
        band = QPainterPath(QPointF(b.left() + w * .50, b.top()))
        band.lineTo(b.left() + w * .66, b.top())
        band.lineTo(b.left() + w * .40, b.bottom())
        band.lineTo(b.left() + w * .24, b.bottom())
        band.closeSubpath()
        st(p, (255, 255, 255, int(24 + 30 * k)))
        p.drawPath(band)
        st(p, lgrad(0, b.top(), 0, b.top() + b.height() * .55,
                    (0, (255, 255, 255, int(95 + 50 * k))),
                    (1, (255, 255, 255, 0))))
        p.drawRoundedRect(QRectF(b.left() + 2, b.top() + 1, w - 4, b.height() * .55), 11, 11)
        p.setClipping(False)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(lgrad(b.left(), b.top(), b.right(), b.bottom(),
                                   (0, (255, 255, 255, 235)),
                                   (.45, (160, 225, 255, 90)),
                                   (1, (255, 255, 255, 175)))), 1.4))
        p.drawRoundedRect(b, 13, 13)


ORB = {
    "idle": ((35, 150, 255), (150, 235, 255), .8, .025, 1.3),
    "listening": ((40, 215, 255), (170, 245, 255), 1.1, .045, 2.2),
    "thinking": ((95, 125, 255), (175, 190, 255), 1.7, .02, 3.0),
    "speaking": ((60, 235, 230), (190, 255, 245), 1.4, .06, 3.4),
    "waiting_approval": ((245, 175, 45), (255, 225, 130), 1.2, .035, 2.0),
    "executing": ((45, 225, 145), (165, 255, 210), 1.6, .04, 2.8),
    "paused": ((110, 140, 175), (180, 205, 230), .4, .01, .8),
    "error": ((235, 65, 75), (255, 155, 165), 1.3, .03, 2.2),
}


class OrbWidget(QWidget):
    """Luminous orb with per-state rings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.state, self.t = "idle", 0.0
        self._timer = QTimer(self, interval=33, timeout=self._tick)

    def set_state(self, s):
        self.state = s if s in ORB else self.state
        self.update()

    def showEvent(self, e):
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def _tick(self):
        self.t += .033 * ORB[self.state][2]
        self.update()

    def paintEvent(self, _):
        base, rim, _s, amp, rate = ORB[self.state]
        R = min(self.width(), self.height()) / 2
        if R < 10:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.t
        x, y = self.width() / 2, self.height() / 2
        c = QPointF(x, y)
        r = R * .5 * (1 + amp * math.sin(t * rate * 2))
        mid = tuple((a + b) // 2 for a, b in zip(base, (20, 70, 200)))
        st(p, rgrad(x, y, R, (0, (*base, 120)), (.5, (*base, 80)), (.68, (*base, 34)), (1, (*base, 0))))
        p.drawEllipse(c, R, R)
        st(p, rgrad(x, y, r, (0, (225, 255, 255)), (.2, (95, 225, 255)), (.5, mid),
                    (.82, (18, 52, 150)), (1, (8, 24, 88)), fx=x - .32 * r, fy=y - .38 * r))
        p.drawEllipse(c, r, r)
        sp = QPainterPath()
        sp.addEllipse(c, r, r)
        p.save()
        p.setClipPath(sp)
        for k in range(3):
            d = r * (.42 + .17 * k)
            st(p, None, (210, 248, 255, 60 + 25 * k), max(1, r * .045))
            p.drawArc(QRectF(x - d, y - d, 2 * d, 2 * d),
                      int((t * (26 + 14 * k) * (-1 if k % 2 else 1) + k * 70) % 360 * 16),
                      (55 + 20 * k) * 16)
        p.restore()
        st(p, rgrad(x, y, r * 1.04, (.84, (*rim, 0)), (.93, (*rim, 140)),
                    (.955, (235, 255, 255, 240)), (.98, (*rim, 90)), (1, (*rim, 0))))
        p.drawEllipse(c, r * 1.04, r * 1.04)
        p.save()
        p.translate(x - .3 * r, y - .48 * r)
        p.rotate(-28)
        st(p, lgrad(0, -.2 * r, 0, .2 * r, (0, (255, 255, 255, 215)), (1, (255, 255, 255, 0))))
        p.drawEllipse(QPointF(0, 0), .36 * r, .19 * r)
        p.restore()
        if self.state == "listening":
            for k in range(3):
                ph = (t * .35 + k / 3) % 1
                d = r * (1.06 + .9 * ph)
                st(p, None, (*rim, int(150 * (1 - ph) ** 1.5)), 1.6)
                p.drawEllipse(c, d, d)
        elif self.state == "speaking":
            st(p, None, (*rim, 190), 2)
            p.drawPolyline([
                QPointF(
                    x + r * (1.22 + .07 * math.sin(5 * a + t * 5) + .04 * math.sin(9 * a - t * 7)) * math.cos(a),
                    y + r * (1.22 + .07 * math.sin(5 * a + t * 5) + .04 * math.sin(9 * a - t * 7)) * math.sin(a),
                )
                for a in (i * math.pi / 45 for i in range(91))
            ])
        elif self.state == "thinking":
            d = r * 1.2
            for j in range(3):
                st(p, None, (*rim, 200 - j * 55), 2.4)
                p.drawArc(QRectF(x - d, y - d, 2 * d, 2 * d), int((-t * 140 + j * 120) % 360 * 16), 50 * 16)


def apply_backdrop(win, frost):
    """Frosted: strong acrylic blur (Windows 10/11) clipped to the rounded glass body. Clear: no native effect. Returns a message or ''."""
    if sys.platform != "win32":
        return "Frosted blur is only available on Windows; showing clear glass." if frost else ""
    try:
        import ctypes
        from ctypes import wintypes as wt

        class ACCENT(ctypes.Structure):
            _fields_ = [("state", ctypes.c_int), ("flags", ctypes.c_int), ("color", ctypes.c_uint), ("anim", ctypes.c_int)]

        class WCA(ctypes.Structure):
            _fields_ = [("attr", ctypes.c_int), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]

        u32, gdi = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
        u32.SetWindowCompositionAttribute.argtypes = [wt.HWND, ctypes.POINTER(WCA)]
        u32.SetWindowCompositionAttribute.restype = wt.BOOL
        u32.SetWindowRgn.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
        gdi.CreateRoundRectRgn.restype = ctypes.c_void_p
        gdi.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        hwnd, rgn = wt.HWND(int(win.winId())), None
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
        for attr, val in ((33, 2 if frost and not win.isMaximized() else 1), (34, 0xFFFFFFFE)):
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ctypes.c_uint(val)), 4)
        if frost:
            d, m = win.devicePixelRatioF(), win.isMaximized()
            i = 0 if m else round(win.root.inset * d)
            rad = 0 if m else round(win.root.radius * 2 * d)
            rgn = gdi.CreateRoundRectRgn(i, i, int(win.width() * d) - i + 1, int(win.height() * d) - i + 1, rad, rad)
        u32.SetWindowRgn(hwnd, rgn, True)
        acc = ACCENT(4 if frost else 0, 2 if frost else 0, 0x99502810, 0)
        data = WCA(19, ctypes.cast(ctypes.pointer(acc), ctypes.c_void_p), ctypes.sizeof(acc))
        if not u32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)):
            return "Frosted blur is not supported by this Windows version; showing clear glass." if frost else ""
        if frost:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                    on = bool(winreg.QueryValueEx(k, "EnableTransparency")[0])
            except OSError:
                on = True
            if not on:
                return 'Windows "Transparency effects" is off (Settings > Personalization > Colors), so the blur will not show. Clear glass still works.'
    except Exception as e:
        return f"Native backdrop setup failed ({e}); showing clear glass." if frost else ""
    return ""


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
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
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
