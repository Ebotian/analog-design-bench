"""Read a task's signoff grid from its official verifier.

The public verifier is the strongest available evidence for "which process ×
supply × temperature points does this task sign off over".  Two forms occur in
``data/t1/tasks/<id>/tests/verify.py``:

* a TOML ``SPEC`` string (``tomllib.loads``) with ``[pvt]`` / ``[coverage]``
  (and a few sibling spellings such as ``[operating_grid]``);
* plain Python constants (``CORNERS`` / ``SUPPLIES`` / ``TEMPERATURES``) or a
  list of point tuples (``PVT`` / ``POINTS`` / ``CASES``).

This module extracts those declarations without executing the verifier: TOML
is parsed by ``tomllib``, Python is parsed by ``ast`` and only a whitelist of
literal/container/product expressions is evaluated.  Anything it cannot prove
returns ``None``; the caller then falls back to prose/deck evidence and keeps
the entry flagged for review.
"""

from __future__ import annotations

import ast
import re
import tomllib
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast

from .corners import GridKind

SPEC_RE = re.compile(
    r"SPEC\s*=\s*tomllib\.loads\(\s*r?(?P<quote>'''|\"\"\")(?P<body>.*?)(?P=quote)\s*\)",
    re.S,
)
POINT_KEYS = (
    "PVT",
    "PVT_ROWS",
    "PVT_POINTS",
    "POINTS",
    "CASES",
    "HOLD_POINTS",
    "FFT_PVT",
)
PROCESS_NAMES = {
    "tt", "ff", "ss", "fs", "sf", "ll", "hh", "hl", "lh", "tt_mm", "mc", "mc_mm",
}


class SpecError(ValueError):
    """A verifier declaration exists but cannot be read safely."""


@dataclass(frozen=True)
class VerifierSpec:
    """One verifier's declared signoff grid and per-bench corner subsets."""

    source: str
    processes: tuple[str, ...]
    supplies: tuple[float, ...]
    temperatures: tuple[float, ...]
    coverage: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    kind: GridKind = "cartesian"
    quote: str = ""

    def complete(self) -> bool:
        return bool(self.processes and self.supplies and self.temperatures)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not isinstance(number, float):
        return None
    if not -1e12 <= number <= 1e12:
        return None
    return number


def _evaluate(node: ast.AST, environment: Mapping[str, object]) -> object:
    """Evaluate the small expression subset used by verifier constants."""
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, environment)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in environment:
            return environment[node.id]
        raise SpecError(f"unknown name {node.id}")
    if isinstance(node, ast.Tuple):
        return tuple(_evaluate(item, environment) for item in node.elts)
    if isinstance(node, ast.List):
        return [_evaluate(item, environment) for item in node.elts]
    if isinstance(node, ast.Set):
        return {_evaluate(item, environment) for item in node.elts}
    if isinstance(node, ast.Dict):
        table: dict[object, object] = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                raise SpecError("dictionary unpacking is not supported")
            table[_evaluate(key, environment)] = _evaluate(value, environment)
        return table
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        numeric = _number(_evaluate(node.operand, environment))
        if numeric is None:
            raise SpecError("unary minus on a non-number")
        return -numeric
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left, environment)
        right = _evaluate(node.right, environment)
        if isinstance(node.op, ast.Mult):
            return cast(Any, left) * cast(Any, right)
        if isinstance(node.op, ast.Add):
            return cast(Any, left) + cast(Any, right)
        if isinstance(node.op, ast.Sub):
            return cast(Any, left) - cast(Any, right)
        if isinstance(node.op, ast.Div):
            return cast(Any, left) / cast(Any, right)
        raise SpecError("unsupported binary operator")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        name = node.func.id
        arguments = [_evaluate(item, environment) for item in node.args]
        if name == "product":
            return [
                tuple(item)
                for item in product(*cast(Sequence[Iterable[Any]], arguments))
            ]
        if name == "list":
            return list(cast(Sequence[Any], arguments[0]))
        if name == "tuple":
            return tuple(cast(Sequence[Any], arguments[0]))
        if name == "range":
            return list(range(*cast(Sequence[int], arguments)))
        if name == "len":
            return len(cast(Sequence[Any], arguments[0]))
        raise SpecError(f"unsupported call {name}")
    raise SpecError(f"unsupported expression {type(node).__name__}")


