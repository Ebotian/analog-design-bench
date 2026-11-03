from __future__ import annotations

import pytest

from adb_solve import netlist as spice
from adb_solve import params

NETLIST = """\
* synthetic design used by the tests
.subckt outer in out vdd vss
XM0 out in vss vss sky130_fd_pr__nfet_01v8 l=0.15 w=1 nf=1
; inline comment with a fake device R99 bad 1 2 3
XR1 out net1 vss sky130_fd_pr__res_high_po w=1 l=2 mult=1
CC1 net1 vss 5p
.subckt inner a b vdd vss
XM1 b a vss vss sky130_fd_pr__nfet_01v8 l=1.0
+ w=2 nf=4
.ends inner
XI0 in out2 vdd vss inner
.ends outer
"""


def test_lossless_render_without_edits():
    doc = spice.parse(NETLIST)
    assert doc.render([]) == NETLIST


def test_statements_comments_and_continuations():
    doc = spice.parse(NETLIST)
    devices = doc.devices()
    names = [device.name for device in devices]
    assert names == ["XM0", "XR1", "CC1", "XM1", "XI0"]
    inner = devices[3]
    assert [token.text for token in inner.tokens] == [
        "XM1", "b", "a", "vss", "vss", "sky130_fd_pr__nfet_01v8",
        "l=1.0", "w=2", "nf=4",
    ]
    assert inner.physical == [7, 8]


def test_subcircuit_scopes():
    doc = spice.parse(NETLIST)
    scopes = {device.name: device.scope for device in doc.devices()}
    assert scopes == {
        "XM0": "outer",
        "XR1": "outer",
        "CC1": "outer",
        "XM1": "outer/inner",
        "XI0": "outer",
    }


def test_assignment_spans_point_at_values_only():
    doc = spice.parse(NETLIST)
    statement = doc.devices()[0]
    pairs = {key: token for key, token in statement.assignments()}
    assert pairs["w"].text == "1"
    body = doc.lines[pairs["w"].line].body
    assert body[pairs["w"].start : pairs["w"].end] == "1"
    assert body[pairs["w"].start - 2 : pairs["w"].start] == "w="


def test_replacement_touches_one_token():
    doc = spice.parse(NETLIST)
    statement = doc.devices()[0]
    token = dict(statement.assignments())["w"]
    text = doc.render([spice.Replacement(token.line, token.start, token.end, "3.5")])
    diff = spice.line_diff(NETLIST, text)
    assert len(diff) == 1
    assert "w=3.5" in diff[0][2]
    assert diff[0][0] == token.line
    assert text.splitlines()[token.line] == diff[0][2]


def test_reject_overlapping_replacements():
    doc = spice.parse(NETLIST)
    with pytest.raises(ValueError):
        doc.render(
            [
                spice.Replacement(5, 0, 3, "AAA"),
                spice.Replacement(5, 2, 4, "BBBB"),
            ]
        )


def test_parameter_extraction_units_and_bounds():
    design = params.extract(spice.parse(NETLIST))
    index = design.by_id()
    # Sky130 PDK subcircuits take w/l in micrometres; SI storage is metres.
    assert index["outer/XM0.w"].value == pytest.approx(1e-6)
    assert index["outer/XM0.l"].value == pytest.approx(0.15e-6)
    assert index["outer/XM0.w"].unit == "m"
    assert index["outer/XM0.nf"].bounds.grid == 1.0
    assert index["outer/XM0.w"].group == "outer:sky130_fd_pr__nfet_01v8"
    assert index["outer/inner/XM1.w"].value == pytest.approx(2e-6)
    assert index["outer/CC1.value"].value == pytest.approx(5e-12)
    assert index["outer/CC1.value"].unit == "F"
    assert index["outer/CC1.value"].writable


def test_width_bounds_follow_finger_count():
    design = params.extract(spice.parse(NETLIST))
    index = design.by_id()
    width = design.bounds_for(index["outer/XM0.w"])
    assert width.minimum == pytest.approx(0.42e-6 * 1)
    assert width.maximum == pytest.approx(7e-6 * 1)


def test_identity_writeback_is_a_no_op():
    design = params.extract(spice.parse(NETLIST))
    values = {param.id: param.value for param in design.writable()}
    assert design.apply(values) == NETLIST


def test_apply_changes_only_the_value_token():
    design = params.extract(spice.parse(NETLIST))
    text = design.apply({"outer/XM0.w": 3e-6, "outer/XM0.nf": 2.0})
    diff = spice.line_diff(NETLIST, text)
    assert len(diff) == 1
    assert "w=3 nf=2" in diff[0][2]


def test_apply_rejects_values_the_checker_calls_illegal():
    """Hard legality = what check_circuit.py rejects without extra flags."""
    design = params.extract(spice.parse(NETLIST))
    with pytest.raises(ValueError):
        design.apply({"outer/XM0.l": 0.0})  # not positive
    with pytest.raises(ValueError):
        design.apply({"outer/XM0.l": -1e-6})  # not positive
    with pytest.raises(ValueError):
        design.apply({"outer/CC1.value": -1e-12})  # not positive
    with pytest.raises(KeyError):
        design.apply({"nope.w": 1.0})


def test_apply_allows_fractional_multiplicity_the_checker_accepts():
    """Measured against check_circuit.py: ``m=0.5`` and ``nf=1.5`` pass.

    Integer grids stay search advice; calling them illegal would refuse
    designs the official gate accepts.
    """
    design = params.extract(spice.parse(NETLIST))
    text = design.apply({"outer/XM0.nf": 1.5})
    assert "nf=1.5" in text


def test_apply_allows_off_model_geometry_the_reference_itself_uses():
    """The modelled ranges are an optional checker flag, not a legality rule.

    Frozen references in the corpus use e.g. ``w=50 m=16``; rejecting that at
    write-back would make the tool unable to restate its own input.
    """
    design = params.extract(spice.parse(NETLIST))
    text = design.apply({"outer/XM0.l": 9e-6, "outer/XM0.w": 50e-6})
    # Digit style follows the original token: l=0.15 keeps two decimals.
    assert "l=9.00 w=50" in text
    assert design.netlist.render([]) == NETLIST


def test_search_box_carries_the_modelled_ranges_separately():
    design = params.extract(spice.parse(NETLIST))
    box = design.search_box()
    assert box["outer/XM0.l"].maximum == pytest.approx(8e-6)
    assert box["outer/XM0.l"].source == "pdk-modeled"
    assert box["outer/XM0.nf"].grid == 1.0
    assert box["outer/XM0.nf"].source == "heuristic"
    assert box["outer/CC1.value"].source == "heuristic"


def test_apply_rejects_non_writable():
    doc = spice.parse(NETLIST + "VDD vdd vss DC 1.8\n")
    design = params.extract(doc)
    source = design.by_id()["VDD.dc"]
    assert not source.writable
    assert source.reason == "source/stimulus"
    with pytest.raises(ValueError):
        design.apply({"VDD.dc": 2.0})


def test_symbolic_param_is_not_writable():
    doc = spice.parse(".subckt a b c\nR1 b c {rval}\n.ends a\n.param rval={base}*2\n")
    design = params.extract(doc)
    index = design.by_id()
    assert not index[".param.rval"].writable
    assert "expression" in index[".param.rval"].reason
