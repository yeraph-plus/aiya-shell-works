"""File handler: working-copy builder for path inputs.

Two responsibilities:

1. **Unit construction** (per / shared scope).  Honors the ``recurse`` flag
   to either keep directory inputs whole (folder unit) or expand to file
   sub-units while tracking ``source_root`` (preserves relative structure
   inside ``output_dir``).
2. **Working-copy preparation**.  Either copy the input into ``output_dir``
   (default, safe) or operate on the original path (direct mode — required
   by destructive workflows such as flatten or unlock).  ``output_dir`` is
   always created — even in direct mode — so extra产出 files have a
   guaranteed landing place.  See ``AGENTS.md`` §14.3 for the contract.

In ``scope=shared`` all inputs are copied into ``output_dir`` and the unit
becomes ``output_dir`` itself — a single batch task over a merged tree.
"""

from __future__ import annotations

from pathlib import Path
from shutil import copy2, copytree
from typing import Any

from .context import PipelineContext
from .exceptions import FileHandlingError
from .input import InputPlan


# ---------------------------------------------------------------------------
# Unit dicts: lightweight data carrier consumed by executor._prepare_context
# ---------------------------------------------------------------------------

def build_path_units(paths: list[Path], *, recurse: bool) -> list[dict[str, Any]]:
    """Return one unit dict per processing unit, given raw input paths.

    * ``recurse=True``: directories expand to contained *files* with
      ``source_root`` set so relative layout is preserved on copy.
    * ``recurse=False``: directories stay whole (one folder unit); files
      yield themselves (single-file unit).  Mixing both is caller's job to
      validate (``resolve_input`` does so).
    """
    units: list[dict[str, Any]] = []
    for p in paths:
        if p.is_file():
            units.append({"path": p, "source_root": None})
        elif p.is_dir():
            if recurse:
                source_root = p
                for fp in sorted(p.rglob("*")):
                    if fp.is_file():
                        units.append({"path": fp, "source_root": source_root})
            else:
                units.append({"path": p, "source_root": None})
        else:
            raise FileHandlingError(f"不支持的输入路径类型: {p}")
    return units


def build_lines_units(lines: list[str]) -> list[dict[str, Any]]:
    """One unit per non-empty text line (atom=line)."""

    return [{"line": line} for line in lines]


# ---------------------------------------------------------------------------
# WorkingCopier: unit-tree-builder + working-copy preparation
# ---------------------------------------------------------------------------

