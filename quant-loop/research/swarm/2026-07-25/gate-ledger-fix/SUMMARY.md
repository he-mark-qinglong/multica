# gate-ledger-fix research summary

**Date:** 2026-07-25  
**Output directory:** `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/gate-ledger-fix/`  
**Scope:** server gate rules, ledger verdict semantics, old-strategy module inventory, multi-strategy portfolio methodology.

---

## 1. gate-ledger-fix

### 1.1 Problem

Current `server/internal/gate/gate.go` treats missing metrics as "skipped" rules that do not fail the gate. Only `sharpe` is mandatory. This lets a strategy like `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` (Sharpe 31.7, no `ann_return`, no OOS, no `profit_factor`) obtain an overall `pass`.

`server/internal/handler/metric.go` parses the metrics blob and forwards nil values to the gate, so the silent skip happens at ingest time and is persisted into `run_metric.gate_status`.

### 1.2 Proposed strict gate rules

| Rule | Threshold | Required? | Missing-behavior |
|------|-----------|-----------|------------------|
| `sharpe` | `>= 1.0` | yes | no-data (cannot judge) |
| `ann_return` | `>= 0.15` | yes | **fail** |
| `max_drawdown` | `< 0.25` (magnitude) | yes | **fail** |
| `profit_factor` | `> 1.5` | yes | **fail** (or compute from `daily_returns`/`equity_curve` in blob) |
| `oos_windows` | `>= 3` | yes | **fail** |
| `oos_sharpe` | `>= 1.0` | yes | **fail** |

Status values: `pass` | `fail` | `no-data`.

### 1.3 Server patch (minimal)

Files touched (proposals in this directory, no production edit):

- `gate_proposal.go` — strict `DefaultRules`, `missing required metric` note, `StatusNoData`.
- `metric_proposal.go` — helper to compute daily-return `profit_factor` from blob arrays (`daily_returns`, `equity_curve`, `nav_curve`) when the agent omits `profit_factor`.
- `gate_test_proposal.go` — new tests: missing core fields fail, H3-no-PF fails, OOS windows <3 fails, sharpe-missing is no-data.
- `migration_proposal.sql` — update `run_metric.gate_status` CHECK constraint to accept `no-data`; recommend backfill via `POST /api/metrics/reevaluate`.

### 1.4 Demonstration

`gate_demo.py` mirrors current vs proposed evaluation on representative cases:

| case | current | proposed | why changed |
|------|---------|----------|-------------|
| vpvr_stable_depeg_p3opt_091 (sharpe only) | pass | **fail** | missing ann_return / maxDD / PF / OOS |
| H3 baseline as-uploaded (no PF) | pass | **fail** | missing profit_factor |
| H3 baseline with PF=1.22 | fail | **fail** | PF below 1.5 threshold |
| strong candidate | pass | pass | all fields present and pass |
| overfit (OOS windows=2) | fail | fail | unchanged |
| empty metrics | no-data | no-data | unchanged |

Run: `python3 gate_demo.py` → `gate_demo_results.json`.

### 1.5 Ledger verdict redesign

Current ledger conflates "framework-consistent" with "profitable". Proposed state machine:

| Verdict | Meaning |
|---------|---------|
| `CV_PASS` | Cross-framework agreement (W5 / within tolerance) but in-house metrics do **not** yet pass the strict gate. |
| `PROFITABLE` | Framework agreement **and** strict gate pass (sharpe≥1, ann≥15%, \|mdd\|<25%, PF>1.5, oos_sharpe≥1, oos_windows≥3). |
| `HOLD` | Data exists but neither clean pass nor clear kill; needs more evidence. |
| `KILL` | Graveyard, explicit AUTO-ARCHIVE / NOT-PROFITABLE, or hard gate fail with no redeeming signal. |
| `UNTESTED` | No metrics and no framework output. |

`ledger_demo.py` applies this to the current strategy set:

```
Verdict counts: CV_PASS=4, PROFITABLE=0, HOLD=3, KILL=66, UNTESTED=26
```

Key changes vs current ledger:
- Old `PASS` strategies with framework agreement but missing/bad in-house metrics become `CV_PASS` (e.g. `momentum_trend_btc_only_softer_stop`, `vpvr_carry_term_8h`, `vpvr_xs_smart_routing_15m`).
- H3 baseline becomes `KILL` under the strict gate (PF missing / below threshold).
- `pairs_cointegration_1d` becomes `CV_PASS` (framework OK but in-house gate not met).

Files:
- `ledger_proposal.py` — replacement `_status()` and table headers for `scripts/build_results_ledger.py`.
- `ledger_demo.py` — reference implementation run on current data.
- `ledger_demo_counts.json` — resulting verdict distribution.

