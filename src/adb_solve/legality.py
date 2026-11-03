"""Legality guards: refuse an illegal design before paying for simulations.

Three separate things, deliberately not merged:

* **hard values** (this module's ``audit``/``precheck``) — what
  ``check_circuit.py`` rejects unconditionally: positive widths/lengths, integer
  multiplicities, non-negative areas.  Pure and fast; no docker.
* **the checker** (``check_text``/``guard``) — the image's own
  ``/opt/analog-arena/check_circuit.py``, the same script the verifier runs.
  Optional: its geometry range only applies with
  ``--enforce-sky130-geometry``, which the real verifier never passes, and many
  frozen references sit outside that range anyway.
* **search boxes** (``adb_solve.params.Design.search_box``) — advice for the
  optimiser.  Never a legality gate: the corpus itself contains designs outside
  the modelled ranges.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from . import check as checker
from . import params
from .docker_backend import DEFAULT_IMAGE

ROOT = Path(__file__).resolve().parents[2]

Stage = Literal["structure", "values", "checker"]


@dataclass(frozen=True)
class Violation:
    """One reason a design was refused."""

    reason: str
    param: str = ""
    stage: Stage = "values"

    def to_json(self) -> dict[str, str]:
        return {"stage": self.stage, "param": self.param, "reason": self.reason}


@dataclass(frozen=True)
class Verdict:
    """Outcome of a guard: everything that was checked, everything found."""

    ok: bool
    violations: tuple[Violation, ...] = ()
    stages: tuple[Stage, ...] = ()
    output: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "stages": list(self.stages),
            "violations": [v.to_json() for v in self.violations],
        }

    def summary(self, limit: int = 5) -> str:
        shown = [f"{v.param}: {v.reason}" if v.param else v.reason for v in self.violations[:limit]]
        more = len(self.violations) - len(shown)
        if more > 0:
            shown.append(f"... {more} more")
        return "; ".join(shown)


def audit(design: params.Design) -> Verdict:
    """Check the netlist **as written** (corpus replay, pre-sim gate)."""
    violations = [
        Violation(reason=reason, param=param.id, stage="values")
        for param in design.params
        if (reason := params.legality_violation(param, param.value)) is not None
    ]
    return Verdict(ok=not violations, violations=tuple(violations), stages=("values",))


def precheck(design: params.Design, values: Mapping[str, float]) -> Verdict:
    """Check candidate parameter values without touching the netlist."""
    index = design.by_id()
    violations: list[Violation] = []
    for param_id, value in values.items():
        param = index.get(param_id)
        if param is None:
            violations.append(Violation(f"unknown parameter {param_id!r}", param_id, "structure"))
            continue
        if not param.writable:
            violations.append(
                Violation(f"not writable ({param.reason})", param_id, "structure")
            )
            continue
        reason = params.write_violation(param, value)
        if reason is not None:
            violations.append(Violation(reason, param_id, "values"))
    return Verdict(ok=not violations, violations=tuple(violations), stages=("values",))


@dataclass(frozen=True)
class TaskPolicy:
    """Legality settings the task package itself declares.

    These come from ``task.toml`` (``checker_allow_ideal``,
    ``checker_require_subcircuit``, ``memory_mb``), never from reference
    solutions: ideal passives are legal exactly when the task says so.
    """

    allow_ideal: tuple[str, ...] = ()
    require_subcircuit: tuple[str, ...] = ()
    enforce_geometry: bool = False
    memory_mb: int | None = None
    preprocessed: bool = False
    """The task pipeline rewrites the netlist before the checker
    (``check_area.py`` lives in the task image, not in the base tools image).
    Running the bare checker on the raw netlist would then be a false alarm."""
    source: str = ""


def task_policy(task_id: str, root: Path = ROOT) -> TaskPolicy:
    """Read the task's own checker settings; an empty policy when unknown."""
    package = root / "data" / "t1" / "tasks" / task_id / "task.toml"
    if package.exists():
        raw: Any = tomllib.loads(package.read_text())
        source = str(package.relative_to(root))
    else:
        host = root / "data" / "tasks" / f"{task_id}.json"
        if not host.exists():
            return TaskPolicy(source="")
        payload: Any = json.loads(host.read_text())
        raw = tomllib.loads(str(payload.get("taskToml") or ""))
        source = str(host.relative_to(root))
    meta = raw.get("metadata") or {}
    if not isinstance(meta, dict):
        return TaskPolicy(source=source)
    script = root / "data" / "t1" / "tasks" / task_id / "tests" / "test.sh"
    preprocessed = script.exists() and "check_area.py" in script.read_text()
    return TaskPolicy(
        allow_ideal=tuple(str(v) for v in meta.get("checker_allow_ideal", [])),
        require_subcircuit=tuple(str(v) for v in meta.get("checker_require_subcircuit", [])),
        enforce_geometry=bool(meta.get("checker_enforce_geometry", False)),
        memory_mb=int(meta["memory_mb"]) if "memory_mb" in meta else None,
        preprocessed=preprocessed,
        source=source,
    )


def check_netlist(
    text: str,
    *,
    image: str = DEFAULT_IMAGE,
    allow_ideal: Sequence[str] = (),
    enforce_geometry: bool = False,
    require_subcircuit: Sequence[str] = (),
    timeout: float = 120.0,
) -> Verdict:
    """Run the pinned ``check_circuit.py`` on ``text``."""
    result = checker.check_text(
        text,
        image=image,
        allow_ideal=tuple(allow_ideal),
        enforce_geometry=enforce_geometry,
        require_subcircuit=tuple(require_subcircuit),
        timeout=timeout,
    )
    violations = tuple(
        Violation(reason=line.strip(), stage="checker") for line in result.violations
    )
    return Verdict(ok=result.ok, violations=violations, stages=("checker",), output=result.output)


def guard(
    text: str,
    design: params.Design,
    values: Mapping[str, float] | None = None,
    *,
    checker: bool = False,
    image: str = DEFAULT_IMAGE,
    allow_ideal: Sequence[str] = (),
    enforce_geometry: bool = False,
    require_subcircuit: Sequence[str] = (),
    timeout: float = 120.0,
) -> Verdict:
    """Full guard: hard values first, the checker only if they pass.

    The checker is not run when the cheap stage already refused, so an illegal
    proposal never costs a container.
    """
    first = precheck(design, values) if values else audit(design)
    if not first.ok or not checker:
        return first
    second = check_netlist(
        text,
        image=image,
        allow_ideal=allow_ideal,
        enforce_geometry=enforce_geometry,
        require_subcircuit=require_subcircuit,
        timeout=timeout,
    )
    return Verdict(
        ok=second.ok,
        violations=first.violations + second.violations,
        stages=first.stages + second.stages,
        output=second.output,
    )
