# 通用尺寸求解基建 — 需求规格

状态：草案 v0.1（2026-11-02）
范围：Analog Design Bench V2 的“数值闭合”环节；不涉及拓扑生成、版图、PDK。

---

## 0. 背景与假设

已测事实（来源见 `README.md` 与轨迹分析）：

- 50 个参考解中 49 个是字面量网表，没有 `.param`；数值旋钮中位 **63**，
  p90 135，最大 226。
- 强模型在硬任务上做 **11 版**手工修改、4 次 120-corner 全量复测，
  约 **563 次 ngspice**；判分器只需 **143 次**。
- 同一任务上，模型从 19.6 分钟到 119 分钟不等；失败者多顶到 2h 上限。
- 受控曲线：harness 的 spread 从 2h 的 16 pp 收缩到 6h 的 6 pp；
  模型/effort 的 spread 为 60–76 pp。强模型 6h 曲线在 4h 后已平。

**核心假设 H1**：在这些任务上，剩余失败的主要成分是“给定拓扑后的数值闭合”，
而不是拓扑选择或工具速度。若 H1 成立，一个拓扑无关的求解基建应显著提高
通过率，且增益在 2h 大于 6h、对弱模型大于强模型。

**反证条件**（任一成立即需修正 H1）：

- F1：sizing gym 上，困难任务的恢复率 < 30%（1,500 sims 内）；
- F2：agent + tool 在 6h 的通过率增益 < 5 pp；
- F3：工具只提高公开 bench 的通过率，隐藏 gate 不涨（过拟合）。

---

## 1. 范围

### 在范围内

- 对**任意给定网表**做参数化、评测、筛选、优化、回写。
- 面向 agent 的 CLI/MCP 接口；跨模型、跨 harness。
- 合法性护栏、预算记账、缓存、报告。
- 离线校准（sizing gym）与 agent A/B 的度量。

### 不在范围内

- 拓扑生成/替换（由 skill 8 一类“参考拓扑”覆盖）。
- 版图、寄生提取、PDK 修改。
- 读取或推断隐藏阈值；工具只使用公开 spec 与公开 bench。
- 修改判分器或环境镜像。

---

## 2. 数据模型

```text
Design   = { netlist, params[], source_path }
Param    = { id, kind, device, subckt_path, value, unit,
             bounds{min,max,grid}, group, writable }
Bench    = { id, deck_path, corners[{process,supply,temp,seed}], measures[] }
Gate     = { id, metric, direction(min|max|interval|valid), threshold,
             aggregate(worst|all|mean), unit }
Metrics  = { bench, corner, measures{name: float}, status(ok|nc|missing|illegal) }
Report   = { design_hash, params, gates[], margins[], sims, budget,
             candidates[], history[], seed, tool_version }
```

约定：

- 参数一律归一化为 SI 基本单位后存储，回写时按原单位格式化。
- `writable=false` 用于 PDK 固定件、理想源、测试激励。
- `group` 由自动分组产生，可被 manifest 覆盖（人工冻结）。
- 所有浮点比较带相对容差 `1e-9`；报告保留原始精度。

---

## 3. 功能需求