### 1.6 Next action for gate-ledger-fix

1. Review and land `gate_proposal.go` + `metric_proposal.go` + `gate_test_proposal.go` + `migration_proposal.sql`.
2. After deploy, run `POST /api/metrics/reevaluate` to backfill stored rows.
3. Update `scripts/build_results_ledger.py` with the new `_status()` and header schema.
4. Decide whether the `profit_factor > 1.5` threshold is too strict for low-trade-count high-Sharpe variants (e.g. signal-enhance-h3 2024 combined filter: PF 1.087, Sharpe 8.07). This is a policy decision, not a code decision.

---

## 2. knowledge-graph-old-strategies

A reproducible scanner (`knowledge_graph_scanner.py`) recursively scanned `quant-loop/strategies/` (active + `_graveyard` + `_indicators`).

### 2.1 Coverage

- 38 strategy directories
- 64 `strategy.py` entry files
- 362 Python modules classified
- 60 `data_loader.py`, 62 `run_backtest.py`, 72 framework-adapter files

### 2.2 Module classification counts

| Category | Count | Examples |
|----------|-------|----------|
| data_utils | 171 | data loaders, calendars, walk-forward runners, CPCV helpers |
| anti_pattern | 92 | duplicated framework adapters, smoke/diagnose scripts |
| entry | 68 | `strategy.py`, variant runners, prototypes |
| signal_generation | 26 | `build_signals.py`, `indicators.py`, VPVR/cointegration/state-machine |
| risk_sizing | 2 | `sizing_sweep.py`, `kill_criteria.py` |
| evaluation_metrics | 2 | `optimize.py`, `b6_fwer.py` |
| execution_cost | 1 | `fill_engine.py` |

### 2.3 Reusable modules (top candidates for `_shared/`)

- **Signal generation** — `_indicators/mtf_xs_pairs_base_20260718.py`, `_indicators/vpvr_levels.py`, graveyard `build_signals.py` variants with generic `_atr`, `_vpvr_snapshot_levels`, `_zscore`, cointegration helpers.
- **Data/orchestration** — repeated `SourceManifest`-based `data_loader.py` files, `walk_forward.py` schedulers, `run_cpcv.py` helpers.
- **Risk/sizing** — `mtf_xs_pairs_1m_15m_2h_h3_20260718/sizing_sweep.py` (vol-target, ATR, Kelly helpers).
- **Execution** — `_graveyard/paper_trading/.../fill_engine.py` (paper account + fill engine).
- **Evaluation** — `pairs_cointegration_1d_20260709/optimize.py`, `vol_breakout_vpvr_val_fade_1h_5m_20260714/scripts/b6_fwer.py`.

### 2.4 Anti-patterns (do NOT migrate)

- `framework_adapter_*.py` files (72 of them) — copy-pasted per strategy, coupled to vectorbt/backtrader/freqtrade.
- One-off `diagnose.py`, `inspect_*.py`, `smoke_test.py` scripts.
- Strategy-specific runners with hardcoded symbols/paths.

### 2.5 Outputs

- `strategy_module_inventory.json` — 362 records.
- `strategy_module_inventory.csv` — same as CSV.
- `knowledge_graph_summary.md` — full overview + per-category tables + anti-pattern list + migration roadmap.

---

## 3. multi-strategy-portfolio

### 3.1 Objective

Test whether combining the H3 baseline, the 2024 signal-enhance-h3 filtered variants, and other ledger strategies with numbers produces a portfolio better—on a risk-adjusted basis—than any single strategy.

### 3.2 Candidates & window

- **Window:** 2024-01-01 → 2024-12-31 (366 days)
- `h3_baseline` — actual daily equity from `equity_winner_atr_mult_1_00_1d.csv` (8,390 trades)
- `signal_slope_fav_4`, `signal_slope_fav_4_stop_0_7`, `signal_adverse_stop_0_7` — simulated Gaussian daily returns matching `quick_verify_2024.json`
- `pairs_cointegration_1d` — actual daily equity (21 trades; pre-August flat)
- `vpvr_xs_basis_zscore_15m` — actual 15m equity resampled to daily
- `vpvr_xs_smart_routing_15m` — actual 15m equity resampled to daily
- `loid_iceberg_v4_1m` — **excluded**: reported maxDD `-130.830` is inconsistent with a tradable strategy and no equity file exists.

### 3.3 Methods

1. **Equal weight** — 1/K, rebalanced daily.
2. **Risk parity** — weights ∝ 1/σ_i on an expanding 30-day+ window; 5% annual vol floor.
3. **Decorrelation** — weights ∝ (1/avg_abs_corr_i) × (1/σ_i).

### 3.4 Results

