"""Explicit screen-region selection and analysis for Nova (PySide6).

The screen is captured only after the supplied permission callback returns True.
The analyzer receives the selected JPEG bytes and region rectangle. This module
does not click or type; pointing is guidance only.
"""
from __future__ import annotations

from typing import Callable, Optional
from PySide6.QtCore import Qt, QRect, QPoint, Signal
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget, QRubberBand, QApplication


class RegionSelector(QWidget):
    region_selected = Signal(QRect)
    cancelled = Signal()

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self._origin: Optional[QPoint] = None
        self._band = QRubberBand(QRubberBand.Rectangle, self)
        screens = QApplication.screens()
        geometry = screens[0].geometry()
        for screen in screens[1:]:
            geometry = geometry.united(screen.geometry())
        self.setGeometry(geometry)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 10, 25, 105))
        painter.setPen(QPen(QColor(90, 220, 255, 230), 1, Qt.DashLine))
        painter.drawText(24, 36, "Drag to select a screen region • Esc to cancel")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._origin = event.position().toPoint()
            self._band.setGeometry(QRect(self._origin, self._origin))
            self._band.show()

    def mouseMoveEvent(self, event):
        if self._origin is not None:
            self._band.setGeometry(QRect(self._origin, event.position().toPoint()).normalized())

    def mouseReleaseEvent(self, event):
        if self._origin is None:
            return
        rect = QRect(self._origin, event.position().toPoint()).normalized()
        self._band.hide()
        self.close()
        if rect.width() > 8 and rect.height() > 8:
            self.region_selected.emit(rect.translated(self.geometry().topLeft()))
        else:
            self.cancelled.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._band.hide()
            self.close()
            self.cancelled.emit()


class ScreenPointingService:
    """Adapter for existing Nova permission, capture, and vision functions."""

    def __init__(
        self,
        request_screen_permission: Callable[[str], bool],
        capture_region_jpeg: Callable[[QRect], bytes],
        analyze_region: Callable[[bytes, str, QRect], str],
    ):
        self.request_screen_permission = request_screen_permission
        self.capture_region_jpeg = capture_region_jpeg
        self.analyze_region = analyze_region

    def select_and_explain(self, question: str, on_result: Callable[[str], None]) -> RegionSelector:
        if not self.request_screen_permission("Select and analyze a screen region"):
            on_result("Screen access was not approved.")
            return None

        selector = RegionSelector()

        def selected(rect: QRect):
            try:
                image_bytes = self.capture_region_jpeg(rect)
                answer = self.analyze_region(image_bytes, question, rect)
                on_result(answer)
            except Exception as exc:
                on_result(f"Could not analyze the selected region: {exc}")

        selector.region_selected.connect(selected)
        selector.showFullScreen()
        selector.raise_()
        selector.activateWindow()
        return selector
