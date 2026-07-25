# W5-S5 跨工作流整合审查 — Round-2 执行层

> 片：`w5-s5-integration` · 2026-07-25
> 输入：round1 五份（w1-backtest-engine / w2-server-compare / w3-data-scaffold / w4-signal-enhance-h3 / w5-automation-ops），共 **68 个任务**（W1×13 + W2×12 + W3×15 + W4×15 + W5×13）。
> 本文所有「实测」结论均由本 agent 在 Mac worktree 上亲自 grep/ls/git status 核实（2026-07-25），不是转述 round1。
> 任务引用格式 `W<x>-T<y>`；`$PY` = `/Users/mark/sdk/mamba-envs/trading/bin/python3`。

---

## 0. 核实出的 3 个事实修正（影响排期，先读）

1. **W1-T6 是 NOOP**。实测 `grep -rl "/home/smark" quant-loop/_shared quant-loop/validation --include="*.py"` = **0 命中**。W1-T6 的验收命令现在已经通过。→ 转为 INT-01 验证卡直接关闭，不占执行波次资源；同时 W3-T9 shard 清单里的 `validation/` 也是 0 命中，一并剔除。
2. **W4 冻结清单中 3 项无实际竞争者**。实测：`strategies/_indicators/mtf_xs_pairs_base_20260718.py` 无 `/home/smark`（W3-T7 对它 NOOP）且无 `0.0004/0.0008/0.0011/0.0022/0.0024` 成本字面量（W3-T14 对它 NOOP）；`run_btcsol_variants_fixed.py` 无 `/home/smark`，且 research/ 下 13 个含 `/home/smark` 的 .py 全部在 `research/{calibration,ofi,validation,spillover_2026-07-19}`，**无一在 `research/swarm/`**（W3-T6 碰不到它）。→ 真正需要豁免时间表的只剩 **data parquet 一项**（见 §4）。
3. **`compare-page.tsx` 在 worktree 里有他人未提交改动**（`git status` = ` M`）。W2-T8/T9 要在它的上面改。另外 `server/internal/service/*.go`、`server/cmd/server/*.go` 也是脏的 → 所有 server Go 任务的验收若失败，必须先区分是自己的改动还是脏文件导致。所有相关卡片必须内嵌「禁止 `git checkout/restore/clean`，在现有改动之上编辑」红线。

---

## 1. 任务-文件冲突矩阵

「串行」= 必须先后；「并行」= 文件集实测不相交，可同波。

