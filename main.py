"""CLI entry point for the Shell Worker platform.

Designed to be usable on a Linux headless server with no PySide6 installed.
No Python GUI imports anywhere -- runtime detects PTY availability and falls
back to subprocess transparently.

Exit codes:
    0 -- all units succeeded
    1 -- at least one unit failed
    2 -- execution was cancelled
    3 -- workflow / argument validation failed
    4 -- internal exception (unhandled)
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from core import (
    ModuleManager,
    WorkflowLoader,
    WorkflowScheduler,
)
from core.exceptions import (
    PipelineCancelledError,
    PipelineExecutionError,
    WorkflowValidationError,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shell-worker",
        description="Shell Worker Platform -- modular workflow runner.",
    )
    p.add_argument("workflow", nargs="?", help="workflow YAML path or filename under workflows/")

    # ---- input axes ----
    p.add_argument("--files", nargs="+", default=None, help="input file/folder paths, supports glob")
    p.add_argument(
        "--recurse",
        action="store_true",
        default=False,
        help="recursively expand folders into file units (off=folders are whole units)",
    )
    p.add_argument("--lines", default=None, help="text input: separate lines with newline")
    p.add_argument("--lines-file", default=None, help="text input: read from file, '-' for stdin")

    # ---- execution ----
    p.add_argument("--output-dir", default=None, help="output directory (default ./out)")
    p.add_argument(
        "--direct", action="store_true", default=False, help="direct mode: operate on original files without copying"
    )
    p.add_argument("--modules-dir", default="modules", help="modules directory")
    p.add_argument("--workflows-dir", default="workflows", help="workflows directory")

    # ---- scheduling ----
    p.add_argument(
        "--concurrency", "-j", type=int, default=1, metavar="N", help="parallel worker count (default 1, sequential)"
    )
    p.add_argument("--watch", action="store_true", default=False, help="watch input files for changes and re-execute")
    p.add_argument("--cron", default=None, metavar="EXPR", help="cron expression for periodic execution, e.g. '*/5 * * * *'")

    # ---- logging ----
    p.add_argument("--log", action="store_true", default=False, help="enable JSONL event log to output directory")

    # ---- introspection ----
    p.add_argument("--list-workflows", action="store_true", default=False, help="list workflows under workflows/")
    p.add_argument("--list-modules", action="store_true", default=False, help="list modules under modules/")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_parser().parse_args(argv)

    modules_dir = Path(args.modules_dir).resolve()
    workflows_dir = Path(args.workflows_dir).resolve()

    if args.list_workflows:
        _list_workflows(WorkflowLoader(workflows_dir))
        return 0
    if args.list_modules:
        _list_modules(ModuleManager(modules_dir))
        return 0
    if args.workflow is None:
        print("error: workflow argument required, or use --list-workflows/--list-modules", file=sys.stderr)
        return 3

    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd() / "out"

    workflow_arg: str | Path = args.workflow
    if not Path(workflow_arg).is_absolute() and not Path(workflow_arg).exists():
        candidate = workflows_dir / workflow_arg
        if candidate.suffix.lower() not in (".yaml", ".yml"):
            candidate = candidate.with_suffix(".yaml")
        if candidate.exists():
            workflow_arg = candidate

    try:
        module_manager = ModuleManager(modules_dir)

        use_scheduler = args.concurrency > 1 or args.watch or args.cron is not None
        if use_scheduler:
            scheduler = WorkflowScheduler(
                module_manager,
                concurrency=args.concurrency,
                watch=args.watch,
                cron=args.cron,
            )
            summary = scheduler.run(
                workflow_arg,
                output_dir=output_dir,
                files=args.files,
                recurse=args.recurse,
                lines_text=args.lines,
                lines_file=args.lines_file,
                direct_mode=args.direct,
                enable_log=args.log,
            )
        else:
            from core.executor import execute_workflow as _exec_wf

            summary = _exec_wf(
                workflow_arg,
                output_dir=output_dir,
                files=args.files,
                recurse=args.recurse,
                lines_text=args.lines,
                lines_file=args.lines_file,
                direct_mode=args.direct,
                modules_dir=modules_dir,
                workflows_dir=workflows_dir,
                enable_log=args.log,
            )
    except (WorkflowValidationError, PipelineExecutionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except PipelineCancelledError as exc:
        print(f"cancelled: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover
        logging.exception("unhandled")
        print(f"error: {exc}", file=sys.stderr)
        return 4

    if summary.get("cancelled"):
        return 2
    if summary.get("success"):
        print("OK")
        return 0
    print(f"FAILED: {summary.get('failed_units', 0)} / {summary.get('processed_units', 0)}")
    return 1


def _list_workflows(loader: WorkflowLoader) -> None:
    summaries = loader.list_workflows(include_invalid=True)
    print(f"workflows ({len(summaries)}):")
    for s in summaries:
        if s.is_valid:
            atom_label = s.atom or "auto"
            print(f"  {s.filename}  atom={atom_label} scope={s.scope} steps={s.step_count}  - {s.name}")
        else:
            print(f"  [invalid] {s.filename}  - {'; '.join(s.errors)}")


def _list_modules(manager: ModuleManager) -> None:
    modules = manager.scan_modules()
    print(f"modules ({len(modules)}):")
    for slug, d in sorted(modules.items()):
        kind = "path" if d.is_file_module else "line/none"
        print(f"  {slug}  kind={kind} scope={d.scope} tags={list(d.tags)}")
    for w in manager.warnings:
        print(f"  [warn] {w}")


if __name__ == "__main__":
    raise SystemExit(main())
