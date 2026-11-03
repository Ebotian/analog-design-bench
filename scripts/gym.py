#!/usr/bin/env python3
"""Pilot sizing gym: perturb a reference solution, ask the solver to recover it.

For each task × seed:
  1. screen (cached in results/screen/) to get dimensions,
  2. perturb every writable parameter by ±``--perturb`` (log-uniform, seeded),
  3. run ``optimize.solve`` with a hard simulation budget,
  4. if the full-PVT validation passes, score the candidate with the official
     ``test.sh`` — the verifier is the judge, the solver never sees it.

Writes one JSON per run plus ``results/gym/summary.json`` (recovery rate,
sims-to-feasible, per-task breakdown).  Small by design: this is a pilot to
decide whether the solver generalises, not the P5 gym.

Usage:
    python3 scripts/gym.py --tasks sky130-transistor-divide-by-2 ... \
        --seeds 1 2 --budget 800 --jobs 12
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adb_solve import bench, corners as corner_asset, evaluate, optimize, params, screen, verifier  # noqa: E402


def load_task(task: str) -> tuple[dict[str, Any], str]:
    payload: dict[str, Any] = json.loads((ROOT / "data" / "tasks" / f"{task}.json").read_text())
    solution = next(item["content"] for item in payload["files"]["solution"] if item["path"].endswith(".spi"))
    return payload, str(solution)


def decks_of(task: str) -> list[bench.Deck]:
    folder = ROOT / "data" / "t1" / "tasks" / task / "tests" / "benches"
    return [bench.load(path) for path in sorted(folder.glob("*.spi"))]


def dimensions(task: str, text: str, decks: list[bench.Deck], corners: list[dict], jobs: int, budget: int) -> list[str]:
    cached = ROOT / "results" / "screen" / f"{task}.json"
    if cached.exists():
        return [str(name) for name in json.loads(cached.read_text())["dims"]]
    options: screen.ScreenOptions = {"budget": budget, "max_dims": 15}
    report = screen.screen(
        text,
        decks,
        corners,
        options,
        eval_options=evaluate.EvalOptions(jobs=jobs, cache_root=ROOT / ".adb-cache", work_root=ROOT / ".work/sim"),
    )
    cached.parent.mkdir(parents=True, exist_ok=True)
    screen.write_report(report, cached)
    return list(report["dims"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", nargs="+", required=True)
    parser.add_argument("--seeds", nargs="+", type=int, default=[1, 2])
    parser.add_argument("--perturb", type=float, default=0.2)
    parser.add_argument("--budget", type=int, default=1500)
    parser.add_argument("--corners-count", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--sigma", type=float, default=0.35)
    parser.add_argument("--lambda-reg", type=float, default=0.05)
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "gym")
    parser.add_argument("--no-verifier", action="store_true", help="skip the official test.sh")
    args = parser.parse_args()

    packages = verifier.discover(ROOT / "data" / "t1" / "tasks")
    asset = corner_asset.load(ROOT / "configs" / "corners.toml")
    args.out.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []

    for task in args.tasks:
        payload, text = load_task(task)
        decks = decks_of(task)
        corner_list = [dict(corner) for corner in corner_asset.expand(corner_asset.for_task(asset, task))]
        design = params.load_text(text, path=task)
        dims = dimensions(task, text, decks, corner_list, args.jobs, args.budget)
        for seed in args.seeds:
            rng = random.Random(seed)
            values = {p.id: p.value * math.exp(rng.uniform(-args.perturb, args.perturb)) for p in design.writable()}
            perturbed = design.apply(values)
            case = args.out / f"{task}-s{seed}.json"
            # Each run pays for its own simulations: a shared cache would make
            # repeated budgets free and "sims-to-recover" incomparable.
            cache_root = ROOT / ".work" / "gym-cache" / f"{task}-s{seed}-b{args.budget}"
            started = time.perf_counter()
            report = optimize.solve(
                perturbed,
                decks,
                corner_list,
                payload,
                {
                    "dims": dims,
                    "budget": args.budget,
                    "corners_count": args.corners_count,
                    "rounds": args.rounds,
                    "seed": seed,
                    "algorithm": "cma",
                    "sigma": args.sigma,
                    "lambda_reg": args.lambda_reg,
                },
                eval_options=evaluate.EvalOptions(
                    jobs=args.jobs, cache_root=cache_root, work_root=ROOT / ".work/sim"
                ),
                validation_corners=corner_list,
            )
            best = design.apply(
                {k: v for k, v in report["best"]["values"].items() if abs(v - design.by_id()[k].value) > 0}
            )
            candidate = args.out / f"{task}-s{seed}.spi"
            candidate.write_text(best)
            official: dict[str, Any] = {"skipped": True}
            if not args.no_verifier and bool(report["validation"].get("feasible")):
                run = verifier.run(packages[task], candidate, workdir=ROOT / ".work" / "gym-verify", timeout=900)
                artifacts = run.artifacts
                official = {
                    "skipped": False,
                    "status": run.status,
                    "passed": artifacts.tests_passed if artifacts else 0,
                    "total": artifacts.tests_total if artifacts else 0,
                    "reward": artifacts.reward if artifacts else 0.0,
                }
            row = {
                "task": task,
                "seed": seed,
                "params": len(design.writable()),
                "dims": len(dims),
                "sims": report["sims"],
                "sims_to_feasible": report["sims_to_feasible"],
                "validation_feasible": bool(report["validation"].get("feasible")),
                "polish_runs": report["polish"].get("runs", 0) if report.get("polish") else 0,
                "official": official,
                "wall_s": round(time.perf_counter() - started, 1),
            }
            case.write_text(json.dumps({**row, "report": report}, indent=2) + "\n")
            runs.append(row)
            print(json.dumps(row, ensure_ascii=False))

    recovered = [row for row in runs if row["official"].get("passed") == row["official"].get("total") and not row["official"].get("skipped")]
    search_ok = [row for row in runs if row["validation_feasible"]]
    sims = [row["sims_to_feasible"] for row in recovered if row["sims_to_feasible"]]
    summary = {
        "schema": 1,
        "tasks": args.tasks,
        "seeds": args.seeds,
        "perturb": args.perturb,
        "budget": args.budget,
        "runs": len(runs),
        "search_feasible": len(search_ok),
        "recovered": len(recovered),
        "verified": sum(1 for row in runs if not row["official"].get("skipped")),
        "median_sims_to_feasible": statistics.median(sims) if sims else None,
        "max_sims_to_feasible": max(sims) if sims else None,
        "by_task": {
            task: {
                "runs": sum(1 for row in runs if row["task"] == task),
                "recovered": sum(1 for row in recovered if row["task"] == task),
            }
            for task in args.tasks
        },
        "results": runs,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
