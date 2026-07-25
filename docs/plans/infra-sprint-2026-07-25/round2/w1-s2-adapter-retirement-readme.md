# 片 w1-s2 — W1 T3/T4/T5 细化任务卡（adapter 退役 + README 口径修正）

> Round-2 执行卡。每张卡自包含，面向零上下文 caocao-m3 agent。
> Python 一律 `/Users/mark/sdk/mamba-envs/trading/bin/python3`（下称 `$PY`）。
> 所有命令工作目录为 `/Users/mark/multica/quant-loop`，除非另有说明。
> 所有文件清单已于 2026-07-25 用 glob/grep 实测核实（不是估计）。
>
> **背景一句话**：统一验证管线（`validation/generic_harness.py`，contract-v2）
> 只认策略目录里的 `signals.py` + `data_loader.py`，回放由共享 adapter
> （`validation/adapters/` 下 4 个文件）完成。各策略目录里的
> `framework_adapter_*.py` 是旧管线的遗留物——没有任何 harness 代码动态加载它们
> （已 grep 全仓库核实，`validation/` 里仅 `generic_harness.py:4` docstring 提及），
> 活跃策略的 `strategy.py` 也都不 import 它们。本次任务 = 删除这些死文件 +
> 修正 `validation/README.md` 的 gate 表述漂移。

---

## 卡片 T3 — 活跃策略 adapter 退役 part A（4 个目录，8 个 .py + 1 个测试 + 5 个 .pyc）

- **目标**：删除 4 个活跃策略目录下的 `framework_adapter_*.py`、唯一一个 adapter 测试文件、以及对应 `__pycache__` 里的 `.pyc`。
- **机器**：mac（需要 trading env 跑 pytest 验收）
- **依赖**：无（G0，可与 T4/T5 并行；与 T4 文件集不相交）
- **预估**：15 分钟

### 读（核实用，可选）
- `validation/generic_harness.py:1-30`（docstring 说明新管线不需要 per-strategy adapter）

### 精确删除清单（全部实测存在，逐一 `rm`）

```
strategies/vpvr_carry_term_8h_20260711/framework_adapter_backtrader.py
strategies/vpvr_carry_term_8h_20260711/framework_adapter_freqtrade.py
strategies/vpvr_carry_term_8h_20260711/framework_adapter_vectorbt.py
strategies/vpvr_carry_term_8h_20260711/tests/test_framework_adapter_backtrader.py
strategies/vpvr_carry_term_8h_20260711/__pycache__/framework_adapter_freqtrade.cpython-312.pyc
strategies/vpvr_carry_term_8h_20260711/__pycache__/framework_adapter_vectorbt.cpython-312.pyc
strategies/vpvr_xs_smart_routing_15m_20260715/framework_adapter_backtrader.py
strategies/vpvr_xs_smart_routing_15m_20260715/framework_adapter_freqtrade.py
strategies/vpvr_xs_smart_routing_15m_20260715/__pycache__/framework_adapter_backtrader.cpython-312.pyc
strategies/vpvr_xs_smart_routing_15m_20260715/__pycache__/framework_adapter_freqtrade.cpython-312.pyc
strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/framework_adapter_backtrader.py
strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/framework_adapter_freqtrade.py
strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/__pycache__/framework_adapter_backtrader.cpython-312.pyc
strategies/momentum_trend_btc_only_softer_stop_1h_20260712/framework_adapter_backtrader.py
```

注意：
- `vpvr_carry_term_8h_20260711/tests/test_framework_adapter_backtrader.py:13` 有
  `from framework_adapter_backtrader import ...` —— **必须随 adapter 一起删**，
  否则 pytest 收集到它时会 ImportError。该目录下 `tests/test_strategy.py` **保留**。
- 若某个 `__pycache__/*.pyc` 在清单里但执行时已不存在（pycache 易变），跳过即可，
  不算失败；但清单里的 9 个 `.py` 文件必须全部删除成功。

### 明确不碰（保留）

- 这 4 个目录下的 `strategy.py`、`config.json`、`data_loader.py`、`run_backtest.py`、`walk_forward.py`、`SPEC.md`、`data/`、`results/`、`tests/` 里的其他文件
- `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/data/framework_adapter_report.json` 和
  `strategies/momentum_trend_btc_only_softer_stop_1h_20260712/data/framework_adapter_report.json`（历史证据 JSON，保留）
