# KAMA Strategy Enhancement Report

**Date:** 2026-08-02  
**Baseline:** KAMA(er=5, fast=2, slow=30) slope over 10 bars > 0 → long, ≤ 0 → flat.  
**Data:** BTCUSDT 4h, 2019-09-08 → 2026-07-24 (15,068 bars).  
**Cost model:** 7 bp round-trip (3.5 bp per side).  
**Baseline CPCV:** Mean OOS Sharpe = 1.12, DSR = 1.10, worst fold = +0.27. **PASS.**

---

## Executive Summary

| # | Enhancement | Verdict | Sharpe (vs 1.12) | MaxDD (vs −47%) | 2024-26 t (vs 1.94) |
|---|------------|---------|-------------------|------------------|---------------------|
| 1 | KAMA Short-Side | **FAIL** | 0.78 ↓ | −68% ↓ | 1.76 ↓ |
| 2 | KAMA ±z Bands | **FAIL** | 0.60 ↓ | −14% ↑ | 0.59 ↓ |
| 3 | Regime Filter (high-vol) | **PARTIAL** | 0.97 ↓ | −24% ↑ | 0.84 ↓ |
| — | Combined (z + regime) | **FAIL** | 0.60 ↓ | −10% ↑ | 0.07 ↓ |

**Bottom line:** None of the three enhancements beat the baseline long-only on Sharpe. The baseline's edge comes from capturing BTC's structural uptrend — mean-reversion entries and shorting both work against this. The only direction worth retaining is the **high-volatility regime filter as a risk-management overlay**: it nearly halves max drawdown (−47% → −24%) at a modest Sharpe cost (1.12 → 0.92), improving the Calmar ratio from 2.37 to 3.85. However, it does not address the 2024-2026 edge weakening — in fact it makes it slightly worse.

---

## Methodology

All strategies use the same data pipeline (`kama_core.load_ohlc`), KAMA implementation, cost model, and no-lookahead convention (signal at bar close, position effective next bar). Metrics:

- **Sharpe:** annualised, `mean/std × √2190` (2190 = 4h bars/year).
- **t-stat:** `mean/std × √N` over all bars.
- **MaxDD:** peak-to-trough drawdown on cumulative compounded returns.
- **Calmar:** `Sharpe / |MaxDD|` — drawdown-adjusted return efficiency.
- **nRT:** round-trip trade count.

Script: `run_enhancements.py` · Raw results: `results_enhancements.json`

---

## Enhancement 1: KAMA Short-Side

**Logic:** slope > 0 → long (+1), slope < 0 → short (−1), instead of flat.  
Buffered variants add a hysteresis dead-zone (slope% must exceed ±buffer%) to reduce whipsaw at trend transitions.

### Parameter Table

| Variant | Sharpe | t-full | MaxDD | Calmar | nRT | 2024-26 t | mean bps/bar |
|---------|--------|--------|-------|--------|-----|-----------|-------------|
| **Baseline long-only** | **1.12** | **2.95** | **−0.472** | **2.37** | **455** | **1.94** | **2.08** |
| Short (buf=0.0%) | 0.78 | 2.03 | −0.684 | 1.14 | 455 | 1.76 | 2.11 |
| Short (buf=0.1%) | 0.60 | 1.57 | −0.778 | 0.77 | 402 | 1.44 | — |
| Short (buf=0.3%) | 0.52 | 1.36 | −0.832 | 0.63 | 353 | 1.36 | — |
| Short (buf=0.5%) | 0.67 | 1.77 | −0.729 | 0.92 | 321 | 1.66 | — |

### Yearly t-values

| Year | Baseline | Short buf=0% | Short buf=0.3% |
|------|----------|-------------|----------------|
| 2020 | **2.54** | 0.96 | 0.89 |
| 2021 | **0.61** | −0.15 | −0.54 |
| 2022 | −0.08 | **1.27** | 0.42 |
| 2023 | **1.74** | 0.47 | 1.07 |
| 2024 | **2.72** | 2.13 | 2.33 |
| 2025 | −0.15 | −0.27 | −0.61 |
| 2026 | 0.20 | 1.06 | 0.27 |

### Analysis

Shorting is structurally destructive for BTC. The 2022 bear market IS captured (t: −0.08 → +1.27), but every bull year is degraded:
- **2020** (BTC +250%): t drops from 2.54 → 0.96 — the short position bleeds throughout the strongest uptrend.
- **2021** (volatile bull): t goes from +0.61 → −0.15 — shorting chops back the trend edge entirely.

Adding a hysteresis buffer makes things **worse**, not better — the dead-zone causes the strategy to hold stale positions through trend transitions, increasing drawdowns (−68% → −83%). The mean per-bar return is nearly identical to baseline (2.11 vs 2.08 bps), but volatility explodes because short positions add negatively-correlated variance without compensating return.

