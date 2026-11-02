"""Run a task's official verifier in the local sandbox.

The verifier ships as ``tasks/<id>/tests/{Dockerfile,test.sh,verify.py,benches}``
in the public task repository (ingested as T1).  Its Dockerfile is
``FROM <pinned sky130-tools image>`` + a handful of COPYs, so it builds offline
against the image we already have.

Two sandbox details are load-bearing and easy to get wrong:

* the design must be mounted as a **file** at ``/app/circuit.spi``; mounting a
  directory at ``/app`` hides the image's ``/app/analog_arena_tests``;
* the verifier writes ``/logs`` as root, so the host cannot read or delete the
  artifacts until a helper container hands ownership back.

Everything the verifier produces is JSON (``reward.json``, ``summary.json``,
``run-summary.json``, ``reports/analog-signoff/metrics.json``, ``new-ctrf.json``);
:func:`parse_artifacts` turns those into typed values and nothing else is read.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Mapping, Sequence, TypeAlias, cast

DEFAULT_IMAGE = (
    "ghcr.io/arcadia-1/analog-arena-sky130-tools"
    "@sha256:668783dd8cbcd5ebce5264785803ce73a9466e79dbe4c98cdf4566348a33437a"
)
HELPER_IMAGE = "alpine:3.21"
#: ngspice must run single-threaded: one multi-threaded ngspice per task
#: saturates the box (measured: 8 threads/proc, 31 runnable threads, everything
#: ~100x slower).  Only 24 of the 50 verifiers pin this themselves, and they
#: write it to ``/root/.spiceinit``.  The verifiers that run ngspice from a
#: temporary working directory (e.g. the bottom-plate sampler) never see a
#: ``.spiceinit`` mounted at ``/app``, so the sandbox mounts it at both places.
SPICEINIT = "set num_threads=1\n"
#: Host-side wrapper mounted as ``/count/ngspice``; it appends one line per
#: invocation under ``/logs`` and then execs the real pinned binary.  Verifiers
#: disagree about whether they print a run count (and some use an absolute
#: NGSPICE path), so the count is measured instead of inferred.
NGSPICE_COUNTER = ".ngspice-invocations"
NGSPICE_WRAPPER = """#!/bin/sh
printf '%s\\n' "$$" >> /logs/.ngspice-invocations
exec /opt/ngspice/bin/ngspice "$@"
"""
ARTIFACT_DIR = "verifier"
REPORT_DIR = "verifier/reports/analog-signoff"

Status: TypeAlias = Literal["passed", "failed", "error", "timeout", "build-error"]


class VerifierError(RuntimeError):
    """The harness itself failed (docker missing, image absent, bad package)."""


@dataclass(frozen=True)
class TaskPackage:
    """The judging side of one task as stored in ``data/t1``."""

    task_id: str
    tests_dir: Path

    @property
    def dockerfile(self) -> Path:
        return self.tests_dir / "Dockerfile"

    @property
    def test_script(self) -> Path:
        return self.tests_dir / "test.sh"

    def require(self) -> None:
        for path in (self.dockerfile, self.test_script):
            if not path.is_file():
                raise VerifierError(f"{self.task_id}: missing {path}")


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class VerifierArtifacts:
    reward: float
    tests_passed: int
    tests_total: int
    partial: float
    ngspice_runs: int
    wall_clock_s: float
    summed_run_time_s: float
    workers: int
    failed_runs: tuple[str, ...]
    gates: tuple[GateResult, ...]
    measurements: Mapping[str, object]
    metrics_rows: int
    runs_source: str = "run-summary"
    wall_clock_source: str = "verifier"

    @classmethod
    def from_json(cls, raw: Mapping[str, object]) -> "VerifierArtifacts":
        """Rebuild from a stored record (used when resuming a sweep)."""
        gates_raw = raw.get("gates")
        gates = tuple(
            GateResult(
                name=str(cast(Mapping[str, object], gate).get("name", "")),
                status=str(cast(Mapping[str, object], gate).get("status", "")),
                message=str(cast(Mapping[str, object], gate).get("message", "")),
            )
            for gate in (gates_raw if isinstance(gates_raw, list) else [])
            if isinstance(gate, dict)
        )
        measurements = raw.get("measurements")
        failed = raw.get("failed_runs")
        return cls(
            reward=_as_float(raw.get("reward")),
            tests_passed=int(_as_float(raw.get("tests_passed"))),
            tests_total=int(_as_float(raw.get("tests_total"))),
            partial=_as_float(raw.get("partial")),
            ngspice_runs=int(_as_float(raw.get("ngspice_runs"))),
            wall_clock_s=_as_float(raw.get("wall_clock_s")),
            summed_run_time_s=_as_float(raw.get("summed_run_time_s")),
            workers=int(_as_float(raw.get("workers", 1))),
            failed_runs=tuple(str(item) for item in failed) if isinstance(failed, list) else (),
            gates=gates,
            measurements=cast(Mapping[str, object], measurements)
            if isinstance(measurements, dict)
            else {},
            metrics_rows=int(_as_float(raw.get("metrics_rows"))),
            runs_source=str(raw.get("runs_source", "run-summary")),
            wall_clock_source=str(raw.get("wall_clock_source", "verifier")),
        )

    def to_json(self) -> dict[str, object]:
        return {
            "reward": self.reward,
            "tests_passed": self.tests_passed,
            "tests_total": self.tests_total,
            "partial": self.partial,
            "ngspice_runs": self.ngspice_runs,
            "wall_clock_s": round(self.wall_clock_s, 3),
            "summed_run_time_s": round(self.summed_run_time_s, 3),
            "workers": self.workers,
            "failed_runs": list(self.failed_runs),
            "gates": [
                {"name": gate.name, "status": gate.status, "message": gate.message}
                for gate in self.gates
            ],
            "measurements": dict(self.measurements),
            "metrics_rows": self.metrics_rows,
            "runs_source": self.runs_source,
            "wall_clock_source": self.wall_clock_source,
        }


@dataclass
class VerifierRun:
    task_id: str
    status: Status
    exit_code: int | None
    wall_s: float
    reason: str = ""
    artifacts: VerifierArtifacts | None = None
    stdout_tail: str = ""
    image: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def to_json(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "wall_s": round(self.wall_s, 3),
            "reason": self.reason,
            "image": self.image,
            "artifacts": self.artifacts.to_json() if self.artifacts else None,
            "stdout_tail": self.stdout_tail,
        }


# -- package helpers --------------------------------------------------------
def discover(root: Path) -> dict[str, TaskPackage]:
    """Every task package under ``root`` (``data/t1/tasks``)."""
    packages: dict[str, TaskPackage] = {}
    for path in sorted(root.iterdir()):
        if path.is_dir() and (path / "tests" / "Dockerfile").is_file():
            packages[path.name] = TaskPackage(task_id=path.name, tests_dir=path / "tests")
    if not packages:
        raise VerifierError(f"no task packages under {root}")
    return packages


def context_digest(tests_dir: Path) -> str:
    """Content hash of a build context, used to tag the image."""
    digest = hashlib.sha256()
    for path in sorted(tests_dir.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(tests_dir)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def image_tag(task_id: str, digest: str) -> str:
    return f"adb-verifier:{task_id}-{digest}"


def image_exists(tag: str) -> bool:
    completed = subprocess.run(
        ["docker", "image", "inspect", tag], capture_output=True, text=True
    )
    return completed.returncode == 0


# -- build ------------------------------------------------------------------
def build_command(package: TaskPackage, tag: str) -> list[str]:
    """The image build invocation, kept separate so it can be tested."""
    return [
        "docker", "build", "--network", "none",
        "-f", str(package.dockerfile), "-t", tag, str(package.tests_dir),
    ]


def build(package: TaskPackage, *, rebuild: bool = False, timeout: float = 900.0) -> str:
    """Build the task's verifier image; returns the tag."""
    package.require()
    digest = context_digest(package.tests_dir)
    tag = image_tag(package.task_id, digest)
    if not rebuild and image_exists(tag):
        return tag
    command = build_command(package, tag)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise VerifierError(f"docker is not on PATH: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VerifierError(f"{package.task_id}: docker build timed out") from exc
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-600:]
        raise VerifierError(f"{package.task_id}: docker build failed: {tail}")
    return tag


