"""Derived measurements that a document may quote.

Every function here reads committed artifacts and recomputes the number; the
results are cached for the duration of one render.  Anything that needs Docker
or a simulation deliberately does not belong here — the documentation build
runs on every commit and must stay sub-second.
"""

from __future__ import annotations

import json
import math
import statistics
from functools import lru_cache
from pathlib import Path
from typing import Any

from adb_solve import corners as corner_asset
from adb_solve import params

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=None)
def corners_stats() -> dict[str, object]:
    """Counts over ``configs/corners.toml`` and its deterministic expansion."""
    asset = corner_asset.load(ROOT / "configs" / "corners.toml")
    points = sum(len(corner_asset.expand(entry)) for entry in asset.tasks.values())
    provenance: dict[str, int] = {}
    kind: dict[str, int] = {}
    for entry in asset.tasks.values():
        provenance[str(entry.provenance)] = provenance.get(str(entry.provenance), 0) + 1
        kind[str(entry.kind)] = kind.get(str(entry.kind), 0) + 1
    return {
        "tasks": len(asset.tasks),
        "points": points,
        "needs_review": asset.meta.needs_review,
        "unresolved": asset.meta.unresolved_values,
        "coverage_tasks": sum(1 for e in asset.tasks.values() if e.coverage),
        "coverage_subsets": sum(len(e.coverage) for e in asset.tasks.values()),
        "provenance": provenance,
        "kind": kind,
    }


@lru_cache(maxsize=None)
def netlist_stats() -> dict[str, object]:
    """Writable-knob distribution over the T0 reference solutions."""
    counts: list[int] = []
    literal = 0
    for path in sorted((ROOT / "data" / "tasks").glob("*.json")):
        payload: Any = json.loads(path.read_text())
        solutions: Any = payload["files"]["solution"]
        text = next(str(item["content"]) for item in solutions if str(item["path"]).endswith(".spi"))
        if ".param" not in text.lower():
            literal += 1
        counts.append(len(params.load_text(text, path=path.stem).writable()))
    counts.sort()
    return {
        "netlists": len(counts),
        "literal": literal,
        "knobs_median": float(statistics.median(counts)),
        "knobs_p90": float(counts[_nearest_rank(len(counts), 0.90)]),
        "knobs_max": counts[-1],
    }


def _nearest_rank(count: int, quantile: float) -> int:
    if count == 0:
        return 0
    return max(0, min(count - 1, math.ceil(quantile * count) - 1))
