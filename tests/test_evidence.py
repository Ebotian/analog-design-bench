"""Corner-grid extraction from task text.

These tests pin the shapes the corpus actually uses, including the ones that
must *not* be promoted to a full grid (paired stress points, single nominal
mentions) -- over-claiming provenance is worse than declaring a gap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adb_solve import evidence

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "data" / "tasks"


def grid_of(text: str) -> list[dict]:
    return evidence.instruction_grids(text)


def test_cartesian_slash_style():
    text = (
        "The AC conditions each run all 45 combinations of tt/ff/ss/fs/sf, "
        "1.62/1.80/1.98 V, and -40/27/125 C."
    )
    (grid, *rest) = grid_of(text)
    assert rest == []
    assert sorted(grid["processes"]) == ["ff", "fs", "sf", "ss", "tt"]
    assert grid["supplies"] == [1.62, 1.8, 1.98]
    assert grid["temperatures"] == [-40.0, 27.0, 125.0]


def test_per_value_unit_style_with_semicolons():
    """`tt, ff, ss; 1.62 V, 1.80 V, 1.98 V; and -40 C, 27 C, 125 C` is one grid."""
    text = (
        "All requirements apply at every Cartesian combination of tt, ff, ss; "
        "1.62 V, 1.80 V, 1.98 V; and -40 C, 27 C, 125 C."
    )
    (grid, *rest) = grid_of(text)
    assert rest == []
    assert grid["supplies"] == [1.62, 1.8, 1.98]
    assert grid["temperatures"] == [-40.0, 27.0, 125.0]


def test_row_style():
    text = (
        "- `tt`, 1.80 V, 40 C; - `ss`, 1.62 V, 125 C; "
        "- `ff`, 1.98 V, -10 C; - `sf`, 1.62 V, -10 C."
    )
    (grid, *rest) = grid_of(text)
    assert grid["kind"] == "rows"
    assert sorted(grid["processes"]) == ["ff", "sf", "ss", "tt"]
    assert grid["supplies"] == [1.62, 1.8, 1.98]
    assert rest == []


def test_nominal_mention_is_not_a_grid():
    assert grid_of("The nominal target is 40 uA at TT/1.8 V/27 C.") == []
    assert grid_of("Operate at a 1.8 V supply and 0.9 V input common mode.") == []


def test_paired_stresses_are_kept_but_marked_as_pairs():
    text = (
        "Dynamic signoff uses the paired low/hot, nominal, and high/cold stresses: "
        "1.62 V/125 C, 1.8 V/27 C, and 1.98 V/-40 C."
    )
    (grid, *rest) = grid_of(text)
    assert grid["kind"] == "pairs"
    assert rest == []
    assert grid["supplies"] == [1.62, 1.8, 1.98]
    assert grid["temperatures"] == [-40.0, 27.0, 125.0]


def test_stated_point_count_breaks_ties():
    """`This is 81 points` confirms the 9x3x3 grid and must win over subsets."""
    payload = json.loads((TASKS / "sky130-bandgap-reference-pvt.json").read_text())
    grid = grid_of(payload["instruction"])[0]
    assert len(grid["processes"]) * len(grid["supplies"]) * len(grid["temperatures"]) == 81
    assert "81 points" in grid["quote"]


def test_mc_statistics_are_not_swept_as_process_corners():
    text = "Signoff covers tt/ff/ss across 1.62/1.8/1.98 V and -40/27/125 C."
    (grid, *_rest) = grid_of(text)
    assert "mc" not in grid["processes"]


def test_deck_evidence_prefers_the_dc_level_of_the_rail():
    task = {
        "files": {
            "tests": [
                {
                    "path": "tb.spi",
                    "content": (
                        '.lib "/opt/sky130/continuous/sky130.lib.spice" ss\n'
                        ".temp 125\n"
                        "VSS vss 0 0\n"
                        "VDD vdd vss 1.62 AC 1\n"
                        "VIN vin 0 DC 0.9\n"
                    ),
                }
            ],
            "solution": [],
        }
    }
    found = evidence.deck_evidence(task)
    assert found["processes"] == ["ss"]
    assert found["temperatures"] == [125.0]
    assert 1.62 in found["supplies"]
    assert 0.9 in found["supplies"]


def test_build_entry_flags_what_it_cannot_prove():
    task = {
        "id": "demo",
        "instruction": "Do the thing well.",
        "files": {
            "tests": [{"path": "tb.spi", "content": "CLOAD out 0 {mystery}\n.end\n"}],
            "solution": [],
        },
    }
    entry = evidence.build_entry(task)
    assert entry.provenance == "assumption"
    assert entry.needs_review
    assert entry.unresolved == ("mystery",)
