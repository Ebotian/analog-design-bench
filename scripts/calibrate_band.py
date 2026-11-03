#!/usr/bin/env python3
"""Measure a per-gate safety band against the oracle.

The reference solution calibrates the proxy at one point.  That is not enough
(the proxy's error is design-dependent), so a few perturbed designs are scored
by both: our evaluation gives our worst value, the official verifier's CTRF
messages give theirs.  The largest disagreement per gate becomes a band, and
the objective then requires ``margin >= band`` instead of ``margin >= 0``.

Usage: python3 scripts/calibrate_band.py --task <id> [--designs 3]
Writes/updates results/calibration/<task>.json with a "bands" section.
"""

from __future__ import annotations

import argparse
import glob
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adb_solve import bench, calibrate, corners as corner_asset, evaluate, gates, params, verifier  # noqa: E402


def our_values(task: str, text: str, decks, corners, jobs: int) -> dict[str, float]:
    report = evaluate.evaluate(
        text, decks, corners,
        evaluate.EvalOptions(jobs=jobs, cache_root=ROOT / ".work" / "calib-cache", work_root=ROOT / ".work" / "sim"),
    )
    names = {n for row in report["results"] for n in (row.get("measures") or {})}
    payload = json.loads((ROOT / "data" / "tasks" / f"{task}.json").read_text())
    gate_set = gates.from_task(payload, names)
    return {gate: value for gate, value in calibrate.worst_values(gate_set, report["results"]).items() if value is not None}


def official_values(logs_glob: str) -> dict[str, tuple[float, str]]:
    path = sorted(glob.glob(logs_glob))[-1]
    ctrf = json.loads(Path(path).read_text())
    out = {}
    for test in ctrf["results"]["tests"]:
        parsed = calibrate.parse_official(str(test.get("message") or ""))
        if parsed is not None:
            out[str(test["name"])] = parsed
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--designs", type=int, default=3)
    parser.add_argument("--perturb", type=float, default=0.15)
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--from-run", type=Path, default=None,
                        help="also learn from a gym run's candidates (the distribution that matters)")
    args = parser.parse_args()

    task = args.task
    payload = json.loads((ROOT / "data" / "tasks" / f"{task}.json").read_text())
    reference = next(item["content"] for item in payload["files"]["solution"] if item["path"].endswith(".spi"))
    decks = [bench.load(p) for p in sorted((ROOT / "data" / "t1" / "tasks" / task / "tests" / "benches").glob("*.spi"))]
    asset = corner_asset.load(ROOT / "configs" / "corners.toml")
    corners = [dict(c) for c in corner_asset.expand(corner_asset.for_task(asset, task))]
    packages = verifier.discover(ROOT / "data" / "t1" / "tasks")
    design = params.load_text(reference, path=task)

    published = {str(m["id"]): gates.base_value(m) for m in payload["referenceResult"]["metrics"]}

    observations: list[tuple[str, float]] = []
    # Reference point: our value vs the published one.
    ours = our_values(task, reference, decks, corners, args.jobs)
    for gate, value in ours.items():
        if published.get(gate) is not None:
            observations.append((gate, abs(value - float(published[gate]))))

    # Perturbed designs: our value vs the verifier's own printed worst.
    for index in range(args.designs):
        rng = random.Random(1000 + index)
        values = {p.id: p.value * (1.0 + rng.uniform(-args.perturb, args.perturb)) for p in design.writable()}
        text = design.apply(values)
        path = ROOT / ".work" / "calib-designs" / f"{task}-{index}.spi"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        run = verifier.run(packages[task], path, workdir=ROOT / ".work" / "calib-verify", timeout=900)
        official = official_values(str(ROOT / ".work" / "calib-verify" / "*" / "logs" / "verifier" / "new-ctrf.json"))
        mine = our_values(task, text, decks, corners, args.jobs)
        for gate, (value, unit) in official.items():
            if mine.get(gate) is None:
                continue
            observations.append((gate, abs(mine[gate] - gates.to_base(value, unit))))
        print(task, "design", index, "official", run.artifacts.tests_passed if run.artifacts else None, "/", run.artifacts.tests_total if run.artifacts else None)

    if args.from_run is not None:
        for record in sorted(args.from_run.glob("*-s*.json")):
            candidate = record.with_suffix(".spi")
            if not candidate.exists():
                continue
            text = candidate.read_text()
            run = verifier.run(packages[task], candidate, workdir=ROOT / ".work" / "calib-verify", timeout=900)
            official = official_values(str(ROOT / ".work" / "calib-verify" / "*" / "logs" / "verifier" / "new-ctrf.json"))
            mine = our_values(task, text, decks, corners, args.jobs)
            for gate, (value, unit) in official.items():
                if mine.get(gate) is not None:
                    observations.append((gate, abs(mine[gate] - gates.to_base(value, unit))))
            print(task, "run", record.stem[-2:], "official", run.artifacts.tests_passed if run.artifacts else None)

    thresholds = {gate.id: gate.threshold for gate in gates.from_task(payload, set(ours)).gates}
    bands = calibrate.band_from(observations, thresholds)
    path = ROOT / "results" / "calibration" / f"{task}.json"
    existing = json.loads(path.read_text()) if path.exists() else {"schema": 1, "task": task}
    existing["bands"] = {gate: round(band, 6) for gate, band in sorted(bands.items())}
    existing["band_observations"] = len(observations)
    path.write_text(json.dumps(existing, indent=2) + "\n")
    print(task, "bands", existing["bands"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
