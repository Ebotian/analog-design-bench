"""Tests for the documentation pipeline: facts, tags, rendering, index, gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adb_docs import cli, facts, render

DOCS_TOML = """\
schema_version = "adb-docs-1"

[[doc]]
id = "page"
title = "Page"
source = "docs/src/page.md"
output = "docs/PAGE.md"
tags = ["kind:goal"]
max_lines = {max_lines}
"""

TAGS_TOML = """\
[namespaces.phase]
values = ["P1"]

[namespaces.status]
values = ["open", "done"]

[namespaces.kind]
values = ["goal", "evidence"]

[namespaces.topic]
free = true
"""

FACT_TOML = """\
[facts.runs]
kind = "json"
file = "data/numbers.json"
path = "runs"
format = "{:,}"
"""


def make_repo(tmp_path: Path, source: str, *, fact_toml: str = FACT_TOML, max_lines: int = 40) -> Path:
    (tmp_path / "docs" / "src").mkdir(parents=True)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "numbers.json").write_text(json.dumps({"runs": 3600}))
    (tmp_path / "docs" / "docs.toml").write_text(DOCS_TOML.format(max_lines=max_lines))
    (tmp_path / "docs" / "tags.toml").write_text(TAGS_TOML)
    (tmp_path / "docs" / "facts.toml").write_text(fact_toml)
    (tmp_path / "docs" / "src" / "page.md").write_text(source)
    return tmp_path


BLOCK = "<!-- block: one | tags: phase:P1,status:open,kind:goal -->"


def test_build_renders_facts_and_index(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        f"# Page\n\n{BLOCK}\n## One\n\nruns = {{{{fact:runs}}}}\n",
    )
    result = render.build(root)
    assert result.problems == ()
    page = result.files["docs/PAGE.md"]
    assert "runs = 3,600" in page
    assert "generated from docs/src/page.md" in page
    blocks = result.index["blocks"]
    assert [b["block"] for b in blocks] == ["page/one"]
    assert blocks[0]["line"] == 4
    assert blocks[0]["end"] == len(page.splitlines())
    assert result.index["facts"]["runs"]["used_in"] == ["page/one"]


def test_unknown_fact_fails(tmp_path: Path) -> None:
    root = make_repo(tmp_path, f"# Page\n\n{BLOCK}\n## One\n\n{{{{fact:nope}}}}\n")
    result = render.build(root)
    assert any("unknown fact 'nope'" in p for p in result.problems)


def test_literal_fact_is_flagged(tmp_path: Path) -> None:
    source = (
        f"# Page\n\n{BLOCK}\n## One\n\n{{{{fact:runs}}}}\n\n"
        "<!-- block: two | tags: kind:evidence -->\n## Two\n\n3,600\n"
    )
    root = make_repo(tmp_path, source)
    result = render.build(root)
    assert any("literal '3,600' should be {{fact:runs}}" in p for p in result.problems)


def test_unused_fact_fails(tmp_path: Path) -> None:
    root = make_repo(tmp_path, f"# Page\n\n{BLOCK}\n## One\n\nno numbers\n")
    result = render.build(root)
    assert any("registered but never used" in p for p in result.problems)


def test_dated_heading_fails(tmp_path: Path) -> None:
    root = make_repo(tmp_path, f"# Page\n\n{BLOCK}\n## 2026-11-02 记录\n\ntext\n")
    result = render.build(root)
    assert any("date-stamped heading" in p for p in result.problems)


def test_inline_dated_note_fails(tmp_path: Path) -> None:
    root = make_repo(tmp_path, f"# Page\n\n{BLOCK}\n## One\n\n（2026-11-03 注：改过）\n")
    result = render.build(root)
    assert any("inline dated amendment" in p for p in result.problems)


def test_line_budget_fails(tmp_path: Path) -> None:
    body = "\n".join(f"- line {n}" for n in range(30))
    root = make_repo(tmp_path, f"# Page\n\n{BLOCK}\n## One\n\n{body}\n", max_lines=10)
    result = render.build(root)
    assert any("budget 10" in p for p in result.problems)


def test_block_needs_heading(tmp_path: Path) -> None:
    root = make_repo(tmp_path, f"# Page\n\n{BLOCK}\n\nplain text\n")
    result = render.build(root)
    assert any("must be followed by a heading" in p for p in result.problems)


def test_unknown_tag_value_fails(tmp_path: Path) -> None:
    marker = "<!-- block: one | tags: phase:P9,kind:goal -->"
    root = make_repo(tmp_path, f"# Page\n\n{marker}\n## One\n\nx\n")
    result = render.build(root)
    assert any("'phase:P9' is not in phase" in p for p in result.problems)


def test_duplicate_block_id_fails(tmp_path: Path) -> None:
    root = make_repo(tmp_path, f"# Page\n\n{BLOCK}\n## One\n\nx\n\n{BLOCK}\n## Two\n\ny\n")
    result = render.build(root)
    assert any("duplicate block id" in p for p in result.problems)


def test_malformed_marker_fails(tmp_path: Path) -> None:
    root = make_repo(tmp_path, "# Page\n\n<!-- block: one tags: kind:goal -->\n## One\n\nx\n")
    result = render.build(root)
    assert any("malformed block marker" in p for p in result.problems)


def test_context_prints_only_matching_block(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = (
        f"# Page\n\n{BLOCK}\n## One\n\nfirst\n\n"
        "<!-- block: two | tags: kind:evidence -->\n## Two\n\nsecond\n"
    )
    root = make_repo(tmp_path, source)
    render.write(render.build(root), root)
    assert cli.cmd_context(["kind:evidence"], root=root) == 0
    out = capsys.readouterr().out
    assert "second" in out and "first" not in out
    assert "docs/PAGE.md:" in out


def test_find_matches_or_and_fields(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = (
        f"# Page\n\n{BLOCK}\n## One\n\na\n\n"
        "<!-- block: two | tags: kind:evidence -->\n## Two\n\nb\n"
    )
    root = make_repo(tmp_path, source)
    render.write(render.build(root), root)
    assert cli.cmd_find(["doc=page", "phase:P1|kind:evidence"], root=root) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 2
    assert cli.cmd_find(["status:done"], root=root) == 1


def test_drift_detects_manual_edit(tmp_path: Path) -> None:
    root = make_repo(tmp_path, f"# Page\n\n{BLOCK}\n## One\n\nx\n")
    result = render.build(root)
    render.write(result, root)
    assert render.drift(result, root) == []
    target = root / "docs" / "PAGE.md"
    target.write_text(target.read_text() + "\nhand edit\n")
    assert any("out of date" in p for p in render.drift(result, root))


def test_guard_strings_skip_short_values() -> None:
    small = facts.Fact(id="x", value=12, display="12", spec={})
    large = facts.Fact(id="y", value=3600, display="3,600", spec={})
    assert facts.guard_strings(small) == []
    assert "3,600" in facts.guard_strings(large)
    assert "3600" in facts.guard_strings(large)


def test_registry_rejects_missing_kind(tmp_path: Path) -> None:
    path = tmp_path / "facts.toml"
    path.write_text('[facts.x]\nfile = "a.json"\n')
    with pytest.raises(facts.FactError, match="no kind"):
        facts.load_registry(path)


def test_repository_docs_are_current() -> None:
    root = Path(__file__).resolve().parents[1]
    result = render.build(root)
    assert result.problems == ()
    assert render.drift(result, root) == []
    assert len(result.index["blocks"]) >= 30
    assert len(result.index["facts"]) >= 40
    assert {doc.output for doc in result.docs} >= {"docs/PLAN.md", "README.md", "DATA.md"}