# -- artifacts --------------------------------------------------------------
def _read_json(path: Path) -> object | None:
    try:
        return cast(object, json.loads(path.read_text()))
    except (OSError, ValueError):
        return None


def _locate(logs: Path, name: str) -> Path | None:
    """Find one artifact: the signoff report directory first, then /logs/verifier."""
    for candidate in (logs / REPORT_DIR / name, logs / ARTIFACT_DIR / name):
        if candidate.is_file():
            return candidate
    return None


def _read_artifact(logs: Path, name: str) -> object | None:
    path = _locate(logs, name)
    return None if path is None else _read_json(path)


def parse_artifacts(logs: Path, output: str = "") -> VerifierArtifacts | None:
    """Parse the verifier's JSON outputs; ``None`` when it produced none.

    Verifiers differ in where they leave ``summary.json``/``run-summary.json``
    (report subdirectory vs ``/logs/verifier``), so both are checked.
    """
    reward_raw = _read_artifact(logs, "reward.json")
    summary_raw = _read_artifact(logs, "summary.json")
    run_raw = _read_artifact(logs, "run-summary.json")
    ctrf_raw = _read_artifact(logs, "new-ctrf.json")
    metrics_raw = _read_artifact(logs, "metrics.json")
    if not isinstance(reward_raw, dict) or not reward_raw:
        return None
    reward = cast(dict[str, object], reward_raw)
    summary = cast(dict[str, object], summary_raw) if isinstance(summary_raw, dict) else {}
    run = cast(dict[str, object], run_raw) if isinstance(run_raw, dict) else {}

    gates: list[GateResult] = []
    if isinstance(ctrf_raw, dict):
        results = cast(dict[str, object], ctrf_raw).get("results")
        if isinstance(results, dict):
            rows = cast(dict[str, object], results).get("tests")
            for item in rows if isinstance(rows, list) else []:
                if not isinstance(item, dict):
                    continue
                row = cast(dict[str, object], item)
                gates.append(
                    GateResult(
                        name=str(row.get("name", "")),
                        status=str(row.get("status", "")),
                        message=str(row.get("message", "")),
                    )
                )
    measurements = summary.get("measurements")
    failed = run.get("failed_runs")
    metrics_rows = len(metrics_raw) if isinstance(metrics_raw, list) else 0
    # The wrapper mounted over the image's ngspice counts every process,
    # including verifiers that print no run count at all.
    counter = logs / NGSPICE_COUNTER
    wrapper_runs = (
        len(counter.read_text().splitlines()) if counter.is_file() else None
    )
    raw_runs = run.get("ngspice_runs", run.get("runs"))
    if raw_runs is None:
        raw_runs = summary.get("ngspice_runs", summary.get("runs"))
    runs = int(_as_float(raw_runs))
    runs_source = "run-summary"
    if runs == 0 and metrics_rows:
        runs, runs_source = metrics_rows, "metrics-rows"
    if runs == 0:
        match = re.search(r"ngspice_(?:runs|processes)=(\d+)", output)
        if match is not None:
            runs, runs_source = int(match.group(1)), "stdout"
    if wrapper_runs is not None:
        runs, runs_source = wrapper_runs, "wrapper-count"
    wall = _as_float(run.get("wall_clock_s", summary.get("wall_clock_s")))
    wall_source = "verifier"
    if wall == 0.0:
        match = re.search(r"wall_clock_s=([\d.]+)", output)
        if match is not None:
            wall = float(match.group(1))
        else:
            wall_source = "none"
    return VerifierArtifacts(
        reward=_as_float(reward.get("reward")),
        tests_passed=int(_as_float(reward.get("tests_passed"))),
        tests_total=int(_as_float(reward.get("tests_total"))),
        partial=_as_float(reward.get("partial")),
        ngspice_runs=runs,
        wall_clock_s=wall,
        summed_run_time_s=_as_float(run.get("summed_run_time_s")),
        workers=int(_as_float(run.get("workers", 1))),
        failed_runs=tuple(str(item) for item in failed) if isinstance(failed, list) else (),
        gates=tuple(gates),
        measurements=cast(Mapping[str, object], measurements) if isinstance(measurements, dict) else {},
        metrics_rows=metrics_rows,
        runs_source=runs_source,
        wall_clock_source=wall_source,
    )


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def classify(exit_code: int | None, artifacts: VerifierArtifacts | None, output: str) -> tuple[Status, str]:
    """Status plus a human reason, from the exit code and the artifacts."""
    if exit_code == 124:
        return "timeout", "verifier exceeded the time limit"
    if artifacts is None:
        tail = output.strip().splitlines()[-1] if output.strip() else "no output"
        return "error", f"no verifier artifacts ({tail[:160]})"
    if artifacts.reward >= 1.0 and artifacts.tests_passed == artifacts.tests_total > 0:
        return "passed", ""
    failing = [gate.name for gate in artifacts.gates if gate.status != "passed"]
    reason = f"reward={artifacts.reward:g} ({artifacts.tests_passed}/{artifacts.tests_total})"
    if failing:
        reason += f"; failing: {', '.join(failing[:6])}"
    if artifacts.failed_runs:
        reason += f"; failed runs: {len(artifacts.failed_runs)}"
    if exit_code not in (0, None):
        reason += f"; exit={exit_code}"
    return "failed", reason


