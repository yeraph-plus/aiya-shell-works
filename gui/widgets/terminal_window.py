"""Read-only terminal window for PTY-backed subprocess output.

The session is fetched from the active ``PipelineRuntime`` rather than from
a module-level singleton (the latter breaks under multiprocessing).  The
main window passes the runtime reference when it constructs the dialog.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QPlainTextEdit, QVBoxLayout, QWidget,
)

if TYPE_CHECKING:
    from core import PipelineRuntime


class TerminalWindow(QDialog):
    """Non-modal dialog showing real-time PTY output."""

    output_received = Signal(str)
    session_finished = Signal(object)

    def __init__(
        self,
        session_id: str,
        command: str,
        runtime: "PipelineRuntime",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._runtime = runtime
        self.setWindowTitle(f"终端 — {command}")
        self._dismissed = False
        self.resize(680, 440)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._build_ui()
        self.output_received.connect(self._append_output)
        self.session_finished.connect(self._on_finished)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._output.setFont(font)
        self._output.setStyleSheet(
            "QPlainTextEdit { background-color: #1e1e1e; color: #d4d4d4; }"
        )
        layout.addWidget(self._output, stretch=1)

    def closeEvent(self, event) -> None:
        self._dismissed = True
        session = self._runtime.sessions.get(self._session_id)
        if session is not None and session.exit_code is None:
            session.terminate()
        super().closeEvent(event)

    def append_output(self, text: str) -> None:
        """Thread-safe: emit signal so text lands on the GUI thread."""

        self.output_received.emit(text)

    def notify_finished(self, exit_code: int) -> None:
        self.session_finished.emit(exit_code)

    def _append_output(self, text: str) -> None:
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()

    def _on_finished(self, exit_code: int) -> None:
        if self._dismissed:
            return
        self._append_output(f"\n进程结束，退出码: {exit_code}\n")