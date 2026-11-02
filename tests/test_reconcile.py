"""Value extraction contract for the T0 reconciliation.

These cases guard against matching a fixed corner label or array element
instead of the metric printed by the verifier.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "reconcile_verifiers", ROOT / "scripts" / "reconcile_verifiers.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


reconcile = load_script()


def test_corner_index_does_not_beat_a_unit_matched_zero():
    message = (
        "range=-0.000286639dB at ss/1.62V/+125C/VCM=0.81V/S=0 .. "
        "0dB at tt/1.80V/+40C/VCM=0.9V/S=2 (required -0.001..0.001dB)"
    )
    value, source = reconcile.select_value(message, 0.0, "dB")
    assert value == 0.0
    assert source.endswith("0dB")


def test_missing_label_does_not_beat_a_unit_matched_zero():
    message = "|DNL|=0.000 LSB monotonic=True missing=0"
    value, source = reconcile.select_value(message, 0.0, "LSB")
    assert value == 0.0
    assert source.endswith("0.000 LSB")


def test_zero_count_uses_explicit_error_count():
    gate = {"name": "transfer", "status": "passed", "message": "errors=0 missing=0"}
    reference = {
        "id": "transfer",
        "source_check": "transfer",
        "specification": "transfer = 0 errors",
        "value": 0,
        "status": "pass",
    }
    row = reconcile.compare_gate(gate, reference)
    assert row["current_value"] == 0.0
    assert row["current_value_source"] == "explicit-count:errors=0"
    assert row["value_match"] is True


def test_zero_count_without_a_printed_count_uses_gate_status():
    gate = {
        "name": "sample_hold_16_codes",
        "status": "passed",
        "message": "transfer=[0, 11, 4, 15]; transfer_alt=[15, 0, 8, 7]",
    }
    reference = {
        "id": "code_mismatches",
        "source_check": "sample_hold_16_codes",
        "specification": "code mismatches = 0",
        "value": 0,
        "status": "pass",
    }
    row = reconcile.compare_gate(gate, reference)
    assert row["current_value"] == 0.0
    assert row["current_value_source"] == "gate-status:passed"
    assert row["value_match_basis"] == "gate-status"


def test_zero_count_gate_failure_is_not_silently_zero():
    gate = {"name": "sample_hold_16_codes", "status": "failed", "message": "transfer=[]"}
    reference = {
        "id": "code_mismatches",
        "source_check": "sample_hold_16_codes",
        "specification": "code mismatches = 0",
        "value": 0,
        "status": "pass",
    }
    row = reconcile.compare_gate(gate, reference)
    assert row["current_value"] is None
    assert row["value_match"] is False
    assert row["current_value_source"] == "gate-status:failed-unquantified"
