"""File handler: working-copy builder for path and line inputs.

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

In ``scope=shared`` all path inputs are copied into ``output_dir`` and the
unit becomes ``output_dir`` itself — a single batch task over a merged tree.
For ``scope>1`` the executor creates per-batch units; path batches are merged
into isolated batch worktrees beneath ``output_dir`` and line batches are
injected as ``ctx.shared["input_lines"]``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from shutil import copy2, copytree, move, rmtree
from tempfile import mkdtemp
from typing import Any

from .context import PipelineContext
from .exceptions import FileHandlingError
from .input import InputPlan


@dataclass(frozen=True, slots=True)
class UnitWorkspace:
    """A clean per-unit staging directory owned by an execution workspace."""

    index: int
    path: Path


class ExecutionWorkspace:
    """Create clean unit workspaces and publish them into the final output."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.parent.mkdir(parents=True, exist_ok=True)
        self.root = Path(mkdtemp(prefix=".shell-worker-", dir=self.output_dir.parent))

    def create_unit(self, index: int) -> UnitWorkspace:
        path = self.root / f"unit_{index:06d}"
        path.mkdir(parents=True, exist_ok=False)
        return UnitWorkspace(index=index, path=path)

    def publish(self, unit: UnitWorkspace) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for source in sorted(unit.path.iterdir(), key=lambda item: item.name):
            destination = self.output_dir / source.name
            if source.is_dir():
                if destination.is_file() or destination.is_symlink():
                    destination.unlink()
                copytree(source, destination, copy_function=copy2, dirs_exist_ok=True)
            else:
                if destination.is_dir():
                    rmtree(destination)
                destination.parent.mkdir(parents=True, exist_ok=True)
                copy2(source, destination)

    def map_context(self, ctx: PipelineContext, unit: UnitWorkspace) -> PipelineContext:
        def remap(path: Path) -> Path:
            try:
                return self.output_dir / path.relative_to(unit.path)
            except ValueError:
                return path

        return ctx.clone(
            working_path=remap(ctx.working_path),
            output_dir=self.output_dir,
            extra_files=[remap(path) for path in ctx.extra_files],
        )

    def close(self) -> None:
        rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> ExecutionWorkspace:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def validate_output_separation(
    paths: list[Path] | tuple[Path, ...],
    output_dir: str | Path,
    *,
    strict: bool,
) -> None:
    """Reject source/output overlap before creating or enumerating output."""

    output = Path(output_dir).resolve()
    for raw_path in paths:
        source = raw_path.resolve()
        if source == output:
            raise FileHandlingError(f"输入路径与输出目录不能相同: {source}")
        source_contains_output = source.is_dir() and output.is_relative_to(source)
        output_contains_source = source.is_relative_to(output)
        if source_contains_output or (strict and output_contains_source):
            raise FileHandlingError(f"输入路径与输出目录不能互相嵌套: {source} <-> {output}")

# ---------------------------------------------------------------------------
# Unit dicts: lightweight data carrier consumed by executor.prepare_context
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


def build_lines_units(lines: list[str], batch_size: int = 1) -> list[dict[str, Any]]:
    """Return line-shaped units, optionally grouped into fixed-size batches."""

    if batch_size <= 1:
        return [{"line": line} for line in lines]
    return [{"lines": lines[index : index + batch_size]} for index in range(0, len(lines), batch_size)]


# ---------------------------------------------------------------------------
# WorkingCopier: unit-tree-builder + working-copy preparation
# ---------------------------------------------------------------------------


