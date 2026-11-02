"""Corner matrices as a declarative, versioned asset (IaC).

``configs/corners.toml`` is the single source of truth for "which process ×
supply × temperature points does each task sign off over".  It is generated
from verifier evidence first (``tests/verify.py``'s SPEC/constants), then from
task instructions, bench decks and design netlists by
``scripts/build_corners.py``, reviewed like code, and consumed by ``evaluate``.

The schema is deliberately narrow so that what it cannot know is visible:

* ``provenance`` says where the grid came from -- ``verifier`` (its SPEC or
  constants), ``instruction`` (quoted), ``deck`` (the decks' own defaults) or
  ``assumption``;
* ``unresolved`` lists deck placeholders this asset cannot supply (per-bench
  timings, MC stresses); tests assert that *exactly* those names fail to
  render, so a real gap can never hide behind a declared one;
* ``needs_review`` marks entries a human still has to confirm, and
  ``[meta]`` records the totals so review debt is countable in CI.

Nothing here guesses: a missing sweep is an error, not a default.
"""

from __future__ import annotations

import itertools
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence, TypeAlias, cast

from .sim import Corner, CornerValue

SCHEMA_VERSION = "adb-corners-1"
Provenance: TypeAlias = Literal["instruction", "deck", "assumption", "verifier"]
PROVENANCES: frozenset[str] = frozenset({"instruction", "deck", "assumption", "verifier"})

#: How the source text declared the grid.  ``cartesian`` is exhaustive;
#: ``rows`` and ``pairs`` are stress subsets, which :func:`expand` widens into a
#: product on purpose (a superset is safe to simulate, but it is not the same
#: claim, so those tasks stay flagged for review).
GridKind: TypeAlias = Literal["cartesian", "rows", "pairs", "none"]
GRID_KINDS: frozenset[str] = frozenset({"cartesian", "rows", "pairs", "none"})
MAX_CASES = 400

_ALLOWED_TASK_KEYS = frozenset(
    {
        "provenance",
        "quote",
        "processes",
        "supplies",
        "temperatures",
        "keys",
        "values",
        "unresolved",
        "needs_review",
        "kind",
        "coverage",
    }
)


class CornersError(ValueError):
    """The declaration is invalid; every problem found is listed."""


@dataclass(frozen=True)
class SweepKeys:
    """Placeholder names that receive each swept dimension."""

    process: str = "process"
    supply: str = "supply"
    temperature: str = "temperature"


@dataclass(frozen=True)
class TaskCorners:
    task_id: str
    provenance: Provenance
    quote: str
    processes: tuple[str, ...]
    supplies: tuple[float, ...]
    temperatures: tuple[float, ...]
    values: Mapping[str, CornerValue] = field(default_factory=dict)
    keys: SweepKeys = field(default_factory=SweepKeys)
    unresolved: tuple[str, ...] = ()
    needs_review: bool = False
    kind: GridKind = "cartesian"
    coverage: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class Meta:
    needs_review: int
    unresolved_values: int


@dataclass(frozen=True)
class Corners:
    schema_version: str
    meta: Meta
    tasks: Mapping[str, TaskCorners]


def expand(entry: TaskCorners, max_cases: int = MAX_CASES) -> list[Corner]:
    """Cartesian grid for one task, in a documented, stable order.

    Order is ``processes`` (outer) → ``supplies`` → ``temperatures`` (inner),
    with declared ``values`` overlaid.  Raises if the grid is implausibly
    large, which usually means a parsing mistake rather than a real signoff.
    """
    size = len(entry.processes) * len(entry.supplies) * len(entry.temperatures)
    if size > max_cases:
        raise CornersError(
            f"{entry.task_id}: {size} corner cases exceeds the {max_cases} limit"
        )
    corners: list[Corner] = []
    for process, supply, temperature in itertools.product(
        entry.processes, entry.supplies, entry.temperatures
    ):
        corner: dict[str, CornerValue] = dict(entry.values)
        for key, value in (
            (entry.keys.process, process),
            (entry.keys.supply, supply),
            (entry.keys.temperature, temperature),
        ):
            if key in corner:
                raise CornersError(
                    f"{entry.task_id}: value {key!r} collides with a swept dimension"
                )
            corner[key] = value
        corners.append(corner)
    return corners


