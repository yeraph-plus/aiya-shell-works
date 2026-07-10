"""Combined input panel with layered input area and action bar.

Supports file/folder path input with dashed border, line text input with
solid border, and none input mode. The input area and action bar are
clearly separated in a two-layer vertical layout.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import InputInspector


_DASHED_BORDER_STYLE = (
    "QFrame#inputDashedFrame {"
    "  border: 2px dashed #aab2bd;"
    "  border-radius: 10px;"
    "  background: #fafbfc;"
    "}"
)

_SOLID_BORDER_STYLE = (
    "QFrame#inputSolidFrame {"
    "  border: 1px solid #aab2bd;"
    "  border-radius: 8px;"
    "  background: #fafbfc;"
    "}"
)


class _DropFrame(QFrame):
    """QFrame hosting the input list with drag-and-drop support."""

    paths_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("inputDashedFrame")

        self._list = QListWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._list)

        self._overlay = QLabel("拖拽文件或文件夹到这里", self)
        self._overlay.setAlignment(Qt.AlignCenter)
        self._overlay.setStyleSheet(
            "QLabel { background: rgba(45,140,240,120); color: #ffffff;"
            " font-size: 16pt; font-weight: bold; border-radius: 8px; padding: 12px; }"
        )
        self._overlay.hide()

        self.setStyleSheet(_DASHED_BORDER_STYLE)

    @property
    def list_widget(self) -> QListWidget:
        return self._list

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())

    def set_drop_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.isEnabled() and self._has_local_urls(event):
            self._overlay.show()
            self._overlay.raise_()
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._overlay.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._overlay.hide()
        if not self.isEnabled() or not self._has_local_urls(event):
            event.ignore()
            return
        paths: list[str] = []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            p = str(Path(url.toLocalFile()).resolve())
            if p not in paths:
                paths.append(p)
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
            return
        event.ignore()

    @staticmethod
    def _has_local_urls(event: QDragEnterEvent | QDropEvent) -> bool:
        mime = event.mimeData()
        return mime.hasUrls() and any(url.isLocalFile() for url in mime.urls())


class InputPanel(QWidget):
    """Input panel with layered input area and action bar."""

    paths_changed = Signal()
    status_message = Signal(str)
    warning = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._atom = "none"
        self._recurse = False

        self._build_ui()
        self._bind_signals()
        self.set_atom("none", False)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        input_area = QWidget()
        input_layout = QVBoxLayout(input_area)
        input_layout.setContentsMargins(0, 0, 0, 0)

        self._drop_frame = _DropFrame()
        input_layout.addWidget(self._drop_frame, stretch=1)

        self.input_list = self._drop_frame.list_widget

        self._text_frame = QFrame()
        self._text_frame.setObjectName("inputSolidFrame")
        self._text_frame.setStyleSheet(_SOLID_BORDER_STYLE)
        text_inner = QVBoxLayout(self._text_frame)
        text_inner.setContentsMargins(8, 8, 8, 8)
        self.text_editor = QPlainTextEdit()
        self.text_editor.setPlaceholderText("每行一个任务，空行自动忽略。")
        text_inner.addWidget(self.text_editor)
        self._text_frame.hide()
        input_layout.addWidget(self._text_frame, stretch=1)

        self._none_frame = QFrame()
        self._none_frame.setObjectName("inputSolidFrame")
        self._none_frame.setStyleSheet(_SOLID_BORDER_STYLE)
        none_inner = QVBoxLayout(self._none_frame)
        none_inner.setContentsMargins(8, 8, 8, 8)
        self.none_label = QLabel("无需输入，从空白直接产出文件。")
        self.none_label.setAlignment(Qt.AlignCenter)
        self.none_label.setStyleSheet("color: #95a5a6; font-size: 10pt;")
        none_inner.addWidget(self.none_label)
        self._none_frame.hide()
        input_layout.addWidget(self._none_frame, stretch=1)

        layout.addWidget(input_area, stretch=1)

        action_bar = QFrame()
        action_bar.setStyleSheet("QFrame { background: #f5f5f5; border-radius: 6px; }")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(8, 6, 8, 6)

        self.add_files_button = QPushButton("添加文件")
        self.add_folder_button = QPushButton("添加文件夹")
        self.remove_button = QPushButton("删除选中")
        self.clear_button = QPushButton("清空")

        action_layout.addWidget(self.add_files_button)
        action_layout.addWidget(self.add_folder_button)
        action_layout.addStretch(1)
        action_layout.addWidget(self.remove_button)
        action_layout.addWidget(self.clear_button)

        layout.addWidget(action_bar)

    def _bind_signals(self) -> None:
        self.add_files_button.clicked.connect(self._choose_files)
        self.add_folder_button.clicked.connect(self._choose_folder)
        self.remove_button.clicked.connect(self._remove_selected)
        self.clear_button.clicked.connect(self.clear)
        self._drop_frame.paths_dropped.connect(self.add_paths)
        self.input_list.model().rowsInserted.connect(self._emit_changed)
        self.input_list.model().rowsRemoved.connect(self._emit_changed)
        self.text_editor.textChanged.connect(self._emit_changed)

    def _emit_changed(self, *_args: object) -> None:
        self.paths_changed.emit()

    _VALID_ATOMS = {"file", "folder", "line", "none"}

    def set_atom(self, atom: str, recurse: bool) -> None:
        """Set the input atom type and configure the UI accordingly."""
        if atom not in self._VALID_ATOMS:
            atom = "none"
        self._atom = atom
        self._recurse = recurse

        wants_path = atom in {"file", "folder"}
        wants_text = atom == "line"

        self._drop_frame.setVisible(wants_path)
        self.input_list.setVisible(wants_path)
        self.input_list.setEnabled(wants_path)
        self._text_frame.setVisible(wants_text)
        self.text_editor.setVisible(wants_text)
        self.text_editor.setEnabled(wants_text)
        self._none_frame.setVisible(atom == "none")

        is_file_recurse = atom == "file" and recurse
        self.add_files_button.setVisible(wants_path and is_file_recurse)
        self.add_folder_button.setVisible(wants_path)
        self.remove_button.setVisible(wants_path)
        self.clear_button.setVisible(True)

        self.add_files_button.setEnabled(wants_path and is_file_recurse)
        self.add_folder_button.setEnabled(wants_path)
        self.remove_button.setEnabled(wants_path)
        self.clear_button.setEnabled(atom != "none")

        if atom == "none":
            self.clear()

    def set_running(self, running: bool) -> None:
        """Enable/disable controls based on execution state."""
        atom = self._atom
        is_file_recurse = atom == "file" and self._recurse
        is_path_input = atom in {"file", "folder"}

        self.add_files_button.setEnabled(not running and is_file_recurse)
        self.add_folder_button.setEnabled(not running and is_path_input)
        self.remove_button.setEnabled(not running and is_path_input)
        self.clear_button.setEnabled(not running and atom != "none")
        self._drop_frame.set_drop_enabled(not running and is_path_input)
        self.input_list.setEnabled(not running and is_path_input)
        self.text_editor.setEnabled(not running and atom == "line")

    def has_input(self) -> bool:
        """Check if there is valid input."""
        atom = self._atom
        if atom in {"file", "folder"}:
            return self.input_list.count() > 0
        if atom == "line":
            return bool(self.text_editor.toPlainText().strip())
        return True

    def get_files(self) -> list[str]:
        """Get the list of input file paths."""
        return [self.input_list.item(i).data(Qt.UserRole) for i in range(self.input_list.count())]

    def get_lines(self) -> str:
        """Get the text input lines."""
        return self.text_editor.toPlainText()

    def clear(self) -> None:
        """Clear all input."""
        self.input_list.clear()
        self.text_editor.clear()
        self.paths_changed.emit()

    def set_unit_status(self, row: int, status: str) -> None:
        """Update the status badge for a specific input unit."""
        if row < 0 or row >= self.input_list.count():
            return
        item = self.input_list.item(row)
        if item is None:
            return
        path = item.data(Qt.UserRole)
        badge = {
            "processing": "[处理中]",
            "completed": "[完成]",
            "failed": "[失败]",
            "cancelled": "[取消]",
        }.get(status, "[等待]")
        item.setText(f"{badge} {path}")

    def reset_unit_badges(self) -> None:
        """Reset all unit status badges to waiting."""
        for i in range(self.input_list.count()):
            item = self.input_list.item(i)
            if item is not None:
                path = item.data(Qt.UserRole)
                item.setText(f"[等待] {path}")

    def add_paths(self, paths: list[str]) -> None:
        """Add file/folder paths to the input list."""
        if self._atom not in {"file", "folder"}:
            self.status_message.emit("当前工作流不需要路径输入。")
            return

        existing = {self.input_list.item(i).data(Qt.UserRole) for i in range(self.input_list.count())}
        resolved = [Path(p).resolve() for p in paths]

        if self._atom == "folder":
            valid_paths = []
            invalid_paths = []
            for raw_path in paths:
                p = Path(raw_path).resolve()
                result = InputInspector.validate_directory(p)
                if not result.is_valid:
                    invalid_paths.append(f"{result.path}: {result.error}")
                    continue
                normalized = str(p)
                if normalized not in existing:
                    valid_paths.append(normalized)
                    existing.add(normalized)
        else:
            valid, invalid = InputInspector.validate_path_input(resolved)
            valid_paths = [str(v) for v in valid]
            invalid_paths = [f"{inv.path}: {inv.error}" for inv in invalid]

        added_count = 0
        for path in valid_paths:
            item = QListWidgetItem(f"[等待] {path}")
            item.setData(Qt.UserRole, path)
            self.input_list.addItem(item)
            added_count += 1

        if added_count:
            self.status_message.emit(f"已添加 {added_count} 个输入。")

        if invalid_paths:
            details = "\n".join(invalid_paths[:10])
            if len(invalid_paths) > 10:
                details += "\n…"
            self.warning.emit("部分输入未添加", "以下输入无效已跳过：\n" + details)

        self.paths_changed.emit()

    def _choose_files(self) -> None:
        """Open file dialog to select input files."""
        if self._atom != "file" or not self._recurse:
            return
        selected, _ = QFileDialog.getOpenFileNames(self, "选择输入文件")
        if selected:
            self.add_paths(selected)

    def _choose_folder(self) -> None:
        """Open folder dialog to select input folder."""
        if self._atom not in {"file", "folder"}:
            return
        selected = QFileDialog.getExistingDirectory(self, "选择输入文件夹")
        if selected:
            self.add_paths([selected])

    def _remove_selected(self) -> None:
        """Remove selected items from the input list."""
        rows = sorted(
            (self.input_list.row(item) for item in self.input_list.selectedItems()),
            reverse=True,
        )
        for row in rows:
            self.input_list.takeItem(row)
        self.paths_changed.emit()