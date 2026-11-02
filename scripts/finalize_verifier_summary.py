#!/usr/bin/env python3
"""Rebuild ``results/verifier/summary.json`` from the per-task JSON records.

The sweep writes the summary at the end of a run, but the record set can be
regenerated independently (for example after renaming a field).  This script
reads only the per-task JSON plus the sweep timestamps/CPU measurement in the
existing summary, and recomputes every aggregate.

Usage:
    python3 scripts/finalize_verifier_summary.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adb_solve import verifier  # noqa: E402

RUNS = ROOT / "results" / "verifier"


def load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, default=RUNS)
    args = parser.parse_args()
    summary_path = args.runs / "summary.json"
    old = load(summary_path)
    records = [
        verifier.load_result(path)
        for path in sorted(args.runs.glob("sky130-*.json"))
        if path.stem != "summary"
    ]
    results = [item for item in records if item is not None]
    if not results:
        print(f"no task records under {args.runs}", file=sys.stderr)
        return 1
    started = datetime.fromisoformat(str(old["started_utc"]))
    finished = datetime.fromisoformat(str(old["finished_utc"]))
    summary = {
        "schema": 2,
        **verifier.summarize(results),
        "started_utc": started.isoformat(timespec="seconds"),
        "finished_utc": finished.isoformat(timespec="seconds"),
        "sweep_wall_s": round((finished - started).total_seconds(), 1),
        "jobs": old.get("jobs"),
        "cpu_busy_percent": old.get("cpu_busy_percent"),
        "leftover_containers": old.get("leftover_containers", []),
        "ngspice_threads_after": old.get("ngspice_threads_after", 0),
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(
        f"wrote {summary_path}: tasks={summary['tasks']} {summary['status_counts']} "
        f"sims={summary['ngspice_runs']} sweep_wall={summary['sweep_wall_s']}s "
        f"task_wall_sum={summary['task_wall_sum_s']}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
