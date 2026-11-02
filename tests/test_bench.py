from __future__ import annotations

import pytest

from adb_solve import bench
from adb_solve import sim

DECK = """\
* nominal AC deck
.lib "/opt/sky130/continuous/sky130.lib.spice" tt
.include "/app/circuit.spi"
.options reltol=1e-4
.temp 27
VSS vss 0 0
VDD vdd vss DC 1.8 AC 1
CLOAD out vss {cl}
.ac dec 40 10 100Meg
.end
"""


def test_placeholders_are_discovered():
    assert bench.placeholders(DECK) == ["cl"]


def test_render_replaces_and_reports_missing():
    rendered = bench.render(DECK, {"cl": "5p"})
    assert "CLOAD out vss 5p" in rendered
    with pytest.raises(bench.MissingValues) as excinfo:
        bench.render(DECK, {})
    assert excinfo.value.names == ["cl"]


def test_infer_corner_reads_lib_temp_and_supplies():
    corner = bench.infer_corner(DECK)
    assert corner["process"] == "tt"
    assert corner["temperature"] == "27"
    assert corner["supplies"] == {"VSS": "0", "VDD": "1.8"}


def test_rewrite_process_and_temperature():
    assert '.lib "/opt/sky130/continuous/sky130.lib.spice" ss' in bench.rewrite_process(DECK, "ss")
    assert ".temp 125" in bench.rewrite_temperature(DECK, 125)
    with_temp = bench.rewrite_temperature(".end\n", -40)
    assert with_temp == ".temp -40\n.end\n"
    existing = bench.rewrite_temperature(".temp 27\n.end\n", 0)
    assert existing == ".temp 0\n.end\n"


def test_measures_parsing_ignores_noise():
    output = """\
vref_min = 1.222638e+00
vref_max              =  1.240000e+00
vout_avg            =  1.50000e+00 from=  1.00000e-03 to=  1.00028e-02
Doing analysis at TEMP = 27.000000 and TNOM = 27.000000
No. of Data Rows : 40
error: measure vref_pp failed
"""
    measures = sim.parse_measures(output)
    assert measures["vref_min"] == pytest.approx(1.222638)
    assert measures["vref_max"] == pytest.approx(1.24)
    assert measures["vout_avg"] == pytest.approx(1.5)
    assert "data" not in measures
    assert "vref_pp" not in measures


def test_classify():
    assert sim.classify(0, "", {"v": 1.0}) == "ok"
    assert sim.classify(0, "", {}) == "missing"
    assert sim.classify(124, "", {}) == "timeout"
    assert sim.classify(1, "Warning: singular matrix", {}) == "nc"
    assert sim.classify(1, "fatal: something else", {}) == "error"


MIXED = """\
.option seed=%SEED%
.lib {model} __CORNER__
.temp @@TEMP@@
VDD vdd 0 DC {supply}
"""


def test_all_placeholder_styles_in_the_corpus_are_recognised():
    """The corpus uses four syntaxes: {x}, %X%, __X__ and @@X@@."""
    assert bench.placeholders(MIXED) == ["CORNER", "SEED", "TEMP", "model", "supply"]


def test_render_fills_every_style_in_one_pass():
    rendered = bench.render(
        MIXED,
        {"SEED": 1234, "model": '"tt.lib"', "CORNER": "tt", "TEMP": 125, "supply": 1.8},
    )
    assert "seed=1234" in rendered
    assert '.lib "tt.lib" tt' in rendered
    assert ".temp 125" in rendered
    assert "DC 1.8" in rendered
    assert not bench.placeholders(rendered)


def test_missing_values_lists_every_unfilled_name():
    with pytest.raises(bench.MissingValues) as excinfo:
        bench.render(MIXED, {"SEED": 1})
    assert excinfo.value.names == ["CORNER", "TEMP", "model", "supply"]


def test_pdk_file_names_are_not_mistaken_for_placeholders():
    """Regression: ``__model__`` inside a .include path is part of the name."""
    deck = (
        '.include "/opt/sky130/pdk/sky130A/libs.tech/ngspice/'
        'sky130_fd_pr__model__inductors.model.spice"\n'
        ".temp @@TEMP@@\n"
    )
    assert bench.placeholders(deck) == ["TEMP"]
    assert bench.render(deck, {"TEMP": 27}).endswith(".temp 27\n")


def test_value_lookup_is_case_insensitive():
    """ngspice parameter names are case-insensitive, so {IB} must find ib."""
    deck = ".param IB=20u\nIREF vdd iref {IB}\n.end\n"
    assert bench.render(deck, {"ib": "20u"}).splitlines()[1] == "IREF vdd iref 20u"