# -- sandbox helpers --------------------------------------------------------
def _chown_logs(logs: Path) -> None:
    """Hand ``/logs`` back to the caller: the verifier writes it as root."""
    subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{logs}:/logs", "--entrypoint", "sh", HELPER_IMAGE,
            "-c", f"chown -R {os.getuid()}:{os.getgid()} /logs",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def remove_container(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=120)


def docker_command(
    *,
    name: str,
    image: str,
    design: Path,
    logs: Path,
    spiceinit: Path,
    wrapper: Path,
    cpus: int,
    memory: str,
) -> list[str]:
    """The sandbox invocation, kept separate so it can be tested."""
    return [
        "docker", "run", "--rm", "--name", name,
        "--network", "none", "--cpus", str(cpus), "--memory", memory,
        "-e", "OMP_NUM_THREADS=1",
        "-e", "NGSPICE=/count/ngspice",
        "-e", "PATH=/count:/opt/analog-arena:/opt/ngspice/bin:/usr/local/sbin:"
        "/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "-v", f"{wrapper}:/count/ngspice:ro",
        "-v", f"{design}:/app/circuit.spi:ro",
        "-v", f"{spiceinit}:/app/.spiceinit:ro",
        # Verifiers that chdir into a temp directory still read $HOME/.spiceinit.
        # A writable mount keeps test.sh's own `printf > /root/.spiceinit` happy.
        "-v", f"{spiceinit}:/root/.spiceinit",
        "-v", f"{logs}:/logs",
        "--entrypoint", "/tests/test.sh",
        image,
    ]


