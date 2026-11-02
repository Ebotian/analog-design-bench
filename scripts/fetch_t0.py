#!/usr/bin/env python3
"""T0 crawl for Analog Design Bench v2.

Fetches, per task and per model, the run records (run-1..3) from the public
site API.  Each record holds the verdict, duration, steps, token/cost
accounting, the gate results and the final netlist.  The script is
resumable: an existing key in data/index.jsonl is skipped.

Usage:
    python3 fetch_t0.py [--tasks-only] [--workers 6]

Writes:
    data/tasks.json                  task list
    data/leaderboard.json            leaderboard aggregates
    data/figures.json                6h time-course curves
    data/tasks/<task_id>.json        task detail (instruction, benches, ref)
    data/runs/<task_id>.jsonl        one run record per line
    data/index.jsonl                 crawl index (status, bytes, sha256)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

BASE = "https://analog-design-bench.tokenzhang.com"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
UA = "adb-research/0.1 (reproducibility study; contact: local)"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept": "application/json"})


def get(path: str, timeout: int = 60) -> tuple[int, bytes]:
    url = BASE + path
    last = None
    for attempt in range(4):
        try:
            r = SESSION.get(url, timeout=timeout)
            if r.status_code == 200:
                return 200, r.content
            if r.status_code in (404, 410):
                return r.status_code, b""
            last = r.status_code
        except requests.RequestException as exc:  # network hiccup
            last = type(exc).__name__
        time.sleep(0.5 * (2**attempt) + random.random() * 0.3)
    return int(last) if isinstance(last, int) else 0, b""


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")


def load_index() -> dict[str, dict]:
    path = DATA / "index.jsonl"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["key"]] = row
    return out


def append_index(row: dict) -> None:
    with (DATA / "index.jsonl").open("a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks-only", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    (DATA / "tasks").mkdir(parents=True, exist_ok=True)
    (DATA / "runs").mkdir(parents=True, exist_ok=True)

    # 1. task list + aggregates
    code, body = get("/api/v2/tasks")
    if code != 200:
        print("tasks list failed", code, file=sys.stderr)
        return 1
    tasks = json.loads(body)["tasks"]
    write_json(DATA / "tasks.json", tasks)
    for path, name in (
        ("/api/v2/leaderboard", "leaderboard.json"),
        ("/api/v2/figures?edition=paper-skills-1-8", "figures.json"),
    ):
        code, body = get(path)
        if code == 200:
            (DATA / name).write_bytes(body)
    print(f"tasks: {len(tasks)}")

    # 2. task details
    details: dict[str, dict] = {}
    def fetch_task(t: dict) -> tuple[str, int, bytes]:
        code, body = get(f"/api/v2/live/tasks/{t['id']}")
        return t["id"], code, body

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(fetch_task, t) for t in tasks]
        for fut in as_completed(futs):
            tid, code, body = fut.result()
            if code == 200:
                details[tid] = json.loads(body)
                write_json(DATA / "tasks" / f"{tid}.json", details[tid])
            else:
                print("task detail failed", tid, code, file=sys.stderr)
    print(f"task details: {len(details)}")

    if args.tasks_only:
        return 0

    # 3. model slugs per task (task detail is authoritative)
    slugs: dict[str, list[str]] = {}
    for tid, d in details.items():
        ids = [m["id"] for m in ((d.get("results") or {}).get("models") or [])]
        if ids:
            slugs[tid] = ids
    union = sorted({m for ids in slugs.values() for m in ids})
    print(f"model slugs: {len(union)} (per-task lists: {len(slugs)})")

    # 4. run records, resumable
    index = load_index()
    jobs = []
    for tid in sorted(details):
        for model in slugs.get(tid, union):
            for run in (1, 2, 3):
                key = f"{tid}/{model}/run-{run}"
                if key in index and index[key]["status"] == 200:
                    continue
                jobs.append((tid, model, run, key))
    print(f"pending runs: {len(jobs)}")

    def fetch_run(job):
        tid, model, run, key = job
        code, body = get(f"/api/v2/live/tasks/{tid}/runs/{model}/run-{run}")
        return job, code, body

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(fetch_run, j) for j in jobs]
        for fut in as_completed(futs):
            (tid, model, run, key), code, body = fut.result()
            row = {
                "key": key,
                "task": tid,
                "model": model,
                "run": run,
                "status": code,
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest() if body else "",
            }
            if code == 200 and body:
                try:
                    rec = json.loads(body)
                except json.JSONDecodeError:
                    row["status"] = 0
                else:
                    rec["_meta"] = {"task": tid, "model": model, "run": run}
                    with (DATA / "runs" / f"{tid}.jsonl").open("a") as fh:
                        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    append_index(row)
            else:
                append_index(row)
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(jobs)}", flush=True)

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
