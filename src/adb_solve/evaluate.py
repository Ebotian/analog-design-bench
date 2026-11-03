"""Bench × corner evaluation with caching.

``evaluate`` is the single entry point the CLI, the future optimiser and the
sizing gym share: give it a netlist, some decks and a corner matrix, get one
status + measure set per case.  It owns orchestration only -- rendering lives
in :mod:`adb_solve.bench`, simulation behind :class:`adb_solve.sim.SimBackend`,
caching in :mod:`adb_solve.cache`, so it can be tested with a fake backend.

Post-processing into gates is deliberately absent: P1 measures, P2/P3 decide
pass/fail, so this module never interprets a number as "pass".
"""

from __future__ import annotations

import hashlib
import itertools
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence, TypedDict, cast

from . import bench
from .cache import Cache, hash_inputs
from .corners import SweepKeys
from .docker_backend import DEFAULT_IMAGE, DockerBackend, image_id
from .sim import Corner, CornerValue, SimBackend, SimCase, SimResult, SimResultPayload

SCHEMA_VERSION = "adb-eval-1"


class CaseReport(SimResultPayload, total=False):
    """One result row in an evaluation report."""

    deck: str
    log_tail: str


class EvalReport(TypedDict):
    schema_version: str
    design_hash: str
    sims: int
    sims_run: int
    wall_seconds: float
    cache: dict[str, object]
    budget: dict[str, object]
    status_counts: dict[str, int]
    results: list[CaseReport]


@dataclass(frozen=True)
class EvalOptions:
    image: str = DEFAULT_IMAGE
    jobs: int = 1
    timeout: float = 120.0
    cache_root: Path = Path(".adb-cache")
    use_cache: bool = True
    work_root: Path = Path(".work/sim")
    oneshot: bool = False
    budget: int | None = None
    prior_used: int = 0
    memory: str | None = "4g"


def design_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def corner_matrix(
    *,
    processes: Sequence[str] | None = None,
    temperatures: Sequence[float] | None = None,
    values: dict[str, CornerValue] | None = None,
) -> list[Corner]:
    """Cartesian product of process × temperature, plus fixed deck values."""
    base: dict[str, CornerValue] = dict(values or {})
    corners: list[Corner] = []
    process_options: list[str | None] = list(processes) if processes else [None]
    temperature_options: list[float | None] = list(temperatures) if temperatures else [None]
    for process, temperature in itertools.product(process_options, temperature_options):
        corner: dict[str, CornerValue] = dict(base)
        if process is not None:
            corner["process"] = process
        if temperature is not None:
            corner["temperature"] = temperature
        corners.append(corner)
    return corners


def build_cases(
    netlist_text: str,
    decks: Sequence[bench.Deck],
    corners: Sequence[Corner],
    keys: SweepKeys | None = None,
) -> list[SimCase]:
    """Render every deck at every corner into a simulation case.

    ``keys`` names the placeholder that carries each swept dimension (the
    corner asset may call the process placeholder ``CORNER`` rather than
    ``process``); the same names drive the ``.lib``/``.temp`` rewrites.
    """
    keys = keys or SweepKeys()
    cases: list[SimCase] = []
    for deck in decks:
        for index, corner in enumerate(corners):
            # Every corner entry is also available as a deck value: decks use
            # {temperature} / {supply} placeholders as well as literal corners.
            text = deck.render(dict(corner))
            if corner.get(keys.process) is not None:
                text = bench.rewrite_process(text, str(corner[keys.process]))
            if corner.get(keys.temperature) is not None:
                text = bench.rewrite_temperature(
                    text, float(cast(float, corner[keys.temperature]))
                )
            cases.append(
                SimCase(
                    case_id=f"{deck.name}[{index}]",
                    netlist=netlist_text,
                    deck_name=deck.name,
                    deck_text=text,
                    corner=corner,
                )
            )
    return cases


def _backend(options: EvalOptions) -> DockerBackend:
    return DockerBackend(
        image=options.image,
        jobs=options.jobs,
        timeout=options.timeout,
        root=options.work_root,
        oneshot=options.oneshot,
        memory=options.memory,
    )


