"""Differential-evolution sizing loop over the screened dimensions.

The loop is staged: it optimises on a small corner subset (nominal + stress by
default) and leaves full-PVT validation to the caller / official verifier.
Every generation is one ``evaluate_many`` batch, so the cache, the budget and
the container lifecycle are shared across the whole generation.

Selection is by :mod:`adb_solve.gates` loss; a candidate with a failed
simulation is never preferred over one that converged.  Repair clamps into the
*search box* (advisory) and re-checks hard legality, then falls back to the
reference value for a dimension it cannot legalise.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence, TypedDict, cast

from . import bench, evaluate, gates as gate_lib, params, screen
from .sim import Corner, SimBackend

SCHEMA_VERSION = "adb-solve-1"


class SolveOptions(TypedDict, total=False):
    dims: list[str]
    population: int
    generations: int
    f: float
    cr: float
    seed: int
    tau: float
    budget: int | None
    bound: float
    quantize: bool
    lambda_fail: float
    lambda_reg: float
    corner_mode: str
    corners_count: int
    rounds: int
    feed_corners: int


@dataclass(frozen=True)
class Settings:
    dims: tuple[str, ...] = ()
    population: int = 12
    generations: int = 20
    f: float = 0.6
    cr: float = 0.7
    seed: int = 0
    tau: float = 0.1
    budget: int | None = None
    bound: float = 0.5
    quantize: bool = False
    lambda_fail: float = 2.0
    lambda_reg: float = 0.05
    corner_mode: str = "worst"
    corners_count: int = 4
    rounds: int = 2
    feed_corners: int = 2

    @classmethod
    def from_options(cls, options: SolveOptions | None) -> "Settings":
        raw = options or {}
        return cls(
            dims=tuple(raw.get("dims", ())),
            population=int(raw.get("population", 12)),
            generations=int(raw.get("generations", 20)),
            f=float(raw.get("f", 0.6)),
            cr=float(raw.get("cr", 0.7)),
            seed=int(raw.get("seed", 0)),
            tau=float(raw.get("tau", 0.1)),
            budget=raw.get("budget"),
            bound=float(raw.get("bound", 0.5)),
            quantize=bool(raw.get("quantize", False)),
            lambda_fail=float(raw.get("lambda_fail", 2.0)),
            lambda_reg=float(raw.get("lambda_reg", 0.05)),
            corner_mode=str(raw.get("corner_mode", "worst")),
            corners_count=int(raw.get("corners_count", 4)),
            rounds=int(raw.get("rounds", 2)),
            feed_corners=int(raw.get("feed_corners", 2)),
        )


class CandidateReport(TypedDict, total=False):
    values: dict[str, float]
    margins: dict[str, float | None]
    loss: float
    feasible: bool
    failures: int
    regularization: float


class SolveReport(TypedDict):
    schema_version: str
    design_hash: str
    dims: list[str]
    gates: dict[str, object]
    reference: CandidateReport
    best: CandidateReport
    sims: int
    sims_run: int
    sims_to_feasible: int | None
    budget: dict[str, object]
    wall_seconds: float
    search_corners: list[dict[str, float | int | str]]
    validation: dict[str, object]
    validation_rounds: list[dict[str, object]]
    history: list[dict[str, object]]
    notes: list[str]


@dataclass
class _Candidate:
    values: dict[str, float]
    text: str
    loss: float = math.inf
    margins: dict[str, float | None] = field(default_factory=dict)
    failures: int = 0
    regularization: float = 0.0
    feasible: bool = False
    evaluated_at: int = 0

    def report(self) -> CandidateReport:
        return {
            "values": {key: float(value) for key, value in self.values.items()},
            "margins": dict(self.margins),
            "loss": round(self.loss, 6),
            "feasible": self.feasible,
            "failures": self.failures,
            "regularization": round(self.regularization, 6),
        }


def _reference_values(design: params.Design, dims: Sequence[str]) -> dict[str, float]:
    index = design.by_id()
    return {name: index[name].value for name in dims}


def _regularization(values: Mapping[str, float], reference: Mapping[str, float]) -> float:
    ratios = [math.log(values[name] / reference[name]) for name in values if reference.get(name)]
    return sum(r * r for r in ratios) / len(ratios) if ratios else 0.0


def repair(
    design: params.Design,
    values: Mapping[str, float],
    reference: Mapping[str, float],
    *,
    quantize: bool = False,
) -> dict[str, float]:
    """Clamp into the search box, optionally snap to an integer grid, keep legality.

    The search box is advice, not law: a clamp only narrows the proposal.  If a
    repaired value would be illegal, the reference value for that dimension is
    restored, so a proposal never leaves this function illegal.
    """
    box = design.search_box()
    index = design.by_id()
    out: dict[str, float] = {}
    for name, value in values.items():
        param = index[name]
        bounds = box.get(name)
        candidate = float(value)
        if bounds is not None:
            if bounds.minimum is not None:
                candidate = max(candidate, bounds.minimum)
            if bounds.maximum is not None:
                candidate = min(candidate, bounds.maximum)
            if quantize and bounds.grid:
                candidate = round(candidate / bounds.grid) * bounds.grid
        if params.write_violation(param, candidate) is not None:
            candidate = reference.get(name, param.value)
        out[name] = candidate
    return out


def _render(design: params.Design, values: Mapping[str, float], reference: Mapping[str, float]) -> str:
    changed = {name: value for name, value in values.items() if value != reference.get(name)}
    if not changed:
        return design.text
    return design.apply(changed)


def _score(
    rows: Sequence[Mapping[str, Any]],
    gate_set: gate_lib.GateSet,
    values: Mapping[str, float],
    reference: Mapping[str, float],
    settings: Settings,
) -> tuple[dict[str, float | None], int, float, float, bool]:
    margins = gate_lib.margins(gate_set, rows)
    failures = gate_lib.failed_cases(rows)
    regularization = _regularization(values, reference)
    value = gate_lib.loss(
        margins,
        failures,
        tau=settings.tau,
        lambda_fail=settings.lambda_fail,
        regularization=regularization,
        lambda_reg=settings.lambda_reg,
    )
    return margins, failures, regularization, value, gate_lib.feasible(margins) and failures == 0


def _score_batch(
    report: Mapping[str, Any],
    labelled: Mapping[str, _Candidate],
    gate_set: gate_lib.GateSet,
    reference: Mapping[str, float],
    settings: Settings,
    used_before: int,
) -> None:
    by_variant: dict[str, list[Mapping[str, Any]]] = {}
    for row in cast(Sequence[Mapping[str, Any]], report.get("results") or []):
        by_variant.setdefault(str(row.get("variant", "")), []).append(row)
    for label, candidate in labelled.items():
        rows = by_variant.get(label, [])
        if not rows:
            continue
        margins, failures, regularization, value, feasible = _score(
            rows, gate_set, candidate.values, reference, settings
        )
        candidate.margins = margins
        candidate.failures = failures
        candidate.regularization = regularization
        candidate.loss = value
        candidate.feasible = feasible
        candidate.evaluated_at = used_before


def solve(
    netlist_text: str,
    decks: Sequence[bench.Deck],
    corners: Sequence[Corner],
    payload: Mapping[str, Any],
    options: SolveOptions | None = None,
    backend: SimBackend | None = None,
    eval_options: evaluate.EvalOptions | None = None,
    validation_corners: Sequence[Corner] | None = None,
) -> SolveReport:
    """Optimise ``dims`` against the task's public gate specs.

    Three fidelity stages share one budget, because a full-PVT generation costs
    30x a nominal one:

    1. probe: one sweep of the input to resolve gates and rank corners;
    2. search: DE on the nominal corner (cheap, many candidates), then on the
       binding corners (expensive, fewer candidates) seeded by the first phase;
    3. validation: the best candidate on every corner.  A search-feasible
       candidate that fails validation is reported as such, never accepted.
    """
    settings = Settings.from_options(options)
    if not settings.dims:
        raise ValueError("solve needs at least one dimension; run `screen` first")
    started = time.perf_counter()
    design = params.load_text(netlist_text, path="solve")
    index = design.by_id()
    missing = [name for name in settings.dims if name not in index]
    if missing:
        raise ValueError(f"unknown dimensions: {', '.join(missing)}")
    reference = _reference_values(design, settings.dims)
    base = eval_options or evaluate.EvalOptions()

    rounds = max(1, settings.rounds)
    reserve = rounds * len(validation_corners) * max(1, len(decks)) if validation_corners else 0
    search_budget = None if settings.budget is None else max(0, settings.budget - reserve)
    used = 0
    sims_to_feasible: int | None = None
    notes: list[str] = []

    gate_set: gate_lib.GateSet | None = None
    search_corners: list[Corner] = [dict(corner) for corner in corners]
    phases: list[tuple[Sequence[Corner], float]] = []
    nominal_corner = dict(corners[screen.nominal_index(corners)]) if len(corners) > 1 else None
    if settings.corner_mode == "worst" and len(corners) > 1 and settings.corners_count < len(corners):
        probe = evaluate.evaluate_many(
            {"probe": netlist_text},
            decks,
            corners,
            replace(base, budget=search_budget, prior_used=0),
            backend,
        )
        used += int(probe["sims_run"])
        gate_set = gate_lib.from_task(payload, _measure_names(probe))
        if not gate_set.gates:
            raise ValueError("no gate could be resolved from the task's public specs")
        worst = _worst_corners(probe, gate_set, settings.corners_count, corners)
        nominal = [nominal_corner] if nominal_corner is not None else search_corners
        # 40% of the search goes to cheap nominal work; the rest to the corners
        # that actually bind, seeded by whatever the cheap phase found.
        phases = [(nominal, 0.4), (worst, 1.0)]
        search_corners = worst
    else:
        phases = [(search_corners, 1.0)]

    rng = random.Random(settings.seed)

    def spawn(values: Mapping[str, float]) -> _Candidate:
        repaired = repair(design, values, reference, quantize=settings.quantize)
        text = _render(design, repaired, reference)
        return _Candidate(values=repaired, text=text)

    batch = 0

    def run_batch(
        candidates: Sequence[_Candidate], phase_corners: Sequence[Corner]
    ) -> tuple[evaluate.EvalManyReport, dict[str, _Candidate], int]:
        nonlocal used, batch
        unique: dict[str, _Candidate] = {}
        for candidate in candidates:
            unique.setdefault(candidate.text, candidate)
        labelled = {f"c{batch}{position}": candidate for position, candidate in enumerate(unique.values())}
        batch += 1
        report = evaluate.evaluate_many(
            {label: candidate.text for label, candidate in labelled.items()},
            decks,
            phase_corners,
            replace(base, budget=search_budget, prior_used=used),
            backend,
        )
        spent = int(report["sims_run"])
        used += spent
        return report, labelled, spent

    def init_population(size: int) -> list[_Candidate]:
        population = [spawn(reference)]
        for _ in range(size - 1):
            population.append(
                spawn(
                    {
                        name: reference[name] * math.exp(rng.uniform(-settings.bound, settings.bound))
                        for name in settings.dims
                    }
                )
            )
        return population

    def evolve(
        population: list[_Candidate],
        phase_corners: Sequence[Corner],
        cap: int | None,
        phase_index: int,
        final_phase: bool = False,
    ) -> list[_Candidate]:
        nonlocal gate_set, sims_to_feasible
        cases = max(1, len(decks) * len(phase_corners))
        size = max(2, settings.population)
        if cap is not None:
            size = max(2, min(size, max(1, (cap - used) // cases)))
        if not population:
            population = init_population(size)
        # A phase with a different corner set makes every stored loss stale;
        # comparing a one-corner loss with a two-corner loss would keep the
        # cheaper candidate forever.  Re-score the population first.
        for candidate in population:
            candidate.loss = math.inf
            candidate.margins = {}
            candidate.feasible = False
        if gate_set is None:
            report, labelled, _ = run_batch(population, phase_corners)
            resolved = gate_lib.from_task(payload, _measure_names(report))
            if not resolved.gates:
                raise ValueError("no gate could be resolved from the task's public specs")
            gate_set = resolved
            notes.extend(f"unresolved gate: {gate}" for gate in resolved.unresolved)
            _score_batch(report, labelled, resolved, reference, settings, used)
        else:
            pending = [candidate for candidate in population if candidate.loss == math.inf]
            if pending:
                report, labelled, _ = run_batch(pending, phase_corners)
                _score_batch(report, labelled, gate_set, reference, settings, used)
        history.append(_generation_row(len(history), population, used, phase_index))
        generation = 0
        while generation < settings.generations:
            generation += 1
            if cap is not None and used + cases > cap:
                notes.append(f"phase {phase_index}: budget reached after {used} simulations")
                break
            trials: list[_Candidate] = []
            for target in range(len(population)):
                picks = [i for i in range(len(population)) if i != target]
                if len(picks) < 3:
                    break
                a, b, c = (population[i] for i in rng.sample(picks, 3))
                trial_values: dict[str, float] = {}
                for name in settings.dims:
                    if rng.random() < settings.cr:
                        mutant = a.values[name] + settings.f * (b.values[name] - c.values[name])
                        trial_values[name] = mutant if mutant > 0 else reference[name]
                    else:
                        trial_values[name] = population[target].values[name]
                trials.append(spawn(trial_values))
            if not trials:
                break
            report, labelled, spent = run_batch(trials, phase_corners)
            _score_batch(report, labelled, gate_set, reference, settings, used)
            for position, trial in enumerate(trials):
                if trial.loss < population[position].loss:
                    population[position] = trial
            best = min(population, key=lambda candidate: candidate.loss)
            history.append(_generation_row(len(history), population, used, phase_index))
            if final_phase and sims_to_feasible is None and best.feasible:
                sims_to_feasible = used
            if spent == 0 and cap is not None and used >= cap:
                break
        return population

    def validate(candidate: _Candidate) -> dict[str, object]:
        nonlocal used, gate_set
        if not validation_corners:
            return {"skipped": True, "reason": "no validation corners"}
        cost = len(validation_corners) * max(1, len(decks))
        remaining = None if settings.budget is None else settings.budget - used
        if remaining is not None and remaining < cost:
            notes.append(f"validation skipped: needs {cost} sims, {remaining} left")
            return {
                "skipped": True,
                "reason": f"needs {cost} sims, {remaining} left",
                "corners": len(validation_corners),
            }
        if gate_set is None:
            raise ValueError("no gate could be resolved from the task's public specs")
        report = evaluate.evaluate_many(
            {"validate": candidate.text},
            decks,
            validation_corners,
            replace(base, budget=settings.budget, prior_used=used),
            backend,
        )
        spent = int(report["sims_run"])
        used += spent
        rows = [
            row
            for row in cast(Sequence[Mapping[str, Any]], report.get("results") or [])
            if row.get("variant") == "validate"
        ]
        margins = gate_lib.margins(gate_set, rows)
        failures = gate_lib.failed_cases(rows)
        worst = _worst_corners(report, gate_set, settings.feed_corners, validation_corners)
        return {
            "skipped": False,
            "corners": len(validation_corners),
            "sims": spent,
            "margins": margins,
            "failures": failures,
            "feasible": gate_lib.feasible(margins) and failures == 0,
            "worst": worst,
        }

    history: list[dict[str, object]] = []
    population: list[_Candidate] = []

    validation: dict[str, object] = {"skipped": True, "reason": "not run"}
    validation_rounds: list[dict[str, object]] = []
    feedback: list[Corner] = []

    for round_index in range(max(1, settings.rounds)):
        if round_index == 0:
            for phase_index, (phase_corners, share) in enumerate(phases):
                cap = None if search_budget is None else int(search_budget * share)
                population = evolve(
                    population, phase_corners, cap, phase_index,
                    final_phase=phase_index == len(phases) - 1,
                )
        else:
            if not feedback:
                break
            # The corner set changed, so the old optimum is probably no longer
            # the right basin.  Keep the best half and respawn the rest around
            # it with a wider spread; without this, DE cannot cross a 2x gap.
            ranked = sorted(population, key=lambda candidate: candidate.loss)
            half = max(1, len(ranked) // 2)
            width = settings.bound * 1.5
            population = ranked[:half] + [
                spawn(
                    {
                        name: ranked[0].values[name] * math.exp(rng.uniform(-width, width))
                        for name in settings.dims
                    }
                )
                for _ in range(len(ranked) - half)
            ]
            cap = None if search_budget is None else search_budget - used
            population = evolve(population, [*search_corners, *feedback], cap, 10 + round_index, final_phase=True)
        best = min(population, key=lambda candidate: candidate.loss)
        validation = validate(best)
        validation_rounds.append(
            {"round": round_index, "sims": used, **{key: value for key, value in validation.items() if key != "margins"}}
        )
        if validation.get("skipped") or validation.get("feasible"):
            break
        fresh = [
            corner
            for corner in cast(Sequence[Corner], validation.get("worst") or [])
            if corner not in search_corners and corner not in feedback
        ]
        if not fresh:
            notes.append("validation failed on corners already being searched")
            break
        feedback.extend(fresh)
        notes.append(f"round {round_index}: fed {len(fresh)} validation corner(s) back into the search")

    if gate_set is None:
        raise ValueError("no gate could be resolved from the task's public specs")
    limit = settings.budget
    return {
        "schema_version": SCHEMA_VERSION,
        "design_hash": evaluate.design_hash(netlist_text),
        "dims": list(settings.dims),
        "gates": gate_set.to_json(),
        "reference": population[0].report(),
        "best": best.report(),
        "sims": used,
        "sims_run": used,
        "sims_to_feasible": sims_to_feasible,
        "budget": {
            "limit": limit,
            "used": used,
            "remaining": None if limit is None else max(0, limit - used),
            "exhausted": bool(limit is not None and used >= limit),
        },
        "search_corners": [dict(corner) for corner in search_corners],
        "validation": validation,
        "validation_rounds": validation_rounds,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "history": history,
        "notes": notes,
    }


def _worst_corners(
    report: Mapping[str, Any],
    gate_set: gate_lib.GateSet,
    limit: int,
    corners: Sequence[Corner],
) -> list[Corner]:
    """Union of each gate's worst corner, then the next-smallest margins.

    Ranking corners by one global margin misses corners that bind a single gate
    (the CMRR corner is fine for gain, so a global ranking skips it and the
    optimiser optimises the wrong corner).
    """
    order = {
        json.dumps(dict(corner), sort_keys=True, default=str): index
        for index, corner in enumerate(corners)
    }
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in cast(Sequence[Mapping[str, Any]], report.get("results") or []):
        key = json.dumps(dict(cast(Mapping[str, Any], row.get("corner") or {})), sort_keys=True, default=str)
        grouped.setdefault(key, []).append(row)
    per_corner = {
        key: gate_lib.margins(gate_set, rows) for key, rows in grouped.items()
    }
    chosen: list[str] = []
    for gate in gate_set.gates:
        candidates = [
            (margins[gate.id], key)
            for key, margins in per_corner.items()
            if margins.get(gate.id) is not None
        ]
        if not candidates:
            continue
        _, key = min(candidates)
        if key not in chosen:
            chosen.append(key)
    remaining = sorted(
        (key for key in per_corner if key not in chosen),
        key=lambda key: (
            min((value if value is not None else -math.inf) for value in per_corner[key].values()),
            order.get(key, len(order)),
        ),
    )
    ordered = (chosen + remaining)[:limit]
    return [dict(corners[order[key]]) for key in ordered if key in order]


def _measure_names(report: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for row in cast(Sequence[Mapping[str, Any]], report.get("results") or []):
        names.update(str(name) for name in (row.get("measures") or {}))
    return names


def _generation_row(
    generation: int, population: Sequence[_Candidate], used: int, phase: int = 0
) -> dict[str, object]:
    return {
        "generation": generation,
        "phase": phase,
        "sims": used,
        "best_loss": round(min(candidate.loss for candidate in population), 6),
        "best_margin": _min_margin(min(population, key=lambda candidate: candidate.loss)),
        "feasible": sum(1 for candidate in population if candidate.feasible),
    }


def _min_margin(candidate: _Candidate) -> float | None:
    values = [m for m in candidate.margins.values() if m is not None]
    return round(min(values), 6) if values else None


def write_report(report: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")


def summarise(report: SolveReport) -> str:
    ref = report["reference"]
    best = report["best"]
    lines = [
        f"dims={len(report['dims'])} sims={report['sims']} "
        f"feasible={best['feasible']} sims_to_feasible={report['sims_to_feasible']} "
        f"budget={report['budget']['used']}/{report['budget']['limit']}",
        f"reference loss={ref['loss']:.4f}  best loss={best['loss']:.4f}",
    ]
    for gate_id, margin in best["margins"].items():
        shown = "n/a" if margin is None else f"{margin:+.3f}"
        lines.append(f"  {gate_id:<24} {shown}")
    validation = report["validation"]
    if isinstance(validation, dict) and not validation.get("skipped"):
        lines.append(f"validation: corners={validation['corners']} sims={validation['sims']} feasible={validation['feasible']}")
        for gate_id, margin in cast(Mapping[str, float | None], validation["margins"]).items():
            shown = "n/a" if margin is None else f"{margin:+.3f}"
            lines.append(f"  {gate_id:<24} {shown}")
    elif isinstance(validation, dict):
        lines.append(f"validation: skipped ({validation.get('reason')})")
    return "\n".join(lines)
