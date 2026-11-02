#!/usr/bin/env python3
"""Reconcile local verifier runs with each task's T0 ``referenceResult``.

Reads only the JSON that the sweep wrote (``results/verifier/<id>.json``) and
the T0 task records, then writes a machine-readable reconciliation:

    results/reconcile.json

For every reference metric (gate) it records the specification, the T0 value,
the value printed by this run's official verifier, the relative error, the
pass/fail consistency, and the raw message the value came from.  Values are
extracted from the verifier's own CTRF message; when several numbers appear
(corner labels, limits, secondary checks) the candidate closest to the T0
value is selected, and the selection method is recorded so a reader can audit
it.  A gate whose value cannot be extracted gets ``null`` rather than a guess.

Usage:
    python3 scripts/reconcile_verifiers.py
    python3 scripts/reconcile_verifiers.py --out results/reconcile.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "data" / "tasks"
DEFAULT_RUNS = ROOT / "results" / "verifier"
DEFAULT_OUT = ROOT / "results" / "reconcile.json"
#: Same design, same pinned verifier: values should agree to printed precision.
VALUE_TOLERANCE = 0.01

#: Units a verifier message may print after a number.  Longest-first so
#: ``V/V`` is not read as ``V`` and ``dB`` is not a prefix.
UNIT = (
    r"(?:[pnumkMG\u00b5\u03bc]?(?:"
    r"V/V|V/us|A/V|ppm/C|Vrms|Arms|Vpp|dB|deg|LSB|bit|ohm|Ohm|Hz|"
    r"V|A|W|s|F|H|C|J|K"
    r")(?:rms|pp|RMS|PP)?|%)"
)
NUMBER = re.compile(
    r"(?:(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=|:)\s*)?"
    r"(?P<value>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    rf"(?P<unit>{UNIT})?"
)
PREFERRED_NAMES = re.compile(r"worst|max|min|range|yield|gain|power|noise|sndr|sfdr", re.I)
SI_PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "M": 1e6, "G": 1e9}


@dataclass(frozen=True)
class Candidate:
    value: float
    name: str
    text: str
    unit: str = ""

def candidates(message: str) -> list[Candidate]:
    """Every number in a verifier message, with the label just before it."""
    found: list[Candidate] = []
    for match in NUMBER.finditer(message):
        try:
            value = float(match.group("value"))
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        found.append(
            Candidate(
                value=value,
                name=(match.group("name") or "").casefold(),
                text=match.group(0),
                unit=(match.group("unit") or ""),
            )
        )
    return found


def unit_parts(unit: str) -> tuple[float, str] | None:
    """``mW`` -> ``(1e-3, "W")``; unknown or dimensionless -> ``None``."""
    text = (unit or "").replace("\u00b5", "u").replace("\u03bc", "u")
    text = re.sub(r"(?i)(rms|pp)$", "", text)
    if not text or text == "%":
        return None
    if text in {"dB", "deg", "LSB", "bit"}:
        return 1.0, text
    base = text
    scale = 1.0
    if len(text) > 1 and text[0] in SI_PREFIX:
        scale = SI_PREFIX[text[0]]
        base = text[1:]
    return scale, base


def in_reference_unit(candidate: Candidate, reference_unit: str) -> float:
    """Convert a printed value to the unit used by the T0 specification."""
    current = unit_parts(candidate.unit)
    target = unit_parts(reference_unit)
    if current is None or target is None or current[1] != target[1]:
        return candidate.value
    return candidate.value * current[0] / target[0]


def specification_unit(specification: str | None) -> str:
    if not specification:
        return ""
    matches = re.findall(UNIT, specification)
    return matches[-1] if matches else ""


def select_value(
    message: str, reference: float | None, reference_unit: str = ""
) -> tuple[float | None, str]:
    """Choose the message number that is the gate's measured value.

    With a T0 value available, the nearest candidate (after unit normalisation)
    is the only defensible anchor: the same verifier must print the same
    measurement, and a second value in the message is a limit or a corner
    label.  Without a T0 value, prefer an explicitly named
    ``worst``/``max``/``min`` number and otherwise fall back to the first
    number in the message.
    """
    found = candidates(message)
    if not found:
        return None, "no-number"
    named = [item for item in found if item.name]
    if reference is not None:
        # Monte Carlo yields are printed as percentages while the reference
        # stores a fraction; use the explicitly named yield candidate.
        if reference <= 1.0:
            yields = [item for item in found if "yield" in item.name and item.unit == "%"]
            if yields:
                best_yield = min(yields, key=lambda item: abs(item.value / 100.0 - reference))
                return best_yield.value / 100.0, f"yield-percent:{best_yield.text}"

        def distance(item: Candidate) -> float:
            return abs(in_reference_unit(item, reference_unit) - reference)

        best = min(found, key=lambda item: (distance(item), 0 if item.name else 1))
        value = in_reference_unit(best, reference_unit)
        source = f"closest:{best.text}" if best.name else f"closest-number:{best.text}"
        return value, source
    preferred = [item for item in named if PREFERRED_NAMES.search(item.name)]
    chosen = preferred[0] if preferred else (named[0] if named else found[0])
    return chosen.value, f"named:{chosen.text}" if chosen.name else f"first-number:{chosen.text}"


def relative_error(reference: float, current: float) -> float:
    if reference == 0.0:
        return abs(current - reference)
    return abs(current - reference) / abs(reference)


def _squash(name: str) -> str:
    """Normalised name for fuzzy fallback matching."""
    words = re.split(r"[^a-z0-9]+", name.casefold())
    stop = {"pvt", "worst", "minimum", "maximum", "min", "max", "nominal", "the", "and"}
    return "".join(word for word in words if word and word not in stop)


def find_gate(
    metric: Mapping[str, Any], gates: Mapping[str, Mapping[str, Any]]
) -> tuple[str, str]:
    """Map a reference metric to the gate that produced it.

    ``source_check`` is the authoritative link (the reference record was built
    from the verifier's own check names); id and a normalised fallback only
    cover records that predate that field.
    """
    source = str(metric.get("source_check") or "")
    if source in gates:
        return source, "source_check"
    metric_id = str(metric.get("id") or "")
    if metric_id in gates:
        return metric_id, "id"
    squash = _squash(metric_id)
    normalised = {name: _squash(name) for name in gates}
    for name, key in normalised.items():
        if key and key == squash:
            return name, "normalised-name"
    close = difflib.get_close_matches(squash, list(normalised.values()), n=1, cutoff=0.7)
    if close:
        target = close[0]
        for name, key in normalised.items():
            if key == target:
                return name, "fuzzy-name"
    return "", "unmatched"


def compare_gate(
    gate: Mapping[str, Any] | None, reference: Mapping[str, Any] | None
) -> dict[str, Any]:
    gate = gate or {}
    message = str(gate.get("message", ""))
    current_status = str(gate.get("status", "")) if gate else "missing"
    reference_value = None
    reference_status = None
    specification = None
    if reference is not None:
        raw_value = reference.get("value")
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            reference_value = float(raw_value)
        reference_status = str(reference.get("status", "")) or None
        specification = str(reference.get("specification", "")) or None

    metric_id = str((reference or {}).get("id", ""))
    boolean_metric = bool(
        reference_value == 1.0
        and (
            re.search(r"=\s*1\s*$", specification or "")
            or metric_id.endswith("_pass")
        )
    )
    derived_decimals: int | None = None
    if boolean_metric:
        current_value = 1.0 if current_status == "passed" else 0.0
        source = "gate-status"
    elif (
        "VDD" in (specification or "")
        and (swing := re.search(r"tightest Vpp=([\d.eE+-]+)V at [^;]*?/([\d.]+)V", message))
    ):
        current_value = float(swing.group(1)) / float(swing.group(2))
        source = f"derived:Vpp/VDD:{swing.group(0)}"
    elif "1.3 VDD" in (specification or "") and (
        loaded := re.search(r"VOUT=([\d.eE+-]+)V at [^;]*?/([\d.]+)V", message)
    ):
        current_value = float(loaded.group(1)) / (1.3 * float(loaded.group(2)))
        source = f"derived:VOUT/(1.3*VDD):{loaded.group(0)}"
    elif "duty" in metric_id.casefold() and (duty := re.search(r"duty=([^;]+)%", message)):
        readings = [float(item) for item in duty.group(1).split("/") if item.strip()]
        current_value = max(abs(value - 50.0) for value in readings) if readings else None
        source = f"derived:max|duty-50|:{duty.group(1)}"
        derived_decimals = 2
    else:
        current_value, source = select_value(
            message, reference_value, specification_unit(specification)
        )
        if (
            reference_value is not None
            and current_value is not None
            and "yield" in metric_id.casefold()
            and current_value > 1.5
            and reference_value <= 1.0
        ):
            current_value /= 100.0
            source += ":percent-to-fraction"
    error = (
        relative_error(reference_value, current_value)
        if reference_value is not None and current_value is not None
        else None
    )
    rounding_match = bool(
        derived_decimals is not None
        and reference_value is not None
        and current_value is not None
        and round(reference_value, derived_decimals) == round(current_value, derived_decimals)
    )
    value_match = bool(
        error is not None and (error <= VALUE_TOLERANCE or rounding_match)
    )
    value_match_basis = (
        "printed-precision"
        if rounding_match and error is not None and error > VALUE_TOLERANCE
        else ("tolerance" if value_match else "none")
    )
    expected_pass = None if reference_status is None else reference_status == "pass"
    status_match = (
        None if expected_pass is None else expected_pass == (current_status == "passed")
    )
    return {
        "gate_id": str(gate.get("name", "")),
        "id": str(reference.get("id", "")) if reference is not None else "",
        "specification": specification,
        "reference_value": reference_value,
        "current_value": current_value,
        "current_value_source": source,
        "relative_error": None if error is None else round(error, 9),
        "value_match": value_match,
        "value_match_basis": value_match_basis,
        "reference_status": reference_status,
        "current_status": current_status,
        "status_match": status_match,
        "message": message,
    }


def load_reference(task_id: str) -> dict[str, Any]:
    payload = json.loads((TASKS / f"{task_id}.json").read_text())
    return cast(dict[str, Any], payload["referenceResult"])


def reconcile_one(run: Mapping[str, Any], reference: Mapping[str, Any]) -> dict[str, Any]:
    task_id = str(run.get("task_id", reference.get("task_id", "")))
    artifacts = run.get("artifacts")
    run_artifacts = cast(Mapping[str, Any], artifacts) if isinstance(artifacts, dict) else {}
    gates = {
        str(gate.get("name", "")): cast(Mapping[str, Any], gate)
        for gate in cast(list[Any], run_artifacts.get("gates", []))
        if isinstance(gate, dict)
    }
    ref_metrics = [
        cast(Mapping[str, Any], metric)
        for metric in cast(list[Any], reference.get("metrics", []))
        if isinstance(metric, dict)
    ]
    rows: list[dict[str, Any]] = []
    used_gates: set[str] = set()
    for metric in ref_metrics:
        gate_name, mapping = find_gate(metric, gates)
        row = compare_gate(gates.get(gate_name), metric)
        row["gate_id"] = gate_name
        row["mapping"] = mapping
        rows.append(row)
        if gate_name:
            used_gates.add(gate_name)
    extra = [compare_gate(gate, None) for name, gate in gates.items() if name not in used_gates]

    comparable = [row for row in rows if row["reference_value"] is not None]
    value_matches = [
        row for row in comparable
        if row["value_match"] and row["value_match_basis"] == "tolerance"
    ]
    rounding_matches = [
        row for row in comparable
        if row["value_match"] and row["value_match_basis"] == "printed-precision"
    ]
    status_matches = [row for row in rows if row["status_match"] is True]
    mismatches = [
        row
        for row in rows
        if row["status_match"] is False or (row["relative_error"] is not None and not row["value_match"])
    ]
    return {
        "task_id": task_id,
        "reference": {
            "status": reference.get("status"),
            "reward": reference.get("reward"),
            "tests_passed": reference.get("tests_passed"),
            "tests_total": reference.get("tests_total"),
            "ngspice_runs": cast(
                Mapping[str, Any], reference.get("runtime", {})
            ).get("ngspice_runs"),
        },
        "run": {
            "status": run.get("status"),
            "reward": run_artifacts.get("reward"),
            "tests_passed": run_artifacts.get("tests_passed"),
            "tests_total": run_artifacts.get("tests_total"),
            "ngspice_runs": run_artifacts.get("ngspice_runs"),
            "wall_s": run.get("wall_s"),
        },
        "counts": {
            "reference_metrics": len(ref_metrics),
            "run_gates": len(gates),
            "matched_gates": len(used_gates),
            "comparable_values": len(comparable),
            "values_within_tolerance": len(value_matches),
            "printed_precision_matches": len(rounding_matches),
            "values_matched": len(value_matches) + len(rounding_matches),
            "status_matches": len(status_matches),
            "mismatches": len(mismatches),
            "extra_gates": len(extra),
        },
        "gates": rows,
        "extra_gates": extra,
    }


def summarize(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total_metrics = sum(cast(int, item["counts"]["reference_metrics"]) for item in tasks)
    total_gates = sum(cast(int, item["counts"]["run_gates"]) for item in tasks)
    comparable = sum(cast(int, item["counts"]["comparable_values"]) for item in tasks)
    value_matches = sum(cast(int, item["counts"]["values_within_tolerance"]) for item in tasks)
    rounding_matches = sum(
        cast(int, item["counts"].get("printed_precision_matches", 0)) for item in tasks
    )
    status_matches = sum(cast(int, item["counts"]["status_matches"]) for item in tasks)
    reference_sims = sum(
        value
        for item in tasks
        if isinstance(value := item["reference"].get("ngspice_runs"), int)
    )
    local_sims = sum(
        value
        for item in tasks
        if isinstance(value := item["run"].get("ngspice_runs"), int)
    )
    sim_differences = [
        {
            "task_id": item["task_id"],
            "reference": item["reference"].get("ngspice_runs"),
            "local": item["run"].get("ngspice_runs"),
        }
        for item in tasks
        if item["reference"].get("ngspice_runs") != item["run"].get("ngspice_runs")
    ]
    mismatches = [
        {"task_id": item["task_id"], **row}
        for item in tasks
        for row in cast(list[dict[str, Any]], item["gates"])
        if row["status_match"] is False
        or (row["relative_error"] is not None and not row["value_match"])
    ]
    run_mismatches = [
        {
            "task_id": item["task_id"],
            "reference": item["reference"],
            "run": item["run"],
        }
        for item in tasks
        if item["reference"]["status"] != item["run"]["status"]
        or item["reference"]["reward"] != item["run"]["reward"]
        or item["reference"]["tests_passed"] != item["run"]["tests_passed"]
        or item["reference"]["tests_total"] != item["run"]["tests_total"]
    ]
    return {
        "schema": 1,
        "tolerance": VALUE_TOLERANCE,
        "tasks": len(tasks),
        "reference_metrics": total_metrics,
        "run_gates": total_gates,
        "comparable_values": comparable,
        "values_within_tolerance": value_matches,
        "values_matched_by_printed_precision": rounding_matches,
        "values_matched": value_matches + rounding_matches,
        "status_matches": status_matches,
        "reference_ngspice_runs": reference_sims,
        "local_ngspice_runs": local_sims,
        "sim_difference_count": len(sim_differences),
        "sim_differences": sim_differences,
        "mismatch_count": len(mismatches),
        "run_level_mismatch_count": len(run_mismatches),
        "mismatches": mismatches,
        "run_level_mismatches": run_mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    paths = sorted(
        path for path in args.runs.glob("sky130-*.json") if path.stem != "summary"
    )
    if not paths:
        print(f"no run records under {args.runs}", file=sys.stderr)
        return 1
    tasks: list[dict[str, Any]] = []
    for path in paths:
        run = cast(dict[str, Any], json.loads(path.read_text()))
        task_id = str(run.get("task_id") or path.stem)
        tasks.append(reconcile_one(run, load_reference(task_id)))

    report = {
        "source": {
            "runs": str(args.runs.relative_to(ROOT)) if args.runs.is_relative_to(ROOT) else str(args.runs),
            "task_records": str(TASKS.relative_to(ROOT)),
        },
        "summary": summarize(tasks),
        "tasks": tasks,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(args.out)
    summary = report["summary"]
    print(
        f"wrote {args.out}: {summary['tasks']} tasks, "
        f"{summary['reference_metrics']} reference metrics, "
        f"{summary['values_matched']}/{summary['comparable_values']} values matched "
        f"({summary['values_within_tolerance']} within {summary['tolerance']:.0%}, "
        f"{summary['values_matched_by_printed_precision']} at printed precision), "
        f"{summary['mismatch_count']} mismatch(es), "
        f"{summary['run_level_mismatch_count']} run-level mismatch(es)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