| # | 文件 / 目录 | 撞车任务 | 裁决与串行方式 |
|---|---|---|---|
| C1 | `server/internal/gate/gate.go` + `gate_test.go` | W2-T1、W2-T2 vs **W5-T7** | **W2 拥有语义**（W5 round1 §4 已自认退让）。W5-T7 降级为纯部署步骤并与 W2-T11 合并为同一部署窗口（见 C13），不再改 gate.go、不建迁移。串行：W2-T1 → W2-T2。 |
| C2 | `server/internal/handler/metric.go` | W2-T3 vs W2-T5 | 串行 W2-T3 → W2-T5（W2 round1 已注明：T5 只改 `ReevaluateRunMetrics` 函数体，行区间不重叠）。 |
| C3 | `server/migrations/125_*` | W2-T5 vs W5-T7 | 只建 W2-T5 的 `125_run_metric_gate_status_check.{up,down}.sql`。最新序号实测为 124（`124_issue_claim_columns`，且该文件本身未提交——属他人工作，禁动）。 |
| C4 | `packages/views/compare/components/compare-page.tsx` | W2-T8 → W2-T9（流内串行）；**worktree 脏文件** | T8 → T9 串行不变。两张卡都必须内嵌红线：在他人未提交改动之上编辑，禁止任何 git 恢复操作；同目录 `compare-page.tsx.bak.gate-pass-only` / `.bak.gate-filter-20260720` 禁碰（W2 round1 §1.4 已注明）。 |
| C5 | `quant-loop/_shared/**`、`quant-loop/validation/**` 的 `/home/smark` | W1-T6 vs W3-T9 | 实测 0 命中（事实修正 #1）。W1-T6 → INT-01 验证关闭；W3-T9 从 shard 剔除 `validation/`。**无串行需要**。 |
| C6 | `quant-loop/scripts/` | W3-T4（12 个含 `/home/smark` 的 .py/.sh，实测清单：fetch_binance_spot_1h / finalize_aggtrades_report / backfill_aggtrades_vision / fetch_binance_funding / v10_backtrader / v10_grid_v2 / fetch_binance_usdm_30m / v10_grid_search / fetch_binance_usdm_1m + 3 个 run_aggtrades*.sh）vs W2-T10（`build_results_ledger.py`，实测 0 命中 `/home/smark`）vs W1-T10（新建 `new_variant.py`） | 三个任务文件集**实测互不相交 → 可同波并行**。 |
| C7 | 脚手架**产品冲突**（非文件冲突） | W1-T10（`quant-loop/scripts/new_variant.py`）vs W3-T10（`quant-loop/_shared/templates/scaffold.py`） | 两个生成器会产出两套骨架。裁决：**W3-T10 先行**（模板 + SPEC 四要素是地基），W1-T10 降为薄包装（调 `_shared.templates.scaffold` 生成后补 signals.py 示例与自检），排 W3-T10 之后一个波次。由 INT-03 在 wave 0 发仲裁指令。 |
| C8 | `strategies/_indicators/mtf_xs_pairs_base_20260718.py` | W4 冻结 vs W3-T7、W3-T14 | 实测两者对该文件均 NOOP（事实修正 #2）。→ INT-02 重审关闭；W3-T7/T14 的卡片改为「验证 0 命中 + 写豁免说明」，**不编辑该文件**。 |
| C9 | `quant-loop/data/perp_1m/*.parquet`、`data/funding/*.parquet` | W4 读冻结（T07/T08 断言 `n_bars==2448219` 与 7 窗边界表）vs W3-T15 增量刷新 | **唯一真冻结项**。W3-T15 必须排在 W4-T09~T14 全部 exit 0 之后（wave 4，见 §4）。 |
| C10 | 活跃策略目录（`vpvr_carry_term_8h_20260711`、`vpvr_xs_smart_routing_15m_20260715`、`vpvr_xs_basis_zscore_15m_funding_filter_20260712`、`vol_breakout_vpvr_val_fade_1h_5m_20260714`、`pairs_cointegration_1d_20260709`、`momentum_trend_btc_only_softer_stop_1h_20260712`） | W1-T3/T4 删 `framework_adapter_*.py` vs W3-T7/T8 改同目录其他 .py | W3 规则已排除 `framework_adapter_*` → 文件名不相交，**可并行**。W1-T3/T4 实测目标存在：活跃 adapter 11 个 + graveyard 62 个。 |
| C11 | `scripts/deploy.sh`（repo 根） | W5-T6（唯一编辑者）vs W2-T11/T12、W5-T7（仅调用） | W5-T6 落地前，任何部署任务不得执行。调用方统一走「本地全绿 → `scripts/deploy.sh server|web` → T5 冒烟」一条路径。 |
| C12 | mamba trading env（`/Users/mark/sdk/mamba-envs/trading`） | W1-T1 | 全局单例，wave 0 第一个派、只派一个 agent。W1-T12 等需要框架 import 的验收必须等它完成。freqtrade 装不上时按 W1 round1 降级预案（backtrader+vectorbt），并在结果中如实报告——parent 需决策「双框架 CV 降级为单框架」是否接受。 |
| C13 | .105 部署窗口 | W2-T11（server）、W2-T12（web）、W5-T7（已降级） | **合并为一个窗口、固定顺序**：`deploy.sh server`（W2-T11，含迁移 125 + reevaluate backfill）→ `deploy.sh web`（W2-T12，有真实 no-data/fail 数据可验）。W5-T7 不单独存在。deploy 期间 .105 autopilot 调度短暂受影响，窗口内禁止并行派其他 .105 任务。 |
| C14 | `quant-loop/scripts/` vs repo 根 `scripts/` 命名歧义 | W1-T10、W2-T10、W3-T4（quant-loop/scripts/）vs W5-T1/T2/T4/T5/T6（repo 根 scripts/） | 所有卡片统一写**绝对路径或带前缀相对路径**。W5 的 ops 脚本全在 repo 根 `scripts/`（与 `deploy.sh` 同目录）；W1/W2/W3 的全在 `quant-loop/scripts/`。 |
| C15 | `_shared/` 新文件 | W1-T2（`_shared/validation/fee_shock.py`）、W3-T1/T2（`_shared/paths.py`、`data_loader.py`）、W3-T11（`_shared/validation/validate_metrics.py`）、W3-T12（`_shared/execution/check_inline_costs.py`）、W5-T8~T13（`_shared/paper/`、`_shared/swarm/`） | 全部为新文件、文件名互不相交（实测 `_shared/` 现状：无 paths.py / data_loader.py / paper/ / swarm/）→ **可同波并行**。W1-T2 与 W3-T11 同目录不同文件，无冲突。 |

