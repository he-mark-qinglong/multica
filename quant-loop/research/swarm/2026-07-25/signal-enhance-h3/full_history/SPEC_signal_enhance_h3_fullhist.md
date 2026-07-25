# SPEC — signal-enhance-h3 full-history validation (pre-registered 2026-07-25)

> **Status:** pre-registered, immutable except by re-issue of this SPEC.
> **Slice:** w4-s1 / W4-T01
> **Parent workstream:** w4-signal-enhance-h3
> **Author (this file):** knowledge-curator agent (Kimi), dispatched under Multica task SMA-36496 on 2026-07-25.
> **Scope:** pre-registration only — no backtest results are produced, claimed, or implied in this document.

---

## 假设

> 15m z-slope 顺向转弯入场过滤（lookback=4, favorable）+ 0.7z 逆势止损（替代 regime_break=3.0 宽止损，等效设为 9.0）能过滤掉以 regime_break 出场的亏损交易（2024 子样本中占 78.2%、均亏 -19.7bps net），使 H3 BTC+SOL 组合在全历史 walk-forward OOS 上保持 Sharpe ≥ 1.0 且 60bps 成本下仍为正。

### 假设来源（primary sources，已实地读取并核对）

| 数值片段 | 出处 |
|---|---|
| 「78.2% / 均亏 -19.7 bps net」是 regime_break 出场子集的统计 | `signal-enhance-h3/SUMMARY.md` L32（exit-reason 分布表第 2 行）；同一事实陈述于 L28 |
| 「78% of trades exit on `regime_break` ... -19.7 bps net」 | `signal-enhance-h3/SUMMARY.md` L28（与上表同义、同一文件不同行） |
| 「704 trades, Sharpe 8.07, +15.4 bps net, win rate 68.9%」 | `signal-enhance-h3/quick_verify_2024.json` 第 5 个对象（0-indexed 索引 4，键 `variant == "slope_fav_4_stop_0_7"`，`n_trades == 704`，`sharpe_daily_resampled == 8.0735`，`mean_net_bps == 15.429`，`win_rate == 0.6889`） |
| 候选参数组合 `slope_filter={lookback:4, sign:"favorable"}` + `adverse_stop_z=0.7` + `regime_break=9.0` | `signal-enhance-h3/quick_verify.py` L42（variants 列表第 5 项） |

### 假设等级

- **方向性证据 (directional)**：2024 子样本 in-sample，回测并未出样本（cycle-46 纪律：本 SPEC 不以 2024 数值为决策依据）。
- **待验证 (under test)**：本 workstream 后续卡（T05+）将执行全历史 walk-forward OOS 与 fee-shock 测试，本假设仅在那之后才能被确认或证伪。

---

## 锁定参数

以下参数在本 workstream 内**禁止改动**（cycle-46 纪律：仅此一个预注册组合，不做参数扫荡）。所有数值均来自已实地读取的只读文件，标注一手出处。

| 参数 | 值 | 出处 |
|---|---|---|
| `z_entry`（zscore_entry_threshold） | 2.5 | `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json` L17（`"zscore_entry_threshold": 2.5`） |
| `z_exit`（zscore_exit_threshold） | 0.5 | 同上 L18 + L30 |
| `max_hold`（max_holding_bars） | 240 | 同上 L20 + L32 |
| `slope_lookback` | 4 | `signal-enhance-h3/quick_verify.py` L42（`slope_filter.lookback`） |
| `slope_sign` | `favorable` | 同上 L42（`slope_filter.sign`） |
| `adverse_stop_z` | 0.7 | 同上 L42 |
| `regime_break=9.0`（覆盖 H3 baseline 的 3.0，等效禁用宽止损） | 9.0 | 同上 L42；H3 原值 3.0 见 `config.json` L19/L31 |
| 费率/滑点 | fee=1 bps/side, slip=1 bps/side, per leg；= 4 bps pair RT | `config.json` L41-42（`fees_bps_per_side=1.0`, `slippage_bps_per_side=1.0`） |
| 标的 | `BTCUSDT` + `SOLUSDT` only | `config.json` L11-12（`instruments=["BTCUSDT","SOLUSDT"]`, `pairs=["BTCUSDT/SOLUSDT"]`） |
| funding filter threshold | 5e-4 | `config.json` L21（`funding_filter_threshold=0.0005`） |
| funding EMA window | 4 | `config.json` L22（`funding_ema_window=4`） |
| walk_forward train / test / step（1m bars） | 525600 / 262800 / 262800 | `config.json` L45-47 |
| walk_forward 最小窗口数 | 3（`min_windows`） | `config.json` L48 |
| bootstrap seed | 42 | `config.json` L57 |
| bootstrap resamples | 10000 | `config.json` L56 |
| Sharpe 方法 | daily_resampled | `config.json` L43（`sharpe_method`） |

