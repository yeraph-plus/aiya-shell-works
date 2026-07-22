"""Read-only terminal window with explicit stop and close actions."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class TerminalWindow(QDialog):
    """Non-modal dialog showing real-time PTY output."""

    output_received = Signal(str)
    session_finished = Signal(int)

    def __init__(
        self,
        session_id: str,
        command: str,
        *,
        stop_callback: Callable[[str], bool] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_id = session_id
        self._stop_callback = stop_callback
        self.setWindowTitle(f"终端 — {command}")
        self.resize(680, 440)
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
        self._output.setStyleSheet("QPlainTextEdit { background-color: #1e1e1e; color: #d4d4d4; }")
        layout.addWidget(self._output, stretch=1)

        controls = QHBoxLayout()
        self._status = QLabel("运行中")
        controls.addWidget(self._status)
        controls.addStretch(1)
        self._stop_button = QPushButton("停止")
        self._stop_button.clicked.connect(self._stop_session)
        controls.addWidget(self._stop_button)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        controls.addWidget(close_button)
        layout.addLayout(controls)

    def _stop_session(self) -> None:
        if self._stop_callback is None or not self._stop_callback(self._session_id):
            self._status.setText("无法停止：会话已结束")
            self._stop_button.setEnabled(False)
            return
        self._status.setText("正在停止")
        self._stop_button.setEnabled(False)

    def append_output(self, text: str) -> None:
        """Thread-safe: emit signal so text lands on the GUI thread."""
        self.output_received.emit(text)

    def notify_finished(self, exit_code: int) -> None:
        """Thread-safe: emit signal so GUI thread handles completion."""
        self.session_finished.emit(exit_code)

    def _append_output(self, text: str) -> None:
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()

    def _on_finished(self, exit_code: int) -> None:
        self._append_output(f"\n进程结束，退出码: {exit_code}\n")
        self._status.setText(f"已结束（{exit_code}）")
        self._stop_button.setEnabled(False)