---

## 2. 波次编排（依赖 DAG 拓扑排序）

池子 2×128 对 68+6 任务不构成约束（单波最多 26 个），**瓶颈是依赖链与三个硬串行点**（C1 gate 语义、C13 部署窗口、C9 数据冻结）。关键路径：
`W3-T1 → W3-T2 → W3-T10 → W1-T10 → W1-T13` 与 `W4-T02/03/04 → T06 → T08 → T09-14 → T15`（最长，5 跳）。

### Wave 0 — 地基（26 任务全并行；Mac 24 / either 2）

`W1-T1*`、W1-T2、W1-T3、W1-T4、W1-T5、W1-T7 ｜ W2-T1、W2-T3、W2-T6 ｜ W3-T1、W3-T11、W3-T12、W3-T13 ｜ W4-T01、T02、T03、T04 ｜ W5-T1、T2、T4、T5、T8、T11 ｜ INT-01、INT-02、INT-03（仲裁指令，不占执行 agent）

- `*` W1-T1 第一个派（C12 单例）。
- 出口门禁：W1-T1 完成（否则 Wave 2 的 W1-T12 无法验收）；W4-T02/03/04 全绿（否则 Wave 1 的 W4 parity 无输入）；W5-T5 完成（C11 部署链路前提）。

### Wave 1 — 主体（24 任务全并行；Mac 22 / 105-pref 2）

W1-T8(←T2)、W1-T9、W1-T11 ｜ W2-T2(←T1)、W2-T4(←T3)、W2-T5(←T3)、W2-T7(←T6)、W2-T8(←T6)、W2-T10 ｜ W3-T2(←T1)、W3-T3(←T1)、W3-T4~T9(←T1，T7 按 INT-02 重审版) ｜ W4-T05(←T02,T03)、W4-T06(←T02,T03,T04) ｜ W5-T3(←T2)、W5-T6(←T5)、W5-T9(←T8)、W5-T10(←T8)、W5-T12(←T11)

- **质量闸门：W4-T06（loop parity 双锚定）不绿，Wave 2/3 的 W4 执行任务一律不放行**（parity 不绿后面全是垃圾数）。
- W2-T2 与 W2-T4/T5 分别等 T1、T3，但 T2/T4/T5 三者文件不相交可并行。

### Wave 2 — 收口前（7 任务）

W1-T12(←T8,T3,T4) ｜ W2-T9(←T7,T8) ｜ W3-T10(←T2)、W3-T14(←T7,T12，INT-02 重审版) ｜ W4-T07、W4-T08(←T06) ｜ W5-T13(←T4,T11)

- W4-T07 与 T08 是重 CPU（单窗 <5min、全历史 10-25min），Mac 上两者并行没问题；**不要与 Wave 3 的 6 窗口挤同一时刻**。

