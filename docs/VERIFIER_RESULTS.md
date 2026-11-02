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
| 总 ngspice 仿真次数 | 2,429 | `summary.ngspice_runs` |
| sims 计数来源 | wrapper-count 50 | `summary.runs_sources` |
| 墙钟来源 | harness 11 / verifier 39 | `summary.wall_clock_sources` |
| verifier 自报墙钟合计 | 2,065.9 s | `summary.verifier_reported_wall_s` |
| harness 回退墙钟合计 | 639.8 s | `summary.harness_fallback_wall_s` |
| 墙钟合计（自报+回退） | 2,705.8 s | `summary.wall_clock_sum_s` |
| 逐任务墙钟合计 | 2,718.5 s | `summary.task_wall_sum_s` |
| 本次 sweep 墙钟 | 730.0 s | `summary.sweep_wall_s` |
| 主机 CPU 平均 / 峰值 | 39.8% / 68.3% | `summary.cpu_busy_percent.mean` / `.peak` |
| 残留容器 | 0 | `summary.leftover_containers` |
| 残留 ngspice 线程 | 0 | `summary.ngspice_threads_after` |

## 逐任务

| 任务 | 状态 | reward | gates | sims | verifier 墙钟 (s) | 调度墙钟 (s) |
|---|---|---:|---:|---:|---:|---:|
| `sky130-ahuja-compensated-two-stage-load-range-pvt` | passed | 1.000 | 8/8 | 21 | 16.1 | 16.1 |
| `sky130-analog-input-mux-8to1-pvt` | passed | 1.000 | 5/5 | 15 | 13.3 | 13.7 |
| `sky130-bandgap-reference-pvt` | passed | 1.000 | 12/12 | 189 | 73.0 | 73.3 |
| `sky130-beta-multiplier-reference-pvt-mc` | passed | 1.000 | 12/12 | 95 | 34.7 | 35.0 |
| `sky130-bootstrap-driver-pt` | passed | 1.000 | 24/24 | 3 | 251.2 | 251.2 |
| `sky130-bootstrap-sampler-fft` | passed | 1.000 | 5/5 | 22 | 24.0 | 24.3 |
| `sky130-bottom-plate-sampler-pvt` | passed | 1.000 | 6/6 | 14 | 34.5 | 34.8 |
| `sky130-buf-ppsf-bs-gain1-sfdr90` | passed | 1.000 | 4/4 | 20 | 43.1 | 43.1 |
| `sky130-capless-ldo-1v0-20ma-pvt` | passed | 1.000 | 8/8 | 20 | 9.5 | 9.5 |
| `sky130-class-d-halfbridge-eff95-tt` | passed | 1.000 | 5/5 | 1 | 150.4 | 150.4 |
| `sky130-cml-divider-div2-range1gto10g` | passed | 1.000 | 2/2 | 25 | 11.5 | 11.8 |
| `sky130-cml-tx-driver-28g-nrz-pvt` | passed | 1.000 | 23/23 | 12 | 6.4 | 6.7 |
| `sky130-cmos-switched-resistor-dac-5bit-pvt` | passed | 1.000 | 8/8 | 41 | 59.4 | 59.7 |
| `sky130-complementary-folded-cascode-ab-opamp-pvt` | passed | 1.000 | 14/14 | 212 | 103.1 | 103.4 |
| `sky130-constant-gm-stable-gain-amplifier-pvt` | passed | 1.000 | 9/9 | 81 | 28.1 | 28.4 |
| `sky130-current-starved-ring-vco-pvt` | passed | 1.000 | 9/9 | 30 | 68.3 | 68.6 |
| `sky130-delta-sigma-adc-1bit-first-order-osr32-sndr40` | passed | 1.000 | 9/9 | 3 | 29.1 | 29.4 |
| `sky130-fct-residue-amplifier-900msps` | passed | 1.000 | 13/13 | 10 | 111.4 | 111.7 |
| `sky130-fd-sampling-feedback-ota-gain1-settling40ns-noise300uv-pvt-mc` | passed | 1.000 | 15/15 | 114 | 64.0 | 64.3 |
| `sky130-fd-sampling-feedback-ota-gain8-settling10ns-noise1mv-pvt-mc` | passed | 1.000 | 15/15 | 143 | 84.8 | 85.1 |
| `sky130-fd-two-stage-miller-opamp-gain60-pm60-noise50uv-pvt` | passed | 1.000 | 11/11 | 36 | 21.0 | 21.2 |
| `sky130-flash-adc-3bit-pvt` | passed | 1.000 | 13/13 | 8 | 40.3 | 40.6 |
| `sky130-flash-adc-4bit-50msps` | passed | 1.000 | 6/6 | 3 | 132.5 | 132.8 |
| `sky130-folded-cascode-ota-gain130-pm60-noise50uv-pvt` | passed | 1.000 | 8/8 | 60 | 31.0 | 31.3 |
| `sky130-fully-differential-ota-c-biquad-pvt` | passed | 1.000 | 16/16 | 63 | 17.1 | 17.8 |
| `sky130-gilbert-cell-mixer-2p4ghz-pvt-mismatch` | passed | 1.000 | 7/7 | 23 | 10.2 | 10.5 |
| `sky130-gmr-degen-amp-constant-gm-pvt` | passed | 1.000 | 4/4 | 30 | 12.3 | 12.3 |
| `sky130-gmr-degenerated-diff-amp-gain2-bw70m-power1p2mw-sdr60` | passed | 1.000 | 4/4 | 10 | 3.7 | 3.7 |
| `sky130-high-psrr-bandgap-reference-pvt` | passed | 1.000 | 5/5 | 12 | 5.2 | 5.4 |
| `sky130-inductive-lna-2p4ghz-gain12-nf2-pvt` | passed | 1.000 | 7/7 | 27 | 11.2 | 11.6 |
| `sky130-lc-vco-2ghz-pvt` | passed | 1.000 | 9/9 | 27 | 52.4 | 52.4 |
| `sky130-ldo-ota5-robust-pvt-mc` | passed | 1.000 | 9/9 | 161 | 35.4 | 35.7 |
| `sky130-low-power-line-driver-pvt` | passed | 1.000 | 8/8 | 135 | 66.9 | 66.9 |
| `sky130-low-power-oscillator-2mhz-pvt` | passed | 1.000 | 3/3 | 5 | 102.0 | 102.3 |
| `sky130-nmos-pass-ldo-0p4v-pvt-mc` | passed | 1.000 | 7/7 | 104 | 27.6 | 27.6 |
| `sky130-ota-5t-gain40-pm60-noise50uv-pvt` | passed | 1.000 | 7/7 | 13 | 6.5 | 6.5 |
| `sky130-pll-charge-pump-pvt` | passed | 1.000 | 6/6 | 50 | 32.1 | 32.4 |
| `sky130-programmable-icc-iptat-current-mirror-pvt` | passed | 1.000 | 11/11 | 6 | 2.7 | 3.1 |
| `sky130-regulated-charge-pump-10mhz-pvt` | passed | 1.000 | 4/4 | 13 | 75.2 | 75.6 |
| `sky130-ring-amp-3stage-gain8-400uw` | passed | 1.000 | 7/7 | 4 | 2.2 | 2.5 |
| `sky130-sar-adc-4bit-async` | passed | 1.000 | 4/4 | 6 | 173.9 | 174.2 |
| `sky130-sar-adc-6bit-async` | passed | 1.000 | 4/4 | 12 | 269.3 | 269.6 |
| `sky130-switched-capacitor-2to1-converter-pvt` | passed | 1.000 | 9/9 | 94 | 86.0 | 86.4 |
| `sky130-telescopic-cascode-ota-pvt-fix` | passed | 1.000 | 8/8 | 30 | 13.7 | 14.1 |
| `sky130-three-stage-nested-miller-ota-pvt` | passed | 1.000 | 11/11 | 225 | 106.6 | 106.9 |
| `sky130-tia-zt8k-bw750m-noise5p-sfdr70-power5m-pt` | passed | 1.000 | 6/6 | 45 | 13.4 | 13.7 |
| `sky130-transistor-divide-by-2` | passed | 1.000 | 4/4 | 2 | 0.9 | 1.3 |
| `sky130-two-stage-miller-opamp-gain60-ugb200-pm60-noise50uv-pvt` | passed | 1.000 | 11/11 | 69 | 28.5 | 28.8 |
| `sky130-two-stage-miller-opamp-gain70-ugb20-pm60-noise50uv-pvt` | passed | 1.000 | 11/11 | 69 | 28.4 | 28.7 |
| `sky130-unregulated-charge-pump-10mhz-pvt` | passed | 1.000 | 4/4 | 16 | 81.6 | 81.9 |