def for_task(corners: Corners, task_id: str) -> TaskCorners:
    try:
        return corners.tasks[task_id]
    except KeyError as exc:
        raise CornersError(f"task {task_id!r} is not declared in the corner asset") from exc


# -- loading ----------------------------------------------------------------
def load(path: str | Path = Path("configs/corners.toml")) -> Corners:
    """Read and validate a corner declaration."""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CornersError(f"{path}: {exc}") from exc
    return parse(raw, source=str(path))


def parse(raw: Mapping[str, Any], source: str = "<memory>") -> Corners:
    """Validate a decoded declaration."""
    problems: list[str] = []

    schema = raw.get("schema_version")
    if schema != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION!r}, got {schema!r}")

    meta = _parse_meta(raw.get("meta"), problems)
    tasks_raw = raw.get("tasks")
    tasks: dict[str, TaskCorners] = {}
    if not isinstance(tasks_raw, dict) or not tasks_raw:
        problems.append("no [tasks.*] tables declared")
        tasks_raw = {}
    for task_id, body in cast(dict[str, Any], tasks_raw).items():
        entry = _parse_task(task_id, body, problems)
        if entry is not None:
            tasks[task_id] = entry

    if problems:
        raise CornersError(f"{source}:\n  " + "\n  ".join(problems))

    flagged = sum(1 for entry in tasks.values() if entry.needs_review)
    unresolved = sum(len(entry.unresolved) for entry in tasks.values())
    assert meta is not None
    if meta.needs_review != flagged:
        problems.append(
            f"[meta].needs_review is {meta.needs_review} but {flagged} task(s) are flagged"
        )
    if meta.unresolved_values != unresolved:
        problems.append(
            f"[meta].unresolved_values is {meta.unresolved_values} but {unresolved} name(s) are listed"
        )
    if problems:
        raise CornersError(f"{source}:\n  " + "\n  ".join(problems))
    return Corners(schema_version=SCHEMA_VERSION, meta=meta, tasks=tasks)


def _parse_meta(raw: object, problems: list[str]) -> Meta | None:
    if not isinstance(raw, dict):
        problems.append("missing [meta] table")
        return None
    table = cast(dict[str, Any], raw)
    values: dict[str, int] = {}
    for key in ("needs_review", "unresolved_values"):
        value = table.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            problems.append(f"[meta].{key} must be a non-negative integer")
            continue
        values[key] = value
    if len(values) != 2:
        return None
    return Meta(needs_review=values["needs_review"], unresolved_values=values["unresolved_values"])