class WorkingCopier:
    """Make working copies (or direct references) for processing units.

    ``output_dir`` is always created on construction (even in direct mode)
    so sidecar files have a landing strip — this is invariant §14.3.
    """

    def __init__(
        self,
        output_dir: str | Path,
        *,
        direct_mode: bool = False,
        move_mode: bool = False,
    ) -> None:
        if direct_mode and move_mode:
            raise ValueError("direct_mode and move_mode are mutually exclusive")
        self.output_dir = Path(output_dir)
        self.direct_mode = direct_mode
        self.move_mode = move_mode
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def prepare_none(self, *, shared: Mapping[str, Any] | None = None) -> PipelineContext:
        return PipelineContext(
            original_input=None,
            working_path=self.output_dir,
            output_dir=self.output_dir,
            shared=dict(shared or {}),
        )

    def prepare_line(self, unit: dict[str, Any], *, shared: Mapping[str, Any] | None = None) -> PipelineContext:
        lines = unit.get("lines")
        if lines is None:
            lines = [str(unit["line"])]
        else:
            lines = [str(line) for line in lines]
        payload = {**(shared or {}), "input_lines": list(lines)}
        if len(lines) == 1:
            payload["input_line"] = lines[0]
        return PipelineContext(
            original_input=None,
            working_path=self.output_dir,
            output_dir=self.output_dir,
            shared=payload,
        )

    def prepare_batched_path_unit(
        self,
        units: list[dict[str, Any]],
        *,
        batch_index: int,
        shared: Mapping[str, Any] | None = None,
    ) -> PipelineContext:
        """Merge one path batch into its own isolated worktree."""

        if self.direct_mode:
            raise FileHandlingError("scope>1 与 direct_mode 不兼容：批次需要 output_dir 形成独立工作树。")

        batch_root = self._make_unique_path(self.output_dir / f"_batch_{batch_index:04d}")
        batch_root.mkdir(parents=True, exist_ok=False)

        for unit in units:
            source = Path(unit["path"])
            source_root = Path(unit["source_root"]) if unit.get("source_root") else None
            self._ensure_existing(source)
            if source_root is not None:
                self._ensure_existing_directory(source_root, label="source root")
            try:
                if source.is_file():
                    rel = self._resolve_relative(source, source_root)
                    self._transfer_into(source, batch_root / rel)
                else:
                    self._transfer_into(source, batch_root / source.name)
            except OSError as exc:
                raise FileHandlingError(f"scope>1 批次复制失败: {source}") from exc

        return PipelineContext(
            original_input=None,
            working_path=batch_root,
            output_dir=self.output_dir,
            shared=dict(shared or {}),
        )

    def prepare_path_unit(
        self,
        unit: dict[str, Any],
        *,
        shared: Mapping[str, Any] | None = None,
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
                    working_path = self._transfer_file(source, source_root=source_root)
                else:
                    working_path = self._transfer_directory(source)
            except OSError as exc:
                raise FileHandlingError(f"复制失败: {source}") from exc

        return PipelineContext(
            original_input=source,
            working_path=working_path,
            output_dir=self.output_dir,
            shared=dict(shared or {}),
            source_root=source_root,
        )

    def prepare_shared_path_unit(
        self,
        paths: list[Path],
        *,
        recurse: bool,
        shared: Mapping[str, Any] | None = None,
    ) -> PipelineContext:
        """Combine all path inputs into a single unit rooted at output_dir."""

        for p in paths:
            self._ensure_existing(p)
            if self.direct_mode:
                # In direct mode we cannot merge disjoint originals into a single
                # working tree without copying; reversible merge would require
                # a temp dir.  Refuse to keep semantics unambiguous.
                raise FileHandlingError("scope=0 与 direct_mode 不兼容：shared 需要 output_dir 形成合并树。")
            try:
                if p.is_file():
                    rel = Path(p.name)
                    self._transfer_into(p, self.output_dir / rel)
                else:
                    target = self._make_unique_path(self.output_dir / p.name)
                    if self.move_mode:
                        move(str(p), str(target))
                    else:
                        copytree(p, target, copy_function=copy2)
            except OSError as exc:
                raise FileHandlingError(f"shared 合并复制失败: {p}") from exc

        return PipelineContext(
            original_input=None,
            working_path=self.output_dir,
            output_dir=self.output_dir,
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

    def _transfer_into(self, source: Path, destination: Path) -> Path:
        destination = self._make_unique_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.move_mode:
            return Path(move(str(source), str(destination)))
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

    def _transfer_file(self, source: Path, *, source_root: Path | None = None) -> Path:
        if not self.move_mode:
            return self._copy_file(source, source_root=source_root)
        relative = self._resolve_relative(source, source_root)
        destination = self._make_unique_path(self.output_dir / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        return Path(move(str(source), str(destination)))

    def _copy_directory(self, source: Path) -> Path:
        destination = self._make_unique_path(self.output_dir / source.name)
        copytree(source, destination, copy_function=copy2)
        return destination

    def _transfer_directory(self, source: Path) -> Path:
        if not self.move_mode:
            return self._copy_directory(source)
        destination = self._make_unique_path(self.output_dir / source.name)
        return Path(move(str(source), str(destination)))

    @staticmethod
    def _resolve_relative(source: Path, source_root: Path | None) -> Path:
        if source_root is None:
            return Path(source.name)
        try:
            return source.relative_to(source_root)
        except ValueError as exc:
            raise FileHandlingError(f"文件 {source} 不在 source_root {source_root} 内，无法保持相对路径。") from exc

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

    if plan.kind == "line":
        return build_lines_units(list(plan.lines))
    if plan.kind == "none":
        return [{"path": None, "source_root": None}]
    return build_path_units(list(plan.files), recurse=plan.recurse)


def make_unique_path(target: Path) -> Path:
    """Re-export ``WorkingCopier._make_unique_path`` as a standalone helper."""

    return WorkingCopier._make_unique_path(target)
