"""Tunable-parameter layer over a parsed netlist.

Responsibilities (docs/REQUIREMENTS.md FR-1..FR-4, P1):

* turn every numeric device parameter into a :class:`Param` with a stable id,
  SI value, unit, grouping key and bounds derived from the PDK checker;
* mark things that must not be tuned (sources, symbolic ``.param`` values);
* write values back through :meth:`Design.apply` so that the only bytes that
  change are the digit tokens themselves.

Values are stored in SI base units.  ``native_scale`` converts back to the
unit the netlist literal uses, which is what ngspice sees: Sky130 PDK
subcircuits take ``w``/``l``/``pd``/``ps`` in micrometres and ``ad``/``as`` in
micrometre squared, so ``X... sky130_fd_pr__nfet_01v8 w=1`` is ``1e-6`` m.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Sequence

from . import netlist as spice
from .numbers import format_like, parse_literal, to_si

# Geometry limits enforced by /opt/analog-arena/check_circuit.py (micrometres).
MOS_GEOMETRY_UM: dict[str, tuple[float, float, float, float]] = {
    "sky130_fd_pr__nfet_01v8": (0.15, 8.0, 0.42, 7.0),
    "sky130_fd_pr__pfet_01v8": (0.15, 8.0, 0.42, 7.0),
    "sky130_fd_pr__nfet_01v8_lvt": (0.15, 8.0, 0.42, 7.0),
    "sky130_fd_pr__pfet_01v8_lvt": (0.35, 8.0, 0.42, 7.0),
}

INTEGER_KINDS = frozenset({"nf", "m", "mult", "mf"})
NONNEGATIVE_KINDS = frozenset({"ad", "as", "pd", "ps"})
MOS_KINDS = frozenset({"w", "l", "nf", "m", "mult", "mf", "ad", "as", "pd", "ps", "vm"})

UNIT_BY_KIND: dict[str, str] = {
    "w": "m",
    "l": "m",
    "pd": "m",
    "ps": "m",
    "ad": "m^2",
    "as": "m^2",
    "nf": "",
    "m": "",
    "mult": "",
    "mf": "",
    "vm": "",
    "value": "",  # filled from the device letter: ohm, F, H ...
    "dc": "",
    "param": "",
}

IDEAL_UNITS = {"R": "ohm", "C": "F", "L": "H", "K": ""}


@dataclass(frozen=True)
class Bounds:
    """Search box for one parameter.

    ``source`` says how far the box can be trusted:

    * ``checker``    -- legality the official checker enforces unconditionally
                        (positive geometry, integer multiplicities);
    * ``pdk-modeled``-- the checker's modelled ranges, only enforced with
                        ``--enforce-sky130-geometry``, which the task verifiers
                        do not use (many frozen references sit outside it);
    * ``heuristic``  -- a working range invented here, useful for search only;
    * ``none``       -- unknown.
    """

    minimum: float | None = None
    maximum: float | None = None
    grid: float | None = None  # None = continuous, 1.0 = integer
    source: str = "none"

    def contains(self, value: float) -> bool:
        if self.minimum is not None and value < self.minimum * (1 - 1e-12):
            return False
        if self.maximum is not None and value > self.maximum * (1 + 1e-12):
            return False
        return True

    def to_json(self) -> dict[str, object]:
        return {
            "min": self.minimum,
            "max": self.maximum,
            "grid": self.grid,
            "source": self.source,
        }


@dataclass(frozen=True)
class Param:
    """One tunable number in a netlist."""

    id: str
    kind: str
    device: str
    subckt_path: str
    value: float
    raw: str
    unit: str
    writable: bool
    group: str
    line: int
    start: int
    end: int
    native_scale: float
    bounds: Bounds
    reason: str = ""
    statement: int = -1

    # -- conversions -------------------------------------------------------
    def token_value(self, value_si: float | None = None) -> float:
        """``value_si`` expressed in the netlist literal's own units."""
        return (self.value if value_si is None else value_si) / self.native_scale

    def text_for(self, value_si: float) -> str:
        """Render ``value_si`` the way this literal is written."""
        return format_like(self.raw, self.token_value(value_si))

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "device": self.device,
            "subckt_path": self.subckt_path,
            "value": self.value,
            "raw": self.raw,
            "unit": self.unit,
            "writable": self.writable,
            "group": self.group,
            "bounds": self.bounds.to_json(),
            "reason": self.reason,
        }


