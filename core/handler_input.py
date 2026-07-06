"""Text-input handler for *input* mode workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .pipeline import PipelineContext


class InputHandler:
    """Build units and prepare contexts for text-input (*input*) mode."""

    @staticmethod
    def build_units(lines: list[str]) -> list[dict[str, Any]]:
        """Convert a list of text lines into unit dicts."""
        return [{"line": line} for line in lines]

    @staticmethod
    def prepare_context(
        line: str,
        output_dir: str | Path,
        *,
        shared: dict[str, Any] | None = None,
    ) -> PipelineContext:
        """Create a PipelineContext for a single text line."""
        return PipelineContext(
            original_input=None,
            working_path=Path(output_dir),
            output_dir=Path(output_dir),
            mode="input",
            shared={**(shared or {}), "input_line": line},
        )
