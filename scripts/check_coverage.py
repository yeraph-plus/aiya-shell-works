from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CRITICAL_MODULES = (
    "core/context.py",
    "core/events.py",
    "core/executor.py",
    "core/files.py",
    "core/runtime.py",
    "core/scheduler.py",
)


def _percent(summary: dict[str, Any]) -> float:
    return float(summary["percent_covered"])


def check_coverage(report_path: Path, *, overall_minimum: float = 85.0, module_minimum: float = 90.0) -> list[str]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    total = _percent(report["totals"])
    if total < overall_minimum:
        failures.append(f"core overall: {total:.2f}% < {overall_minimum:.2f}%")

    files = report["files"]
    for module in CRITICAL_MODULES:
        if module not in files:
            failures.append(f"critical module missing from report: {module}")
            continue
        covered = _percent(files[module]["summary"])
        if covered < module_minimum:
            failures.append(f"{module}: {covered:.2f}% < {module_minimum:.2f}%")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce kernel coverage thresholds from coverage.py JSON output.")
    parser.add_argument("report", nargs="?", type=Path, default=Path("coverage.json"))
    parser.add_argument("--overall-minimum", type=float, default=85.0)
    parser.add_argument("--module-minimum", type=float, default=90.0)
    args = parser.parse_args(argv)

    failures = check_coverage(
        args.report,
        overall_minimum=args.overall_minimum,
        module_minimum=args.module_minimum,
    )
    if failures:
        sys.stderr.write("coverage gate failed:\n- " + "\n- ".join(failures) + "\n")
        return 1
    sys.stdout.write("coverage gate passed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
