"""OAT screening: which parameters actually move the measured numbers?

One factor at a time, ±``delta``, at a nominal corner and the two corners
farthest from it.  The score is the largest relative change of any measured
quantity; a perturbation that breaks convergence counts as maximal influence.

Two modes, chosen by the budget:

* ``per-param`` -- every writable parameter is perturbed (cheap enough when
  ``2 × params × corners ≤ budget``);
* ``coarse-then-param`` -- groups are perturbed first, then individual
  parameters only inside the top groups (large designs such as 226-knob
  netlists).

Gate thresholds are deliberately not consulted: screening measures influence,
it does not decide pass/fail.  That belongs to the P3 objective, which uses the
public thresholds without reading reference values.
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence, TypedDict, cast

from . import bench, evaluate, params
from .sim import Corner, SimBackend

SCHEMA_VERSION = "adb-screen-1"


class ScreenOptions(TypedDict, total=False):
    delta: float
    corners: int
    max_dims: int
    top_groups: int
    budget: int | None
    prior_used: int


@dataclass(frozen=True)
class ScreenSettings:
    delta: float = 0.2
    corners: int = 3
    max_dims: int = 15
    top_groups: int = 5
    budget: int | None = None
    prior_used: int = 0

    @classmethod
    def from_options(cls, options: ScreenOptions | None) -> "ScreenSettings":
        raw = options or {}
        return cls(
            delta=float(raw.get("delta", 0.2)),
            corners=int(raw.get("corners", 3)),
            max_dims=int(raw.get("max_dims", 15)),
            top_groups=int(raw.get("top_groups", 5)),
            budget=raw.get("budget"),
            prior_used=int(raw.get("prior_used", 0)),
        )


class ScreenReport(TypedDict):
    schema_version: str
    design_hash: str
    mode: str
    delta: float
    corners: dict[str, object]
    params: int
    sims: int
    sims_run: int
    budget: dict[str, object]
    wall_seconds: float
    ranking: list[dict[str, object]]
    groups: list[dict[str, object]]
    dims: list[str]
    dims_count: int
    skipped: list[str]
    notes: list[str]
    results: list[dict[str, object]]


def nominal_index(corners: Sequence[Corner]) -> int:
    """``tt`` / 27 °C / median supply, or the least extreme point."""
    supplies = sorted(float(c["supply"]) for c in corners if c.get("supply") is not None)
    median_supply = supplies[len(supplies) // 2] if supplies else 0.0

    def score(corner: Corner) -> float:
        process = str(corner.get("process") or "")
        temperature = float(corner.get("temperature") or 27.0)
        supply = float(corner.get("supply") or 0.0)
        supply_penalty = abs(supply - median_supply) / median_supply if median_supply else 0.0
        return (0.0 if process in {"tt", ""} else 1.0) + abs(temperature - 27.0) / 100.0 + supply_penalty

    return min(range(len(corners)), key=lambda index: (score(corners[index]), index))


def _corner_distance(a: Corner, b: Corner) -> float:
    total = 0.0
    if a.get("process") != b.get("process"):
        # Process corners change the devices themselves; weight them above a
        # temperature or supply step so the stress pair is not two supply
        # points of the same process.
        total += 1.0
    if a.get("temperature") is not None and b.get("temperature") is not None:
        total += abs(float(a["temperature"]) - float(b["temperature"])) / 100.0
    if a.get("supply") is not None and b.get("supply") is not None:
        supply = float(b["supply"]) or 1.0
        total += abs(float(a["supply"]) - float(b["supply"])) / supply
    return total


def _axis_values(corners: Sequence[Corner], axis: str) -> list[float]:
    return sorted({float(c[axis]) for c in corners if c.get(axis) is not None})


def stress_indices(corners: Sequence[Corner], nominal: int, count: int) -> list[int]:
    """Pick stress corners that cover the extremes of each swept axis.

    Distance alone tends to return two corners that differ only in supply, or
    two temperatures of the same process: far from nominal, yet uninformative
    together.  Instead, score a candidate set by how many axis extremes it
    covers (distinct process values, min/max temperature, min/max supply) and
    break ties by spread.  Small enough to enumerate: at most a few thousand
    subsets for real corner assets.
    """
    candidates = [i for i in range(len(corners)) if i != nominal]
    if count <= 0 or not candidates:
        return []
    count = min(count, len(candidates))
    processes = sorted({str(c["process"]) for c in corners if c.get("process") is not None})
    temperatures = _axis_values(corners, "temperature")
    supplies = _axis_values(corners, "supply")

    def coverage(selection: Sequence[int]) -> int:
        chosen = [corners[nominal], *(corners[i] for i in selection)]
        covered = len({str(c.get("process")) for c in chosen if c.get("process") is not None})
        if len(temperatures) > 1:
            temps = {float(c["temperature"]) for c in chosen if c.get("temperature") is not None}
            covered += int(temperatures[0] in temps) + int(temperatures[-1] in temps)
        if len(supplies) > 1:
            volts = {float(c["supply"]) for c in chosen if c.get("supply") is not None}
            covered += int(supplies[0] in volts) + int(supplies[-1] in volts)
        return covered

    def spread(selection: Sequence[int]) -> float:
        anchors = [nominal, *selection]
        return min(
            _corner_distance(corners[a], corners[b])
            for position, a in enumerate(anchors)
            for b in anchors[position + 1 :]
        )

    best_key: tuple[int, float, tuple[int, ...]] | None = None
    best_selection: tuple[int, ...] = ()
    for selection in itertools.combinations(candidates, count):
        key = (coverage(selection), spread(selection), tuple(-i for i in selection))
        if best_key is None or key > best_key:
            best_key, best_selection = key, selection
    return list(best_selection)
def _as_float(value: object) -> float:
    return float(cast(float, value))


def _corner_key(corner: Mapping[str, object]) -> str:
    return json.dumps(corner, sort_keys=True, default=str)


def _variants(
    design: params.Design,
    selection: Iterable[str],
    delta: float,
    tag: str,
) -> tuple[dict[str, str], list[str]]:
    variants: dict[str, str] = {}
    skipped: list[str] = []
    index = design.by_id()
    for param_id in sorted(selection):
        param = index[param_id]
        for sign, factor in (("+", 1.0 + delta), ("-", 1.0 - delta)):
            value = param.value * factor
            if params.write_violation(param, value) is not None:
                skipped.append(f"{param_id}{sign}")
                continue
            text = design.apply({param_id: value})
            if text == design.text:
                skipped.append(f"{param_id}{sign}")
                continue
            variants[f"{tag}:{param_id}{sign}"] = text
    return variants, skipped


def _scaled_group(
    design: params.Design, members: Sequence[params.Param], delta: float, tag: str
) -> tuple[str | None, str | None]:
    """One variant that scales every member of a group; ``None`` if impossible."""
    values: dict[str, float] = {}
    for param in members:
        value = param.value * (1.0 + delta) if tag.endswith("+") else param.value * (1.0 - delta)
        if params.write_violation(param, value) is not None:
            return None, f"group {tag}: {param.id} cannot move by {delta:+.0%}"
        values[param.id] = value
    text = design.apply(values)
    if text == design.text:
        return None, f"group {tag}: perturbation changes no bytes"
    return text, None


def _relative_change(base: Mapping[str, float], other: Mapping[str, float]) -> float:
    change = 0.0
    for name, value in other.items():
        if name not in base:
            continue
        reference = base[name]
        scale = max(abs(reference), abs(value), 1e-12)
        change = max(change, min(1.0, abs(value - reference) / scale))
    return change


def _index_rows(rows: Sequence[Mapping[str, object]]) -> dict[tuple[str, str, str], Mapping[str, object]]:
    """``(variant, corner, deck) -> row``; variants without a row are absent."""
    indexed: dict[tuple[str, str, str], Mapping[str, object]] = {}
    for row in rows:
        corner = row.get("corner") or {}
        indexed[(str(row.get("variant", "")), _corner_key(cast(Mapping[str, object], corner)), str(row.get("deck", "")))] = row
    return indexed


def _score_variant(
    base: Mapping[tuple[str, str, str], Mapping[str, object]],
    variant: str,
    corner_keys: Sequence[str],
) -> tuple[float | None, dict[str, float], list[str]]:
    """Worst relative change over corners; ``None`` when nothing was measured."""
    changes: dict[str, float] = {}
    failures: list[str] = []
    compared = 0
    for corner in corner_keys:
        for deck in {key[2] for key in base if key[1] == corner}:
            reference = base.get(("base", corner, deck))
            if reference is None or reference.get("status") != "ok":
                continue
            row = base.get((variant, corner, deck))
            if row is None:
                continue
            if row.get("status") != "ok":
                changes[f"{corner}|{deck}"] = 1.0
                failures.append(f"{variant}@{corner}|{deck}: {row.get('status')}")
                continue
            measures = cast(Mapping[str, float], reference.get("measures") or {})
            other = cast(Mapping[str, float], row.get("measures") or {})
            changes[f"{corner}|{deck}"] = _relative_change(measures, other)
            compared += 1
    if compared == 0:
        return None, changes, failures
    return max(changes.values()), changes, failures


def screen(
    netlist_text: str,
    decks: Sequence[bench.Deck],
    corners: Sequence[Corner],
    options: ScreenOptions | None = None,
    backend: SimBackend | None = None,
    eval_options: evaluate.EvalOptions | None = None,
) -> ScreenReport:
    settings = ScreenSettings.from_options(options)
    if not corners:
        raise ValueError("screening needs at least one corner")
    if not 0.0 < settings.delta < 1.0:
        raise ValueError("delta must be between 0 and 1")

    started = time.perf_counter()
    design = params.load_text(netlist_text, path="screen")
    writable = [param.id for param in design.writable()]
    nominal = nominal_index(corners)
    chosen = [nominal] + stress_indices(corners, nominal, max(0, settings.corners - 1))
    chosen_corners = [corners[index] for index in chosen]
    corner_keys = [_corner_key(cast(Mapping[str, object], corner)) for corner in chosen_corners]

    per_param_cost = len(writable) * 2 * len(chosen_corners)
    affordable = settings.budget is None or per_param_cost + len(chosen_corners) <= settings.budget
    skipped: list[str] = []
    notes: list[str] = []

    rows: list[dict[str, object]] = []
    sims_run = 0
    budget_reports: list[dict[str, object]] = []

    base_options = eval_options or evaluate.EvalOptions()

    def run(variants: Mapping[str, str], corner_subset: Sequence[Corner]) -> dict[str, object]:
        nonlocal sims_run
        options_eval = replace(
            base_options,
            budget=settings.budget,
            prior_used=settings.prior_used + sims_run,
        )
        report = evaluate.evaluate_many(variants, decks, corner_subset, options_eval, backend)
        sims_run += int(report["sims_run"])
        budget_reports.append(report["budget"])
        rows.extend(cast(list[dict[str, object]], report["results"]))
        return cast(dict[str, object], report)

    ranking: list[dict[str, object]] = []
    groups_out: list[dict[str, object]] = []
    dims: list[str] = []
    mode = "per-param" if affordable else "coarse-then-param"

    if affordable:
        variants, skipped = _variants(design, writable, settings.delta, "p")
        run({"base": netlist_text, **variants}, chosen_corners)
        indexed = _index_rows(rows)
        for param_id in writable:
            plus = f"p:{param_id}+"
            minus = f"p:{param_id}-"
            score, breakdown, failures = _score_variant(indexed, plus, corner_keys)
            score_minus, breakdown_minus, failures_minus = _score_variant(indexed, minus, corner_keys)
            if score is None and score_minus is None:
                continue
            merged = {**breakdown, **breakdown_minus}
            ranking.append(
                {
                    "param": param_id,
                    "score": round(max(score or 0.0, score_minus or 0.0), 6),
                    "up": round(score or 0.0, 6),
                    "down": round(score_minus or 0.0, 6),
                    "per_corner": merged,
                }
            )
            notes.extend(failures + failures_minus)
        ranking.sort(key=lambda row: (-_as_float(row["score"]), str(row["param"])))
        dims = [str(row["param"]) for row in ranking[: settings.max_dims]]
        groups_out = _group_scores(design, ranking)
    else:
        groups = {
            name: members
            for name, members in design.groups().items()
            if any(member.writable for member in members)
        }
        group_variants: dict[str, str] = {}
        for name in sorted(groups):
            for sign in ("+", "-"):
                text, problem = _scaled_group(design, groups[name], settings.delta, f"{name}{sign}")
                if problem:
                    notes.append(problem)
                elif text is not None:
                    group_variants[f"g:{name}::{sign}"] = text
        run({"base": netlist_text, **group_variants}, chosen_corners)
        indexed = _index_rows(rows)
        for name in sorted(groups):
            up = _score_variant(indexed, f"g:{name}::+", corner_keys)
            down = _score_variant(indexed, f"g:{name}::-", corner_keys)
            if up[0] is None and down[0] is None:
                continue
            groups_out.append(
                {
                    "group": name,
                    "params": [member.id for member in groups[name]],
                    "score": round(max(up[0] or 0.0, down[0] or 0.0), 6),
                }
            )
            notes.extend(up[2] + down[2])
        groups_out.sort(key=lambda row: (-_as_float(row["score"]), str(row["group"])))
        top = {str(row["group"]) for row in groups_out[: settings.top_groups]}
        top_params = [
            member.id for name, members in groups.items() if name in top for member in members if member.writable
        ]
        variants, skipped = _variants(design, top_params, settings.delta, "p")
        if variants:
            run({"base": netlist_text, **variants}, [chosen_corners[0]])
        nominal_key = [_corner_key(cast(Mapping[str, object], chosen_corners[0]))]
        indexed = _index_rows(rows)
        for param_id in top_params:
            up = _score_variant(indexed, f"p:{param_id}+", nominal_key)
            down = _score_variant(indexed, f"p:{param_id}-", nominal_key)
            if up[0] is None and down[0] is None:
                continue
            ranking.append(
                {
                    "param": param_id,
                    "score": round(max(up[0] or 0.0, down[0] or 0.0), 6),
                    "up": round(up[0] or 0.0, 6),
                    "down": round(down[0] or 0.0, 6),
                    "per_corner": {**up[1], **down[1]},
                }
            )
            notes.extend(up[2] + down[2])
        ranking.sort(key=lambda row: (-_as_float(row["score"]), str(row["param"])))
        dims = [str(row["param"]) for row in ranking[: settings.max_dims]]

    limit = settings.budget
    used = settings.prior_used + sims_run
    budget: dict[str, object] = {
        "limit": limit,
        "prior_used": settings.prior_used,
        "used": sims_run,
        "total_used": used,
        "remaining": None if limit is None else max(0, limit - used),
        "exhausted": any(bool(report.get("exhausted")) for report in budget_reports),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "design_hash": evaluate.design_hash(netlist_text),
        "mode": mode,
        "delta": settings.delta,
        "corners": {
            "nominal": dict(chosen_corners[0]),
            "stress": [dict(corner) for corner in chosen_corners[1:]],
            "all": len(corners),
        },
        "params": len(writable),
        "sims": len(rows),
        "sims_run": sims_run,
        "budget": budget,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "ranking": ranking,
        "groups": groups_out,
        "dims": dims,
        "dims_count": len(dims),
        "skipped": skipped,
        "notes": notes[:20],
        "results": [
            {
                "case": row.get("case"),
                "variant": row.get("variant"),
                "deck": row.get("deck"),
                "status": row.get("status"),
                "measures": row.get("measures") or {},
            }
            for row in rows
        ],
    }


def _group_scores(design: params.Design, ranking: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    best: dict[str, float] = {}
    for row in ranking:
        param_id = str(row["param"])
        param = design.by_id().get(param_id)
        if param is None:
            continue
        best[param.group] = max(best.get(param.group, 0.0), _as_float(row["score"]))
    out = [
        {"group": group, "score": round(score, 6), "params": [p.id for p in design.groups()[group]]}
        for group, score in best.items()
    ]
    out.sort(key=lambda row: (-_as_float(row["score"]), str(row["group"])))
    return out


def write_report(report: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")


def summarise(report: ScreenReport, limit: int = 10) -> str:
    lines = [
        f"mode={report['mode']} params={report['params']} dims={len(report['dims'])} "
        f"sims={report['sims']} run={report['sims_run']} budget={report['budget']['total_used']}"
    ]
    for row in report["ranking"][:limit]:
        per = row["per_corner"]
        worst = max((float(v) for v in cast(Mapping[str, float], per).values()), default=0.0)
        lines.append(f"{_as_float(row['score']):8.3f}  {row['param']:<32} worst={worst:.3f}")
    return "\n".join(lines)
