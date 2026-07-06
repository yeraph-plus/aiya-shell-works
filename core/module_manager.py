"""Module discovery, validation and caching helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .config_schema import validate_config_schema
from . import CORE_VERSION


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModuleDefinition:
    """Represents a validated processing module."""

    slug: str
    module_meta: dict[str, Any]
    config_schema: dict[str, Any]
    run: Callable[..., Any]
    path: Path
    module: ModuleType
    core_version: str = "1.0.0"
    tags: tuple[str, ...] = ()
    mode: tuple[str, ...] = ()
    parent: str | None = None


class ModuleManager:
    """Scan, validate and cache modules from a directory."""

    def __init__(self, modules_dir: str | Path) -> None:
        self.modules_dir = Path(modules_dir)
        self._modules_cache: dict[str, ModuleDefinition] = {}
        self._warnings: list[str] = []
        self._has_scanned = False

    @property
    def warnings(self) -> list[str]:
        """Return a copy of warnings collected during the last scan."""

        return list(self._warnings)

    @property
    def available_tags(self) -> list[str]:
        """Return sorted unique tags across all cached modules."""

        tags: set[str] = set()
        for definition in self._modules_cache.values():
            for tag in definition.tags:
                if tag:
                    tags.add(tag)
        return sorted(tags)

    def scan_modules(self, force: bool = False) -> dict[str, ModuleDefinition]:
        """Scan the modules directory and cache validated modules."""

        if self._has_scanned and not force:
            return dict(self._modules_cache)

        self._modules_cache = {}
        self._warnings = []
        self._has_scanned = True

        if not self.modules_dir.exists():
            self._add_warning(
                f"模块目录不存在，已跳过扫描: {self.modules_dir}"
            )
            return {}

        if not self.modules_dir.is_dir():
            self._add_warning(
                f"模块路径不是目录，已跳过扫描: {self.modules_dir}"
            )
            return {}

        for module_path in sorted(self.modules_dir.glob("*.py")):
            if module_path.name == "__init__.py":
                continue

            module = self._load_module(module_path)
            if module is None:
                continue

            module_definition = self._validate_module(module, module_path)
            if module_definition is None:
                continue

            if module_definition.slug in self._modules_cache:
                existing = self._modules_cache[module_definition.slug]
                self._add_warning(
                    "模块 slug 重复，已忽略后加载的模块: "
                    f"{module_definition.slug} ({module_path} 与 {existing.path})"
                )
                continue

            self._modules_cache[module_definition.slug] = module_definition

        self._validate_parent_references()
        return dict(self._modules_cache)

    def rescan_modules(self) -> dict[str, ModuleDefinition]:
        """Force a full rescan and refresh the cache."""

        return self.scan_modules(force=True)

    def get_modules(self) -> dict[str, ModuleDefinition]:
        """Return cached modules, scanning once on first access."""

        return self.scan_modules()

    def get_module(self, slug: str) -> ModuleDefinition | None:
        """Return a validated module by slug."""

        return self.get_modules().get(slug)

    def _load_module(self, module_path: Path) -> ModuleType | None:
        content_hash = hashlib.sha256(module_path.read_bytes()).hexdigest()
        import_name = f"shell_worker_dynamic_modules.{module_path.stem}_{content_hash}"
        module = ModuleType(import_name)
        module.__file__ = str(module_path)
        try:
            source = module_path.read_text(encoding="utf-8")
            code = compile(source, str(module_path), "exec")
            exec(code, module.__dict__)
        except Exception as exc:  # pragma: no cover - exercised by tests
            self._add_warning(
                f"导入模块失败，已忽略 {module_path}: {exc}"
            )
            return None

        return module

    def _validate_module(
        self, module: ModuleType, module_path: Path
    ) -> ModuleDefinition | None:
        module_meta = getattr(module, "MODULE_META", None)
        if not isinstance(module_meta, dict):
            self._add_warning(
                f"模块缺少合法的 MODULE_META 字典，已忽略: {module_path}"
            )
            return None

        slug = module_meta.get("slug")
        name = module_meta.get("name")
        if not isinstance(slug, str) or not slug.strip():
            self._add_warning(
                f"模块 MODULE_META.slug 缺失或非法，已忽略: {module_path}"
            )
            return None

        if not isinstance(name, str) or not name.strip():
            self._add_warning(
                f"模块 MODULE_META.name 缺失或非法，已忽略: {module_path}"
            )
            return None

        core_version = module_meta.get("core_version")
        if not isinstance(core_version, str) or not core_version.strip():
            self._add_warning(
                f"模块 MODULE_META.core_version 缺失或非法，已忽略: {module_path}"
            )
            return None

        raw_tags = module_meta.get("tags", [])
        if not isinstance(raw_tags, list) or not all(
            isinstance(t, str) and t.strip() for t in raw_tags
        ):
            self._add_warning(
                f"模块 MODULE_META.tags 必须是非空字符串列表，已忽略: {module_path}"
            )
            return None
        tags = tuple(t.strip() for t in raw_tags)

        raw_mode = module_meta.get("mode", [])
        if not isinstance(raw_mode, list) or not raw_mode or not all(
            isinstance(m, str) and m in ("file", "folder", "none", "cycle", "input") for m in raw_mode
        ):
            self._add_warning(
                f"模块 MODULE_META.mode 必须是非空列表且值在 file/folder/none/cycle/input 中，已忽略: {module_path}"
            )
            return None
        mode = tuple(raw_mode)

        parent = module_meta.get("parent")
        if parent is not None and (not isinstance(parent, str) or not parent.strip()):
            self._add_warning(
                f"模块 MODULE_META.parent 提供时必须是非空字符串，已忽略: {module_path}"
            )
            parent = None
        if isinstance(parent, str):
            parent = parent.strip()

        config_schema = getattr(module, "CONFIG_SCHEMA", None)
        if not isinstance(config_schema, dict):
            self._add_warning(
                f"模块缺少合法的 CONFIG_SCHEMA 字典，已忽略: {module_path}"
            )
            return None
        is_valid_schema, schema_errors = validate_config_schema(config_schema)
        if not is_valid_schema:
            details = "；".join(schema_errors)
            self._add_warning(
                f"模块 CONFIG_SCHEMA 不符合规格，已忽略: {module_path} ({details})"
            )
            return None

        run = getattr(module, "run", None)
        if not callable(run):
            self._add_warning(
                f"模块缺少可调用的 run 入口，已忽略: {module_path}"
            )
            return None

        return ModuleDefinition(
            slug=slug.strip(),
            module_meta=dict(module_meta),
            config_schema=dict(config_schema),
            run=run,
            path=module_path,
            module=module,
            core_version=core_version.strip(),
            tags=tags,
            mode=mode,
            parent=parent,
        )

    def _validate_parent_references(self) -> None:
        for slug, definition in self._modules_cache.items():
            if definition.parent is not None and definition.parent not in self._modules_cache:
                self._add_warning(
                    f"模块 '{slug}' 声明的 parent '{definition.parent}' "
                    f"不存在于已扫描的模块中。"
                )

    def _add_warning(self, message: str) -> None:
        self._warnings.append(message)
        LOGGER.warning(message)
