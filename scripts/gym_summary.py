#!/usr/bin/env python3
"""Recompute results/gym/summary.json from the per-run JSON files.

Cheap on purpose: the runs are the expensive part, so the summary must be
derivable without re-simulating.  Also reports how close the failures came,
because "0 recovered" alone hides whether the solver missed by 1% or 40%.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "results" / "gym"
    runs = []
    for path in sorted(folder.glob("*-s*.json")):
        payload = json.loads(path.read_text())
        report = payload.get("report") or {}
        margins = (report.get("validation") or {}).get("margins") or {}
        values = [value for value in margins.values() if value is not None]
        runs.append(
            {
                "task": payload["task"],
                "seed": payload["seed"],
                "sims": payload["sims"],
                "sims_to_feasible": payload.get("sims_to_feasible"),
                "validation_feasible": payload.get("validation_feasible"),
                "polish_runs": payload.get("polish_runs", 0),
                "official_passed": (payload.get("official") or {}).get("passed"),
                "official_total": (payload.get("official") or {}).get("total"),
                "worst_margin": min(values) if values else None,
                "short_gates": sorted(
                    name for name, value in margins.items() if value is not None and value < 0
                ),
            }
        )
    if not runs:
        print(f"no runs under {folder}", file=sys.stderr)
        return 1
    recovered = [r for r in runs if r["official_passed"] and r["official_passed"] == r["official_total"]]
    verified = [r for r in runs if r["official_total"]]
    worst = [r["worst_margin"] for r in runs if r["worst_margin"] is not None]
    summary = {
        "schema": 2,
        "runs": len(runs),
        "recovered": len(recovered),
        "verified": len(verified),
        "search_feasible": sum(1 for r in runs if r["validation_feasible"]),
        "tasks": sorted({r["task"] for r in runs}),
        "median_sims_to_feasible": statistics.median(
            [r["sims_to_feasible"] for r in recovered if r["sims_to_feasible"]]
        )
        if recovered
        else None,
        "best_worst_margin": round(max(worst), 4) if worst else None,
        "median_worst_margin": round(statistics.median(worst), 4) if worst else None,
        "runs_detail": runs,
    }
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "runs_detail"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