@dataclass
class Design:
    """A netlist plus its extracted parameters."""

    netlist: spice.Netlist
    params: list[Param] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.netlist.text

    # -- lookups -----------------------------------------------------------
    def by_id(self) -> dict[str, Param]:
        return {param.id: param for param in self.params}

    def writable(self) -> list[Param]:
        return [param for param in self.params if param.writable]

    def groups(self) -> dict[str, list[Param]]:
        grouped: dict[str, list[Param]] = {}
        for param in self.params:
            grouped.setdefault(param.group, []).append(param)
        return grouped

    def bounds_for(self, param: Param) -> Bounds:
        """Bounds, resolving width limits that depend on the finger count."""
        if param.kind != "w":
            return param.bounds
        base = param.id.rsplit(".", 1)[0]
        fingers = self.by_id().get(f"{base}.nf")
        if fingers is None or fingers.value <= 0:
            return param.bounds
        minimum = 0.42e-6 * fingers.value
        maximum = 7.0e-6 * fingers.value
        return replace(param.bounds, minimum=minimum, maximum=maximum)

    # -- bounds ------------------------------------------------------------
    def search_box(self) -> dict[str, Bounds]:
        """Advisory ``{param_id: bounds}`` for search, not a legality gate."""
        return {param.id: self.bounds_for(param) for param in self.params}

    # -- write-back --------------------------------------------------------
    def apply(self, values: dict[str, float]) -> str:
        """Return the netlist text with ``{param_id: SI value}`` written back.

        Unknown ids, non-writable parameters and out-of-model values raise
        :class:`ValueError` instead of silently producing an illegal netlist.
        """
        index = self.by_id()
        replacements: list[spice.Replacement] = []
        for param_id, value in values.items():
            param = index.get(param_id)
            if param is None:
                raise KeyError(f"unknown parameter {param_id!r}")
            if not param.writable:
                raise ValueError(f"parameter {param_id!r} is not writable ({param.reason})")
            text = param.text_for(value)
            if text == param.raw:
                # A write-back that lands on the same literal is always legal,
                # even when the reference design sits outside the checker's
                # modelled range (some frozen references do).
                continue
            violation = _legality_violation(param, value)
            if violation is not None:
                raise ValueError(f"{param_id}={value!r}: {violation}")
            replacements.append(
                spice.Replacement(line=param.line, start=param.start, end=param.end, text=text)
            )
        return self.netlist.render(replacements)


def _mos_scales(target: str) -> dict[str, float]:
    """SI factor for the numeric parameters of a PDK subcircuit."""
    if target.startswith("sky130_fd_pr__"):
        return {"w": 1e-6, "l": 1e-6, "pd": 1e-6, "ps": 1e-6, "ad": 1e-12, "as": 1e-12}
    return {}


def _device_target(statement: spice.Statement) -> str:
    positional = statement.positional()
    return positional[-1].text.lower() if positional else ""


def _checker_bounds(kind: str, target: str, native_value: float) -> Bounds:
    """Search box for ``kind`` on a device of family ``target``."""
    geometry = MOS_GEOMETRY_UM.get(target)
    if kind in INTEGER_KINDS:
        return Bounds(minimum=1.0, maximum=None, grid=1.0, source="checker")
    if kind in NONNEGATIVE_KINDS:
        return Bounds(minimum=0.0, maximum=None, grid=None, source="checker")
    if geometry is not None and kind == "l":
        minimum, maximum, _min_w, _max_w = geometry
        return Bounds(
            minimum=minimum * 1e-6,
            maximum=maximum * 1e-6,
            grid=None,
            source="pdk-modeled",
        )
    if geometry is not None and kind == "w":
        _min_l, _max_l, min_w, max_w = geometry
        # Upper limit scales with the finger count; Design.bounds_for() fixes it.
        return Bounds(
            minimum=min_w * 1e-6,
            maximum=max_w * 1e-6,
            grid=None,
            source="pdk-modeled",
        )
    if kind in {"value", "dc"} and native_value > 0:
        return Bounds(
            minimum=native_value * 1e-3,
            maximum=native_value * 1e3,
            grid=None,
            source="heuristic",
        )
    return Bounds()


def _legality_violation(param: Param, value: float) -> str | None:
    """What ``check_circuit.py`` rejects unconditionally, or ``None``.

    Search bounds are *not* validated here: the modelled w/l ranges need an
    optional checker flag that the task verifiers do not pass, so enforcing
    them at write-back would reject designs the corpus itself contains.
    """
    if not math.isfinite(value):
        return "value must be finite"
    if param.bounds.grid is not None:
        steps = value / param.bounds.grid
        if abs(steps - round(steps)) > 1e-9:
            return f"value must be a multiple of {param.bounds.grid:g}"
        if value <= 0:
            return "value must be positive"
        return None
    if param.kind in NONNEGATIVE_KINDS:
        return None if value >= 0 else "value must be non-negative"
    if param.kind in {"w", "l", "value", "dc"}:
        return None if value > 0 else "value must be positive"
    return None