| ID | 需求 | 验收 |
|---|---|---|
| **FR-1** | 解析 SPICE 网表：器件、端口、子电路、参数、注释；保留原始行与列 | 50/50 参考解可解析，零异常 |
| **FR-2** | 无损回写：只改参数位 | 回写后 `diff` 仅落在参数 token；重解析参数值一致 |
| **FR-3** | 参数抽取：`w/l/m/nf`、`R/C/L value`、源 `dc`；单位与量级规范化 | 与人工抽查 3 个任务一致 |
| **FR-4** | 自动分组：按 `(subckt_path, device_type)` 初分；用敏感度相关性合并共线组 | 组数落在 5–20；镜像对同组 |
| **FR-5** | 评测：bench × corner 并行；每 sim 超时；失败分类为 `nc/missing/illegal` | 与 `reference.json` 的 gate 结果逐项一致 |
| **FR-6** | 指标抽取：从 `.meas` 输出解析；缺失即失败（不得当作通过） | 缺失场景有单测 |
| **FR-7** | Gate 计算：worst-case 跨 corner；区间/有效性条件；权重可配 | 与 `verify.py` 在 50 个参考解上一致 |
| **FR-8** | 筛选：OAT ±20%，nominal + 2 corner；输出敏感度排序与推荐维度 | 63 维 → ≤15 维，且保留所有失败 gate 相关组 |
| **FR-9** | 优化：DE（粗层）、CMA-ES（≤30 维）、局部抛光；离散量取整+修复 | 固定 seed 可复现；预算内返回最优 |
| **FR-10** | 合法性：调用 `check_circuit.py`；几何/模型范围/nf/m 整数；拒绝并修复 | 3,600 条 T0 回放，非法提案率 < 1% |
| **FR-11** | 预算：`--budget N` 硬上限；记账；耗尽时返回部分结果 | 超限不越界；报告含 `sims` |
| **FR-12** | 缓存：`sha256(netlist, bench, corner, seed)` → metrics | 重复评测命中率 > 80% |
| **FR-13** | 报告：JSON，含每候选的 gates/margins/sims；stdout 只放摘要 | schema 校验通过 |
| **FR-14** | 接口：CLI 与 MCP 同语义；`schema` 自描述 | 两个 harness 各跑通 1 题 |

### 目标函数（FR-9 细化）

对每个 gate `g` 与 corner `c`，定义归一化余量

```
m_g(x,c) = (value(x,c) - threshold) / scale_g      （方向已统一为“越大越好”）
w_g(x)   = min_c m_g(x,c)                           （worst-case）
```

标量损失：

```
L(x) = Σ_g softplus(-w_g(x)/τ)                      # 违反惩罚
     + λ_fail * (# 仿真失败)                         # 失败不是差分数
     + λ_reg  * ||log(x/x0)||²                       # 留在原设计附近
```

- 若存在 `w_g < 0`：先最小化 `max_g(-w_g)`（可行性优先）。
- 若全部 `w_g ≥ 0`：最小化 `Σ_g exp(-w_g/τ)`（最大化最坏余量）。
- `τ`、`λ_*`、`scale_g` 写入配置，不硬编码。

### 分阶段预算（FR-11 细化）

| 阶段 | bench 范围 | 预算 | 目的 |
|---|---|---|---|
| A 筛选 | nominal + 2 corner | 100–200 sims | 排序、降维 |
| B 粗搜 | 5–8 corner | 300–600 sims | 找可行域 |
| C 全 PVT | 全部 corner + MC | 5 候选 × 1 次 | 收口与验证 |
| D 抛光 | 全部 | 100–200 sims | 最坏余量最大化 |

默认总预算 1,500 sims，可按任务配置；报告中给出各阶段实际消耗。

---

## 4. 接口

### CLI

```bash
adb-solve schema  --netlist circuit.spi --bench-dir testbench --out schema.json
adb-solve vars    --netlist circuit.spi
adb-solve eval    --netlist circuit.spi --bench tb_nominal --corner tt/1.80/27
adb-solve screen  --netlist circuit.spi --top 12
adb-solve suggest --netlist circuit.spi --budget 600 --seed 7 --out cand.json
adb-solve apply   --netlist circuit.spi --cand cand.json --out circuit.new.spi
adb-solve check   --netlist circuit.spi
adb-solve solve   --netlist circuit.spi --budget 1500 --seed 7 --out report.json
```

### MCP

同名工具，参数/返回与 CLI 一致；`solve` 为长任务，需支持进度事件与取消。

### 约定

- 输入输出都是文件，进程无状态；同一输入 + 同一 seed = 同一输出。
- 不读隐藏 gate、不读参考解；边界来自 PDK/checker。
- `solve` 退出码：0 = 找到全通过；2 = 预算耗尽但有可行候选；3 = 无可行候选。

