# Analog Design Bench — 本地数据与求解基建

本目录保存 Analog Design Bench V2 的**可复现语料**，以及在其上构建
“通用尺寸求解基建”的计划与需求。数据用于离线校准。

本项目已从 `rsi-dev/related_files/` 独立为 `~/analog-design-bench`，
自带抓取脚本、完整性校验、清单与许可分层。

## 目录

```
data/
  tasks.json                 50 个任务的列表（id/title/category/通过计数）
  tasks/<task_id>.json       单任务详情：instruction、公开 bench、参考解、
                             referenceResult（含 runtime / metrics）、per-model trials
  runs/<task_id>.jsonl       该任务 24 模型 × 3 rollout 的 run 记录（每行一条）
  index.jsonl                抓取索引：key、http status、bytes、sha256
  leaderboard.json           站点榜单聚合（24 条）
  figures.json               6h 时间曲线（8 张图，含 harness/effort/skill）
scripts/
  fetch_t0.py                T0 抓取脚本（可重入，跳过已完成 key）
  check_t0.py                完整性校验 + 清单生成
src/adb_solve/               求解基建包（P1 已落地：解析/回写/评测/缓存/CLI）
configs/corners.toml         每个任务的 corner 矩阵（IaC，声明式、受版本控制）
tests/                       pytest 单测 + docker 端到端 smoke
manifests/
  t0.sha256                  104 个数据文件的 sha256
  t0.meta.json               快照日期、条数、体积、canary
docs/
  PLAN.md                    数据阶段 T0/T1/T2 与工具阶段 P1–P5
  REQUIREMENTS.md            通用求解基建的需求规格（核心文档）
LICENSE                      Apache-2.0（代码）
LICENSES/CC-BY-NC-4.0.txt    数据与 benchmark 内容
NOTICE                       许可分层与署名
DATA.md                      数据卡：来源、完整性、缺陷、刷新、限制
```

## 来源与口径

- 站点：`https://analog-design-bench.tokenzhang.com`，V2，`updated=2026-11-01`。
- 抓取时间：2026-11-02。API 无版本号，站点改版后需重抓并记录 `sha256`。
- 任务包亦可从 GitHub（`Arcadia-1/analog-design-bench`）或 Hugging Face
  （`Arcadia-2026/analog-design-bench`）获得；两者都**不含 rollout**。
- 许可：见下文“许可”一节与 `NOTICE`；数据含 canary 字符串，
  不得进入训练语料。

## T0 完整性校验（已通过）

| 检查 | 结果 |
|---|---|
| run 记录数 | **3,600 / 3,600**（50 任务 × 24 模型 × 3 rollout） |
| HTTP 200 | 3,600 |
| 二进制通过数 | **1,679**，与站点 `stats.binaryPasses` 一致 |
| 去重 key | 3,600（无重复） |
| 体积 | 18.3 MB（含最终网表） |
| 榜单复算 | 前 4 名 pass@1 与 avgMinutes 与站点逐位一致 |

## 求解基建（P1）

```bash
make test                     # 单测（无 docker 也能跑，docker 用例自动跳过）
make types                    # mypy --strict src/adb_solve
make p1                       # 50 个参考解：解析 + identity + 扰动回写 + check_circuit

# 单任务实际调用
PYTHONPATH=src python3 -m adb_solve vars   --netlist circuit.spi
PYTHONPATH=src python3 -m adb_solve check  --netlist circuit.spi --allow-ideal R C
PYTHONPATH=src python3 -m adb_solve eval   --netlist circuit.spi --bench benches/ \
    --values '{"supply": 1.8, "temperature": 27}' --process tt --jobs 4 --out report.json
PYTHONPATH=src python3 -m adb_solve apply  --netlist circuit.spi --cand cand.json --out new.spi
```

评测跑在 `ghcr.io/arcadia-1/analog-arena-sky130-tools`（ngspice-46 + Sky130 PDK）里，
每个 worker 一个常驻容器，deck 挂在 `/app`；缓存键是 `netlist + 渲染后的 deck + image id`。