- `strategies/_graveyard/` 下任何文件（那是 T4 的范围）
- 其他活跃目录（`vol_breakout_*`、`pairs_cointegration_*`）——那是 T4 的范围

### 步骤

1. `cd /Users/mark/multica/quant-loop`
2. 逐一 `rm` 上面清单中的文件（`.py` 必须全部删除；`.pyc` 不存在则跳过）。
3. 跑验收命令（见下）。
4. 如果 pytest 出现与本删除无关的红（预先存在的失败），在结果里如实报告失败名，不要顺手修。

### 验收（全部必须通过）

```bash
cd /Users/mark/multica/quant-loop

# 1) 这 4 个目录下不再有任何 framework_adapter 源文件
find strategies -name "framework_adapter_*.py" -not -path "*_graveyard*" \
  | grep -E "carry_term|smart_routing|basis_zscore|softer_stop" | wc -l
# 期望输出: 0

# 2) 全局还剩 3 个活跃 adapter（全在 T4 的两个目录里，证明你没越界删）
find strategies -name "framework_adapter_*.py" -not -path "*_graveyard*" -not -path "*__pycache__*" | wc -l
# 期望输出: 3

# 3) 基线测试仍绿
$PY -m pytest validation/ _shared/ -q
# 期望: 末尾 "passed" 数 >= 142（基线为 142 passed, 3 skipped），无 failed/error
```

---

## 卡片 T4 — 活跃 adapter 退役 part B（2 目录）+ graveyard 62 个 adapter 全删

- **目标**：删除 `vol_breakout_vpvr_val_fade_1h_5m_20260714`（2 个）和 `pairs_cointegration_1d_20260709`（1 个）目录下的 adapter；删除 `strategies/_graveyard/` 下全部 62 个 `framework_adapter_*.py` 及 39 个对应 `.pyc`。
- **机器**：mac（需要 trading env 跑 pytest 验收）
- **依赖**：无（G0，可与 T3/T5 并行；与 T3 文件集不相交）
- **预估**：20 分钟

### 精确删除清单

**part B 活跃目录（3 个 .py，逐一 `rm`）：**

```
strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/framework_adapter_backtrader.py
strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/framework_adapter_freqtrade.py
strategies/pairs_cointegration_1d_20260709/framework_adapter_freqtrade.py
```

（这 3 个文件没有对应的 test 文件，也没有 `__pycache__` `.pyc`——已实测核实。）

**graveyard（62 个 .py + 39 个 .pyc，用下面两条命令删，不要手敲 62 次）：**

```bash
cd /Users/mark/multica/quant-loop

# 删除前快照（必须先看数量，应为 62 和 39；不对就停下来报告，不要删）
find strategies/_graveyard -name "framework_adapter_*.py" -not -path "*__pycache__*" | wc -l   # 期望 62
find strategies/_graveyard -path "*__pycache__*" -name "framework_adapter_*.pyc" | wc -l        # 期望 39

# 执行删除
find strategies/_graveyard -name "framework_adapter_*.py" -not -path "*__pycache__*" -delete
find strategies/_graveyard -path "*__pycache__*" -name "framework_adapter_*.pyc" -delete
```

62 个 graveyard `.py` 的分布（供 sanity check，家族目录: 文件数）：

```
1m_klines_reversal: 9   funding_carry: 3   momentum_trend: 1
options_macro_sentiment: 11   vpvr_funding: 15   vpvr_reversion: 1
vpvr_xs_pairs_4h: 4   xs_pairs_30m: 18   合计 62
```

### 明确不碰（保留）

- `strategies/_graveyard/` 下除上述两类文件外的一切：尤其
  `find strategies/_graveyard -name "framework_adapter_report*.json"`（3 个，历史证据，保留）
  和所有 `strategy.py` / `data_loader.py` / `config.json` / `results/`。
  **graveyard 策略的信号代码一律不改不重跑**（cycle-46 纪律：已 KILL 家族不重验）。
- `strategies/pairs_cointegration_1d_20260709/` 下除 `framework_adapter_freqtrade.py` 外的一切
  （`data/framework_adapter_report.json`、`results/`、`cointegration.py` 等全部保留——
  后续 T12 要把这个目录迁移到 generic 管线）。
