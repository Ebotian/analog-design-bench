"""Lossless SPICE netlist reader.

The parser keeps the original text byte-for-byte: every statement remembers
which physical lines it covers and where each token sits, so write-back can
replace a single numeric token and leave the rest of the file untouched.

Supported syntax is what the Sky130 bench netlists actually use:

* ``*`` full-line comments, ``;``/``$`` inline comments (same rule the
  official ``check_circuit.py`` uses);
* ``+`` continuation lines;
* ``.subckt``/``.ends`` nesting (a scope path such as ``sar/nand``);
* dot directives (``.param``, ``.lib``, ``.temp``, ``.control`` ...);
* device lines with positional and ``key=value`` parameters.

Directives are kept but not interpreted; :mod:`adb_solve.params` decides what
is a tunable number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

TOKEN_RE = re.compile(r"\S+")
COMMENT_CHARS = (";", "$")


@dataclass(frozen=True)
class Token:
    """A whitespace-delimited token and its position inside one physical line."""

    text: str
    line: int
    start: int
    end: int


@dataclass(frozen=True)
class PhysicalLine:
    index: int
    raw: str
    body: str
    code: str
    kind: str  # "blank" | "comment" | "code"
    is_continuation: bool


@dataclass
class Statement:
    """A logical line: a physical line plus any continuations of it."""

    kind: str  # "directive" | "device"
    name: str  # first token, original case
    tokens: list[Token]
    physical: list[int]
    scope: str  # "" at top level, "outer/inner" inside nested subcircuits
    line: int

    @property
    def keyword(self) -> str:
        """Lower-cased first token (``".subckt"``, ``"xm0"`` ...)."""
        return self.name.lower()

    def assignments(self) -> list[tuple[str, Token]]:
        """``key=value`` pairs in order, as ``(lower_key, value_token)``."""
        pairs: list[tuple[str, Token]] = []
        for token in self.tokens[1:]:
            if "=" not in token.text:
                continue
            key, _, _value = token.text.partition("=")
            if not key:
                continue
            # The value token points at the value only, so write-back can
            # replace it without touching "key=".
            offset = token.start + len(key) + 1
            pairs.append(
                (
                    key.lower(),
                    Token(
                        text=token.text[len(key) + 1 :],
                        line=token.line,
                        start=offset,
                        end=token.end,
                    ),
                )
            )
        return pairs

    def positional(self) -> list[Token]:
        """Tokens that are neither ``key=value`` nor the leading name."""
        return [t for t in self.tokens[1:] if "=" not in t.text]


@dataclass(frozen=True)
class Replacement:
    """Replace one token (by physical span) with new text."""

    line: int
    start: int
    end: int
    text: str


@dataclass
class Netlist:
    path: Path | None
    text: str
    lines: list[PhysicalLine]
    statements: list[Statement] = field(default_factory=list)

    # -- rendering ---------------------------------------------------------
    def render(self, replacements: list[Replacement]) -> str:
        """Return the netlist text with every replacement applied."""
        bodies = [line.body for line in self.lines]
        endings = [_ending(line.raw, line.body) for line in self.lines]
        by_line: dict[int, list[Replacement]] = {}
        for repl in replacements:
            if repl.line < 0 or repl.line >= len(self.lines):
                raise IndexError(f"line {repl.line} outside netlist")
            by_line.setdefault(repl.line, []).append(repl)
        for index, repls in by_line.items():
            spans = sorted(repls, key=lambda r: r.start)
            previous_end = -1
            for repl in spans:
                if repl.start < previous_end:
                    raise ValueError(f"overlapping replacements on line {index}")
                if repl.start > len(bodies[index]) or repl.end > len(bodies[index]):
                    raise ValueError(f"replacement span outside line {index}")
                previous_end = repl.end
            for repl in sorted(repls, key=lambda r: r.start, reverse=True):
                bodies[index] = bodies[index][: repl.start] + repl.text + bodies[index][repl.end :]
        return "".join(body + ending for body, ending in zip(bodies, endings))

    # -- queries -----------------------------------------------------------
    def devices(self) -> list[Statement]:
        return [s for s in self.statements if s.kind == "device"]

    def directives(self, keyword: str | None = None) -> list[Statement]:
        result = [s for s in self.statements if s.kind == "directive"]
        if keyword is not None:
            lowered = keyword.lower()
            result = [s for s in result if s.keyword == lowered]
        return result

    def subcircuit_definitions(self) -> list[Statement]:
        return self.directives(".subckt")


def _ending(raw: str, body: str) -> str:
    """The line terminator of ``raw`` (``"\\n"``, ``"\\r\\n"`` or ``""``)."""
    return raw[len(body) :]


def _split_inline_comment(body: str) -> str:
    cut = len(body)
    for char in COMMENT_CHARS:
        index = body.find(char)
        if index != -1:
            cut = min(cut, index)
    return body[:cut]


def _tokenize(code: str, line: int) -> list[Token]:
    return [
        Token(text=match.group(0), line=line, start=match.start(), end=match.end())
        for match in TOKEN_RE.finditer(code)
    ]


def parse(text: str, path: str | Path | None = None) -> Netlist:
    """Parse ``text`` into a :class:`Netlist`."""
    raw_lines = text.splitlines(keepends=True)
    # splitlines() drops the terminator of a trailing line without newline,
    # which is what we want; an empty file yields no lines.
    lines: list[PhysicalLine] = []
    for index, raw in enumerate(raw_lines):
        body = raw.split("\n", 1)[0].rstrip("\r")
        stripped = body.strip()
        if not stripped:
            lines.append(PhysicalLine(index, raw, body, "", "blank", False))
            continue
        if stripped.startswith("*"):
            lines.append(PhysicalLine(index, raw, body, "", "comment", False))
            continue
        code = _split_inline_comment(body)
        is_continuation = code.lstrip().startswith("+")
        lines.append(
            PhysicalLine(index, raw, body, code, "code", is_continuation)
        )

    statements: list[Statement] = []
    for index, line in enumerate(lines):
        if line.kind != "code":
            continue
        if line.is_continuation and statements:
            previous = statements[-1]
            tokens = _tokenize(line.code, index)
            if tokens and tokens[0].text.startswith("+"):
                # The leading '+' is the continuation marker, not content.
                first = tokens[0]
                rest = first.text[1:]
                tokens = tokens[1:]
                if rest:
                    tokens.insert(
                        0,
                        Token(text=rest, line=first.line, start=first.start + 1, end=first.end),
                    )
            previous.tokens.extend(tokens)
            previous.physical.append(index)
            continue
        tokens = _tokenize(line.code, index)
        if not tokens:
            continue
        head = tokens[0].text
        kind = "directive" if head.startswith(".") else "device"
        statements.append(
            Statement(
                kind=kind,
                name=head,
                tokens=tokens,
                physical=[index],
                scope="",
                line=index,
            )
        )

    _assign_scopes(statements)
    return Netlist(path=Path(path) if path is not None else None, text=text, lines=lines, statements=statements)


def _assign_scopes(statements: list[Statement]) -> None:
    """Fill ``Statement.scope`` by walking ``.subckt``/``.ends`` in order."""
    stack: list[str] = []
    for statement in statements:
        keyword = statement.keyword
        if keyword == ".subckt":
            statement.scope = "/".join(stack)
            name = statement.tokens[1].text if len(statement.tokens) > 1 else ""
            stack.append(name)
        elif keyword == ".ends":
            statement.scope = "/".join(stack)
            if stack:
                stack.pop()
        else:
            statement.scope = "/".join(stack)


def read(path: str | Path) -> Netlist:
    """Read and parse ``path``."""
    path = Path(path)
    return parse(path.read_text(), path=path)


def line_diff(before: str, after: str) -> list[tuple[int, str, str]]:
    """Line indices that differ, as ``(index, before_line, after_line)``."""
    old = before.splitlines()
    new = after.splitlines()
    changes = []
    for index in range(max(len(old), len(new))):
        a = old[index] if index < len(old) else ""
        b = new[index] if index < len(new) else ""
        if a != b:
            changes.append((index, a, b))
    return changes