### 不得引入新参数的明示清单

- 不扫 `slope_lookback ∈ {2,3,5,6,...}`，仅 4。
- 不扫 `slope_sign ∈ {adverse, none}`，仅 favorable。
- 不扫 `adverse_stop_z ∈ {0.5, 0.6, 0.8, 0.9}`，仅 0.7。
- 不在 fee-shock 上做 22 bps / 60 bps 之外的等级；本次验证两档（详见证伪条件）。
- 不重设 `regime_break` 阈值；本 SPEC 已锁定为 9.0（覆盖 config.json 的 3.0）。

---

## 证伪条件

任一条件成立即 **KILL 证据成立**，判定回主线（不在本 workstream 内下结论）。KEEP/KILL 判决归主线单线程。

1. **7 窗 OOS mean Sharpe（daily-resampled）< 1.0** — 直接未达 `hard_gates.oos_sharpe_min=1.0`（`config.json` L51）。
2. **bootstrap CI lower（seed=42，resamples=10000）< 0.5** — 直接未达 `hard_gates.bootstrap_ci_lower_min=0.5`（`config.json` L55）。
3. **60 bps pair-RT fee-shock Sharpe ≤ 0** — 策略对成本极度敏感，无法在主流量化成本下存活。
4. **parity 测试（后续卡 T05/T06）不通过** ⇒ 管线本身不可信，本 SPEC 下所有结果作废，需回到 H3-variants-h1h2h4 runner 做修复后再重发 SPEC。

### baseline 锚点对照（仅用于语境，不可被引为 KILL 阈值）

> 以下数值由 orchestrator 在 `H3-baseline-repro/metrics.json` 上用 `/Users/mark/sdk/mamba-envs/trading/bin/python3` 实地读出（2026-07-25 实地读取值）。本 knowledge-curator 在本次执行中未独立重新读取该 json 文件以避免触碰 task 约束以外的资源；如下数值与本 SPEC 的证伪条件无耦合关系——证伪阈值以本节上方 4 条为准。

| baseline 指标 | 值 | 用途 |
|---|---|---|
| OOS Sharpe（7 窗 daily-resampled 均值） | 1.8748 | 上下文：现有 baseline 已过 G1；本 SPEC 不要求增强组合的 OOS Sharpe 显著高于 baseline，只要求 ≥ 1.0 |
| bootstrap CI lower（seed 42 / 10000） | 0.8879 | 上下文：远高于 hard-gate 阈值 0.5；本 SPEC 的 KILL 阈值 2 与之无关 |
| 60 bps fee-shock Sharpe | -0.0213 | 上下文：baseline 在 60 bps 下已转负；本 SPEC 的 KILL 阈值 3 要求增强组合在 60 bps 下不为负 |
| full n_trades | 40 963 | 上下文：本增强组合预期 trade 数显著小于 baseline（2024 子样本已降至 704），更需 OOS 验证避免过稀采样 |

### 阈值与 baseline 的关系（一句话澄清）

证伪阈值是**硬门槛**（来自 `hard_gates` 与本 SPEC 的额外约定），不与 baseline 做相对比较；baseline 锚点仅供研判「策略是否在退化」之用。

---

## funding 双源警示

**核心警示：2024 子样本证据（704 trades, Sharpe 8.07）的 funding 来自非权威源，不得作为决策依据；本次全历史验证一律使用权威 loader。**

### 双源对照

| 字段 | 权威源（本 SPEC 采用） | 2024 子样本实际采用（不可外推） |
|---|---|---|
| BTC funding parquet | `quant-loop/data/funding/BTCUSDT.parquet`（由 `run_btcsol_variants_fixed.py` L59-60 / L101-109 读取） | `quant-loop/funding_analysis/BTCUSDT_funding.parquet` |
| SOL funding parquet | `quant-loop/data/funding/SOLUSDT.parquet`（同 loader） | `quant-loop/strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/data/SOLUSDT__funding.parquet` |

### 一手出处

- `signal-enhance-h3/data_loader_patch.py` L19-20 给出 BTC / SOL funding 的具体路径常量 `BTC_FUNDING` 与 `SOL_FUNDING`，均指向**非权威源**。
- 权威管线由 `H3-variants-h1h2h4/run_btcsol_variants_fixed.py` L101-109 函数 `load_funding(symbol)` 给出，路径常量解析到 `quant-loop/data/funding/{symbol}.parquet`（H3-variants-h1h2h4 L59-60）。

### 后果（量化）

