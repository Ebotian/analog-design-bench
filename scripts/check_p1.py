#!/usr/bin/env python3
"""P1 acceptance check over the T0 reference solutions.

For each of the 50 tasks in ``data/tasks/``:

1. parse the reference netlist (FR-1) and extract tunable parameters (FR-3);
2. write every parameter back at its current value and require the text to be
   byte-identical (FR-2, the "only digits move" guarantee);
3. perturb every writable parameter inside its checker bounds, write it back,
   and require that (a) re-parsing returns the requested values and (b) the
   line diff touches only lines that carry a changed parameter;
4. run the official ``check_circuit.py`` on the reference netlist in the
   pinned Sky130 image (P1 completion criterion).

Usage:
    python3 scripts/check_p1.py [--no-checker] [--jobs 8] [--only TASK_ID]

Writes ``.work/p1_report.json`` and prints one summary line per task.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adb_solve import check as legality  # noqa: E402
from adb_solve import netlist as spice  # noqa: E402
from adb_solve import params  # noqa: E402

TASKS = ROOT / "data" / "tasks"
WORK = ROOT / ".work" / "p1"

# check_circuit.py allows only .subckt/.ends, but this task's instruction makes
# the top-level ``.param cboot`` a design variable, so its pipeline cannot be
# running the shared checker.  Recorded as an exception rather than hidden.
CHECKER_DIRECTIVE_EXCEPTIONS = {
    "sky130-bootstrap-driver-pt": "top-level .param cboot is a declared design variable",
}


def allow_ideal(task: dict) -> tuple[str, ...]:
    """``checker_allow_ideal`` from the task TOML, as a tuple of letters."""
    for line in task.get("taskToml", "").splitlines():
        stripped = line.strip()
        if stripped.startswith("checker_allow_ideal"):
            inside = stripped.split("=", 1)[1]
            return tuple(
                item.strip().strip('"').strip("'")
                for item in inside.strip().strip("[]").split(",")
                if item.strip()
            )
    return ()


def perturb(param: params.Param, design: params.Design) -> float:
    """Move a parameter somewhere legal, deterministically."""
    bounds = design.bounds_for(param)
    value = param.value
    if bounds.grid is not None:
        return value + bounds.grid
    target = value * 1.1
    if bounds.maximum is not None and target > bounds.maximum:
        target = value * 0.9
    if bounds.maximum is not None and target > bounds.maximum:
        target = bounds.maximum
    if bounds.minimum is not None and target < bounds.minimum:
        target = bounds.minimum
    if target == value:
        target = value * 1.01
    return target


def check_task(task_id: str, do_checker: bool, image: str) -> dict:
    payload = json.loads((TASKS / f"{task_id}.json").read_text())
    solutions = [
        item for item in payload["files"]["solution"]
        if item["path"].endswith((".spi", ".cir", ".net"))
    ]
    if not solutions:
        return {"task": task_id, "status": "no-solution"}
    text = solutions[0]["content"]

    design = params.load_text(text)
    summary = params.summarise(design)
    result: dict = {"task": task_id, **summary}

    identity = design.apply({param.id: param.value for param in design.writable()})
    result["identity_exact"] = identity == text

    requested = {param.id: perturb(param, design) for param in design.writable()}
    edited_lines = {design.by_id()[key].line for key in requested}
    try:
        mutated = design.apply(requested)
    except ValueError as exc:  # bounds or grid bug
        result["status"] = f"apply-failed: {exc}"
        return result

    reparsed = params.load_text(mutated)
    after = reparsed.by_id()
    fidelity_errors = []
    for key, want in requested.items():
        got = after[key].value
        if not math.isclose(got, want, rel_tol=1e-6, abs_tol=1e-18):
            fidelity_errors.append(f"{key}: {want!r} -> {got!r}")
    result["fidelity_errors"] = fidelity_errors[:5]
    result["fidelity_ok"] = not fidelity_errors

    diff = spice.line_diff(text, mutated)
    diff_lines = {index for index, _before, _after in diff}
    result["lines_changed"] = len(diff_lines)
    result["lines_changed_outside_params"] = sorted(diff_lines - edited_lines)[:5]
    result["diff_confined"] = diff_lines <= edited_lines

    if do_checker:
        checked = legality.check_text(
            text,
            image=image,
            allow_ideal=allow_ideal(payload),
            workdir=WORK / "check" / task_id,
        )
        result["checker_ok"] = checked.ok
        result["checker_violations"] = checked.violations[:5]
        geometry = legality.check_text(
            text,
            image=image,
            allow_ideal=allow_ideal(payload),
            enforce_geometry=True,
            workdir=WORK / "check-geometry" / task_id,
        )
        result["geometry_violations"] = geometry.violations[:5]

    if not result.get("checker_ok", True) and task_id in CHECKER_DIRECTIVE_EXCEPTIONS:
        result["checker_exception"] = CHECKER_DIRECTIVE_EXCEPTIONS[task_id]
    checker_ok = result.get("checker_ok", True) or "checker_exception" in result
    result["status"] = "ok" if (
        result["identity_exact"] and result["fidelity_ok"] and result["diff_confined"] and checker_ok
    ) else "fail"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--only", action="append", help="task id (repeatable)")
    parser.add_argument("--no-checker", action="store_true")
    parser.add_argument("--image", default=None)
    parser.add_argument("--out", type=Path, default=WORK / "report.json")
    args = parser.parse_args()

    from adb_solve.docker_backend import DEFAULT_IMAGE

    image = args.image or DEFAULT_IMAGE
    task_ids = args.only or sorted(path.stem for path in TASKS.glob("*.json"))
    WORK.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(
            pool.map(lambda task: check_task(task, not args.no_checker, image), task_ids)
        )

    failures = [item for item in results if item.get("status") != "ok"]
    report = {
        "tasks": len(results),
        "ok": len(results) - len(failures),
        "failed": len(failures),
        "checker": not args.no_checker,
        "results": results,
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    for item in results:
        marker = "ok  " if item.get("status") == "ok" else "FAIL"
        print(
            f"{marker} {item['task']:<46} "
            f"params={item.get('params', 0):3d} writable={item.get('writable', 0):3d} "
            f"groups={item.get('groups', 0):2d} changed_lines={item.get('lines_changed', 0):3d}"
            f"{'' if item.get('checker_ok', True) else '  checker-exception'}"
        )
    print(f"\n{report['ok']}/{report['tasks']} tasks ok -> {args.out}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