def _parse_task(task_id: str, body: object, problems: list[str]) -> TaskCorners | None:
    prefix = f"tasks.{task_id}"
    if not isinstance(body, dict):
        problems.append(f"{prefix}: must be a table")
        return None
    table = cast(dict[str, Any], body)
    start = len(problems)

    unknown = set(table) - _ALLOWED_TASK_KEYS
    if unknown:
        problems.append(f"{prefix}: unknown key(s) {', '.join(sorted(unknown))}")

    provenance = table.get("provenance")
    if provenance not in PROVENANCES:
        problems.append(f"{prefix}: provenance must be one of {sorted(PROVENANCES)}")

    quote = table.get("quote", "")
    if not isinstance(quote, str):
        problems.append(f"{prefix}: quote must be a string")
        quote = ""
    elif provenance == "instruction" and not quote.strip():
        problems.append(f"{prefix}: instruction-sourced entries need a non-empty quote")

    processes = _string_list(table.get("processes"), f"{prefix}.processes", problems)
    supplies = _float_list(table.get("supplies"), f"{prefix}.supplies", problems)
    temperatures = _float_list(
        table.get("temperatures"), f"{prefix}.temperatures", problems
    )
    for supply in supplies:
        if supply <= 0:
            problems.append(f"{prefix}.supplies: {supply} must be positive")
    for temperature in temperatures:
        if not -100.0 <= temperature <= 300.0:
            problems.append(f"{prefix}.temperatures: {temperature} outside -100..300 C")
    for name in processes:
        if not name.strip():
            problems.append(f"{prefix}.processes: empty process name")

    kind = table.get("kind", "cartesian")
    if kind not in GRID_KINDS:
        problems.append(f"{prefix}: kind must be one of {sorted(GRID_KINDS)}")
        kind = "none"

    keys = _parse_keys(table.get("keys"), prefix, problems)
    values = _parse_values(table.get("values"), prefix, problems)
    unresolved = _string_list(
        table.get("unresolved"), f"{prefix}.unresolved", problems,
        required=False, allow_empty=True,
    )
    coverage = _parse_coverage(table.get("coverage"), prefix, problems)
    needs_review = table.get("needs_review", False)
    if not isinstance(needs_review, bool):
        problems.append(f"{prefix}.needs_review must be a boolean")
        needs_review = False

    if len(problems) != start:
        return None
    return TaskCorners(
        task_id=task_id,
        provenance=cast(Provenance, provenance),
        quote=quote.strip(),
        processes=tuple(processes),
        supplies=tuple(supplies),
        temperatures=tuple(temperatures),
        values=values,
        keys=keys,
        unresolved=tuple(unresolved),
        needs_review=bool(needs_review),
        kind=cast(GridKind, kind),
        coverage=coverage,
    )


def _string_list(
    raw: object,
    where: str,
    problems: list[str],
    *,
    required: bool = True,
    allow_empty: bool = False,
) -> list[str]:
    if raw is None:
        if required:
            problems.append(f"{where}: missing required array")
        return []
    if not isinstance(raw, list):
        problems.append(f"{where}: must be an array of strings")
        return []
    if not raw and not allow_empty:
        problems.append(f"{where}: must not be empty")
        return []
    items: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            problems.append(f"{where}: item {item!r} is not a string")
            continue
        if item in items:
            problems.append(f"{where}: duplicate {item!r}")
            continue
        items.append(item)
    return items


def _float_list(raw: object, where: str, problems: list[str]) -> list[float]:
    if raw is None:
        problems.append(f"{where}: missing required array")
        return []
    if not isinstance(raw, list):
        problems.append(f"{where}: must be an array of numbers")
        return []
    if not raw:
        problems.append(f"{where}: must not be empty")
        return []
    items: list[float] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            problems.append(f"{where}: item {item!r} is not a number")
            continue
        value = float(item)
        if not math.isfinite(value):
            problems.append(f"{where}: item {item!r} is not finite")
            continue
        if value in items:
            problems.append(f"{where}: duplicate {value}")
            continue
        items.append(value)
    return items


def _parse_keys(raw: object, prefix: str, problems: list[str]) -> SweepKeys:
    if raw is None:
        return SweepKeys()
    if not isinstance(raw, dict):
        problems.append(f"{prefix}.keys: must be a table")
        return SweepKeys()
    table = cast(dict[str, Any], raw)
    unknown = set(table) - {"process", "supply", "temperature"}
    if unknown:
        problems.append(f"{prefix}.keys: unknown key(s) {', '.join(sorted(unknown))}")
    names: dict[str, str] = {}
    for key in ("process", "supply", "temperature"):
        value = table.get(key, key)
        if not isinstance(value, str) or not value:
            problems.append(f"{prefix}.keys.{key} must be a non-empty string")
            continue
        names[key] = value
    if len(names) != 3:
        return SweepKeys()
    if len(set(names.values())) != 3:
        problems.append(f"{prefix}.keys: sweep keys must be distinct")
    return SweepKeys(process=names["process"], supply=names["supply"], temperature=names["temperature"])


