"""Terminal sessions: cross-platform PTY spawner with stream dispatch.

Platform routing:

* win32 — prefer ``pywinpty.PtyProcess`` (optional dependency)
* posix — use ``pty.fork()`` from stdlib
* fallback — ``subprocess.Popen`` plus ``communicate``; no interactive stdin

All platforms forward output to the runtime's EventBus as
``terminal:*`` events, so callers see one uniform contract.  The GUI layer
attaches its own listener to the bus, CLI layers just see them in the JSONL
sink — both receive the same ``terminal:output`` payload.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .runtime import PipelineRuntime

LOGGER = logging.getLogger(__name__)

_ANSI_STRIP_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[@-_]")


@dataclass(slots=True, frozen=True)
class TerminalResult:
    """Outcome of an external command execution."""

    exit_code: int
    output_text: str = ""

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0


class TerminalSessionRegistry:
    """Process-level registry of live terminal sessions, scoped per runtime.

    The legacy implementation kept a module-level ``_sessions`` dict which
    broke under mulitprocess spawning.  Moving it onto the runtime keeps one
    process's registrations private to its runtime instance — the foundation
    for a future ``multiprocessing.Pool`` transport.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()

    def register(self, session: TerminalSession) -> None:
        with self._lock:
            self._sessions[session.id] = session

    def unregister(self, session: TerminalSession) -> None:
        with self._lock:
            self._sessions.pop(session.id, None)

    def get(self, session_id: str) -> TerminalSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def close_all(self) -> None:
        """Terminate every outstanding session (runtime shutdown)."""

        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                session.terminate()
            except Exception:  # pragma: no cover
                pass

    def __len__(self) -> int:
        return len(self._sessions)


