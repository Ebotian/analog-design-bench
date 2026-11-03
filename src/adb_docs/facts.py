"""Resolve documentation facts from committed artifacts.

A fact stores a *selector*, never a value: a JSON path, a TOML path, a line
count, or a callable.  Values are resolved at render time, so prose cannot
carry a number that the repository's own artifacts disagree with.

The registry format is documented in ``docs/facts.toml``.
"""

from __future__ import annotations

import importlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[2]


class FactError(ValueError):
    """A fact cannot be resolved from its declared source."""


@dataclass(frozen=True)
class Fact:
    """One resolved fact: its id, the value, and how it prints."""

    id: str
    value: object
    display: str
    spec: Mapping[str, Any]


def registry_path(root: Path = ROOT) -> Path:
    return root / "docs" / "facts.toml"


def load_registry(path: Path | None = None) -> dict[str, Mapping[str, Any]]:
    """Read the fact registry; rejects entries without an extractor."""
    source = path if path is not None else registry_path()
    raw = tomllib.loads(source.read_text())
    facts = raw.get("facts")
    if not isinstance(facts, dict):
        raise FactError(f"{source}: missing [facts] table")
    out: dict[str, Mapping[str, Any]] = {}
    for name, spec in facts.items():
        if not isinstance(spec, dict):
            raise FactError(f"{source}: fact {name!r} is not a table")
        if "kind" not in spec:
            raise FactError(f"{source}: fact {name!r} has no kind")
        out[str(name)] = spec
    return out


def resolve(root: Path = ROOT, path: Path | None = None) -> dict[str, Fact]:
    """Resolve every registered fact, sorted by id for stable output."""
    registry = load_registry(path if path is not None else registry_path(root))
    calls: dict[str, object] = {}
    resolved: dict[str, Fact] = {}
    for name in sorted(registry):
        spec = registry[name]
        value = _extract(root, name, spec, calls)
        resolved[name] = Fact(id=name, value=value, display=_display(name, spec, value), spec=spec)
    return resolved


def _extract(
    root: Path,
    name: str,
    spec: Mapping[str, Any],
    calls: dict[str, object],
) -> object:
    kind = spec.get("kind")
    if kind == "json":
        return _dig(_read_json(root, name, spec), spec.get("path"), name)
    if kind == "toml":
        return _dig(_read_toml(root, name, spec), spec.get("path"), name)
    if kind == "lines":
        path = _file(root, name, spec)
        return sum(1 for line in path.read_text().splitlines() if line.strip())
    if kind == "call":
        target = spec.get("call")
        if not isinstance(target, str) or ":" not in target:
            raise FactError(f"fact {name!r}: call must be 'module:function'")
        if target not in calls:
            module_name, function_name = target.split(":", 1)
            module = importlib.import_module(module_name)
            function = getattr(module, function_name)
            if not callable(function):
                raise FactError(f"fact {name!r}: {target} is not callable")
            calls[target] = function()
        value = calls[target]
        return _dig(value, spec.get("key"), name)
    raise FactError(f"fact {name!r}: unknown kind {kind!r}")


def _file(root: Path, name: str, spec: Mapping[str, Any]) -> Path:
    raw = spec.get("file")
    if not isinstance(raw, str):
        raise FactError(f"fact {name!r}: missing file")
    path = root / raw
    if not path.exists():
        raise FactError(f"fact {name!r}: {raw} does not exist")
    return path


def _read_json(root: Path, name: str, spec: Mapping[str, Any]) -> object:
    try:
        return json.loads(_file(root, name, spec).read_text())
    except json.JSONDecodeError as exc:
        raise FactError(f"fact {name!r}: invalid JSON: {exc}") from exc


def _read_toml(root: Path, name: str, spec: Mapping[str, Any]) -> object:
    try:
        return tomllib.loads(_file(root, name, spec).read_text())
    except tomllib.TOMLDecodeError as exc:
        raise FactError(f"fact {name!r}: invalid TOML: {exc}") from exc


def _dig(value: object, path: object, name: str) -> object:
    if path is None:
        return value
    if not isinstance(path, str):
        raise FactError(f"fact {name!r}: path must be a string")
    current = value
    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                raise FactError(f"fact {name!r}: no key {part!r} in {path!r}")
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise FactError(f"fact {name!r}: bad list index {part!r}") from exc
        else:
            raise FactError(f"fact {name!r}: cannot descend into {type(current).__name__}")
    return current


def _display(name: str, spec: Mapping[str, Any], value: object) -> str:
    if "truncate" in spec:
        value = str(value)[: int(spec["truncate"])]
    if "scale" in spec:
        try:
            value = float(value) * float(spec["scale"])  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise FactError(f"fact {name!r}: scale needs a number") from exc
    template = spec.get("format")
    if isinstance(template, str):
        try:
            return template.format(value)
        except (IndexError, KeyError, ValueError) as exc:
            raise FactError(f"fact {name!r}: format {template!r} failed: {exc}") from exc
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def guard_strings(fact: Fact) -> list[str]:
    """Spellings that must not appear literally in prose.

    Short values are skipped: a bare ``33`` or ``50`` appears inside unrelated
    numbers often enough that guarding it would only create false failures.
    """
    if fact.spec.get("guard") is False:
        return []
    candidates = {fact.display}
    value = fact.value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if float(value).is_integer():
            candidates.add(str(int(value)))
            candidates.add(f"{int(value):,}")
    return sorted(s for s in candidates if len(s) >= 3)


def call_function(target: str) -> Callable[[], object]:
    """Import ``module:function`` (used by tests and the CLI)."""
    module_name, function_name = target.split(":", 1)
    function = getattr(importlib.import_module(module_name), function_name)
    if not callable(function):
        raise FactError(f"{target} is not callable")
    return function  # type: ignore[no-any-return]
