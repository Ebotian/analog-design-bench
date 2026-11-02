from __future__ import annotations

import math

import pytest

from adb_solve import numbers


def test_parses_suffixes_and_ignores_trailing_letters():
    assert numbers.to_si("1.8") == 1.8
    assert numbers.to_si("1.8V") == 1.8
    assert numbers.to_si("10uF") == pytest.approx(1e-5)
    assert numbers.to_si("1kohm") == pytest.approx(1e3)
    assert numbers.to_si("2meg") == pytest.approx(2e6)
    assert numbers.to_si("5mil") == pytest.approx(5 * 25.4e-6)
    assert numbers.to_si("-0.5m") == pytest.approx(-0.5e-3)
    assert numbers.to_si("1e-3") == pytest.approx(1e-3)
    assert numbers.to_si(".5p") == pytest.approx(0.5e-12)


def test_non_literals_are_rejected():
    for token in ("{gain}", "vdd/2", "w", "1.0n+2p", "", "nan", "inf"):
        assert numbers.parse_literal(token) is None, token


def test_identity_formatting_reproduces_the_token():
    for token in ("1.0", "72", "0.5", "620p", "1e-3", "1.8V", "10uF", "2meg", "-0.5m", "0"):
        literal = numbers.parse_literal(token)
        assert literal is not None
        assert numbers.format_like(token, literal.value) == token
        assert numbers.same_value(token, literal.value)


def test_format_keeps_the_original_suffix():
    assert numbers.format_like("620p", 1e-9) == "1000p"
    assert numbers.format_like("1k", 2500.0) == "2.5k"
    assert numbers.format_like("1.0", 1.25) == "1.25"
    assert numbers.format_like("72", 144.0) == "144"


def test_format_avoids_precision_loss():
    value = 1.23456789e-9
    text = numbers.format_like("1n", value)
    assert math.isclose(numbers.to_si(text), value, rel_tol=1e-9)


@pytest.mark.parametrize(
    ("raw", "value_si", "expected"),
    [
        ("5e-12", 5.5e-12, "5.5e-12"),
        ("40e-15", 4e-14, "40e-15"),
        ("40e-15", 4.4e-14, "44e-15"),
        ("1.0", 1.0000001, "1.0000001"),
        ("620p", 6.2e-10, "620p"),
        ("2.5u", 2.25e-6, "2.25u"),
    ],
)
def test_format_keeps_the_requested_value(raw, value_si, expected):
    """Formatting must widen precision rather than round the number away."""
    assert numbers.format_like(raw, value_si) == expected
    assert numbers.to_si(expected) == pytest.approx(value_si, rel=1e-9, abs=0)


def test_identity_holds_for_unnormalised_scientific_tokens():
    for token in ("40e-15", "1.5e-3", "40E-15", "5e-12"):
        literal = numbers.parse_literal(token)
        assert literal is not None
        assert numbers.format_like(token, literal.value) == token


@pytest.mark.parametrize("raw", ["5e-12", "40e-15", "1.0", "620p", "72", "2.5u", "1.8V"])
@pytest.mark.parametrize("factor", [0.5, 1.1, 1.37, 2.0, 10.0])
def test_write_back_never_changes_the_value_it_was_given(raw, factor):
    """The write-back invariant: what the caller asks for is what ngspice sees."""
    literal = numbers.parse_literal(raw)
    assert literal is not None
    wanted = literal.value * factor
    written = numbers.format_like(raw, wanted)
    read_back = numbers.to_si(written)
    assert read_back == pytest.approx(wanted, rel=1e-9, abs=0), written
