"""Render tagged markdown sources into the committed documents.

Two annotations are machine-readable; everything else is ordinary markdown:

    <!-- doc: plan | title: 阶段计划 | tags: doc:plan,kind:goal -->

is not used here (doc metadata lives in ``docs/docs.toml``); inside a source:

    <!-- block: p1 | tags: phase:P1,status:done,kind:goal -->

starts a tagged block, and ``{{fact:corpus.runs}}`` prints a measured value.

Checks run on every build and are what keeps the documents honest:

* placeholders must resolve, and every registered fact must be used;
* a fact's rendered spelling must not also appear as a literal in prose;
* tags must come from the closed vocabulary in ``docs/tags.toml``;
* block ids must be unique, and every block must start with a heading;
* no date-stamped headings or inline dated amendments (history belongs to git);
* each document must stay within its line budget.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import facts as fact_lib

ROOT = Path(__file__).resolve().parents[2]
INDEX = "docs/index.json"

PLACEHOLDER = re.compile(r"\{\{fact:([a-z0-9][a-z0-9_.-]*)\}\}")
BLOCK = re.compile(r"^<!-- block: ([a-z0-9][a-z0-9_/-]*) \| tags: ([A-Za-z0-9_:,.-]+) -->$")
DATE_HEADING = re.compile(r"^#{1,6}[ \t].*\b\d{4}-\d{2}-\d{2}\b")
DATED_NOTE = re.compile(r"[（(]\s*\d{4}-\d{2}-\d{2}\s*注")
HEADING = re.compile(r"^#{1,6}[ \t]")


class RenderError(ValueError):
    """The document set is malformed."""


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    output: str
    tags: tuple[str, ...]
    max_lines: int
    source: str | None = None
    generated_by: str | None = None


@dataclass
class _Draft:
    block: str
    line: int
    tags: tuple[str, ...]
    facts: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Block:
    id: str
    doc: str
    path: str
    line: int
    end: int
    tags: tuple[str, ...]
    heading: str
    facts: tuple[str, ...]


@dataclass(frozen=True)
class Build:
    docs: tuple[Doc, ...]
    files: Mapping[str, str]
    index: dict[str, Any]
    problems: tuple[str, ...]
    facts: Mapping[str, fact_lib.Fact]


def load_docs(path: Path | None = None) -> tuple[Doc, ...]:
    source = path if path is not None else ROOT / "docs" / "docs.toml"
    raw = tomllib.loads(source.read_text())
    entries = raw.get("doc")
    if not isinstance(entries, list):
        raise RenderError(f"{source}: missing [[doc]] entries")
    docs: list[Doc] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise RenderError(f"{source}: [[doc]] must be a table")
        docs.append(
            Doc(
                id=str(entry["id"]),
                title=str(entry["title"]),
                output=str(entry["output"]),
                tags=tuple(str(t) for t in entry.get("tags", [])),
                max_lines=int(entry.get("max_lines", 0)),
                source=str(entry["source"]) if "source" in entry else None,
                generated_by=str(entry["generated_by"]) if "generated_by" in entry else None,
            )
        )
    return tuple(docs)


def load_tags(path: Path | None = None) -> dict[str, set[str] | None]:
    source = path if path is not None else ROOT / "docs" / "tags.toml"
    raw = tomllib.loads(source.read_text())
    namespaces = raw.get("namespaces")
    if not isinstance(namespaces, dict):
        raise RenderError(f"{source}: missing [namespaces]")
    out: dict[str, set[str] | None] = {}
    for name, spec in namespaces.items():
        if not isinstance(spec, dict):
            raise RenderError(f"{source}: namespace {name!r} must be a table")
        if spec.get("free") is True:
            out[str(name)] = None
        else:
            out[str(name)] = {str(v) for v in spec.get("values", [])}
    return out


def validate_tags(tags: Iterable[str], namespaces: Mapping[str, set[str] | None], where: str) -> list[str]:
    problems: list[str] = []
    for tag in tags:
        if ":" not in tag:
            problems.append(f"{where}: tag {tag!r} must be namespace:value")
            continue
        namespace, value = tag.split(":", 1)
        if namespace not in namespaces:
            problems.append(f"{where}: unknown tag namespace {namespace!r}")
            continue
        allowed_values = namespaces[namespace]
        if allowed_values is not None and value not in allowed_values:
            allowed = ", ".join(sorted(allowed_values))
            problems.append(f"{where}: {tag!r} is not in {namespace} ({allowed})")
    return problems


def build(root: Path = ROOT, docs_path: Path | None = None, tags_path: Path | None = None) -> Build:
    docs = load_docs(docs_path if docs_path is not None else root / "docs" / "docs.toml")
    namespaces = load_tags(tags_path if tags_path is not None else root / "docs" / "tags.toml")
    fact_map = fact_lib.resolve(root)
    problems: list[str] = []
    files: dict[str, str] = {}
    drafts: dict[str, list[_Draft]] = {}
    used: dict[str, set[str]] = {name: set() for name in fact_map}

    for doc in docs:
        problems.extend(validate_tags(doc.tags, namespaces, f"{doc.id} (doc tags)"))
        problems.extend(_check_output(doc, root))
        if doc.generated_by is not None:
            path = root / doc.output
            if not path.exists():
                problems.append(f"{doc.id}: generated output {doc.output} is missing")
                continue
            text = path.read_text()
            lines = len(text.splitlines())
            if doc.max_lines and lines > doc.max_lines:
                problems.append(f"{doc.id}: {doc.output} is {lines} lines, budget {doc.max_lines}")
            files[doc.output] = text
            drafts[doc.id] = []
            continue
        if doc.source is None:
            problems.append(f"{doc.id}: neither source nor generated_by is set")
            continue
        text, doc_drafts, doc_problems = render_doc(doc, fact_map, namespaces, root)
        problems.extend(doc_problems)
        files[doc.output] = text
        drafts[doc.id] = doc_drafts
        for draft in doc_drafts:
            for name in draft.facts:
                used[name].add(draft.block)

    for name, where in used.items():
        if not where:
            problems.append(f"fact {name!r} is registered but never used; delete it or use it")

    blocks, block_problems = _finish_blocks(docs, drafts, files)
    problems.extend(block_problems)
    index = _index(docs, blocks, fact_map, used, files)
    files[INDEX] = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    return Build(docs=docs, files=files, index=index, problems=tuple(problems), facts=fact_map)


def _check_output(doc: Doc, root: Path) -> list[str]:
    target = (root / doc.output).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return [f"{doc.id}: output {doc.output} escapes the repository"]
    if doc.output == INDEX:
        return [f"{doc.id}: output collides with the index"]
    return []


def render_doc(
    doc: Doc,
    fact_map: Mapping[str, fact_lib.Fact],
    namespaces: Mapping[str, set[str] | None],
    root: Path = ROOT,
) -> tuple[str, list[_Draft], list[str]]:
    assert doc.source is not None
    source_path = root / doc.source
    if not source_path.exists():
        return "", [], [f"{doc.id}: source {doc.source} is missing"]
    lines = source_path.read_text().splitlines()
    problems: list[str] = []
    drafts: list[_Draft] = []
    out: list[str] = [f"<!-- generated from {doc.source} by scripts/build_docs.py; do not edit -->"]
    sentinels: list[str] = [""]
    seen: set[str] = set()
    current: _Draft | None = None

    for lineno, line in enumerate(lines, start=1):
        where = f"{doc.source}:{lineno}"
        if line.startswith("<!-- block:"):
            match = BLOCK.match(line)
            if match is None:
                problems.append(f"{where}: malformed block marker (expected '<!-- block: id | tags: a:b,c:d -->')")
            else:
                block_id, raw_tags = match.group(1), match.group(2)
                tags = tuple(t.strip() for t in raw_tags.split(",") if t.strip())
                if block_id in seen:
                    problems.append(f"{where}: duplicate block id {block_id!r}")
                seen.add(block_id)
                if not tags:
                    problems.append(f"{where}: block {block_id!r} has no tags")
                problems.extend(validate_tags(tags, namespaces, where))
                current = _Draft(block=f"{doc.id}/{block_id}", line=len(out) + 1, tags=tags)
                drafts.append(current)
        elif DATE_HEADING.match(line):
            problems.append(f"{where}: date-stamped heading; history belongs to git")
        elif DATED_NOTE.search(line):
            problems.append(f"{where}: inline dated amendment; state the current fact instead")

        rendered, sentinel = _substitute(line, fact_map, current, where, problems)
        out.append(rendered)
        sentinels.append(sentinel)

    problems.extend(_literal_problems(doc.source, sentinels, fact_map))
    problems.extend(_heading_problems(doc, drafts, out))
    if doc.max_lines and len(out) > doc.max_lines:
        problems.append(f"{doc.id}: rendered {len(out)} lines, budget {doc.max_lines}")
    return "\n".join(out) + "\n", drafts, problems


def _substitute(
    line: str,
    fact_map: Mapping[str, fact_lib.Fact],
    current: _Draft | None,
    where: str,
    problems: list[str],
) -> tuple[str, str]:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        fact = fact_map.get(name)
        if fact is None:
            problems.append(f"{where}: unknown fact {name!r}")
            return match.group(0)
        if current is not None:
            current.facts.add(name)
        return fact.display

    rendered = PLACEHOLDER.sub(replace, line)
    sentinel = PLACEHOLDER.sub(lambda m: "\x00" + m.group(1) + "\x00", line)
    return rendered, sentinel


def _literal_problems(
    source: str,
    sentinels: Sequence[str],
    fact_map: Mapping[str, fact_lib.Fact],
) -> list[str]:
    guards = {name: fact_lib.guard_strings(fact) for name, fact in fact_map.items()}
    problems: list[str] = []
    for index, line in enumerate(sentinels):
        if not line:
            continue
        for name, spellings in guards.items():
            for spelling in spellings:
                # Digit boundaries: "39.8" inside "39.88" is a different number.
                pattern = re.compile(r"(?<![\d.])" + re.escape(spelling) + r"(?!\d)")
                if pattern.search(line):
                    problems.append(
                        f"{source}:{index}: literal {spelling!r} should be {{{{fact:{name}}}}}"
                    )
    return problems


def _heading_problems(doc: Doc, drafts: Sequence[_Draft], out: Sequence[str]) -> list[str]:
    problems: list[str] = []
    for draft in drafts:
        following = out[draft.line] if draft.line < len(out) else ""
        if not HEADING.match(following):
            problems.append(f"{doc.source}: block {draft.block!r} must be followed by a heading")
    return problems


def _finish_blocks(
    docs: Sequence[Doc],
    drafts: Mapping[str, Sequence[_Draft]],
    files: Mapping[str, str],
) -> tuple[list[Block], list[str]]:
    blocks: list[Block] = []
    problems: list[str] = []
    for doc in docs:
        doc_drafts = sorted(drafts.get(doc.id, []), key=lambda d: d.line)
        lines = files.get(doc.output, "").splitlines()
        for position, draft in enumerate(doc_drafts):
            end = doc_drafts[position + 1].line - 1 if position + 1 < len(doc_drafts) else len(lines)
            heading = lines[draft.line].strip() if draft.line < len(lines) else ""
            if not HEADING.match(heading):
                heading = ""
            blocks.append(
                Block(
                    id=draft.block,
                    doc=doc.id,
                    path=doc.output,
                    line=draft.line,
                    end=end,
                    tags=draft.tags,
                    heading=heading.lstrip("# ").strip(),
                    facts=tuple(sorted(draft.facts)),
                )
            )
        by_id = [b.id for b in blocks if b.doc == doc.id]
        if len(by_id) != len(set(by_id)):
            problems.append(f"{doc.id}: duplicate block ids in the index")
    return blocks, problems


def _index(
    docs: Sequence[Doc],
    blocks: Sequence[Block],
    fact_map: Mapping[str, fact_lib.Fact],
    used: Mapping[str, set[str]],
    files: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "schema": 1,
        "generator": "scripts/build_docs.py",
        "docs": [
            {
                "id": doc.id,
                "title": doc.title,
                "path": doc.output,
                "source": doc.source,
                "generated_by": doc.generated_by,
                "tags": list(doc.tags),
                "lines": len(files.get(doc.output, "").splitlines()),
            }
            for doc in docs
        ],
        "blocks": [
            {
                "block": block.id,
                "doc": block.doc,
                "path": block.path,
                "line": block.line,
                "end": block.end,
                "tags": list(block.tags),
                "heading": block.heading,
                "facts": list(block.facts),
            }
            for block in blocks
        ],
        "facts": {
            name: {
                "display": fact.display,
                "value": fact.value,
                "extract": dict(fact.spec),
                "used_in": sorted(used.get(name, ())),
            }
            for name, fact in sorted(fact_map.items())
            if used.get(name)
        },
    }


def drift(result: Build, root: Path = ROOT) -> list[str]:
    """Outputs that differ from what the sources would render."""
    problems: list[str] = []
    for relative, text in result.files.items():
        path = root / relative
        if not path.exists():
            problems.append(f"{relative}: missing (run `make docs`)")
        elif path.read_text() != text:
            problems.append(f"{relative}: out of date (run `make docs`)")
    return problems


def write(result: Build, root: Path = ROOT) -> list[str]:
    written: list[str] = []
    for relative, text in result.files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(relative)
    return sorted(written)
