"""Verifier SPEC extraction: TOML, Python constants, and point lists."""

from __future__ import annotations

import pytest

from adb_solve import verifier_spec

TOML_SOURCE = '''
SPEC = tomllib.loads(r"""
[pvt]
corners = ["tt", "ff", "ss"]
supply_voltages_v = [1.62, 1.80, 1.98]
temperatures_c = [-40, 27, 125]

[coverage]
startup_corners = ["tt", "ss"]
noise_corners = ["tt"]
""")
'''

POINT_SOURCE = '''
CORNERS = ("tt", "ff")
SUPPLIES = (1.8,)
TEMPERATURES = (27,)
PVT = list(product(CORNERS, SUPPLIES, TEMPERATURES))
'''

CASES_SOURCE = '''
CASES = (
    ("nominal", "tt", 1.8, 27),
    ("cold", "ss", 1.62, -40),
)
'''


def test_reads_toml_pvt_and_coverage():
    spec = verifier_spec.extract(TOML_SOURCE)
    assert spec is not None
    assert spec.source == "toml"
    assert spec.processes == ("tt", "ff", "ss")
    assert spec.supplies == (1.62, 1.8, 1.98)
    assert spec.temperatures == (-40.0, 27.0, 125.0)
    assert spec.coverage["startup_corners"] == ("tt", "ss")
    assert spec.coverage["noise_corners"] == ("tt",)
    assert spec.kind == "cartesian"


def test_reads_python_dimensions_and_product():
    spec = verifier_spec.extract(POINT_SOURCE)
    assert spec is not None
    assert spec.source == "python"
    assert spec.processes == ("tt", "ff")
    assert spec.supplies == (1.8,)
    assert spec.kind == "cartesian"


def test_reads_named_case_tuples():
    spec = verifier_spec.extract(CASES_SOURCE)
    assert spec is not None
    assert spec.processes == ("ss", "tt")
    assert spec.supplies == (1.62, 1.8)
    assert spec.temperatures == (-40.0, 27.0)
    assert spec.kind == "rows"


def test_refuses_unknown_code_instead_of_executing(tmp_path):
    source = "CORNERS = [__import__('os').system('true')]\n"
    assert verifier_spec.extract(source) is None


def test_missing_verifier_returns_none(tmp_path):
    assert verifier_spec.from_tests_dir(tmp_path) is None


def test_from_tests_dir_reads_verify_py(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "verify.py").write_text(CASES_SOURCE)
    spec = verifier_spec.from_tests_dir(tests)
    assert spec is not None
    assert spec.processes == ("ss", "tt")


def test_point_dicts_with_operating_points():
    source = '''
SPEC = tomllib.loads(r"""
[pvt]
corners = ["tt", "ss"]
operating_points = [
  { supply_v = 1.62, temperature_c = -40 },
  { supply_v = 1.80, temperature_c = 27 },
]
""")
'''
    spec = verifier_spec.extract(source)
    assert spec is not None
    assert spec.processes == ("tt", "ss")
    assert spec.supplies == (1.62, 1.8)
    assert spec.temperatures == (-40.0, 27.0)
    assert spec.kind == "pairs"


def test_import_error_is_not_swallowed_as_a_grid():
    with pytest.raises(verifier_spec.SpecError):
        verifier_spec._environment("def broken(:\n")
