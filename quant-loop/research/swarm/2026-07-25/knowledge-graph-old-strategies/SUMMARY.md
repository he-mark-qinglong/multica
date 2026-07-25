# knowledge-graph-old-strategies — Engineering Review

**Date:** 2026-07-25  
**Scope:** gate/ledger hardening, old-strategy module mining, multi-strategy portfolio combination.  
**Output directory:** `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/knowledge-graph-old-strategies/`

No production code was modified. All server/ledger proposals are design files in this directory.

---

## 1. gate-ledger-fix

### 1.1 Problem

`server/internal/gate/gate.go` treats a missing metric as a *skipped* rule (`skipNote = "skipped: no data"`, `Pass: true`).  The overall status becomes `pass` as long as `sharpe` is present.  This is why a strategy like `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` (Sharpe 31.7, no ann_return, no OOS, PF 2.72) is reported as `pass` even though it has no evidence of annualised profitability or out-of-sample robustness.

`server/internal/handler/metric.go` forwards the parsed blob unchanged; there is no profit-factor fallback and OOS windows are not enforced.

The top-level `results-ledger.md` collapses "framework-consistent" and "actually profitable" into a single `PASS` verdict, which is semantically confusing.

### 1.2 Proposed strict gate

See `gate_strict_proposal.go` for the reference implementation.  Key changes:

| rule | old behaviour | proposed behaviour |
|------|---------------|--------------------|
| `sharpe` missing | skipped | `NO_DATA` — nothing to evaluate |
| `ann_return` / `max_drawdown` / `profit_factor` missing | skipped | `FAIL` — insufficient evidence |
| `profit_factor` missing | skipped | compute fallback from `DailyReturns` if supplied |
| `oos_windows` missing or `< 3` | skipped | `FAIL` |
| `oos_sharpe` missing | skipped | `FAIL` |

New status machine:

- `NO_DATA` — sharpe or core fields missing.
- `FAIL` — a hard gate failed (or required field missing).
- `HOLD` — partial pass, needs more work.
- `PROFITABLE` — in-sample gates pass (sharpe ≥ 1, ann_return ≥ 15%, maxDD < 25%, PF > 1.5, ≥ 30 trades) but OOS not yet proven.
- `CV_PASS` — all hard gates pass including OOS (windows ≥ 3, OOS sharpe ≥ 1).

### 1.3 Server patch surface

- `server/internal/gate/gate.go`: replace `DefaultRules`/`Evaluate` with the strict version; add `Metrics.DailyReturns` and `profitFactorFromReturns` fallback.
- `server/internal/handler/metric.go`: parse a `daily_returns` array from the metrics blob and forward it to the gate.  See `metric_handler_patch.md` for the exact diff.

### 1.4 Ledger state machine

`build_results_ledger_proposal.py` emits `results-ledger-proposed.md` with explicit columns for in-sample metrics, OOS sharpe/windows, framework consistency, and a verdict in `{CV_PASS, PROFITABLE, HOLD, KILL, NO_DATA, UNTESTED}`.

Notable reclassifications on current data:

- `mtf_xs_pairs_1m_15m_2h_h3_20260718` → `NO_DATA` because `metrics.json` lacks `profit_factor`.
- `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` (graveyard) → `KILL` (ann_return missing).
- `vpvr_xs_basis_zscore_15m_funding_filter_20260712` → `KILL` (PF 0.10, Sharpe 0.25).
- `pairs_cointegration_1d_20260709` → `NO_DATA` (PF missing in `metrics.json`).

### 1.5 Files

- `gate_strict_proposal.go`
- `metric_handler_patch.md`
- `build_results_ledger_proposal.py`
- `results-ledger-proposed.md`

---

## 2. knowledge-graph-old-strategies — module catalog

### 2.1 Method

`scan_strategies.py` AST-walks `quant-loop/strategies/` (active) and `quant-loop/strategies/_graveyard/` (all families), extracting:

- entry files (`strategy.py`, `run_backtest.py`, `run_*.py`, `*_backtest.py`);
- imports (detects `_shared` usage);
- cost-model source (`_shared` vs hardcoded);
- gate logic source (`_shared` vs manual vs none);
- feature modules per category (signal, risk/sizing, execution, cost, evaluation);
- reusable function/class candidates.

It writes:

- `strategy_catalog.json` (one record per strategy)
- `strategy_catalog.csv` (flattened by feature category)
- `module_catalog.md` (human-readable)

Actual directories scanned: **100** (active + graveyard), not the ~122 mentioned in the prompt.  The current repo has 51 active + 49 graveyard entries.

