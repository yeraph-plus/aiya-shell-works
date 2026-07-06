from __future__ import annotations

from pathlib import Path

from core import ModuleManager


def write_module(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_module_manager_loads_valid_modules(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "rename_file.py",
        """
MODULE_META = {"slug": "rename-file", "name": "Rename File", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {"prefix": {"type": "str", "default": ""}},
}

def run(context, config):
    return {"context": context, "config": config}
""".strip(),
    )

    manager = ModuleManager(modules_dir)

    modules = manager.scan_modules()

    assert list(modules) == ["rename-file"]
    assert manager.warnings == []
    assert modules["rename-file"].module_meta["name"] == "Rename File"
    assert modules["rename-file"].config_schema["type"] == "object"
    assert callable(modules["rename-file"].run)


def test_module_manager_ignores_invalid_modules_and_collects_warnings(
    tmp_path: Path,
) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "valid.py",
        """
MODULE_META = {"slug": "valid", "name": "Valid", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )
    write_module(
        modules_dir / "missing_meta.py",
        """
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )
    write_module(
        modules_dir / "bad_entry.py",
        """
MODULE_META = {"slug": "bad-entry", "name": "Bad Entry", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}
run = "not-callable"
""".strip(),
    )
    write_module(
        modules_dir / "broken_import.py",
        """
raise RuntimeError("boom")
""".strip(),
    )

    manager = ModuleManager(modules_dir)

    modules = manager.scan_modules()

    assert list(modules) == ["valid"]
    assert len(manager.warnings) == 3
    assert any("MODULE_META" in warning for warning in manager.warnings)
    assert any("run" in warning for warning in manager.warnings)
    assert any("导入模块失败" in warning for warning in manager.warnings)


