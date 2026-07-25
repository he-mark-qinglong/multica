# SPEC — momentum_trend_multi_tf_atr_scaled_v3_1h_20260712

> Strategy id: `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712`
> Iteration: 88 (V12 — orchestrator direction: V10 + BTC-only + softer -3.5 ATR stop)
> Author: vpvr-specialist (SMA-32971 follow-up)
> Date: 2026-07-13
> Universe: **BTCUSDT only** (ETH dropped — degraded more than BTC under V11 regime filter)
> Direction: long + short (bidirectional)
> Status: bootstrap, paper-trade only

## 0. V12 deltas vs V11 (and V10)

V11 (iter#87) was a regression: regime filter over-blocked in early windows and didn't materially help w1. Orchestrator direction (kimi 2dc6dfc5 cycle-14): revert to V10 signal, drop ETH, soften stop.

| delta | from V11 | to V12 | why |
|-------|----------|--------|-----|
| universe | BTC + ETH | **BTC only** | ETH more sensitive to V11 regime filter (sharpe -0.10 vs BTC +0.42 in-sample) |
| regime filter | enabled | **removed** | over-blocked recovery entries in w0 (V10's best window) |
| ATR trailing K | 2.5 | **3.5** | softer stop gives vol-spike breathing room; V11's entry-anchored stop was unnecessary safety once signal is V10-clean |
| signal logic | V11 (regime-filtered) | **V10 (verbatim)** | V10 had 3/4 OOS positive windows; V11 lost w0 |

Everything else (4h EMA trend, 1h RSI cross, 1h ADX confirm, ATR-scaled sizing, fee/slip model, walk-forward schedule) is identical to V10.

## 1. Goal

Pivot away from the `xs_pairs` family (V3-V9) — which consistently failed
`wf_ratio >= 0.5` due to regime-specific microstructure overfit — and introduce
a **multi-timeframe momentum/trend-following** strategy.

Three hypotheses are tested simultaneously by this iteration:

1. Trend strategies have **naturally lower in-sample fit** because they are
   "out of market" most of the time; this gives the walk-forward test a fair
   signal-to-noise floor (in-sample Sharpe is not artificially inflated by
   always-in positions, which is the dominant xs_pairs overfit mechanism).
2. Multi-TF confirmation (15m-equivalent at 1h entry + 4h trend filter)
   reduces false breakouts by requiring the higher-TF regime to agree with
   the lower-TF trigger.
3. ATR-scaled position sizing normalizes for vol-regime changes, reducing
   OOS decay when the strategy was sized in a low-vol regime and then runs
   in a high-vol regime.

Single primary metric: a deterministic multi-TF backtest + walk-forward that
passes the **issue-scoped evidence gate**:

- in-sample sharpe >= 0.5
- wf_ratio >= 0.5
- min_oos_sharpe >= 0 (no negative OOS windows)

## 2. Universe & data

| item        | value                                                       |
|-------------|-------------------------------------------------------------|
| symbols     | BTCUSDT, ETHUSDT (start simple; SOL later)                  |
| source      | `/home/smark/multica/quant-loop/live_data/<SYM>_<TF>.parquet`|
| TFs         | 1h (entry) + 4h (trend filter)                             |
| 1h span     | 2022-01-01 → 2026-07-10 (≈40k bars)                         |
| 4h span     | 2022-01-01 → 2026-07-10 (≈10k bars)                         |
| manifest    | `data/manifest.parquet.sha256` (per-file SHA256)            |

The 4h frame is used only for the EMA(50) trend filter; signals fire on 1h
bars. The 4h EMA(50) value is **forward-filled** to the 1h grid so the
filter is constant between 4h closes (no leakage).

## 3. Indicators

All indicators are pure functions of an OHLCV frame.

| name             | TF | params       | formula                                                                |
|------------------|----|--------------|------------------------------------------------------------------------|
| EMA(50)          | 4h | period=50    | standard EMA, seed from SMA(50)                                         |
| EMA slope        | 4h | period=50    | `(EMA[t] - EMA[t-1]) / EMA[t-1]`                                       |
| RSI(14)          | 1h | period=14    | Wilder smoothed RS, output in [0,100]                                  |
| ADX(14)          | 1h | period=14    | Wilder ADX with DI+/DI-                                                |
| ATR(14)          | 1h | period=14    | Wilder ATR, output in **price units**                                  |

Look-ahead discipline: every indicator on bar `t` is computed from
`[t-W, t-1]` data only. The 4h EMA is `shift(1)` to make the filter
strictly trailing.

## 4. Entry (long)

All three conditions must be true on the same 1h bar:

1. `ema50_4h_slope[t] > 0` — 4h regime is up
2. `rsi14_1h[t-1] < 50 AND rsi14_1h[t] >= 50` — RSI crosses 50 upward in trend dir
3. `adx14_1h[t] > 20` — directional trend present (not choppy)

## 5. Entry (short)

Mirror of §4: `ema50_4h_slope < 0` AND `rsi14[t-1] > 50 AND rsi14[t] <= 50`
AND `adx14 > 20`.

## 6. Position sizing

Vol-scaled target:

    size_quote = (0.01 * equity) / (atr14 / price)

This targets 1% equity-at-risk per 1-ATR move. Concretely, with
`atr14 = $100` and `price = $60,000`, the notional is `0.01 * equity / (100/60000)`
= `6 * equity`, capped at the per-signal max notional.

| rule                  | value                                                      |
|-----------------------|------------------------------------------------------------|
| risk per signal       | 1% of equity per 1-ATR move                                |
| max notional          | 5% of equity per signal                                    |
| max gross exposure    | 5% of equity across open positions                         |
| per-symbol cap        | 1 position (long OR short, never both)                     |

## 7. Exits

First triggered wins:

1. **4h trend reversal**: `ema50_4h_slope[t]` flips sign against the
   current direction (long closed if slope<0; short closed if slope>0).
2. **1h RSI cross back**: `rsi14[t-1] > 50 AND rsi14[t] <= 50` for a long
   (mirror for short).
3. **ATR trailing stop**: `close[t] < entry - 2.5 * ATR(14)[t]` (long)
   or `close[t] > entry + 2.5 * ATR(14)[t]` (short). The anchor is the
   **entry price** (not a ratcheting high/low); the ratchet variant is
   deferred.

A bar that satisfies an exit is the **exit bar**; the next bar is the first
that may re-enter.

## 8. Costs

| item                   | value                                                       |
|------------------------|-------------------------------------------------------------|
| fees per side          | 1.0 bps                                                     |
| slippage per side      | 1.0 bps                                                     |
| total round-trip cost  | 4.0 bps                                                     |

Applied at entry (`close * (1+cost_per_side)`) and at exit
(`close * (1-cost_per_side)`).

## 9. Walk-forward / acceptance

Walk-forward (per spec):

- 4 sequential non-overlapping windows
- train 1y (8760 1h bars) + test 6m (4380 1h bars) + step 6m
- schedule: train `[k*4380, k*4380 + 8760]`, test `[k*4380 + 8760, k*4380 + 13140]`
- bars_per_year for Sharpe = 8760 (1h, 24*365)

In-sample (full-period) and OOS metrics are computed. `wf_ratio` is
defined as:

    wf_ratio = mean(OOS sharpe) / in-sample sharpe

Issue-scoped evidence gate (must all be true to mark this issue done):

- [ ] in-sample sharpe >= 0.5
- [ ] wf_ratio >= 0.5
- [ ] min_oos_sharpe >= 0 (no negative OOS windows)

Below any gate → archive path, status `done` with `[NOT-PROFITABLE]` verdict.

Hard user gates G1-G7 from CLAUDE.md are deferred (this iteration is a
single-strategy pivot to test the family hypothesis, not a ship candidate
yet). Cycle-46 lessons: trend filters destroy carry; multi-TF confirmation
is expected to underperform vs single-TF in the first iteration. The
target here is **methodological validation**, not G1-G7 compliance.

## 10. Out of scope (deferred to next iteration)

- G1-G7 hard user gates (Sharpe ≥ 1.0, MDD < 25%, etc.)
- Framework CV (freqtrade/backtrader OOS walk-forward)
- Bootstrap 95% CI
- FWER Bonferroni correction (single-strategy iter, no family yet)
- Trailing-stop ratchet
- Portfolio combo with vpvr_reversion / xs_pairs

## 11. Risk & human-in-the-loop

- Paper-trade only — no live orders are placed by this strategy.
- Irreversible operations: none.
- `data_loader.py` and `run_backtest.py` both refuse to run if
  `LIVE_TRADING=1` is set.

---

## Fee model

This strategy follows multica canonical fee model (see `../FEE_MODEL.md`):

- taker_fee: 0.0010 (10 bps) per side
- slippage: 0.0005 (5 bps) per side
- effective round-trip cost: 0.0030 (30 bps)
- fill price: `bar.close` of the signal/exit bar (NOT next-bar open)
- compounding: yes, equity updated after each closed trade
- funding_rate / borrow_fee: not modeled

If this strategy deviates from the spec, document the deviation here as
`[DEVIATION] <field>: <value> (vs spec <baseline>)`.

[DEVIATION] fees: spec uses 1bps/side (matching V5) but canonical is 10bps taker.
This iteration aligns with V5 fee convention so the comparison to xs_pairs is
apples-to-apples. Revisit if/when this strategy is upgraded to a ship candidate.