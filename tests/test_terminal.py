"""Terminal: cross-platform PTY / subprocess spawn via runtime.spawn.

Tests assume we run on win32 here; posix path needs adjustment in CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core import PipelineRuntime
from core.terminal import TerminalResult, TerminalSession, TerminalSessionRegistry, _windows_subprocess_kwargs

REPO_ROOT = Path(__file__).resolve().parents[1]


def _mock_tool() -> Path:
    return REPO_ROOT / "resources" / ("mock_tool.bat" if sys.platform == "win32" else "mock_tool.sh")


@pytest.fixture()
def runtime() -> PipelineRuntime:
    return PipelineRuntime()


def test_terminal_result_is_success_only_when_zero() -> None:
    assert TerminalResult(exit_code=0).is_success
    assert not TerminalResult(exit_code=1).is_success


def test_terminal_session_registry_register_unregister() -> None:
    reg = TerminalSessionRegistry()
    sess = TerminalSession(cmd=["true"], runtime=PipelineRuntime())  # never run
    reg.register(sess)
    assert len(reg) == 1
    assert reg.get(sess.id) is sess
    reg.unregister(sess)
    assert len(reg) == 0


def test_terminal_session_rejects_empty_command() -> None:
    with pytest.raises(ValueError):
        TerminalSession(cmd=[], runtime=PipelineRuntime())


def test_windows_subprocess_is_hidden_by_default() -> None:
    options = _windows_subprocess_kwargs(show_console=False)
    assert options["creationflags"] & 0x08000000


def test_windows_subprocess_console_can_be_requested() -> None:
    options = _windows_subprocess_kwargs(show_console=True)
    assert not options["creationflags"] & 0x08000000


def test_runtime_spawn_emits_started_output_finished(tmp_path: Path, runtime: PipelineRuntime) -> None:
    tool = _mock_tool()
    if not tool.exists():  # pragma: no cover
        pytest.skip(f"mock tool missing: {tool}")
    out = tmp_path / "input.txt"
    out.write_text("data", encoding="utf-8")
    if sys.platform != "win32":
        # ensure .sh is executable
        tool.chmod(0o755)
    events: list = []
    runtime.subscribe(events.append)

    result = runtime.spawn([str(tool), str(out)])
    assert result.is_success

    types = [e.text for e in events]
    assert "terminal:started" in types
    assert "terminal:output" in types
    assert "terminal:finished" in types
    finish_event = [e for e in events if e.text == "terminal:finished"][0]
    assert finish_event.data["exit_code"] == 0


def test_runtime_spawn_session_registered_during_run(tmp_path: Path) -> None:
    """``TerminalSessionRegistry.register`` is called at the start of ``spawn``."""

    runtime = PipelineRuntime()
    tool = _mock_tool()
    if not tool.exists():  # pragma: no cover
        pytest.skip("mock tool missing")
    out = tmp_path / "x.txt"
    out.write_text("d", encoding="utf-8")
    saw_session_count: list[int] = []

    def listener(event):
        # While ``terminal:started`` is firing, the session should already
        # be registered (because we register before run() emits it).
        if event.text == "terminal:started":
            saw_session_count.append(len(runtime.sessions))

    runtime.subscribe(listener)
    runtime.spawn([str(tool), str(out)])
    assert saw_session_count == [1]


def test_runtime_spawn_nonexistent_command_fails(tmp_path: Path) -> None:
    runtime = PipelineRuntime()
    if sys.platform == "win32":
        # subprocess will raise FileNotFoundError on spawn → runtime passes it through.
        with pytest.raises((OSError, FileNotFoundError)):
            runtime.spawn([str(tmp_path / "nope-nope.exe")])
    else:
        # posix rejects with raise; either message helps debug.
        with pytest.raises((OSError, FileNotFoundError, IndexError)):
            runtime.spawn(["/this/does/not/exist/anywhere"])


def test_runtime_sessions_cleared_after_run(tmp_path: Path) -> None:
    runtime = PipelineRuntime()
    tool = _mock_tool()
    if not tool.exists():  # pragma: no cover
        pytest.skip("mock tool missing")
    out = tmp_path / "x.txt"
    out.write_text("y", encoding="utf-8")
    runtime.spawn([str(tool), str(out)])
    # After spawn returns, the session unregisters automatically.
    assert len(runtime.sessions) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="winpty fallback only applies on Windows")
def test_runtime_spawn_falls_back_when_winpty_spawn_fails(tmp_path: Path, monkeypatch) -> None:
    runtime = PipelineRuntime()
    tool = _mock_tool()
    if not tool.exists():  # pragma: no cover
        pytest.skip("mock tool missing")
    out = tmp_path / "x.txt"
    out.write_text("y", encoding="utf-8")

    import winpty

    def boom(*args, **kwargs):
        raise winpty.WinptyError("boom")

    monkeypatch.setattr(winpty.PtyProcess, "spawn", staticmethod(boom))
    events: list = []
    runtime.subscribe(events.append)

    result = runtime.spawn([str(tool), str(out)])
    assert result.is_success
    assert any(e.type == "warning" and "回退到子进程模式" in e.text for e in events)
    started = [e for e in events if e.text == "terminal:started"]
    assert started
    assert started[0].data["backend"] == "subprocess"


def test_runtime_close_terminates_outstanding_sessions() -> None:
    """Manually craft a no-op session and verify close_all clears it."""

    reg = TerminalSessionRegistry()
    runtime = PipelineRuntime()
    # Register a fake session (no underlying process)
    sess = TerminalSession(cmd=["sleep", "0"], runtime=runtime)
    reg.register(sess)
    assert len(reg) == 1
    reg.close_all()
    assert len(reg) == 0
