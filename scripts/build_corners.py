#!/usr/bin/env python3
"""Generate ``configs/corners.toml`` from corpus evidence.

The corner matrix is an IaC asset: extraction proposes it, a human reviews the
diff, the committed file is what evaluation consumes.  All the evidence logic
lives in :mod:`adb_solve.evidence` (typed and unit-tested); this script is only
the CLI.

Usage:
    python3 scripts/build_corners.py            # write the asset
    python3 scripts/build_corners.py --check    # fail if it is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adb_solve import corners  # noqa: E402
from adb_solve.evidence import build_entry  # noqa: E402

TASKS = ROOT / "data" / "tasks"
DEFAULT_OUT = ROOT / "configs" / "corners.toml"
HEADER = """\
Corner matrices for Analog Design Bench V2 (generated; review like code).

Source of truth for 'which process x supply x temperature points does each
task sign off over'.  Regenerate with scripts/build_corners.py; check drift
with `make corners`.  Entries flagged needs_review, or carrying unresolved
placeholders, are declared debt -- not silent guesses."""


def collect() -> list[corners.TaskCorners]:
    return [
        build_entry(json.loads(path.read_text()))
        for path in sorted(TASKS.glob("*.json"))
    ]


def changed_tasks(committed: str, regenerated: str) -> list[str]:
    """Task ids whose declaration differs, for a reviewable --check report."""
    try:
        old = corners.parse(__import__("tomllib").loads(committed))
        new = corners.parse(__import__("tomllib").loads(regenerated))
    except corners.CornersError:
        return ["<unparseable>"]
    ids = set(old.tasks) | set(new.tasks)
    return sorted(
        task_id
        for task_id in ids
        if old.tasks.get(task_id) != new.tasks.get(task_id)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when the asset is stale")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    entries = collect()
    text = corners.dumps(entries, HEADER)
    out = args.out

    if args.check:
        if not out.exists():
            print(f"{out} is missing; run scripts/build_corners.py", file=sys.stderr)
            return 1
        committed = out.read_text()
        if committed == text:
            print(f"{out} is up to date ({len(entries)} tasks)")
            return 0
        stale = changed_tasks(committed, text)
        print(
            f"{out} is stale: {len(stale)} task(s) changed "
            f"({', '.join(stale[:8])}{' ...' if len(stale) > 8 else ''})",
            file=sys.stderr,
        )
        return 1

    corners.write(out, entries, HEADER)
    flagged = sum(1 for entry in entries if entry.needs_review)
    unresolved = sum(len(entry.unresolved) for entry in entries)
    print(
        f"wrote {out}: {len(entries)} tasks, "
        f"{flagged} need review, {unresolved} unresolved placeholder name(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
