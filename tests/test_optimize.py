"""DE sizing loop: repair, budget, and reaching a feasible point."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

import pytest

from adb_solve import bench, evaluate, optimize, params
from adb_solve.sim import SimCase, SimResult

NETLIST = """\
.subckt amp vss vdd in out
XM1 out in vss vss sky130_fd_pr__nfet_01v8 w=0.7 l=0.5 m=1
.ends
"""

DECK = """\
.lib "/opt/sky130/sky130.lib.spice" tt
.include "/app/circuit.spi"
.temp 27
VDD vdd 0 DC 1.8
.op
.end
"""

CORNERS = [{"process": "tt", "temperature": 27.0, "supply": 1.8}]

PAYLOAD = {
    "referenceResult": {
        "metrics": [{"id": "gain_40db", "specification": "gain 40 dB ≥ 40 dB", "metric": 40}]
    }
}

WIDTH = re.compile(r"w=([0-9.eE+-]+)")


class GainFake:
    """Input noise: gain_db = 40 * (w / 0.7); the gate needs w >= 0.7."""

    def simulate(self, cases: Sequence[SimCase]) -> list[SimResult]:
        out = []
        for case in cases:
            match = WIDTH.search(case.netlist)
            width = float(match.group(1)) if match else 0.7
            out.append(
                SimResult(
                    case_id=case.case_id,
                    status="ok",
                    measures={"m0_dc_gain_db": 40.0 * width / 0.7},
                    seconds=0.01,
                    returncode=0,
                    corner=case.corner,
                )
            )
        return out


def _options(tmp_path: Path) -> evaluate.EvalOptions:
    return evaluate.EvalOptions(
        image="fake-image", cache_root=tmp_path / "cache", work_root=tmp_path / "work"
    )


def _deck() -> list[bench.Deck]:
    return [bench.Deck("tb.spi", DECK)]


def test_repair_clamps_into_the_search_box() -> None:
    design = params.load_text(NETLIST)
    reference = {"amp/XM1.w": 0.7e-6}
    repaired = optimize.repair(design, {"amp/XM1.w": -1.0}, reference)
    assert repaired["amp/XM1.w"] == pytest.approx(0.42e-6)  # modelled minimum, not negative


def test_repair_restores_the_reference_when_clamping_cannot_legalise() -> None:
    design = params.load_text(NETLIST)
    reference = {"amp/XM1.w": 0.7e-6}
    repaired = optimize.repair(design, {"amp/XM1.w": float("nan")}, reference)
    assert repaired["amp/XM1.w"] == 0.7e-6


def test_solve_reaches_a_feasible_point_and_reports_sims(tmp_path: Path) -> None:
    report = optimize.solve(
        NETLIST,
        _deck(),
        CORNERS,
        PAYLOAD,
        {"dims": ["amp/XM1.w"], "population": 8, "generations": 12, "seed": 3, "budget": 200},
        GainFake(),
        _options(tmp_path),
    )
    assert report["best"]["feasible"] is True
    assert report["sims_to_feasible"] is not None
    assert report["sims_to_feasible"] <= 200
    assert report["dims"] == ["amp/XM1.w"]
    assert report["gates"]["gates"][0]["measure"] == "dc_gain_db"
    assert report["best"]["margins"]["gain_40db"] >= 0


def test_solve_degrades_gracefully_when_the_budget_is_tiny(tmp_path: Path) -> None:
    report = optimize.solve(
        NETLIST,
        _deck(),
        CORNERS,
        PAYLOAD,
        {"dims": ["amp/XM1.w"], "population": 8, "budget": 3, "seed": 1},
        GainFake(),
        _options(tmp_path),
    )
    assert report["budget"]["exhausted"] is True
    assert report["sims"] <= 3
    assert report["best"]["feasible"] in {True, False}


def test_solve_rejects_unknown_dimensions(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        optimize.solve(
            NETLIST, _deck(), CORNERS, PAYLOAD, {"dims": ["nope"]}, GainFake(), _options(tmp_path)
        )


def test_solve_requires_a_resolvable_gate(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        optimize.solve(
            NETLIST, _deck(), CORNERS, {}, {"dims": ["amp/XM1.w"]}, GainFake(), _options(tmp_path)
        )


CORNERS_TWO = [
    {"process": "tt", "temperature": 27.0, "supply": 1.8},
    {"process": "ss", "temperature": 125.0, "supply": 1.62},
]


class CornerGainFake:
    """gain drops 2x at the hot corner; only w can compensate."""

    def simulate(self, cases: Sequence[SimCase]) -> list[SimResult]:
        out = []
        for case in cases:
            match = WIDTH.search(case.netlist)
            width = float(match.group(1)) if match else 0.7
            hot = float(case.corner.get("temperature", 27.0)) > 100
            gain = 40.0 * width / 0.7 * (0.5 if hot else 1.0)
            out.append(
                SimResult(
                    case_id=case.case_id,
                    status="ok",
                    measures={"m0_dc_gain_db": gain},
                    seconds=0.01,
                    returncode=0,
                    corner=case.corner,
                )
            )
        return out


def test_validation_corners_are_fed_back_and_the_loop_closes(tmp_path: Path) -> None:
    """Search on the cold corner passes; the hot corner only appears in
    validation.  The cutter must feed it back and re-optimise, not accept."""
    report = optimize.solve(
        NETLIST,
        _deck(),
        [CORNERS_TWO[0]],
        PAYLOAD,
        {
            "dims": ["amp/XM1.w"], "population": 10, "generations": 40, "seed": 5,
            "budget": 1600, "rounds": 3, "feed_corners": 1, "bound": 0.8,
        },
        CornerGainFake(),
        _options(tmp_path),
        validation_corners=CORNERS_TWO,
    )
    assert len(report["validation_rounds"]) >= 2
    assert any("fed" in note for note in report["notes"])
    assert report["validation"]["feasible"] is True
    assert report["validation"]["margins"]["gain_40db"] >= 0


def test_validation_that_passes_immediately_stops_after_one_round(tmp_path: Path) -> None:
    report = optimize.solve(
        NETLIST,
        _deck(),
        CORNERS_TWO,
        PAYLOAD,
        {"dims": ["amp/XM1.w"], "population": 8, "generations": 6, "seed": 2, "budget": 400, "bound": 0.8},
        GainFake(),
        _options(tmp_path),
        validation_corners=CORNERS_TWO,
    )
    assert len(report["validation_rounds"]) == 1
    assert report["validation_rounds"][0]["feasible"] in {True, False}


TWO_GATES = {
    "referenceResult": {
        "metrics": [
            {"id": "gain_40db", "specification": "gain 40 dB ≥ 40 dB"},
            {"id": "cmrr_50db", "specification": "cmrr 50 dB ≥ 50 dB"},
        ]
    }
}


def test_worst_corner_prefers_the_violated_gate() -> None:
    """Corner X holds the worst gain (but gain passes); corner Y violates CMRR.
    Y must come first, or the cutter adds a corner that decides nothing."""
    from adb_solve import gates

    corner_x = {"process": "tt", "temperature": 27.0}
    corner_y = {"process": "ss", "temperature": 125.0}
    report = {
        "results": [
            {"variant": "v", "corner": corner_x, "status": "ok",
             "measures": {"m0_dc_gain_db": 41.0, "m0_cmrr_db_1khz": 60.0}},
            {"variant": "v", "corner": corner_y, "status": "ok",
             "measures": {"m0_dc_gain_db": 48.0, "m0_cmrr_db_1khz": 45.0}},
        ]
    }
    gate_set = gates.from_task(TWO_GATES, {"m0_dc_gain_db", "m0_cmrr_db_1khz"})
    worst = optimize._worst_corners(report, gate_set, 1, [corner_x, corner_y])
    assert worst == [corner_y]


def test_matching_pairs_finds_input_pair_and_mirror_but_not_biased_mirror() -> None:
    from adb_solve import params as param_lib

    netlist = """\
