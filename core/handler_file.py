"""File handler for preparing safe working copies for processing units."""

from __future__ import annotations

from pathlib import Path
from shutil import copy2, copytree
from typing import Any

from .pipeline import PipelineContext, PipelineMode


class FileHandlingError(RuntimeError):
    """Raised when preparing or cleaning up a processing unit fails."""


class FileHandler:
    """Prepare safe working copies (or direct references) for modules.

    In **copy mode** (``direct_mode=False``, the default) every input is
    duplicated into *output_dir* before the workflow touches it.  In
    **direct mode** (``direct_mode=True``) the *working_path* points at
    the original file/folder on disk and no copy is made.

    *output_dir* is always set so extra files (summaries, logs, ...) have
    a place to land.
    """

    def __init__(
        self,
        output_dir: str | Path,
        *,
        direct_mode: bool = False,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.direct_mode = direct_mode
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Unit builders – moved from the executor so it stays slim
    # ------------------------------------------------------------------

    @staticmethod
    def _build_file_units_impl(
        input_paths: list[Path], *, include_source_root: bool
    ) -> list[dict[str, Any]]:
        units: list[dict[str, Any]] = []
        for p in input_paths:
            if p.is_file():
                units.append({"path": p, "source_root": None})
            elif p.is_dir():
                source_root = p if include_source_root else None
                files = sorted(path for path in p.rglob("*") if path.is_file())
                for file_path in files:
                    units.append({"path": file_path, "source_root": source_root})
        return units

    @staticmethod
    def build_file_units(input_paths: list[Path]) -> list[dict[str, Any]]:
        """Build unit dicts for *file* mode.

        Each file yields one unit.  Every folder is recursively expanded
        and its contained files keep ``source_root`` set to the folder so
        relative directory structure is preserved.
        """
        return FileHandler._build_file_units_impl(input_paths, include_source_root=True)

    @staticmethod
    def build_cycle_units(input_paths: list[Path]) -> list[dict[str, Any]]:
        """Build unit dicts for *cycle* mode (no ``source_root``)."""
        return FileHandler._build_file_units_impl(input_paths, include_source_root=False)

    @staticmethod
    def build_folder_unit(folder_path: Path) -> list[dict[str, Any]]:
        """Build a single-unit list for *folder* mode."""
        return [{"path": folder_path, "source_root": None}]

    # ------------------------------------------------------------------
    # Context preparation
    # ------------------------------------------------------------------

    def prepare_none_context(
        self,
        *,
        shared: dict[str, Any] | None = None,
    ) -> PipelineContext:
        """Create the initial context for workflows that do not need inputs."""

        return PipelineContext(
            original_input=None,
            working_path=self.output_dir,
            output_dir=self.output_dir,
            mode="none",
            shared=dict(shared or {}),
        )

    def prepare_context(
        self,
        unit: dict[str, Any],
        *,
        mode: PipelineMode,
        shared: dict[str, Any] | None = None,
        base_context: PipelineContext | None = None,
    ) -> PipelineContext:
        """Route to the correct preparation method based on *mode*."""
        if mode == "none":
            return self.prepare_none_context(shared=shared)
        if mode == "file":
            return self._prepare_file_unit(unit, shared=shared)
        if mode == "cycle":
            return self._prepare_cycle_unit(unit, shared=shared, base_context=base_context)
        if mode == "folder":
            return self._prepare_folder_unit(unit, shared=shared)
        raise FileHandlingError(f"不支持的工作流模式: {mode}")

    def finalize_context(self, ctx: PipelineContext, *, success: bool) -> bool:  # noqa: ARG002
        """Reserved extension point — currently a no-op.

        Original files are never deleted by the platform.
        """
        _ = ctx
        return False

    # ------------------------------------------------------------------
    # Internal – file unit preparation
    # ------------------------------------------------------------------

    def _prepare_file_unit(
        self,
        unit: dict[str, Any],
        *,
        shared: dict[str, Any] | None = None,
    ) -> PipelineContext:
        source = Path(unit["path"])
        source_root = Path(unit["source_root"]) if unit.get("source_root") else None

        self._ensure_existing_file(source)
        if source_root is not None:
            self._ensure_existing_directory(source_root, label="source root")

        if self.direct_mode:
            working_path = source
        else:
            try:
                working_path = self._copy_file(source, source_root=source_root)
            except OSError as exc:
                raise FileHandlingError(f"复制文件失败: {source}") from exc

        return PipelineContext(
            original_input=source,
            working_path=working_path,
            output_dir=self.output_dir,
            mode="file",
            shared=dict(shared or {}),
            source_root=source_root,
        )

    def _prepare_cycle_unit(
        self,
        unit: dict[str, Any],
        *,
        shared: dict[str, Any] | None = None,
        base_context: PipelineContext | None = None,
    ) -> PipelineContext:
        ctx = self._prepare_file_unit(unit, shared=shared)
        if base_context is not None:
            ctx = ctx.clone(
                shared=base_context.shared,
                events=base_context.events,
                extra_files=list(base_context.extra_files),
            )
        return ctx

    def _prepare_folder_unit(
        self,
        unit: dict[str, Any],
        *,
        shared: dict[str, Any] | None = None,
    ) -> PipelineContext:
        source = Path(unit["path"])
        self._ensure_existing_directory(source)

        if self.direct_mode:
            working_path = source
        else:
            try:
                working_path = self._copy_directory(source)
            except OSError as exc:
                raise FileHandlingError(f"复制文件夹失败: {source}") from exc

        return PipelineContext(
            original_input=source,
            working_path=working_path,
            output_dir=self.output_dir,
            mode="folder",
            shared=dict(shared or {}),
        )

    # ------------------------------------------------------------------
    # File-system helpers (copy mode only)
    # ------------------------------------------------------------------

    def _copy_file(self, source: Path, *, source_root: Path | None = None) -> Path:
        relative_path = self._resolve_relative_path(source, source_root)
        destination = self._make_unique_path(self.output_dir / relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy2(source, destination)
        return destination

    def _copy_directory(self, source: Path) -> Path:
        destination = self._make_unique_path(self.output_dir / source.name)
        copytree(source, destination, copy_function=copy2)
        return destination

    def _resolve_relative_path(self, source: Path, source_root: Path | None) -> Path:
        if source_root is None:
            return Path(source.name)

        try:
            relative_path = source.relative_to(source_root)
        except ValueError as exc:
            raise FileHandlingError(
                f"文件 {source} 不在 source_root {source_root} 内，无法保持相对路径。"
            ) from exc

        return relative_path

    def _make_unique_path(self, target: Path) -> Path:
        if not target.exists():
            return target

        parent = target.parent
        suffix = "".join(target.suffixes)
        stem = target.name[: -len(suffix)] if suffix else target.name

        counter = 1
        while True:
            candidate_name = f"{stem} ({counter}){suffix}"
            candidate = parent / candidate_name
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _ensure_existing_file(path: Path) -> None:
        if not path.exists():
            raise FileHandlingError(f"输入文件不存在: {path}")
        if not path.is_file():
            raise FileHandlingError(f"预期文件但收到非文件路径: {path}")

    @staticmethod
    def _ensure_existing_directory(path: Path, *, label: str = "输入目录") -> None:
        if not path.exists():
            raise FileHandlingError(f"{label}不存在: {path}")
        if not path.is_dir():
            raise FileHandlingError(f"{label}不是目录: {path}")