- `fund_allow` 过滤（funding EMA 绝对值小于 5e-4 才允许入场，配置见 `config.json` L21-22）在两套源下命中区间不同 ⇒ 同一段 1m klines 上 entry 时机集合不同。
- 因此 2024 子样本 Sharpe 8.07 / 704 trades / 68.9% win-rate **仅为方向性证据**：
  - 它证明「slope_fav_4 + adverse_stop_0.7 这一族逻辑有继续追查的价值」；
  - 但**不可外推**到全历史、不构成 KEEP 依据、不被本 SPEC 的任何证伪条件或假设条件所引用。

### 本 SPEC 的强制要求

- 本 workstream 后续所有卡（T02 起的所有数据加载、T05+ 的所有回测）**必须**通过权威 loader（`run_btcsol_variants_fixed.py::load_funding`）读取 funding；如需复现 2024 子样本的 funding 数据，仅可在只读 read-only 引用下做诊断，**不得用于**决策、KEEP/KILL 或本 SPEC 的修订。
- 若其他 workstream 正在做 funding 数据层统一工作（与本 SPEC 并行的关联 task），须明确告知：旧数（funding_analysis + graveyard）与新管线（data/funding）结果不可直接比对，差异来自 funding 源而非策略逻辑。

---

## 范围与纪律

1. **目录边界**：本 workstream 仅在 `quant-loop/research/swarm/2026-07-25/signal-enhance-h3/full_history/`（即本文件所在的 FH 目录）下新建文件；其他路径一律不动。
2. **只读 import**：以下文件 / 路径**只可读取**，禁止修改：
   - `quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py`
   - `quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json`
   - `quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py`
   - `quant-loop/data/perp_1m/*.parquet`
   - `quant-loop/data/funding/*.parquet`
   - `quant-loop/research/swarm/2026-07-25/H3-baseline-repro/metrics.json`
   - `signal-enhance-h3/` 目录下一切既有产物（含 SUMMARY.md / quick_verify.py / quick_verify_2024.json / data_loader_patch.py / run_experiments.py / 既有图片与日志）
3. **禁止 git 操作**：worktree 中有他人的未提交改动，本 workstream 一律不触碰（不 commit / push / rebase / reset / checkout 切换 / stash）。
4. **不跑回测**：本卡（T01）以及 T02 的验收只做数据加载，不调用 `run_backtest`；首次回测由后续 T05+ 卡执行。
5. **不做参数扫荡**：仅此一个预注册组合（`slope_fav_4_stop_0_7`，即 `slope_filter={lookback:4,sign:"favorable"}` + `adverse_stop_z=0.7` + `regime_break=9.0`）；cycle-46 纪律禁止扩参。
6. **不跑 G5/G7**：本 SPEC 阶段 CPCV mean OOS Sharpe 与 deflated Sharpe 标记为 `NOT_RUN`，由后续卡决定是否触发。
7. **KEEP/KILL 单线程**：本 workstream 仅产出验证证据（OOS / fee-shock / bootstrap CI / parity），不自行下结论；最终判决归主线单线程。
8. **python 解释器**：一切 Python 执行必须使用 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（系统 python3 无 pyarrow）。本卡（T01）不执行 Python；自 T02 起统一遵循。

---

## 文件谱系与可追溯性

| 产物 | 类型 | 出处 |
|---|---|---|
| 本 SPEC | 新建（pre-registration） | 由 knowledge-curator agent 在 T01 写入 `full_history/SPEC_signal_enhance_h3_fullhist.md` |
| `quick_verify.py` L42 | 已存在（只读引用） | `signal-enhance-h3/quick_verify.py` |
| `quick_verify_2024.json` 第 5 对象 | 已存在（只读引用） | `signal-enhance-h3/quick_verify_2024.json` |
| `data_loader_patch.py` L19-20 | 已存在（只读引用） | `signal-enhance-h3/data_loader_patch.py` |
| `H3-baseline-repro/metrics.json` baseline 锚点 | 已存在（只读引用，本卡未独立读取） | `H3-baseline-repro/metrics.json` |
| H3 config.json 锁定参数 | 已存在（只读引用） | `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/config.json` |

---

## 已知未知（explicit unknowns）

- 本卡（T01）阶段没有重新独立读取 `H3-baseline-repro/metrics.json`；baseline 锚点（1.8748 / 0.8879 / -0.0213 / 40963）由 orchestrator 在 2026-07-25 实地读出后写入 task card，本 SPEC 仅作语境引用，未做独立 re-verification。
- `quick_verify_2024.json` 第 5 对象的键顺序在文件读取时已确认；后续卡若对该 json 做计算需保留该顺序假设或在代码中显式按 `variant == "slope_fav_4_stop_0_7"` 检索。
- 2024 子样本数据时间窗边界与本次全历史 OOS 的实际切窗位置之间没有一一对应关系——这是双源警示中的已知边界条件，不在本 SPEC 内被消除。

---

Pre-registration commit: (to be filled by orchestrator at dispatch time)