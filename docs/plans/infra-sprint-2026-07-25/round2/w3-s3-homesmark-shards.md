# W3-S3 — `/home/smark` 迁移 shards：T7（mtf/vpvr 活跃族）/ T8（其余策略目录）/ T9（顶层 misc）— Round-2 执行卡

> 生成：2026-07-25，round-2 planning agent。所有文件清单、命中数、行号均为本机实读核实。
> 执行者：cheap caocao-m3 agents，零上下文，每卡 <30 min。
> 本 slice 共 4 卡：W3-T7、W3-T8a、W3-T8b、W3-T9（T8 因 28 文件超 30-min 预算拆成两卡）。

---

## 0. 全卡通用规则（每张卡都内联，执行 agent 必读）

### 0.1 背景（一句话）

quant-loop 下大量 `.py` 硬编码 `/home/smark/multica/quant-loop/...` 绝对路径，在 Mac 上全部失效。
W3-T1（另一 slice）已建立 `quant-loop/_shared/paths.py`，提供三个函数（均返回 `pathlib.Path`）：

- `quant_loop_root()` → quant-loop 根目录（读 `QUANT_LOOP_ROOT` env，缺省从 paths.py 自身 `__file__` 推导）
- `data_root()` → `quant_loop_root() / "data"`
- `live_data_root()` → `quant_loop_root() / "live_data"`

本 slice 的任务 = 把指派文件里的 `/home/smark` 字面量全部替换为上述函数的调用。

### 0.2 前置检查（每个 agent 第一步，失败即停）

```bash
cd /Users/mark/multica/quant-loop
ls _shared/paths.py && /Users/mark/sdk/mamba-envs/trading/bin/python3 -c \
  "import sys; sys.path.insert(0,'.'); from _shared.paths import quant_loop_root, data_root, live_data_root; print(quant_loop_root(), data_root(), live_data_root())"
```

若 `_shared/paths.py` 不存在或 import 失败 → **立即停工**，最终报告写 `BLOCKED on W3-T1`，不得自己创建 paths.py（单一 owner 是 W3-T1）。

### 0.3 替换配方（对指派清单中每个文件机械执行）

1. `grep -n '/home/smark' <file>` 取全部命中行。
2. 逐行按模式替换（命中在 docstring/注释里也要改，grep 计数包含它们）：

   | 命中形态 | 替换为 |
   |---|---|
   | `Path("/home/smark/multica/quant-loop/data/<sub>")` | `data_root() / "<sub>"` |
   | `"/home/smark/multica/quant-loop/data/<sub>"`（str 语境，如 CLI default） | `str(data_root() / "<sub>")` |
   | `Path("/home/smark/multica/quant-loop/live_data")` | `live_data_root()` |
   | `Path("/home/smark/multica/quant-loop")` 或其他 quant-loop 内路径 | `quant_loop_root() / "<相对部分>"` |
   | docstring/注释中的路径叙述 | 改写为 `~/multica/quant-loop/...` 或 "repo-relative `<sub>`"（不含 `/home/smark`） |
   | 硬编码 sys.path 插入（例：`_shared_root = "/home/smark/multica/quant-loop"; sys.path.insert(0, _shared_root)`） | 改为 `str(quant_loop_root())` |

3. 若文件尚不能 import `_shared.paths`，在最后一个 import 之后插入以下块（`N` 由卡内按文件给出；
   只保留实际用到的函数名；`Path`/`sys` 已 import 则勿重复 import）：

```python
try:
    from _shared.paths import data_root, live_data_root, quant_loop_root
except ImportError:  # bare-script mode
    import sys
    from pathlib import Path
    _QL = str(Path(__file__).resolve().parents[N])
    if _QL not in sys.path:
        sys.path.insert(0, _QL)
    from _shared.paths import data_root, live_data_root, quant_loop_root
```

   模式来源：`_shared/execution/cost_model.py:36-52`（双模式 import 先例）。
4. 每改完一个文件：`/Users/mark/sdk/mamba-envs/trading/bin/python3 -m py_compile <file>` 必须通过。

### 0.4 硬性禁令（每张卡都适用）