- T3 的 4 个目录（`carry_term` / `smart_routing` / `basis_zscore` / `softer_stop`）。
- 非 `framework_adapter_*` 名字的其他 adapter 文件（如 `harness_adapter.py`，若存在）。

### 步骤

1. `cd /Users/mark/multica/quant-loop`
2. 先跑两条 `wc -l` 快照命令核对 62 / 39；数字不符就停止并在结果里报告实际数字。
3. `rm` part B 的 3 个 `.py`。
4. 跑两条 `-delete` 命令。
5. 跑验收命令。
6. pytest 若有与本删除无关的预先存在失败，如实报告，不修。

### 验收（全部必须通过）

```bash
cd /Users/mark/multica/quant-loop

# 1) 全仓库不再有任何 framework_adapter 源文件（含 graveyard，排除 pycache）
find strategies -name "framework_adapter_*.py" -not -path "*__pycache__*" | wc -l
# 期望输出: 0

# 2) pycache 里也清干净了
find strategies -path "*__pycache__*" -name "framework_adapter_*.pyc" | wc -l
# 期望输出: 0

# 3) 证据 JSON 仍在
find strategies -name "framework_adapter_report*.json" | wc -l
# 期望输出: >= 16（实测当前 16：13 活跃 + 3 graveyard；只能多不能少）

# 4) 基线测试仍绿
$PY -m pytest validation/ _shared/ -q
# 期望: "passed" 数 >= 142（基线 142 passed, 3 skipped），无 failed/error
```

---

## 卡片 T5 — `validation/README.md` gate 口径修正（对齐代码现状）

- **目标**：README 的 gate 表与 `validation/gates.py` 实际代码对齐（G3/G4 写反了、G7 还是已退役的 Bonferroni t-test、缺 T1 行、缺 MISSING_FIELD=FAIL 语义）。
- **机器**：either（纯文档编辑 + grep 验收，不需要跑 pytest）
- **依赖**：无（G0，可与 T3/T4 并行）
- **预估**：15 分钟

### 读（改之前先读这两处，它们是唯一事实源）

- `quant-loop/validation/README.md`（91 行，唯一要改的文件）
- `quant-loop/validation/gates.py:1-44`（gate 表 docstring + 常量）和 `:143-150`（G7 DSR 实现）

### 问题清单（实测漂移，README 行号 vs 代码）

| README 现状 | 代码事实（gates.py） |
|---|---|
| :39 `G7 one-sided t-test p < 0.0125 ... (Bonferroni 0.05/4)` | G7 = **Deflated Sharpe Ratio > 0**（Bailey-LdP 2014），`G7_MIN_DSR = 0.0`（:39），实现 `deflated_sharpe(oos_native_sharpe, n_trials, sample_len)`，`DEFAULT_N_TRIALS = 100`（:44） |
| :35 `G3 profit_factor > 1.5` | G3 = **max_drawdown_pct > -0.25**（负号约定，worst symbol，:35 `G3_MAX_DRAWDOWN = -0.25`） |
| :36 `G4 max_drawdown < 25%` | G4 = **profit_factor > 1.5**（mean across symbols，inf 封顶 10，:36 `G4_MIN_PROFIT_FACTOR = 1.5`） |
| 无 T1 行 | T1 = pooled OOS trades >= 30（:40 `T1_MIN_TRADES = 30`） |
| :37 G5 写死 "3 windows" | G5 = worst framework mean OOS Sharpe >= 1.0（backtrader+freqtrade，:130-138）；窗口数是 CLI 参数 `--windows`，不要写死 3 |
| :6 "across 3 contiguous OOS windows" | 同上，窗口数是可配参数 |
| 无 MISSING_FIELD 说明 | 缺数据（如无 framework 窗口）→ G5 observed=NaN → enforce.py 记 **MISSING_FIELD FAIL**；缺失永远不是 pass（gates.py:131-132 注释） |

### 步骤（只改 `validation/README.md` 一个文件）

1. 替换 :31-39 的 gate 表为下表（逐字）：

