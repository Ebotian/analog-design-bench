<!-- generated from docs/src/plan.md by scripts/build_docs.py; do not edit -->
# 阶段计划

两条线并行：**数据线（T）**给证据，**工具线（P）**给能力。每个阶段都有可检查的完成标准；
实现过程不写在这里——它属于 git 历史（`git log -p -- docs/src`），正文只保留当前状态与证据指针。

## 数据线

<!-- block: t0 | tags: phase:T0,status:done,kind:goal,topic:corpus -->
### T0 — run 级语料（已完成）

- **目标**：拿到全部 run 的判定、时长、步数、token/成本、gate 明细与最终网表。
- **输出**：`data/runs/*.jsonl`、`data/index.jsonl`、`data/tasks/*.json`。
- **完成标准**：3,600 条记录、1,679 通过、与站点 `stats.binaryPasses`
  一致、榜单前 4 名可逐位复算——由 `make check` 复验。
- **用途**：run 级时间/步数分布；通过电路作为 sizing gym 样本池；近失败样本作为恢复基准。

<!-- block: t1 | tags: phase:T1,status:done,kind:goal,topic:verifier -->
### T1 — 判分器语料（已完成）

- **目标**：把每个任务的官方判分器与运行环境落到本地，使判分可复现。
- **输出**：`data/t1/`，50 个任务 / 889 个文件 / 2.58 MB，
  来源 commit `972f7cd`；`manifests/t1.sha256` 与 `t1.meta.json` 可校验。
- **完成标准**：`python3 scripts/fetch_t1.py --check` 通过。
- **边界**：只读；verifier 只作 oracle/校准与 A/B 的官方判分，不作优化目标，不改一行。

<!-- block: t2 | tags: phase:T2,status:open,kind:goal,topic:trajectory -->
### T2 — 分层轨迹（可选，未开始）

- **触发条件**：要做 per-task/per-model 时间模型，或训练“下一步动作”预测器。
- **抽样**：6 题（最难/25%/中位/75%/最简/class-D）× 8 模型 × run-1，约 48 条；
  需要时扩到 24 模型 × 1 run；全量约 2.5 GB。
- **完成标准**：能按模型/任务给出“LLM 秒数、工具秒数、步数、最大单步延迟”四元组，
  与站点 `avgMinutes` 对得上（±5%）。

## 工具线

<!-- block: p1 | tags: phase:P1,status:done,kind:goal,topic:solver -->
### P1 — 参数层与评测层（已完成）

- **目标**：把“改网表 → 跑仿真 → 读指标”做成稳定、可缓存、可并行的一次调用。
- **已交付**：无损解析与只改参数位的回写、参数抽取、bench×corner 评测与失败分类、
  `sha256` 缓存、`check_circuit.py` 包装；CLI `vars/check/eval/apply/schema`。
- **验收**：50 个参考解全部解析、回写并过 checker（1 个已记录例外）；
  官方 `test.sh` 全过（50 题 / 430 gate /
  2,429 次 ngspice），与 `referenceResult` 逐 gate 对账见
  `docs/VERIFIER_RESULTS.md`。
- **边界**：verifier 只作校准与 A/B 官方判分，不作优化目标；不改、不外发。

<!-- block: corners | tags: phase:P1,status:debt,kind:evidence,topic:corner -->
#### Corner 矩阵（IaC）

`configs/corners.toml` 是每任务签核网格的唯一事实源，由 `scripts/build_corners.py` 从
判分器 SPEC（`[pvt]`/`[coverage]`）生成，`make corners` 检查漂移。

- 规模：50 个任务，展开 1,218 个签核点。
- 出处：verifier 31 / instruction 7 /
  deck 3 / assumption 9。
- 网格：cartesian 19 / rows 14 /
  pairs 5 / none 12。
- 声明债务：33 个待审、33 个未解析占位符名；
  14 个任务带 22 组 per-bench coverage 子集。
- 纪律：不静默猜测；deck 渲染失败的占位符集合必须恰好等于声明的 `unresolved`。

<!-- block: p2 | tags: phase:P2,status:done,kind:goal,topic:guardrail -->
### P2 — 合法性护栏与预算（已完成）

