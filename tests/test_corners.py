"""Contract for the corner matrix as a versioned, declarative asset.

The matrix is IaC: ``configs/corners.toml`` is the single source of truth, it
must cover every task in the corpus, every entry must say where it came from,
and the declared values must be enough to render every deck of that task.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adb_solve import bench, corners

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "corners.toml"
TASKS = ROOT / "data" / "tasks"


@pytest.fixture(scope="module")
def declaration() -> corners.Corners:
    return corners.load(CONFIG)


@pytest.fixture(scope="module")
def corpus() -> dict[str, dict]:
    return {
        path.stem: json.loads(path.read_text())
        for path in sorted(TASKS.glob("*.json"))
    }


# -- schema -----------------------------------------------------------------
def test_load_rejects_unknown_keys(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text('schema_version = "adb-corners-1"\n[tasks.a]\nprocesses = ["tt"]\n')
    with pytest.raises(corners.CornersError) as excinfo:
        corners.load(path)
    assert "processes" not in str(excinfo.value)  # names the missing field, not the present one
    assert "supplies" in str(excinfo.value)


def test_load_reports_every_problem_at_once(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text(
        'schema_version = "adb-corners-1"\n'
        "[tasks.a]\n"
        'provenance = "guess"\n'
        'quote = ""\n'
        "processes = []\n"
        "supplies = [-1.0]\n"
        "temperatures = []\n"
        "[tasks.b]\n"
        'provenance = "deck"\n'
        "processes = [\"tt\"]\n"
        "supplies = [1.8]\n"
        "temperatures = []\n"
    )
    with pytest.raises(corners.CornersError) as excinfo:
        corners.load(path)
    message = str(excinfo.value)
    assert "tasks.a" in message and "tasks.b" in message
    assert "provenance" in message and "supplies" in message


def test_expand_is_deterministic_and_cartesian(tmp_path):
    entry = corners.TaskCorners(
        task_id="t",
        provenance="instruction",
        quote="q",
        processes=("tt", "ss"),
        supplies=(1.62, 1.8),
        temperatures=(-40.0, 27.0),
        values={},
        needs_review=False,
    )
    first = corners.expand(entry)
    second = corners.expand(entry)
    assert first == second
    assert len(first) == 2 * 2 * 2
    assert first[0]["process"] == "tt" and first[1]["temperature"] == 27.0
    assert first[0]["supply"] == 1.62


def test_expand_injects_declared_values_and_supply_key():
    entry = corners.TaskCorners(
        task_id="t",
        provenance="deck",
        quote="",
        processes=("tt",),
        supplies=(1.8,),
        temperatures=(27.0,),
        values={"cl": "5p", "vcm": 0.9},
        keys=corners.SweepKeys(supply="VDD"),
        needs_review=True,
    )
    (corner,) = corners.expand(entry)
    assert corner == {"VDD": 1.8, "temperature": 27.0, "process": "tt", "cl": "5p", "vcm": 0.9}


def test_expand_refuses_absurd_grids():
    entry = corners.TaskCorners(
        task_id="t",
        provenance="assumption",
        quote="",
        processes=tuple(f"p{i}" for i in range(20)),
        supplies=tuple(1.0 + i / 10 for i in range(20)),
        temperatures=tuple(float(i) for i in range(20)),
        values={},
        needs_review=True,
    )
    with pytest.raises(corners.CornersError):
        corners.expand(entry, max_cases=100)


# -- the committed asset ----------------------------------------------------
def test_declaration_covers_exactly_the_corpus(declaration, corpus):
    assert set(declaration.tasks) == set(corpus)


def test_every_entry_declares_its_evidence(declaration):
    for task_id, entry in declaration.tasks.items():
        assert entry.provenance in {"instruction", "deck", "assumption", "verifier"}, task_id
        if entry.provenance in {"instruction", "verifier"}:
            assert entry.quote, f"{task_id}: sourced entries need a quote"
        assert entry.processes and entry.supplies and entry.temperatures, task_id
        assert all(value > 0 for value in entry.supplies), task_id


def test_review_debt_is_declared_not_hidden(declaration):
    flagged = {task_id for task_id, entry in declaration.tasks.items() if entry.needs_review}
    assert declaration.meta.needs_review == len(flagged)
    assert flagged <= set(declaration.tasks)


def test_declared_values_render_every_deck_of_its_task(declaration, corpus):
    """The asset must be sufficient input for evaluation, not just paperwork.

    Either a task renders completely, or the only names it cannot render are
    exactly the ones it declared as unresolved -- declared debt can never hide
    an undeclared gap.
    """
    for task_id, entry in declaration.tasks.items():
        corner = corners.expand(entry)[0]
        blocked: set[str] = set()
        for item in corpus[task_id]["files"]["tests"]:
            deck = bench.Deck(item["path"].split("/")[-1], item["content"])
            try:
                deck.render(corner)
            except bench.MissingValues as exc:
                blocked |= set(exc.names)
        assert blocked == set(entry.unresolved), (
            f"{task_id}: declared {sorted(entry.unresolved)} but decks block on {sorted(blocked)}"
        )


def test_kind_says_how_exhaustive_the_grid_is(declaration):
    for task_id, entry in declaration.tasks.items():
        assert entry.kind in {"cartesian", "rows", "pairs", "none"}, task_id
        if entry.kind != "cartesian":
            assert entry.needs_review, f"{task_id}: a {entry.kind} grid cannot be exhaustive"
        if entry.provenance == "instruction":
            assert entry.kind != "none", task_id
        elif entry.provenance != "verifier":
            assert entry.kind == "none", task_id


def test_asset_drives_case_generation(declaration, corpus):
    """The declared grid must be directly consumable by the evaluator."""
    from adb_solve import evaluate

    task_id = "sky130-lc-vco-2ghz-pvt"
    entry = declaration.tasks[task_id]
    payload = corpus[task_id]
    decks = [
        bench.Deck(item["path"].split("/")[-1], item["content"])
        for item in payload["files"]["tests"]
    ]
    matrix = corners.expand(entry)
    cases = evaluate.build_cases(
        payload["files"]["solution"][0]["content"], decks, matrix, entry.keys
    )
    assert len(cases) == len(matrix) * len(decks)
    assert {case.corner[entry.keys.process] for case in cases} == set(entry.processes)
    assert all(case.deck_text for case in cases)


def test_dumps_round_trips_through_load(tmp_path):
    """The asset format is part of the contract: write then read must agree."""
    entries = [
        corners.TaskCorners(
            task_id="b-task",
            provenance="instruction",
            quote='all 27 combinations, "quoted" \\ text',
            processes=("ff", "tt"),
            supplies=(1.62, 1.8),
            temperatures=(-40.0, 27.0),
            values={"cl": "5p", "vcm": 0.9},
            keys=corners.SweepKeys(process="CORNER", supply="VDD"),
            unresolved=("SEED",),
            needs_review=True,
            kind="pairs",
        ),
        corners.TaskCorners(
            task_id="a-task",
            provenance="deck",
            quote="",
            processes=("tt",),
            supplies=(1.8,),
            temperatures=(27.0,),
        ),
    ]
    path = tmp_path / "corners.toml"
    corners.write(path, entries)
    loaded = corners.load(path)
    assert loaded.tasks == {entry.task_id: entry for entry in entries}
    assert list(loaded.tasks) == ["a-task", "b-task"]  # sorted on write


def test_verifier_entries_carry_coverage_subsets(declaration):
    """A [coverage] table in the verifier SPEC survives into the asset."""
    entry = declaration.tasks["sky130-bandgap-reference-pvt"]
    assert entry.provenance == "verifier"
    assert entry.coverage["noise_corners"] == ("tt",)
    assert set(entry.coverage["startup_corners"]) == {"tt", "ss", "ff"}
    for name, corners in entry.coverage.items():
        assert name and corners, (entry.task_id, name)
