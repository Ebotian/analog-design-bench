# 参考解官方判分实测

本文由 `scripts/build_results_md.py` 生成，数据源只有 `results/verifier/<task_id>.json`、`results/verifier/summary.json` 与 `results/reconcile.json`；表格中每个数字都直接读取对应字段，不在正文手写测量值。

## 口径

判分方是每个任务自带的官方沙箱：`data/t1/tasks/<task_id>/tests/test.sh`，镜像由同一目录的 `Dockerfile` 基于钉死的 sky130-tools 镜像构建。被测设计是 T0 记录里的参考网表（`data/tasks/<id>.json` 的 `.spi`）。沙箱只把 `circuit.spi` 以文件形式挂到 `/app/circuit.spi`，`/logs` 交给 verifier 写 JSON，结束后归还文件属主。

ngspice 固定单线程（`set num_threads=1`，同时挂到 `/app/.spiceinit` 与 `$HOME/.spiceinit`），否则一个多线程 ngspice 就能拖慢整机；这是本轮把此前的超时任务降到分钟级的原因。

## 总览

| 指标 | 值 | JSON 字段 |
|---|---:|---|
| 任务数 | 50 | `summary.tasks` |
| 状态分布 | passed 50 | `summary.status_counts` |
| 总 ngspice 仿真次数 | 1,991 | `summary.ngspice_runs` |
| verifier 自报墙钟合计 | 1,975.3 s | `summary.verifier_wall_s` |
| 逐任务墙钟合计 | 3,021.2 s | `summary.task_wall_sum_s` |
| 本次 sweep 墙钟 | 528.0 s | `summary.sweep_wall_s` |
| 主机 CPU 平均 / 峰值 | 42.7% / 97.2% | `summary.cpu_busy_percent.mean` / `.peak` |
| 残留容器 | 0 | `summary.leftover_containers` |
| 残留 ngspice 线程 | 0 | `summary.ngspice_threads_after` |

## 逐任务

