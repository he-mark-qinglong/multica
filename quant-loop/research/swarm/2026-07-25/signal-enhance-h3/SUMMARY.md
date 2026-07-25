# signal-enhance-h3 — Research Summary

**Date:** 2026-07-25  
**Researcher:** Kimi swarm agent  
**Output directory:** `/Users/mark/multica/quant-loop/research/swarm/2026-07-25/signal-enhance-h3/`

## 1. What was done

1. **Diagnosed the existing H3 BTC/SOL full-history trade log** (`strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/results/trades_winner_atr_mult_1_00.csv`, 39 617 trades). Computed per-trade gross/net PnL, win rate, exit-reason split, `z_at_entry` deciles, direction bias and hourly seasonality.
2. **Ran a targeted 2024-only verification** (`quick_verify.py`) of four plausible signal enhancements, reusing the H3 signal builder and cost model from the shared base. This is *directional* evidence only — not the campaign walk-forward OOS.
3. **Produced two diagnostic charts:**
   - `chart_diag_exit_reasons.png` — baseline exit-reason counts and mean PnL.
   - `chart_quick_verify_2024.png` — 2024 Sharpe and mean net PnL across variants.

## 2. Why H3 loses money at the signal layer

The H3 entry rule (`|z_1m| ≥ 2.5`, funding regime filter, ATR sizing) generates a **positive gross edge that is almost exactly eaten by cost**.

| metric | value |
|---|---|
| trades (full history) | 39 617 |
| mean **net** PnL | **-7.82 bps/trade** |
| mean **gross** PnL | **+0.18 bps/trade** |
| net win rate | 26.96 % |
| gross win rate | 41.08 % |
| cost assumption | 8 bps RT per pair trade (config `fees=1bps/side`, `slippage=1bps/side`) |

**Root cause:** 78 % of trades exit on `regime_break` (z continues past ±3.0 against the position). These trades lose a mean of **-19.7 bps net**. The remaining 21 % that mean-revert to `z_exit=0.5` win **+39.9 bps net**, but there are not enough of them and they do not cover the cost drag.

| exit reason | share | median bars held | mean net PnL |
|---|---|---|---|
| regime_break | 78.2 % | 2 | -19.7 bps |
| z_mean_revert | 21.2 % | 57 | +39.9 bps |
| max_holding | 0.6 % | 240 | -153.5 bps |

Other observations:
- Gross trade-level PF is ~1.01 — essentially breakeven before cost.
- There is **no meaningful directional bias**: long-a-short-b and short-a-long-b lose almost equally.
- `z_at_entry` deciles show losses are **not concentrated at the most extreme z**; even modestly extreme entries (deciles 4-7) lose ~7-9 bps net. This means the simple z-score threshold is not selective enough.
- Hourly seasonality exists (UTC 15:00-17:00 worst, 20:00-23:00 best), but the spread is only ~4-5 bps — not large enough to rescue the strategy alone.

## 3. Tested improvements

All numbers below are from the **2024-only quick verification**. They are in-sample for 2024 and must be validated with the campaign walk-forward before any ship decision.

| variant | trades | mean net (bps) | win rate | Sharpe (daily) | PF | max DD |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 8 379 | -6.96 | 26.5 % | 2.44 | 1.015 | -10.7 % |
| slope_fav_4 | 1 101 | **+6.10** | **48.5 %** | **7.53** | 1.088 | -3.2 % |
| slope_adv_4 | 7 856 | -7.54 | 25.8 % | 1.01 | 1.007 | -12.3 % |
| adverse_stop_0.7 | 2 758 | -4.90 | 52.2 % | 2.61 | 1.015 | -9.0 % |
| **slope_fav_4 + stop_0.7** | **704** | **+15.43** | **68.9 %** | **8.07** | 1.087 | -3.2 % |
| candle_confirm | 5 314 | -7.45 | 26.4 % | 0.81 | 1.006 | -10.7 % |
| funding_diff_filter | 3 956 | -7.11 | 27.0 % | 1.46 | 1.013 | -8.8 % |

### 3.1 Favourable 15m z-slope filter (verified directionally)