def run(
    package: TaskPackage,
    solution: Path,
    *,
    image: str | None = None,
    workdir: Path,
    timeout: float = 1800.0,
    cpus: int = 2,
    memory: str = "6g",
) -> VerifierRun:
    """Run one task's verifier against ``solution`` and collect its JSON."""
    package.require()
    image = image or build(package)
    case = workdir / package.task_id
    logs = case / "logs"
    if logs.exists():
        shutil.rmtree(logs, ignore_errors=True)
    logs.mkdir(parents=True, exist_ok=True)
    design = case / "circuit.spi"
    shutil.copyfile(solution, design)
    spiceinit = case / ".spiceinit"
    spiceinit.write_text(SPICEINIT)
    wrapper = case / "ngspice-count"
    wrapper.write_text(NGSPICE_WRAPPER)
    wrapper.chmod(0o755)
    name = f"adb-vf-{package.task_id[-24:]}-{uuid.uuid4().hex[:6]}".replace("_", "-")
    command = docker_command(
        name=name, image=image, design=design, logs=logs,
        spiceinit=spiceinit, wrapper=wrapper, cpus=cpus, memory=memory,
    )
    started = time.perf_counter()
    # Stream the container log to disk: a timed-out run must still leave evidence.
    log_path = case / "run.log"
    timed_out = False
    exit_code: int | None = None
    try:
        with log_path.open("w") as sink:
            try:
                process = subprocess.Popen(
                    command, stdout=sink, stderr=subprocess.STDOUT, text=True
                )
            except FileNotFoundError as exc:
                raise VerifierError(f"docker is not on PATH: {exc}") from exc
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                remove_container(name)  # killing pid 1 ends the `docker run` client
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    process.kill()
                exit_code = 124
        output = log_path.read_text(errors="replace") if log_path.is_file() else ""
        _chown_logs(logs)
        artifacts = parse_artifacts(logs, output)
        if artifacts is not None and artifacts.wall_clock_s == 0.0:
            artifacts = replace(
                artifacts,
                wall_clock_s=time.perf_counter() - started,
                wall_clock_source="harness",
            )
        if timed_out:
            return VerifierRun(
                task_id=package.task_id,
                status="timeout",
                exit_code=124,
                wall_s=time.perf_counter() - started,
                reason=f"verifier exceeded {timeout:g}s",
                artifacts=artifacts,
                stdout_tail=_tail(output),
                image=image,
            )
        status, reason = classify(exit_code, artifacts, output)
        return VerifierRun(
            task_id=package.task_id,
            status=status,
            exit_code=exit_code,
            wall_s=time.perf_counter() - started,
            reason=reason,
            artifacts=artifacts,
            stdout_tail=_tail(output),
            image=image,
        )
    finally:
        remove_container(name)


