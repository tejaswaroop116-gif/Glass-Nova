"""ui_dialogs.py - Modularized HUD widgets, overlays, and dialogs for Glass Nova UI."""
import html
import json
import math
import random
import time

from PySide6.QtCore import Qt, QPointF, QRectF, QTimer, Signal
from PySide6.QtGui import QColor as C, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from bot import cl, lgrad, st
from permissions import CATEGORIES


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
        norm = min(0.95, max(0.04, (self._last_rms / 680.0) ** 0.72))
        self._samples.append(norm)
        if len(self._samples) > 64:
            del self._samples[:-64]
        self.update()

    def _tick(self):
        self._phase += 0.22
        now = time.monotonic()
        if (now - self._last_push_ts) > 0.14:
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

        st(p, lgrad(r.left(), r.top(), r.left(), r.bottom(), (0, (6, 32, 42, 195)), (1, (3, 18, 26, 225))), (55, 225, 245, 135), 1.1)
        p.drawRoundedRect(r, 4, 4)

        clip = QPainterPath()
        clip.addRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
        p.save()
        p.setClipPath(clip)

        n = len(self._samples)
        if n >= 2:
            pts = []
            step_x = w / float(n - 1)
            for i, val in enumerate(self._samples):
                px = r.left() + i * step_x
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

            line_path = QPainterPath(pts[0])
            for pt in pts[1:]:
                line_path.lineTo(pt)
            st(p, None, (64, 248, 255, 90), 3.4)
            p.drawPath(line_path)
            st(p, None, (135, 252, 255, 245), 1.6)
            p.drawPath(line_path)

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

        hdr = QLabel("MIC SENSITIVITY")
        hdr.setStyleSheet("font-size: 10.5pt; font-weight: 700; letter-spacing: 2px; color: #b8f8ff;")
        lay.addWidget(hdr)

        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet("background: rgba(64, 232, 255, 130); border: none;")
        lay.addWidget(div)

        self.waveform = MicWaveformCanvas(self)
        self.waveform.set_sensitivity(self.sensitivity, self.auto_nearest)
        lay.addWidget(self.waveform)

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

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setObjectName("micSensSlider")
        self.slider.setRange(0, 100)
        self.slider.setValue(self.sensitivity)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.valueChanged.connect(self._on_slider_changed)
        lay.addWidget(self.slider)

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

        header = QLabel("🔒  Security & Permission Request")
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
    """Non-interactive translucent screen overlay that highlights a UIAutomation target rectangle during a walkthrough."""
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
