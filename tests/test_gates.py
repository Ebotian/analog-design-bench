"""Public gate specs → margins → scalar loss."""

from __future__ import annotations

import pytest

from adb_solve import gates

PAYLOAD = {
    "referenceResult": {
        "metrics": [
            {"id": "gain_40db", "specification": "gain 40 dB ≥ 40 dB", "metric": 40},
            {"id": "ugb_100mhz", "specification": "ugb 100 MHz ≥ 100 MHz", "metric": 100},
            {"id": "power_0p5mw", "specification": "power 0.5 mW ≤ 0.5 mW", "metric": 0.5},
            {"id": "noise_50uvrms", "specification": "noise 50 µVrms ≤ 50 µVrms", "metric": 50},
        ]
    }
}

MEASURES = {
    "m0_dc_gain_db",
    "m1_dc_gain_db",
    "m0_ugb_hz",
    "m0_power_w",
    "m0_input_noise_vrms",
}


def gate_set() -> gates.GateSet:
    return gates.from_task(PAYLOAD, MEASURES)


def test_units_and_directions_are_parsed() -> None:
    resolved = {gate.id: gate for gate in gate_set().gates}
    assert set(resolved) == {"gain_40db", "ugb_100mhz", "power_0p5mw", "noise_50uvrms"}
    assert resolved["gain_40db"].measure == "dc_gain_db"
    assert resolved["gain_40db"].threshold == pytest.approx(40.0)
    assert resolved["ugb_100mhz"].measure == "ugb_hz"
    assert resolved["ugb_100mhz"].threshold == pytest.approx(100e6)
    assert resolved["power_0p5mw"].higher_is_better is False
    assert resolved["noise_50uvrms"].threshold == pytest.approx(50e-6)


def test_margins_take_the_worst_case_across_points() -> None:
    rows = [
        {"status": "ok", "measures": {"m0_dc_gain_db": 44.0}},
        {"status": "ok", "measures": {"m1_dc_gain_db": 36.0}},
    ]
    margins = gates.margins(gate_set(), rows)
    # worst gain = 36 dB → (36 - 40) / 40
    assert margins["gain_40db"] == pytest.approx(-0.1)


def test_missing_measurement_is_not_a_pass() -> None:
    margins = gates.margins(gate_set(), [{"status": "ok", "measures": {}}])
    assert all(value is None for value in margins.values())
    assert gates.feasible(margins) is False
    assert gates.loss(margins, 0) > 0


def test_ambiguous_quantity_is_left_unresolved() -> None:
    payload = {"referenceResult": {"metrics": [{"id": "gain_40db", "specification": "gain 40 dB ≥ 40 dB"}]}}
    ambiguous = gates.from_task(payload, {"dc_gain_db", "loop_gain_db"})
    assert ambiguous.gates == ()
    assert ambiguous.unresolved == ("gain_40db",)


def test_loss_penalises_failures_and_keeps_passing_designs_cheap() -> None:
    passing = {gate.id: 0.2 for gate in gate_set().gates}
    assert gates.feasible(passing)
    base = gates.loss(passing, 0)
    assert 0.0 < base < 1.0  # softplus(-m/tau) never reaches zero
    assert gates.loss(passing, 3, lambda_fail=2.0) - base == pytest.approx(6.0)


def test_violated_margins_cost_more_than_passing_ones() -> None:
    failing = {gate.id: -0.2 for gate in gate_set().gates}
    passing = {gate.id: 0.2 for gate in gate_set().gates}
    assert gates.loss(failing, 0) > gates.loss(passing, 0)


def test_regularization_enters_the_loss() -> None:
    passing = {gate.id: 0.2 for gate in gate_set().gates}
    base = gates.loss(passing, 0)
    assert gates.loss(passing, 0, regularization=1.0, lambda_reg=0.5) - base == pytest.approx(0.5)