`verifier 墙钟` 取 verifier 自报值；若 verifier 不报，则取该任务的调度墙钟，并以 `artifacts.wall_clock_source` 标记（见总览的墙钟来源）。`sims` 由挂在沙箱 PATH 上的 ngspice 计数包装器逐次累加，包含重试/失败调用；因此它可以高于 T0 `runtime.ngspice_runs`，差异逐条列在下方对账节。

## 与 T0 `referenceResult` 对账

对账按 reference 的 gate（`referenceResult.metrics[].source_check`）对应到本次 verifier 的 CTRF gate；参考值、本次值、相对误差和状态一致性逐条写进 `results/reconcile.json`。本次值从 verifier 自己打印的 CTRF 消息中提取，并记录提取方式；出现多个候选数字时取与 T0 参考值最近的一个，因此若同一 gate 的实测值漂移超过容差，会落进不一致清单而不是被吞掉。

| 指标 | 值 | JSON 字段 |
|---|---:|---|
| 已对账任务 | 50 | `reconcile.summary.tasks` |
| 参考 gate 总数 | 452 | `reconcile.summary.reference_metrics` |
| 本次 verifier gate 总数 | 430 | `reconcile.summary.run_gates` |
| 可比值数 | 452 | `reconcile.summary.comparable_values` |
| 数值匹配（容差/打印精度） | 438 / 452 | `reconcile.summary.values_matched` |
| 其中容差内 | 437 | `reconcile.summary.values_within_tolerance` |
| 其中按打印精度一致 | 1 | `reconcile.summary.values_matched_by_printed_precision` |
| 其中按 gate 状态（零计数指标） | 14 | `reconcile.summary.values_matched_by_gate_status` |
| 已对账（数值或 gate 状态） | 452 / 452 | `reconcile.summary.values_resolved` |
| 状态一致 | 452 / 452 | `reconcile.summary.status_matches` |
| 值/状态不一致 | 0 | `reconcile.summary.mismatch_count` |
| 任务级 reward/gate 数不一致 | 0 | `reconcile.summary.run_level_mismatch_count` |
| T0 记录 sims 合计 | 2,297 | `reconcile.summary.reference_ngspice_runs` |
| 本地计数 sims 合计 | 2,429 | `reconcile.summary.local_ngspice_runs` |
| sims 不一致任务数 | 5 | `reconcile.summary.sim_difference_count` |
| T0 墙钟合计 | 3,349.9 s | `reconcile.summary.reference_wall_s` |
| 本地墙钟合计 | 2,718.5 s | `reconcile.summary.local_wall_s` |
| 墙钟可比任务数 | 50 | `reconcile.summary.wall_comparable_tasks` |
| 墙钟平均相对差 | 30.4% | `reconcile.summary.wall_mean_relative_difference` |
| verifier 墙钟可比任务数 | 39 | `reconcile.summary.verifier_wall_comparable_tasks` |
| verifier 墙钟平均相对差 | 30.0% | `reconcile.summary.verifier_wall_mean_relative_difference` |
| 值容差 | 1.0% | `reconcile.summary.tolerance` |

