"""Drag-and-drop input widget for workflow execution."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class DropZoneWidget(QWidget):
    """Accept local file and folder drops and emit resolved paths."""

    paths_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._enabled_hint = "拖拽文件或文件夹到这里"
        self._disabled_hint = "当前工作流不需要输入"

        self.setAcceptDrops(True)
        self.setObjectName("dropZoneWidget")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)

        self._title_label = QLabel("拖拽输入区")
        self._title_label.setAlignment(Qt.AlignCenter)
        self._hint_label = QLabel(self._enabled_hint)
        self._hint_label.setWordWrap(True)
        self._hint_label.setAlignment(Qt.AlignCenter)

        layout.addStretch(1)
        layout.addWidget(self._title_label)
        layout.addWidget(self._hint_label)
        layout.addStretch(1)

        self.setMinimumHeight(150)
        self.setStyleSheet(
            """
            QWidget#dropZoneWidget {
                border: 3px dashed #aab2bd;
                border-radius: 12px;
                background: #fafbfc;
            }
            QWidget#dropZoneWidget[dropActive="true"] {
                border-color: #2d8cf0;
                background: #e8f4fd;
            }
            """
        )

    def set_drop_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)
        self._hint_label.setText(self._enabled_hint if enabled else self._disabled_hint)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.isEnabled() and self._has_local_urls(event):
            self.setProperty("dropActive", True)
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._set_inactive()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if not self.isEnabled() or not self._has_local_urls(event):
            event.ignore()
            return

        paths: list[str] = []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = str(Path(url.toLocalFile()).resolve())
            if path not in paths:
                paths.append(path)

        self._set_inactive()
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    @staticmethod
    def _has_local_urls(event: QDragEnterEvent | QDropEvent) -> bool:
        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            return False
        return any(url.isLocalFile() for url in mime_data.urls())

    def _set_inactive(self) -> None:
        self.setProperty("dropActive", False)
        self.style().unpolish(self)
        self.style().polish(self)