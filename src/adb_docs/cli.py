"""Query the documentation index instead of grepping the documents.

The index maps tags to blocks with file and line numbers, so an agent can load
one section (``context``) rather than a whole file.  Run
``python3 -m adb_docs --help`` for the command list.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import render

ROOT = Path(__file__).resolve().parents[2]


def load_index(root: Path = ROOT) -> dict[str, Any]:
    path = root / render.INDEX
    if not path.exists():
        raise SystemExit(f"{path} is missing; run `make docs`")
    raw: Any = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise SystemExit(f"{path} is not a JSON object")
    return raw


def _blocks(index: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: Any = index.get("blocks", [])
    if not isinstance(blocks, list):
        raise SystemExit("docs/index.json: 'blocks' must be a list")
    return [b for b in blocks if isinstance(b, dict)]


def matches(block: dict[str, Any], expression: Sequence[str]) -> bool:
    """AND over tokens; ``a|b`` is OR within one token; ``id=``/``doc=`` select."""
    tags = set(str(t) for t in block.get("tags", []))
    for token in expression:
        if token.startswith("id="):
            if str(block.get("block")) != token[3:]:
                return False
        elif token.startswith("doc="):
            if str(block.get("doc")) != token[4:]:
                return False
        elif not any(alternative in tags for alternative in token.split("|")):
            return False
    return True


def cmd_find(expression: Sequence[str], root: Path = ROOT) -> int:
    found = [b for b in _blocks(load_index(root)) if matches(b, expression)]
    for block in found:
        print(
            f"{block.get('path')}:{block.get('line')}-{block.get('end')}\t"
            f"{block.get('block')}\t{','.join(str(t) for t in block.get('tags', []))}\t"
            f"{block.get('heading', '')}"
        )
    if not found:
        print(f"no block matches {':'.join(expression)}", file=sys.stderr)
        return 1
    return 0


def cmd_context(expression: Sequence[str], root: Path = ROOT) -> int:
    found = [b for b in _blocks(load_index(root)) if matches(b, expression)]
    if not found:
        print(f"no block matches {':'.join(expression)}", file=sys.stderr)
        return 1
    cache: dict[str, list[str]] = {}
    for block in found:
        path = str(block.get("path"))
        lines = cache.setdefault(path, (root / path).read_text().splitlines())
        start = int(block.get("line", 1))
        end = int(block.get("end", start))
        print(f"# {path}:{start}-{end}  {block.get('block')}  {','.join(str(t) for t in block.get('tags', []))}")
        print("\n".join(lines[start - 1 : end]))
        print()
    return 0


def cmd_show(name: str, root: Path = ROOT) -> int:
    facts: Any = load_index(root).get("facts", {})
    if not isinstance(facts, dict) or name not in facts:
        print(f"unknown or unused fact {name!r}", file=sys.stderr)
        return 1
    entry = facts[name]
    print(f"{name} = {entry.get('display')}")
    extract = entry.get("extract", {})
    print(f"  value:   {entry.get('value')!r}")
    print(f"  extract: {json.dumps(extract, ensure_ascii=False, sort_keys=True)}")
    print(f"  used in: {', '.join(str(u) for u in entry.get('used_in', []))}")
    return 0


def cmd_docs(root: Path = ROOT) -> int:
    docs: Any = load_index(root).get("docs", [])
    for doc in docs:
        origin = doc.get("source") or doc.get("generated_by") or "?"
        print(f"{doc.get('id')}\t{doc.get('path')}\t{doc.get('lines')} lines\t{origin}")
    return 0


def cmd_render(check: bool, root: Path = ROOT) -> int:
    result = render.build(root)
    problems = list(result.problems)
    if check:
        problems.extend(render.drift(result, root))
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print(
            f"docs up to date ({len(result.docs)} docs, "
            f"{len(result.index['blocks'])} blocks, {len(result.index['facts'])} facts)"
        )
        return 0
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    written = render.write(result, root)
    print(f"wrote {len(written)} files ({len(result.index['blocks'])} blocks, {len(result.index['facts'])} facts)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adb-docs", description="Tagged docs: render, check, query.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("render", help="render docs/src into the committed documents")
    sub.add_parser("check", help="fail if any document or the index is out of date")
    sub.add_parser("docs", help="list documents and their origins")

    find = sub.add_parser("find", help="list blocks matching an expression")
    find.add_argument("expression", nargs="+", help="tags (AND), a|b for OR, id=block, doc=id")
    context = sub.add_parser("context", help="print the markdown of matching blocks")
    context.add_argument("expression", nargs="+", help="same expression syntax as `find`")
    show = sub.add_parser("show", help="show a fact, its extractor and where it is used")
    show.add_argument("name")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "render":
        return cmd_render(check=False)
    if args.command == "check":
        return cmd_render(check=True)
    if args.command == "find":
        return cmd_find(args.expression)
    if args.command == "context":
        return cmd_context(args.expression)
    if args.command == "show":
        return cmd_show(args.name)
    if args.command == "docs":
        return cmd_docs()
    raise SystemExit(f"unknown command {args.command!r}")