def test_module_manager_uses_cache_until_rescan(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    module_path = modules_dir / "sample.py"
    write_module(
        module_path,
        """
MODULE_META = {"slug": "sample", "name": "Sample v1", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return "v1"
""".strip(),
    )

    manager = ModuleManager(modules_dir)

    first_scan = manager.get_module("sample")
    assert first_scan is not None
    assert first_scan.module_meta["name"] == "Sample v1"

    write_module(
        module_path,
        """
MODULE_META = {"slug": "sample", "name": "Sample v2", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {"flag": {"type": "bool", "default": False}}}

def run(context, config):
    return "v2"
""".strip(),
    )

    cached_module = manager.get_module("sample")
    assert cached_module is not None
    assert cached_module.module_meta["name"] == "Sample v1"
    assert cached_module.run(None, None) == "v1"

    refreshed_modules = manager.rescan_modules()

    assert refreshed_modules["sample"].module_meta["name"] == "Sample v2"
    assert refreshed_modules["sample"].config_schema["properties"]["flag"]["type"] == "bool"
    assert refreshed_modules["sample"].run(None, None) == "v2"


def test_module_manager_rejects_invalid_config_schema(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "bad_schema.py",
        """
MODULE_META = {"slug": "bad-schema", "name": "Bad Schema", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {"mode": {"type": "string"}}}

def run(context, config):
    return context
""".strip(),
    )

    manager = ModuleManager(modules_dir)

    modules = manager.scan_modules()

    assert modules == {}
    assert any("CONFIG_SCHEMA 不符合规格" in warning for warning in manager.warnings)


def test_module_manager_warns_on_duplicate_slug(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "first.py",
        """
MODULE_META = {"slug": "shared", "name": "First", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return "first"
""".strip(),
    )
    write_module(
        modules_dir / "second.py",
        """
MODULE_META = {"slug": "shared", "name": "Second", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return "second"
""".strip(),
    )

    manager = ModuleManager(modules_dir)

    modules = manager.scan_modules()

    assert list(modules) == ["shared"]
    assert modules["shared"].module_meta["name"] == "First"
    assert any("模块 slug 重复" in warning for warning in manager.warnings)


def test_module_manager_rejects_missing_core_version(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "no_version.py",
        """
MODULE_META = {"slug": "nv", "name": "No Version", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )

    manager = ModuleManager(modules_dir)
    modules = manager.scan_modules()

    assert modules == {}
    assert any("core_version" in warning for warning in manager.warnings)


def test_module_manager_rejects_invalid_mode(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "bad_mode.py",
        """
MODULE_META = {"slug": "bm", "name": "Bad Mode", "core_version": "1.0.0", "tags": ["test"], "mode": ["invalid"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )

    manager = ModuleManager(modules_dir)
    modules = manager.scan_modules()

    assert modules == {}
    assert any("mode" in warning for warning in manager.warnings)


def test_module_manager_rejects_empty_mode_list(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "empty_mode.py",
        """
MODULE_META = {"slug": "em", "name": "Empty Mode", "core_version": "1.0.0", "tags": ["test"], "mode": []}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )

    manager = ModuleManager(modules_dir)
    modules = manager.scan_modules()

    assert modules == {}
    assert any("mode" in warning for warning in manager.warnings)


def test_module_manager_available_tags_is_sorted_unique(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "a.py",
        """
MODULE_META = {"slug": "alpha", "name": "Alpha", "core_version": "1.0.0", "tags": ["image", "resize"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )
    write_module(
        modules_dir / "b.py",
        """
MODULE_META = {"slug": "beta", "name": "Beta", "core_version": "1.0.0", "tags": ["text", "image"], "mode": ["none"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )

    manager = ModuleManager(modules_dir)
    manager.scan_modules()

    tags = manager.available_tags
    assert tags == ["image", "resize", "text"]


# ---------------------------------------------------------------------------
# Additional boundary tests
# ---------------------------------------------------------------------------


def test_module_manager_scans_nonexistent_dir_returns_empty(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-dir"
    manager = ModuleManager(missing)
    modules = manager.scan_modules()
    assert modules == {}
    assert any("模块目录不存在" in w for w in manager.warnings)


def test_module_manager_scans_file_not_dir(tmp_path: Path) -> None:
    f = tmp_path / "not-a-dir"
    f.write_text("data", encoding="utf-8")
    manager = ModuleManager(f)
    modules = manager.scan_modules()
    assert modules == {}
    assert any("模块路径不是目录" in w for w in manager.warnings)


def test_module_manager_skips_init_py(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "__init__.py",
        """
MODULE_META = {"slug": "init", "name": "Init", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )
    manager = ModuleManager(modules_dir)
    modules = manager.scan_modules()
    assert modules == {}


def test_module_manager_get_module_nonexistent(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    manager = ModuleManager(modules_dir)
    manager.scan_modules()
    assert manager.get_module("no-such-slug") is None


def test_module_manager_warnings_is_copy(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    manager = ModuleManager(modules_dir)
    manager.scan_modules()
    warnings = manager.warnings
    warnings.append("mutated")
    assert "mutated" not in manager.warnings


def test_module_manager_rejects_tags_with_empty_string(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "bad_tags.py",
        """
MODULE_META = {"slug": "bt", "name": "Bad Tags", "core_version": "1.0.0", "tags": [""], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )
    manager = ModuleManager(modules_dir)
    modules = manager.scan_modules()
    assert modules == {}
    assert any("tags" in w for w in manager.warnings)


def test_module_manager_rejects_whitespace_slug(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "bad_slug.py",
        """
MODULE_META = {"slug": "   ", "name": "Bad Slug", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )
    manager = ModuleManager(modules_dir)
    modules = manager.scan_modules()
    assert modules == {}
    assert any("slug" in w for w in manager.warnings)


def test_module_manager_rejects_whitespace_name(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "bad_name.py",
        """
MODULE_META = {"slug": "bn", "name": "  ", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )
    manager = ModuleManager(modules_dir)
    modules = manager.scan_modules()
    assert modules == {}
    assert any("name" in w for w in manager.warnings)


def test_module_manager_rejects_whitespace_core_version(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "bad_version.py",
        """
MODULE_META = {"slug": "bv", "name": "Bad Version", "core_version": " ", "tags": ["test"], "mode": ["file"]}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )
    manager = ModuleManager(modules_dir)
    modules = manager.scan_modules()
    assert modules == {}
    assert any("core_version" in w for w in manager.warnings)


def test_module_manager_rejects_parent_not_string(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "bad_parent.py",
        """
MODULE_META = {"slug": "bp", "name": "Bad Parent", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"], "parent": 123}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )
    manager = ModuleManager(modules_dir)
    manager.scan_modules()
    assert any("parent" in w for w in manager.warnings)


def test_module_manager_accepts_valid_parent(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    write_module(
        modules_dir / "child.py",
        """
MODULE_META = {"slug": "child", "name": "Child", "core_version": "1.0.0", "tags": ["test"], "mode": ["file"], "parent": "parent-mod"}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(context, config):
    return None
""".strip(),
    )
    manager = ModuleManager(modules_dir)
    modules = manager.scan_modules()
    assert "child" in modules
    assert modules["child"].parent == "parent-mod"


def test_module_manager_available_tags_empty(tmp_path: Path) -> None:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    manager = ModuleManager(modules_dir)
    assert manager.available_tags == []