def _parse_coverage(
    raw: object, prefix: str, problems: list[str]
) -> dict[str, tuple[str, ...]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.append(f"{prefix}.coverage: must be a table")
        return {}
    coverage: dict[str, tuple[str, ...]] = {}
    for name, value in cast(dict[str, Any], raw).items():
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{prefix}.coverage: empty subset name")
            continue
        items = _string_list(
            value, f"{prefix}.coverage.{name}", problems,
            required=True, allow_empty=False,
        )
        coverage[name] = tuple(items)
    return coverage


def _parse_values(raw: object, prefix: str, problems: list[str]) -> dict[str, CornerValue]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.append(f"{prefix}.values: must be a table")
        return {}
    values: dict[str, CornerValue] = {}
    for key, value in cast(dict[str, Any], raw).items():
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            problems.append(f"{prefix}.values.{key}: must be a string or number")
            continue
        values[key] = value
    return values


# -- writing (the asset is generated, then reviewed like code) ---------------
def dumps(tasks: Sequence[TaskCorners], header: str = "") -> str:
    """Serialise declarations to the canonical TOML text.

    Deterministic: tasks sorted by id, dimensions sorted, values sorted, so a
    regeneration that changes nothing produces a byte-identical file.
    """
    ordered = sorted(tasks, key=lambda entry: entry.task_id)
    flagged = sum(1 for entry in ordered if entry.needs_review)
    unresolved = sum(len(entry.unresolved) for entry in ordered)
    lines: list[str] = []
    if header:
        lines.extend(f"# {line}".rstrip() for line in header.strip().splitlines())
        lines.append("")
    lines.extend(
        [
            f'schema_version = "{SCHEMA_VERSION}"',
            "",
            "[meta]",
            f"needs_review = {flagged}",
            f"unresolved_values = {unresolved}",
            "",
        ]
    )
    for entry in ordered:
        lines.append(f'[tasks."{entry.task_id}"]')
        lines.append(f'provenance = "{entry.provenance}"')
        lines.append(f'kind = "{entry.kind}"')
        if entry.quote:
            lines.append(f"quote = {_toml_string(entry.quote)}")
        lines.append(f"processes = [{', '.join(_toml_string(p) for p in entry.processes)}]")
        lines.append(f"supplies = [{', '.join(_toml_number(s) for s in entry.supplies)}]")
        lines.append(
            f"temperatures = [{', '.join(_toml_number(t) for t in entry.temperatures)}]"
        )
        lines.append(
            "keys = { "
            + ", ".join(
                f'{dimension} = {_toml_string(getattr(entry.keys, dimension))}'
                for dimension in ("process", "supply", "temperature")
            )
            + " }"
        )
        lines.append(f"needs_review = {'true' if entry.needs_review else 'false'}")
        if entry.unresolved:
            lines.append(
                f"unresolved = [{', '.join(_toml_string(name) for name in entry.unresolved)}]"
            )
        if entry.coverage:
            lines.append(
                "coverage = { "
                + ", ".join(
                    f"{_toml_string(name)} = [{', '.join(_toml_string(item) for item in corners)}]"
                    for name, corners in sorted(entry.coverage.items())
                )
                + " }"
            )
        if entry.values:
            lines.append("")
            lines.append(f'[tasks."{entry.task_id}".values]')
            for name, value in sorted(entry.values.items()):
                literal = _toml_string(value) if isinstance(value, str) else _toml_number(value)
                lines.append(f"{name} = {literal}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _toml_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_number(value: float) -> str:
    return repr(float(value))


def write(path: str | Path, tasks: Sequence[TaskCorners], header: str = "") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(tasks, header))
