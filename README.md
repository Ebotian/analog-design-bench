<!-- generated from docs/src/readme.md by scripts/build_docs.py; do not edit -->
# Analog Design Bench — 本地数据与求解基建

本目录保存 Analog Design Bench V2 的**可复现语料**，以及在其上构建“通用尺寸求解基建”的
计划与需求。数据用于离线校准。项目已从 `rsi-dev/related_files/` 独立为 `~/analog-design-bench`。

> 本文件与 `DATA.md`、`docs/*.md` 都是**渲染产物**：手改会在 commit 时被拒绝。
> 改文档请编辑 `docs/src/`，然后 `make docs`。

<!-- block: layout | tags: kind:usage,topic:corpus -->
## 目录

```
data/                 T0 语料（tasks/、runs/、index.jsonl、leaderboard.json、figures.json）
data/t1/              判分器语料（tests/、task.toml、results/、environment/）
scripts/              抓取、校验、判分、对账、文档渲染（见 `ls scripts/`）
src/adb_solve/        求解基建：解析/回写/评测/缓存/CLI
src/adb_docs/         文档事实解析、渲染、索引查询
configs/corners.toml  每任务 corner 矩阵（IaC，声明式、受版本控制）
results/              逐任务判分 JSON、sweep 汇总、逐 gate 对账
manifests/            t0/t1 的 sha256 与 meta
docs/src/             文档源（唯一手写处）
docs/facts.toml       事实注册表：只存提取器，不存值
docs/index.json       生成索引：tag → block → file:line
.githooks/            pre-commit → `make docs-check`
tests/                pytest 单测 + docker 端到端 smoke
```

<!-- block: origin | tags: kind:data,topic:corpus -->
## 来源与口径

- 站点：`https://analog-design-bench.tokenzhang.com`，V2，`updated=2026-11-01`；抓取时间 2026-11-02。
  API 无版本号，站点改版后需重抓并记录 `sha256`。
- 任务包亦可从 GitHub（`Arcadia-1/analog-design-bench`）或 Hugging Face
  （`Arcadia-2026/analog-design-bench`）获得；两者都**不含 rollout**。
- 许可：见下文“许可”与 `NOTICE`；数据含 canary 字符串，不得进入训练语料。

<!-- block: t0-check | tags: phase:T0,status:done,kind:evidence,topic:corpus -->
## T0 完整性校验（已通过）

| 检查 | 结果 |
|---|---|
| run 记录数 | **3,600 / 3,600**（50 任务 × 24 模型 × 3 rollout） |
| HTTP 200 | 全部 |
| 二进制通过数 | **1,679**，与站点 `stats.binaryPasses` 一致 |
| 去重 key | 无重复 |
| 快照体积 | 104 个文件，21.9 MB |
| 榜单复算 | 前 4 名 pass@1 与 avgMinutes 与站点逐位一致 |

<!-- block: p1-usage | tags: phase:P1,status:done,kind:usage,topic:solver -->
## 求解基建（P1–P3 筛选）

```bash
make test        # 单测（无 docker 也能跑）；make types = mypy --strict；make p1 = 参考解验收
PYTHONPATH=src python3 -m adb_solve vars  --netlist circuit.spi
PYTHONPATH=src python3 -m adb_solve eval  --netlist circuit.spi --bench benches/ \
    --values '{"supply": 1.8, "temperature": 27}' --process tt --jobs 4 --out report.json
PYTHONPATH=src python3 -m adb_solve apply --netlist circuit.spi --cand cand.json --out new.spi
# P2：护栏与预算
... eval --legality checker        # 跑镜像里的 check_circuit.py（策略取自 task.toml）
... eval --budget 600 --out report.json            # 硬上限
... eval --budget 1500 --resume report.json        # 只花剩余预算
make legality                      # 全部 T0 电路回放 → results/legality.json
# P3 求解：screen 出维度，solve 做 DE（探针 → 分阶段 → 全 PVT 验证）
... solve --bench benches/ --corners configs/corners.toml --task <id> \
    --screen results/screen/<id>.json --budget 3200 --jobs 12 --out result.json
# P3 筛选：OAT ±20%，交叉角点，输出 ≤15 维
... screen --bench benches/ --corners configs/corners.toml --task <id> \
    --budget 600 --jobs 12 --compact --out results/screen/<id>.json
```

回放基线：3,600 条电路中纯校验拒绝 3 条
（其中通过的设计 0 条），checker 另外拦下
219 条；**通过的设计被误杀
0 条**。checker 实测接受 `m=0.5`，因此整数倍率只是
搜索建议，不是合法性。筛选实测：18 参数任务 444 次仿真压到
12 维；232 参数任务 602 次仿真压到 15 维
（预算优先：先粗组、后参数）。求解器把搜索与**全 PVT 验证**分开报告，验证失败时把最差角点回灌搜索集。
OTA 在 ±20% 扰动后 3,276 次仿真仍差 CMRR -0.062：
角点已对上，缺的是搜索能力，因此不判为通过。

评测跑在 `ghcr.io/arcadia-1/analog-arena-sky130-tools`（ngspice-46 + Sky130 PDK）里，
每个 worker 一个常驻容器，deck 挂在 `/app`；缓存键是 `netlist + 渲染后的 deck + image id`。

<!-- block: corners | tags: phase:P1,status:debt,kind:usage,topic:corner -->
## Corner 矩阵（IaC）