- **目标**：不让优化器提出非法或越界的设计，不让一次调用烧掉全部预算。
- **合法性**：`precheck` 是纯函数，校验未知参数、不可写参数与 checker 无条件拒绝的规则；
  `--legality checker` 再跑镜像内的 `check_circuit.py`。任务自己的 `task.toml` 决定
  `checker_allow_ideal` / `require_subcircuit`，不靠猜。
- **预算**：每次 eval 记 sims（缓存命中不计），`--budget` 是跨调用的硬上限，超限返回
  部分结果并列出 `skipped`；`--resume report.json` 只花剩余预算。
- **资源**：容器 `--memory`（默认 4g，与 task.toml 的 `memory_mb` 一致），每个 worker 写
  `.spiceinit` 固定 `num_threads=1`。
- **回放证据**（`results/legality.json`）：3,600 条 T0 电路中，纯校验拒绝
  3 条；全部跑 checker（3,600 条）后另有
  219 条只被 checker 拦下；**通过的电路误杀
  0 条**；72 条属于自带
  `check_area.py` 预处理的任务，跳过 checker 并记录在报告里。
- **已定口径（实测修正）**：合法性 ≠ 搜索盒。checker 实测接受 `m=0.5`、`nf=1.5`，
  所以整数倍率降级为**搜索建议**，`apply` 只硬校验正值与非负面积；几何范围是可选开关
  （参考解中多数超出它，例如 `w=50 m=16`）。
- **未做**：拒绝后的自动修复（取整/夹取）归 P3；本轮只拒绝，并给出可定位的违规项。

<!-- block: p3 | tags: phase:P3,status:open,kind:goal,topic:solver -->
### P3 — 筛选与求解（≈5–8 天）

- **目标**：把中位 75.5 个可写参数压到 10–15 维，再用成熟的全局优化收口。
- **内容**：OAT 敏感度（±20%，nominal + 2 corner）、相关性分组、层次参数化、
  DE（粗层）/CMA-ES（≤30 维）/局部抛光、离散量取整修复、分阶段预算（廉价筛选 → 前 5 名全 PVT）。
- **完成标准**：sizing gym 上简单任务 ≥ 90% 恢复、困难任务 ≥ 30%，中位 sims-to-pass ≤ 1,500；
  消融（去筛选/去分组/去缓存）各掉多少 pp 有数。
- **风险**：对公开 bench 过拟合；必须用留出种子/角落验证。

<!-- block: p4 | tags: phase:P4,status:open,kind:goal,topic:cli -->
### P4 — 接口层（CLI + MCP，≈2 天）

- **目标**：任意 harness、任意模型，用同一条路径调用。
- **内容**：8 个动词 `vars/screen/eval/suggest/apply/check/solve/report`；无状态文件进出；
  `schema` 自描述；预算与进度写入 `report.json`，harness 不解析 stdout。
- **完成标准**：2 个 harness 各跑通 1 题；模型只读 `--help`/schema 即可用。
- **顺序**：P3 之后再做——接口应围绕稳定语义，而不是反过来。

<!-- block: p5 | tags: phase:P5,status:open,kind:goal,topic:validation -->
### P5 — 验证：sizing gym 与 agent A/B（≈3–5 天）

- **目标**：回答“瓶颈是不是数值闭合”，而不是“工具好不好用”。
- **Gym**：起点为 50 个参考解 + 3,600 个 T0 电路；扰动 20–50%；
  指标为恢复率、sims-to-pass、非法率、失败模式；基线为随机/OAT/DE/DE+筛选/缓存。
- **A/B**：2 模型（强/弱）× 2 harness × 20 题 × 3 rollouts，2h 与 6h 两个截点。
- **止损**：gym 困难任务恢复率 < 30%（1,500 sims 内）或 A/B 6h 增益 < 5 pp，
  即判定“数值闭合不是主要瓶颈”，停止投入。

## 顺序与依赖

```
T0 ✅ ──► P1 ✅ ──► P2 ──► P3 ──► P5
                      │             │
                      └──► P4 ──────┘
T1 ✅（判分器语料）    T2（可选，解释用）
```

## 工作量口径

单人估算，不含等待镜像/网络的时间；P3 的不确定性最大（优化器调参），
若 gym 恢复率不达标，应在 P5 的判定点止损，而不是继续加算力。
