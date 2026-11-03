"""OAT screening: corner choice, ranking, mode selection, budget."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from adb_solve import evaluate, screen
from adb_solve.sim import SimCase, SimResult

NETLIST = """\
.subckt amp vss vdd in out
XM1 out in vss vss sky130_fd_pr__nfet_01v8 w=1 l=0.5 m=1
XM2 out in vdd vdd sky130_fd_pr__pfet_01v8 w=2 l=0.5 m=1
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

CORNERS = [
    {"process": "tt", "temperature": 27.0, "supply": 1.8},
    {"process": "ss", "temperature": 125.0, "supply": 1.62},
    {"process": "ff", "temperature": -40.0, "supply": 1.98},
    {"process": "ss", "temperature": -40.0, "supply": 1.62},
]

WIDTH = re.compile(r"XM1\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+w=([0-9.eE+-]+)")


class ScreenFake:
    """Only XM1.w moves ``gain``; everything else is inert."""

    def __init__(self) -> None:
        self.calls = 0

    def simulate(self, cases: Sequence[SimCase]) -> list[SimResult]:
        self.calls += 1
        results = []
        for case in cases:
            match = WIDTH.search(case.netlist)
            width = float(match.group(1)) if match else 1.0
            results.append(
                SimResult(
                    case_id=case.case_id,
                    status="ok",
                    measures={"gain": width, "offset": 0.5},
                    seconds=0.01,
                    returncode=0,
                    corner=case.corner,
                )
            )
        return results


def _options(tmp_path: Path) -> evaluate.EvalOptions:
    return evaluate.EvalOptions(
        image="fake-image",
        jobs=1,
        cache_root=tmp_path / "cache",
        work_root=tmp_path / "work",
        use_cache=True,
    )


def test_nominal_is_tt_27_median_supply() -> None:
    assert screen.nominal_index(CORNERS) == 0


def test_stress_corners_are_spread_not_just_far() -> None:
    """Greedy farthest-point: opposite process and opposite temperature."""
    assert screen.stress_indices(CORNERS, 0, 2) == [1, 2]


def test_full_oat_ranks_the_only_sensitive_parameter(tmp_path: Path) -> None:
    report = screen.screen(
        NETLIST,
        [__import__("adb_solve.bench", fromlist=["Deck"]).Deck("tb.spi", DECK)],
        CORNERS,
        {"delta": 0.2, "corners": 3, "max_dims": 3},
        ScreenFake(),
        _options(tmp_path),
    )
    assert report["mode"] == "per-param"
    assert report["params"] == 6
    assert report["dims"][0] == "amp/XM1.w"
    top = report["ranking"][0]
    assert top["param"] == "amp/XM1.w"
    assert float(top["score"]) > 0.1
    assert all(float(row["score"]) == 0.0 for row in report["ranking"][1:])
    # 3 corners × (1 baseline + 6 params × 2 signs) cases
    assert report["sims"] == 3 * 13
    assert report["budget"]["used"] == 3 * 13


def test_small_budget_switches_to_coarse_then_param(tmp_path: Path) -> None:
    report = screen.screen(
        NETLIST,
        [__import__("adb_solve.bench", fromlist=["Deck"]).Deck("tb.spi", DECK)],
        CORNERS,
        {"delta": 0.2, "corners": 3, "max_dims": 3, "budget": 30, "top_groups": 2},
        ScreenFake(),
        _options(tmp_path),
    )
    assert report["mode"] == "coarse-then-param"
    assert report["groups"]
    assert report["ranking"]
    assert report["dims"]
    assert int(report["budget"]["used"]) <= 30


def test_budget_exhaustion_is_reported(tmp_path: Path) -> None:
    report = screen.screen(
        NETLIST,
        [__import__("adb_solve.bench", fromlist=["Deck"]).Deck("tb.spi", DECK)],
        CORNERS,
        {"corners": 3, "budget": 4},
        ScreenFake(),
        _options(tmp_path),
    )
    assert report["budget"]["exhausted"] is True
    assert int(report["budget"]["used"]) <= 4
    assert report["dims"] == []


def test_invalid_delta_is_rejected(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError):
        screen.screen(
            NETLIST,
            [__import__("adb_solve.bench", fromlist=["Deck"]).Deck("tb.spi", DECK)],
            CORNERS,
            {"delta": 1.5},
            ScreenFake(),
            _options(tmp_path),
        )
