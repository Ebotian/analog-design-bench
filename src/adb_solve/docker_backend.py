"""Docker backend: run ngspice decks in the pinned Sky130 tools image.

The image ships ngspice-46, the Sky130 PDK and
``/opt/analog-arena/check_circuit.py``.  Bench decks reference the design as
``.include "/app/circuit.spi"``, so each worker mounts its own directory at
``/app``.

One long-lived container per worker keeps the overhead at roughly one
``docker exec`` (~40 ms) instead of a full ``docker run`` per simulation.
Workers rewrite ``circuit.spi`` in their own directory between cases, so cases
run concurrently without clobbering each other.  ``timeout`` runs *inside* the
container so a hung ngspice is killed rather than orphaned.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Sequence

from .sim import SimCase, SimResult, classify, parse_measures

DEFAULT_IMAGE = (
    "ghcr.io/arcadia-1/analog-arena-sky130-tools"
    "@sha256:668783dd8cbcd5ebce5264785803ce73a9466e79dbe4c98cdf4566348a33437a"
)
NGSPICE = "/opt/ngspice/bin/ngspice"


class SimulationError(RuntimeError):
    """The simulation backend itself broke (docker missing, image absent ...)."""


def image_id(image: str = DEFAULT_IMAGE) -> str:
    """Immutable id of ``image`` so cache entries cannot leak across builds."""
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SimulationError(f"cannot inspect docker image {image}: {exc}") from exc
    return completed.stdout.strip()


class _Worker:
    """One container plus the host directory mounted at ``/app``."""

    def __init__(
        self,
        image: str,
        root: Path,
        index: int,
        timeout: float,
        oneshot: bool,
        memory: str | None = None,
    ):
        self.image = image
        self.dir = root / f"worker-{index}"
        self.dir.mkdir(parents=True, exist_ok=True)
        # ngspice reads .spiceinit from the working directory.  One thread per
        # simulation is what the official verifier uses; letting each worker
        # spawn a full OMP pool behind --cpus 1 only adds context switching.
        (self.dir / ".spiceinit").write_text("set num_threads=1\n")
        self.timeout = timeout
        self.oneshot = oneshot
        self.memory = memory
        self.name = f"adb-sim-{uuid.uuid4().hex[:10]}"
        self._started = False

    def _limits(self) -> list[str]:
        return ["--memory", self.memory] if self.memory else []

    def start(self) -> None:
        if self.oneshot:
            return
        subprocess.run(
            [
                "docker", "run", "-d", "--rm", "--name", self.name,
                "--network", "none",
                "--cpus", "1",
                *self._limits(),
                "-v", f"{self.dir}:/app",
                "-w", "/app",
                "--entrypoint", "sleep",
                self.image,
                "infinity",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        subprocess.run(
            ["docker", "rm", "-f", self.name],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self._started = False

    def run(self, case: SimCase) -> SimResult:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "circuit.spi").write_text(case.netlist)
        deck_path = self.dir / case.deck_name
        deck_path.parent.mkdir(parents=True, exist_ok=True)
        deck_path.write_text(case.deck_text)

        command = ["docker", "exec", "-w", "/app", self.name]
        if self.oneshot:
            command = [
                "docker", "run", "--rm", "--network", "none",
                *self._limits(),
                "-v", f"{self.dir}:/app", "-w", "/app", self.image,
            ]
        command += ["timeout", str(self.timeout), NGSPICE, "-b", f"/app/{case.deck_name}"]

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout + 30,
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout if isinstance(exc.stdout, str) else ""
            return SimResult(
                case_id=case.case_id,
                status="timeout",
                measures=parse_measures(output),
                seconds=time.perf_counter() - started,
                returncode=124,
                log_tail=_tail(output),
                corner=case.corner,
            )
        seconds = time.perf_counter() - started
        output = (completed.stdout or "") + (completed.stderr or "")
        measures = parse_measures(output)
        return SimResult(
            case_id=case.case_id,
            status=classify(completed.returncode, output, measures),
            measures=measures,
            seconds=seconds,
            returncode=completed.returncode,
            log_tail=_tail(output),
            corner=case.corner,
        )


def _tail(text: str, limit: int = 1200) -> str:
    return text.strip()[-limit:]


class DockerBackend:
    """``SimBackend`` that runs cases inside the pinned docker image."""

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        jobs: int = 1,
        timeout: float = 120.0,
        root: Path = Path(".work/sim"),
        oneshot: bool = False,
        memory: str | None = "4g",
    ):
        self.image = image
        self.jobs = jobs
        self.timeout = timeout
        self.root = root
        self.oneshot = oneshot
        self.memory = memory

    def simulate(self, cases: Sequence[SimCase]) -> list[SimResult]:
        if not cases:
            return []
        if shutil.which("docker") is None:
            raise SimulationError("docker is not on PATH")
        self.root.mkdir(parents=True, exist_ok=True)
        jobs = max(1, min(self.jobs, len(cases)))
        workers = [
            _Worker(self.image, self.root, index, self.timeout, self.oneshot, self.memory)
            for index in range(jobs)
        ]
        for worker in workers:
            worker.start()

        results: list[SimResult | None] = [None] * len(cases)
        chunks = [list(range(index, len(cases), jobs)) for index in range(jobs)]
        from concurrent.futures import ThreadPoolExecutor

        try:
            def consume(worker: _Worker, indices: list[int]) -> None:
                for index in indices:
                    results[index] = worker.run(cases[index])

            with ThreadPoolExecutor(max_workers=jobs) as pool:
                futures = [
                    pool.submit(consume, worker, chunk)
                    for worker, chunk in zip(workers, chunks)
                ]
                for future in futures:
                    future.result()
        finally:
            for worker in workers:
                worker.stop()
        return [result for result in results if result is not None]