`configs/corners.toml` 是“每个任务在哪些 process × supply × temperature 上签核”的唯一事实源。
`scripts/build_corners.py` 先读官方判分器 `tests/verify.py`（TOML SPEC 或 Python 常量），
只有判分器没有可用网格时才回退到题面原文、deck 与网表；`make corners` 检查漂移。

| 字段 | 含义 |
|---|---|
| `provenance` | `verifier`（判分器）/ `instruction`（题面原句，带 `quote`）/ `deck` / `assumption`（无证据，标记为假设） |
| `kind` | `cartesian`（穷举）/ `rows`、`pairs`（行式或配对压力点）/ `none` |
| `unresolved` | 本资产无法提供的 deck 占位符，测试要求“渲染失败的恰好是这些名字” |
| `needs_review` | 仍需人工确认；`[meta]` 给出总数，债务可计数、不隐藏 |

当前状态：50 个任务全部登记；`provenance` = verifier
31 / instruction 7 / deck
3 / assumption 9；`kind` = cartesian
19 / rows 14 / pairs 5
/ none 12；33 个待审、33
个未解析占位符名；14 个任务带 22 组
per-bench `coverage` 子集；展开共 1,218 个签核点。实测判分见
`docs/VERIFIER_RESULTS.md` 与 `results/`。

<!-- block: docs | tags: kind:usage,topic:docs -->
## 文档系统

文档分四层，`md` 是输出，不是输入：

| 层 | 位置 | 谁改 |
|---|---|---|
| 事实 | `docs/facts.toml` | 人；只写提取器（JSON 路径 / 函数），不写值 |
| 正文 | `docs/src/*.md` | 人；`{{fact:...}}` 占位符 + `<!-- block: id \| tags: ... -->` |
| 产物 | `docs/*.md`、`README.md`、`DATA.md`、`docs/index.json` | `make docs` |
| 历史 | git | `git log`；正文不写实现日志，也没有日期标题 |

```bash
make docs                                # 渲染全部文档与索引
make docs-check                          # 漂移检查（pre-commit 钩子跑这个）
python3 scripts/docs.py find phase:P2     # tag → block → file:line
python3 scripts/docs.py context phase:P2  # 只打印相关段落，省上下文
python3 scripts/docs.py show verifier.sims  # 事实的值、来源、被谁引用
make hooks                               # 安装 .githooks（core.hooksPath）
```

标签是闭集（`docs/tags.toml`）：`phase:` `status:` `kind:` `doc:` `topic:`；新增标签是一次 diff。

<!-- block: reproduce | tags: kind:usage,topic:corpus -->
## 复现

```bash
python3 scripts/fetch_t0.py --tasks-only   # 任务详情，约 7 s
python3 scripts/fetch_t0.py --workers 6    # 全部 run，约 5 min
make check                                 # 复验并重写 manifests/
```

脚本可重入：`status=200` 的 key 会跳过。新增记录以追加方式写入，崩溃后重跑可能产生重复行，
分析时按 `(task, model, run)` 去重。

<!-- block: data-gaps | tags: kind:gap,topic:corpus -->
## 已知数据质量问题

抓取原样保留，不做修补；分析时应显式处理：

- **285 / 3,600 条 run 没有 `result.gates`**，其中
  4 条仍记为通过；中位时长 117.8 min。
  状态分布见 `manifests/t0.meta.json` 的 `quality.verifier_status_counts`。
- 至少 1 条记录自相矛盾：`sky130-beta-multiplier-reference-pvt-mc / claude-fable-5 / run-1`
  的 `frozen_verifier_reward=0`，但 `binary_pass=1`、`tests_passed=12/12`、外层 `pass=true`。
- 部分轨迹截断在只读步骤，step/duration 与最终网表可能不自洽。
- `reward=1.0` 与 `pass` 一一对应（1,679 条），不存在四舍五入差额。

<!-- block: absent | tags: kind:gap,topic:corpus -->
## 不在这里的东西

- **轨迹（T2）**：单 run NDJSON 在 `/api/v2/live/tasks/<id>/runs/<model>/run-<n>/trajectory`，
  按需分层抓取，全量约 2.5 GB（见 `docs/PLAN.md`）。
- **6h 原始 run**：站点只暴露聚合曲线（`data/figures.json`）。
- **agent 侧**：harness 版本与模型快照未发布，榜单 run 不可重跑。
- **判分环境**：GHCR 镜像 `ghcr.io/arcadia-1/analog-arena-sky130-tools@sha256:668783dd…`，
  已本地验证 class-D 参考解 5/5、reward=1.0。

<!-- block: license | tags: kind:policy,topic:corpus -->
## 许可

本项目**完全采用上游 Analog Design Bench 的许可方案**：

| 内容 | 许可 | 归属 |
|---|---|---|
| `scripts/`、`src/`、`Makefile` | **Apache-2.0**（`LICENSE`） | Ebotian |
| `data/`（任务文本、参考解、bench、run、曲线） | **CC BY-NC 4.0**（`LICENSES/CC-BY-NC-4.0.txt`） | Analog Design Bench authors (Arcadia-1) |
| `docs/`、`README.md`、`DATA.md` | **CC BY-NC 4.0** | Ebotian，引用部分归上游 |

- **非商业**：benchmark 内容的商业使用需上游书面许可。
- **署名**：Analog Design Bench, Arcadia-1, arXiv:2609.33356。
- **不训练**：保留 canary，不得进入训练语料。
- **模型输出**：`data/runs/*.jsonl` 中的 `circuit` 由第三方模型生成，再托管前需确认各提供方条款。

详见 `NOTICE` 与 `DATA.md`。