- **只改卡内列出的文件**；`.md` / `.json` / `.log` / `.parquet` 一律禁改（历史证据）。
- **`framework_adapter_*.py` 一律跳过**（adapter 收敛工作流的领土）。
- **W4 冻结清单（W4 完成前禁止任何修改，违反即整卡作废）**：
  - `quant-loop/strategies/_indicators/mtf_xs_pairs_base_20260718.py`
  - `quant-loop/strategies/_indicators/tests/test_mtf_xs_pairs_base_20260718.py`
  - `quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h1_20260718/`、`..._h2_.../`、`..._h3_.../`、`..._h4_.../` 四个目录整体
    （h3 config.json 是 W4 parity 锚点；h1-h4 的成本迁移由后续 W3-T14 负责）
  - `quant-loop/strategies/mtf_h2_vpvr_edge_1m_15m_2h_20260718/`（0 命中，无需动）
  - `quant-loop/research/swarm/2026-07-25/H3-variants-h1h2h4/run_btcsol_variants_fixed.py`
  - `quant-loop/research/swarm/2026-07-25/H3-baseline-repro/metrics.json`
  - `quant-loop/data/perp_1m/*.parquet`、`quant-loop/data/funding/*.parquet`（可改 fetcher 代码，**严禁运行**任何 fetch/resample/backtest）
- 不跑回测、不跑 fetcher、不做 git 操作、不改 `_graveyard/`。
- worktree 里有别人的未提交改动——只碰卡内文件，`git status` 里其他改动一律无视。

### 0.5 已核实的全局事实（执行 agent 不必重新盘点）

- 全 quant-loop 非 graveyard、非 framework_adapter 的 `.py`/`.sh` `/home/smark` 命中：**78 文件**（2026-07-25 实测）。
- h1-h4、mtf_h2_vpvr_edge、`_indicators/mtf_xs_pairs_base_20260718.py` 及其测试：**0 命中**（round-1 假设已过时，勿去"迁移"它们）。
- `_shared/paths.py` 当前**尚不存在**（W3-T1 产物），所以 0.2 前置检查是硬闸门。

---

## W3-T7 — shard: mtf/vpvr 活跃策略族（12 文件）

- **目标**：活跃候选族 4 个策略目录内 12 个 `.py` 的 `/home/smark` 清零。
- **机器**：mac（验收含 pytest + mamba python）｜**估时**：25 min｜**依赖**：W3-T1
- **读写文件**（全部已核实存在；命中数=实测 grep 行数；N=0.3 第 3 步的 parents[N]）：

| 文件 | 命中 | N |
|---|---|---|
| `quant-loop/strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/data_loader.py` | 1（L17 `SHARED_POOL = Path(".../data/perp_1m")`） | 2 |
| `quant-loop/strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/diagnose.py` | 2 | 2 |
| `quant-loop/strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/inspect_full.py` | 2 | 2 |
| `quant-loop/strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/inspect_trades.py` | 2 | 2 |
| `quant-loop/strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/smoke_test.py` | 2 | 2 |
| `quant-loop/strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/strategy.py` | 1（L622 `_shared_root = "/home/smark/multica/quant-loop"` 的 sys.path 插入块 L622-625，改为 `str(quant_loop_root())`；注意此处 import 块在函数体内，保持缩进） | 2 |
| `quant-loop/strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/build_signals.py` | 2 | 2 |
| `quant-loop/strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/data_loader.py` | 1 | 2 |
| `quant-loop/strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/strategy.py` | 1 | 2 |
| `quant-loop/strategies/vpvr_edge_zscore_15m_only_20260720/strategy.py` | 1 | 2 |
| `quant-loop/strategies/impl_vpvr_multi_tf_funding/build_signals.py` | 3 | 2 |
| `quant-loop/strategies/impl_vpvr_multi_tf_funding/data_loader.py` | 1 | 2 |

- **步骤**：§0.2 前置 → 逐文件执行 §0.3 → 每文件 py_compile。
- **典型替换示例**（`mtf_vpvr_edge_zscore.../data_loader.py:17`）：
  - 改前：`SHARED_POOL = Path("/home/smark/multica/quant-loop/data/perp_1m")`
  - 改后：`SHARED_POOL = data_root() / "perp_1m"`
- **验收**（全过才算完成）：
  ```bash
  cd /Users/mark/multica/quant-loop
  grep -rn '/home/smark' strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718 \
    strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720 \
    strategies/vpvr_edge_zscore_15m_only_20260720 \
    strategies/impl_vpvr_multi_tf_funding --include='*.py' | wc -l   # 期望 0
  /Users/mark/sdk/mamba-envs/trading/bin/python3 -m pytest \
    strategies/_indicators/tests/test_mtf_xs_pairs_base_20260718.py -q   # 期望 7 passed（当前基线即 7 passed / 1.4s，合成数据，不碰冻结文件）
  ```