### 2.2 High-level findings

| finding | detail |
|---------|--------|
| `_shared` adoption is low | Most strategies do **not** import `_shared.execution.cost_model`, `_shared.gates.enforce`, or `_shared.validation.compute_metrics`.  They re-implement cost/gate/metrics locally. |
| Cost model fragmentation | Only `loid_iceberg_v4_1m_20260720` and `vpvr_xs_basis_zscore_15m_funding_filter_20260712` clearly use `_shared.execution.cost_model`.  Many strategies hardcode 8 bps/24 bps assumptions. |
| Gate logic fragmentation | `_shared.gates.enforce` is used by `loid_iceberg_v4` only.  Most graveyard strategies have ad-hoc `if sharpe > X` checks or no gate logic. |
| Duplicated signal primitives | `pair_zscore`, `wilder_atr`, `aggregate_ohlcv`, `rolling_vpvr_levels`, `funding_filter_mask`, and `zscore_slope` appear in many directories, often copy-pasted with minor drift. |

### 2.3 Clean migration candidates (move to `_shared/`)

From the catalog, these files/functions are generic and already isolated:

- `strategies/_indicators/mtf_xs_pairs_base_20260718.py` — `aggregate_ohlcv`, `align_lower_to_upper`, `pair_zscore`, `wilder_atr`, `zscore_slope`, `rolling_vpvr_levels`, `sharpe_daily_resampled`.
- `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/strategy.py` — `pair_basis_zscore`, `compute_spread`, `funding_filter_mask`, `_smooth_funding_series`.
- `strategies/_graveyard/funding_carry/funding_oscillator_mr/strategy.py` — `_event_zscore` (generic event-driven z-score).

These should become `_shared/indicators/pair_zscore.py`, `_shared/indicators/vpvr.py`, `_shared/indicators/funding_regime.py`, etc., with tests.

### 2.4 Cautionary / strategy-specific modules (do NOT move)

- Framework adapter files (`framework_adapter_backtrader.py`, `framework_adapter_freqtrade.py`, `framework_adapter_vectorbt.py`) — per-engine glue, tied to campaign harness.
- Parameter sweep runners (`run_param_scan*.py`, `sizing_sweep.py`) — campaign-specific.
- Any file with hypothesis-specific symbols (`_h3`, `_v72`, `_u5`, `_p3opt`) or hardcoded pair lists.
- Graveyard macro/options/on-chain signals with tiny sample sizes (< 100 trades) and high in-sample Sharpe — keep as archive, not reusable code.

### 2.5 Files

- `scan_strategies.py`
- `strategy_catalog.json`
- `strategy_catalog.csv`
- `module_catalog.md`

---

## 3. multi-strategy-portfolio

### 3.1 Goal

Test whether combining a weak-positive H3 baseline, a strong 2024-filtered H3 candidate, and a weak ledger strategy (`vpvr_xs_basis_zscore_15m_funding_filter_20260712`) produces a portfolio Sharpe / drawdown profile better than holding H3 alone.

Because the signal-enhance candidate only has a verified 2024 run, the experiment is run on the **2024-01-01 → 2024-12-31** overlap.

### 3.2 Data