### sims 差异

本地计数是实际 ngspice 进程数；T0 `runtime.ngspice_runs` 为 `null` 表示参考记录未给出该字段，不是 0。

| 任务 | T0 sims | 本地 sims |
|---|---:|---:|
| `sky130-buf-ppsf-bs-gain1-sfdr90` | — | 20 |
| `sky130-flash-adc-4bit-50msps` | — | 3 |
| `sky130-fully-differential-ota-c-biquad-pvt` | 62 | 63 |
| `sky130-nmos-pass-ldo-0p4v-pvt-mc` | — | 104 |
| `sky130-ring-amp-3stage-gain8-400uw` | — | 4 |

### 墙钟对账

墙钟受机器负载与 Docker 启动开销影响，不设通过阈值；这里记录 T0 与本次的差值和相对差，`verifier 墙钟` 仅在本次来源为 `verifier` 时与 T0 自报值可比。

| 任务 | T0 墙钟 (s) | 本地墙钟 (s) | 相对差 | T0 verifier 墙钟 (s) | 本地 verifier 墙钟 (s) | 本地来源 |
|---|---:|---:|---:|---:|---:|---|
| `sky130-ahuja-compensated-two-stage-load-range-pvt` | 23.9 | 16.1 | 32.7% | — | 16.1 | harness |
| `sky130-analog-input-mux-8to1-pvt` | 11.4 | 13.7 | 20.0% | 11.0 | 13.3 | verifier |
| `sky130-bandgap-reference-pvt` | 101.1 | 73.3 | 27.4% | 100.7 | 73.0 | verifier |
| `sky130-beta-multiplier-reference-pvt-mc` | 49.2 | 35.0 | 28.8% | 48.9 | 34.7 | verifier |
| `sky130-bootstrap-driver-pt` | 360.0 | 251.2 | 30.2% | — | 251.2 | harness |
| `sky130-bootstrap-sampler-fft` | 35.1 | 24.3 | 30.6% | 34.7 | 24.0 | verifier |
| `sky130-bottom-plate-sampler-pvt` | 78.3 | 34.8 | 55.5% | 77.9 | 34.5 | verifier |
| `sky130-buf-ppsf-bs-gain1-sfdr90` | 59.3 | 43.1 | 27.3% | — | 43.1 | harness |
| `sky130-capless-ldo-1v0-20ma-pvt` | 15.4 | 9.5 | 38.6% | — | 9.5 | harness |
| `sky130-class-d-halfbridge-eff95-tt` | 216.3 | 150.4 | 30.5% | — | 150.4 | harness |
| `sky130-cml-divider-div2-range1gto10g` | 16.9 | 11.8 | 30.3% | 16.6 | 11.5 | verifier |
| `sky130-cml-tx-driver-28g-nrz-pvt` | 11.0 | 6.7 | 39.1% | 10.6 | 6.4 | verifier |
| `sky130-cmos-switched-resistor-dac-5bit-pvt` | 85.3 | 59.7 | 30.0% | 85.0 | 59.4 | verifier |
| `sky130-complementary-folded-cascode-ab-opamp-pvt` | 159.3 | 103.4 | 35.1% | 158.9 | 103.1 | verifier |
| `sky130-constant-gm-stable-gain-amplifier-pvt` | 41.0 | 28.4 | 30.6% | 40.6 | 28.1 | verifier |
| `sky130-current-starved-ring-vco-pvt` | 88.0 | 68.6 | 22.1% | 87.7 | 68.3 | verifier |
| `sky130-delta-sigma-adc-1bit-first-order-osr32-sndr40` | 39.4 | 29.4 | 25.3% | 39.0 | 29.1 | verifier |
| `sky130-fct-residue-amplifier-900msps` | 148.3 | 111.7 | 24.6% | 147.9 | 111.4 | verifier |
| `sky130-fd-sampling-feedback-ota-gain1-settling40ns-noise300uv-pvt-mc` | 104.8 | 64.3 | 38.6% | 104.4 | 64.0 | verifier |
| `sky130-fd-sampling-feedback-ota-gain8-settling10ns-noise1mv-pvt-mc` | 129.1 | 85.1 | 34.1% | 128.7 | 84.8 | verifier |
| `sky130-fd-two-stage-miller-opamp-gain60-pm60-noise50uv-pvt` | 33.8 | 21.2 | 37.1% | 33.4 | 21.0 | verifier |
| `sky130-flash-adc-3bit-pvt` | 56.4 | 40.6 | 28.0% | 56.4 | 40.3 | verifier |
| `sky130-flash-adc-4bit-50msps` | 200.3 | 132.8 | 33.7% | 199.5 | 132.5 | verifier |
| `sky130-folded-cascode-ota-gain130-pm60-noise50uv-pvt` | 30.7 | 31.3 | 1.8% | 30.7 | 31.0 | verifier |
| `sky130-fully-differential-ota-c-biquad-pvt` | 13.3 | 17.8 | 34.1% | 12.3 | 17.1 | verifier |
| `sky130-gilbert-cell-mixer-2p4ghz-pvt-mismatch` | 13.6 | 10.5 | 23.2% | 13.3 | 10.2 | verifier |
| `sky130-gmr-degen-amp-constant-gm-pvt` | 17.1 | 12.3 | 28.3% | — | 12.3 | harness |
| `sky130-gmr-degenerated-diff-amp-gain2-bw70m-power1p2mw-sdr60` | 5.3 | 3.7 | 29.6% | — | 3.7 | harness |
| `sky130-high-psrr-bandgap-reference-pvt` | 7.6 | 5.4 | 28.1% | 7.2 | 5.2 | verifier |
| `sky130-inductive-lna-2p4ghz-gain12-nf2-pvt` | 15.4 | 11.6 | 25.1% | 15.1 | 11.2 | verifier |
| `sky130-lc-vco-2ghz-pvt` | 58.0 | 52.4 | 9.6% | 58.0 | 52.4 | harness |
| `sky130-ldo-ota5-robust-pvt-mc` | 21.5 | 35.7 | 66.3% | 21.1 | 35.4 | verifier |
| `sky130-low-power-line-driver-pvt` | 88.7 | 66.9 | 24.6% | — | 66.9 | harness |
| `sky130-low-power-oscillator-2mhz-pvt` | 71.9 | 102.3 | 42.3% | 71.5 | 102.0 | verifier |
| `sky130-nmos-pass-ldo-0p4v-pvt-mc` | 15.0 | 27.6 | 83.6% | — | 27.6 | harness |
| `sky130-ota-5t-gain40-pm60-noise50uv-pvt` | 7.7 | 6.5 | 15.8% | — | 6.5 | harness |
| `sky130-pll-charge-pump-pvt` | 75.8 | 32.4 | 57.2% | 75.8 | 32.1 | verifier |
| `sky130-programmable-icc-iptat-current-mirror-pvt` | 3.8 | 3.1 | 19.1% | 3.4 | 2.7 | verifier |
| `sky130-regulated-charge-pump-10mhz-pvt` | 61.2 | 75.6 | 23.5% | 60.8 | 75.2 | verifier |
| `sky130-ring-amp-3stage-gain8-400uw` | 3.0 | 2.5 | 15.8% | 2.7 | 2.2 | verifier |
| `sky130-sar-adc-4bit-async` | 140.1 | 174.2 | 24.3% | 140.1 | 173.9 | verifier |
| `sky130-sar-adc-6bit-async` | 166.0 | 269.6 | 62.4% | 165.6 | 269.3 | verifier |
| `sky130-switched-capacitor-2to1-converter-pvt` | 110.6 | 86.4 | 21.9% | 110.2 | 86.0 | verifier |
| `sky130-telescopic-cascode-ota-pvt-fix` | 19.4 | 14.1 | 27.4% | 18.6 | 13.7 | verifier |
| `sky130-three-stage-nested-miller-ota-pvt` | 137.1 | 106.9 | 22.0% | 136.8 | 106.6 | verifier |
| `sky130-tia-zt8k-bw750m-noise5p-sfdr70-power5m-pt` | 11.8 | 13.7 | 15.9% | 11.5 | 13.4 | verifier |
| `sky130-transistor-divide-by-2` | 1.1 | 1.3 | 15.3% | 1.0 | 0.9 | verifier |
| `sky130-two-stage-miller-opamp-gain60-ugb200-pm60-noise50uv-pvt` | 35.9 | 28.8 | 19.7% | 35.5 | 28.5 | verifier |
| `sky130-two-stage-miller-opamp-gain70-ugb20-pm60-noise50uv-pvt` | 37.1 | 28.7 | 22.6% | 36.8 | 28.4 | verifier |
| `sky130-unregulated-charge-pump-10mhz-pvt` | 117.6 | 81.9 | 30.4% | 117.2 | 81.6 | verifier |