---

## W3-T8a — shard: 其余策略目录 part 1（17 个单命中文件）

- **目标**：17 个策略目录里各 1 个 `.py` 的 `/home/smark` 清零（全部是单文件单/双命中，机械替换）。
- **机器**：either（mac 优先，验收用 mamba python）｜**估时**：25 min｜**依赖**：W3-T1
- **读写文件**（全部已核实存在；N 全部 = 2，即 `strategies/<name>/file.py` → `parents[2]`）：

  `quant-loop/strategies/` 下：
  1. `donchian_breakout_atr_1d_20260709/data_loader.py`
  2. `loid_iceberg_v4_1m_20260720/run_first_btc_90d.py`
  3. `loid_vpvr_confluence_20260717/build_signals.py`
  4. `momentum_intraday_fast_15m_btc_20260712/data_loader.py`
  5. `momentum_trend_btc_only_softer_stop_1h_20260712/data_loader.py`
  6. `momentum_trend_multi_tf_atr_scaled_1h_20260712/data_loader.py`
  7. `momentum_trend_multi_tf_atr_scaled_v2_1h_20260712/data_loader.py`
  8. `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/data_loader.py`
  9. `trend_regime_gate_1d_adx_4h_1h_20260714/data_loader.py`
  10. `vol_breakout_2tf_vpvr_confluence_4h_20260712/data_loader.py`
  11. `vpvr_carry_term_8h_20260711/data_loader.py`
  12. `vpvr_regime_reversion_4h_vol_switch_20260710/data_loader.py`
  13. `vpvr_reversal_check_20260717/run_cross_check.py`
  14. `vpvr_tod_session_filter_15m_20260715/data_loader.py`
  15. `vpvr_xs_reversion_1d_momentum_filter_20260709/data_loader.py`
  16. `vpvr_xs_smart_routing_15m_20260715/data_loader.py`（L18 `LIVE_DATA = Path(".../live_data")` → `live_data_root()`）
  17. `xs_momentum_rank_1d_20260709/data_loader.py`

- **步骤**：§0.2 → 逐文件 §0.3 → py_compile。这批文件命中形态几乎全是 `Path("/home/smark/multica/quant-loop/{data/<sub>,live_data}")` 的模块级常量。
- **验收**：
  ```bash
  cd /Users/mark/multica/quant-loop
  for f in strategies/donchian_breakout_atr_1d_20260709/data_loader.py \
    strategies/loid_iceberg_v4_1m_20260720/run_first_btc_90d.py \
    strategies/loid_vpvr_confluence_20260717/build_signals.py \
    strategies/momentum_intraday_fast_15m_btc_20260712/data_loader.py \
    strategies/momentum_trend_btc_only_softer_stop_1h_20260712/data_loader.py \
    strategies/momentum_trend_multi_tf_atr_scaled_1h_20260712/data_loader.py \
    strategies/momentum_trend_multi_tf_atr_scaled_v2_1h_20260712/data_loader.py \
    strategies/trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/data_loader.py \
    strategies/trend_regime_gate_1d_adx_4h_1h_20260714/data_loader.py \
    strategies/vol_breakout_2tf_vpvr_confluence_4h_20260712/data_loader.py \
    strategies/vpvr_carry_term_8h_20260711/data_loader.py \
    strategies/vpvr_regime_reversion_4h_vol_switch_20260710/data_loader.py \
    strategies/vpvr_reversal_check_20260717/run_cross_check.py \
    strategies/vpvr_tod_session_filter_15m_20260715/data_loader.py \
    strategies/vpvr_xs_reversion_1d_momentum_filter_20260709/data_loader.py \
    strategies/vpvr_xs_smart_routing_15m_20260715/data_loader.py \
    strategies/xs_momentum_rank_1d_20260709/data_loader.py; do
    grep -q '/home/smark' "$f" && echo "FAIL: $f"; \
    /Users/mark/sdk/mamba-envs/trading/bin/python3 -m py_compile "$f" || echo "COMPILE FAIL: $f";
  done; echo DONE   # 期望：无任何 FAIL 行，仅输出 DONE
  ```

---

## W3-T8b — shard: 其余策略目录 part 2（11 文件，含棘手项）

