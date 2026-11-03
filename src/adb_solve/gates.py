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


def loss(
    gate_margins: Mapping[str, float | None],
    failures: int,
    tau: float = 0.1,
    lambda_fail: float = 2.0,
    regularization: float = 0.0,
    lambda_reg: float = 0.05,
) -> float:
    """Scalar objective: violated margins, failed simulations, distance from x0."""
    total = 0.0
    for margin in gate_margins.values():
        if margin is None:
            total += 1.0  # unmeasured is not a pass
            continue
        total += _softplus(-margin / tau)
    total += lambda_fail * failures
    total += lambda_reg * regularization
    return total


def _softplus(x: float) -> float:
    if x > 30:
        return x
    if x < -30:
        return math.exp(x)
    return math.log1p(math.exp(x))


def feasible(gate_margins: Mapping[str, float | None]) -> bool:
    return bool(gate_margins) and all(m is not None and m >= 0 for m in gate_margins.values())
