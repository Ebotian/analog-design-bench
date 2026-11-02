"""Simulation data model and pure post-processing.

Nothing in here touches docker or the filesystem: cases and results are plain
data, measure parsing and failure classification are pure functions, and
:class:`SimBackend` is the seam a backend (or a test double) implements.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal, Mapping, Protocol, Sequence, TypeAlias, TypedDict

SimStatus: TypeAlias = Literal["ok", "nc", "missing", "timeout", "error"]

CornerValue: TypeAlias = float | int | str
"""A value a deck placeholder or corner field can hold."""

Corner: TypeAlias = Mapping[str, CornerValue]
"""Corner identity: process/temperature plus whatever the deck was given."""

# Same ``name = number`` shape the official verifier parses (verify.py).
# It deliberately does not anchor at end of line: ngspice appends ranges such as
# ``from= 1.0e-03 to= 1.0e-02`` after the value.
MEASURE_RE = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*=\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)",
    re.IGNORECASE,
)

NC_PATTERNS: tuple[str, ...] = (
    "no convergence",
    "singular matrix",
    "timestep too small",
    "time step too small",
    "doanalyses:",
    "gmin stepping failed",
    "source stepping failed",
)


class SimResultPayload(TypedDict):
    """JSON shape stored in the cache and embedded in evaluation reports."""

    case: str
    status: str
    seconds: float
    returncode: int | None
    measures: dict[str, float]
    corner: dict[str, CornerValue]


@dataclass(frozen=True)
class SimCase:
    """One netlist × deck combination to simulate."""

    case_id: str
    netlist: str
    deck_name: str
    deck_text: str
    corner: Corner = field(default_factory=dict)

    def key_material(self) -> bytes:
        """Canonical bytes for the cache key: everything that affects results."""
        payload = {
            "netlist": self.netlist,
            "deck": self.deck_text,
            "deck_name": self.deck_name,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class SimResult:
    case_id: str
    status: SimStatus
    measures: Mapping[str, float]
    seconds: float
    returncode: int | None
    log_tail: str = ""
    corner: Corner = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_payload(self) -> SimResultPayload:
        return {
            "case": self.case_id,
            "status": self.status,
            "seconds": round(self.seconds, 3),
            "returncode": self.returncode,
            "measures": dict(self.measures),
            "corner": dict(self.corner),
        }


class SimBackend(Protocol):
    """Anything that can turn cases into results (docker, a fake, a farm)."""

    def simulate(self, cases: Sequence[SimCase]) -> list[SimResult]:
        ...


def parse_measures(output: str) -> dict[str, float]:
    """Extract ``name = value`` lines (ngspice ``.meas`` output)."""
    values: dict[str, float] = {}
    for line in output.splitlines():
        match = MEASURE_RE.match(line)
        if match is None:
            continue
        try:
            value = float(match.group(2))
        except ValueError:
            continue
        if value == value and value not in (float("inf"), float("-inf")):
            values[match.group(1).lower()] = value
    return values


def classify(returncode: int | None, output: str, measures: Mapping[str, float]) -> SimStatus:
    """Map a finished ngspice run onto ``ok/nc/missing/timeout/error``."""
    if returncode == 124:
        return "timeout"
    lowered = output.lower()
    if any(pattern in lowered for pattern in NC_PATTERNS):
        return "nc"
    if returncode not in (0, None):
        return "error"
    if not measures:
        return "missing"
    return "ok"
