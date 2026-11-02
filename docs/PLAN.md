# 阶段计划

两条线并行：**数据线（T）**给证据，**工具线（P）**给能力。
每个阶段都有可检查的完成标准，避免“做完感觉不错”。

---

## 数据线

### T0 — run 级语料（✅ 已完成）

- **目标**：拿到 3,600 条 run 的判定、时长、步数、token/成本、gate 明细和
  **最终网表**。
- **输入**：站点 `/api/v2/live/tasks/<id>/runs/<model>/run-<n>`。
- **输出**：`data/runs/*.jsonl`、`data/index.jsonl`、`data/tasks/*.json`。
- **完成标准**：3,600 条、1,679 通过、与站点 `stats.binaryPasses` 一致、
  榜单前 4 名可逐位复算。**已满足**。
- **用途**：
  1. run 级时间/步数分布，替代榜单均值；
  2. 1,679 个通过电路 + 1,921 个失败/近失败电路，作为 sizing gym 的样本池；
  3. 近失败样本（reward 0.8–0.99）用于“从接近可行处恢复”的基准。

### T1 — 分层轨迹（待做，≈1 天）

- **目标**：解释时间去哪了：模型延迟 vs 工具等待 vs 轮询/排队。
- **抽样**：6 题（最难 / 25% / 中位 / 75% / 最简单 / class-D）× 8 模型 ×
  run-1 = 48 条，约 33 MB；需要时扩到 24 模型 × 1 run = 144 条（≈100 MB）。
- **输出**：`data/traj/<task>/<model>-run-1.ndjson` + 每步
  `request_time/response_time/tool_call` 的派生表 `data/steps.parquet`。
- **完成标准**：能按模型/任务给出“LLM 秒数、工具秒数、步数、最大单步延迟”
  四元组；与站点 `avgMinutes` 对得上（±5%）。

### T2 — 全量轨迹（可选，≈2.5 GB）

- **触发条件**：只有要做 per-task/per-model 时间模型、或训练“下一步动作”
  预测器时才做。
- **成本**：3,600 次请求，P6 约 5–10 分钟；解析 2.5 GB NDJSON。
- **注意**：对公共站点要限速；机器人条款允许，但不宜反复全量重抓。

---

## 工具线

### P1 — 参数层与评测层（🚧 进行中：解析/回写/评测已可跑）

- **目标**：把“改网表 → 跑仿真 → 读指标”变成一条稳定、可缓存、可并行的调用。
- **内容**：
  - SPICE 解析：器件、端口、参数、子电路；保留原始行以便无损回写。
  - 参数抽取：`w/l/m/nf`、`R/C/L value`、源 `dc`；单位规范化。
  - 回写：只改数字，`diff` 必须只落在参数位。
  - 评测：bench × corner 矩阵，并行，超时，失败分类（不收敛/缺测量/非法）。
  - 缓存：`sha256(netlist, bench, corner, seed)` → metrics。
  - 输出：JSON metrics（与 gate 同名），不返回原始日志。
- **完成标准**：
  - 50 个参考解全部能抽取 + 回写 + 通过 `check_circuit.py`；
  - 回写后重新评测，gate 结果与 `reference.json` 一致；
  - 缓存命中率 > 80%（同一设计重复评测时）。
- **依赖**：无。**这是唯一“只赚不赔”的阶段。**

#### 实现记录（2026-11-03）

已落地于 `src/adb_solve/`，`make test types p1` 可复现：

| 模块 | 职责 | 验收证据 |
|---|---|---|
| `numbers` | SPICE 数值：尾数/指数/单位后缀，精度不够则加位数而非四舍五入 | 回写保真性质测试（7 种字面量 × 5 种倍率） |
| `netlist` | 无损解析：原始行/列、`;`/`$` 行内注释、`+` 续行、`.subckt` 作用域 | identity 渲染逐字节一致 |
| `params` | 参数抽取（SI 存储）、分组、搜索盒、硬合法性、只改参数位的回写 | 50/50 任务 diff 只落在参数 token |
| `bench` | 四种占位符 `{x}` `%X%` `__X__` `@@X@@`、`.lib` 工艺角与 `.temp` 重写 | 语料实测 104/177 个 deck 带占位符 |
| `sim` / `docker_backend` | case/result、失败分类、常驻容器（~40 ms/次） | 单测 + docker smoke |
| `evaluate` / `cache` | bench × corner 矩阵、`sha256(netlist, deck, image id)` 缓存 | 重复评测命中率 100% |
| `check` | `/opt/analog-arena/check_circuit.py` 包装 | 50/50 通过（1 个已知例外） |

