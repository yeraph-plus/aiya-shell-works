"""Filesystem infrastructure and execution workspaces.

All filesystem mutations performed by the kernel live here.  A workspace is a
real directory because modules may pass its paths to external programs.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from shutil import copy2, copytree, move, rmtree
from threading import RLock
from typing import Any

from .exceptions import FileHandlingError
from .input import InputPlan
from .planning import ExecutionUnit


def _unique_path(target: Path) -> Path:
    return _unique_path_with_reservations(target, set())


def _unique_path_with_reservations(target: Path, reserved: set[Path]) -> Path:
    if not target.exists() and not target.is_symlink():
        if target not in reserved:
            return target
    suffix = "".join(target.suffixes)
    stem = target.name[: -len(suffix)] if suffix else target.name
    for counter in range(1, 10001):
        candidate = target.parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists() and not candidate.is_symlink() and candidate not in reserved:
            return candidate
    raise FileHandlingError(f"unable to generate unique path: {target}")


def make_unique_path(target: Path) -> Path:
    return _unique_path(Path(target))


def validate_output_separation(paths: list[Path] | tuple[Path, ...], output_dir: str | Path, *, strict: bool) -> None:
    output = Path(output_dir).resolve()
    for raw in paths:
        source = Path(raw).resolve()
        if source == output:
            raise FileHandlingError(f"输入路径与输出目录不能相同: {source}")
        if source.is_dir() and output.is_relative_to(source):
            raise FileHandlingError(f"输入路径与输出目录不能互相嵌套: {source} <-> {output}")
        if strict and source.is_relative_to(output):
            raise FileHandlingError(f"输入路径与输出目录不能互相嵌套: {source} <-> {output}")


def build_path_units(paths: list[Path], *, recurse: bool) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            units.append({"path": path, "source_root": None})
        elif path.is_dir():
            if recurse:
                for child in sorted(path.rglob("*")):
                    if child.is_file():
                        units.append({"path": child, "source_root": path})
            else:
                units.append({"path": path, "source_root": None})
        else:
            raise FileHandlingError(f"不支持的输入路径类型: {path}")
    return units


def build_lines_units(lines: list[str], batch_size: int = 1) -> list[dict[str, Any]]:
    if batch_size <= 1:
        return [{"line": line} for line in lines]
    return [{"lines": lines[i : i + batch_size]} for i in range(0, len(lines), batch_size)]


def units_from_plan(plan: InputPlan) -> list[dict[str, Any]]:
    if plan.kind == "line":
        return build_lines_units(list(plan.lines))
    if plan.kind == "none":
        return [{"path": None, "source_root": None}]
    return build_path_units(list(plan.files), recurse=plan.recurse)


@dataclass(slots=True)
class WorkspaceFile:
    """A tracked file or directory in a :class:`UnitWorkspace`."""

    workspace: UnitWorkspace
    path: Path

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)

    @property
    def relative_path(self) -> Path:
        return self.workspace.relative_path(self.path)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def is_file(self) -> bool:
        return self.path.is_file()

    @property
    def is_dir(self) -> bool:
        return self.path.is_dir()

    def read_text(self, **kwargs: Any) -> str:
        return self.workspace.read_text(self.path, **kwargs)

    def read_bytes(self) -> bytes:
        return self.workspace.read_bytes(self.path)

    def write_text(self, data: str, **kwargs: Any) -> WorkspaceFile:
        return self.workspace.write_text(self.path, data, **kwargs)

    def write_bytes(self, data: bytes) -> WorkspaceFile:
        return self.workspace.write_bytes(self.path, data)

    def copy_to(self, target: str | Path) -> WorkspaceFile:
        return self.workspace.copy(self.path, target)

    def move_to(self, target: str | Path) -> WorkspaceFile:
        return self.workspace.move(self.path, target)

    def rename(self, name: str) -> WorkspaceFile:
        return self.workspace.rename(self.path, name)

    def delete(self) -> None:
        self.workspace.delete(self.path)


@dataclass(frozen=True, slots=True)
class PreparedWorkspaceUnit:
    workspace: UnitWorkspace
    original_input: Path | None = None
    source_root: Path | None = None
    input_lines: tuple[str, ...] = ()


@dataclass(slots=True)
class UnitWorkspace:
    """One unit's isolated manifest over a real output-backed workspace."""

    index: int
    root: Path
    current_path: Path
    owned_roots: set[Path] = field(default_factory=set)
    referenced_roots: dict[Path, Path] = field(default_factory=dict)
    reserved_paths: set[Path] = field(default_factory=set, repr=False)
    allocation_lock: RLock = field(default_factory=RLock, repr=False)
    execution: ExecutionWorkspace | None = field(default=None, repr=False)
    _entries: dict[Path, WorkspaceFile] = field(default_factory=dict, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _module_access: str = field(default="read_write", init=False, repr=False)

    @property
    def current(self) -> WorkspaceFile:
        return self.file(self.current_path)

    def relative_path(self, path: str | Path) -> Path:
        candidate = Path(path).resolve(strict=False)
        root = self.root.resolve()
        if candidate.is_relative_to(root):
            return candidate.relative_to(root)
        for referenced, alias in self.referenced_roots.items():
            referenced = referenced.resolve(strict=False)
            if candidate == referenced:
                return alias
            if candidate.is_relative_to(referenced):
                return alias / candidate.relative_to(referenced)
        for owned in self._external_roots():
            if candidate == owned:
                return Path(owned.name)
            if candidate.is_relative_to(owned):
                return Path(owned.name) / candidate.relative_to(owned)
        raise FileHandlingError(f"路径不属于当前工作区: {path}")

    def _ensure_inside(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        candidate = candidate.resolve(strict=False)
        if candidate.is_relative_to(self.root.resolve()):
            return candidate
        for owned in self._external_roots():
            if candidate == owned or (owned.is_dir() and candidate.is_relative_to(owned)):
                return candidate
        raise FileHandlingError(f"路径越界: {path}")

    def _external_roots(self) -> list[Path]:
        root = self.root.resolve()
        owned = [
            owned.resolve(strict=False)
            for owned in self.owned_roots
            if not owned.resolve(strict=False).is_relative_to(root)
        ]
        referenced = [path.resolve(strict=False) for path in self.referenced_roots]
        return list(dict.fromkeys([*owned, *referenced]))

    def _ensure_mutable(self, path: str | Path) -> Path:
        candidate = self._ensure_inside(path)
        if self._module_access == "read":
            raise FileHandlingError("只读模块不能修改工作区")
        if any(
            candidate == referenced.resolve(strict=False)
            or candidate.is_relative_to(referenced.resolve(strict=False))
            for referenced in self.referenced_roots
        ):
            raise FileHandlingError(f"只读引用不能修改: {candidate}")
        if candidate == self.root.resolve():
            raise FileHandlingError("不能修改工作区根目录")
        return candidate

    @contextmanager
    def module_access(self, access: str):
        previous = self._module_access
        self._module_access = access
        try:
            yield
        finally:
            self._module_access = previous

    def _is_owned(self, path: Path) -> bool:
        candidate = path.resolve(strict=False)
        return any(
            candidate == owned.resolve(strict=False) or candidate.is_relative_to(owned.resolve(strict=False))
            for owned in self.owned_roots
        )

    def _allocate_destination(self, path: str | Path, *, reserve: bool = False) -> Path:
        requested = self._ensure_mutable(path)
        root = self.root.resolve()
        if requested.is_relative_to(root):
            relative = requested.relative_to(root)
            top_level = root / relative.parts[0]
            owns_top_level = any(
                owned.resolve(strict=False) == top_level
                or owned.resolve(strict=False).is_relative_to(top_level)
                or top_level.is_relative_to(owned.resolve(strict=False))
                for owned in self.owned_roots
            )
            if not owns_top_level and (
                top_level.exists() or top_level.is_symlink() or top_level in self.reserved_paths
            ):
                allocated_top = _unique_path_with_reservations(top_level, self.reserved_paths)
                requested = allocated_top.joinpath(*relative.parts[1:])
        allocated = _unique_path_with_reservations(requested, self.reserved_paths)
        if reserve:
            self.reserved_paths.add(allocated)
        return allocated

    def _register_owned(self, path: Path) -> None:
        candidate = path.resolve(strict=False)
        root = self.root.resolve()
        if candidate.is_relative_to(root):
            relative = candidate.relative_to(root)
            if not relative.parts:
                return
            candidate = root / relative.parts[0]
            self.reserved_paths.add(candidate)
        if self.execution is not None:
            self.execution.claim_owned(self, candidate)
        if any(candidate == owned or candidate.is_relative_to(owned) for owned in self.owned_roots):
            return
        self.owned_roots = {owned for owned in self.owned_roots if not owned.is_relative_to(candidate)}
        self.owned_roots.add(candidate)

    def _unregister_owned(self, path: Path) -> None:
        candidate = path.resolve(strict=False)
        self.owned_roots = {owned for owned in self.owned_roots if owned.resolve(strict=False) != candidate}
        if self.execution is not None:
            self.execution.release_owned(self, candidate)

    def _register_reference(self, path: Path, relative: Path) -> None:
        candidate = path.resolve(strict=False)
        requested = self.root / relative
        reserved = {self.root / alias for alias in self.referenced_roots.values()}
        alias = _unique_path_with_reservations(requested, reserved).relative_to(self.root)
        self.referenced_roots[candidate] = alias

    @staticmethod
    def _validate_name(name: str) -> str:
        candidate = Path(name)
        if not name or name in {".", ".."} or candidate.is_absolute() or candidate.name != name:
            raise FileHandlingError(f"非法文件名: {name}")
        return name

    def path(self, *parts: str | Path) -> Path:
        if not parts:
            return self.current_path
        return self._ensure_inside(self.root.joinpath(*parts))

    def file(self, path: str | Path) -> WorkspaceFile:
        resolved = self._ensure_inside(path)
        if resolved != self.root.resolve() and not self._is_tracked(resolved):
            raise FileHandlingError(f"路径未登记到当前工作区: {resolved}")
        entry = self._entries.get(resolved)
        if entry is None:
            entry = WorkspaceFile(self, resolved)
            self._entries[resolved] = entry
        return entry

    def set_current(self, path: str | Path) -> WorkspaceFile:
        resolved = self._ensure_inside(path)
        if not resolved.exists() and not resolved.is_symlink():
            raise FileHandlingError(f"当前资源不存在: {resolved}")
        self.refresh()
        if resolved != self.root.resolve() and not self._is_tracked(resolved):
            raise FileHandlingError(f"当前资源未登记到工作区: {resolved}")
        self.current_path = resolved
        return self.file(resolved)

    def refresh(self) -> None:
        with self._lock:
            previous = self._entries
            refreshed: dict[Path, WorkspaceFile] = {}
            root = self.root.resolve(strict=False)
            root_entry = previous.get(root)
            refreshed[root] = root_entry or WorkspaceFile(self, root)
            roots = list(self.owned_roots) + list(self.referenced_roots)
            for tracked in roots:
                if not tracked.exists() and not tracked.is_symlink():
                    continue
                resolved = tracked.resolve(strict=False)
                refreshed[resolved] = previous.get(resolved) or WorkspaceFile(self, resolved)
                if tracked.is_dir():
                    for item in tracked.rglob("*"):
                        resolved_item = item.resolve(strict=False)
                        refreshed[resolved_item] = previous.get(resolved_item) or WorkspaceFile(self, resolved_item)
            self._entries = refreshed
            current = self.current_path.resolve(strict=False)
            if current != root and current not in refreshed:
                self.current_path = root

    def _is_tracked(self, path: Path) -> bool:
        candidate = path.resolve(strict=False)
        if candidate in self._entries:
            return True
        return any(
            candidate == root.resolve(strict=False)
            or (root.is_dir() and candidate.is_relative_to(root.resolve(strict=False)))
            for root in [*self.owned_roots, *self.referenced_roots]
        )

    def entries(self, recursive: bool = True) -> list[WorkspaceFile]:
        self.refresh()
        items = list(self._entries.values())
        if not recursive:
            if self.current_path.resolve(strict=False) == self.root.resolve():
                items = [entry for entry in items if len(entry.relative_path.parts) == 1]
            elif self.current_path.is_file():
                items = [entry for entry in items if entry.path == self.current_path]
            else:
                items = [entry for entry in items if entry.path.parent == self.current_path]
        return sorted(items, key=lambda entry: str(entry.relative_path))

    def files(self, recursive: bool = True) -> list[WorkspaceFile]:
        return [entry for entry in self.entries(recursive=recursive) if entry.is_file]

    def directories(self, recursive: bool = True) -> list[WorkspaceFile]:
        return [entry for entry in self.entries(recursive=recursive) if entry.is_dir]

    def create_file(
        self,
        name: str | Path,
        data: str | bytes = b"",
        *,
        encoding: str = "utf-8",
    ) -> WorkspaceFile:
        with self.allocation_lock:
            target = self._allocate_destination(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(data, bytes):
                target.write_bytes(data)
            else:
                target.write_text(data, encoding=encoding)
            self.reserved_paths.discard(target)
        self._register_owned(target)
        self.refresh()
        return self.file(target)

    def create_directory(self, name: str | Path) -> WorkspaceFile:
        with self.allocation_lock:
            target = self._allocate_destination(name)
            target.mkdir(parents=True, exist_ok=False)
            self.reserved_paths.discard(target)
        self._register_owned(target)
        self.refresh()
        return self.file(target)

    def allocate_file(self, name: str | Path) -> WorkspaceFile:
        """Reserve a collision-safe path for an external program to create."""

        with self.allocation_lock:
            target = self._allocate_destination(name, reserve=True)
            target.parent.mkdir(parents=True, exist_ok=True)
        self._register_owned(target)
        return WorkspaceFile(self, target)

    def adopt(self, path: str | Path) -> WorkspaceFile:
        """Add an existing external-program result to this unit's manifest."""

        target = self._ensure_mutable(path)
        if not target.exists() and not target.is_symlink():
            raise FileHandlingError(f"产物路径不存在: {target}")
        root = self.root.resolve()
        if target.is_relative_to(root):
            relative = target.relative_to(root)
            top_level = root / relative.parts[0]
            owns_top_level = any(
                top_level == owned.resolve(strict=False)
                or top_level.is_relative_to(owned.resolve(strict=False))
                for owned in self.owned_roots
            )
            if target != top_level and not owns_top_level and top_level not in self.reserved_paths:
                raise FileHandlingError(f"不能把既有顶层目录中的未分配路径加入工作区: {target}")
        if self.execution is not None:
            self.execution.claim_adopted(self, target)
        self._register_owned(target)
        self.reserved_paths.discard(target)
        self.refresh()
        return self.file(target)

    def read_text(self, source: str | Path, **kwargs: Any) -> str:
        target = self._ensure_inside(source)
        self.refresh()
        if target != self.root.resolve() and not self._is_tracked(target):
            raise FileHandlingError(f"路径未登记到当前工作区: {target}")
        return target.read_text(**kwargs)

    def read_bytes(self, source: str | Path) -> bytes:
        target = self._ensure_inside(source)
        self.refresh()
        if target != self.root.resolve() and not self._is_tracked(target):
            raise FileHandlingError(f"路径未登记到当前工作区: {target}")
        return target.read_bytes()

    def write_text(self, target: str | Path, data: str, **kwargs: Any) -> WorkspaceFile:
        destination = self._ensure_mutable(target)
        with self.allocation_lock:
            if not self._is_owned(destination):
                destination = self._allocate_destination(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(data, **kwargs)
            self.reserved_paths.discard(destination)
        self._register_owned(destination)
        self.refresh()
        return self.file(destination)

    def write_bytes(self, target: str | Path, data: bytes) -> WorkspaceFile:
        destination = self._ensure_mutable(target)
        with self.allocation_lock:
            if not self._is_owned(destination):
                destination = self._allocate_destination(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            self.reserved_paths.discard(destination)
        self._register_owned(destination)
        self.refresh()
        return self.file(destination)

    def copy(self, source: str | Path, target: str | Path) -> WorkspaceFile:
        src = self._ensure_mutable(source)
        if not src.exists():
            raise FileHandlingError(f"源路径不存在: {src}")
        with self.allocation_lock:
            dst = self._allocate_destination(target)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                copytree(src, dst, copy_function=copy2)
            else:
                copy2(src, dst)
            self.reserved_paths.discard(dst)
        self._register_owned(dst)
        self.refresh()
        return self.file(dst)

    def move(self, source: str | Path, target: str | Path) -> WorkspaceFile:
        src = self._ensure_mutable(source)
        if not src.exists():
            raise FileHandlingError(f"源路径不存在: {src}")
        requested = self._ensure_mutable(target)
        if requested == src:
            return self.file(src)
        previous_current = self.current_path.resolve(strict=False)
        with self.allocation_lock:
            dst = self._allocate_destination(requested)
            dst.parent.mkdir(parents=True, exist_ok=True)
            moved = Path(move(str(src), str(dst))).resolve(strict=False)
            self.reserved_paths.discard(moved)
        if src.resolve(strict=False) in {owned.resolve(strict=False) for owned in self.owned_roots}:
            self._unregister_owned(src)
        self._register_owned(moved)
        remapped: dict[Path, WorkspaceFile] = {}
        for entry_path, entry in self._entries.items():
            if entry_path == src or entry_path.is_relative_to(src):
                new_path = moved / entry_path.relative_to(src)
                entry.path = new_path
                remapped[new_path] = entry
            else:
                remapped[entry_path] = entry
        self._entries = remapped
        if previous_current == src or previous_current.is_relative_to(src):
            self.current_path = moved / previous_current.relative_to(src)
        self.refresh()
        return self.file(moved)

    def rename(self, source: str | Path, name: str) -> WorkspaceFile:
        src = self._ensure_mutable(source)
        return self.move(src, src.parent / self._validate_name(name))

    def delete(self, source: str | Path) -> None:
        target = self._ensure_mutable(source)
        if target.is_dir() and not target.is_symlink():
            rmtree(target)
        else:
            target.unlink(missing_ok=True)
        self._unregister_owned(target)
        self.reserved_paths.discard(target)
        current = self.current_path.resolve(strict=False)
        if current == target or current.is_relative_to(target):
            self.current_path = self.root.resolve()
        self.refresh()

    def publish(self) -> None:
        self.refresh()


class ExecutionWorkspace:
    """Allocate unit resources directly in the final output workspace."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.root = self.output_dir
        self._lock = RLock()
        self._reservations: set[Path] = set()
        self._baseline_roots = {path.resolve(strict=False) for path in self.root.iterdir()}
        self._claims: dict[Path, int | None] = {}

    def create_unit(self, index: int) -> UnitWorkspace:
        # Kept as a compatibility entry point; input is attached by prepare_unit.
        unit = UnitWorkspace(
            index=index,
            root=self.root,
            current_path=self.root,
            reserved_paths=self._reservations,
            allocation_lock=self._lock,
            execution=self,
        )
        unit.refresh()
        return unit

    def _top_level(self, path: Path) -> Path | None:
        candidate = path.resolve(strict=False)
        if not candidate.is_relative_to(self.root):
            return None
        relative = candidate.relative_to(self.root)
        if not relative.parts:
            return None
        return self.root / relative.parts[0]

    def claim_owned(self, unit: UnitWorkspace, path: Path) -> None:
        top_level = self._top_level(path)
        if top_level is None:
            return
        with self._lock:
            if top_level in self._claims and self._claims[top_level] != unit.index:
                raise FileHandlingError(f"产物已属于其他处理单元: {top_level}")
            self._claims[top_level] = unit.index
            self._reservations.add(top_level)

    def claim_adopted(self, unit: UnitWorkspace, path: Path) -> None:
        top_level = self._top_level(path)
        if top_level is None:
            if unit._is_owned(path):
                return
            raise FileHandlingError(f"不能登记工作区外产物: {path}")
        with self._lock:
            if any(
                top_level == owned.resolve(strict=False) or top_level.is_relative_to(owned.resolve(strict=False))
                for owned in unit.owned_roots
            ):
                return
            if top_level in self._baseline_roots:
                raise FileHandlingError(f"不能接管执行前已存在的产物: {top_level}")
            if top_level in self._claims:
                raise FileHandlingError(f"不能接管其他处理单元的产物: {top_level}")
            self._claims[top_level] = unit.index
            self._reservations.add(top_level)

    def release_owned(self, unit: UnitWorkspace, path: Path) -> None:
        top_level = self._top_level(path)
        if top_level is None:
            return
        with self._lock:
            if self._claims.get(top_level) == unit.index:
                self._claims.pop(top_level, None)
                for reserved in list(self._reservations):
                    if reserved == top_level or reserved.is_relative_to(top_level):
                        self._reservations.discard(reserved)

    def prepare_unit(
        self,
        index: int,
        unit: ExecutionUnit,
        *,
        direct_mode: bool = False,
        move_mode: bool = False,
        reference_mode: bool = False,
        shared: Mapping[str, Any] | None = None,
        unit_workspace: UnitWorkspace | None = None,
    ) -> PreparedWorkspaceUnit:
        workspace = unit_workspace or self.create_unit(index)
        if unit.kind == "line":
            return PreparedWorkspaceUnit(workspace=workspace, input_lines=unit.lines)
        if unit.kind == "none":
            return PreparedWorkspaceUnit(workspace=workspace)

        is_shared = unit.layout == "shared"
        is_batched = unit.layout == "batch"
        if direct_mode and is_shared:
            raise FileHandlingError("scope=0 与 direct_mode 不兼容：shared 需要 output_dir 形成合并树。")
        if direct_mode and is_batched:
            raise FileHandlingError("scope>1 与 direct_mode 不兼容：批次需要独立工作区。")

        if is_batched and not reference_mode:
            with self._lock:
                batch_root = _unique_path_with_reservations(
                    self.root / f"_batch_{index:04d}",
                    self._reservations,
                )
                batch_root.mkdir(parents=True, exist_ok=False)
            workspace.root = batch_root
            workspace.current_path = batch_root
            workspace.owned_roots.add(batch_root.resolve())
            workspace.reserved_paths.add(batch_root.resolve())
            self.claim_owned(workspace, batch_root)

        paths = unit.paths
        first: Path | None = None
        original_source: Path | None = None
        for item in paths:
            source = item.path.resolve()
            if not source.exists():
                raise FileHandlingError(f"输入路径不存在: {source}")
            if direct_mode:
                destination = source
            elif reference_mode:
                source_root = item.source_root.resolve() if item.source_root else None
                if source_root is not None:
                    source_root = source_root.resolve()
                    try:
                        relative = source.relative_to(source_root)
                    except ValueError as exc:
                        raise FileHandlingError(f"文件 {source} 不在 source_root {source_root} 内") from exc
                else:
                    relative = Path(source.name)
                destination = source
                workspace._register_reference(source, relative)
            else:
                source_root = item.source_root.resolve() if item.source_root else None
                if source_root is not None:
                    source_root = source_root.resolve()
                    try:
                        relative = source.relative_to(source_root)
                    except ValueError as exc:
                        raise FileHandlingError(f"文件 {source} 不在 source_root {source_root} 内") from exc
                else:
                    relative = Path(source.name)
                with workspace.allocation_lock:
                    destination = workspace._allocate_destination(relative)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    workspace._register_owned(destination)
                    try:
                        if move_mode:
                            destination = Path(move(str(source), str(destination))).resolve(strict=False)
                        elif source.is_dir():
                            copytree(source, destination, copy_function=copy2)
                        else:
                            copy2(source, destination)
                    except OSError as exc:
                        if not move_mode:
                            self.discard(workspace)
                        raise FileHandlingError(f"输入导入失败: {source}") from exc
            if not reference_mode:
                workspace._register_owned(destination)
            if first is None:
                first = destination
                original_source = source
        workspace.current_path = workspace.root if is_shared or is_batched else (first or workspace.root)
        workspace.refresh()
        source_root = None
        if len(paths) == 1 and paths[0].source_root:
            source_root = paths[0].source_root
        original_input = None if is_shared or is_batched else original_source
        return PreparedWorkspaceUnit(
            workspace=workspace,
            original_input=original_input,
            source_root=source_root,
        )

    def publish(self, unit: UnitWorkspace) -> None:
        unit.publish()
        with self._lock:
            for root in unit.owned_roots:
                top_level = self._top_level(root)
                if top_level is not None and self._claims.get(top_level) == unit.index:
                    self._claims[top_level] = None
                    for reserved in list(self._reservations):
                        if reserved == top_level or reserved.is_relative_to(top_level):
                            self._reservations.discard(reserved)

    def discard(self, unit: UnitWorkspace) -> None:
        for root in sorted(unit.owned_roots, key=lambda path: len(path.parts), reverse=True):
            try:
                root.relative_to(self.root)
            except ValueError:
                continue
            top_level = self._top_level(root)
            if top_level is not None and self._claims.get(top_level) != unit.index:
                continue
            if root.is_dir() and not root.is_symlink():
                rmtree(root, ignore_errors=True)
            else:
                root.unlink(missing_ok=True)
            if top_level is not None:
                self._claims.pop(top_level, None)
                self._reservations.discard(top_level)
        unit.owned_roots.clear()
        unit.referenced_roots.clear()
        unit.refresh()

    def close(self) -> None:
        # Output is the workspace; never remove user-visible results.
        return None

    def __enter__(self) -> ExecutionWorkspace:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