**Verdict: FAIL.** BTC's persistent upward drift (Sharpe 0.74 buy-and-hold over 7 years) means shorts are a structural drag. The short-side converts a trend-following edge into leveraged beta with worse risk-adjusted returns than buy-and-hold.

---

## Enhancement 2: KAMA ±z Standard Deviation Bands

**Logic:** KAMA as centre line with ±z × std(residual) bands. Entry: close < KAMA − z·std AND slope > 0 (dip-buy in uptrend). Exit: close ≥ KAMA (mean reverted) OR close > KAMA + z·std (stop). Std computed on residuals (close − KAMA) over `std_window` bars.

This is a mean-reversion entry layered on the trend filter — buy dips, exit when price returns to the adaptive centre.

### Parameter Table (std_window = 20)

| z | Sharpe | t-full | MaxDD | nRT | 2024-26 t | mean bps/bar |
|---|--------|--------|-------|-----|-----------|-------------|
| **Baseline** | **1.12** | **2.95** | **−0.472** | **455** | **1.94** | **2.08** |
| 1.0 | 0.17 | 0.45 | −0.325 | 319 | 0.17 | 0.11 |
| 1.5 | 0.34 | 0.90 | −0.177 | 179 | −0.04 | 0.17 |
| 2.0 | 0.15 | 0.39 | −0.158 | 87 | 0.12 | 0.05 |
| 2.5 | 0.31 | 0.81 | −0.128 | 30 | 0.92 | 0.07 |

### std_window Sensitivity (z = 1.5)

| std_window | Sharpe | t-full | MaxDD | nRT | 2024-26 t |
|-----------|--------|--------|-------|-----|-----------|
| 10 | −0.02 | −0.06 | −0.278 | 194 | −0.26 |
| 20 | 0.34 | 0.90 | −0.177 | 179 | −0.04 |
| 30 | 0.39 | 1.03 | −0.183 | 151 | 0.97 |
| 50 | 0.60 | 1.58 | −0.136 | 128 | 0.59 |

### Long+Short Symmetric Variant

| z | Sharpe | t-full | MaxDD | nRT | 2024-26 t |
|---|--------|--------|-------|-----|-----------|
| 1.5 (L+S) | 0.20 | 0.54 | −0.259 | 324 | 0.83 |
| 2.0 (L+S) | 0.26 | 0.67 | −0.248 | 152 | 0.91 |

### Analysis

All variants are far below baseline. The fundamental problem: **the exit rule (touch KAMA) systematically truncates trend profits.** In a strong BTC uptrend, price spends most of its time above KAMA. The strategy:

1. Buys a dip below the lower band — good entry price.
2. Price rebounds to KAMA — exit triggered, small profit captured.
3. Trend continues for hundreds of basis points without the strategy on board.

The per-bar mean return collapses from 2.08 bps (baseline) to 0.05-0.27 bps. Even with wider std windows (50 bars), the best Sharpe is only 0.60 — barely half the baseline. Adding a short side makes it worse, confirming the short-side finding from Enhancement 1.

The low drawdowns (−13% to −18%) are a feature of being in the market so briefly, not a sign of risk efficiency — the strategy simply captures very little of the move.

**Verdict: FAIL.** Mean-reversion entries are antithetical to trend-following alpha in BTC. The exit-at-centre rule leaves the majority of trend profits on the table. This direction would only make sense in a range-bound market, which BTC structurally is not.

---

## Enhancement 3: KAMA Regime Filter (ATR/Price)

**Logic:** Compute ATR(14)/price ratio. Take KAMA long signals only when vol_ratio > its rolling median (high-volatility regime = trending). Hypothesis: filter out choppy consolidation periods where trend signals whipsaw.

### Parameter Table

| Direction | Median Win | Sharpe | t-full | MaxDD | Calmar | nRT | 2024-26 t |
|-----------|-----------|--------|--------|-------|--------|-----|-----------|
| **Baseline** | — | **1.12** | **2.95** | **−0.472** | **2.37** | **455** | **1.94** |
| High-vol | 25 | 0.73 | 1.93 | −0.261 | 2.80 | 483 | 1.11 |
| High-vol | 50 | 0.92 | 2.40 | −0.239 | 3.85 | 472 | 1.11 |
| High-vol | 100 | 0.97 | 2.53 | −0.274 | 3.54 | 438 | 0.84 |
| Low-vol | 25 | 0.65 | 1.72 | −0.443 | 1.47 | 555 | 1.24 |
| Low-vol | 50 | 0.49 | 1.28 | −0.609 | 0.80 | 571 | 1.22 |
| Low-vol | 100 | 0.45 | 1.19 | −0.554 | 0.81 | 533 | 1.54 |