- **目标**：多命中/嵌套较深的其余策略文件清零。
- **机器**：either（mac 优先）｜**估时**：25 min｜**依赖**：W3-T1
- **读写文件**（全部已核实存在）：

| 文件 | 命中 | N |
|---|---|---|
| `quant-loop/strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/data_loader.py` | 4（L6 docstring、L53 `DEFAULT_SOURCE_ROOT` → `live_data_root()`、L57 指向 `strategies/vpvr_iceberg_fade_5m_20260711/data/...` → `quant_loop_root() / "strategies" / "vpvr_iceberg_fade_5m_20260711" / "data" / "BTCUSDT__5m.parquet"`、**L233 棘手项见下**） | 2 |
| `quant-loop/strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/strategy.py` | 1 | 2 |
| `quant-loop/strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/scripts/b6_aggregate.py` | 1 | **3** |
| `quant-loop/strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/scripts/b6_bootstrap.py` | 1 | **3** |
| `quant-loop/strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/scripts/b6_fwer.py` | 1 | **3** |
| `quant-loop/strategies/pairs_cointegration_1d_20260709/backtest.py` | 1 | 2 |
| `quant-loop/strategies/pairs_cointegration_1d_20260709/data_loader.py` | 1 | 2 |
| `quant-loop/strategies/_indicators/tests/test_iter94_20260714.py` | ≥1 | **3** |
| `quant-loop/strategies/_indicators/tests/test_vpvr_levels.py` | ≥1 | **3** |
| `quant-loop/strategies/_oos_rank_20260718/oos_walk_forward.py` | 1 | 2 |
| `quant-loop/strategies/reports/_build_correlation.py` | 1 | 2 |

- **棘手项 1 — L233 multica_workspaces 路径**（`vol_breakout_vpvr_val_fade.../data_loader.py:233`）：
  改前：`TR = Path("/home/smark/multica_workspaces/f9a9d34e-b809-4564-b0c0-b781a70a3f25/42a03459/workdir/trading")`
  这不是 quant-loop 内路径，无既有 env 约定。替换为（文件顶部需 `import os`）：
  ```python
  _WORKSPACES_ROOT = Path(os.environ.get(
      "MULTICA_WORKSPACES_ROOT",
      str(quant_loop_root().resolve().parent.parent / "multica_workspaces"),
  ))
  TR = _WORKSPACES_ROOT / "f9a9d34e-b809-4564-b0c0-b781a70a3f25/42a03459/workdir/trading"
  ```
  （`.105` 上 `quant_loop_root()` = `/home/smark/multica/quant-loop` → 推回 `/home/smark/multica_workspaces`，与原值一致。）
- **棘手项 2 — `_indicators/tests/`**：只许改 `test_iter94_20260714.py` 和 `test_vpvr_levels.py`；
  **`test_mtf_xs_pairs_base_20260718.py` 禁碰**（W4 冻结，且它 0 命中）。tests 目录文件 N=3
  （`tests/` → `_indicators/` → `strategies/` → quant-loop）。
- **步骤**：§0.2 → 逐文件 §0.3（注意 b6_* 与 tests 的 N=3）→ py_compile。
- **验收**：
  ```bash
  cd /Users/mark/multica/quant-loop
  grep -rn '/home/smark' strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714 \
    strategies/pairs_cointegration_1d_20260709 strategies/_oos_rank_20260718 \
    strategies/reports strategies/_indicators/tests --include='*.py' | wc -l   # 期望 0
  for f in $(find strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714 strategies/pairs_cointegration_1d_20260709 strategies/_oos_rank_20260718 strategies/reports -name '*.py'); do
    /Users/mark/sdk/mamba-envs/trading/bin/python3 -m py_compile "$f" || echo "COMPILE FAIL: $f";
  done
  /Users/mark/sdk/mamba-envs/trading/bin/python3 -m py_compile strategies/_indicators/tests/test_iter94_20260714.py strategies/_indicators/tests/test_vpvr_levels.py && echo OK   # 期望 OK，无 COMPILE FAIL
  ```

---

## W3-T9 — shard: 顶层 misc + data/funding fetcher（7 文件）+ 全局清零核验

