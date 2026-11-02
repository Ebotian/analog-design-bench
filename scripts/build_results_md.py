#!/usr/bin/env python3
"""Render the verifier measurement report from the generated JSON only.

Every number in the output comes from one of:

    results/verifier/summary.json     sweep totals, CPU, leftovers
    results/verifier/<task>.json      per-task reward/gates/sims/wall
    results/reconcile.json            per-gate T0 reconciliation

The script is the provenance map: each table column is filled from the named
field, and no statistic is computed by hand in the markdown.

Usage:
    python3 scripts/build_results_md.py
    python3 scripts/build_results_md.py --out docs/VERIFIER_RESULTS.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, cast

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "results" / "verifier"
RECONCILE = ROOT / "results" / "reconcile.json"
DEFAULT_OUT = ROOT / "docs" / "VERIFIER_RESULTS.md"


def load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text()))


def task_records() -> list[dict[str, Any]]:
    return [
        load(path)
        for path in sorted(RUNS.glob("sky130-*.json"))
        if path.stem != "summary"
    ]


def pct(value: object, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}%"


def frac_pct(value: object, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.{digits}f}%"


def num(value: object, digits: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return f"{value:,}"


def artifact(record: Mapping[str, Any], key: str) -> object:
    items = record.get("artifacts")
    return cast(Mapping[str, Any], items).get(key) if isinstance(items, dict) else None


def render() -> str:
    summary = load(RUNS / "summary.json")
    reconcile = load(RECONCILE)
    rec_summary = cast(Mapping[str, Any], reconcile["summary"])
    rec_tasks = {
        str(item["task_id"]): item for item in cast(list[dict[str, Any]], reconcile["tasks"])
    }
    records = task_records()
    counts = cast(Mapping[str, Any], summary.get("status_counts", {}))
    cpu = cast(Mapping[str, Any], summary.get("cpu_busy_percent", {}))
    lines: list[str] = []
    add = lines.append

    add("# 参考解官方判分实测")
    add("")
    add(
        "本文由 `scripts/build_results_md.py` 生成，数据源只有 "
        "`results/verifier/<task_id>.json`、`results/verifier/summary.json` 与 "
        "`results/reconcile.json`；表格中每个数字都直接读取对应字段，"
        "不在正文手写测量值。"
    )
    add("")
    add("## 口径")
    add("")
    add(
        "判分方是每个任务自带的官方沙箱：`data/t1/tasks/<task_id>/tests/test.sh`，"
        "镜像由同一目录的 `Dockerfile` 基于钉死的 sky130-tools 镜像构建。"
        "被测设计是 T0 记录里的参考网表（`data/tasks/<id>.json` 的 `.spi`）。"
        "沙箱只把 `circuit.spi` 以文件形式挂到 `/app/circuit.spi`，"
        "`/logs` 交给 verifier 写 JSON，结束后归还文件属主。"
    )
    add("")
    add(
        "ngspice 固定单线程（`set num_threads=1`，同时挂到 `/app/.spiceinit` 与 "
        "`$HOME/.spiceinit`），否则一个多线程 ngspice 就能拖慢整机；"
        "这是本轮把此前的超时任务降到分钟级的原因。"
    )
    add("")
    add("## 总览")
    add("")
    add("| 指标 | 值 | JSON 字段 |")
    add("|---|---:|---|")
    add(
        f"| 任务数 | {num(summary.get('tasks'))} | `summary.tasks` |"
    )
    add(
        "| 状态分布 | "
        + " / ".join(f"{key} {num(value)}" for key, value in counts.items())
        + " | `summary.status_counts` |"
    )
    add(
        f"| 总 ngspice 仿真次数 | {num(summary.get('ngspice_runs'))} | "
        "`summary.ngspice_runs` |"
    )
    add(
        "| sims 计数来源 | "
        + " / ".join(
            f"{name} {num(value)}"
            for name, value in cast(
                Mapping[str, Any], summary.get("runs_sources", {})
            ).items()
        )
        + " | `summary.runs_sources` |"
    )
    add(
        "| 墙钟来源 | "
        + " / ".join(
            f"{name} {num(value)}"
            for name, value in cast(
                Mapping[str, Any], summary.get("wall_clock_sources", {})
            ).items()
        )
        + " | `summary.wall_clock_sources` |"
    )
    add(
        f"| verifier 自报墙钟合计 | {num(summary.get('verifier_reported_wall_s'))} s | "
        "`summary.verifier_reported_wall_s` |"
    )
    add(
        f"| harness 回退墙钟合计 | {num(summary.get('harness_fallback_wall_s'))} s | "
        "`summary.harness_fallback_wall_s` |"
    )
    add(
        f"| 墙钟合计（自报+回退） | {num(summary.get('wall_clock_sum_s'))} s | "
        "`summary.wall_clock_sum_s` |"
    )
    add(
        f"| 逐任务墙钟合计 | {num(summary.get('task_wall_sum_s'))} s | "
        "`summary.task_wall_sum_s` |"
    )
    add(
        f"| 本次 sweep 墙钟 | {num(summary.get('sweep_wall_s'))} s | "
        "`summary.sweep_wall_s` |"
    )
    add(
        f"| 主机 CPU 平均 / 峰值 | {pct(cpu.get('mean'))} / {pct(cpu.get('peak'))} | "
        "`summary.cpu_busy_percent.mean` / `.peak` |"
    )
    add(
        f"| 残留容器 | {len(cast(list[Any], summary.get('leftover_containers', [])))} | "
        "`summary.leftover_containers` |"
    )
    add(
        f"| 残留 ngspice 线程 | {num(summary.get('ngspice_threads_after'))} | "
        "`summary.ngspice_threads_after` |"
    )
    add("")
    add("## 逐任务")
    add("")
    add(
        "| 任务 | 状态 | reward | gates | sims | verifier 墙钟 (s) | 调度墙钟 (s) |"
    )
    add("|---|---|---:|---:|---:|---:|---:|")
    for record in records:
        task_id = str(record.get("task_id", ""))
        passed = artifact(record, "tests_passed")
        total = artifact(record, "tests_total")
        gates = f"{num(passed)}/{num(total)}" if passed is not None else "—"
        add(
            f"| `{task_id}` | {record.get('status')} | "
            f"{num(artifact(record, 'reward'), 3)} | {gates} | "
            f"{num(artifact(record, 'ngspice_runs'))} | "
            f"{num(artifact(record, 'wall_clock_s'))} | "
            f"{num(record.get('wall_s'))} |"
        )
    add("")
    add(
        "`verifier 墙钟` 取 verifier 自报值；若 verifier 不报，则取该任务的"
        "调度墙钟，并以 `artifacts.wall_clock_source` 标记（见总览的墙钟来源）。"
        "`sims` 由挂在沙箱 PATH 上的 ngspice 计数包装器逐次累加，"
        "包含重试/失败调用；因此它可以高于 T0 `runtime.ngspice_runs`，"
        "差异逐条列在下方对账节。"
    )
    add("")
    add("## 与 T0 `referenceResult` 对账")
    add("")
    add(
        "对账按 reference 的 gate（`referenceResult.metrics[].source_check`）"
        "对应到本次 verifier 的 CTRF gate；参考值、本次值、相对误差和状态一致性"
        "逐条写进 `results/reconcile.json`。本次值从 verifier 自己打印的 CTRF "
        "消息中提取，并记录提取方式；出现多个候选数字时取与 T0 参考值最近的一个，"
        "因此若同一 gate 的实测值漂移超过容差，会落进不一致清单而不是被吞掉。"
    )
    add("")
    add("| 指标 | 值 | JSON 字段 |")
    add("|---|---:|---|")
    add(
        f"| 已对账任务 | {num(rec_summary.get('tasks'))} | `reconcile.summary.tasks` |"
    )
    add(
        f"| 参考 gate 总数 | {num(rec_summary.get('reference_metrics'))} | "
        "`reconcile.summary.reference_metrics` |"
    )
    add(
        f"| 本次 verifier gate 总数 | {num(rec_summary.get('run_gates'))} | "
        "`reconcile.summary.run_gates` |"
    )
    add(
        f"| 可比值数 | {num(rec_summary.get('comparable_values'))} | "
        "`reconcile.summary.comparable_values` |"
    )
    add(
        f"| 值匹配（容差或打印精度） | {num(rec_summary.get('values_matched'))} / "
        f"{num(rec_summary.get('comparable_values'))} | "
        "`reconcile.summary.values_matched` |"
    )
    add(
        f"| 其中容差内 | {num(rec_summary.get('values_within_tolerance'))} | "
        "`reconcile.summary.values_within_tolerance` |"
    )
    add(
        f"| 其中按打印精度一致 | {num(rec_summary.get('values_matched_by_printed_precision'))} | "
        "`reconcile.summary.values_matched_by_printed_precision` |"
    )
    add(
        f"| 状态一致 | {num(rec_summary.get('status_matches'))} / "
        f"{num(rec_summary.get('reference_metrics'))} | "
        "`reconcile.summary.status_matches` |"
    )
    add(
        f"| 值/状态不一致 | {num(rec_summary.get('mismatch_count'))} | "
        "`reconcile.summary.mismatch_count` |"
    )
    add(
        f"| 任务级 reward/gate 数不一致 | {num(rec_summary.get('run_level_mismatch_count'))} | "
        "`reconcile.summary.run_level_mismatch_count` |"
    )
    add(
        f"| T0 记录 sims 合计 | {num(rec_summary.get('reference_ngspice_runs'))} | "
        "`reconcile.summary.reference_ngspice_runs` |"
    )
    add(
        f"| 本地计数 sims 合计 | {num(rec_summary.get('local_ngspice_runs'))} | "
        "`reconcile.summary.local_ngspice_runs` |"
    )
    add(
        f"| sims 不一致任务数 | {num(rec_summary.get('sim_difference_count'))} | "
        "`reconcile.summary.sim_difference_count` |"
    )
    add(
        f"| T0 墙钟合计 | {num(rec_summary.get('reference_wall_s'))} s | "
        "`reconcile.summary.reference_wall_s` |"
    )
    add(
        f"| 本地墙钟合计 | {num(rec_summary.get('local_wall_s'))} s | "
        "`reconcile.summary.local_wall_s` |"
    )
    add(
        f"| 墙钟可比任务数 | {num(rec_summary.get('wall_comparable_tasks'))} | "
        "`reconcile.summary.wall_comparable_tasks` |"
    )
    add(
        f"| 墙钟平均相对差 | {frac_pct(rec_summary.get('wall_mean_relative_difference'))} | "
        "`reconcile.summary.wall_mean_relative_difference` |"
    )
    add(
        f"| verifier 墙钟可比任务数 | "
        f"{num(rec_summary.get('verifier_wall_comparable_tasks'))} | "
        "`reconcile.summary.verifier_wall_comparable_tasks` |"
    )
    add(
        f"| verifier 墙钟平均相对差 | "
        f"{frac_pct(rec_summary.get('verifier_wall_mean_relative_difference'))} | "
        "`reconcile.summary.verifier_wall_mean_relative_difference` |"
    )
    add(
        f"| 值容差 | {num(float(rec_summary.get('tolerance', 0.0)) * 100, 1)}% | "
        "`reconcile.summary.tolerance` |"
    )
    add("")
    add("### sims 差异")
    add("")
    add(
        "本地计数是实际 ngspice 进程数；T0 `runtime.ngspice_runs` 为 `null` 表示"
        "参考记录未给出该字段，不是 0。"
    )
    add("")
    sim_differences = cast(
        list[dict[str, Any]], rec_summary.get("sim_differences", [])
    )
    if not sim_differences:
        add("空：每个任务的本地计数与 T0 记录一致。")
    else:
        add("| 任务 | T0 sims | 本地 sims |")
        add("|---|---:|---:|")
        for row in sim_differences:
            add(
                f"| `{row.get('task_id')}` | {num(row.get('reference'))} | "
                f"{num(row.get('local'))} |"
            )
    add("")
    add("### 墙钟对账")
    add("")
    add(
        "墙钟受机器负载与 Docker 启动开销影响，不设通过阈值；这里记录 T0 与本次的"
        "差值和相对差，`verifier 墙钟` 仅在本次来源为 `verifier` 时与 T0 自报值可比。"
    )
    add("")
    add(
        "| 任务 | T0 墙钟 (s) | 本地墙钟 (s) | 相对差 | T0 verifier 墙钟 (s) | "
        "本地 verifier 墙钟 (s) | 本地来源 |"
    )
    add("|---|---:|---:|---:|---:|---:|---|")
    for item in cast(list[dict[str, Any]], reconcile["tasks"]):
        comparison = cast(Mapping[str, Any], item.get("wall_comparison", {}))
        add(
            f"| `{item.get('task_id')}` | "
            f"{num(comparison.get('reference_wall_s'))} | "
            f"{num(comparison.get('local_wall_s'))} | "
            f"{frac_pct(comparison.get('wall_relative_difference'))} | "
            f"{num(comparison.get('reference_verifier_wall_s'))} | "
            f"{num(comparison.get('local_verifier_wall_s'))} | "
            f"{comparison.get('local_verifier_wall_source')} |"
        )
    add("")
    add("### 按打印精度一致的 gate")
    add("")
    rounding_rows = [
        {**row, "task_id": item["task_id"]}
        for item in cast(list[dict[str, Any]], reconcile["tasks"])
        for row in cast(list[dict[str, Any]], item["gates"])
        if row.get("value_match_basis") == "printed-precision"
    ]
    if not rounding_rows:
        add("空：所有匹配值都在容差内。")
    else:
        add("| 任务 | 参考 gate | 本次 gate | 参考值 | 本次值 | 相对误差 | 证据 |")
        add("|---|---|---|---:|---:|---:|---|")
        for row in rounding_rows:
            add(
                f"| `{row.get('task_id', '')}` | `{row.get('id')}` | "
                f"`{row.get('gate_id')}` | "
                f"{num(row.get('reference_value'), 6)} | "
                f"{num(row.get('current_value'), 6)} | "
                f"{num(row.get('relative_error'), 6)} | {row.get('message')} |"
            )
    add("")
    add("### 对账不一致清单")
    add("")
    mismatches = cast(list[dict[str, Any]], rec_summary.get("mismatches", []))
    run_mismatches = cast(list[dict[str, Any]], rec_summary.get("run_level_mismatches", []))
    if not mismatches and not run_mismatches:
        add("空：每条参考 gate 都在容差或打印精度内找到对应实测值，状态全部一致。")
    else:
        add("| 任务 | gate | 参考值 | 本次值 | 相对误差 | 状态一致 |")
        add("|---|---|---:|---:|---:|---|")
        for row in mismatches:
            add(
                f"| `{row.get('task_id')}` | `{row.get('id')}` | "
                f"{num(row.get('reference_value'), 6)} | "
                f"{num(row.get('current_value'), 6)} | "
                f"{num(row.get('relative_error'), 6)} | {row.get('status_match')} |"
            )
        for row in run_mismatches:
            add(
                f"| `{row.get('task_id')}` | task-level | "
                f"{row.get('reference')} | {row.get('run')} | — | false |"
            )
    add("")
    add("## 非通过项")
    add("")
    failing = cast(list[dict[str, Any]], summary.get("failing", []))
    if not failing:
        add("空：`summary.failing` 为空。")
    else:
        add("| 任务 | 状态 | 原因 |")
        add("|---|---|---|")
        for row in failing:
            add(f"| `{row.get('task_id')}` | {row.get('status')} | {row.get('reason')} |")
    add("")
    add("## 字段映射")
    add("")
    add(
        "逐任务表：`status` ← `<task>.status`；`reward` ← `<task>.artifacts.reward`；"
        "`gates` ← `<task>.artifacts.tests_passed/tests_total`；"
        "`sims` ← `<task>.artifacts.ngspice_runs`；"
        "`verifier 墙钟` ← `<task>.artifacts.wall_clock_s`；"
        "`调度墙钟` ← `<task>.wall_s`。`summary.sweep_wall_s` 由 "
        "`started_utc` 与 `finished_utc` 之差生成；`summary.task_wall_sum_s` "
        "是逐任务 `wall_s` 之和，二者在并行 sweep 下不相等，故分列。"
    )
    add("")
    add(
        "对账表：任务数与 gate 数 ← `reconcile.summary` 同名字段；"
        "值匹配数为 `values_matched`，等于 `values_within_tolerance` 与 "
        "`values_matched_by_printed_precision` 之和；"
        "每条 gate 的 `reference_value`、`current_value`、`relative_error`、"
        "`status_match`、`current_value_source` 与原始 `message` 见 "
        "`reconcile.tasks[].gates[]`。"
    )
    add("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="fail when the report is stale")
    args = parser.parse_args()
    text = render()
    if args.check:
        if not args.out.exists():
            print(f"{args.out} is missing; run scripts/build_results_md.py", file=sys.stderr)
            return 1
        if args.out.read_text() != text:
            print(f"{args.out} is stale; run scripts/build_results_md.py", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date ({len(text.splitlines())} lines)")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(f"wrote {args.out} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
