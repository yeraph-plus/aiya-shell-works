"""run-external-tool — file path input + per-unit example module.

Demonstrates the runtime.spawn pipeline for invoking external binaries:
the same path used by FFmpeg / VapourSynth modules, but here against the
minimal ``mock_tool.{bat,sh}`` shipped in ``resources/``.

Highlights:
* ``runtime.spawn([...])`` for cross-platform PTY / subprocess spawn,
* terminal:* events flowing through the EventBus for GUI / CLI log sinks,
* exit-code-driven decision (``result.is_success``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context import PipelineContext
    from core.runtime import PipelineRuntime

MODULE_META = {
    "slug": "verify-run-external-tool",
    "name": "Verify — Run External Tool",
    "description": "Invoke an external CLI binary on each working file using resources/mock_tool.",
    "core_version": "2.0.0",
    "tags": ["example", "external"],
    "is_file_module": True,
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "mock_tool_path": {
            "type": "str",
            "title": "External tool path",
            "default": "",
            "description": "Path to mock_tool.bat (.sh). Empty → auto-detect by platform under ../resources.",
        },
    },
}


def _default_tool_path(project_dir: Path) -> Path:
    name = "mock_tool.bat" if sys.platform == "win32" else "mock_tool.sh"
    return project_dir / "resources" / name


# Repo root = parent of the modules/ directory containing this file.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def run(ctx: PipelineContext, cfg: dict[str, Any], runtime: PipelineRuntime) -> PipelineContext | None:
    raw = (cfg.get("mock_tool_path") or "").strip()
    if raw:
        tool = Path(raw)
    else:
        # Default to repo-root/resources; modules/ is sibling of resources/.
        tool = _default_tool_path(_REPO_ROOT)

    if not tool.exists():
        runtime.log("verify-run-external-tool", "error", f"未找到外部工具: {tool}")
        raise FileNotFoundError(f"mock tool not found: {tool}")

    if sys.platform != "win32":
        try:
            tool.chmod(0o755)
        except OSError:
            pass

    cmd = [str(tool), str(ctx.current.path)]
    runtime.log("verify-run-external-tool", "hint", f"spawn: {' '.join(cmd)}")
    result = runtime.spawn(cmd)
    sidecar_path = Path(f"{ctx.current.path}.done")
    if sidecar_path.exists():
        ctx.adopt(sidecar_path)
    runtime.log(
        "verify-run-external-tool",
        "success" if result.is_success else "error",
        f"exit={result.exit_code} ({Path(cmd[0]).name})",
        {"exit_code": result.exit_code, "command": " ".join(cmd)},
    )
    return ctx