### Wave 3 — 执行波 + server 部署窗口（9 任务）

W4-T09~T14（6 窗口，←T08；Mac 3 + 105 3，先过 INT-06 数据预检）｜ W1-T10(←W3-T10，C7 薄包装版) ｜ **W2-T11 = 部署窗口**（←W2-T1..T5 全绿 + W5-T5/T6 落地；内含 C13 顺序的前半 `deploy.sh server` + reevaluate backfill + 抽查；W5-T7 已并入）

- INT-06 必须先于 W4 的 .105 窗口任务返回「数据在位」。
- W2-T11 执行期间 .105 上不再并行其他任务（C13）。

### Wave 4 — 聚合 + 解冻 + web 部署（4 任务）

W4-T15(←T07,T09-14) ｜ W3-T15(←T3,T4,T5 + **C9 解冻**：W4-T09~14 全绿) ｜ W2-T12(←T6-T9 + W2-T11 之后) ｜ W1-T13(←T10,T12)

### Wave 5 — 整合验收（1 任务）

INT-05 全量回归 sweep（见 §5）。

---

## 3. 68 任务 × 机器分配草案

机器事实：Mac = 数据（`quant-loop/data/` 11G 实测在本机）+ 完整 worktree + mamba `$PY` + pnpm monorepo + Go（`deploy.sh` 本地交叉编译）；.105 = linux server、Go、部署目标（`/home/smark/multica`）、历史数据目录（`/home/smark/multica/quant-loop/data`，W3 §1.3 硬编码路径证明其存在，但**内容新鲜度未核实** → INT-06 预检）。

| 任务 | 机器 | 理由 |
|---|---|---|
| W1-T1 env 安装 | **mac** | mamba env 只存在于 Mac |
| W1-T2/T3/T4/T5/T7/T8/T9/T11 | **mac** | quant-loop pytest + `$PY` |
| W1-T10/T12/T13 | **mac** | quant-loop 脚手架/迁移样板/文档，验收都要跑 harness |
| W2-T1/T2/T3/T4/T5 | **either（建议 105）** | 纯 Go build/test；放 105 可把 Mac 的 CPU 让给 W4 回测。若 105 checkout 与 Mac worktree 不同步，则以 Mac 为编辑面、105 仅跑测试——派发时由调度者确认同一份代码 |
| W2-T6/T7/T8/T9 | **mac** | pnpm monorepo（typecheck/vitest 在 Mac 可跑）；compare-page.tsx 脏文件在 Mac worktree |
| W2-T10 | **mac** | quant-loop python 脚本 |
| W2-T11（部署窗口） | **105**（从有 ssh 权限的 checkout 执行 `scripts/deploy.sh`） | 部署目标机；含 pg_dump/migrate/binary swap |
| W2-T12 | **105**（前置 `pnpm typecheck && pnpm test` 在 mac 跑绿） | web 部署 = rsync 到 .105 远端 build |
| W3-T1~T14 | **mac** | 全部依赖 `$PY` + 本地数据冒烟（T2 验收读真实 BTCUSDT_15m parquet） |
| W3-T15 | **mac** | 数据刷新写本机 parquet；网络抓取 |
| W4-T01~T08、T15 | **mac** | 读 `data/perp_1m` + `data/funding`（Mac 实测在位） |
| W4-T09/T11/T13 | **mac** | 窗口 1/3/5（Mac 与 T07/T08 合计并发 ≤4，防 swap 干扰耗时证据） |
| W4-T10/T12/T14 | **105**（过 INT-06 预检后） | 窗口 2/4/6；`QUANT_LOOP_ROOT=/home/smark/multica/quant-loop`；若预检失败全部回退 Mac、串行降级 |
| W5-T1 | **mac** | launchd 探测项（caocao-tunnel/model-proxy/multica-daemon）在 Mac；另 curl .105 healthz |
| W5-T2/T3/T4/T8~T13 | **mac** | multica CLI 对 .105:8080 但从 Mac 发；`_shared/paper|swarm` 测试用 `$PY` |
| W5-T5/T6 | **mac** | `SMOKE_HOST` 可指向任意；deploy.sh 本地构建 |
| W5-T7 | —（已并入 W2-T11，见 C1/C13） | — |
| INT-01/02/03/05 | **mac** | 见 §5 |
| INT-04 | **105** | 见 §5 |
| INT-06 | **105** | 见 §5 |