| component | source | notes |
|-----------|--------|-------|
| H3 baseline 2024 | `equity_h3_baseline_2024.csv` (generated by `build_h3_candidate_equity.py`) | 8 379 trades, Sharpe 2.44 |
| H3 candidate (`slope_fav_4` + `adverse_stop_0.7`) | `equity_h3_slope_fav_4_stop_0_7_2024.csv` | 704 trades, Sharpe 8.07 |
| v72 xs-basis z-score | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/results/equity_A_iter72_BTCUSDT_ETHUSDT.csv` | 15m equity resampled to daily; Sharpe 0.25 over full history |

### 3.3 Methodology

`portfolio_combinations.py` computes daily returns, aligns 366 common days, and tests:

1. **Equal weight** — `1/3` each.
2. **Risk parity** — inverse-volatility weights.
3. **Correlation-off** — inverse-volatility weights penalised by average pairwise correlation (long-only).

Weights are rebalanced daily.  Turnover is approximated from drift + rebalance.  Cost sensitivity is reported at 0 / 8 / 22 bps round-trip per unit of turnover.

### 3.4 Results

| method | w_h3_base | w_candidate | w_v72 | Sharpe | ann_return | maxDD | turnover/yr | Sharpe @ 22 bps |
|--------|-----------|-------------|-------|--------|-----------|-------|-------------|-----------------|
| h3_baseline (single) | 1.0 | 0.0 | 0.0 | **2.49** | 45.9% | -9.6% | 0.00 | — |
| h3_candidate (single) | 0.0 | 1.0 | 0.0 | **8.04** | 111.0% | -2.0% | 0.00 | — |
| v72 (single) | 0.0 | 0.0 | 1.0 | **0.86** | 5.8% | -5.9% | 0.00 | — |
| equal_weight | 0.333 | 0.333 | 0.333 | 5.22 | 48.7% | -2.7% | 0.60 | 5.21 |
| risk_parity | 0.201 | 0.337 | 0.461 | 5.66 | 42.9% | -2.4% | 0.55 | 5.64 |
| correlation_off | 0.175 | 0.301 | 0.524 | 5.37 | 38.1% | -2.6% | 0.53 | 5.35 |

Pairwise correlations (2024 daily returns):

|  | h3_base | h3_candidate | v72 |
|--|---------|--------------|-----|
| h3_base | 1.00 | 0.47 | 0.05 |
| h3_candidate | 0.47 | 1.00 | 0.01 |
| v72 | 0.05 | 0.01 | 1.00 |

### 3.5 Interpretation

- The **candidate strategy alone dominates** every portfolio in this subsample (Sharpe 8.04, maxDD -2.0%).  Adding the weak v72 leg does **not** produce a portfolio better than the best single strategy.
- However, the portfolios do improve dramatically over the **H3 baseline alone**: Sharpe rises from 2.49 to ~5.4–5.7 and maxDD falls from -9.6% to ~-2.5%.
- v72 is almost uncorrelated with both H3 series, so diversification benefit is real but its Sharpe is too low to offset the candidate dilution.
- Cost sensitivity is negligible at 8–22 bps because daily turnover is modest (~0.5–0.6×/year).

### 3.6 Verdict & next steps

For the **2024 subsample**, the answer is: **no combination beats the H3 candidate as a single strategy**, but combinations materially improve robustness vs. the H3 baseline.

Before trusting this for capital allocation:

1. **Run the signal-enhance candidate on the full history** (same walk-forward windows as H3) to confirm the 2024 result is not a lucky year.
2. **Add a genuinely positive, uncorrelated third strategy** instead of a near-zero-Sharpe one.  Candidates from the ledger: `pairs_cointegration_1d_20260709` (high Sharpe but sparse event dates) or `loid_iceberg_v4_1m_20260720` after fixing its reported maxDD anomaly.
3. **Use walk-forward OOS returns** rather than in-sample 2024 returns for weight estimation, to avoid look-ahead bias in risk-parity/correlation-off weights.
4. **Stress-test cost** with the ratified 22 bps futures model and realistic rebalance slippage.

### 3.7 Files

- `build_h3_candidate_equity.py`
- `portfolio_combinations.py`
- `candidate_metrics_2024.json`
- `portfolio_results.json`
- `portfolio_results.csv`
- `portfolio_equity.csv`
- `equity_h3_baseline_2024.csv`
- `equity_h3_slope_fav_4_stop_0_7_2024.csv`

---

## 4. Deliverables

All files are in `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/knowledge-graph-old-strategies/`:

```text
SUMMARY.md                              # this file
gate_strict_proposal.go                 # strict gate design
metric_handler_patch.md                 # metric.go diff
build_results_ledger_proposal.py        # new ledger builder
results-ledger-proposed.md              # sample ledger with new state machine
scan_strategies.py                      # AST scanner
strategy_catalog.json / .csv            # raw scan output
module_catalog.md                       # human-readable module map
build_h3_candidate_equity.py            # generate 2024 H3 candidate equity
portfolio_combinations.py               # equal/risk-parity/correlation-off combinations
portfolio_results.json / .csv           # combination metrics
portfolio_equity.csv                    # combined equity curves
candidate_metrics_2024.json             # baseline + candidate 2024 metrics
equity_h3_baseline_2024.csv
equity_h3_slope_fav_4_stop_0_7_2024.csv
```

---

**Key conclusion:** The current gate/ledger pipeline conflates missing data with pass status and conflates framework consistency with profitability.  A strict gate + explicit `{CV_PASS, PROFITABLE, HOLD, KILL, NO_DATA}` ledger state machine is the smallest fix.  Old strategies contain reusable signal primitives (`pair_zscore`, `rolling_vpvr`, `funding_filter`) that should migrate to `_shared/`, while framework adapters and campaign-specific runners should stay put.  In the 2024 multi-strategy test, diversification lowers drawdown but the H3 signal-enhance candidate alone still has the highest Sharpe — portfolio value only appears once more independent, positive-expectation strategies are added.
