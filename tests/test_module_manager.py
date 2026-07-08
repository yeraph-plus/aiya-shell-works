"""Module manager: scans modules dir, enforces is_file_module / scope meta."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import ModuleManager


@pytest.fixture()
def modules_dir(tmp_path: Path) -> Path:
    d = tmp_path / "modules"
    d.mkdir()
    return d


def _write(modules_dir: Path, name: str, body: str) -> None:
    (modules_dir / name).write_text(body, encoding="utf-8")


VALID_MODULE = """MODULE_META = {
    "slug": "demo",
    "name": "Demo",
    "core_version": "2.0.0",
    "tags": ["a", "b"],
    "is_file_module": True,
}
CONFIG_SCHEMA = {"type": "object", "properties": {}}

def run(ctx, cfg, runtime):
    return ctx
"""


def test_valid_module_is_cached(modules_dir: Path) -> None:
    _write(modules_dir, "demo.py", VALID_MODULE)
    mgr = ModuleManager(modules_dir)
    modules = mgr.scan_modules()
    assert "demo" in modules
    mod = modules["demo"]
    assert mod.is_file_module is True
    assert mod.scope == 1
    assert mod.tags == ("a", "b")
    assert mgr.warnings == []


def test_rescan_rebuilds_cache(modules_dir: Path) -> None:
    _write(modules_dir, "demo.py", VALID_MODULE)
    mgr = ModuleManager(modules_dir)
    mgr.scan_modules()
    _write(modules_dir, "extra.py", VALID_MODULE.replace('"demo"', '"extra"').replace('"Demo"', '"Extra"'))
    # Cached result
    assert "extra" not in mgr.scan_modules()
    # Force rescan
    assert "extra" in mgr.rescan_modules()


def test_module_missing_meta_ignored(modules_dir: Path) -> None:
    _write(modules_dir, "no_meta.py", "def run(ctx, cfg, runtime):\n    return ctx\n")
    mgr = ModuleManager(modules_dir)
    modules = mgr.scan_modules()
    assert "no_meta" not in modules
    assert any("MODULE_META" in w for w in mgr.warnings)


def test_module_invalid_is_file_module_ignored(modules_dir: Path) -> None:
    body = VALID_MODULE.replace('"is_file_module": True', '"is_file_module": "yes"')
    _write(modules_dir, "bad_kind.py", body)
    modules = ModuleManager(modules_dir).scan_modules()
    assert "demo" not in modules


def test_module_missing_is_file_module_ignored(modules_dir: Path) -> None:
    body = VALID_MODULE.replace('    "is_file_module": True,\n', "")
    _write(modules_dir, "no_kind.py", body)
    assert "demo" not in ModuleManager(modules_dir).scan_modules()


def test_module_line_kind_accepted(modules_dir: Path) -> None:
    body = VALID_MODULE.replace('"is_file_module": True', '"is_file_module": False')
    _write(modules_dir, "line_mod.py", body)
    modules = ModuleManager(modules_dir).scan_modules()
    assert "demo" in modules
    assert modules["demo"].is_file_module is False


def test_module_invalid_scope_ignored(modules_dir: Path) -> None:
    body = VALID_MODULE.replace("}\nCONFIG_SCHEMA", '"scope": -1,\n}\nCONFIG_SCHEMA')
    _write(modules_dir, "bad_scope.py", body)
    assert "demo" not in ModuleManager(modules_dir).scan_modules()


def test_module_large_scope_accepted(modules_dir: Path) -> None:
    body = VALID_MODULE.replace("}\nCONFIG_SCHEMA", '"scope": 999,\n}\nCONFIG_SCHEMA')
    _write(modules_dir, "big_scope.py", body)
    modules = ModuleManager(modules_dir).scan_modules()
    assert "demo" in modules
    assert modules["demo"].scope == 999


def test_module_scope_missing_defaults_to_1(modules_dir: Path) -> None:
    _write(modules_dir, "demo.py", VALID_MODULE)
    modules = ModuleManager(modules_dir).scan_modules()
    assert "demo" in modules
    assert modules["demo"].scope == 1


def test_module_missing_run_ignored(modules_dir: Path) -> None:
    body = VALID_MODULE.split("\ndef run")[0]
    _write(modules_dir, "no_run.py", body)
    assert "demo" not in ModuleManager(modules_dir).scan_modules()


def test_module_duplicate_slug_rejected(modules_dir: Path) -> None:
    _write(modules_dir, "a.py", VALID_MODULE)
    _write(modules_dir, "b.py", VALID_MODULE)  # same slug "demo"
    mgr = ModuleManager(modules_dir)
    mgr.scan_modules()
    assert any("重复" in w for w in mgr.warnings)


def test_module_parent_reference_unknown_logged(modules_dir: Path) -> None:
    body = VALID_MODULE.replace("}\nCONFIG_SCHEMA", '"parent": "ghost"},\nCONFIG_SCHEMA')
    _write(modules_dir, "with_parent.py", body)
    mgr = ModuleManager(modules_dir)
    mgr.scan_modules()
    assert any("parent" in w for w in mgr.warnings)


def test_module_config_schema_invalid_rejects(modules_dir: Path) -> None:
    body = VALID_MODULE.replace('{"type": "object", "properties": {}}', '{"type": "weird", "properties": {}}')
    _write(modules_dir, "bad_schema.py", body)
    assert "demo" not in ModuleManager(modules_dir).scan_modules()


def test_module_missing_dir_warns(tmp_path: Path) -> None:
    mgr = ModuleManager(tmp_path / "nope")
    assert mgr.scan_modules() == {}
    assert any("目录不存在" in w for w in mgr.warnings)


def test_module_exec_failure_is_warning(modules_dir: Path) -> None:
    bad = "raise RuntimeError('boom')\n"
    _write(modules_dir, "boom.py", bad + VALID_MODULE)
    mgr = ModuleManager(modules_dir)
    mgr.scan_modules()
    assert any("导入模块失败" in w for w in mgr.warnings)
