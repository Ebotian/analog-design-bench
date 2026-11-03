"""Check whether our gate proxy reproduces the oracle's reference numbers.

The verifier publishes, for the reference solution, the worst value of every
gate (`referenceResult.metrics[].value`).  Running the same reference through
our own decks and corner set gives our worst value for the same gate.  If the
two disagree beyond the publication's rounding, our proxy is not measuring what
the oracle measures, and optimising it is chasing a number that does not exist.

Tolerance is derived, not chosen: the published values carry four significant
digits, so the rounding bound is 0.5 * 10^-3 relative = 5e-4 relative.  A gate
whose relative difference exceeds that is flagged `proxy-mismatch` and should be
reported rather than optimised.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import gates as gate_lib

PUBLICATION_RELATIVE_PRECISION = 5e-4  # half of the last of four significant digits


@dataclass(frozen=True)
class GateCheck:
    gate: str
    ours: float | None
    published: float | None
    relative_difference: float | None
    verdict: str  # "ok" | "proxy-mismatch" | "unmeasured"

    def to_json(self) -> dict[str, object]:
        return {
            "gate": self.gate,
            "ours": self.ours,
            "published": self.published,
            "relative_difference": self.relative_difference,
            "verdict": self.verdict,
        }


def worst_values(gate_set: gate_lib.GateSet, rows: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
    """Raw worst measured value per gate, in the measure's own units."""
    values: dict[str, list[float]] = {}
    for row in rows:
        if row.get("status") != "ok":
            continue
        for name, value in (row.get("measures") or {}).items():
            values.setdefault(gate_lib.base_of(str(name)), []).append(float(value))
    out: dict[str, float | None] = {}
    for gate in gate_set.gates:
        samples = values.get(gate.measure) or []
        out[gate.id] = (min(samples) if gate.higher_is_better else max(samples)) if samples else None
    return out


def compare(payload: Mapping[str, Any], gate_set: gate_lib.GateSet, rows: Sequence[Mapping[str, Any]]) -> list[GateCheck]:
    published: dict[str, float] = {}
    for metric in (payload.get("referenceResult") or {}).get("metrics") or []:
        identifier = str(metric.get("id") or "")
        value = gate_lib.base_value(metric)
        if identifier and value is not None:
            published[identifier] = value
    ours = worst_values(gate_set, rows)
    checks: list[GateCheck] = []
    for gate in gate_set.gates:
        mine = ours.get(gate.id)
        theirs = published.get(gate.id)
        if mine is None or theirs is None:
            checks.append(GateCheck(gate.id, mine, theirs, None, "unmeasured"))
            continue
        scale = abs(theirs) if theirs else 1.0
        difference = (mine - theirs) / scale
        verdict = "ok" if abs(difference) <= PUBLICATION_RELATIVE_PRECISION else "proxy-mismatch"
        checks.append(GateCheck(gate.id, mine, theirs, difference, verdict))
    return checks


def mismatched(checks: Sequence[GateCheck]) -> list[str]:
    return [check.gate for check in checks if check.verdict == "proxy-mismatch"]


def write(path: Path, task: str, checks: Sequence[GateCheck]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "task": task,
                "tolerance": PUBLICATION_RELATIVE_PRECISION,
                "gates": [check.to_json() for check in checks],
                "trusted": [check.gate for check in checks if check.verdict == "ok"],
                "proxy_mismatch": mismatched(checks),
            },
            indent=2,
        )
        + "\n"
    )