---

## 5. 非功能需求

| 类别 | 要求 |
|---|---|
| 离线 | 运行期无网络；镜像内只依赖 ngspice、python、numpy |
| 确定性 | 固定 seed；并行不改变结果顺序；缓存不影响结果 |
| 性能 | 单次 `eval` 开销 < 50 ms（不含 sim）；缓存命中 < 10 ms |
| 可恢复 | 中断后可从 `report.json` 续跑；不丢已花预算 |
| 可观测 | 每次 sim 记 `(bench, corner, status, seconds)`；可导出 |
| 安全 | 不修改 PDK/测试文件；只写 `circuit.spi` 与输出目录 |
| 许可 | 不打包 benchmark 内容；只依赖本地镜像与用户提供的网表 |

---

## 6. 验证

### 6.1 sizing gym（不依赖 LLM）

- **起点**：50 个参考解 + T0 的 3,600 个电路（含 1,679 个通过）。
- **扰动**：对 `writable` 参数做 20–50% 对数均匀缩放；保证合法。
- **任务**：从扰动点恢复到全 gate 通过。
- **指标**：恢复率、`sims-to-pass` 中位数、非法率、失败模式分布。
- **基线**：随机搜索 / OAT / DE / DE+筛选 / DE+筛选+缓存。
- **分层**：按任务通过率（<25%、25–50%、>50%）报告，不报单一均值。

### 6.2 agent A/B

- 2 模型（强/弱）× 2 harness × 20 题 × 3 rollouts。
- 臂：`no-tool` / `tool`；截点 2h 与 6h。
- 指标：pass@1、sims、tokens、分钟、pass-then-regress 率。
- 统计：配对 bootstrap 95% CI；报告绝对值与相对增益。

### 6.3 验收阈值（初版，可调）

- 简单任务恢复率 ≥ 90%，困难任务 ≥ 30%；
- 中位 `sims-to-pass` ≤ 1,500；
- 非法提案率 < 1%；
- agent A/B 的 2h 增益 ≥ 8 pp 且 CI 下界 > 0，才继续投入 P4/P5。

---

## 7. 风险与对策

| 风险 | 表现 | 对策 |
|---|---|---|
| 过拟合公开 bench | 公开通过、隐藏不通过 | 只用 spec 阈值；留出 seed/corner；隐藏集复核 |
| 分组语义错误 | 镜像对被拆开，优化器乱调 | 敏感度相关性合并；人工冻结 manifest 仅作覆盖 |
| 仿真失败主导 | 优化器钻不收敛区 | 失败记为 infeasible；修复/退避；必要时收缩边界 |
| 搜索维度仍太高 | DE 收敛慢 | 层次参数化 + 筛选；先粗后细 |
| 工具替代了能力 | 榜单区分度下降 | 双臂报告；主榜保持 no-tool |
| 预算被打穿 | 单题跑几小时 | 硬上限 + 分阶段；超限返回部分结果 |
| 复现性 | 浮点/并行导致差异 | 固定 seed、固定顺序、记录版本与 hash |

---

## 8. 开放问题

1. **gate 权重**：等权还是按 spec 重要性？先等权，报告里给敏感性。
2. **有效性条件**（如“无工作点”）如何进入连续目标？当前按失败处理。
3. **离散拓扑改动**（增减器件）不在范围内；若 gym 显示拓扑是瓶颈，需另立项。
4. **多目标权衡**（功耗 vs 噪声）是否需要 Pareto 前沿？先做可行性，后做前沿。
5. **跨任务迁移**：能否把“哪类参数敏感”的先验跨任务复用？先不做，避免泄漏。

---

## 9. 交付物

- `adb-solve` CLI（P1–P3）
- MCP server（P4）
- `schema.json` / `report.json` 的 JSON Schema
- sizing gym 脚本与基线结果（P5）
- 一份“工具 on/off”的 A/B 报告（P5）
