# vpvr_funding_hvn_lvn_confluence_20260718 — SPEC

SMA-34901 implementation. Re-runs the VPVR-confluence long-only
prototype (SMA-34890 → `vpvr_funding_hvn_lvn_20260718`) with the
in-sample window expanded to cover **all hot-funding regimes**
(Nov 2023 → Dec 2024) and across **BTC + ETH + SOL** so the
acceptance gate of ≥30 trades can be tested honestly.

This is **not** a new signal — it is the same wire-up as the
prototype, multiplied by symbol so we can verify whether the
prototype's q1_2024 numbers (Sharpe 1.13, ann +24%, PF 1.46, 11
trades) survive when the data window is enlarged and when the
strategy is allowed to express itself on multiple perp books.

## Signal (per symbol, long-only)

For each 15m bar `t` on each symbol:

1. `funding[t]` — last funding rate paid strictly before bar `t`,
   reindexed onto the 15m bar index with `ffill` (per cycle-46
   no-look-ahead shift(1) discipline).
2. `carry_gate[t]` = `funding[t] > 0.0003` (3 bps / 8h event =
   0.03% — the issue body threshold).
3. `hvn_levels[t]` — VPVR levels from a rolling 4h window
   (180 × 4h bars ≈ 30 days) using `vpvr_levels.detect_vpvr_levels`.
   The snapshot used to evaluate bar `t` uses 4h bars in
   `[-181 .. -1]` from bar `t`.
4. `support_zone[t]` — exists iff `close[t]` is inside (or within
   `proximity_atr * ATR(14)` of) the band of any HVN level.
5. `lvn_zones[t]` — same snapshot, filtered to LVN nodes whose
   `price_low > entry_price` (above the entry HVN).

`entry_signal[t] = 1` iff `carry_gate[t]` and `support_zone[t]`
both true.

## Trade management (per symbol)

| knob | value |
|---|---|
| funding_threshold | 0.0003 (3 bps / 8h) |
| vpvr_window_4h_bars | 180 (~30 days) |
| vpvr_num_bins | 24 |
| vpvr_num_hvn | 3 |
| vpvr_num_lvn | 5 |
| vpvr_hvn_quantile | 0.85 |
| vpvr_lvn_quantile | 0.15 |
| proximity_atr | 1.0 |
| atr_period | 14 |
| cooldown_bars (15m) | 16 (~4 h) |
| max_hold_bars (15m) | 96 (~24 h) |
| funding_flip_threshold | -0.0003 |
| fee_bps_per_fill | 4.0 |
| slippage_bps_per_fill | 1.0 |
| starting_capital_usd | 100,000 |

Position sizing: fixed-fraction 1.0 of equity per trade (per
prototype, so the equity curve is the simplest read on hit-rate).

## Exit logic

1. **Primary** — `high[t] >= nearest_lvn_price_high[t]` above
   entry.
2. **Funding flip** — `funding[t] < -0.0003` (deep negative
   carry).
3. **Time stop** — `max_hold_bars` reached.

## Window

`hot_funding_2023_2024` = 2023-11-01 → 2024-12-31 (covers all
months with ≥3 funding events > 0.0003 in any of the three
symbols). This is the **in-sample** window where the prototype
is designed to fire; out-of-sample windows (2025 onward) would
have zero trades by inspection of the funding series.

15m bar data covers 2022-01-01 → 2026-07-10 (per `live_data/`);
funding data covers through 2026-07-17. We restrict to the
funding-hot window so the strategy can actually express itself.

## Acceptance gate (per issue body)

- G1 Sharpe_daily ≥ 1.0 (daily-resampled, sqrt(365.25) per
  SMA-34787 audit)
- G2 annualized_return ≥ 15%
- G3 max_drawdown_pct > -25% (i.e. MDD < 25%)
- G4 profit_factor > 1.5
- ≥30 trades in-sample
- Any gate fails → flag OBSOLETE, do NOT mark PROFITABLE

Gates are evaluated on the **combined** backtest — all three
symbols' trades aggregated into a single equity curve and trade
list, then Sharpe / ann / MDD / PF computed over the combined
ledger.

## Cross-check vs SMA-34897 baseline

The simple funding-carry `funding_carry_asym` strategy (SMA-34897)
reports:

- Sharpe_daily = -1.522, ann_ret = -0.094%, MDD = -2.72%, n=63
  trades on the Q1 2024 hot-funding window.

Our confluence signal is the same trigger (funding > 0.0003) plus
an HVN structural filter plus an LVN structural exit. We expect
the structural filter to:

- Reduce trade count (HVN proximity is rare)
- Increase per-trade hit-rate (only the "real absorption" signals)
- Improve Sharpe (less time in low-quality funding-only entries)

A negative Sharpe with similar n would mean the structural filter
adds nothing on the HVN side and may be hurting by filtering out
the few winners the carry baseline had. We report this
comparison in `results/summary.txt`.

## Out of scope (deliberate)

- Walk-forward / bootstrap CI / Bonferroni — the issue explicitly
  asks for an in-sample backtest with hard gates, not a CV
  study.
- Short side / asymmetric exit / vol-target sizing — out of scope.
- Multi-symbol portfolio sizing / correlation analysis — we treat
  symbols as independent signals combined by fixed-fraction 1.0
  per trade.

## Deliverables (per issue body)

- `results/metrics.json` — all gate values + raw returns/equity
- `results/equity.csv` — combined equity curve
- `results/trades.csv` — combined trade ledger
- `results/per_symbol_metrics.json` — per-symbol breakdowns
- `results/summary.txt` — 1-paragraph verdict (PROFITABLE /
  FAIL_GATE_X / OBSOLETE)
- `results/data_gaps.md` — any data gaps discovered

## Done criteria

- [ ] Script compiles and runs end-to-end on BTC + ETH + SOL.
- [ ] Combined metrics satisfy G1-G4 + ≥30 trades, OR are
      honestly flagged OBSOLETE.
- [ ] Cross-check vs SMA-34897 baseline appears in summary.
- [ ] Final comment posted to SMA-34901 via `multica issue
      comment add`.
