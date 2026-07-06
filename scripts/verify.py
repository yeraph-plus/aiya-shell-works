"""End-to-end acceptance runner for shell-refactor.

Spawns ``python main_cli.py`` against each of the six example workflows and
checks that the expected output files exist (and the file *count* matches).
Does NOT read file contents — that's pytest's job.  Exit codes:

    0 — all workflows passed
    1 — at least one failed
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "modules"
WORKFLOWS = ROOT / "workflows"
RESOURCES = ROOT / "resources"
CLI = ROOT / "main_cli.py"

WORK = Path(tempfile.mkdtemp(prefix="shellworker-verify-"))
FAILS: list[str] = []
CASES: list[tuple[str, callable]] = []


def case(name: str) -> callable:
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def run_cli(workflow: str, out: Path, *extra: str) -> tuple[int, str, str]:
    args = [
        sys.executable, str(CLI),
        "--modules-dir", str(MODULES),
        "--workflows-dir", str(WORKFLOWS),
        "--output-dir", str(out),
        workflow,
        *extra,
    ]
    proc = subprocess.run(args, capture_output=True, text=True, cwd=str(ROOT))
    return proc.returncode, proc.stdout, proc.stderr


def _setup_inputs(root: Path) -> dict[str, Path]:
    """Build a small scratch dir tree for the path-based workflows."""

    root.mkdir(parents=True, exist_ok=True)
    src = root / "src"
    src.mkdir()
    (src / "a.txt").write_text("alpha", encoding="utf-8")
    (src / "b.txt").write_text("beta", encoding="utf-8")
    inner = src / "inner"; inner.mkdir()
    (inner / "c.txt").write_text("gamma", encoding="utf-8")
    return {"src": src, "single_file": src / "a.txt"}


@case("create")
def test_create() -> None:
    out = WORK / "create"
    code, _, err = run_cli("example-create.yaml", out)
    if code != 0:
        FAILS.append(f"create: exit={code} err={err.strip()[:200]}")
        return
    if not (out / "hello.txt").exists():
        FAILS.append("create: hello.txt missing")


@case("file-rename")
def test_file_rename() -> None:
    out = WORK / "file-rename"
    scratch = _setup_inputs(WORK / "file-rename-input")
    code, _, err = run_cli(
        "example-file-rename.yaml", out,
        "--files", str(scratch["src"]), "--recurse",
    )
    if code != 0:
        FAILS.append(f"file-rename: exit={code} err={err.strip()[:200]}")
        return
    # Three files renamed (a.txt, b.txt, inner/c.txt — recurse=true)
    renamed = [p for p in out.rglob("*") if p.is_file()
               and "_renamed" in p.name]
    if len(renamed) != 3:
        FAILS.append(f"file-rename: expected 3 renamed files, got {len(renamed)}")
    if not (out / "renames-summary.txt").exists():
        FAILS.append("file-rename: summary missing")


@case("folder-rename")
def test_folder_rename() -> None:
    out = WORK / "folder-rename"
    src = WORK / "folder-rename-input" / "mydir"; src.mkdir(parents=True)
    (src / "note.txt").write_text("note", encoding="utf-8")
    code, _, err = run_cli(
        "example-folder-rename.yaml", out,
        "--files", str(src),  # recurse=false → folder passed as whole unit
    )
    if code != 0:
        FAILS.append(f"folder-rename: exit={code} err={err.strip()[:200]}")
        return
    renamed_dir = out / "pkg-mydir"
    if not renamed_dir.exists():
        FAILS.append("folder-rename: pkg-mydir missing")
        return
    if not (renamed_dir / "note.txt").exists():
        FAILS.append("folder-rename: note.txt missing inside renamed dir")
    if not (out / "folder-summary.txt").exists():
        FAILS.append("folder-rename: summary missing")


@case("cycle-count")
def test_cycle_count() -> None:
    out = WORK / "cycle-count"
    scratch = _setup_inputs(WORK / "cycle-count-input")
    code, _, err = run_cli(
        "example-cycle-count.yaml", out,
        "--files", str(scratch["src"]), "--recurse",
    )
    if code != 0:
        FAILS.append(f"cycle-count: exit={code} err={err.strip()[:200]}")
        return
    report = out / "count.txt"
    if not report.exists():
        FAILS.append("cycle-count: count.txt missing")
        return
    text = report.read_text(encoding="utf-8")
    # 3 input files merged → count=3
    if "count=3" not in text:
        FAILS.append(f"cycle-count: count != 3 in {text!r}")


@case("line-echo")
def test_line_echo() -> None:
    out = WORK / "line-echo"
    code, _, err = run_cli(
        "example-line-echo.yaml", out,
        "--lines", "alpha\nbeta\ngamma",
    )
    if code != 0:
        FAILS.append(f"line-echo: exit={code} err={err.strip()[:200]}")
        return
    files = list(out.glob("task_*.txt"))
    if len(files) != 3:
        FAILS.append(f"line-echo: expected 3 task_*.txt, got {len(files)}")


@case("external-tool")
def test_external_tool() -> None:
    out = WORK / "external-tool"
    scratch = _setup_inputs(WORK / "external-tool-input")
    # Confirm mock tool exists at the project resources dir first.
    mock = RESOURCES / ("mock_tool.bat" if sys.platform == "win32" else "mock_tool.sh")
    if not mock.exists():
        FAILS.append(f"external-tool: mock tool missing at {mock}")
        return
    code, _, err = run_cli(
        "example-external-tool.yaml", out,
        "--files", str(scratch["src"]), "--recurse",
    )
    if code != 0:
        FAILS.append(f"external-tool: exit={code} err={err.strip()[:200]}")
        return
    # mock_tool writes a ``<file>.done`` sidecar next to each input file.
    done_files = [p for p in out.rglob("*") if p.is_file() and p.name.endswith(".done")]
    if len(done_files) != 3:
        FAILS.append(
            f"external-tool: expected 3 .done sidecars, got {len(done_files)}; "
            f"cwd contents = {[p.name for p in out.rglob('*')]}")


def main() -> int:
    print(f"workdir = {WORK}")
    for name, fn in CASES:
        print(f"--- running {name}")
        try:
            fn()
        except Exception as exc:
            FAILS.append(f"{name}: exception {type(exc).__name__}: {exc}")
    if FAILS:
        print("FAILURES:")
        for f in FAILS:
            print(f"  - {f}")
        # Preserve WORK for inspection on failure.
        print(f"  (workdir kept at: {WORK})")
        return 1
    print("ALL ACCEPTANCE PASSED")
    shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())