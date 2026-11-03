"""Legality guards and task-declared checker policy."""

from __future__ import annotations

import pytest

from adb_solve import check as checker
from adb_solve import legality, params

MOS = """\
.subckt amp vss vdd in out
XM1 out in vss vss sky130_fd_pr__nfet_01v8 w=1 l=0.5 m={m}
XM2 out in vdd vdd sky130_fd_pr__pfet_01v8 w=2 l=0.5 m=1
R1 out vss 10k
.ends
"""


def design(m: str = "1") -> params.Design:
    return params.load_text(MOS.format(m=m), path="cell.spi")


def test_audit_accepts_a_legal_cell() -> None:
    verdict = legality.audit(design())
    assert verdict.ok
    assert verdict.stages == ("values",)


def test_audit_rejects_zero_multiplicity() -> None:
    verdict = legality.audit(design(m="0"))
    assert not verdict.ok
    assert any("positive" in v.reason for v in verdict.violations)


def test_precheck_reports_unknown_and_unwritable() -> None:
    verdict = legality.precheck(design(), {"nope": 1.0, "R1.value": 2e4})
    reasons = {v.reason for v in verdict.violations}
    assert any("unknown parameter" in r for r in reasons)
    assert all(v.stage == "structure" for v in verdict.violations)


def test_precheck_rejects_negative_width() -> None:
    verdict = legality.precheck(design(), {"amp/XM1.w": -1e-6})
    assert not verdict.ok
    assert verdict.violations[0].stage == "values"


def test_precheck_allows_a_write_that_lands_on_the_same_literal() -> None:
    """A frozen reference may be outside the modelled range; rewriting it to
    itself proposes nothing, so it must not be refused."""
    broken = design(m="0")
    verdict = legality.precheck(broken, {"amp/XM1.m": 0.0})
    assert verdict.ok


def test_guard_does_not_call_the_checker_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("checker must not run for --legality precheck")

    monkeypatch.setattr(checker, "check_text", explode)
    verdict = legality.guard(design().text, design(), {"amp/XM1.w": 1.2e-6}, checker=False)
    assert verdict.ok
    assert verdict.stages == ("values",)


def test_guard_runs_the_checker_and_merges_violations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        checker,
        "check_text",
        lambda *a, **k: checker.CheckResult(ok=False, returncode=1, violations=["bad line"]),
    )
    verdict = legality.guard(design().text, design(), {"amp/XM1.w": 1.2e-6}, checker=True)
    assert not verdict.ok
    assert verdict.stages == ("values", "checker")
    assert verdict.violations[-1].reason == "bad line"


def test_guard_skips_the_checker_when_values_already_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("a refused candidate must not cost a container")

    monkeypatch.setattr(checker, "check_text", explode)
    verdict = legality.guard(design().text, design(), {"amp/XM1.w": -1e-6}, checker=True)
    assert not verdict.ok
    assert verdict.stages == ("values",)


def test_task_policy_reads_allow_ideal_and_notes_preprocessing() -> None:
    ahuja = legality.task_policy("sky130-ahuja-compensated-two-stage-load-range-pvt")
    assert ahuja.allow_ideal == ("R", "C")
    assert not ahuja.preprocessed
    assert ahuja.source.endswith("task.toml")

    bootstrap = legality.task_policy("sky130-bootstrap-driver-pt")
    assert bootstrap.preprocessed  # check_area.py lives in its own image


def test_task_policy_is_empty_for_unknown_tasks() -> None:
    policy = legality.task_policy("no-such-task")
    assert policy.allow_ideal == ()
    assert policy.source == ""


def test_verdict_json_is_stable() -> None:
    verdict = legality.precheck(design(), {"amp/XM1.w": -1e-6})
    payload = verdict.to_json()
    assert payload["ok"] is False
    assert isinstance(payload["violations"], list)
    assert payload["stages"] == ["values"]
    assert "w:" in verdict.summary()