class WorkingCopier:
    """Make working copies (or direct references) for processing units.

    ``output_dir`` is always created on construction (even in direct mode)
    so sidecar files have a landing strip — this is invariant §14.3.
    """

    def __init__(self, output_dir: str | Path, *, direct_mode: bool = False) -> None:
        self.output_dir = Path(output_dir)
        self.direct_mode = direct_mode
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def prepare_none(self, *, shared: dict[str, Any] | None = None) -> PipelineContext:
        return PipelineContext(
            original_input=None,
            working_path=self.output_dir,
            output_dir=self.output_dir,
            atom="none",
            shared=dict(shared or {}),
        )

    def prepare_line(self, unit: dict[str, Any], *, shared: dict[str, Any] | None = None) -> PipelineContext:
        line = str(unit["line"])
        return PipelineContext(
            original_input=None,
            working_path=self.output_dir,
            output_dir=self.output_dir,
            atom="line",
            shared={**(shared or {}), "input_line": line},
        )

    def prepare_path_unit(
        self,
        unit: dict[str, Any],
        *,
        atom: str,
        shared: dict[str, Any] | None = None,
    ) -> PipelineContext:
        source = Path(unit["path"])
        source_root = Path(unit["source_root"]) if unit.get("source_root") else None

        self._ensure_existing(source)
        if source_root is not None:
            self._ensure_existing_directory(source_root, label="source root")

        if self.direct_mode:
            working_path = source
        else:
            try:
                if source.is_file():
                    working_path = self._copy_file(source, source_root=source_root)
                else:
                    working_path = self._copy_directory(source)
            except OSError as exc:
                raise FileHandlingError(f"复制失败: {source}") from exc

        return PipelineContext(
            original_input=source,
            working_path=working_path,
            output_dir=self.output_dir,
            atom=atom,
            shared=dict(shared or {}),
            source_root=source_root,
        )

    def prepare_shared_path_unit(
        self,
        paths: list[Path],
        *,
        recurse: bool,
        shared: dict[str, Any] | None = None,
    ) -> PipelineContext:
        """Combine all path inputs into a single unit rooted at output_dir."""

        for p in paths:
            self._ensure_existing(p)
            if self.direct_mode:
                # In direct mode we cannot merge disjoint originals into a single
                # working tree without copying; reversible merge would require
                # a temp dir.  Refuse to keep semantics unambiguous.
                raise FileHandlingError(
                    "scope=0 与 direct_mode 不兼容：shared 需要 output_dir 形成合并树。"
                )
            try:
                if p.is_file():
                    rel = Path(p.name)
                    self._copy_into(p, self.output_dir / rel)
                else:
                    target = self._make_unique_path(self.output_dir / p.name)
                    copytree(p, target, copy_function=copy2)
            except OSError as exc:
                raise FileHandlingError(f"shared 合并复制失败: {p}") from exc

        return PipelineContext(
            original_input=None,
            working_path=self.output_dir,
            output_dir=self.output_dir,
            atom="file",
            shared=dict(shared or {}),
        )

    # ------------------------------------------------------------------
    # File-system helpers
    # ------------------------------------------------------------------

    def _copy_into(self, source: Path, destination: Path) -> Path:
        destination = self._make_unique_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            copy2(source, destination)
        else:
            copytree(source, destination, copy_function=copy2)
        return destination

    def _copy_file(self, source: Path, *, source_root: Path | None = None) -> Path:
        relative = self._resolve_relative(source, source_root)
        destination = self._make_unique_path(self.output_dir / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy2(source, destination)
        return destination

    def _copy_directory(self, source: Path) -> Path:
        destination = self._make_unique_path(self.output_dir / source.name)
        copytree(source, destination, copy_function=copy2)
        return destination

    @staticmethod
    def _resolve_relative(source: Path, source_root: Path | None) -> Path:
        if source_root is None:
            return Path(source.name)
        try:
            return source.relative_to(source_root)
        except ValueError as exc:
            raise FileHandlingError(
                f"文件 {source} 不在 source_root {source_root} 内，无法保持相对路径。"
            ) from exc

    @staticmethod
    def _make_unique_path(target: Path) -> Path:
        if not target.exists():
            return target
        parent = target.parent
        suffix = "".join(target.suffixes)
        stem = target.name[: -len(suffix)] if suffix else target.name
        counter = 1
        while counter <= 10000:
            candidate = parent / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1
        raise RuntimeError(f"unable to generate unique name: {target} (10000 attempts)")

    @staticmethod
    def _ensure_existing(path: Path) -> None:
        if not path.exists():
            raise FileHandlingError(f"输入路径不存在: {path}")

    @staticmethod
    def _ensure_existing_directory(path: Path, *, label: str = "输入目录") -> None:
        if not path.exists():
            raise FileHandlingError(f"{label}不存在: {path}")
        if not path.is_dir():
            raise FileHandlingError(f"{label}不是目录: {path}")


# ---------------------------------------------------------------------------
# Convenience: dispatch from InputPlan to units + context shape
# ---------------------------------------------------------------------------

def units_from_plan(plan: InputPlan) -> list[dict[str, Any]]:
    """Pure unit construction from a plan — no filesystem copy yet."""

    if plan.atom == "line":
        return build_lines_units(list(plan.lines))
    if plan.atom == "none":
        return [{"path": None, "source_root": None}]
    return build_path_units(list(plan.files), recurse=plan.recurse)


def make_unique_path(target: Path) -> Path:
    """Re-export ``WorkingCopier._make_unique_path`` as a standalone helper."""

    return WorkingCopier._make_unique_path(target)