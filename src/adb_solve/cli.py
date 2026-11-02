"""``adb-solve`` command line.

Only the verbs P1 can honour are wired up: ``vars``, ``check``, ``eval``,
``apply`` and ``schema``.  ``solve`` (P3) fails with a clear message instead of
pretending to optimise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from . import bench, check as legality, corners as corner_asset, evaluate, params
from .docker_backend import DEFAULT_IMAGE, SimulationError
from .sim import CornerValue
from . import netlist as spice

MISSING = 2


def _read_json(value: str | None) -> dict[str, object]:
    """Read a JSON object from an inline string or a file path."""
    if not value:
        return {}
    raw = json.loads(value) if value.strip().startswith("{") else json.loads(Path(value).read_text())
    if not isinstance(raw, dict):
        raise SystemExit("expected a JSON object")
    return cast(dict[str, object], raw)


def _candidate_values(payload: dict[str, object]) -> dict[str, float]:
    """``{param: value}`` from a candidate file, optionally under ``params``."""
    body = payload.get("params", payload)
    if not isinstance(body, dict):
        raise SystemExit("candidate file must map parameter ids to numbers")
    values: dict[str, float] = {}
    for key, value in cast(dict[str, object], body).items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SystemExit(f"candidate {key!r} is not a number: {value!r}")
        values[str(key)] = float(value)
    return values


def _design(path: Path) -> params.Design:
    return params.load(path)


def cmd_vars(args: argparse.Namespace) -> int:
    design = _design(args.netlist)
    payload = {
        "netlist": str(args.netlist),
        "summary": params.summarise(design),
        "params": [param.to_json() for param in design.params],
    }
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {args.out}")
    else:
        print(json.dumps(payload, indent=2))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    text = Path(args.netlist).read_text()
    allow = tuple(args.allow_ideal or ())
    result = legality.check_text(
        text,
        image=args.image,
        allow_ideal=allow,
        enforce_geometry=args.enforce_geometry,
        require_subcircuit=tuple(args.require_subcircuit or ()),
    )
    print(json.dumps(result.to_json(), indent=2))
    return 0 if result.ok else 1


def _decks(args: argparse.Namespace) -> list[bench.Deck]:
    decks: list[bench.Deck] = []
    for entry in args.bench:
        path = Path(entry)
        if path.is_dir():
            decks.extend(bench.load(item) for item in sorted(path.glob("*.spi")))
        else:
            decks.append(bench.load(path))
    if not decks:
        raise SystemExit("no bench decks found")
    return decks


def _matrix(args: argparse.Namespace) -> list[dict[str, CornerValue]]:
    """Corner matrix from the declared asset, or from ad-hoc CLI options."""
    if args.corners is None:
        if args.task is not None:
            raise SystemExit("--task requires --corners")
        values = cast(dict[str, CornerValue], _read_json(args.values))
        return [
            dict(corner)
            for corner in evaluate.corner_matrix(
                processes=args.process,
                temperatures=args.temperature,
                values=values,
            )
        ]
    if args.task is None:
        raise SystemExit("--corners requires --task")
    if args.values or args.process or args.temperature:
        raise SystemExit("--corners cannot be combined with --values/--process/--temperature")
    entry = corner_asset.for_task(corner_asset.load(args.corners), args.task)
    return [dict(corner) for corner in corner_asset.expand(entry)]


def cmd_eval(args: argparse.Namespace) -> int:
    text = Path(args.netlist).read_text()
    decks = _decks(args)
    corners = _matrix(args)
    keys = None
    if args.corners is not None and args.task is not None:
        keys = corner_asset.for_task(corner_asset.load(args.corners), args.task).keys
    options = evaluate.EvalOptions(
        image=args.image,
        jobs=args.jobs,
        timeout=args.timeout,
        cache_root=Path(args.cache_dir),
        use_cache=not args.no_cache,
        work_root=Path(args.work_dir),
        oneshot=args.oneshot,
    )
    try:
        report = evaluate.evaluate(text, decks, corners, options, keys=keys)
    except bench.MissingValues as exc:
        print(f"missing deck values: {', '.join(exc.names)}", file=sys.stderr)
        return MISSING
    except SimulationError as exc:
        print(f"simulation backend error: {exc}", file=sys.stderr)
        return MISSING
    if args.out:
        evaluate.write_report(report, Path(args.out))
    print(evaluate.summarise(report))
    print(
        f"sims={report['sims']} status={report['status_counts']} "
        f"cache={report['cache']['hits']}/{report['cache']['misses']} "
        f"wall={report['wall_seconds']}s"
    )
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    design = _design(args.netlist)
    values = _candidate_values(_read_json(args.cand))
    try:
        text = design.apply(values)
    except (KeyError, ValueError) as exc:
        print(f"cannot apply candidates: {exc}", file=sys.stderr)
        return MISSING
    out = Path(args.out)
    out.write_text(text)
    before = spice.line_diff(design.text, text)
    print(f"wrote {out} ({len(before)} line(s) changed)")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    design = _design(args.netlist)
    schema = {
        "schema_version": "adb-schema-1",
        "netlist": str(args.netlist),
        "summary": params.summarise(design),
        "params": [
            {**param.to_json(), "bounds": design.bounds_for(param).to_json()}
            for param in design.params
        ],
        "groups": {group: len(members) for group, members in design.groups().items()},
        "decks": [bench.load(path).describe() for path in sorted(Path(args.bench_dir).glob("*.spi"))]
        if args.bench_dir
        else [],
    }
    if args.out:
        Path(args.out).write_text(json.dumps(schema, indent=2) + "\n")
        print(f"wrote {args.out}")
    else:
        print(json.dumps(schema, indent=2))
    return 0


def cmd_solve(args: argparse.Namespace) -> int:
    print(
        "solve is P3 (docs/PLAN.md): the evaluator works, the optimiser does not exist yet.",
        file=sys.stderr,
    )
    return MISSING


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adb-solve", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    common_netlist = argparse.ArgumentParser(add_help=False)
    common_netlist.add_argument("--netlist", type=Path, required=True)

    vars_parser = sub.add_parser("vars", parents=[common_netlist], help="list tunable parameters")
    vars_parser.add_argument("--out", type=Path)
    vars_parser.set_defaults(func=cmd_vars)

    check_parser = sub.add_parser("check", parents=[common_netlist], help="run the legality checker")
    check_parser.add_argument("--allow-ideal", nargs="*", default=None, choices=["R", "C", "L", "K"])
    check_parser.add_argument("--enforce-geometry", action="store_true")
    check_parser.add_argument("--require-subcircuit", nargs="+", default=None)
    check_parser.add_argument("--image", default=DEFAULT_IMAGE)
    check_parser.set_defaults(func=cmd_check)

    eval_parser = sub.add_parser("eval", parents=[common_netlist], help="simulate benches")
    eval_parser.add_argument("--bench", nargs="+", required=True, help="deck files or directories")
    eval_parser.add_argument("--values", help="JSON file or inline object of deck values")
    eval_parser.add_argument("--corners", type=Path, help="corner asset, e.g. configs/corners.toml")
    eval_parser.add_argument("--task", help="task id inside the corner asset")
    eval_parser.add_argument("--process", nargs="*", default=None, help="process corners, e.g. tt ss ff")
    eval_parser.add_argument("--temperature", nargs="*", type=float, default=None)
    eval_parser.add_argument("--jobs", type=int, default=1)
    eval_parser.add_argument("--timeout", type=float, default=120.0)
    eval_parser.add_argument("--out", type=Path)
    eval_parser.add_argument("--image", default=DEFAULT_IMAGE)
    eval_parser.add_argument("--cache-dir", default=".adb-cache")
    eval_parser.add_argument("--work-dir", default=".work/sim")
    eval_parser.add_argument("--no-cache", action="store_true")
    eval_parser.add_argument("--oneshot", action="store_true", help="one container per simulation")
    eval_parser.set_defaults(func=cmd_eval)

    apply_parser = sub.add_parser("apply", parents=[common_netlist], help="write candidate values back")
    apply_parser.add_argument("--cand", required=True, help="JSON file or inline {param: value}")
    apply_parser.add_argument("--out", type=Path, required=True)
    apply_parser.set_defaults(func=cmd_apply)

    schema_parser = sub.add_parser("schema", parents=[common_netlist], help="self-describing JSON")
    schema_parser.add_argument("--bench-dir", type=Path)
    schema_parser.add_argument("--out", type=Path)
    schema_parser.set_defaults(func=cmd_schema)

    solve_parser = sub.add_parser("solve", parents=[common_netlist], help="not implemented (P3)")
    solve_parser.add_argument("--budget", type=int, default=1500)
    solve_parser.set_defaults(func=cmd_solve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
