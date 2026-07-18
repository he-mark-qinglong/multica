# SPEC — trend_regime_gate_1d_adx_4h_1h_20260714 (V1, iter#101)

> Strategy id: `trend_regime_gate_1d_adx_4h_1h_20260714`
> Iteration: 101 (campaign `cycle-58 spec fallback`, trend-strategist pivot)
> Date: 2026-07-14
> Universe: BTCUSDT, ETHUSDT, SOLUSDT (USD-M spot, 1h entry + 4h trend + 1d regime)
> Direction: long + short (bidirectional)
> Status: paper-trade only

## 1. Goal

Pivot away from the exhausted `vpvr_reversion_*` family (cycle-46 family
rule) and the `vol_breakout + vpvr_confluence` axis (cycle-49 yielded
0/3 PROFITABLE per parent issue SMA-33912). This is **V1 of 3**
trend-dominant variants under SMA-33996.

Trend is **dominant** (>=70% alpha weight). Cross-axis element is the
**1d ADX regime gate** (~30% alpha weight). The hypothesis is: breakouts
that align with the higher-TF trend and fire inside an active trend
regime are higher-quality; regime filtering alone eliminates most
low-edge chop entries without destroying the trend edge.

## 2. Universe & data

| item        | value                                                            |
|-------------|------------------------------------------------------------------|
| symbols     | BTCUSDT, ETHUSDT, SOLUSDT                                        |
| source      | `/home/smark/multica/quant-loop/live_data/<SYM>_(1h|4h).parquet` |
| 1h frame    | entry TF                                                        |
| 4h frame    | trend filter (EMA20/EMA50 + slope)                              |
| 1d frame    | derived from 4h via deterministic resample (no extra fetch)      |
| manifest    | `data/manifest.parquet.sha256`                                   |

The 4h trend indicator is computed on the 4h frame, shifted by 1 bar
(strictly trailing), then forward-filled onto the 1h grid. The 1d
ADX is computed on the derived 1d frame, shifted by 1 bar, then
forward-filled onto the 1h grid.

## 3. Indicators

All indicators are pure functions over OHLCV frames.

| name             | TF | params    | formula                              |
|------------------|----|-----------|--------------------------------------|
| EMA20            | 4h | period=20 | standard EMA, SMA-seeded             |
| EMA50            | 4h | period=50 | standard EMA, SMA-seeded             |
| EMA50 slope      | 4h | period=50 | (EMA[t] - EMA[t-1]) / EMA[t-1]      |
| ADX(14)          | 1d | period=14 | Wilder smoothed DX                   |
| Donchian N-bar hi| 1h | N=20      | rolling max of high, shifted by 1   |
| Donchian N-bar lo| 1h | N=20      | rolling min of low, shifted by 1    |
| ATR(14)          | 1h | period=14 | Wilder ATR (price units)             |
| Volume MA(20)    | 1h | period=20 | rolling mean of volume               |

## 4. Entry (long)

All five conditions must be true on the same 1h bar:

1. `ema20_4h > ema50_4h` and `ema50_4h_slope > 0` — 4h trend is up.
2. `adx_1d > 25` — 1d regime is active.
3. `close > hh_n` (1h close above 20-bar Donchian high) — breakout.
4. `vol_ratio >= 1.0` — volume at least matches its 20-bar MA.
5. All indicators have warmed up (no NaN in any column).

## 5. Entry (short)

Mirror of §4 with `ema20_4h < ema50_4h`, slope < 0, `close < ll_n`.

## 6. Position sizing

Risk-per-trade (1% of equity) divided by the ATR-based stop distance:

    notional = (risk_per_trade * equity) / ((atr_stop * atr) / price)

Capped at `max_notional_pct = 100%` of equity per signal. Default
`risk_per_trade = 0.01`, `atr_stop = 1.5`. With `atr = $50`, `price = $60k`,
stop distance = 75 USD/coin → notional ≈ (0.01 * 100000) / (75/60000) ≈
800k, capped at 100k. In practice this means the typical position is
"as much as 1% risk allows, capped at the book."

## 7. Exits (first triggered wins)

1. **Stop**: `close < entry - 1.5 * ATR(14)` (long) or
   `close > entry + 1.5 * ATR(14)` (short).
2. **Target**: `close > entry + 3.0 * ATR(14)` (long) or
   `close < entry - 3.0 * ATR(14)` (short). Asymmetric RR = 1:2.
3. **Ratcheting trailing**: `close < highest_since_entry - 2.0 * ATR(14)`
   (long) or `close > lowest_since_entry + 2.0 * ATR(14)` (short).
4. **4h trend reversal**: `ema50_4h_slope` flips sign against open dir.
5. **Time stop**: `bars_held > 240` (10 calendar days on 1h).

## 8. Costs

10 bps taker fee + 5 bps slippage per side (canonical multica fee model).
Total round-trip cost = 30 bps.

## 9. Walk-forward / acceptance

4 sequential non-overlapping windows:
- train 1y (8760 1h bars) + test 6m (4380 1h bars) + step 6m (4380 1h bars)

In-sample and OOS metrics are computed. `bootstrap_ci` runs 10000
resamples (seed=42) on OOS trade returns. `bonferroni` reports alpha =
0.0125 (4 variants in the campaign).

Hard user gates (G1-G7 from CLAUDE.md) — must all pass to mark `done`:
- in-sample Sharpe >= 1.0
- mean OOS Sharpe >= 1.0
- mean OOS annualized return >= 15%
- mean OOS profit factor > 1.5
- max_drawdown < 25%
- bootstrap 95% CI lower bound >= 0.5 on mean OOS trade return
- Bonferroni: CI lower > 0 at alpha = 0.0125

Below any gate → archive path, status `done` with `[NOT-PROFITABLE]`
verdict + cycle-45/46 lessons note.

## 10. Out of scope (deferred)

- Framework CV (freqtrade/backtrader OOS walk-forward). The `framework_cv.json`
  artifact is reserved for a later cycle.
- Live trading.
- Per-symbol parameter optimization (single global parameter set).
- Ratchet trailing combined with break-even stop (deferred; ratchet alone is
  tested here).

## 11. Risk & human-in-the-loop

- Paper-trade only.
- `data_loader.py`, `run_backtest.py`, `walk_forward.py` all refuse to run
  with `LIVE_TRADING=1`.
- No live orders, no API keys, no external network calls.
- All gates are deterministic given the same data + config.

## 12. Fee model

Follows multica canonical fee model (see `/home/smark/multica/quant-loop/FEE_MODEL.md`):
- taker fee: 10 bps per side
- slippage: 5 bps per side
- round-trip cost: 30 bps
- fill price: `bar.close` of signal/exit bar (no next-bar open)
- compounding: yes, equity updated after each closed trade
- funding_rate / borrow_fee: not modeled (V1 is spot, not perpetual)

## 13. Trend alpha weight justification

`trend_alpha_weight = 0.7` — the 4h EMA trend gate + 4h trend-reversal
exit carry the dominant signal. The 1d ADX regime gate is the cross-axis
confirmation that filters entries but does not drive direction.

If the strategy slips below G1 (Sharpe>=1) under OOS walk-forward, the
audit trail (per-window annualized, per-window Sharpe, bootstrap CI,
bonferroni) is sufficient for `cycle-58` lessons-learned reporting without
adding artifacts.

[END OF SPEC]