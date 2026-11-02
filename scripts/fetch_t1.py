#!/usr/bin/env python3
"""T1 ingest: the judging side of the task packages.

T0 came from the site API, which only exposes the instruction, the benches, the
reference solution and its result.  The public repository additionally ships,
per task, the verifier and everything needed to run it locally:

    tests/{verify.py,test.sh,Dockerfile,benches/,models/}
    task.toml, instruction.md, results/reference.json
    environment/{Dockerfile,starter/}

This script copies exactly that set (never the solution, which T0 already has),
writes a sha256 manifest and a meta file.  The source is read-only.

Usage:
    python3 scripts/fetch_t1.py --repo /path/to/analog-design-bench [--check]

Writes:
    data/t1/tasks/<task_id>/...      verifier corpus, 50 tasks
    manifests/t1.sha256             sha256 of every copied file
    manifests/t1.meta.json          source commit, counts, canary
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T0 = ROOT / "data" / "tasks"
DEST = ROOT / "data" / "t1"
MANIFEST = ROOT / "manifests" / "t1.sha256"
META = ROOT / "manifests" / "t1.meta.json"

# Copied per task, relative to tasks/<id>/.
TASK_PATHS: tuple[str, ...] = (
    "tests",
    "task.toml",
    "instruction.md",
    "results/reference.json",
    "environment/Dockerfile",
    "environment/starter",
)
# Copied once from tasks/.
SHARED_PATHS: tuple[str, ...] = ("benchmark.toml", "defaults.toml")
CANARY_RE = re.compile(r"analog-design-bench-canary GUID ([0-9a-f-]+)", re.I)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def t0_task_ids() -> list[str]:
    return sorted(path.stem for path in T0.glob("*.json"))


def copy_tree(source: Path, destination: Path, copied: list[Path], root: Path) -> None:
    """Copy a file or directory tree, recording every file written."""
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append(destination)
        return
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        copied.append(target)


def ingest(repo: Path) -> dict[str, object]:
    packages = repo / "tasks"
    if not packages.is_dir():
        raise SystemExit(f"{repo} does not look like the task repository (no tasks/)")

    wanted = t0_task_ids()
    available = {path.name for path in packages.iterdir() if path.is_dir()}
    missing = sorted(set(wanted) - available)
    if missing:
        raise SystemExit(f"source is missing {len(missing)} T0 task(s): {missing[:5]}")

    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    copied: list[Path] = []
    for name in SHARED_PATHS:
        shared = packages / name
        if shared.is_file():
            copy_tree(shared, DEST / "tasks" / name, copied, DEST)

    per_task: dict[str, int] = {}
    for task_id in wanted:
        before = len(copied)
        for relative in TASK_PATHS:
            source = packages / task_id / relative
            if source.exists():
                copy_tree(source, DEST / "tasks" / task_id / relative, copied, DEST)
        per_task[task_id] = len(copied) - before
        if per_task[task_id] == 0:
            raise SystemExit(f"task {task_id}: nothing copied; check TASK_PATHS")

    commit = ""
    try:
        import subprocess

        commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        commit = ""

    canary = ""
    readme = repo / "README.md"
    if readme.is_file():
        match = CANARY_RE.search(readme.read_text())
        canary = match.group(1) if match else ""

    return {
        "tasks": 50,
        "files": len(copied),
        "bytes": sum(path.stat().st_size for path in copied),
        "commit": commit,
        "canary": canary,
        "per_task": per_task,
    }


def write_manifest() -> int:
    lines = []
    for path in sorted(DEST.rglob("*")):
        if path.is_file():
            lines.append(f"{sha256(path)}  {path.relative_to(ROOT)}")
    MANIFEST.write_text("\n".join(lines) + "\n")
    return len(lines)


def check_manifest() -> int:
    if not MANIFEST.is_file():
        print(f"{MANIFEST} is missing; run scripts/fetch_t1.py", file=sys.stderr)
        return 1
    expected = {}
    for line in MANIFEST.read_text().splitlines():
        digest, _, relative = line.partition("  ")
        expected[relative] = digest
    actual = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in sorted(DEST.rglob("*"))
        if path.is_file()
    }
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(rel for rel in set(expected) & set(actual) if expected[rel] != actual[rel])
    if missing or extra or changed:
        print(
            f"T1 corpus drift: {len(missing)} missing, {len(extra)} extra, "
            f"{len(changed)} changed",
            file=sys.stderr,
        )
        for label, rows in (("missing", missing), ("extra", extra), ("changed", changed)):
            for row in rows[:5]:
                print(f"  {label}: {row}", file=sys.stderr)
        return 1
    print(f"T1 corpus matches the manifest ({len(actual)} files)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, help="local clone of Arcadia-1/analog-design-bench")
    parser.add_argument("--check", action="store_true", help="verify data/t1 against the manifest")
    args = parser.parse_args()

    if args.check:
        return check_manifest()

    if args.repo is None:
        parser.error("--repo is required (path to a local clone of the task repository)")

    result = ingest(args.repo)
    files = write_manifest()
    META.write_text(
        json.dumps(
            {
                "schema": 1,
                "snapshot_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "source": "https://github.com/Arcadia-1/analog-design-bench",
                "commit": result["commit"],
                "contents": "tests/, task.toml, instruction.md, results/reference.json, environment/",
                "tasks": result["tasks"],
                "files": files,
                "bytes": result["bytes"],
                "canary": result["canary"],
            },
            indent=2,
        )
        + "\n"
    )
    print(
        f"wrote {files} files ({result['bytes']} bytes) for {result['tasks']} tasks "
        f"from {result['commit'][:12]}; manifest -> {MANIFEST.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