实机验证：`sky130-lc-vco-2ghz-pvt` 的 `tb_tune` 在 tt/27/1.8 下
`p1_freq=2.140 GHz`、`power_avg=1.89 mW`、`startup_time=12.5 ns`，与参考指标同量级。

P1 期间确认的三件事（会影响 P2/P3）：

1. `check_circuit.py` 的**几何范围只在 `--enforce-sky130-geometry` 下生效**，而 32/50 个参考解超出该范围
   （如 `w=50 m=16`），说明真实 verifier 不启用它。故“搜索盒”与“合法性”必须分开：
   回写只校验 checker 无条件拒绝的东西（正值、整数倍率、非负面积），模型范围只作搜索建议。
2. 顶层 `.param` 会被共享 checker 拒绝，但 `sky130-bootstrap-driver-pt` 的题面把 `cboot` 声明为设计变量，
   记为已知例外（`scripts/check_p1.py` 里显式列出理由）。
3. deck 分三类（177 个）：118 个用 `.control` 打印 `name = value`、9 个带 `.meas`
   （这两类能直接从 stdout 取指标）；45 个只 `.save`，**产物是波形文件而不是文本**
   （加 `-r` 才跑，`.meas` 输出会被 `-r` 吞掉，两种必须分开走）；另外 5 个是
   `fixtures/*.spi`，本来就不是测试台（供别的 deck include）。我们最初把后两类
   合称“无输出”，是错的——波形同样是指标来源，只是读取方在 verifier 里。

#### corner 矩阵已作为 IaC 落地

`configs/corners.toml` + `src/adb_solve/corners.py`（schema/校验/展开）+
`src/adb_solve/evidence.py`（从题面、deck、网表抽取）+ `scripts/build_corners.py`（生成与漂移检查）。
纪律：`make corners` 在证据与已提交文件不一致时失败；`[meta]` 的 `needs_review` 与
`unresolved_values` 必须等于实际计数；测试要求"deck 渲染失败的占位符集合恰好等于该任务声明的
`unresolved`"，因此已声明的缺口无法掩盖未声明的缺口。

**已按判分器重建（2026-11-02）**。`src/adb_solve/verifier_spec.py` 先读
`tasks/<id>/tests/verify.py`：4 个任务有 TOML SPEC（`[pvt]`/`[coverage]`），27 个用
Python 常量或点列表声明同一组条件；AST 只解释字面量、容器与 `product`，不执行 verifier。
其余 19 个没有可用网格，才回退到题面/deck，并保留待审标记。

当前状态：50 个任务全部登记；provenance = verifier 31 / instruction 7 / deck 3 /
assumption 9；kind = cartesian 19 / rows 14 / pairs 5 / none 12；33 个待审、
33 个未解析占位符名（7 个任务）；14 个任务带 22 组 per-bench `coverage` 子集；
展开共 1,218 个签核点。`pairs`/`rows` 是压力点子集，展开成笛卡尔积属于保守超集，
故这些任务保持 `needs_review`。

实机验证：`adb-solve eval --corners configs/corners.toml --task sky130-lc-vco-2ghz-pvt`
在声明的 27 个点上全部 `ok`（4 并发 5m19s，其中 1 个命中缓存；p1_freq 2.135–2.146 GHz，
与参考指标 `minimum_zero_control_frequency = 2.1128 GHz` 同量级）。

#### 官方判分实测（2026-11-02）

`scripts/run_verifiers.py` 用每个任务的官方 `tests/test.sh` 跑 T0 参考解，
`scripts/reconcile_verifiers.py` 与 `referenceResult.metrics` 逐 gate 对账。结果：

- 50/50 `passed`，430 个 verifier gate，2,429 次 ngspice 仿真（沙箱 PATH 上的
  计数包装器逐次累加，含重试/失败调用；T0 记录合计 2,297，5 个任务有差异）；
- sweep 墙钟 760.0 s，逐任务墙钟合计 2,771.7 s，verifier 墙钟合计 2,750.0 s
  （39 个任务自报、11 个回退到调度墙钟），CPU 平均 39.6% / 峰值 97.2%；
  结束后残留容器与 ngspice 线程均为 0；
- 452 条参考 gate 全部匹配：451 条在 1% 容差内，1 条受 verifier 打印精度限制；
  状态一致 452/452，值/状态不一致 0，任务级 reward/gate 数不一致 0。

逐任务 JSON 在 `results/verifier/`，对账 JSON 在 `results/reconcile.json`，
说明文档 `docs/VERIFIER_RESULTS.md` 由 `scripts/build_results_md.py` 只读这些 JSON 生成。
仍存缺口：11 个 verifier 不自报墙钟（已回退到调度墙钟并标 `wall_clock_source`）；
33 个待审 corner 条目与 33 个未解析占位符名未清零。