### 按打印精度一致的 gate

| 任务 | 参考 gate | 本次 gate | 参考值 | 本次值 | 相对误差 | 证据 |
|---|---|---|---:|---:|---:|---|
| `sky130-transistor-divide-by-2` | `maximum_duty_cycle_error` | `duty_cycle` | 0.108700 | 0.110000 | 0.011960 | high/low duty=49.9/49.89/49.89/50.1/50.11/50.11%; low/high=4.937e-07/1.8V |

### 按 gate 状态一致的零计数 gate

这些参考 metric 是 "0 errors / 0 mismatches" 型计数；verifier 消息没有打印该计数时，用 gate 自身的 pass/fail 作为 0 值证据，并在 `current_value_source` 标 `gate-status`。若 gate 失败且没有显式计数，会记为无法量化的不一致，而不是猜一个 0。

| 任务 | 参考 gate | 本次 gate | 参考值 | 本次值 | 本次来源 |
|---|---|---|---:|---:|---|
| `sky130-bandgap-reference-pvt` | `signoff_complete` | `complete_signoff` | 1.000000 | 1.000000 | gate-status |
| `sky130-beta-multiplier-reference-pvt-mc` | `signoff_complete` | `complete_signoff` | 1.000000 | 1.000000 | gate-status |
| `sky130-cml-tx-driver-28g-nrz-pvt` | `eye_sign` | `eye_sign` | 0.000000 | 0.000000 | gate-status:passed |
| `sky130-cml-tx-driver-28g-nrz-pvt` | `sensitivity_sign` | `sensitivity_sign` | 0.000000 | 0.000000 | gate-status:passed |
| `sky130-flash-adc-3bit-pvt` | `valid_output_levels_pass` | `valid_output_levels` | 1.000000 | 1.000000 | gate-status |
| `sky130-lc-vco-2ghz-pvt` | `output_operating_range_pass` | `pvt_output_operating_range` | 1.000000 | 1.000000 | gate-status |
| `sky130-lc-vco-2ghz-pvt` | `reference_compliance_pass` | `pvt_reference_compliance` | 1.000000 | 1.000000 | gate-status |
| `sky130-pll-charge-pump-pvt` | `pvt_current_accuracy_pass` | `pvt_current_accuracy` | 1.000000 | 1.000000 | gate-status |
| `sky130-pll-charge-pump-pvt` | `pvt_inactive_modes_pass` | `pvt_inactive_modes` | 1.000000 | 1.000000 | gate-status |
| `sky130-pll-charge-pump-pvt` | `representative_pulse_fidelity_pass` | `representative_pulse_fidelity` | 1.000000 | 1.000000 | gate-status |
| `sky130-pll-charge-pump-pvt` | `pvt_switching_speed_pass` | `pvt_switching_speed` | 1.000000 | 1.000000 | gate-status |
| `sky130-pll-charge-pump-pvt` | `pvt_power_pass` | `pvt_power` | 1.000000 | 1.000000 | gate-status |
| `sky130-sar-adc-4bit-async` | `code_mismatches` | `sample_hold_16_codes` | 0.000000 | 0.000000 | gate-status:passed |
| `sky130-sar-adc-6bit-async` | `code_mismatch_count` | `sample_hold_64_codes` | 0.000000 | 0.000000 | gate-status:passed |

### 对账不一致清单

空：每条参考 gate 都在容差、打印精度或 gate 状态内找到对应实测值，状态全部一致。

## 非通过项

空：`summary.failing` 为空。

## 字段映射

逐任务表：`status` ← `<task>.status`；`reward` ← `<task>.artifacts.reward`；`gates` ← `<task>.artifacts.tests_passed/tests_total`；`sims` ← `<task>.artifacts.ngspice_runs`；`verifier 墙钟` ← `<task>.artifacts.wall_clock_s`；`调度墙钟` ← `<task>.wall_s`。`summary.sweep_wall_s` 由 `started_utc` 与 `finished_utc` 之差生成；`summary.task_wall_sum_s` 是逐任务 `wall_s` 之和，二者在并行 sweep 下不相等，故分列。

对账表：任务数与 gate 数 ← `reconcile.summary` 同名字段；数值匹配数为 `values_matched`，等于 `values_within_tolerance` 与 `values_matched_by_printed_precision` 之和；`values_resolved` 再加 `values_matched_by_gate_status`；每条 gate 的 `reference_value`、`current_value`、`relative_error`、`status_match`、`current_value_source` 与原始 `message` 见 `reconcile.tasks[].gates[]`。