.subckt amp vss ibias vdd vinn vinp vout
XM1 nleft vinp tail vss sky130_fd_pr__nfet_01v8 l=1 w=96 nf=12
XM2 vout vinn tail vss sky130_fd_pr__nfet_01v8 l=1 w=96 nf=12
XM3 nleft nleft vdd vdd sky130_fd_pr__pfet_01v8 l=1 w=64 nf=8
XM4 vout nleft vdd vdd sky130_fd_pr__pfet_01v8 l=1 w=64 nf=8
XM5 tail ibias vss vss sky130_fd_pr__nfet_01v8 l=3 w=200 nf=20
XM6 ibias ibias vss vss sky130_fd_pr__nfet_01v8 l=3 w=60 nf=6
.ends
"""
    design = param_lib.load_text(netlist)
    pairs = param_lib.matching_pairs(design)
    assert ("amp/XM1", "amp/XM2") in pairs
    assert ("amp/XM3", "amp/XM4") in pairs
    assert not any("XM5" in first or "XM5" in second for first, second in pairs)


def test_symmetry_seed_is_symmetric(tmp_path: Path) -> None:
    netlist = """\
.subckt amp vss ibias vdd vinn vinp vout
XM1 nleft vinp tail vss sky130_fd_pr__nfet_01v8 l=0.8 w=80 nf=15
XM2 vout vinn tail vss sky130_fd_pr__nfet_01v8 l=1.2 w=96 nf=12
XM3 nleft nleft vdd vdd sky130_fd_pr__pfet_01v8 l=1 w=64 nf=8
XM4 vout nleft vdd vdd sky130_fd_pr__pfet_01v8 l=1 w=64 nf=8
.ends
"""
    report = optimize.solve(
        netlist,
        _deck(),
        CORNERS,
        PAYLOAD,
        {"dims": ["amp/XM1.w", "amp/XM2.w"], "population": 6, "generations": 2, "seed": 1, "budget": 40},
        GainFake(),
        _options(tmp_path),
    )
    values = report["best"]["values"]
    assert any("paired parameter" in note for note in report["notes"])
    # Paired dimensions collapse to their mean, so the pair stays matched.
    assert values["amp/XM1.w"] == values["amp/XM2.w"]