负载均衡：Wave 0/1 各 24-26 个任务全在 Mac 侧也可被 128 池一口吃下；真正需要双机的是 Wave 3 的 8 个 Python 长循环（W4 窗口纪律：单机并发 ≤4）。

---

## 4. W4 冻结清单豁免时间表

W4 round1 §5 冻结 6 项，实测后 5 项可即刻豁免、1 项真冻结：

| 冻结项 | 竞争者 | 实测结论 | 豁免时点 |
|---|---|---|---|
| `strategies/_indicators/mtf_xs_pairs_base_20260718.py` | W3-T7、W3-T14 | 无 `/home/smark`、无成本字面量 → 两任务对它 NOOP | **即刻豁免**（INT-02 关闭后生效）；若 INT-02 重审意外发现确需改，则冻结延长至 W4-T15 聚合完成之后 |
| `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json` | 无（W3 全线禁改 .json/.md） | 无人触碰 | **即刻豁免** |
| `research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py` | W3-T6（名义上覆盖 research/） | 该文件 0 命中 `/home/smark`；W3-T6 实测清单 13 个文件全在 calibration/ofi/validation/spillover，不含 swarm | **即刻豁免** |
| `research/swarm/2026-07-25/H3-baseline-repro/metrics.json` | 无 | 无人触碰 | **即刻豁免** |
| quick_verify 旧产物（旧 funding 源） | 无 | 只读引用 | 不适用 |
| **`quant-loop/data/perp_1m/*.parquet`、`quant-loop/data/funding/*.parquet`** | **W3-T15（增量刷新写 parquet + manifest）** | 真冲突：W4-T07/T08/T09-14 的 `n_bars==2448219` 与 7 窗 ISO 边界断言会被刷新打破 | **冻结至 W4-T09~T14 全部 exit 0 且 W4-T15 聚合启动前**。W3-T15 排 Wave 4；其卡片内嵌前置检查：`ls quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/results/se_h3_wf_window_{0..6}.json` 7 个文件齐全才准跑 |

---

## 5. 本 slice 的执行卡（6 张，INT-01~06）

### INT-01 — W1-T6 零命中验证关闭（NOOP 化）
- 目标：用一条命令证明 W1-T6 无事可做，把任务从执行队列关闭。
- 读：`quant-loop/_shared/`、`quant-loop/validation/`（全目录，只读 grep）。写：无（只产出结果文本）。
- 步骤：
  1. `cd /Users/mark/multica/quant-loop`
  2. `grep -rl "/home/smark" _shared validation --include="*.py" | wc -l`
  3. 结果必须为 `0`；若 >0，把命中文件清单原样写进结果，W1-T6 恢复为正常任务按 round1 执行。
- 验收：`grep -rl "/home/smark" _shared validation --include="*.py" | wc -l` 输出 `0`。
- est 2 min · 机器 **mac** · deps 无

### INT-02 — W3-T7/T14 对 mtf_xs_pairs base 的重审关闭
- 目标：确认 W3-T7（路径迁移）与 W3-T14（成本迁移）对冻结文件 `mtf_xs_pairs_base_20260718.py` 均为 NOOP，正式解除 C8 冲突。
- 读：`quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py`、`quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h{1,2,3,4}_20260718/*.py`。写：无。
- 步骤：
  1. `cd /Users/mark/multica/quant-loop`
  2. `grep -c "/home/smark" strategies/_indicators/mtf_xs_pairs_base_20260718.py` → 期望 0
  3. `grep -rnE "0\.00(04|08|11|16|22|24)" strategies/mtf_xs_pairs_1m_15m_2h_h{1,2,3,4}_20260718 strategies/_indicators/mtf_xs_pairs_base_20260718.py --include="*.py" | grep -v framework_adapter | grep -v test | wc -l` → 期望 0
  4. 任一 >0：把命中行写进结果，W3-T7/T14 按 round1 原样执行但**冻结延长至 W4-T15 之后**（§4 表）。