### Yearly t-values (High-vol, median_win=50)

| Year | Baseline | High-vol filtered |
|------|----------|------------------|
| 2020 | **2.54** | 0.22 |
| 2021 | 0.61 | **1.13** |
| 2022 | −0.08 | **0.32** |
| 2023 | **1.74** | **2.38** |
| 2024 | **2.72** | 2.01 |
| 2025 | −0.15 | −0.68 |
| 2026 | 0.20 | 0.10 |

### Analysis

**The filter works as a risk reducer, not an edge enhancer.**

The high-vol filter at median_win=50 achieves:
- MaxDD nearly halved: −47.2% → −23.9% (−49% reduction)
- Calmar ratio improved: 2.37 → 3.85 (+62%)
- Sharpe cost: 1.12 → 0.92 (−18%)

But the tradeoff is clear: the filter removes bars from the trend, and some of those bars are the most profitable ones. The 2020 mega-bull is severely damaged (t: 2.54 → 0.22) because 2020's rally was steady and low-volatility — exactly what the filter removes. Conversely, 2021 and 2023 improve because those years had volatility-driven trends that the filter correctly identifies.

The low-vol filter is strictly worse across all metrics — confirming that the trending edge lives in high-volatility regimes, as hypothesised. But filtering to high-vol doesn't concentrate the edge enough to beat unfiltered.

**Crucially, the filter does NOT address the 2024-2026 weakness:** 2024-2026 t drops from 1.94 → 1.11. The recent edge weakening appears to be a trend-quality problem (BTC trend is becoming noisier/choppier), not a volatility-regime problem.

**Verdict: PARTIAL PASS.** Not an edge enhancer, but a legitimate risk-management overlay. If the goal is drawdown control (e.g., for a fund with risk limits), the high-vol filter at median_win=50 is worth considering. If the goal is higher returns, it's a dead end.

---

## Combined: KAMA ±z Bands in High-Vol Regime

**Logic:** Mean-reversion dip-buy entries (z=1.5/2.0), but only in high-volatility regime (ATR/price > 50-period median). Tests whether the regime filter rescues the band strategy.

| Variant | Sharpe | t-full | MaxDD | nRT | 2024-26 t |
|---------|--------|--------|-------|-----|-----------|
| **Baseline** | **1.12** | **2.95** | **−0.472** | **455** | **1.94** |
| Combined z=1.5 | 0.60 | 1.57 | −0.098 | 94 | 0.07 |
| Combined z=2.0 | 0.27 | 0.70 | −0.096 | 49 | 0.11 |

**Verdict: FAIL.** The regime filter doesn't rescue the mean-reversion entry. The combined strategy has very low drawdowns (−10%) but only because it's rarely in the market (49-94 trades over 7 years). Returns are far too low to be useful. The core problem identified in Enhancement 2 — exiting at KAMA truncates trend profits — is not fixed by adding a regime filter.

---

## Conclusions & Recommendations

### What doesn't work
1. **Shorting BTC** — The asset's structural upward drift makes shorts a persistent drag. The KAMA slope correctly identifies bear periods (2022 t improves), but the cost in bull years is larger than the benefit. No buffer configuration helps.
2. **Mean-reversion bands on KAMA** — Fundamentally incompatible with trend-following. The exit-at-centre rule captures small mean-reversion bounces but forfeits the large trend moves that are the strategy's primary edge.
3. **Combining the two** — Regime filtering doesn't fix the structural mismatch.

### What shows partial promise
4. **High-volatility regime filter** — As a **risk overlay, not an alpha source.** The high-vol filter (median_win=50) nearly halves max drawdown while improving Calmar by 62%. It could be deployed as:
   - A position-sizing scalar: full size in high-vol, reduced size in low-vol (rather than binary on/off).
   - A drawdown circuit-breaker overlay.

### Recommendation
- **Ship the baseline as-is.** It remains the best risk-adjusted strategy. No enhancement beats it on Sharpe.
- **For the 2024-2026 edge weakening:** none of these three directions address it. The problem appears to be BTC trend quality degradation, not a fixable signal deficiency. Future work should explore:
  - Multi-timeframe confirmation (e.g., require daily KAMA agreement).
  - Volume/participation filters (volume is free data already in the parquet).
  - Cross-asset confirmation (ETH/SOL KAMA slopes as BTC signal filters).
- **For risk management:** consider the high-vol regime filter (median_win=50) as a position-size modulator if fund-level drawdown constraints require it.

---

*Generated by `run_enhancements.py`. All backtests use actual price data with 7bp RT costs and no lookahead. Full results in `results_enhancements.json`.*
