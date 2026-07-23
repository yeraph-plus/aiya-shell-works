"""Discovers and validates processing modules from a directory.

Each ``modules/<name>.py`` must expose three module-level objects:

* ``MODULE_META: dict`` — slug, name, core_version, tags, access, platforms, scope, …
* ``CONFIG_SCHEMA: dict`` — JSON-style schema for the GUI form / validator
* ``run(ctx, cfg, runtime)`` — entry point called by the executor
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, cast

from .config_schema import validate_config_schema
from .context import PipelineContext
from .version import CORE_VERSION

LOGGER = logging.getLogger(__name__)

VALID_ATOMS = ("file", "folder", "line", "none")  # GUI metadata only — no kernel constraint
# scope: 0 = shared, 1 = per-unit, >1 = fixed-size batch
VALID_SCOPES = ">=0"
ModuleAccess = Literal["read", "read_write"]
VALID_MODULE_ACCESS = frozenset({"read", "read_write"})
VALID_MODULE_PLATFORMS = frozenset({"windows", "linux", "darwin"})


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    return sys.platform


@dataclass(frozen=True, slots=True)
class ModuleDefinition:
    """A discovered, validation-passing module."""

    slug: str
    module_meta: dict[str, Any]
    config_schema: dict[str, Any]
    run: Callable[..., PipelineContext | None]
    path: Path
    module: ModuleType
    core_version: str = "2.0.0"
    tags: tuple[str, ...] = ()
    access: ModuleAccess = "read_write"
    platforms: tuple[str, ...] | None = None
    scope: int = 1
    parent: str | None = None

    def supports_platform(self, platform: str | None = None) -> bool:
        return self.platforms is None or (platform or current_platform()) in self.platforms


class ModuleManager:
    """Scan, validate and cache modules from a directory."""

    def __init__(self, modules_dir: str | Path) -> None:
        self.modules_dir = Path(modules_dir)
        self._cache: dict[str, ModuleDefinition] = {}
        self._warnings: list[str] = []
        self._scanned = False

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

    @property
    def available_tags(self) -> list[str]:
        tags: set[str] = set()
        for d in self._cache.values():
            tags.update(t for t in d.tags if t)
        return sorted(tags)

    def scan_modules(self, *, force: bool = False) -> dict[str, ModuleDefinition]:
        """Scan and cache.  Cached on first call."""

        if self._scanned and not force:
            return dict(self._cache)
        self._cache = {}
        self._warnings = []
        self._scanned = True

        if not self.modules_dir.exists():
            self._warn(f"模块目录不存在，已跳过扫描: {self.modules_dir}")
            return {}
        if not self.modules_dir.is_dir():
            self._warn(f"模块路径不是目录，已跳过扫描: {self.modules_dir}")
            return {}

        for path in sorted(self.modules_dir.glob("*.py")):
            if path.name == "__init__.py":
                continue
            module = self._load_module(path)
            if module is None:
                continue
            definition = self._validate_module(module, path)
            if definition is None:
                continue
            if definition.slug in self._cache:
                self._warn(
                    f"模块 slug 重复，已忽略后加载的模块: {definition.slug} "
                    f"({path} 与 {self._cache[definition.slug].path})"
                )
                continue
            self._cache[definition.slug] = definition

        self._validate_parents()
        return dict(self._cache)

    def rescan_modules(self) -> dict[str, ModuleDefinition]:
        return self.scan_modules(force=True)

    def get_modules(self) -> dict[str, ModuleDefinition]:
        return self.scan_modules()

    def get_module(self, slug: str) -> ModuleDefinition | None:
        return self.get_modules().get(slug)

    # ------------------------------------------------------------------

    def _load_module(self, path: Path) -> ModuleType | None:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        name = f"shell_worker_dynamic_modules.{path.stem}_{digest}"
        module = ModuleType(name)
        module.__file__ = str(path)
        try:
            code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
            exec(code, module.__dict__)
        except Exception as exc:
            self._warn(f"导入模块失败，已忽略 {path}: {exc}")
            return None
        return module

    def _validate_module(self, module: ModuleType, path: Path) -> ModuleDefinition | None:
        meta = getattr(module, "MODULE_META", None)
        if not isinstance(meta, dict):
            self._warn(f"模块缺少合法的 MODULE_META 字典，已忽略: {path}")
            return None

        slug = meta.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            self._warn(f"MODULE_META.slug 缺失或非法，已忽略: {path}")
            return None
        name = meta.get("name")
        if not isinstance(name, str) or not name.strip():
            self._warn(f"MODULE_META.name 缺失或非法，已忽略: {path}")
            return None
        core_version = meta.get("core_version")
        if not isinstance(core_version, str) or not core_version.strip():
            self._warn(f"MODULE_META.core_version 缺失或非法，已忽略: {path}")
            return None
        if core_version.strip() != CORE_VERSION:
            self._warn(
                f"MODULE_META.core_version 与内核不兼容，已忽略: {path} "
                f"(module={core_version.strip()}, core={CORE_VERSION})"
            )
            return None

        raw_tags = meta.get("tags", [])
        if not isinstance(raw_tags, list) or not all(isinstance(t, str) and t.strip() for t in raw_tags):
            self._warn(f"MODULE_META.tags 必须是非空字符串列表，已忽略: {path}")
            return None
        tags = tuple(t.strip() for t in raw_tags)

        raw_access = meta.get("access", "read_write")
        if not isinstance(raw_access, str) or raw_access not in VALID_MODULE_ACCESS:
            self._warn(f"MODULE_META.access 必须是 'read' 或 'read_write'，已忽略: {path}")
            return None
        access = cast(ModuleAccess, raw_access)

        raw_platforms = meta.get("platforms")
        platforms: tuple[str, ...] | None
        if raw_platforms is None:
            platforms = None
        elif (
            isinstance(raw_platforms, list)
            and raw_platforms
            and all(isinstance(item, str) and item in VALID_MODULE_PLATFORMS for item in raw_platforms)
            and len(set(raw_platforms)) == len(raw_platforms)
        ):
            platforms = tuple(raw_platforms)
        else:
            supported = ", ".join(sorted(VALID_MODULE_PLATFORMS))
            self._warn(f"MODULE_META.platforms 必须为 None 或不重复的非空列表 ({supported})，已忽略: {path}")
            return None

        raw_scope = meta.get("scope", 1)
        if not isinstance(raw_scope, int) or raw_scope < 0:
            self._warn(f"MODULE_META.scope 必须是 >= 0 的整数，已忽略: {path}")
            return None
        scope = raw_scope

        parent = meta.get("parent")
        if parent is not None and (not isinstance(parent, str) or not parent.strip()):
            self._warn(f"MODULE_META.parent 提供时必须是非空字符串，已忽略: {path}")
            parent = None
        if isinstance(parent, str):
            parent = parent.strip() or None

        schema = getattr(module, "CONFIG_SCHEMA", None)
        if not isinstance(schema, dict):
            self._warn(f"模块缺少合法的 CONFIG_SCHEMA 字典，已忽略: {path}")
            return None
        valid_schema, schema_errors = validate_config_schema(schema)
        if not valid_schema:
            self._warn(f"CONFIG_SCHEMA 不符合规格，已忽略: {path} ({'；'.join(schema_errors)})")
            return None

        run = getattr(module, "run", None)
        if not callable(run):
            self._warn(f"模块缺少可调用的 run 入口，已忽略: {path}")
            return None
        try:
            inspect.signature(run).bind(object(), {}, object())
        except (TypeError, ValueError) as exc:
            self._warn(f"模块 run 入口必须接受 (ctx, cfg, runtime)，已忽略: {path} ({exc})")
            return None

        return ModuleDefinition(
            slug=slug.strip(),
            module_meta=dict(meta),
            config_schema=dict(schema),
            run=run,
            path=path,
            module=module,
            core_version=core_version.strip(),
            tags=tags,
            access=access,
            platforms=platforms,
            scope=scope,
            parent=parent,
        )

    def _validate_parents(self) -> None:
        for slug, definition in self._cache.items():
            if definition.parent and definition.parent not in self._cache:
                self._warn(f"模块 '{slug}' 声明的 parent '{definition.parent}' 不存在于已扫描的模块中。")
        cycles: set[str] = set()
        for slug in self._cache:
            chain: list[str] = []
            current: str | None = slug
            while current is not None and current in self._cache:
                if current in chain:
                    cycles.update(chain[chain.index(current) :])
                    break
                chain.append(current)
                current = self._cache[current].parent
        for slug in sorted(cycles):
            self._warn(f"模块 '{slug}' 的 parent 关系形成循环，已忽略该模块。")
            self._cache.pop(slug, None)

    def _warn(self, message: str) -> None:
        self._warnings.append(message)
        LOGGER.warning(message)