- 验收：步骤 2、3 均输出 `0`。
- est 3 min · 机器 **mac** · deps 无

### INT-03 — 脚手架二合一仲裁指令（C7）
- 目标：向 W1-T10 与 W3-T10 的派发 prompt 注入统一裁决，防止两套骨架。
- 读：`docs/plans/infra-sprint-2026-07-25/round1/w1-backtest-engine.md`（T10 节）、`round1/w3-data-scaffold.md`（T10 节）。写：无代码；产出为两段派发附言文本。
- 步骤：
  1. 给 W3-T10 附言：「按 round1 原样执行，模板落 `_shared/templates/scaffold.py`，SPEC_TEMPLATE 含 §5 四要素。」
  2. 给 W1-T10 附言：「不要从零写生成器。`quant-loop/scripts/new_variant.py` 必须 `import` 并调用 `_shared/templates/scaffold.py` 的生成函数完成目录骨架，自身只负责：生成 contract-v2 `signals.py`（toy 双均线示例）+ 调 `_shared/templates/strategy_contract_v2.py` 的 `check_contract` 自检 + 打印接入指引。验收命令不变。」
  3. 若 W3-T10 尚未完成，W1-T10 不得派发（依赖已写入 §2 Wave 3）。
- 验收：`grep -n "scaffold" quant-loop/scripts/new_variant.py` 在 W1-T10 交付物中 ≥1 命中（整合验收时由 INT-05 复核）。
- est 5 min · 机器 **mac** · deps 无

### INT-04 — 部署窗口看门人（C13 顺序执行）
- 目标：Wave 3~4 的 .105 部署按固定顺序一次完成，不二次重启。
- 读：`scripts/deploy.sh`（全文件）、`server/migrations/125_run_metric_gate_status_check.up.sql`（W2-T5 产物）。写：无仓库文件（只执行与记录输出）。
- 步骤：
  1. 前置确认：`cd /Users/mark/multica/server && go test ./... -count=1` 全绿；`ls migrations/125_*.sql | wc -l` = 2；W5-T5/T6 已交付（`scripts/deploy_smoke.sh`、`deploy.sh --dry-run` 存在）。
  2. `bash scripts/deploy.sh server`，观察输出含 migrate up（应用 125）、`/healthz` 通过、无回滚。
  3. backfill：`curl -s -X POST http://192.168.0.105:8080/api/metrics/reevaluate -d '{}'`（带 workspace auth），确认响应含 `no-data` 计数且 `errors == 0`。
  4. 抽查 sharpe-only 行（vpvr_stable_depeg_p3opt_091）gate_status == `"fail"`。
  5. `SMOKE_HOST=http://192.168.0.105:8080 bash scripts/deploy_smoke.sh` 全绿。
  6. 仅当 1-5 全绿，放行 W2-T12（`bash scripts/deploy.sh web`）。任一步失败：停止、保留现场输出、上报 ESCALATE，禁止重试部署。
- 验收：步骤 3 响应 JSON 含 `"no-data"` 键；步骤 5 退出码 0。
- est 15 min · 机器 **105** · deps W2-T1..T5、W5-T5、W5-T6