- **目标**：`backtest/ analysis/ funding_analysis/ workdir/` + `data/funding/fetch_funding.py` 的 `/home/smark` 清零，并做全局清零核验。
- **机器**：mac｜**估时**：20 min｜**依赖**：W3-T1；全局核验部分还依赖 W3-T4/T5/T6/T7/T8a/T8b（其他 slice）全部落地
- **读写文件**（全部已核实存在）：

| 文件 | 命中（实测行） | N |
|---|---|---|
| `quant-loop/backtest/regress_sma34967.py` | ≥1 | 1 |
| `quant-loop/analysis/funding_correlation/run_analysis.py` | 2（L28 `DATA_DIR = Path(".../data/funding")` → `data_root() / "funding"`；L29 `OUT_DIR = Path(".../analysis/funding_correlation")` → `quant_loop_root() / "analysis" / "funding_correlation"`） | 2 |
| `quant-loop/analysis/funding_spread_heatmap/funding_spread_heatmap.py` | ≥1 | 2 |
| `quant-loop/funding_analysis/analyze_mean_reversion.py` | ≥1 | 1 |
| `quant-loop/workdir/framework_validate_scan.py` | ≥1（workdir 只改 `.py`；同目录 `.md` 报告禁碰） | 1 |
| `quant-loop/workdir/resample_1m_to_2h.py` | 4（L3-4 docstring、L33 `SRC_DIR` → `data_root() / "perp_1m"`、L34 `DST_DIR` → `data_root() / "perp_2h"`；**只改代码，严禁运行**） | 1 |
| `quant-loop/data/funding/fetch_funding.py` | 2（L71 docstring 用法示例、L105 `DEFAULT_OUT_DIR = "/home/smark/multica/quant-loop/data/funding"` → `str(data_root() / "funding")`） | 2 |

  注意：`data/funding/fetch_funding.py` 不在 round-1 T9 列的目录里，但它是唯一在 `data/` 下的非 graveyard 命中文件，
  且 W3-T15（数据刷新）依赖它完成迁移——本卡显式接管。**只编辑，不运行。**
- **步骤**：§0.2 → 逐文件 §0.3 → py_compile → 全局核验。
- **验收**：
  ```bash
  cd /Users/mark/multica/quant-loop
  grep -rn '/home/smark' backtest analysis funding_analysis validation backtests workdir \
    --include='*.py' --include='*.sh' | wc -l        # 期望 0
  grep -n '/home/smark' data/funding/fetch_funding.py | wc -l   # 期望 0
  # 全局清零（需 T4-T8 全部落地后才可能为 0；若其他 slice 未完，记录非零清单并在报告中注明是哪些 shard 的文件）：
  grep -rl '/home/smark' . --include='*.py' --include='*.sh' \
    | grep -v '_graveyard' | grep -v 'framework_adapter_' | wc -l   # 最终期望 0
  for f in backtest/regress_sma34967.py analysis/funding_correlation/run_analysis.py \
    analysis/funding_spread_heatmap/funding_spread_heatmap.py funding_analysis/analyze_mean_reversion.py \
    workdir/framework_validate_scan.py workdir/resample_1m_to_2h.py data/funding/fetch_funding.py; do
    /Users/mark/sdk/mamba-envs/trading/bin/python3 -m py_compile "$f" || echo "COMPILE FAIL: $f";
  done; echo DONE   # 期望仅 DONE
  ```

---

## 依赖与并行

```
W3-T1 (_shared/paths.py，另一 slice) ──► W3-T7 ─┐
                                      ──► W3-T8a ┼── 四卡文件互不相交，可同批并行
                                      ──► W3-T8b │
                                      ──► W3-T9 ─┘（其全局 grep 验收另需 T4/T5/T6 落地）
后续：T7 → W3-T14（mtf_xs_pairs 成本迁移，需 W4 冻结解除）；T9 + T4/T5 → W3-T15（数据刷新）
```

## 与 round-1 的偏差记录（已核实）

1. round-1 T7 假设 h1-h4 与 mtf_h2_vpvr_edge 含 `/home/smark` —— 实测 **0 命中**，T7 收缩为 12 文件。
2. round-1 T7 验收引用的 `_indicators/tests/test_mtf_xs_pairs_base_20260718.py` 存在且当前 7 passed（已实测），保留为 T7 验收。
3. `data/funding/fetch_funding.py`（2 命中）不在 round-1 任何 shard 范围内，但 T15 依赖它——已显式划入 T9。
4. T8 原 28 文件拆为 T8a（17）+ T8b（11），保证 30-min 预算。
