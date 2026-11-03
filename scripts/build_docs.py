#!/usr/bin/env python3
"""Render the tagged documentation sources, or check that they are current.

Usage:
    python3 scripts/build_docs.py           # write docs + docs/index.json
    python3 scripts/build_docs.py --check   # fail on drift (pre-commit gate)

The logic lives in ``adb_docs.render``; this script only adds the sys.path
shim so it runs without an install.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adb_docs import render  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when rendered output is stale")
    args = parser.parse_args()

    result = render.build(ROOT)
    problems = list(result.problems)
    if args.check:
        problems.extend(render.drift(result, ROOT))
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            print("run `make docs` to regenerate", file=sys.stderr)
            return 1
        print(
            f"docs up to date ({len(result.docs)} docs, "
            f"{len(result.index['blocks'])} blocks, {len(result.index['facts'])} facts)"
        )
        return 0

    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    written = render.write(result, ROOT)
    print(
        f"wrote {len(written)} files "
        f"({len(result.index['blocks'])} blocks, {len(result.index['facts'])} facts)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