class TerminalSession:
    """Spawn ``cmd`` inside a PTY (where supported) and stream output."""

    def __init__(
        self,
        cmd: list[str],
        *,
        runtime: PipelineRuntime,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        exit_pattern: str | None = None,
        exit_action: str = "write_newline",
    ) -> None:
        if not cmd:
            raise ValueError("TerminalSession requires a non-empty command list.")
        self.id: str = uuid.uuid4().hex
        self.cmd: list[str] = list(cmd)
        self.cwd = Path(cwd) if cwd else None
        defaults_env = dict(os.environ)
        defaults_env.setdefault("PYTHONIOENCODING", "utf-8")
        defaults_env.setdefault("PYTHONUTF8", "1")
        # Normalize/extend with caller overrides.
        _custom_env = env or {}
        defaults_env.update({k: str(v) for k, v in _custom_env.items()})
        self.env: dict[str, str] = defaults_env
        self._runtime = runtime
        self._exit_pattern = exit_pattern
        self._exit_action = exit_action
        self._exit_code: int | None = None
        self._process: Any = None
        self._buf = ""
        self._pattern_matched = False
        self._output_text = ""

        # Winpty handle / subprocess handle live on the chosen backend.
        self._backend: str = "auto"
        self._child_stream: Any = None  # subprocess.Popen fallback pipe

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    def run(self) -> TerminalResult:
        """Run the command and block until termination is detected."""

        try:
            if sys.platform == "win32":
                return self._run_winpty()
            elif sys.platform.startswith(("linux", "darwin", "freebsd")):
                return self._run_posix_pty()
            return self._run_subprocess_fallback()
        finally:
            self._emit_finished(self._exit_code if self._exit_code is not None else -1)

    # ------------------------------------------------------------------
    # Winpty backend
    # ------------------------------------------------------------------

    def _run_winpty(self) -> TerminalResult:
        try:
            from winpty import PtyProcess
        except ImportError:
            return self._run_subprocess_fallback(print_warning=True)

        self._backend = "winpty"
        try:
            self._process = PtyProcess.spawn(
                self.cmd,
                cwd=str(self.cwd) if self.cwd else None,
                env=self.env,
            )
            self._emit_started()
            while self._process.isalive():
                try:
                    raw = self._process.read(4096)
                except (OSError, EOFError):
                    break
                if raw:
                    self._consume_and_emit(raw)
            self._exit_code = self._process.wait()
        except Exception as exc:
            if self._process is None:
                LOGGER.warning("winpty spawn failed, falling back to subprocess: %s", exc)
                return self._run_subprocess_fallback(
                    print_warning=True,
                    warning_text=f"winpty 启动失败，回退到子进程模式（无交互 stdin）: {exc}",
                )
            LOGGER.exception("winpty runtime failed: %s", exc)
            self._exit_code = -127
            raise
        return TerminalResult(
            exit_code=self._exit_code if self._exit_code is not None else -1,
            output_text=self._output_text,
        )

    # ------------------------------------------------------------------
    # Posix pty backend
    # ------------------------------------------------------------------

    def _run_posix_pty(self) -> TerminalResult:
        import pty

        try:
            pid, fd = pty.fork()
        except OSError as exc:  # pragma: no cover
            LOGGER.warning("pty.fork failed, falling back to subprocess: %s", exc)
            return self._run_subprocess_fallback(print_warning=True)

        if pid == 0:
            # Child process
            try:
                if self.cwd:
                    os.chdir(self.cwd)
                for k, v in self.env.items():
                    os.environ[k] = v
                os.execvp(self.cmd[0], self.cmd)
            except OSError:  # pragma: no cover
                os._exit(127)
        # Parent process
        self._backend = "posix-pty"
        self._emit_started()
        self._child_stream = fd
        try:
            while True:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                self._consume_and_emit(data.decode("utf-8", errors="replace"))
            _, status = os.waitpid(pid, 0)
            if os.WIFEXITED(status):
                self._exit_code = os.WEXITSTATUS(status)
            elif os.WIFSIGNALED(status):
                self._exit_code = -1
            else:
                self._exit_code = -1
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        return TerminalResult(
            exit_code=self._exit_code if self._exit_code is not None else -1,
            output_text=self._output_text,
        )

    # ------------------------------------------------------------------
    # Subprocess fallback (e.g. headless / no winpty installed)
    # ------------------------------------------------------------------

    def _run_subprocess_fallback(
        self,
        *,
        print_warning: bool = False,
        warning_text: str | None = None,
    ) -> TerminalResult:
        import subprocess

        if print_warning:
            self._runtime.log(
                "terminal",
                "warning",
                warning_text or "原生 PTY 不可用，回退到子进程模式（无交互 stdin）。",
                {"session_id": self.id, "command": " ".join(self.cmd)},
            )
        self._backend = "subprocess"
        try:
            proc = subprocess.Popen(
                self.cmd,
                cwd=str(self.cwd) if self.cwd else None,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self._process = proc
            self._emit_started()
            if proc.stdout is not None:
                for line in proc.stdout:
                    self._consume_and_emit(line)
            self._exit_code = proc.wait()
        except OSError as exc:
            LOGGER.exception("subprocess fallback failed: %s", exc)
            self._exit_code = -127
            raise
        return TerminalResult(
            exit_code=self._exit_code if self._exit_code is not None else -1,
            output_text=self._output_text,
        )

    # ------------------------------------------------------------------
    # Streaming helpers
    # ------------------------------------------------------------------

    def _consume_and_emit(self, raw: str) -> None:
        clean = _ANSI_STRIP_RE.sub("", raw)
        if not clean:
            return
        self._output_text += clean
        self._runtime.log(
            "terminal",
            "message",
            "terminal:output",
            {"session_id": self.id, "text": clean},
        )
        self._check_exit_pattern(clean)

    def _check_exit_pattern(self, data: str) -> None:
        if self._exit_pattern is None or self._pattern_matched:
            return
        self._buf += data
        if self._exit_pattern in self._buf:
            self._pattern_matched = True
            self._runtime.log(
                "terminal",
                "message",
                "terminal:close",
                {"session_id": self.id},
            )
            if self._exit_action == "write_newline":
                self.write("\n")
            elif self._exit_action == "terminate":
                self.terminate()

    def _emit_started(self) -> None:
        self._runtime.log(
            "terminal",
            "message",
            "terminal:started",
            {"session_id": self.id, "command": " ".join(self.cmd), "backend": self._backend},
        )

    def _emit_finished(self, exit_code: int) -> None:
        self._runtime.log(
            "terminal",
            "message",
            "terminal:finished",
            {"session_id": self.id, "exit_code": exit_code},
        )

    def write(self, data: str) -> None:
        """Write data to the child stdin (GUI interactive path)."""

        proc = self._process
        if proc is None:
            return
        if self._backend == "winpty":
            try:
                proc.write(data)
            except OSError:
                pass
        elif self._backend == "posix-pty":
            # Not implemented interactively for posix in this pass — winpty + GUI is the primary use case.
            return
        elif self._backend == "subprocess":
            stdin = getattr(proc, "stdin", None)
            if stdin is not None:
                try:
                    stdin.write(data)
                    stdin.flush()
                except OSError:
                    pass

    def terminate(self) -> None:
        """Terminate the underlying process (idempotent)."""

        proc = self._process
        if proc is None:
            return
        try:
            if self._backend == "winpty":
                proc.terminate(force=True)
            elif self._backend == "subprocess":
                proc.terminate()
        except OSError:
            pass


def get_session(runtime: PipelineRuntime, session_id: str) -> TerminalSession | None:
    """Convenience accessor used by GUI layers to find an interactive terminal."""

    return runtime.sessions.get(session_id)
