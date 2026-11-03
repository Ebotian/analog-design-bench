from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest

from adb_solve import bench, evaluate
from adb_solve.sim import SimCase, SimResult

DECK = """\
.lib "/opt/sky130/continuous/sky130.lib.spice" tt
.include "/app/circuit.spi"
.temp 27
VDD vdd 0 DC 1.8
CLOAD out 0 {cl}
.ac dec 10 10 100Meg
.end
"""


class FakeBackend:
    """Test double: records what it was asked to simulate."""

    def __init__(self, status: str = "ok", measures: dict[str, float] | None = None):
        self.calls: list[list[SimCase]] = []
        self.status = status
        self.measures = measures if measures is not None else {"vout": 1.0}

    def simulate(self, cases: Sequence[SimCase]) -> list[SimResult]:
        self.calls.append(list(cases))
        return [
            SimResult(
                case_id=case.case_id,
                status=self.status,  # type: ignore[arg-type]
                measures=self.measures,
                seconds=0.01,
                returncode=0,
                corner=case.corner,
            )
            for case in cases
        ]


def _options(tmp_path: Path, **overrides: object) -> evaluate.EvalOptions:
    defaults: dict[str, object] = {
        "image": "fake-image",
        "jobs": 1,
        "cache_root": tmp_path / "cache",
        "work_root": tmp_path / "work",
        "use_cache": True,
    }
    defaults.update(overrides)
    return evaluate.EvalOptions(**defaults)  # type: ignore[arg-type]


def test_cases_cover_every_deck_at_every_corner():
    decks = [bench.Deck("tb_a.spi", DECK), bench.Deck("tb_b.spi", DECK)]
    corners = evaluate.corner_matrix(
        processes=["tt", "ss"], temperatures=[27.0, 125.0], values={"cl": "5p"}
    )
    cases = evaluate.build_cases("netlist", decks, corners)
    assert len(cases) == 2 * 4
    assert len({case.case_id for case in cases}) == len(cases)
    assert all("5p" in case.deck_text for case in cases)


def test_corner_options_rewrite_process_and_temperature():
    deck = bench.Deck("tb.spi", DECK)
    corners = evaluate.corner_matrix(processes=["ss"], temperatures=[125.0], values={"cl": "1p"})
    (case,) = evaluate.build_cases("netlist", [deck], corners)
    assert '.lib "/opt/sky130/continuous/sky130.lib.spice" ss' in case.deck_text
    assert ".temp 125" in case.deck_text


def test_missing_deck_values_are_an_error():
    deck = bench.Deck("tb.spi", DECK)
    with pytest.raises(bench.MissingValues):
        evaluate.build_cases("netlist", [deck], [{}])


def test_evaluate_reports_status_and_measures(tmp_path):
    backend = FakeBackend()
    report = evaluate.evaluate(
        "netlist",
        [bench.Deck("tb.spi", bench.render(DECK, {"cl": "5p"}))],
        [{}],
        _options(tmp_path),
        backend,
    )
    assert report["sims"] == 1
    assert report["status_counts"] == {"ok": 1}
    assert report["results"][0]["measures"] == {"vout": 1.0}
    assert report["results"][0]["deck"] == "tb.spi"
    assert len(backend.calls) == 1


def test_second_run_is_served_from_cache(tmp_path):
    deck = bench.Deck("tb.spi", bench.render(DECK, {"cl": "5p"}))
    backend = FakeBackend()
    first = evaluate.evaluate("netlist", [deck], [{}], _options(tmp_path), backend)
    second = evaluate.evaluate("netlist", [deck], [{}], _options(tmp_path), backend)
    assert len(backend.calls) == 1
    assert first["cache"]["hits"] == 0
    assert second["cache"]["hits"] == 1
    assert second["results"] == first["results"]


def test_cache_can_be_disabled(tmp_path):
    deck = bench.Deck("tb.spi", bench.render(DECK, {"cl": "5p"}))
    backend = FakeBackend()
    options = _options(tmp_path, use_cache=False)
    evaluate.evaluate("netlist", [deck], [{}], options, backend)
    evaluate.evaluate("netlist", [deck], [{}], options, backend)
    assert len(backend.calls) == 2


def test_result_order_is_stable(tmp_path):
    decks = [bench.Deck("tb_b.spi", bench.render(DECK, {"cl": "5p"})),
             bench.Deck("tb_a.spi", bench.render(DECK, {"cl": "5p"}))]
    corners = evaluate.corner_matrix(temperatures=[27.0, 125.0])
    report = evaluate.evaluate("netlist", decks, corners, _options(tmp_path), FakeBackend())
    assert [row["case"] for row in report["results"]] == [
        "tb_b.spi[0]", "tb_b.spi[1]", "tb_a.spi[0]", "tb_a.spi[1]",
    ]


def test_log_tail_is_dropped_for_successful_runs(tmp_path):
    report = evaluate.evaluate(
        "netlist",
        [bench.Deck("tb.spi", bench.render(DECK, {"cl": "5p"}))],
        [{}],
        _options(tmp_path),
        FakeBackend(),
    )
    assert "log_tail" not in report["results"][0]


def test_non_ok_results_keep_their_detail(tmp_path):
    backend = FakeBackend(status="nc", measures={})
    report = evaluate.evaluate(
        "netlist",
        [bench.Deck("tb.spi", bench.render(DECK, {"cl": "5p"}))],
        [{}],
        _options(tmp_path),
        backend,
    )
    assert report["status_counts"] == {"nc": 1}


