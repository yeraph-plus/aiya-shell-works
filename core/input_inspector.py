"""Pre-execution input validation utilities."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class ValidationResult:
    """Result of validating a single input path."""

    path: Path
    is_valid: bool
    error: str = ""


class InputInspector:
    """Validates input paths before execution.

    Called by the GUI when adding files to ensure only valid inputs
    enter the processing list.  Also used for text-input splitting.
    """

    @staticmethod
    def validate_file(path: str | Path) -> ValidationResult:
        """Check whether *path* exists and is a regular file."""
        p = Path(path)
        if not p.exists():
            return ValidationResult(p, False, f"文件不存在: {p}")
        if not p.is_file():
            return ValidationResult(p, False, f"不是文件: {p}")
        return ValidationResult(p, True)

    @staticmethod
    def validate_directory(path: str | Path) -> ValidationResult:
        """Check whether *path* exists and is a directory."""
        p = Path(path)
        if not p.exists():
            return ValidationResult(p, False, f"目录不存在: {p}")
        if not p.is_dir():
            return ValidationResult(p, False, f"不是目录: {p}")
        return ValidationResult(p, True)

    @staticmethod
    def validate_file_input(
        paths: list[str] | list[Path],
    ) -> tuple[list[Path], list[ValidationResult]]:
        """Validate a batch of file/folder paths for *file* or *cycle* mode.

        Returns ``(valid_paths, invalid_results)``.  For any folder in the
        list the folder is recursively enumerated and its contained *files*
        are validated individually.
        """
        valid: list[Path] = []
        invalid: list[ValidationResult] = []

        for raw in paths:
            p = Path(raw)
            if not p.exists():
                invalid.append(ValidationResult(p, False, f"路径不存在: {p}"))
                continue
            if p.is_file():
                valid.append(p)
            elif p.is_dir():
                for file_path in sorted(p.rglob("*")):
                    if file_path.is_file():
                        valid.append(file_path)
            else:
                invalid.append(ValidationResult(p, False, f"不支持该路径类型: {p}"))

        return valid, invalid

    @staticmethod
    def validate_path_input(
        paths: list[str] | list[Path],
    ) -> tuple[list[Path], list[ValidationResult]]:
        """Validate file/folder inputs without expanding directories.

        GUI selection should keep raw paths intact so the executor can preserve
        mode-specific semantics such as ``source_root`` and shared cycle input.
        """
        valid: list[Path] = []
        invalid: list[ValidationResult] = []

        for raw in paths:
            p = Path(raw)
            if not p.exists():
                invalid.append(ValidationResult(p, False, f"路径不存在: {p}"))
                continue
            if p.is_file() or p.is_dir():
                valid.append(p)
                continue
            invalid.append(ValidationResult(p, False, f"不支持该路径类型: {p}"))

        return valid, invalid

    @staticmethod
    def validate_folder_input(path: str | Path) -> ValidationResult:
        """Validate a single directory path for *folder* mode."""
        return InputInspector.validate_directory(path)

    @staticmethod
    def validate_text_input(text: str) -> list[str]:
        """Split *text* into non-empty, stripped lines."""
        return [line.strip() for line in text.splitlines() if line.strip()]