def _tail(text: str, limit: int = 4000) -> str:
    return text.strip()[-limit:]


# -- sweep helpers ----------------------------------------------------------
def result_path(out: Path, task_id: str) -> Path:
    return out / f"{task_id}.json"


def should_skip(path: Path, resume: bool) -> bool:
    """A completed record is reused only when resuming."""
    return resume and path.is_file()


def write_result(path: Path, run_result: VerifierRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(run_result.to_json(), indent=2) + "\n")
    temporary.replace(path)


def load_result(path: Path) -> VerifierRun | None:
    """Read a stored record without re-running anything."""
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    record = cast(dict[str, object], raw)
    artifacts_raw = record.get("artifacts")
    exit_code_raw = record.get("exit_code")
    exit_code = exit_code_raw if isinstance(exit_code_raw, int) else None
    return VerifierRun(
        task_id=str(record.get("task_id", "")),
        status=cast(Status, str(record.get("status", "error"))),
        exit_code=exit_code,
        wall_s=_as_float(record.get("wall_s")),
        reason=str(record.get("reason", "")),
        artifacts=VerifierArtifacts.from_json(cast(Mapping[str, object], artifacts_raw))
        if isinstance(artifacts_raw, dict)
        else None,
        stdout_tail=str(record.get("stdout_tail", ""))[-2000:],
        image=str(record.get("image", "")),
    )


def leftover_containers(prefix: str = "adb-vf-") -> list[str]:
    completed = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True
    )
    return [line for line in completed.stdout.split() if line.startswith(prefix)]


def ngspice_threads() -> int:
    """Live ngspice tasks/threads on the host (zero after a clean sweep)."""
    completed = subprocess.run(["ps", "-eLo", "comm="], capture_output=True, text=True)
    return sum(1 for line in completed.stdout.splitlines() if "ngspice" in line)


def assert_clean() -> tuple[list[str], int]:
    """Return the leftovers and raise if the sandbox did not clean up."""
    containers = leftover_containers()
    threads = ngspice_threads()
    if containers or threads:
        raise VerifierError(
            f"sandbox not clean: containers={containers} ngspice_threads={threads}"
        )
    return containers, threads


def summarize(results: Sequence[VerifierRun]) -> dict[str, object]:
    """Aggregate a sweep: counts, sims, wall clock, and the failing list."""
    counts: dict[str, int] = {}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1
    sims = sum(item.artifacts.ngspice_runs for item in results if item.artifacts)
    verifier_wall = sum(item.artifacts.wall_clock_s for item in results if item.artifacts)
    gates_failed = [
        {"task_id": item.task_id, "gate": gate.name}
        for item in results
        if item.artifacts
        for gate in item.artifacts.gates
        if gate.status != "passed"
    ]
    wall_sources: dict[str, int] = {}
    run_sources: dict[str, int] = {}
    for item in results:
        if not item.artifacts:
            continue
        wall_sources[item.artifacts.wall_clock_source] = (
            wall_sources.get(item.artifacts.wall_clock_source, 0) + 1
        )
        run_sources[item.artifacts.runs_source] = (
            run_sources.get(item.artifacts.runs_source, 0) + 1
        )
    return {
        "tasks": len(results),
        "status_counts": dict(sorted(counts.items())),
        "ngspice_runs": sims,
        "runs_sources": dict(sorted(run_sources.items())),
        "wall_clock_sources": dict(sorted(wall_sources.items())),
        "verifier_wall_s": round(verifier_wall, 1),
        "task_wall_sum_s": round(sum(item.wall_s for item in results), 1),
        "failing": [
            {"task_id": item.task_id, "status": item.status, "reason": item.reason}
            for item in results
            if item.status != "passed"
        ],
        "failing_gates": gates_failed,
    }


