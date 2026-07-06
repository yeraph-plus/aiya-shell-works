"""Dynamic parameter form generation based on module config schemas."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui.workflow_editor_state import (
    SchemaField,
    coerce_field_value,
    iter_schema_fields,
)


class DynamicParameterForm(QWidget):
    """Render editable parameter widgets from a module config schema."""

    values_changed = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fields: tuple[SchemaField, ...] = ()
        self._widgets: dict[str, QWidget] = {}
        self._empty_label = QLabel("该模块没有可编辑参数。")
        self._form_layout = QFormLayout()
        self._form_layout.setContentsMargins(0, 0, 0, 0)
        self._form_layout.addRow(self._empty_label)
        self.setLayout(self._form_layout)

    def set_schema(
        self,
        schema: dict[str, Any] | None,
        values: dict[str, Any] | None = None,
    ) -> None:
        self._clear_layout()
        self._widgets = {}
        self._fields = iter_schema_fields(schema)

        if not self._fields:
            self._empty_label = QLabel("该模块没有可编辑参数。")
            self._form_layout.addRow(self._empty_label)
            self.values_changed.emit({})
            return

        initial_values = dict(values or {})
        for field in self._fields:
            widget = self._create_widget(field, initial_values.get(field.name, field.default))
            self._widgets[field.name] = widget
            label = field.label + (" *" if field.required else "")
            self._form_layout.addRow(label, widget)

        self.values_changed.emit(self.get_values())

    def get_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field in self._fields:
            widget = self._widgets[field.name]
            if field.field_type == "bool":
                raw_value = widget.isChecked()  # type: ignore[attr-defined]
            elif field.field_type == "int":
                raw_value = widget.value()  # type: ignore[attr-defined]
            elif field.field_type == "float":
                raw_value = widget.value()  # type: ignore[attr-defined]
            elif field.field_type in {"select", "radio"}:
                if field.field_type == "select":
                    raw_value = widget.currentData()  # type: ignore[attr-defined]
                else:
                    raw_value = _radio_value(widget)
            elif field.field_type in {"file_path", "folder_path"}:
                line_edit = widget.findChild(QLineEdit)
                raw_value = line_edit.text() if line_edit is not None else ""
            else:
                raw_value = widget.text()  # type: ignore[attr-defined]
            values[field.name] = coerce_field_value(field, raw_value)
        return values

    def _clear_layout(self) -> None:
        while self._form_layout.rowCount():
            row = self._form_layout.rowCount() - 1
            label_item = self._form_layout.itemAt(row, QFormLayout.ItemRole.LabelRole)
            field_item = self._form_layout.itemAt(row, QFormLayout.ItemRole.FieldRole)
            for item in (label_item, field_item):
                if item is not None and item.widget() is not None:
                    item.widget().deleteLater()
            self._form_layout.removeRow(row)

    def _create_widget(self, field: SchemaField, value: Any) -> QWidget:
        if field.field_type == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(coerce_field_value(field, value)))
            widget.toggled.connect(self._emit_values)
            self._apply_metadata(widget, field)
            return widget

        if field.field_type == "int":
            widget = QSpinBox()
            widget.setRange(
                int(field.minimum) if field.minimum is not None else -999999999,
                int(field.maximum) if field.maximum is not None else 999999999,
            )
            widget.setSingleStep(int(field.step) if field.step is not None else 1)
            widget.setValue(int(coerce_field_value(field, value)))
            widget.valueChanged.connect(self._emit_values)
            self._apply_metadata(widget, field)
            return widget

        if field.field_type == "float":
            widget = QDoubleSpinBox()
            widget.setDecimals(6)
            widget.setRange(
                float(field.minimum) if field.minimum is not None else -999999999.0,
                float(field.maximum) if field.maximum is not None else 999999999.0,
            )
            widget.setSingleStep(float(field.step) if field.step is not None else 0.1)
            widget.setValue(float(coerce_field_value(field, value)))
            widget.valueChanged.connect(self._emit_values)
            self._apply_metadata(widget, field)
            return widget

        if field.field_type == "select":
            widget = QComboBox()
            for option in field.options:
                widget.addItem(option.label, option.value)
            current_value = coerce_field_value(field, value)
            current_index = widget.findData(current_value)
            if current_index >= 0:
                widget.setCurrentIndex(current_index)
            widget.currentIndexChanged.connect(self._emit_values)
            self._apply_metadata(widget, field)
            return widget

        if field.field_type == "radio":
            container = QWidget()
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 2, 0, 2)
            group = QButtonGroup(container)
            group.setExclusive(True)
            current_value = coerce_field_value(field, value)
            for option in field.options:
                radio = QRadioButton(str(option.label))
                radio.setProperty("radio_value", option.value)
                group.addButton(radio)
                layout.addWidget(radio)
                if option.value == current_value:
                    radio.setChecked(True)
            group.buttonClicked.connect(self._emit_values)
            self._apply_metadata(container, field)
            return container

        if field.field_type in {"file_path", "folder_path"}:
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            line_edit = QLineEdit(str(coerce_field_value(field, value)))
            if field.placeholder:
                line_edit.setPlaceholderText(field.placeholder)
            button = QPushButton("浏览")
            button.clicked.connect(
                lambda: self._choose_path(field.field_type, line_edit)
            )
            line_edit.textChanged.connect(self._emit_values)
            layout.addWidget(line_edit, stretch=1)
            layout.addWidget(button)
            self._apply_metadata(container, field)
            return container

        widget = QLineEdit(str(coerce_field_value(field, value)))
        if field.placeholder:
            widget.setPlaceholderText(field.placeholder)
        widget.textChanged.connect(self._emit_values)
        self._apply_metadata(widget, field)
        return widget

    def _choose_path(self, field_type: str, line_edit: QLineEdit) -> None:
        if field_type == "file_path":
            selected, _ = QFileDialog.getOpenFileName(self, "选择文件")
        else:
            selected = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if selected:
            line_edit.setText(selected)

    def _apply_metadata(self, widget: QWidget, field: SchemaField) -> None:
        tooltip = field.description or field.placeholder
        if tooltip:
            widget.setToolTip(tooltip)

    def _emit_values(self, *_args: object) -> None:
        self.values_changed.emit(self.get_values())


def _radio_value(container: QWidget) -> Any:
    for child in container.children():
        if isinstance(child, QRadioButton) and child.isChecked():
            return child.property("radio_value")
    return None