### INT-05 — 全量整合回归 sweep（Wave 5 唯一任务）
- 目标：所有波次结束后跑全仓不变量，确认 68 任务叠加无相互破坏。
- 读：全仓库（只读命令）。写：无。
- 步骤（`cd /Users/mark/multica` 起逐条执行，任一失败即记录并继续，最后汇总）：
  1. `cd quant-loop && $PY -m pytest validation/ _shared/ -q` → 期望 ≥142 passed（round1 基线 142 passed, 3 skipped）且无 failed
  2. `cd server && go build ./... && go test ./internal/gate/ ./internal/handler/ -count=1` → 全绿
  3. `pnpm typecheck` → 0 错
  4. `grep -rl "/home/smark" quant-loop --include="*.py" --include="*.sh" --exclude-dir=_graveyard --exclude-dir=data | wc -l` → 0
  5. `find quant-loop/strategies -name "framework_adapter_*.py" -not -path "*__pycache__*" | wc -l` → 0
  6. `grep -c "Bonferroni" quant-loop/validation/README.md` → 0
  7. `grep -n "scaffold" quant-loop/scripts/new_variant.py | head -3` → ≥1 命中（INT-03 复核）
  8. `bash -n quant-loop/validation/ci/validate_changed_variants.sh && bash -n scripts/deploy.sh` → 语法全过
  9. `git status --porcelain packages/views/compare/components/compare-page.tsx server/internal/gate/` → 确认只有本 sprint 预期改动、无 `.bak` 文件被动过（`ls packages/views/compare/components/*.bak* | wc -l` = 2）
- 验收：9 条全部符合期望；输出汇总表（每条 PASS/FAIL + 实测值）。
- est 15 min · 机器 **mac** · deps 全部 68 任务

### INT-06 — .105 数据在位预检（W4 窗口任务放行闸）
- 目标：确认 .105 上 `quant-loop/data` 与 Mac 同源同新鲜度，决定 W4-T10/T12/T14 能否上 105。
- 读：.105 的 `/home/smark/multica/quant-loop/data/perp_1m/BTCUSDT_1m.parquet`（只读元信息）。写：无。
- 步骤：
  1. `ssh smark@192.168.0.105 'ls -la /home/smark/multica/quant-loop/data/perp_1m/BTCUSDT_1m.parquet /home/smark/multica/quant-loop/data/funding/BTCUSDT.parquet'` → 两文件必须存在
  2. `ssh smark@192.168.0.105 'md5sum /home/smark/multica/quant-loop/data/perp_1m/BTCUSDT_1m.parquet'`（linux 用 `md5sum`；若慢可改 `stat -c %s` 比字节数）与 Mac 侧 `md5 -q quant-loop/data/perp_1m/BTCUSDT_1m.parquet`（或 `stat -f %z`）对比
  3. 一致 → 结果「GO-105」；不一致或 ssh 失败 → 「NO-GO」，W4-T10/T12/T14 全部回退 Mac 串行执行（每窗 <5min，6 窗串行 <30min，仍在预算内）
- 验收：结果文本以 `GO-105` 或 `NO-GO` 开头 + 两侧 hash/size 实测值。
- est 5 min · 机器 **105**（从 Mac 发起 ssh）· deps 无；被 W4-T10/T12/T14 依赖

---

## 6. 给 parent 的开放决策点（不阻塞派发，但需在 Wave 2 前拍板）

1. **freqtrade 装不上的降级**（C12）：W1-T1 部分失败时，「双框架 CV」目标降级为 backtrader+vectorbt 是否接受？影响 W1-T12 验收口径。
2. **W2-T1..T5 编辑面**（§3）：Go 任务建议 105 执行，但若 105 checkout 与 Mac worktree 不同步会产生两份代码。需要调度者确认：要么全部 Mac 编辑 + 105 仅部署，要么 105 checkout 先与 Mac 同步。
3. **W3-T14 的实质**：INT-02 若确认 0 命中，mtf_xs_pairs 族成本迁移无事可做——H 族成本（fee 1+1bps/side/leg = 4bps pair RT）本就不是 `BINANCE_FUTURES`（22bps RT）口径，round1 的「迁移到统一成本模型」前提不成立，建议直接关闭并在 ledger 记 NOOP。