**Logic:** Only enter when the 15-minute z-score is already turning back toward the mean. For long-a-short-b (`z < -2.5`) require `z_slope_15m > 0`; for short-a-long-b require `z_slope_15m < 0`.

**2024 result:** Drops trade count to ~13 % of baseline but flips the mean trade from -7 bps to **+6 bps**, win rate to 48.5 %, and Sharpe to 7.53. This is the single strongest improvement tested.

### 3.2 Tight adverse z-stop (verified directionally)

**Logic:** Replace the wide `regime_break=3.0` stop with a stop at `|z_entry - z_current| ≥ 0.7`. This cuts the typical losing trade before it becomes a large regime-break loss.

**2024 result:** Win rate rises to 52 % and max DD falls, but mean net is still **-4.9 bps** because the tighter stop is triggered on many small adverse moves that later mean-revert. It is most useful **combined** with the slope filter.

### 3.3 Combined slope filter + tight stop (verified directionally)

**Logic:** Apply both filters: only enter on a favourable 15m turn, and cap adverse excursion at 0.7 z.

**2024 result:** 704 trades, mean net **+15.4 bps**, win rate **68.9 %**, Sharpe **8.07**. This is the candidate configuration to take forward.

### 3.4 Other filters (not convincing)

- **Candle confirmation** (require the signal 1m candle to move in the trade direction): slightly worse.
- **Funding-differential filter** (only enter when the funding spread favours the leg you are long): marginal improvement (Sharpe 1.46) but not enough to flip net PnL.

## 4. Gate status

Using the **full-history baseline metrics** from `results/metrics.json` and the daily equity curve:

| gate | criterion | baseline | status |
|---|---|---|---|
| G1 | Sharpe ≥ 1.0 | 1.35 | **PASS** |
| G2 | annualized return ≥ 15 % | 25.0 % | **PASS** |
| G3 | max drawdown > -25 % | -13.7 % | **PASS** |
| G4 | profit factor > 1.5 | 1.22 (daily bars) | **FAIL** |
| G5 | CPCV mean OOS Sharpe ≥ 1.0 | not run | **PENDING** |
| G6 | bootstrap CI95 lower ≥ 0.5 | 1.914 (PR#6 OOS) | **PASS** (from ledger) |
| G7 | deflated Sharpe > 0 | not run | **PENDING** |

The 2024 signal-enhancement results **do not yet count toward gates** because they are in-sample. They only show that the signal layer has headroom.

## 5. Verdict: continue or KILL?

**Do not KILL yet.** The H3 signal has a structural gross edge that is currently swamped by execution cost and by a poorly timed entry (78 % regime-break exits). The 2024 verification shows that a 15m z-slope filter plus a tight adverse stop can turn the mean trade strongly positive and lift Sharpe above the gate threshold. Before shipping, this must survive:

1. Full-history **walk-forward OOS** (the campaign’s 7-window anchored walk-forward).
2. **Cost stress test** at the ratified 22 bps RT maker/taker model.
3. **G5/G7** cross-validation and deflated-Sharpe checks.

If the combined filter fails any of those, the strategy should be **KILL**ed — the baseline cannot be rescued by execution alone.

## 6. Next 1-2 actions

1. **Run full-history walk-forward OOS for the combined candidate** (`slope_fav_4` + `adverse_stop_z=0.7`) using the campaign train/test windows, and compare OOS Sharpe/PF/maxDD against the baseline. This is the only way to rule out 2024 overfitting.
2. **If OOS passes G1-G4**, add the two filters as configurable parameters in the H3 base (without modifying production code, write the patch in this swarm directory) and hand off to the execution-cost (`H3-execution-maker`) workstream to see if the strategy survives 22 bps RT.

## 7. Files produced

- `data_loader_patch.py` — swarm-local data loader for canonical 1m + funding data.
- `run_experiments.py` — full-history experiment harness (cached signals + variant loops).
- `quick_verify.py` — 2024-only verification harness.
- `quick_verify_2024.json` — metrics table for the 2024 variants.
- `plot_results.py` — chart generator for full-history results.
- `make_summary_charts.py` — chart generator used for this summary.
- `chart_diag_exit_reasons.png`
- `chart_quick_verify_2024.png`
- `SUMMARY.md` (this file)