| Method | Sharpe | AnnReturn | MaxDD | Avg Turnover (ann.) | Sharpe @ 8bps | Sharpe @ 22bps |
|--------|-------:|----------:|------:|--------------------:|--------------:|---------------:|
| h3_baseline (standalone) | 1.93 | 24.7% | -9.77% | — | — | — |
| signal_slope_fav_4_stop_0_7 (standalone) | 8.39 | 114.3% | -3.44% | — | 5.55 | 0.56 |
| Equal weight | **9.52** | 43.5% | -1.23% | 0.64 | 9.51 | 9.49 |
| Risk parity | **9.59** | 28.1% | -0.68% | 0.68 | 9.57 | 9.54 |
| Decorrelation | **9.67** | 28.5% | -0.71% | 1.97 | 9.61 | 9.51 |

All three combinations beat the H3 baseline (Sharpe 1.93) and the best standalone signal variant (Sharpe 8.39) on a risk-adjusted basis, with max drawdown below -1.3% versus -9.8% for H3 alone.

### 3.5 Why it works

- Pairwise correlations among the seven series are mostly near-zero or negative; adding low-volatility, uncorrelated streams dilutes the high-Sharpe signal variant enough to cut portfolio volatility dramatically.
- Portfolio turnover is <2×/year, so fee sensitivity is minimal. Standalone high-frequency strategies are far more fee-sensitive under a full-notional assumption.

### 3.6 Caveats

- Signal-enhance variants are simulated, not observed daily series.
- `pairs_cointegration_1d` has only 5 non-zero daily returns in 2024; its large risk-parity/decorrelation weight is partly a sparsity artifact.
- `vpvr_xs_smart_routing_15m` is slightly negative standalone but treated as a diversifier.
- Standalone fee model assumes full-notional round-trips per trade and is conservative.

### 3.7 Next validation steps

1. Collect real daily equity curves for the signal-enhance-h3 variants.
2. Run a true walk-forward: estimate weights on a rolling window and evaluate on the next month.
3. Refine the fee model using actual position sizes from trade files.
4. Test Aug–Dec 2024 overlap only, where pairs cointegration is active.
5. Add a "no signal variants" portfolio to isolate whether the improvement comes from weak-strategy blending or from the strong signal variants.

### 3.8 Outputs

- `portfolio_experiment.py` — reproducible script
- `portfolio_results.json` — full numeric results + correlation matrix
- `portfolio_summary.md` — detailed interpretation
- `weights_equal_weight.csv`, `weights_risk_parity.csv`, `weights_decorrelation.csv`

---

## 4. Files produced

```
/Users/mark/multica/quant-loop/research/swarm/2026-07-25/gate-ledger-fix/
├── SUMMARY.md                              # this file
├── gate_proposal.go                        # strict gate evaluator
├── metric_proposal.go                      # daily-return PF helper for ingest
├── gate_test_proposal.go                   # new gate unit tests
├── migration_proposal.sql                  # DB CHECK constraint update
├── gate_demo.py                            # current vs proposed gate demo
├── gate_demo_results.json                  # demo output
├── ledger_proposal.py                      # ledger verdict redesign
├── ledger_demo.py                          # reference ledger verdict run
├── ledger_demo_counts.json                 # verdict distribution
├── knowledge_graph_scanner.py              # strategy scanner
├── strategy_module_inventory.json          # 362 module records
├── strategy_module_inventory.csv
├── knowledge_graph_summary.md              # module inventory summary
├── portfolio_experiment.py                 # portfolio combination script
├── portfolio_results.json                  # numeric results + correlation matrix
├── portfolio_summary.md                    # portfolio interpretation
├── weights_equal_weight.csv                # daily weights
├── weights_risk_parity.csv
└── weights_decorrelation.csv
```

---

## 5. Key conclusions

- **Gate fix is the highest-impact change**: making core metrics required eliminates silent passes for under-documented strategies and aligns ingest-time evaluation with the intended bar.
- **Ledger verdicts must separate framework agreement from profitability**: the proposed `CV_PASS` / `PROFITABLE` / `HOLD` / `KILL` state machine removes the current ambiguity.
- **Old strategies contain reusable infrastructure**, but the bulk (72 framework adapters) is copy-pasted anti-pattern. The `_shared/` migration priority is: indicators → data loaders → sizing/execution → evaluation helpers.
- **Multi-strategy portfolio combinations beat H3 on a risk-adjusted basis**: equal-weight, risk-parity, and decorrelation all produce 2024 Sharpe >9.5 with max drawdown <1.3%, versus H3 standalone Sharpe 1.93 / maxDD -9.8%. The result is driven by low correlation and low portfolio turnover, but signal-enhance variants need real daily equity curves before it can be treated as evidence.