def _environment(source: str) -> dict[str, object]:
    """Top-level simple assignments of a verifier module, in order."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SpecError(f"verifier is not valid Python: {exc}") from exc
    environment: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        try:
            environment[node.targets[0].id] = _evaluate(node.value, environment)
        except (SpecError, ZeroDivisionError, TypeError):
            continue
    return environment


def _points(value: object) -> tuple[list[tuple[str, float, float]], GridKind] | None:
    """Read point tuples/dicts as (corner, supply, temperature)."""
    if not isinstance(value, (tuple, list)) or not value:
        return None
    points: list[tuple[str, float, float]] = []
    shape = "triple"
    for item in value:
        if isinstance(item, dict):
            table_row = cast(Mapping[str, object], item)
            corner = table_row.get("corner") or table_row.get("process")
            supply = table_row.get("supply_v") or table_row.get("supply") or table_row.get("vdd")
            temperature = (
                table_row.get("temperature_c")
                or table_row.get("temperature")
                or table_row.get("temp")
            )
            supply_number = _number(supply)
            temperature_number = _number(temperature)
            if isinstance(corner, str) and supply_number is not None and temperature_number is not None:
                points.append((corner, supply_number, temperature_number))
                continue
            return None
        if isinstance(item, (tuple, list)):
            values = list(item)
            if (
                len(values) >= 4
                and isinstance(values[0], str)
                and isinstance(values[1], str)
                and values[1] in PROCESS_NAMES
                and (supply := _number(values[2])) is not None
                and (temperature := _number(values[3])) is not None
            ):
                points.append((values[1], supply, temperature))
                continue
            if (
                len(values) >= 3
                and isinstance(values[0], str)
                and (supply := _number(values[1])) is not None
                and (temperature := _number(values[2])) is not None
            ):
                points.append((values[0], supply, temperature))
                continue
            if (
                len(values) == 2
                and isinstance(values[0], str)
                and (temperature := _number(values[1])) is not None
            ):
                points.append((values[0], 1.8, temperature))
                shape = "pair"
                continue
        return None
    return points, "pairs" if shape == "pair" else "rows"


def _dimensions(
    processes: object, supplies: object, temperatures: object
) -> tuple[tuple[str, ...], tuple[float, ...], tuple[float, ...]] | None:
    if not isinstance(processes, (tuple, list)):
        return None
    if not isinstance(supplies, (tuple, list)) or not isinstance(temperatures, (tuple, list)):
        return None
    names = [item for item in processes if isinstance(item, str) and item in PROCESS_NAMES]
    volts = [value for item in supplies if (value := _number(item)) is not None]
    degrees = [value for item in temperatures if (value := _number(item)) is not None]
    if not names or not volts or not degrees:
        return None
    if not all(0.2 <= value <= 6.0 for value in volts):
        return None
    if not all(-100.0 <= value <= 300.0 for value in degrees):
        return None
    return tuple(dict.fromkeys(names)), tuple(dict.fromkeys(volts)), tuple(dict.fromkeys(degrees))


def _operating_pairs(value: object) -> list[tuple[float, float]] | None:
    """Operating points that carry supply/temperature but no corner."""
    if not isinstance(value, (tuple, list)) or not value:
        return None
    pairs: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        row = cast(Mapping[str, object], item)
        supply = _number(row.get("supply_v") or row.get("supply") or row.get("vdd"))
        temperature = _number(
            row.get("temperature_c") or row.get("temperature") or row.get("temp")
        )
        if supply is None or temperature is None:
            return None
        pairs.append((supply, temperature))
    return pairs


def _coverage_from_toml(spec: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    coverage: dict[str, tuple[str, ...]] = {}
    table = spec.get("coverage")
    if isinstance(table, dict):
        for name, value in cast(Mapping[str, object], table).items():
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                coverage[str(name)] = tuple(cast(list[str], value))
    dynamic = spec.get("dynamic_conditions")
    if isinstance(dynamic, dict):
        corners = cast(Mapping[str, object], dynamic).get("corners")
        if isinstance(corners, list) and all(isinstance(item, str) for item in corners):
            coverage["dynamic_conditions"] = tuple(cast(list[str], corners))
    secondary = spec.get("secondary")
    if isinstance(secondary, dict):
        points = cast(Mapping[str, object], secondary).get("points")
        parsed = _points(points)
        if parsed is not None:
            coverage["secondary"] = tuple(sorted({point[0] for point in parsed[0]}))
    return coverage


def _from_toml(source: str) -> VerifierSpec | None:
    match = SPEC_RE.search(source)
    if match is None:
        return None
    try:
        spec = tomllib.loads(match.group("body"))
    except tomllib.TOMLDecodeError as exc:
        raise SpecError(f"embedded SPEC is invalid TOML: {exc}") from exc
    table = spec.get("pvt") or spec.get("operating_grid")
    if not isinstance(table, dict):
        return None
    pvt = cast(Mapping[str, Any], table)
    processes = pvt.get("corners")
    supplies = pvt.get("supply_voltages_v") or pvt.get("input_voltages_v")
    temperatures = pvt.get("temperatures_c")
    points = _points(pvt.get("operating_points"))
    kind: GridKind = "cartesian"
    if points is not None:
        rows = points[0]
        processes = sorted({row[0] for row in rows})
        supplies = sorted({row[1] for row in rows})
        temperatures = sorted({row[2] for row in rows})
        kind = "rows" if len(rows) != len(set(processes)) * len(set(supplies)) * len(set(temperatures)) else "cartesian"
    elif (operating := _operating_pairs(pvt.get("operating_points"))) is not None:
        supplies = sorted({pair[0] for pair in operating})
        temperatures = sorted({pair[1] for pair in operating})
        kind = "pairs"
    dimensions = _dimensions(processes, supplies, temperatures)
    if dimensions is None:
        return None
    names, volts, degrees = dimensions
    return VerifierSpec(
        source="toml",
        processes=names,
        supplies=volts,
        temperatures=degrees,
        coverage=_coverage_from_toml(spec),
        kind=kind,
        quote="verifier SPEC ([pvt]/[coverage])",
    )


def _from_python(source: str) -> VerifierSpec | None:
    environment = _environment(source)
    dimensions = _dimensions(
        environment.get("CORNERS"),
        environment.get("SUPPLIES") or environment.get("SUPPLIES_V"),
        environment.get("TEMPERATURES") or environment.get("TEMPERATURES_C"),
    )
    coverage: dict[str, tuple[str, ...]] = {}
    for name, value in environment.items():
        if name.endswith("_POINTS") and name != "POINTS":
            parsed = _points(value)
            if parsed is not None:
                coverage[name[: -len("_POINTS")].casefold()] = tuple(
                    sorted({row[0] for row in parsed[0]})
                )
    if dimensions is not None:
        names, volts, degrees = dimensions
        return VerifierSpec(
            source="python",
            processes=names,
            supplies=volts,
            temperatures=degrees,
            coverage=coverage,
            kind="cartesian",
            quote="verifier constants (CORNERS/SUPPLIES/TEMPERATURES)",
        )
    for key in POINT_KEYS:
        parsed = _points(environment.get(key))
        if parsed is None:
            continue
        rows, kind = parsed
        names = tuple(sorted({row[0] for row in rows}))
        volts = tuple(sorted({row[1] for row in rows}))
        degrees = tuple(sorted({row[2] for row in rows}))
        return VerifierSpec(
            source="python",
            processes=names,
            supplies=volts,
            temperatures=degrees,
            coverage=coverage,
            kind=kind,
            quote=f"verifier constant {key}",
        )
    return None


def extract(source: str) -> VerifierSpec | None:
    """Read the verifier source; ``None`` when it declares no usable grid."""
    spec = _from_toml(source)
    if spec is not None:
        return spec
    return _from_python(source)


def from_tests_dir(tests_dir: str | Path) -> VerifierSpec | None:
    """Read ``tests/verify.py`` of one task, if present."""
    path = Path(tests_dir) / "verify.py"
    if not path.is_file():
        return None
    try:
        return extract(path.read_text(errors="replace"))
    except (OSError, SpecError):
        return None
