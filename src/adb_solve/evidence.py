"""Derive corner declarations from corpus evidence.

Three evidence sources, strictest first:

``instruction``
    A sentence of the task text that names processes, supplies and
    temperatures together -- either as a cartesian statement ("all 45
    combinations of tt/ff/ss/fs/sf, 1.62/1.80/1.98 V, and -40/27/125 C") or as
    an explicit row list ("tt, 1.80 V, 40 C; ss, 1.62 V, 125 C; ...").  The
    sentence is quoted so later drift is detectable.
``deck``
    What the decks and the design netlist say: ``.lib`` corner, ``.temp``,
    DC supply values and literal ``.param`` defaults.
``assumption``
    Nothing usable was found.  The entry says so and is flagged.

Extraction never invents a number: anything it cannot source is left in
``unresolved`` and the task is flagged for review.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, cast

from . import bench
from . import netlist as spice
from .corners import GridKind, Provenance, SweepKeys, TaskCorners
from .numbers import to_si

MODEL_LIB = "/opt/sky130/continuous/sky130.lib.spice"

DEVICE_PROCESSES = {"tt", "ff", "ss", "fs", "sf", "ll", "hh", "hl", "lh", "tt_mm"}
# Statistical (mismatch) libraries are not PVT corners; they get their own decks.
STATISTICAL_PROCESSES = {"mc", "mc_mm"}
ALL_PROCESSES = DEVICE_PROCESSES | STATISTICAL_PROCESSES

NUMBER = r"[-+]?\d+(?:\.\d+)?"

SPLIT_RE = re.compile(r";|\n\s*[-*•]\s*|\n")
PROCESS_RE = re.compile(rf"\b(?:{'|'.join(sorted(ALL_PROCESSES, key=len, reverse=True))})\b")
# A unit is preceded by a run of numbers and list separators ("1.62, 1.8, and
# 1.98 V" / "-40/27/125 C").  Matching the run is far more robust than trying
# to spell every separator combination in one regex.
VALUE_RUN_RE = re.compile(r"(?P<run>[-+]?\d[\d.,/\s]*(?:and[\d.,/\s]*)*)\s*(?P<unit>V|C)\b")
# "1.62 V/125 C" is a paired stress point, not a cartesian dimension.
PAIR_RE = re.compile(rf"{NUMBER}\s*V\s*/\s*{NUMBER}\s*C\b", re.I)
LIST_SEPARATOR_RE = re.compile(r"^[\s,;]*(?:and\s*)?$")
GRID_WORDS_RE = re.compile(r"corner|combination|PVT|signoff|sweep", re.I)
MC_WORDS_RE = re.compile(r"monte|\bMC\b|mismatch sample|sample sigma|seeds?\b", re.I)
POINTS_RE = re.compile(r"\b(\d+)\s+(?:PVT\s+)?points\b", re.I)

SUPPLY_KEYS = ("supply", "vdd", "vin")
TEMPERATURE_KEYS = ("temperature", "temp", "temp_c")
PROCESS_KEYS = ("process", "corner")


def _numbers(text: str) -> list[float]:
    return [float(item) for item in re.findall(NUMBER, text)]


def _flat(paragraph: str) -> str:
    return " ".join(paragraph.replace("`", "").replace("*", "").split())


def _lists(text: str, unit: str) -> list[list[float]]:
    """Number lists annotated with ``unit`` (``V`` or ``C``).

    Runs separated only by list punctuation belong to the same list, which is
    what makes ``1.62 V, 1.80 V, 1.98 V`` one three-value list.
    """
    merged: list[tuple[int, int, list[float]]] = []
    for match in VALUE_RUN_RE.finditer(text):
        if match.group("unit") != unit:
            continue
        values = _numbers(match.group("run"))
        if unit == "V":
            values = [value for value in values if 0.2 <= value <= 6.0]
        else:
            values = [value for value in values if -70.0 <= value <= 200.0]
        if not values:
            continue
        if merged and LIST_SEPARATOR_RE.match(text[merged[-1][1] : match.start()]):
            start, _end, previous = merged[-1]
            merged[-1] = (start, match.end(), previous + values)
        else:
            merged.append((match.start(), match.end(), values))
    return [values for _start, _end, values in merged]


def _row_grids(instruction: str) -> list[dict[str, Any]]:
    """Grids declared as explicit rows: one process + one V + one C per row."""
    grids: list[dict[str, Any]] = []
    for paragraph in re.split(r"\n\s*\n", instruction):
        text = _flat(paragraph)
        rows: list[tuple[str, float, float]] = []
        for chunk in SPLIT_RE.split(text):
            processes = PROCESS_RE.findall(chunk.lower())
            volts = [item for group in _lists(chunk, "V") for item in group]
            temps = [item for group in _lists(chunk, "C") for item in group]
            if len(processes) != 1 or len(volts) != 1 or len(temps) != 1:
                continue
            rows.append((processes[0], volts[0], temps[0]))
        unique = list(dict.fromkeys(rows))
        if len(unique) >= 2:
            grids.append(
                {
                    "kind": "rows",
                    "processes": [row[0] for row in unique],
                    "supplies": sorted({row[1] for row in unique}),
                    "temperatures": sorted({row[2] for row in unique}),
                    "quote": _quote(text),
                    "rows": len(unique),
                }
            )
    return grids


def instruction_grids(instruction: str) -> list[dict[str, Any]]:
    """Candidate grids from the task text, most specific first."""
    grids: list[dict[str, Any]] = list(_row_grids(instruction))
    for paragraph in re.split(r"\n\s*\n", instruction):
        text = _flat(paragraph)
        if not text:
            continue
        processes = [name for name in PROCESS_RE.findall(text.lower()) if name in DEVICE_PROCESSES]
        pairs = [
            values
            for match in PAIR_RE.finditer(text)
            if len(values := _numbers(match.group(0))) == 2
        ]
        if len(pairs) >= 2:
            grids.append(
                {
                    "kind": "pairs",
                    "processes": list(dict.fromkeys(processes)),
                    "supplies": sorted({value for value, _temp in pairs if 0.2 <= value <= 6.0}),
                    "temperatures": sorted(
                        {temp for _value, temp in pairs if -70.0 <= temp <= 200.0}
                    ),
                    "quote": _quote(text),
                }
            )
            continue

        supplies = max(_lists(text, "V"), key=len, default=[])
        temperatures = max(_lists(text, "C"), key=len, default=[])
        if not supplies or not temperatures:
            continue
        # A single supply and a single temperature is a nominal mention, not a grid.
        if len(supplies) < 2 and len(temperatures) < 2:
            continue
        grids.append(
            {
                "kind": "cartesian",
                "processes": list(dict.fromkeys(processes)),
                "supplies": sorted(set(supplies)),
                "temperatures": sorted(set(temperatures)),
                "quote": _quote(text),
            }
        )

    def score(grid: Mapping[str, Any]) -> tuple[int, int, int]:
        size = (
            len(cast(list[str], grid["processes"]))
            * len(cast(list[float], grid["supplies"]))
            * len(cast(list[float], grid["temperatures"]))
        )
        stated = _stated_points(str(grid["quote"]))
        kind_rank = {"cartesian": 0, "rows": 1, "pairs": 2}.get(str(grid["kind"]), 3)
        return (
            1 if stated == size else 0,
            size - kind_rank,
            10 if GRID_WORDS_RE.search(str(grid["quote"])) else 0,
        )

    return sorted(grids, key=score, reverse=True)


def _quote(text: str, limit: int = 400) -> str:
    """Trim a quote at a sentence/list boundary so it stays reviewable.

    A stated point count ("This is 81 points") is the strongest evidence for a
    grid, so it is appended even when it falls past the soft limit.
    """
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = head
    for separator in (". ", "; ", ", "):
        index = head.rfind(separator)
        if index >= limit // 2:
            cut = head[: index + 1]
            break
    match = POINTS_RE.search(text)
    if match is not None and match.group(0) not in cut:
        start = text.rfind(". ", 0, match.start())
        start = 0 if start == -1 else start + 2
        end = text.find(". ", match.end())
        end = len(text) if end == -1 else end + 1
        sentence = text[start:end].strip()
        if sentence and len(cut) + len(sentence) + 1 <= limit + 200:
            cut = f"{cut} {sentence}"
    return cut


def _stated_points(text: str) -> int | None:
    match = POINTS_RE.search(text)
    return int(match.group(1)) if match else None


def deck_evidence(task: Mapping[str, Any]) -> dict[str, list[Any]]:
    """Processes, supplies and temperatures the decks themselves declare."""
    processes: list[str] = []
    supplies: list[float] = []
    temperatures: list[float] = []
    for item in task["files"]["tests"]:
        document = spice.parse(item["content"])
        for statement in document.directives(".lib"):
            words = [token.text.strip('"') for token in statement.tokens]
            if words and words[-1].lower() in DEVICE_PROCESSES:
                processes.append(words[-1].lower())
        for statement in document.directives(".temp"):
            for token in statement.positional():
                value = to_si(token.text)
                if value is not None and -70.0 <= value <= 200.0:
                    temperatures.append(value)
        for statement in document.devices():
            if statement.name[:1].upper() != "V":
                continue
            value = _source_level(statement.positional())
            if value is not None and 0.2 <= value <= 6.0:
                supplies.append(value)
    return {
        "processes": sorted(set(processes)),
        "supplies": sorted(set(supplies)),
        "temperatures": sorted(set(temperatures)),
    }


SOURCE_SPECIALS = {"AC", "PULSE", "SIN", "PWL", "EXP", "SFFM", "AM"}


def _source_level(positional: list[spice.Token]) -> float | None:
    """DC level of a voltage source written as ``DC x``, ``x AC ...`` or ``x``."""
    for index, token in enumerate(positional):
        if token.text.upper() == "DC" and index + 1 < len(positional):
            return to_si(positional[index + 1].text)
    for index, token in enumerate(positional):
        if token.text.upper() in SOURCE_SPECIALS and index > 2:
            return to_si(positional[index - 1].text)
    if len(positional) >= 3:
        return to_si(positional[-1].text)
    return None


def declared_params(task: Mapping[str, Any]) -> dict[str, str]:
    """Literal ``.param`` defaults from the decks and from the design itself."""
    values: dict[str, str] = {}
    for group in ("tests", "solution"):
        for item in task["files"][group]:
            for statement in spice.parse(item["content"]).directives(".param"):
                for key, token in statement.assignments():
                    if to_si(token.text) is None:
                        continue
                    values.setdefault(key.casefold(), token.text)
    return values


def placeholder_names(task: Mapping[str, Any]) -> list[str]:
    names: set[str] = set()
    for item in task["files"]["tests"]:
        names |= set(bench.placeholders(item["content"]))
    return sorted(names)


def _pick_key(candidates: Iterable[str], available: set[str]) -> str:
    for name in candidates:
        if name.casefold() in available:
            return name
    return next(iter(candidates))


def build_entry(task: Mapping[str, Any]) -> TaskCorners:
    """Turn one task record into a validated corner declaration."""
    task_id = str(task["id"])
    names = placeholder_names(task)
    available = {name.casefold() for name in names}
    grids = instruction_grids(str(task["instruction"]))
    evidence = deck_evidence(task)
    grid = grids[0] if grids else None

    if grid is not None:
        provenance: Provenance = "instruction"
        kind = str(grid["kind"])
        processes = list(grid["processes"]) or list(evidence["processes"]) or ["tt"]
        supplies = [float(value) for value in grid["supplies"]]
        temperatures = [float(value) for value in grid["temperatures"]]
        quote = str(grid["quote"])
    elif evidence["supplies"] and evidence["temperatures"]:
        provenance = "deck"
        kind = "none"
        processes = list(evidence["processes"]) or ["tt"]
        supplies = [float(value) for value in evidence["supplies"]]
        temperatures = [float(value) for value in evidence["temperatures"]]
        quote = ""
    else:
        provenance = "assumption"
        kind = "none"
        processes, supplies, temperatures, quote = ["tt"], [1.8], [27.0], ""

    keys = SweepKeys(
        process=_pick_key(PROCESS_KEYS, available),
        supply=_pick_key(SUPPLY_KEYS, available),
        temperature=_pick_key(TEMPERATURE_KEYS, available),
    )
    # Sweep keys must be distinct; fall back to the dimension name on collision.
    seen: set[str] = set()
    resolved: dict[str, str] = {}
    for dimension, value in (
        ("process", keys.process),
        ("supply", keys.supply),
        ("temperature", keys.temperature),
    ):
        if value.casefold() in seen:
            value = dimension
        seen.add(value.casefold())
        resolved[dimension] = value
    keys = SweepKeys(**resolved)

    values: dict[str, str] = dict(declared_params(task))
    if any(name.casefold() == "model" for name in names):
        values.setdefault("model", MODEL_LIB)

    swept = {value.casefold() for value in resolved.values()}
    covered = {name.casefold() for name in values}
    unresolved = sorted(
        name for name in names
        if name.casefold() not in covered and name.casefold() not in swept
    )
    values = {
        name: literal
        for name, literal in sorted(values.items())
        if name.casefold() not in swept
    }

    needs_review = bool(
        provenance != "instruction"
        or unresolved
        or len(grids) > 1
        or (grid is not None and grid["kind"] in {"rows", "pairs"})
        or MC_WORDS_RE.search(str(task["instruction"]))
    )
    return TaskCorners(
        task_id=task_id,
        provenance=provenance,
        quote=quote,
        processes=tuple(sorted(set(processes))),
        supplies=tuple(sorted(set(supplies))),
        temperatures=tuple(sorted(set(temperatures))),
        values=values,
        keys=keys,
        unresolved=tuple(unresolved),
        needs_review=needs_review,
        kind=cast(GridKind, kind),
    )