| 任务 | 状态 | reward | gates | sims | verifier 墙钟 (s) | 调度墙钟 (s) |
|---|---|---:|---:|---:|---:|---:|
| `sky130-ahuja-compensated-two-stage-load-range-pvt` | passed | 1.000 | 8/8 | 21 | 0.0 | 30.9 |
| `sky130-analog-input-mux-8to1-pvt` | passed | 1.000 | 5/5 | 0 | 0.0 | 10.8 |
| `sky130-bandgap-reference-pvt` | passed | 1.000 | 12/12 | 189 | 98.5 | 99.0 |
| `sky130-beta-multiplier-reference-pvt-mc` | passed | 1.000 | 12/12 | 95 | 47.5 | 48.0 |
| `sky130-bootstrap-driver-pt` | passed | 1.000 | 24/24 | 0 | 0.0 | 307.6 |
| `sky130-bootstrap-sampler-fft` | passed | 1.000 | 5/5 | 0 | 0.0 | 32.1 |
| `sky130-bottom-plate-sampler-pvt` | passed | 1.000 | 6/6 | 14 | 31.1 | 31.4 |
| `sky130-buf-ppsf-bs-gain1-sfdr90` | passed | 1.000 | 4/4 | 0 | 0.0 | 54.1 |
| `sky130-capless-ldo-1v0-20ma-pvt` | passed | 1.000 | 8/8 | 20 | 0.0 | 13.4 |
| `sky130-class-d-halfbridge-eff95-tt` | passed | 1.000 | 5/5 | 0 | 0.0 | 182.6 |
| `sky130-cml-divider-div2-range1gto10g` | passed | 1.000 | 2/2 | 25 | 15.0 | 15.4 |
| `sky130-cml-tx-driver-28g-nrz-pvt` | passed | 1.000 | 23/23 | 0 | 0.0 | 9.4 |
| `sky130-cmos-switched-resistor-dac-5bit-pvt` | passed | 1.000 | 8/8 | 0 | 0.0 | 78.5 |
| `sky130-complementary-folded-cascode-ab-opamp-pvt` | passed | 1.000 | 14/14 | 0 | 0.0 | 139.6 |
| `sky130-constant-gm-stable-gain-amplifier-pvt` | passed | 1.000 | 9/9 | 81 | 29.5 | 29.8 |
| `sky130-current-starved-ring-vco-pvt` | passed | 1.000 | 9/9 | 30 | 67.1 | 67.4 |
| `sky130-delta-sigma-adc-1bit-first-order-osr32-sndr40` | passed | 1.000 | 9/9 | 3 | 29.6 | 30.0 |
| `sky130-fct-residue-amplifier-900msps` | passed | 1.000 | 13/13 | 10 | 112.3 | 112.6 |
| `sky130-fd-sampling-feedback-ota-gain1-settling40ns-noise300uv-pvt-mc` | passed | 1.000 | 15/15 | 114 | 65.0 | 65.3 |
| `sky130-fd-sampling-feedback-ota-gain8-settling10ns-noise1mv-pvt-mc` | passed | 1.000 | 15/15 | 143 | 85.1 | 85.5 |
| `sky130-fd-two-stage-miller-opamp-gain60-pm60-noise50uv-pvt` | passed | 1.000 | 11/11 | 36 | 20.9 | 21.2 |
| `sky130-flash-adc-3bit-pvt` | passed | 1.000 | 13/13 | 8 | 49.9 | 50.4 |
| `sky130-flash-adc-4bit-50msps` | passed | 1.000 | 6/6 | 0 | 133.6 | 133.9 |
| `sky130-folded-cascode-ota-gain130-pm60-noise50uv-pvt` | passed | 1.000 | 8/8 | 60 | 30.6 | 30.9 |
| `sky130-fully-differential-ota-c-biquad-pvt` | passed | 1.000 | 16/16 | 62 | 17.4 | 18.2 |
| `sky130-gilbert-cell-mixer-2p4ghz-pvt-mismatch` | passed | 1.000 | 7/7 | 23 | 0.4 | 10.0 |
| `sky130-gmr-degen-amp-constant-gm-pvt` | passed | 1.000 | 4/4 | 30 | 0.0 | 12.0 |
| `sky130-gmr-degenerated-diff-amp-gain2-bw70m-power1p2mw-sdr60` | passed | 1.000 | 4/4 | 10 | 0.0 | 3.7 |
| `sky130-high-psrr-bandgap-reference-pvt` | passed | 1.000 | 5/5 | 12 | 5.1 | 5.4 |
| `sky130-inductive-lna-2p4ghz-gain12-nf2-pvt` | passed | 1.000 | 7/7 | 27 | 10.9 | 11.2 |
| `sky130-lc-vco-2ghz-pvt` | passed | 1.000 | 9/9 | 27 | 0.0 | 53.6 |
| `sky130-ldo-ota5-robust-pvt-mc` | passed | 1.000 | 9/9 | 161 | 36.6 | 36.9 |
| `sky130-low-power-line-driver-pvt` | passed | 1.000 | 8/8 | 135 | 0.0 | 68.0 |
| `sky130-low-power-oscillator-2mhz-pvt` | passed | 1.000 | 3/3 | 5 | 91.1 | 91.4 |
| `sky130-nmos-pass-ldo-0p4v-pvt-mc` | passed | 1.000 | 7/7 | 0 | 0.0 | 22.4 |
| `sky130-ota-5t-gain40-pm60-noise50uv-pvt` | passed | 1.000 | 7/7 | 13 | 0.0 | 6.1 |
| `sky130-pll-charge-pump-pvt` | passed | 1.000 | 6/6 | 50 | 32.6 | 32.9 |
| `sky130-programmable-icc-iptat-current-mirror-pvt` | passed | 1.000 | 11/11 | 6 | 2.6 | 2.9 |
| `sky130-regulated-charge-pump-10mhz-pvt` | passed | 1.000 | 4/4 | 13 | 79.9 | 80.3 |
| `sky130-ring-amp-3stage-gain8-400uw` | passed | 1.000 | 7/7 | 0 | 2.3 | 2.7 |
| `sky130-sar-adc-4bit-async` | passed | 1.000 | 4/4 | 6 | 203.4 | 203.7 |
| `sky130-sar-adc-6bit-async` | passed | 1.000 | 4/4 | 12 | 303.8 | 304.1 |
| `sky130-switched-capacitor-2to1-converter-pvt` | passed | 1.000 | 9/9 | 94 | 92.2 | 92.5 |
| `sky130-telescopic-cascode-ota-pvt-fix` | passed | 1.000 | 8/8 | 30 | 15.7 | 16.0 |
| `sky130-three-stage-nested-miller-ota-pvt` | passed | 1.000 | 11/11 | 225 | 111.7 | 112.0 |
| `sky130-tia-zt8k-bw750m-noise5p-sfdr70-power5m-pt` | passed | 1.000 | 6/6 | 45 | 14.3 | 14.6 |
| `sky130-transistor-divide-by-2` | passed | 1.000 | 4/4 | 2 | 1.1 | 1.4 |
| `sky130-two-stage-miller-opamp-gain60-ugb200-pm60-noise50uv-pvt` | passed | 1.000 | 11/11 | 69 | 30.1 | 30.4 |
| `sky130-two-stage-miller-opamp-gain70-ugb20-pm60-noise50uv-pvt` | passed | 1.000 | 11/11 | 69 | 27.1 | 27.4 |
| `sky130-unregulated-charge-pump-10mhz-pvt` | passed | 1.000 | 4/4 | 16 | 81.1 | 81.4 |

