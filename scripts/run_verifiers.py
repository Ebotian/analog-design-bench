#!/usr/bin/env python3
"""Run the official verifier for every reference solution.

For each task: build its verifier image from ``data/t1/tasks/<id>/tests``, run
``test.sh`` against the T0 reference netlist, and store the verifier's own JSON
(reward, gates, measurements, ngspice runs, wall clock) under
``results/verifier/<task_id>.json``.  Then write ``results/verifier/summary.json``.

The sweep is resumable: a task with a stored record is skipped unless
``--rebuild``/``--force`` is given.  Failures are recorded and the sweep
continues; the exit code only reports whether everything passed.

Usage:
    python3 scripts/run_verifiers.py                     # all 50, 6 parallel
    python3 scripts/run_verifiers.py --tasks <id> --force # one task, rerun
    python3 scripts/run_verifiers.py --resume             # skip stored records

Writes:
    results/verifier/<task_id>.json
    results/verifier/summary.json
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adb_solve import verifier  # noqa: E402

TASKS = ROOT / "data" / "tasks"
PACKAGES = ROOT / "data" / "t1" / "tasks"
DEFAULT_OUT = ROOT / "results" / "verifier"
DEFAULT_WORK = ROOT / ".work" / "verifier"


class CpuSampler(threading.Thread):
    """Host CPU utilisation while the sweep runs (12 threads on this box)."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: list[float] = []
        self._stop = threading.Event()

    @staticmethod
    def _stat() -> tuple[int, int]:
        parts = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        return parts[3] + parts[4], sum(parts)

    def run(self) -> None:
        idle, total = self._stat()
        while not self._stop.wait(0.5):
            idle2, total2 = self._stat()
            busy = 100.0 * (1.0 - (idle2 - idle) / max(total2 - total, 1))
            self.samples.append(busy)
            idle, total = idle2, total2

    def stop(self) -> dict[str, float | None]:
        self._stop.set()
        self.join(timeout=5)
        if not self.samples:
            return {"mean": None, "peak": None}
        return {
            "mean": round(sum(self.samples) / len(self.samples), 1),
            "peak": round(max(self.samples), 1),
        }


def reference_solution(task_id: str, work: Path) -> Path:
    """Materialise the T0 reference netlist for one task."""
    payload = json.loads((TASKS / f"{task_id}.json").read_text())
    solution = next(
        item["content"] for item in payload["files"]["solution"] if item["path"].endswith(".spi")
    )
    path = work / task_id / "reference.spi"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(solution)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", nargs="*", default=None, help="task ids (default: all)")
    parser.add_argument("--jobs", type=int, default=6, help="tasks run in parallel")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--timeout", type=float, default=1800.0, help="seconds per task")
    parser.add_argument("--resume", action="store_true", help="skip tasks with a stored record")
    parser.add_argument("--force", action="store_true", help="rerun even if a record exists")
    parser.add_argument("--rebuild", action="store_true", help="force docker rebuild")
    args = parser.parse_args()

    packages = verifier.discover(PACKAGES)
    selected = sorted(args.tasks) if args.tasks else sorted(packages)
    unknown = sorted(set(selected) - set(packages))
    if unknown:
        parser.error(f"unknown task id(s): {unknown}")
    args.out.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sampler = CpuSampler()
    sampler.start()
    started = time.perf_counter()

    def one(task_id: str) -> verifier.VerifierRun:
        path = verifier.result_path(args.out, task_id)
        if not args.force and verifier.should_skip(path, args.resume):
            stored = verifier.load_result(path)
            if stored is not None:
                print(f"skip    {task_id} (stored, {stored.status})", flush=True)
                return stored
        package = packages[task_id]
        solution = reference_solution(task_id, args.work)
        try:
            image = verifier.build(package, rebuild=args.rebuild)
            result = verifier.run(
                package, solution, image=image, workdir=args.work, timeout=args.timeout
            )
        except verifier.VerifierError as exc:
            result = verifier.VerifierRun(
                task_id=task_id, status="build-error", exit_code=None, wall_s=0.0, reason=str(exc)
            )
        verifier.write_result(path, result)
        detail = f"{result.artifacts.ngspice_runs} sims" if result.artifacts else result.reason
        print(f"{result.status:<9} {task_id:<52} {result.wall_s:7.1f}s  {detail}", flush=True)
        return result

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(one, selected))

    wall = time.perf_counter() - started
    cpu = sampler.stop()
    leftovers = verifier.leftover_containers()
    threads_after = verifier.ngspice_threads()
    summary = {
        "schema": 1,
        "started_utc": started_at,
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "harness_wall_s": round(wall, 1),
        "jobs": args.jobs,
        "cpu_busy_percent": cpu,
        "leftover_containers": leftovers,
        "ngspice_threads_after": threads_after,
        **verifier.summarize(results),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    print(
        f"\n{summary['status_counts']} tasks={summary['tasks']} "
        f"sims={summary['ngspice_runs']} verifier_wall={summary['verifier_wall_s']}s "
        f"harness_wall={round(wall, 1)}s cpu={cpu['mean']}%/{cpu['peak']}%"
    )
    if leftovers or threads_after:
        print(
            f"ERROR: sandbox not clean: containers={leftovers} ngspice_threads={threads_after}",
            file=sys.stderr,
        )
    for row in summary["failing"]:
        print(f"  not passed: {row['task_id']} [{row['status']}] {row['reason'][:120]}", file=sys.stderr)
    if leftovers or threads_after:
        return 2
    return 0 if summary["status_counts"].get("passed") == summary["tasks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
