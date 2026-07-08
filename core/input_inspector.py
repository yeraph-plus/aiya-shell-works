"""Pre-execution input validation helpers.

Most GUI-side checks just confirm path existence without expanding
directories — directory expansion is the executor's job so ``source_root``
is preserved.  ``validate_path_input`` exists specifically to defer
directory expansion; see AGENTS.md §14.1 for the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class ValidationResult:
    """Outcome of validating one input path."""

    path: Path
    is_valid: bool
    error: str = ""


class InputInspector:
    """Static validators used by GUI before adding files to the input list."""

    @staticmethod
    def validate_file(path: str | Path) -> ValidationResult:
        p = Path(path)
        if not p.exists():
            return ValidationResult(p, False, f"文件不存在: {p}")
        if not p.is_file():
            return ValidationResult(p, False, f"不是文件: {p}")
        return ValidationResult(p, True)

    @staticmethod
    def validate_directory(path: str | Path) -> ValidationResult:
        p = Path(path)
        if not p.exists():
            return ValidationResult(p, False, f"目录不存在: {p}")
        if not p.is_dir():
            return ValidationResult(p, False, f"不是目录: {p}")
        return ValidationResult(p, True)

    @staticmethod
    def validate_path_input(
        paths: list[str] | list[Path],
    ) -> tuple[list[Path], list[ValidationResult]]:
        """Validate file / folder paths without expanding directories.

        Used by the GUI to keep raw paths intact so the executor can later
        preserve ``source_root`` semantics for the file-atom workflow.
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
            invalid.append(ValidationResult(p, False, f"不支持的路径类型: {p}"))
        return valid, invalid