边界（不变）：verifier 只作 **oracle/校准** 与 A/B 的官方判分，**不作优化目标**（否则是
F3 过拟合）；不修改 verifier；数据 CC-BY-NC，仅本地使用、不打包分发。

### P2 — 合法性护栏与预算（≈2 天）

- **目标**：不让优化器提出非法或越界的设计，不让一次调用烧掉全部预算。
- **内容**：
  - 复用镜像内 `/opt/analog-arena/check_circuit.py`；调用前先做几何/模型范围检查。
  - 边界来自 PDK 与 checker，**不来自参考解**（避免泄漏）。
  - 预算记账：每次 eval 记 sims；`--budget` 硬上限；超限返回部分结果。
- **完成标准**：在 3,600 条 T0 电路上回放，非法提案率 < 1%；
  预算耗尽时进程可恢复、报告完整。

### P3 — 筛选与求解（≈5–8 天）

- **目标**：把 63 维（中位）压到 10–15 维，再用成熟的全局优化收口。
- **内容**：
  - OAT 敏感度：每个粗组 ±20%，nominal + 2 corner，一次一变量。
  - 相关性分组：效应向量共线的组（镜像对、尾电流）合并。
  - 层次参数化：粗层组缩放（5–15 维）→ 细层逐器件（20–60 维）→ 局部抛光。
  - 优化器：DE（粗层）、CMA-ES（≤30 维）、Nelder-Mead/trust-region（抛光）。
  - 离散量：`m/nf` 取整 + 修复；W/L 走合法网格。
  - 目标：最坏 corner 的 gate 余量；可行性优先；仿真失败记为 infeasible。
  - 分阶段预算：廉价筛选 → 前 5 名跑全 PVT。
- **完成标准**：
  - 在 sizing gym（见 P5）上，简单任务 ≥ 90% 恢复，困难任务 ≥ 30%，
    中位 sims-to-pass ≤ 1,500；
  - 消融：去掉筛选、去掉分组、去掉缓存，各掉多少 pp 有数。
- **风险**：优化器在公开 bench 上过拟合；必须用留出种子/角落验证。

### P4 — 接口层（CLI + MCP）（≈2 天）

- **目标**：任意 harness、任意模型，用同一条路径调用。
- **内容**：
  - 6 个动词：`vars / screen / eval / suggest / apply / check`，
    外加 `solve`（一次性跑完）与 `report`。
  - CLI：`adb-solve ...`；MCP server：同语义。
  - 无状态：读 `circuit.spi`，写 `circuit.spi` + `report.json`；自动备份。
  - `schema` 子命令：返回变量、边界、gate、bench 矩阵，供弱模型自描述使用。
  - 预算与进度写入 `report.json`，harness 不需要解析 stdout。
- **完成标准**：在 2 个 harness（如 Codex/Pi 与 Claude Code）里各跑通 1 个任务；
  模型只读 `--help`/schema 即可用。

### P5 — 验证：sizing gym 与 agent A/B（≈3–5 天）

- **目标**：回答“瓶颈是不是数值闭合”，而不是“工具好不好用”。
- **Gym**（不依赖 LLM）：
  - 起点：50 个参考解 + T0 的 3,600 个电路。
  - 扰动：在合法区间内随机缩放 20–50%；保留 gate 结果。
  - 指标：恢复率、sims-to-pass、非法率、失败模式。
  - 基线：随机搜索、OAT、DE、DE+筛选、DE+筛选+缓存。
- **Agent A/B**：
  - 2 模型（一个强、一个弱）× 2 harness × 20 题 × 3 rollouts。
  - 臂：no-tool / tool；2h 与 6h 两个截点。
  - 指标：pass@1、sims、tokens、分钟、回归率。
- **完成标准**：
  - 能给出“工具把 2h 通过率提高 X pp、把模型间 spread 压缩 Y pp”的区间估计；
  - 若 X < 5 pp 且 gym 恢复率低，判定“数值闭合不是主要瓶颈”，停止投入。

---

## 顺序与依赖

```
T0 ✅ ──► P1 ──► P2 ──► P3 ──► P5
          │              │
          └──► P4 ───────┘
T1（可选，解释用）        T2（可选，建模用）
```

- P1 先做：它独立于优化器，直接消掉过度复测与日志解析。
- P3 之前不要做 P4：接口应围绕稳定语义，而不是反过来。
- T1 可与 P1 并行，不阻塞。

---

## 工作量口径

以上为单人估算，不含等待镜像/网络的时间；P3 的不确定性最大（优化器调参），
若 gym 恢复率不达标，应在 P5 的判定点止损，而不是继续加算力。