```markdown
| gate | threshold | evaluated on |
|------|-----------|--------------|
| G1 | mean Sharpe >= 1.0 | native engine, full span, mean across symbols |
| G2 | min(annualized_full, mean_OOS_annualized) >= 15% | native, full + OOS windows |
| G3 | max_drawdown_pct > -0.25 (negative convention) | native, full span, worst symbol |
| G4 | profit_factor > 1.5 (mean across symbols, inf capped at 10) | native, full span |
| G5 | mean OOS Sharpe >= 1.0 in **both** backtrader and freqtrade (worst of the two means) | framework replays, configured OOS windows |
| G6 | bootstrap 95% CI lower of annualized Sharpe >= 0.5 | native pooled OOS daily returns (10000 resamples, seed=42) |
| G7 | Deflated Sharpe Ratio > 0 (Bailey-LdP 2014, n_trials=100) | native mean OOS Sharpe vs multiple-testing hurdle |
| T1 | pooled OOS trades >= 30 | native pooled OOS trades |
```

2. 把 :6 的 "across 3 contiguous OOS windows" 改为
   "across the configured contiguous OOS windows (`--windows` CLI flag)"。
3. 在 gate 表下方（:39 之后、:41 "All frameworks share..." 之前）插入一段：

```markdown
Missing data is never a pass: if a framework engine is unavailable its
windows are skipped (recorded in `report["framework_skips"]`), the
corresponding gate observed value becomes NaN, and the enforcer
(`_shared/gates/enforce.py::certify_metrics`) records a `MISSING_FIELD`
FAIL. A skipped framework leg therefore fails G5 rather than passing it.
```

4. :82-84 的 vectorbt 说明（"do not feed gate G5"）**保持不动**——那是代码现状
   （gates.py:134-137 只消费 backtrader+freqtrade），不属于漂移。vectorbt 是否纳入
   G5 由别的任务（T9）决定，改完才更新 README。
5. :41-43 的 metrics 说明、:45-53 的 framework CV 机制、:55-80 的 contract v2 路由
   说明均与代码一致，**不动**。
6. 不要改其他任何文件。

### 验收（全部必须通过）

```bash
cd /Users/mark/multica/quant-loop

grep -c "Bonferroni" validation/README.md
# 期望输出: 0

grep -c "Deflated Sharpe" validation/README.md
# 期望输出: >= 1

grep -c "MISSING_FIELD" validation/README.md
# 期望输出: >= 1

grep -c "| T1 |" validation/README.md
# 期望输出: 1

# README 里不得再出现写死的 3-window 表述
grep -cn "3 contiguous OOS windows\|3 windows" validation/README.md
# 期望输出: 0

# G3/G4 表述与代码常量一致（在 README 里能 grep 到这两个阈值）
grep -c "\-0.25" validation/README.md   # 期望 >= 1
grep -c "profit_factor > 1.5" validation/README.md   # 期望 >= 1
```

---

## 跨片冲突提示（给 parent）

1. **T3+T4 合起来把 `strategies/` 下 73 个 `framework_adapter_*.py` 删到 0**。若 cleanup/archival workstream 也有 `_graveyard/` 或 `strategies/` 的删除任务，必须按文件名划界：本片只删 `framework_adapter_*.py` / `test_framework_adapter_*.py` / 对应 `.pyc`，不碰其他文件；反向要求对方不碰这几个 pattern。
2. **T4 之后 W1 的 T12**（pairs_cointegration 迁移 generic 管线）才能跑——T12 依赖 T4 已把 `strategies/pairs_cointegration_1d_20260709/framework_adapter_freqtrade.py` 删掉。T4 特意保留该目录其余全部文件。
3. **T5 与 W1 的 T8/T9 有弱耦合**：T8 把默认窗数 3→7、T9 可能把 vectorbt 纳入 G5。T5 已把 README 的窗口数写成中性表述（"configured OOS windows"），不会与 T8 冲突；但 **T9 落地后需再改一次 README 的 vectorbt 说明段**（README :82-84，T5 刻意没动）——建议 parent 把"README vectorbt 段二次更新"并进 T9 的验收，或单开一张 S 卡。
4. T3/T4 的 pytest 验收只跑 `validation/ _shared/`（不收集 `strategies/`），所以即使其他 workstream 正在动 strategies 也不影响本片验收。
