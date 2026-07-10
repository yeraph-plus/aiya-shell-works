"""Log viewer widget for displaying workflow execution logs.

Extracted from main_window.py to provide a reusable log display component
with support for different log levels and timestamps.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QTextEdit, QVBoxLayout, QWidget


class LogViewer(QWidget):
    """Display workflow execution logs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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
        self.log_output.append(html)
        self.log_output.verticalScrollBar().setValue(
            self.log_output.verticalScrollBar().maximum()
        )