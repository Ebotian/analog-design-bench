#!/usr/bin/env python3
"""Compare our gate proxy with the oracle's published reference numbers.

One evaluation of the reference solution per task; the output says which gates
our decks measure faithfully enough to optimise and which ones disagree with
the verifier (those are reported, never optimised).  Needs docker.

Usage: python3 scripts/calibrate_gates.py --tasks <id> [<id> ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adb_solve import bench, calibrate, corners as corner_asset, evaluate, gates, params  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", nargs="+", required=True)
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "calibration")
    args = parser.parse_args()

    asset = corner_asset.load(ROOT / "configs" / "corners.toml")
    for task in args.tasks:
        payload = json.loads((ROOT / "data" / "tasks" / f"{task}.json").read_text())
        text = next(item["content"] for item in payload["files"]["solution"] if item["path"].endswith(".spi"))
        decks = [bench.load(p) for p in sorted((ROOT / "data" / "t1" / "tasks" / task / "tests" / "benches").glob("*.spi"))]
        corners = [dict(c) for c in corner_asset.expand(corner_asset.for_task(asset, task))]
        report = evaluate.evaluate(
            text,
            decks,
            corners,
            evaluate.EvalOptions(jobs=args.jobs, cache_root=ROOT / ".work" / "calib-cache", work_root=ROOT / ".work" / "sim"),
        )
        names = {n for row in report["results"] for n in (row.get("measures") or {})}
        gate_set = gates.from_task(payload, names)
        checks = calibrate.compare(payload, gate_set, report["results"])
        calibrate.write(args.out / f"{task}.json", task, checks)
        print(task, "trusted:", [c.gate for c in checks if c.verdict == "ok"])
        print(task, "mismatch:", [(c.gate, None if c.relative_difference is None else round(c.relative_difference, 4)) for c in checks if c.verdict == "proxy-mismatch"])
        print(task, "unresolved:", gate_set.unresolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
