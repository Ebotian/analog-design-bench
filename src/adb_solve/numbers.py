"""SPICE numeric literals: parse to SI floats, format back in the original style.

ngspice accepts a scale suffix after the mantissa and ignores whatever follows
it, so ``1.8V``, ``10uF`` and ``1kohm`` are all valid.  A literal is kept as
three parts -- mantissa, optional exponent, suffix -- and write-back reuses the
exponent and suffix verbatim, so a value the caller did not touch is
byte-identical and a value the caller did touch keeps its scale.

Precision rule: if the mantissa cannot be written with as many decimals as the
original without changing the number, more decimals are added.  Formatting
never silently rounds a requested value (``5e-12`` scaled by 1.1 becomes
``5.5e-12``, not ``6e-12``).

Only plain literals are handled.  Anything that is not a number (``{gain}``,
``vdd/2``, a parameter name) parses to ``None`` and must be treated as
non-tunable by the caller.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Longest prefixes first: "meg"/"mil" must win over "m".
SCALE_PREFIXES: tuple[tuple[str, float], ...] = (
    ("meg", 1e6),
    ("mil", 25.4e-6),
    ("t", 1e12),
    ("g", 1e9),
    ("k", 1e3),
    ("m", 1e-3),
    ("u", 1e-6),
    ("n", 1e-9),
    ("p", 1e-12),
    ("f", 1e-15),
)

LITERAL_RE = re.compile(
    r"^(?P<mantissa>[+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    r"(?P<exponent>[eE][+-]?\d+)?"
    r"(?P<suffix>[A-Za-z]*)$"
)

# Extra decimal places tried before giving up on the original exponent style.
_MAX_EXTRA_DECIMALS = 12
# Accept a rendering when it is this close, relatively, to the requested value.
_REL_TOL = 1e-12


@dataclass(frozen=True)
class Literal:
    """A parsed SPICE numeric literal."""

    text: str
    mantissa: str
    exponent_text: str
    suffix: str
    scale: float
    decimals: int

    @property
    def exponent(self) -> int:
        return int(self.exponent_text[1:]) if self.exponent_text else 0

    @property
    def scientific(self) -> bool:
        return bool(self.exponent_text)

    @property
    def unit_scale(self) -> float:
        """SI factor applied to the mantissa: suffix prefix × 10**exponent."""
        return self.scale * 10.0**self.exponent

    @property
    def value(self) -> float:
        """The literal's value in SI base units."""
        return float(self.mantissa) * self.unit_scale


def parse_literal(token: str) -> Literal | None:
    """Parse ``token`` as a SPICE numeric literal, or return ``None``."""
    match = LITERAL_RE.match(token.strip())
    if match is None:
        return None
    mantissa = match.group("mantissa")
    suffix = match.group("suffix")
    lower = suffix.lower()
    scale = 1.0
    for prefix, factor in SCALE_PREFIXES:
        if lower.startswith(prefix):
            scale = factor
            break
    decimals = len(mantissa.split(".", 1)[1]) if "." in mantissa else 0
    return Literal(
        text=token,
        mantissa=mantissa,
        exponent_text=match.group("exponent") or "",
        suffix=suffix,
        scale=scale,
        decimals=decimals,
    )


def to_si(token: str) -> float | None:
    """Value of ``token`` in SI base units, or ``None`` if not a literal."""
    literal = parse_literal(token)
    return None if literal is None else literal.value


def _close(rendered: str, value: float) -> bool:
    """True when ``rendered`` still means ``value``."""
    try:
        return math.isclose(float(rendered), value, rel_tol=_REL_TOL, abs_tol=0.0)
    except ValueError:
        return False


def _plain(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def _fallback(value: float, has_exponent: bool) -> str:
    """Last-resort rendering of a mantissa; never carries its own exponent."""
    text = f"{value:.15f}".rstrip("0").rstrip(".")
    if text in {"", "-0"} or (value != 0.0 and float(text) == 0.0):
        text = f"{value:.12g}"
    if has_exponent and "e" in text.lower():
        raise ValueError(f"cannot render {value!r} inside the token's exponent")
    return text


def format_like(token: str, value_si: float) -> str:
    """Format ``value_si`` in the style (exponent and suffix) of ``token``.

    ``format_like("620p", 1e-9)`` gives ``"1000p"``: the suffix is preserved,
    not normalised to ``n``.  ``format_like("5e-12", 5.5e-12)`` gives
    ``"5.5e-12"``: precision is widened instead of rounding to ``6e-12``.
    """
    literal = parse_literal(token)
    if literal is None:
        raise ValueError(f"not a SPICE numeric literal: {token!r}")
    if not math.isfinite(value_si):
        raise ValueError(f"cannot format non-finite value {value_si!r}")
    mantissa_value = value_si / literal.unit_scale
    tail = literal.exponent_text + literal.suffix
    if mantissa_value == 0:
        return _plain(0.0, literal.decimals) + tail
    for extra in range(_MAX_EXTRA_DECIMALS + 1):
        body = _plain(mantissa_value, literal.decimals + extra)
        if _close(body, mantissa_value):
            return body + tail
    return _fallback(mantissa_value, literal.scientific) + tail


def same_value(token: str, value_si: float) -> bool:
    """True when ``token`` already denotes ``value_si`` (within 1e-12 relative)."""
    current = to_si(token)
    if current is None:
        return False
    if current == value_si == 0.0:
        return True
    return math.isclose(current, value_si, rel_tol=_REL_TOL, abs_tol=0.0)
