from __future__ import annotations

import ast
from pathlib import Path

import core

CORE_DIR = Path(__file__).resolve().parents[1] / "core"


def _runtime_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
                return
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.level == 1 and node.module:
                imports.add(node.module.split(".")[0])

    Visitor().visit(tree)
    return imports


def test_core_runtime_import_graph_is_acyclic() -> None:
    modules = {path.stem: path for path in CORE_DIR.glob("*.py") if path.name != "__init__.py"}
    graph = {name: _runtime_imports(path) & modules.keys() for name, path in modules.items()}

    def visit(node: str, stack: tuple[str, ...]) -> None:
        assert node not in stack, " -> ".join((*stack, node))
        for dependency in graph[node]:
            visit(dependency, (*stack, node))

    for module in graph:
        visit(module, ())


def test_low_level_workspace_symbols_are_not_package_api() -> None:
    for name in {
        "ExecutionWorkspace",
        "UnitWorkspace",
        "WorkingCopier",
        "build_lines_units",
        "build_path_units",
        "make_unique_path",
        "units_from_plan",
    }:
        assert not hasattr(core, name)


def test_scheduler_does_not_import_executor_private_symbols() -> None:
    tree = ast.parse((CORE_DIR / "scheduler.py").read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "executor"
        for alias in node.names
    }
    assert not any(name.startswith("_") for name in imported)