def _cache_payload(raw: dict[str, object]) -> SimResultPayload:
    return cast(SimResultPayload, raw)


def evaluate(
    netlist_text: str,
    decks: Sequence[bench.Deck],
    corners: Sequence[Corner],
    options: EvalOptions | None = None,
    backend: SimBackend | None = None,
    keys: SweepKeys | None = None,
) -> EvalReport:
    """Simulate ``netlist_text`` over ``decks × corners`` and return a report."""
    options = options or EvalOptions()
    if options.budget is not None and options.budget < 0:
        raise ValueError("budget must be >= 0")
    if options.prior_used < 0:
        raise ValueError("prior_used must be >= 0")
    started = time.perf_counter()
    cases = build_cases(netlist_text, decks, corners, keys)
    cache = Cache(options.cache_root, namespace="sim", enabled=options.use_cache)

    try:
        image = image_id(options.image) if options.use_cache else options.image
    except Exception:  # backend may be a fake; image identity is best-effort
        image = options.image

    cache_keys = {
        case.case_id: hash_inputs(image, case.key_material(), str(options.timeout))
        for case in cases
    }
    cached: dict[str, SimResultPayload] = {}
    pending: list[SimCase] = []
    for case in cases:
        hit = cache.get(cache_keys[case.case_id])
        if hit is None:
            pending.append(case)
        else:
            cached[case.case_id] = _cache_payload(hit)

    # Budget counts simulations that actually run, not cache hits: re-reading a
    # cached result spends nothing.  ``prior_used`` comes from a resumed report,
    # so the cap is a total across invocations.  Truncation is deterministic
    # (deck × corner order), so a resumed run continues where this one stopped.
    if options.budget is None:
        allowed = pending
    else:
        remaining = max(0, options.budget - options.prior_used)
        allowed = pending[:remaining]
    skipped = pending[len(allowed) :]

    fresh: list[SimResult] = []
    if allowed:
        fresh = (backend or _backend(options)).simulate(allowed)
    for result in fresh:
        cache.put(cache_keys[result.case_id], dict(result.to_payload()))

    rows: list[CaseReport] = [
        cast(CaseReport, dict(result.to_payload())) for result in fresh
    ] + [cast(CaseReport, dict(payload)) for payload in cached.values()]
    deck_of = {case.case_id: case.deck_name for case in cases}
    corner_of = {case.case_id: case.corner for case in cases}
    order = {case.case_id: index for index, case in enumerate(cases)}
    rows.sort(key=lambda row: order.get(str(row["case"]), len(order)))
    for row in rows:
        case_id = str(row["case"])
        row["deck"] = deck_of.get(case_id, "")
        row["corner"] = dict(corner_of.get(case_id, {}))
        if row.get("status") == "ok":
            row.pop("log_tail", None)

    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        counts[status] = counts.get(status, 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "design_hash": design_hash(netlist_text),
        "sims": len(rows),
        "sims_run": len(fresh),
        "wall_seconds": round(time.perf_counter() - started, 3),
        "cache": cache.stats(),
        "budget": {
            "limit": options.budget,
            "prior_used": options.prior_used,
            "used": len(fresh),
            "returned": len(rows),
            "cached": len(cached),
            "exhausted": bool(skipped),
            "skipped": [case.case_id for case in skipped],
        },
        "status_counts": dict(sorted(counts.items())),
        "results": rows,
    }


def write_report(report: EvalReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")


def summarise(report: EvalReport) -> str:
    """One line per case for stdout; the full data stays in the report."""
    lines = []
    for row in report["results"]:
        measures = row.get("measures") or {}
        preview = ", ".join(f"{key}={value:g}" for key, value in list(measures.items())[:3])
        if len(measures) > 3:
            preview += ", ..."
        lines.append(
            f"{row['status']:<8} {row['case']:<32} {row.get('seconds', 0.0):6.2f}s  {preview}"
        )
    return "\n".join(lines)
