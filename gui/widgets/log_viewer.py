"""Log viewer widget for displaying workflow execution logs.

Extracted from main_window.py to provide a reusable log display component
with support for different log levels and timestamps.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QTextEdit, QVBoxLayout, QWidget

BATCH_INTERVAL_MS = 80
MAX_VISIBLE_BLOCKS = 5000


class LogViewer(QWidget):
    """Display workflow execution logs with batched rendering."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending: list[str] = []
        self._batch_timer = QTimer(singleShot=True, interval=BATCH_INTERVAL_MS)
        self._batch_timer.timeout.connect(self._flush_pending)
        self._block_count = 0
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("执行日志会显示在这里。")
        layout.addWidget(self.log_output, stretch=1)

    def append_message(self, message: str) -> None:
        """Append a log message with timestamp and styling."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        escaped = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        if message.startswith("[ERROR]"):
            accent = "#e74c3c"
            extra_style = "font-weight:bold;"
        elif message.startswith("[WARN]"):
            accent = "#e67e22"
            extra_style = "font-weight:bold;"
        elif message.startswith("[OK]"):
            accent = "#27ae60"
            extra_style = ""
        elif message.startswith("[HINT]"):
            accent = "#95a5a6"
            extra_style = "font-style:italic;"
        elif message.startswith("[INFO]"):
            accent = "#7f8c8d"
            extra_style = ""
        else:
            accent = "#2c3e50"
            extra_style = ""

        html = (
            f'<div style="margin:0;padding:1px 6px;white-space:pre-wrap;'
            f'border-left:3px solid {accent};">'
            f'<span style="color:#95a5a6;font-size:9pt;">[{timestamp}]</span> '
            f'<span style="color:{accent};{extra_style}">{escaped}</span>'
            f"</div>"
        )
        self._pending.append(html)
        if not self._batch_timer.isActive():
            self._batch_timer.start()

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        combined = "".join(self._pending)
        self._pending.clear()
        self._block_count += 1
        self.log_output.append(combined)
        self._trim_if_needed()
        self.log_output.verticalScrollBar().setValue(
            self.log_output.verticalScrollBar().maximum()
        )

    def _trim_if_needed(self) -> None:
        if self._block_count <= MAX_VISIBLE_BLOCKS:
            return
        doc = self.log_output.document()
        cursor = doc.find('<div style="margin:0;padding:1px 6px;white-space:pre-wrap;')
        if not cursor.isNull():
            cursor.movePosition(
                cursor.MoveOperation.End, cursor.KeepAnchor
            )
            cursor.removeSelectedText()
            self._block_count -= 1