"""跨平台进程检索与文件解锁模块。

detect 模式：扫描锁定状态，写入 ctx.shared。
release 模式：终止锁定进程（保护 safe_processes），重试验证。
"""

from __future__ import annotations

import ctypes
import platform
import sys
import time
from pathlib import Path
from typing import Any

from core.tools import collect_file_targets

MODULE_META = {
    "slug": "process-inspector",
    "name": "进程检索与文件解锁",
    "core_version": "2.0.0",
    "tags": ["system", "process", "unlock"],
    "atom": ["file", "folder"],
    "scope": 1,
    "description": "跨平台检测并释放其他进程对文件的占用锁定。detect 仅报告，release 终止锁定进程。",
}

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "select",
            "options": [
                {"value": "detect", "title": "仅检测 (detect)"},
                {"value": "release", "title": "释放锁定 (release)"},
            ],
            "default": "detect",
            "title": "处理动作",
        },
        "max_retries": {
            "type": "int",
            "title": "最大重试次数",
            "default": 2,
            "min": 1,
            "max": 5,
        },
        "retry_delay": {
            "type": "float",
            "title": "重试间隔(秒)",
            "default": 1.0,
            "min": 0.1,
            "max": 10.0,
        },
        "safe_processes": {
            "type": "str",
            "title": "保护进程 (逗号分隔)",
            "default": "explorer.exe",
            "description": "release 模式下绝不终止的进程名列表",
        },
    },
}

# ---------------------------------------------------------------------------
# Windows lock detection (kernel32.CreateFileW)
# ---------------------------------------------------------------------------
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80

_kernel32: Any = None


def _get_kernel32() -> Any:
    global _kernel32
    if _kernel32 is None:
        import ctypes.wintypes
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.CreateFileW.argtypes = [
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
            ctypes.wintypes.LPVOID, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
            ctypes.wintypes.HANDLE,
        ]
        _kernel32.CreateFileW.restype = ctypes.wintypes.HANDLE
        _kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        _kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    return _kernel32


def _is_locked_win(path: Path) -> bool:
    k32 = _get_kernel32()
    INVALID = ctypes.c_void_p(-1).value
    handle = k32.CreateFileW(
        str(path), GENERIC_READ | GENERIC_WRITE, 0, None,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
    )
    if handle == INVALID or handle is None:
        return True
    k32.CloseHandle(handle)
    return False


# ---------------------------------------------------------------------------
# Windows process discovery (rstrtmgr.dll Restart Manager)
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    class _RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [
            ("dwProcessId", ctypes.c_uint32),
            ("ProcessStartTime", ctypes.c_ulonglong),
        ]

    class _RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [
            ("Process", _RM_UNIQUE_PROCESS),
            ("strAppName", ctypes.c_wchar_p),
            ("strServiceShortName", ctypes.c_wchar_p),
            ("ApplicationType", ctypes.c_uint32),
            ("AppStatus", ctypes.c_uint32),
            ("TSSessionId", ctypes.c_uint32),
            ("bRestartable", ctypes.c_uint32),
        ]

    _rm: Any = None

    def _get_rm() -> Any:
        global _rm
        if _rm is None:
            _rm = ctypes.WinDLL("rstrtmgr")
            _rm.RmStartSession.argtypes = [
                ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32, ctypes.c_wchar_p,
            ]
            _rm.RmStartSession.restype = ctypes.c_uint32
            _rm.RmRegisterResources.argtypes = [
                ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_wchar_p),
                ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
            ]
            _rm.RmRegisterResources.restype = ctypes.c_uint32
            _rm.RmGetList.argtypes = [
                ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
            ]
            _rm.RmGetList.restype = ctypes.c_uint32
            _rm.RmEndSession.argtypes = [ctypes.c_uint32]
            _rm.RmEndSession.restype = ctypes.c_uint32
        return _rm

    def _find_locking_processes_win(file_path: str) -> list[tuple[int, str]]:
        """Return (pid, name) for processes that hold a lock on *file_path*."""
        try:
            rm = _get_rm()
        except Exception:
            return []

        session = ctypes.c_uint32(0)
        ret = rm.RmStartSession(ctypes.byref(session), 0, None)
        if ret != 0:
            return []

        try:
            path_arr = (ctypes.c_wchar_p * 1)(file_path)
            ret = rm.RmRegisterResources(session, 1, path_arr, 0, None, 0, None)
            if ret != 0:
                return []

            needed = ctypes.c_uint32(0)
            count = ctypes.c_uint32(0)
            reboots = ctypes.c_uint32(0)
            ret = rm.RmGetList(
                session, ctypes.byref(needed), ctypes.byref(count), None,
                ctypes.byref(reboots),
            )
            if ret != 234:  # ERROR_MORE_DATA
                return []

            buf = (_RM_PROCESS_INFO * count.value)()
            ret = rm.RmGetList(
                session, ctypes.byref(needed), ctypes.byref(count), buf,
                ctypes.byref(reboots),
            )
            if ret != 0:
                return []

            result: list[tuple[int, str]] = []
            for i in range(count.value):
                pi = buf[i]
                pid = pi.Process.dwProcessId
                name = pi.strAppName or ""
                result.append((pid, name))
            return result

        finally:
            try:
                rm.RmEndSession(session)
            except Exception:
                pass

