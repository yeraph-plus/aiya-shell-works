"""Cross-platform live terminal sessions for workflow child processes."""

from __future__ import annotations

import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .exceptions import TerminalSpawnError

if TYPE_CHECKING:
    from .runtime import PipelineRuntime

LOGGER = logging.getLogger(__name__)

_ANSI_STRIP_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[@-_]")


@dataclass(slots=True, frozen=True)
class TerminalResult:
    exit_code: int
    output_text: str = ""

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0


class TerminalSessionRegistry:
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
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.terminate()

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


class TerminalSession:
    """A start-once child session with streamed output and controllable stdin."""

    def __init__(
        self,
        cmd: Sequence[str] | str,
        *,
        runtime: PipelineRuntime,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        exit_pattern: str | None = None,
        exit_action: str = "write_newline",
        shell: bool = False,
        on_finished: Callable[[TerminalSession], None] | None = None,
    ) -> None:
        if isinstance(cmd, str):
            if not cmd:
                raise ValueError("TerminalSession requires a non-empty command.")
        elif not cmd:
            raise ValueError("TerminalSession requires a non-empty command list.")
        self.id = uuid.uuid4().hex
        self.cmd: list[str] | str = cmd if isinstance(cmd, str) else list(cmd)
        self.cwd = Path(cwd) if cwd else None
        self.env = dict(os.environ)
        self.env.setdefault("PYTHONIOENCODING", "utf-8")
        self.env.setdefault("PYTHONUTF8", "1")
        self.env.update({key: str(value) for key, value in (env or {}).items()})
        self.shell = shell
        self._runtime = runtime
        self._exit_pattern = exit_pattern
        self._exit_action = exit_action
        self._on_finished = on_finished
        self._backend = "pending"
        self._process: Any = None
        self._master_fd: int | None = None
        self._reader_thread: threading.Thread | None = None
        self._finished = threading.Event()
        self._lock = threading.RLock()
        self._exit_code: int | None = None
        self._output_text = ""
        self._pattern_buffer = ""
        self._pattern_matched = False
        self._started = False
        self._finished_emitted = False

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    @property
    def output_text(self) -> str:
        return self._output_text

    @property
    def is_running(self) -> bool:
        return self._started and not self._finished.is_set()

    def start(self) -> TerminalSession:
        with self._lock:
            if self._started:
                raise RuntimeError("TerminalSession.start() may only be called once.")
            self._started = True
            try:
                if sys.platform == "win32":
                    self._start_windows()
                elif os.name == "posix":
                    self._start_posix_pty()
                else:
                    self._start_subprocess()
            except TerminalSpawnError:
                self._started = False
                raise
            except OSError as exc:
                self._started = False
                raise TerminalSpawnError(f"无法启动命令 {self._command_display()}: {exc}") from exc

            self._emit_started()
            self._reader_thread = threading.Thread(
                target=self._read_and_wait,
                name=f"terminal-{self.id[:8]}",
                daemon=True,
            )
            self._reader_thread.start()
        return self

    def run(self) -> TerminalResult:
        return self.start().wait()

    def wait(self, timeout: float | None = None) -> TerminalResult:
        if not self._started:
            raise RuntimeError("TerminalSession has not been started.")
        if not self._finished.wait(timeout):
            raise TimeoutError(f"terminal session did not finish within {timeout} seconds")
        return TerminalResult(self._exit_code if self._exit_code is not None else -1, self._output_text)

    def write(self, data: str) -> None:
        if not data or not self.is_running:
            return
        try:
            if self._backend == "winpty":
                self._process.write(data)
            elif self._backend == "posix-pty" and self._master_fd is not None:
                os.write(self._master_fd, data.encode("utf-8"))
            else:
                stdin = getattr(self._process, "stdin", None)
                if stdin is not None:
                    stdin.write(data.encode("utf-8"))
                    stdin.flush()
        except (OSError, ValueError):
            LOGGER.debug("terminal write ignored after stream close", exc_info=True)

    def terminate(self) -> None:
        if not self.is_running:
            return
        process = self._process
        try:
            if self._backend == "winpty":
                process.terminate(force=True)
            elif os.name == "posix" and isinstance(process, subprocess.Popen):
                os.killpg(process.pid, signal.SIGTERM)
            elif isinstance(process, subprocess.Popen):
                process.terminate()
        except (OSError, ProcessLookupError):
            pass

    def _argv(self) -> list[str]:
        if self.shell:
            command = self.cmd if isinstance(self.cmd, str) else shlex.join(self.cmd)
            if sys.platform == "win32":
                command = self.cmd if isinstance(self.cmd, str) else subprocess.list2cmdline(self.cmd)
                return [self.env.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
            return ["/bin/sh", "-lc", command]
        if isinstance(self.cmd, str):
            return [self.cmd]
        return list(self.cmd)

    def _start_windows(self) -> None:
        try:
            from winpty import PtyProcess
        except ImportError:
            self._log_fallback("pywinpty 不可用，回退到子进程模式。")
            self._start_subprocess()
            return

        argv = self._argv()
        try:
            self._process = PtyProcess.spawn(
                argv,
                cwd=str(self.cwd) if self.cwd else None,
                env=self.env,
            )
            self._backend = "winpty"
        except Exception as exc:
            self._log_fallback(f"winpty 启动失败，回退到子进程模式: {exc}")
            self._start_subprocess()

    def _start_posix_pty(self) -> None:
        import pty

        master_fd, slave_fd = pty.openpty()
        try:
            self._process = subprocess.Popen(
                self._argv(),
                cwd=str(self.cwd) if self.cwd else None,
                env=self.env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            os.close(master_fd)
            os.close(slave_fd)
            raise TerminalSpawnError(f"无法启动命令 {self._command_display()}: {exc}") from exc
        os.close(slave_fd)
        self._master_fd = master_fd
        self._backend = "posix-pty"

    def _start_subprocess(self) -> None:
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            self._process = subprocess.Popen(
                self._argv(),
                cwd=str(self.cwd) if self.cwd else None,
                env=self.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                **kwargs,
            )
        except OSError as exc:
            raise TerminalSpawnError(f"无法启动命令 {self._command_display()}: {exc}") from exc
        self._backend = "subprocess"

    def _read_and_wait(self) -> None:
        exit_code = -1
        try:
            if self._backend == "winpty":
                while self._process.isalive():
                    try:
                        raw = self._process.read(4096)
                    except (EOFError, OSError):
                        break
                    if raw:
                        self._consume(raw)
                exit_code = int(self._process.wait())
            elif self._backend == "posix-pty":
                self._read_posix_master()
                exit_code = int(self._process.wait())
            else:
                stdout = self._process.stdout
                if stdout is not None:
                    while True:
                        raw = stdout.read(4096)
                        if not raw:
                            break
                        self._consume(raw.decode("utf-8", errors="replace"))
                exit_code = int(self._process.wait())
        except Exception:
            LOGGER.exception("terminal reader failed for session %s", self.id)
            return_code = getattr(self._process, "returncode", None)
            exit_code = int(return_code) if return_code is not None else -1
        finally:
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except OSError:
                    pass
                self._master_fd = None
            self._finish(exit_code)

    def _read_posix_master(self) -> None:
        assert self._master_fd is not None
        while True:
            try:
                raw = os.read(self._master_fd, 4096)
            except OSError:
                break
            if not raw:
                break
            self._consume(raw.decode("utf-8", errors="replace"))

    def _consume(self, raw: str) -> None:
        clean = _ANSI_STRIP_RE.sub("", raw)
        if not clean:
            return
        with self._lock:
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
        self._pattern_buffer += data
        if self._exit_pattern not in self._pattern_buffer:
            return
        self._pattern_matched = True
        self._runtime.log("terminal", "message", "terminal:close", {"session_id": self.id})
        if self._exit_action == "write_newline":
            self.write("\n")
        elif self._exit_action == "terminate":
            self.terminate()

    def _finish(self, exit_code: int) -> None:
        with self._lock:
            self._exit_code = exit_code
            if not self._finished_emitted:
                self._finished_emitted = True
                self._runtime.log(
                    "terminal",
                    "message",
                    "terminal:finished",
                    {"session_id": self.id, "exit_code": exit_code},
                )
        if self._on_finished is not None:
            self._on_finished(self)
        self._finished.set()

    def _emit_started(self) -> None:
        self._runtime.log(
            "terminal",
            "message",
            "terminal:started",
            {"session_id": self.id, "command": self._command_display(), "backend": self._backend},
        )

    def _log_fallback(self, message: str) -> None:
        self._runtime.log(
            "terminal",
            "warning",
            message,
            {"session_id": self.id, "command": self._command_display()},
        )

    def _command_display(self) -> str:
        return self.cmd if isinstance(self.cmd, str) else shlex.join(self.cmd)


def get_session(runtime: PipelineRuntime, session_id: str) -> TerminalSession | None:
    return runtime.sessions.get(session_id)
