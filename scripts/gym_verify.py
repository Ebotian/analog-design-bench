#!/usr/bin/env python3
"""Fill in the official verdict for existing gym candidates.

The verifier is the judge, so a gym run is not scored until ``test.sh`` has
seen the candidate.  This lets a past run be completed without re-simulating:
it runs the official verifier on each ``<task>-s<seed>.spi`` and writes the
result back into the sibling JSON, then the summary is recomputed from those.

Usage: python3 scripts/gym_verify.py results/gym/ladder-3200 ...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adb_solve import verifier  # noqa: E402


def main() -> int:
    folders = [Path(arg) for arg in sys.argv[1:]] or [ROOT / "results" / "gym"]
    packages = verifier.discover(ROOT / "data" / "t1" / "tasks")
    for folder in folders:
        for candidate in sorted(folder.glob("*-s*.spi")):
            record = candidate.with_suffix(".json")
            payload = json.loads(record.read_text())
            task = payload["task"]
            run = verifier.run(packages[task], candidate, workdir=ROOT / ".work" / "gym-verify", timeout=900)
            artifacts = run.artifacts
            payload["official"] = {
                "skipped": False,
                "status": run.status,
                "passed": artifacts.tests_passed if artifacts else 0,
                "total": artifacts.tests_total if artifacts else 0,
                "reward": artifacts.reward if artifacts else 0.0,
            }
            payload["validation_feasible"] = bool(payload.get("validation_feasible"))
            record.write_text(json.dumps(payload, indent=2) + "\n")
            print(candidate.name, payload["official"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