`verifier 墙钟` 为零表示该 verifier 没有自报墙钟；该任务的调度墙钟仍有实测值，`summary.verifier_wall_s` 只汇总自报值。

## 与 T0 `referenceResult` 对账

对账按 reference 的 gate（`referenceResult.metrics[].source_check`）对应到本次 verifier 的 CTRF gate；参考值、本次值、相对误差和状态一致性逐条写进 `results/reconcile.json`。本次值从 verifier 自己打印的 CTRF 消息中提取，并记录提取方式；出现多个候选数字时取与 T0 参考值最近的一个，因此若同一 gate 的实测值漂移超过容差，会落进不一致清单而不是被吞掉。

| 指标 | 值 | JSON 字段 |
|---|---:|---|
| 已对账任务 | 50 | `reconcile.summary.tasks` |
| 参考 gate 总数 | 452 | `reconcile.summary.reference_metrics` |
| 本次 verifier gate 总数 | 430 | `reconcile.summary.run_gates` |
| 可比值数 | 452 | `reconcile.summary.comparable_values` |
| 值匹配（容差或打印精度） | 452 / 452 | `reconcile.summary.values_matched` |
| 其中容差内 | 451 | `reconcile.summary.values_within_tolerance` |
| 其中按打印精度一致 | 1 | `reconcile.summary.values_matched_by_printed_precision` |
| 状态一致 | 452 / 452 | `reconcile.summary.status_matches` |
| 值/状态不一致 | 0 | `reconcile.summary.mismatch_count` |
| 任务级 reward/gate 数不一致 | 0 | `reconcile.summary.run_level_mismatch_count` |
| 值容差 | 1.0% | `reconcile.summary.tolerance` |

### 按打印精度一致的 gate

| 任务 | 参考 gate | 本次 gate | 参考值 | 本次值 | 相对误差 | 证据 |
|---|---|---|---:|---:|---:|---|
| `sky130-transistor-divide-by-2` | `maximum_duty_cycle_error` | `duty_cycle` | 0.108700 | 0.110000 | 0.011960 | high/low duty=49.9/49.89/49.89/50.1/50.11/50.11%; low/high=4.937e-07/1.8V |

### 对账不一致清单

空：每条参考 gate 都在容差或打印精度内找到对应实测值，状态全部一致。

## 非通过项

空：`summary.failing` 为空。

## 字段映射

逐任务表：`status` ← `<task>.status`；`reward` ← `<task>.artifacts.reward`；`gates` ← `<task>.artifacts.tests_passed/tests_total`；`sims` ← `<task>.artifacts.ngspice_runs`；`verifier 墙钟` ← `<task>.artifacts.wall_clock_s`；`调度墙钟` ← `<task>.wall_s`。`summary.sweep_wall_s` 由 `started_utc` 与 `finished_utc` 之差生成；`summary.task_wall_sum_s` 是逐任务 `wall_s` 之和，二者在并行 sweep 下不相等，故分列。

对账表：任务数与 gate 数 ← `reconcile.summary` 同名字段；值匹配数为 `values_matched`，等于 `values_within_tolerance` 与 `values_matched_by_printed_precision` 之和；每条 gate 的 `reference_value`、`current_value`、`relative_error`、`status_match`、`current_value_source` 与原始 `message` 见 `reconcile.tasks[].gates[]`。
