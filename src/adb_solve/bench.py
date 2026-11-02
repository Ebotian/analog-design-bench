"""Bench decks: placeholder rendering and corner discovery.

The public testbenches are templates: the task verifier fills in values such
as ``{supply}``, ``{temperature}`` or ``{cl}``.  The corpus uses four syntaxes
(``{x}``, ``%X%``, ``__X__``, ``@@X@@``); all of them are recognised, and the
deck itself already carries the analysis (``.ac``/``.tran``/``.dc``/``.noise``)
plus an ``.include`` of the design under test.

:func:`render` refuses to guess: a missing value is an error naming every
unfilled placeholder, because silently leaving ``{supply}`` in a deck makes
ngspice fail in a way that looks like a design bug.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import netlist as spice

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
# Percent/dunder/at placeholders are upper-case by convention in the corpus.
# Requiring upper case keeps PDK file names such as
# ``sky130_fd_pr__model__inductors.model.spice`` out of the placeholder set.
_UPPER = r"[A-Z][A-Z0-9_]*"
PLACEHOLDER_STYLES: tuple[str, ...] = (
    rf"\{{(?P<brace>{_NAME})\}}",
    rf"%(?P<percent>{_UPPER})%",
    rf"__(?P<dunder>{_UPPER})__",
    rf"@@(?P<at>{_UPPER})@@",
)
PLACEHOLDER_RE = re.compile("|".join(PLACEHOLDER_STYLES))


def _name_of(match: re.Match[str]) -> str:
    """The placeholder name, whichever syntax matched."""
    return next(value for value in match.groupdict().values() if value is not None)


class MissingValues(KeyError):
    """Raised when a deck placeholder has no value."""

    def __init__(self, names: list[str]):
        self.names = sorted(names)
        super().__init__(", ".join(self.names))


@dataclass(frozen=True)
class Deck:
    """A testbench deck and the values it needs."""

    name: str
    text: str
    path: Path | None = None

    @property
    def placeholders(self) -> list[str]:
        return placeholders(self.text)

    def render(self, values: dict[str, float | int | str]) -> str:
        return render(self.text, values)

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "placeholders": placeholders(self.text),
            "corner": infer_corner(self.text),
            "analyses": analyses(self.text),
        }


def placeholders(text: str) -> list[str]:
    """Sorted unique placeholder names in ``text`` (any supported syntax)."""
    return sorted({_name_of(match) for match in PLACEHOLDER_RE.finditer(text)})


def render(text: str, values: dict[str, float | int | str]) -> str:
    """Fill ``{name}`` placeholders from ``values``.

    Raises :class:`MissingValues` if any placeholder is unfilled.  Values are
    formatted with ``str`` so ``1.8`` and ``"1.8"`` both work; callers that
    care about digits should pre-format.  Lookup is case-insensitive because
    ngspice parameter names are (``{IB}`` is fed by ``ib``).
    """
    lookup = {name.casefold(): value for name, value in values.items()}
    missing = [name for name in placeholders(text) if name.casefold() not in lookup]
    if missing:
        raise MissingValues(missing)
    return PLACEHOLDER_RE.sub(
        lambda match: str(lookup[_name_of(match).casefold()]), text
    )


def analyses(text: str) -> list[str]:
    """Analysis directives present in a deck (``ac``, ``tran`` ...)."""
    found = []
    for line in text.splitlines():
        stripped = line.strip().lower()
        for keyword in (".ac", ".tran", ".dc", ".noise", ".op", ".tf"):
            if stripped.startswith(keyword):
                found.append(keyword[1:])
                break
    return found


def infer_corner(text: str) -> dict[str, object]:
    """Best-effort corner read-out: process, temperature and DC supplies."""
    corner: dict[str, object] = {}
    doc = spice.parse(text)
    for statement in doc.directives():
        keyword = statement.keyword
        words = [token.text for token in statement.positional()]
        if keyword == ".lib":
            all_tokens = [token.text for token in statement.tokens]
            if all_tokens:
                corner["process"] = all_tokens[-1].strip('"')
            if words:
                corner["model"] = words[0].strip('"')
        elif keyword == ".temp" and words:
            corner["temperature"] = words[0]
    supplies: dict[str, str] = {}
    for statement in doc.devices():
        if statement.name[:1].upper() != "V":
            continue
        positional = statement.positional()
        value: str | None = None
        for index, token in enumerate(positional):
            if token.text.upper() == "DC" and index + 1 < len(positional):
                value = positional[index + 1].text
                break
        if value is None and len(positional) >= 3:
            value = positional[-1].text
        if value is not None:
            supplies[statement.name] = value
    if supplies:
        corner["supplies"] = supplies
    return corner


def load(path: str | Path) -> Deck:
    """Read a deck from disk."""
    path = Path(path)
    return Deck(name=path.name, text=path.read_text(), path=path)


def corners_from(
    corner: dict[str, object] | None,
    *,
    process: str | None = None,
    temperature: float | None = None,
    supplies: dict[str, float] | None = None,
) -> dict[str, object]:
    """Merge CLI corner options into a single replacement mapping."""
    merged: dict[str, object] = dict(corner or {})
    if process is not None:
        merged["process"] = process
    if temperature is not None:
        merged["temperature"] = temperature
    for name, value in (supplies or {}).items():
        merged[f"supply_{name}"] = value
        merged[name] = value
    return merged


def rewrite_process(text: str, process: str) -> str:
    """Point every ``.lib ... <corner>`` line at ``process``.

    Sky130 benches select the process corner as the last token of ``.lib``;
    that token is what a process sweep has to move.
    """
    lines = text.splitlines(keepends=True)
    out = []
    for line in lines:
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        stripped = body.strip()
        if stripped.lower().startswith(".lib"):
            tokens = stripped.split()
            if len(tokens) >= 3:
                tokens[-1] = process
                prefix = body[: len(body) - len(body.lstrip())]
                body = prefix + " ".join(tokens)
        out.append(body + ending)
    return "".join(out)


def rewrite_temperature(text: str, temperature: float) -> str:
    """Replace the ``.temp`` value, adding one before ``.end`` if absent."""
    lines = text.splitlines(keepends=True)
    replaced = False
    out = []
    for line in lines:
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        stripped = body.strip().lower()
        if stripped.startswith(".temp"):
            prefix = body[: len(body) - len(body.lstrip())]
            body = f"{prefix}.temp {temperature}"
            replaced = True
        elif not replaced and (stripped == ".end" or stripped.startswith(".end ")):
            out.append(f".temp {temperature}\n")
            replaced = True
        out.append(body + ending)
    if not replaced:
        out.append(f".temp {temperature}\n")
    return "".join(out)
