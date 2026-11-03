"""Public gate specs and the objective built on top of them.

The thresholds are published in ``referenceResult.metrics`` (the same
``specification`` strings the leaderboard shows); reference *values* are never
read.  The quantity behind a gate is matched to the deck measures we already
parse (``m0_ugb_hz`` → ``ugb_hz`` → gate ``ugb_100mhz``); when the match is
ambiguous the gate is reported as unresolved instead of guessed.

Aggregation is the conservative one: the worst measured value over every case
and every in-deck sweep point.  A deck may sweep 3 supplies × 3 temperatures
internally (``m0..m8``), so the worst over those points is what a worst-case
signoff sees.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Collection, Mapping, Sequence

PREFIX = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}

BASE_UNITS = {
    "hz": "Hz",
    "w": "W",
    "v": "V",
    "vrms": "V",
    "vpp": "V",
    "a": "A",
    "s": "s",
    "db": "dB",
    "deg": "deg",
}

SPEC_RE = re.compile(r"([<>]=?)\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*([A-Za-zµ°%/]*)\s*$")
INDEX_RE = re.compile(r"^m[0-9]+_")


class GateError(ValueError):
    """The public spec cannot be turned into a gate."""


@dataclass(frozen=True)
class Gate:
    id: str
    quantity: str
    measure: str
    threshold: float
    higher_is_better: bool
    unit: str

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "measure": self.measure,
            "threshold": self.threshold,
            "higher_is_better": self.higher_is_better,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class GateSet:
    gates: tuple[Gate, ...]
    unresolved: tuple[str, ...] = ()
    source: str = "referenceResult.metrics"

    def to_json(self) -> dict[str, object]:
        return {
            "gates": [gate.to_json() for gate in self.gates],
            "unresolved": list(self.unresolved),
            "source": self.source,
        }


def unit_of(measure: str) -> str | None:
    return BASE_UNITS.get(measure.rsplit("_", 1)[-1].lower())


def to_base(value: float, unit: str) -> float:
    """Convert ``value`` expressed in ``unit`` to the base unit (Hz, W, V ...)."""
    if not unit:
        return value
    if unit in {"dB", "deg", "%", "°"}:
        return value
    prefix = unit[0]
    rest = unit[1:]
    if prefix in PREFIX and rest.lower() in {"hz", "w", "v", "vrms", "a", "s"}:
        return value * PREFIX[prefix]
    if unit.lower().endswith("vrms"):
        return to_base(value, unit[: -len("vrms")] + "V")
    return value


def base_value(metric: Mapping[str, Any]) -> float | None:
    """A published metric value converted into the measure's base units.

    ``referenceResult.metrics[].value`` is printed in display units (111.3 MHz,
    0.4223 mW); the spec string carries that unit, so the same conversion used
    for thresholds applies here.  Reference *values* are read only to check our
    proxy, never to steer it.
    """
    raw = metric.get("value")
    if not isinstance(raw, (int, float)):
        return None
    spec = str(metric.get("spec") or metric.get("specification") or "")
    spec = spec.replace("≥", ">=").replace("≤", "<=")
    match = SPEC_RE.search(spec)
    if match is None:
        return None
    return to_base(float(raw), match.group(3))


def base_of(measure: str) -> str:
    """``m3_dc_gain_db`` → ``dc_gain_db``: the in-deck sweep index is not a name."""
    return INDEX_RE.sub("", measure)


def from_task(payload: Mapping[str, Any], measures: Collection[str]) -> GateSet:
    """Build gates from ``referenceResult.metrics`` and the measures actually seen."""
    result = payload.get("referenceResult") or {}
    metrics = result.get("metrics") or []
    names = {base_of(name) for name in measures}
    gates: list[Gate] = []
    unresolved: list[str] = []
    for metric in metrics:
        gate = _parse_metric(metric, names)
        if gate is None:
            unresolved.append(str(metric.get("id", "?")))
        else:
            gates.append(gate)
    return GateSet(gates=tuple(gates), unresolved=tuple(unresolved))


def _parse_metric(metric: Mapping[str, Any], names: Collection[str]) -> Gate | None:
    identifier = str(metric.get("id") or "")
    if not identifier:
        return None
    spec = str(metric.get("spec") or metric.get("specification") or "")
    # T0 publishes "≥ 40 dB"; the task package's reference.json uses ">=".
    spec = spec.replace("≥", ">=").replace("≤", "<=").replace("\u2265", ">=").replace("\u2264", "<=")
    match = SPEC_RE.search(spec)
    if match is None:
        return None
    operator, raw_threshold, raw_unit = match.groups()
    quantity = re.sub(r"_[0-9][^_]*$", "", identifier) or identifier
    measure = _resolve(quantity, names)
    if measure is None:
        return None
    unit = unit_of(measure) or ""
    threshold = to_base(float(raw_threshold), raw_unit)
    return Gate(
        id=identifier,
        quantity=quantity,
        measure=measure,
        threshold=threshold,
        higher_is_better=operator.startswith(">"),
        unit=unit,
    )


def _resolve(quantity: str, names: Collection[str]) -> str | None:
    """Match ``gain`` to ``dc_gain_db`` but not to ``phase_at_ugb_deg``.

    Tiers, strongest first; a tier with more than one hit is ambiguous and the
    gate is left unresolved rather than guessed.
    """
    if quantity in names:
        return quantity
    tiers = [
        [name for name in names if name.startswith(f"{quantity}_")],
        [name for name in names if name.endswith(f"_{quantity}")],
        [name for name in names if quantity in name.split("_")],
        [name for name in names if quantity in name],
    ]
    for tier in tiers:
        if len(tier) == 1:
            return tier[0]
        if len(tier) > 1:
            return None
    return None


def margins(gates: GateSet, rows: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
    """Normalised worst-case margin per gate; ``None`` when nothing was measured."""
    values: dict[str, list[float]] = {}
    for row in rows:
        if row.get("status") != "ok":
            continue
        for name, value in (row.get("measures") or {}).items():
            values.setdefault(base_of(str(name)), []).append(float(value))
    out: dict[str, float | None] = {}
    for gate in gates.gates:
        samples = values.get(gate.measure) or []
        if not samples:
            out[gate.id] = None
            continue
        worst = min(samples) if gate.higher_is_better else max(samples)
        scale = abs(gate.threshold) if gate.threshold else 1.0
        signed = (worst - gate.threshold) if gate.higher_is_better else (gate.threshold - worst)
        out[gate.id] = signed / scale
    return out


def failed_cases(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for row in rows if row.get("status") != "ok")


@dataclass(frozen=True, order=True)
class Score:
    """Lexicographic objective: feasibility, then the worst violation, then cost.

    Ordering is the point: a candidate that violates a gate is always worse than
    one that does not, regardless of how smooth its other margins are.  Written
    as tuple ordering instead of ``sum + BIG * violation`` so no weight has to
    be invented (and none can be too small to matter).
    """

    violated: bool
    worst_violation: float
    total: float

    def to_json(self) -> dict[str, float | bool]:
        return {
            "violated": self.violated,
            "worst_violation": round(self.worst_violation, 6),
            "total": round(self.total, 6),
        }


def expected_counts(gate_set: GateSet, rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """How many measurements each gate produced in a *complete* evaluation.

    Taken from the baseline run: it defines which decks emit which measure, so
    a candidate that loses a whole deck (or a corner) is detected instead of
    silently scoring on the surviving subset.
    """
    counts: dict[str, int] = {}
    for row in rows:
        for name in (row.get("measures") or {}):
            base = base_of(str(name))
            counts[base] = counts.get(base, 0) + 1
    return {gate.id: counts.get(gate.measure, 0) for gate in gate_set.gates}


def coverage_complete(
    gate_set: GateSet,
    rows: Sequence[Mapping[str, Any]],
    expected: Mapping[str, int],
) -> bool:
    counts: dict[str, int] = {}
    for row in rows:
        for name in (row.get("measures") or {}):
            base = base_of(str(name))
            counts[base] = counts.get(base, 0) + 1
    for gate in gate_set.gates:
        want = expected.get(gate.id, 0)
        if want and counts.get(gate.measure, 0) < want:
            return False
    return True


def score(
    gate_margins: Mapping[str, float | None],
    failures: int,
    tau: float = 0.1,
    lambda_fail: float = 2.0,
    regularization: float = 0.0,
    lambda_reg: float = 0.05,
    incomplete: bool = False,
) -> Score:
    """Lexicographic score; ``tau`` is the margin scale at which a violation hurts.

    ``tau`` is part of the objective's definition (docs/REQUIREMENTS.md), not a
    searching heuristic: it says a margin of ``-tau`` costs about one unit.
    """
    total = 0.0
    worst = 0.0
    unmeasured = False
    for margin in gate_margins.values():
        if margin is None:
            unmeasured = True  # unmeasured is not a pass
            continue
        worst = max(worst, -margin)
        total += _softplus(-margin / tau)
    total += lambda_fail * failures
    total += lambda_reg * regularization
    # A failed simulation means unmeasured gates; incomplete coverage means some
    # gates were measured over fewer cases than the baseline.  Neither is a
    # pass, and both belong in the first (lexicographic) field -- otherwise a
    # candidate with positive margins and a dead deck outranks a fully measured
    # candidate that misses one gate.
    if unmeasured or incomplete or failures:
        return Score(violated=True, worst_violation=math.inf, total=total)
    return Score(violated=worst > 0.0, worst_violation=worst, total=total)


def _softplus(x: float) -> float:
    if x > 30:
        return x
    if x < -30:
        return math.exp(x)
    return math.log1p(math.exp(x))


def feasible(gate_margins: Mapping[str, float | None]) -> bool:
    return bool(gate_margins) and all(m is not None and m >= 0 for m in gate_margins.values())