## Corner 矩阵（IaC）

`configs/corners.toml` 是"每个任务在哪些 process × supply × temperature 上签核"的唯一事实源。
它由 `scripts/build_corners.py` 从证据（题面原文、bench deck、设计网表）生成，人工审阅后提交，
评测直接消费；`make corners` 检查它是否与证据一致（漂移即失败）。

```bash
make corners                  # 重新推导并与已提交文件比对，过期则报错
PYTHONPATH=src python3 -m adb_solve eval --netlist circuit.spi --bench benches/ \
    --corners configs/corners.toml --task sky130-lc-vco-2ghz-pvt --jobs 4 --out report.json
```

每条记录都带出处，**不允许静默猜测**：

| 字段 | 含义 |
|---|---|
| `provenance` | `instruction`（题面原句，带 `quote`）/ `deck`（deck 与网表自身）/ `assumption`（没找到证据，标记为假设） |
| `kind` | `cartesian`（穷举网格）/ `rows`、`pairs`（行式或配对压力点，非穷举）/ `none` |
| `unresolved` | 本资产无法提供的 deck 占位符（每 bench 的时序、MC 应力等），测试要求"渲染失败的恰好是这些名字" |
| `needs_review` | 仍需人工确认；`[meta]` 里给出总数，债务可计数、不隐藏 |

当前状态：50 个任务全部登记；36 个由题面、3 个由 deck 提供，11 个是显式假设；
43 个标记待审、33 个未解析占位符名（分布在 7 个任务）；展开后共 1,194 个签核点。

## 复现

```bash
python3 scripts/fetch_t0.py --tasks-only   # 50 个任务详情，约 7 s
python3 scripts/fetch_t0.py --workers 6    # 3,600 条 run，约 5 min
make check                                 # 复验并重写 manifests/
```

脚本可重入：`data/index.jsonl` 中 `status=200` 的 key 会跳过。
新增记录以追加方式写入，崩溃后重跑可能产生重复行，分析时按
`(task, model, run)` 去重。

## 已知数据质量问题

抓取原样保留，不做修补；分析时应显式处理：

- **285 / 3,600 条 run 没有 `result.gates`**（`verifier_status` 为
  `completed`=180、`verified`=99，另有 `budget_exhausted`=4、
  `checker_rejected`=2）。其中 4 条仍记为通过。中位时长 117.8 min，
  多数是顶到 2h 上限的失败。
- 至少 1 条记录自相矛盾：
  `sky130-beta-multiplier-reference-pvt-mc / claude-fable-5 / run-1`
  的 `frozen_verifier_reward=0`，但 `binary_pass=1`、`tests_passed=12/12`、
  外层 `pass=true`。
- 部分任务的轨迹会截断在只读步骤（见上一轮分析），step/duration 与最终
  网表可能不自洽。
- `reward` 与 `pass` 不是一一对应：`reward=1.0` 有 1,682 条，`pass` 为
  1,679 条，差额来自 `reward ≥ 0.95` 的四舍五入。

## 不在这里的东西

- **轨迹（T1）**：单 run 的 NDJSON 在
  `/api/v2/live/tasks/<id>/runs/<model>/run-<n>/trajectory`，按需分层抓取，
  全量约 2.4–2.5 GB。见 `docs/PLAN.md`。
- **6h 原始 run**：站点只暴露聚合曲线（`data/figures.json`），逐条 6h
  轨迹不可得。
- **agent 侧**：harness 版本与模型快照未发布，榜单 run 不可重跑。
- **判分环境**：在 GHCR 镜像
  `ghcr.io/arcadia-1/analog-arena-sky130-tools@sha256:668783dd…` 中，
  461 MB，已本地验证 class-D 参考解 5/5、reward=1.0。

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
- **模型输出**：`data/runs/*.jsonl` 中的 `circuit` 由第三方模型生成，
  再托管前需确认各提供方条款。

详见 `NOTICE` 与 `DATA.md`。