else:
    def _find_locking_processes_win(_file_path: str) -> list[tuple[int, str]]:  # type: ignore[no-redef]
        return []


# ---------------------------------------------------------------------------
# Unix lock detection + process discovery (lsof)
# ---------------------------------------------------------------------------
def _find_locked_unix(path: Path, runtime: Any) -> list[int]:
    """Return PIDs of processes that hold a lock on *path* (empty = not locked)."""
    result = runtime.spawn(["lsof", "-F", "p", "-t", "--", str(path)])
    if not result.is_success:
        if "command not found" in result.stderr.lower() or "not found" in result.stderr.lower():
            raise RuntimeError("lsof 不可用，请安装 lsof: apt install lsof / brew install lsof")
        return []
    pids: list[int] = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("p"):
            try:
                pids.append(int(line[1:]))
            except ValueError:
                pass
        else:
            try:
                pids.append(int(line))
            except ValueError:
                pass
    return pids


def _kill_pid_unix(pid: int, runtime: Any) -> bool:
    result = runtime.spawn(["kill", str(pid)])
    return result.is_success


def _kill_pid_win(pid: int, name: str) -> bool:
    import subprocess
    try:
        cf = subprocess.CREATE_NO_WINDOW
        proc = subprocess.run(
            ["taskkill", "/f", "/pid", str(pid)],
            capture_output=True, text=True, timeout=15, creationflags=cf,
        )
        if proc.returncode == 0:
            return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(ctx: Any, cfg: Any, runtime: Any) -> Any:
    is_windows = platform.system() == "Windows"
    action: str = cfg.get("action", "detect")
    max_retries: int = cfg.get("max_retries", 2)
    retry_delay: float = float(cfg.get("retry_delay", 1.0))
    safe_raw: str = cfg.get("safe_processes", "explorer.exe")
    safe_set = {s.strip().lower() for s in safe_raw.split(",") if s.strip()}

    targets = collect_file_targets(ctx)
    if not targets:
        runtime.log("process-inspector", "hint", "无可操作的文件。")
        ctx.shared["file_lock_status"] = {"locked": [], "free": [], "freed": []}
        ctx.shared["lock_summary"] = {"total": 0, "locked": 0, "free": 0, "freed": 0}
        return ctx

    total = len(targets)

    # ---- detect phase ----
    locked_paths: list[Path] = []
    for fpath in targets:
        if not fpath.is_file():
            continue
        if is_windows:
            if _is_locked_win(fpath):
                locked_paths.append(fpath)
        else:
            pids = _find_locked_unix(fpath, runtime)
            if pids:
                locked_paths.append(fpath)

    locked_str = [str(p) for p in locked_paths]
    free_paths = [p for p in targets if p.is_file() and p not in locked_paths]
    free_str = [str(p) for p in free_paths]

    freed_str: list[str] = []

    runtime.log("process-inspector", "info",
                 f"检测完成: {len(free_str)} 空闲, {len(locked_str)} 被锁定 (共 {total})")

    if action == "detect":
        ctx.shared["file_lock_status"] = {"locked": locked_str, "free": free_str, "freed": []}
        ctx.shared["lock_summary"] = {"total": total, "locked": len(locked_str), "free": len(free_str), "freed": 0}
        return ctx

    # ---- release phase ----
    if not locked_str:
        runtime.log("process-inspector", "success", "无锁定文件，无需释放。")
        ctx.shared["file_lock_status"] = {"locked": [], "free": free_str, "freed": []}
        ctx.shared["lock_summary"] = {"total": total, "locked": 0, "free": len(free_str), "freed": 0}
        return ctx

    runtime.log("process-inspector", "warning",
                 f"开始释放 {len(locked_str)} 个被锁定文件 (保护: {', '.join(sorted(safe_set)) or '(无)'})")

    for fp in locked_paths:
        fp_str = str(fp)

        if is_windows:
            info = _find_locking_processes_win(fp_str)
        else:
            pids = _find_locked_unix(fp, runtime)
            info = [(pid, f"pid={pid}") for pid in pids]

        if not info:
            runtime.log("process-inspector", "warning",
                         f"无法找到锁定 {fp.name} 的进程，跳过。")
            continue

        for pid, name in info:
            lower_name = name.lower() if name else ""
            is_safe = any(safe in lower_name for safe in safe_set)

            if is_safe:
                runtime.log("process-inspector", "message",
                             f"  保护: {name} (PID {pid}) 锁定 {fp.name}，不终止。")
                continue

            if is_windows:
                ok = _kill_pid_win(pid, name)
            else:
                ok = _kill_pid_unix(pid, runtime)

            if ok:
                runtime.log("process-inspector", "message",
                             f"  已终止: {name} (PID {pid}) → {fp.name}")
            else:
                runtime.log("process-inspector", "error",
                             f"  终止失败: {name} (PID {pid})")

    # ---- retry verification ----
    for attempt in range(max_retries):
        time.sleep(retry_delay)
        still_locked: list[Path] = []
        for fp in locked_paths:
            if is_windows:
                if _is_locked_win(fp):
                    still_locked.append(fp)
            else:
                if _find_locked_unix(fp, runtime):
                    still_locked.append(fp)

        if not still_locked:
            runtime.log("process-inspector", "success", "释放完成，所有文件已解锁。")
            break

        if attempt < max_retries - 1:
            runtime.log("process-inspector", "message",
                         f"仍有 {len(still_locked)} 个文件被锁定，重试 ({attempt + 2}/{max_retries})...")
    else:
        still_locked = locked_paths  # final state after all retries

    # ---- final status ----
    final_locked = [p for p in locked_paths if
                    (_is_locked_win(p) if is_windows else bool(_find_locked_unix(p, runtime)))]
    newly_free = [p for p in locked_paths if p not in final_locked]

    freed_str = [str(p) for p in newly_free]
    final_locked_str = [str(p) for p in final_locked]

    ctx.shared["file_lock_status"] = {
        "locked": final_locked_str,
        "free": free_str,
        "freed": freed_str,
    }
    ctx.shared["lock_summary"] = {
        "total": total,
        "locked": len(final_locked_str),
        "free": len(free_str),
        "freed": len(freed_str),
    }

    if final_locked_str:
        runtime.log("process-inspector", "warning",
                     f"释放完成: {len(freed_str)} 释放, {len(final_locked_str)} 仍锁定")
        for fp in final_locked:
            runtime.log("process-inspector", "error", f"  仍锁定: {fp.name}")
    else:
        runtime.log("process-inspector", "success",
                     f"释放完成，全部 {len(locked_str)} 个文件已解锁。")

    return ctx