def _unique(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    index = 2
    while f"{base}#{index}" in used:
        index += 1
    candidate = f"{base}#{index}"
    used.add(candidate)
    return candidate


def extract(netlist: spice.Netlist) -> Design:
    """Extract every numeric, tunable parameter from ``netlist``."""
    params: list[Param] = []
    used: set[str] = set()

    for order, statement in enumerate(netlist.statements):
        device = statement.name
        device_type = device[0].upper()
        scope = statement.scope
        prefix = f"{scope}/{device}" if scope else device

        if statement.kind == "device" and device_type == "X":
            target = _device_target(statement)
            scales = _mos_scales(target)
            unit_for_kind = dict(UNIT_BY_KIND)
            for key, value_token in statement.assignments():
                if key not in MOS_KINDS:
                    # Unknown numeric knob on a PDK instance; still tunable,
                    # but with no unit or bounds we can justify.
                    native_scale = 1.0
                    unit = ""
                else:
                    native_scale = scales.get(key, 1.0)
                    unit = unit_for_kind[key]
                parsed = parse_literal(value_token.text)
                if parsed is None:
                    continue
                native_value = parsed.value
                param_id = _unique(f"{prefix}.{key}", used)
                params.append(
                    Param(
                        id=param_id,
                        kind=key,
                        device=device,
                        subckt_path=scope,
                        value=native_value * native_scale,
                        raw=value_token.text,
                        unit=unit,
                        writable=True,
                        group=f"{scope}:{target}" if scope else f":{target}",
                        line=value_token.line,
                        start=value_token.start,
                        end=value_token.end,
                        native_scale=native_scale,
                        bounds=_checker_bounds(key, target, native_value),
                        statement=order,
                    )
                )
            continue

        if statement.kind == "device" and device_type in IDEAL_UNITS:
            positional = statement.positional()
            if len(positional) < 2:
                continue
            token = positional[-1] if device_type != "K" else positional[-1]
            parsed = parse_literal(token.text)
            if parsed is None:
                continue
            param_id = _unique(f"{prefix}.value", used)
            params.append(
                Param(
                    id=param_id,
                    kind="value",
                    device=device,
                    subckt_path=scope,
                    value=parsed.value,
                    raw=token.text,
                    unit=IDEAL_UNITS[device_type],
                    writable=True,
                    group=f"{scope}:{device_type}" if scope else f":{device_type}",
                    line=token.line,
                    start=token.start,
                    end=token.end,
                    native_scale=1.0,
                    bounds=_checker_bounds("value", "", parsed.value),
                    statement=order,
                )
            )
            continue

        if statement.kind == "device" and device_type in {"V", "I"}:
            positional = statement.positional()
            source_token: spice.Token | None = None
            for index, candidate in enumerate(positional):
                if candidate.text.upper() == "DC" and index + 1 < len(positional):
                    source_token = positional[index + 1]
                    break
            if source_token is None and len(positional) >= 2:
                last = positional[-1]
                if parse_literal(last.text) is not None:
                    source_token = last
            parsed = parse_literal(source_token.text) if source_token is not None else None
            if source_token is not None and parsed is not None:
                param_id = _unique(f"{prefix}.dc", used)
                params.append(
                    Param(
                        id=param_id,
                        kind="dc",
                        device=device,
                        subckt_path=scope,
                        value=parsed.value,
                        raw=source_token.text,
                        unit="V" if device_type == "V" else "A",
                        writable=False,
                        reason="source/stimulus",
                        group=f"{scope}:{device_type}" if scope else f":{device_type}",
                        line=source_token.line,
                        start=source_token.start,
                        end=source_token.end,
                        native_scale=1.0,
                        bounds=Bounds(),
                        statement=order,
                    )
                )
            continue

        if statement.kind == "directive" and statement.keyword == ".param":
            for key, token in statement.assignments():
                parsed = parse_literal(token.text)
                param_id = _unique(f"{prefix}.{key}", used)
                params.append(
                    Param(
                        id=param_id,
                        kind="param",
                        device=".param",
                        subckt_path=scope,
                        value=parsed.value if parsed is not None else float("nan"),
                        raw=token.text,
                        unit="",
                        writable=parsed is not None,
                        reason="" if parsed is not None else "expression, not a numeric literal",
                        group=f"{scope}:.param" if scope else ":.param",
                        line=token.line,
                        start=token.start,
                        end=token.end,
                        native_scale=1.0,
                        bounds=Bounds(),
                        statement=order,
                    )
                )

    return Design(netlist=netlist, params=params)


def load(path: str | Path) -> Design:
    """Read ``path`` and extract its parameters."""
    return extract(spice.read(path))


def load_text(text: str, path: str | None = None) -> Design:
    """Parse ``text`` and extract its parameters."""
    return extract(spice.parse(text, path=path))


def summarise(design: Design) -> dict[str, object]:
    """Counts used by the CLI and the P1 acceptance script."""
    writable = design.writable()
    kinds: dict[str, int] = {}
    for param in writable:
        kinds[param.kind] = kinds.get(param.kind, 0) + 1
    return {
        "params": len(design.params),
        "writable": len(writable),
        "groups": len(design.groups()),
        "kinds": dict(sorted(kinds.items())),
        "statement_count": len(design.netlist.statements),
    }


def iter_writable(params: Sequence[Param]) -> list[Param]:
    return [param for param in params if param.writable]
