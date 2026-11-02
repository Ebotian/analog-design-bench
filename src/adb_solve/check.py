"""Wrapper around the pinned ``check_circuit.py`` legality gate.

The checker runs inside the Sky130 tools image, so its PDK subcircuit list and
geometry limits are the same ones the bench verifier uses.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .docker_backend import DEFAULT_IMAGE, SimulationError

CHECKER = "/opt/analog-arena/check_circuit.py"


@dataclass
class CheckResult:
    ok: bool
    returncode: int
    violations: list[str] = field(default_factory=list)
    output: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "violations": self.violations,
        }


def check_text(
    text: str,
    *,
    image: str = DEFAULT_IMAGE,
    allow_ideal: tuple[str, ...] = (),
    enforce_geometry: bool = False,
    require_subcircuit: tuple[str, ...] = (),
    timeout: float = 120.0,
    workdir: Path | None = None,
) -> CheckResult:
    """Run the legality checker on ``text``.

    ``allow_ideal`` mirrors the task's ``checker_allow_ideal`` setting, e.g.
    ``("R", "C")`` for designs that are allowed to use ideal passives.
    """
    import tempfile

    directory = Path(workdir) if workdir is not None else Path(tempfile.mkdtemp(prefix="adb-check-"))
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "circuit.spi"
    target.write_text(text)
    command = [
        "docker", "run", "--rm", "--network", "none",
        "-v", f"{directory}:/work:ro",
        image,
        "python3", CHECKER, f"/work/{target.name}",
    ]
    if allow_ideal:
        command.append("--allow-ideal")
        command.extend(allow_ideal)
    if enforce_geometry:
        command.append("--enforce-sky130-geometry")
    if require_subcircuit:
        command.append("--require-subcircuit")
        command.extend(require_subcircuit)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise SimulationError(f"docker is not on PATH: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return CheckResult(ok=False, returncode=124, violations=["checker timed out"], output=output)
    output = (completed.stdout or "") + (completed.stderr or "")
    violations = [
        line for line in output.splitlines()
        if line.startswith("/") and "netlist check passed" not in line
    ]
    return CheckResult(
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        violations=violations,
        output=output.strip(),
    )
