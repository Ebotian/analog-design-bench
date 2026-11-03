#!/usr/bin/env python3
"""Replay the legality guard over the T0 circuits and write results/legality.json.

The corpus is the best available proposal pool: 3,600 netlists written by
models, 1,679 of which the official verifier passed.  Two numbers matter:

* ``rejected_passing`` -- passing circuits the guard refuses; these are false
  positives and must be zero (or explained one by one);
* ``rejected_ratio`` -- share of all proposals refused.  A high value is not a
  bug: it measures how many model-written circuits are illegal as written.

``--checker-sample N`` additionally runs the pinned ``check_circuit.py`` on N
evenly spaced circuits, to confirm the pure precheck agrees with the real
checker.  It needs docker; the default replay does not.

Usage:
    python3 scripts/replay_legality.py [--out results/legality.json] [--checker-sample N] [--jobs 8]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adb_solve import legality, params  # noqa: E402


def load_circuits() -> list[dict]:
    rows: list[dict] = []
    for path in sorted((ROOT / "data" / "runs").glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "legality.json")
    parser.add_argument("--checker-sample", type=int, default=0,
                        help="run check_circuit.py on N evenly spaced circuits (needs docker)")
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()

    started = time.perf_counter()
    circuits = load_circuits()
    parse_errors: list[dict[str, str]] = []
    rejected: list[dict[str, object]] = []
    reasons: Counter[str] = Counter()
    by_task: dict[str, dict[str, int]] = {}
    verdicts: dict[str, bool] = {}

    for index, run in enumerate(circuits):
        meta = run.get("_meta") or {}
        task = str(meta.get("task") or run.get("taskId") or "")
        key = f"{task}/{meta.get('model')}/run-{meta.get('run')}"
        stats = by_task.setdefault(task, {"circuits": 0, "rejected": 0, "rejected_passing": 0})
        stats["circuits"] += 1
        try:
            design = params.load_text(str(run.get("circuit") or ""), path=key)
        except Exception as exc:  # noqa: BLE001 - any parse failure is a finding
            parse_errors.append({"run": key, "error": f"{type(exc).__name__}: {exc}"})
            verdicts[key] = False
            stats["rejected"] += 1
            if run.get("pass"):
                stats["rejected_passing"] += 1
            continue
        verdict = legality.audit(design)
        verdicts[key] = verdict.ok
        if verdict.ok:
            continue
        stats["rejected"] += 1
        if run.get("pass"):
            stats["rejected_passing"] += 1
        for violation in verdict.violations:
            reasons[violation.reason] += 1
        rejected.append(
            {
                "run": key,
                "pass": bool(run.get("pass")),
                "violations": [v.to_json() for v in verdict.violations[:5]],
                "index": index,
            }
        )

    passing = sum(1 for run in circuits if run.get("pass"))
    rejected_passing = sum(1 for row in rejected if row["pass"])
    report: dict[str, object] = {
        "schema": 1,
        "source": "data/runs/*.jsonl",
        "circuits": len(circuits),
        "passing": passing,
        "parse_errors": parse_errors[:20],
        "parse_error_count": len(parse_errors),
        "rejected": len(rejected),
        "rejected_passing": rejected_passing,
        "rejected_ratio": round(len(rejected) / len(circuits), 4) if circuits else 0.0,
        "reasons": dict(reasons.most_common(20)),
        "by_task": {task: stats for task, stats in sorted(by_task.items())},
        "rejected_examples": rejected[:20],
        "checker_sample": {
            "size": 0,
            "agreements": 0,
            "checker_stricter_passing": [],
            "checker_stricter_failing": 0,
            "precheck_stricter": [],
            "skipped_preprocessed": 0,
        },
    }

    if args.checker_sample > 0:
        step = max(1, len(circuits) // args.checker_sample)
        sample = circuits[::step][: args.checker_sample]
        policies = {task: legality.task_policy(task) for task in by_task}
        agreements = 0
        stricter_passing: list[dict[str, object]] = []
        stricter_failing = 0
        precheck_stricter: list[dict[str, object]] = []
        skipped = 0

        def probe(run: dict) -> tuple[bool, bool | None, list[str]]:
            meta = run.get("_meta") or {}
            task = str(meta.get("task") or run.get("taskId") or "")
            key = f"{task}/{meta.get('model')}/run-{meta.get('run')}"
            text = str(run.get("circuit") or "")
            policy = policies.get(task, legality.TaskPolicy())
            pure = verdicts.get(key, False)
            if policy.preprocessed:
                return pure, None, ["task pipeline preprocesses with check_area.py"]
            verdict = legality.check_netlist(
                text,
                allow_ideal=policy.allow_ideal,
                require_subcircuit=policy.require_subcircuit,
                enforce_geometry=policy.enforce_geometry,
            )
            return pure, verdict.ok, [v.reason for v in verdict.violations][:3]

        def label(run: dict) -> str:
            meta = run.get("_meta") or {}
            return f"{meta.get('task')}/{meta.get('model')}/run-{meta.get('run')}"

        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            for run, (pure, checker_ok, detail) in zip(sample, pool.map(probe, sample)):
                if checker_ok is None:
                    skipped += 1
                elif pure == checker_ok:
                    agreements += 1
                elif checker_ok and not pure:
                    precheck_stricter.append({"run": label(run), "pass": bool(run.get("pass")), "detail": detail})
                elif run.get("pass"):
                    # The dangerous kind: a circuit the verifier passed that the
                    # checker refuses.  Anything here must be explained.
                    stricter_passing.append({"run": label(run), "detail": detail})
                else:
                    stricter_failing += 1
        report["checker_sample"] = {
            "size": len(sample),
            "agreements": agreements,
            "checker_stricter_passing": stricter_passing[:20],
            "checker_stricter_passing_count": len(stricter_passing),
            "checker_stricter_failing": stricter_failing,
            "precheck_stricter": precheck_stricter[:20],
            "precheck_stricter_count": len(precheck_stricter),
            "skipped_preprocessed": skipped,
        }

    report["elapsed_s"] = round(time.perf_counter() - started, 2)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        f"circuits={report['circuits']} rejected={report['rejected']} "
        f"({report['rejected_ratio']:.2%}) rejected_passing={rejected_passing} "
        f"parse_errors={report['parse_error_count']} elapsed={report['elapsed_s']}s"
    )
    print(f"wrote {args.out}")
    if rejected_passing:
        print("WARNING: passing circuits were refused; inspect rejected_examples", file=sys.stderr)
        return 1
    sample = report["checker_sample"]
    if isinstance(sample, dict) and sample.get("checker_stricter_passing"):
        print("WARNING: checker refused circuits the verifier passed; inspect checker_sample", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