def test_corner_fields_also_fill_deck_placeholders():
    """A deck may use {temperature}; the corner value must reach it."""
    deck = bench.Deck(
        "tb.spi",
        '.temp {temperature}\nVDD vdd 0 DC {supply}\n.include "/app/circuit.spi"\n.end\n',
    )
    corners = evaluate.corner_matrix(
        temperatures=[125.0], values={"supply": 1.62, "temperature": 27.0}
    )
    cases = evaluate.build_cases("netlist", [deck], corners)
    rendered = [case.deck_text for case in cases]
    assert any("DC 1.62" in text for text in rendered)
    assert all(".temp 125" in text for text in rendered)


# --------------------------------------------------------------- P2: budget


def _budget_fixture(tmp_path: Path) -> tuple[list[bench.Deck], list[dict[str, object]]]:
    deck = bench.Deck("tb.spi", DECK)
    corners = evaluate.corner_matrix(temperatures=[27.0, 125.0], values={"cl": "5p"})
    return [deck], [dict(c) for c in corners]


def test_budget_truncates_deterministically_and_reports_skipped(tmp_path):
    decks, corners = _budget_fixture(tmp_path)
    backend = FakeBackend()
    report = evaluate.evaluate("netlist", decks, corners, _options(tmp_path, budget=1), backend)
    assert report["sims"] == 1
    assert report["sims_run"] == 1
    assert report["budget"] == {
        "limit": 1,
        "prior_used": 0,
        "used": 1,
        "returned": 1,
        "cached": 0,
        "exhausted": True,
        "skipped": ["tb.spi[1]"],
    }
    assert len(backend.calls[0]) == 1


def test_zero_budget_runs_nothing(tmp_path):
    decks, corners = _budget_fixture(tmp_path)
    backend = FakeBackend()
    report = evaluate.evaluate("netlist", decks, corners, _options(tmp_path, budget=0), backend)
    assert backend.calls == []
    assert report["results"] == []
    assert report["budget"]["exhausted"] is True
    assert len(report["budget"]["skipped"]) == 2


def test_cache_hits_do_not_consume_budget(tmp_path):
    decks, corners = _budget_fixture(tmp_path)
    evaluate.evaluate("netlist", decks, corners, _options(tmp_path), FakeBackend())
    backend = FakeBackend()
    report = evaluate.evaluate("netlist", decks, corners, _options(tmp_path, budget=0), backend)
    assert backend.calls == []
    assert report["sims"] == 2
    assert report["budget"]["used"] == 0
    assert report["budget"]["exhausted"] is False


def test_resume_spends_only_the_remaining_budget(tmp_path):
    decks, corners = _budget_fixture(tmp_path)
    first = evaluate.evaluate("netlist", decks, corners, _options(tmp_path, budget=1), FakeBackend())
    assert first["budget"]["used"] == 1
    second = evaluate.evaluate(
        "netlist", decks, corners,
        _options(tmp_path, budget=2, prior_used=first["budget"]["used"]), FakeBackend(),
    )
    assert second["sims"] == 2
    assert second["sims_run"] == 1
    assert second["budget"]["limit"] == 2
    assert second["budget"]["prior_used"] == 1
    assert second["budget"]["used"] == 1
    assert second["budget"]["cached"] == 1
    assert second["budget"]["exhausted"] is False


def test_negative_budget_is_rejected(tmp_path):
    decks, corners = _budget_fixture(tmp_path)
    with pytest.raises(ValueError):
        evaluate.evaluate("netlist", decks, corners, _options(tmp_path, budget=-1), FakeBackend())


def test_worker_sets_single_thread_ngspice_and_memory_limit(tmp_path):
    from adb_solve.docker_backend import _Worker

    worker = _Worker("img", tmp_path, 0, 10.0, False, "2g")
    assert (tmp_path / "worker-0" / ".spiceinit").read_text() == "set num_threads=1\n"
    assert worker._limits() == ["--memory", "2g"]
    unlimited = _Worker("img", tmp_path, 1, 10.0, False, None)
    assert unlimited._limits() == []


def test_evaluate_many_shares_one_budget_and_tags_variants(tmp_path):
    deck = bench.Deck("tb.spi", bench.render(DECK, {"cl": "5p"}))
    backend = FakeBackend()
    report = evaluate.evaluate_many(
        {"base": "netlist", "alt": "netlist 2"},
        [deck],
        [{"process": "tt", "temperature": 27.0}],
        _options(tmp_path, budget=1),
        backend,
    )
    assert report["sims"] == 1
    assert report["budget"]["used"] == 1
    assert report["budget"]["exhausted"] is True
    assert len(report["design_hashes"]) == 2
    assert report["results"][0]["variant"] == "base"
    assert report["results"][0]["case"].startswith("base|")


def test_cache_relabels_rows_for_a_new_batch(tmp_path):
    """A cached row belongs to the netlist+deck, not to the batch that first
    wrote it; re-using it under a new label must not lose it."""
    deck = bench.Deck("tb.spi", bench.render(DECK, {"cl": "5p"}))
    first = evaluate.evaluate_many({"base": "netlist"}, [deck], [{}], _options(tmp_path), FakeBackend())
    second = evaluate.evaluate_many({"validate": "netlist"}, [deck], [{}], _options(tmp_path), FakeBackend())
    assert first["results"][0]["case"].startswith("base|")
    assert second["results"][0]["case"].startswith("validate|")
    assert second["results"][0]["variant"] == "validate"
    assert second["cache"]["hits"] == 1
