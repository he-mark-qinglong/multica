# loid_vpvr_confluence_20260717 — SPEC

SMA-34803 prototype. Wires the upstream iceberg (LOID) detector
(SMA-34796) and the upstream VPVR level detector (SMA-34790) into a
multi-TF backtest over the last 30 days of BTCUSDT 1m/15m/4h data.

## Goal

Score the *quality* of an iceberg-bar signal when it sits on top of a
structural VPVR level. We do not test profitability gates G1–G7 here
(this is a 30d prototype, not a shippable strategy).

## Signal

For each bar `t` at each TF:

1. `iceberg_flag[t]` from `iceberg_detector.detect_iceberg_bars`
   (volume z-score ≥ 3.0, range ratio ≤ 0.75, both relative to
   shifted rolling baselines).
2. `near_hvn[t]` = `|close[t] - hvn_mid[t]| ≤ hvn_atr_buffer * ATR[14](t)`.
3. `near_lvn[t]` = `|close[t] - lvn_mid[t]| ≤ lvn_atr_buffer * ATR[14](t)`.
4. `signal[t] = +1` if `iceberg_flag & near_hvn`, `-1` if
   `iceberg_flag & near_lvn`, else `0`.

`hvn_mid` and `lvn_mid` come from a rolling VPVR over a TF-shaped
window (`vpvr_window_bars_{tf}`). The profile is recomputed on a
snapshot grid and forward-filled; both the snapshot and the rolling
ATR are `shift(1)`'d so the level used to evaluate bar `t` is
computed on data strictly before `t` (no look-ahead).

Direction is determined by the **VPVR level type**, not the iceberg
side proxy, because the staged OHLCV parquets carry no
`taker_buy_base` column and the side proxy would be `unknown` for
every bar.

## Trade management

| knob | 1m | 15m | 4h |
|---|---|---|---|
| `take_profit_atr_k` | 1.5 | 1.5 | 1.5 |
| `hard_stop_atr_k`   | 1.0 | 1.0 | 1.0 |
| `max_hold_bars`     | 30  | 8   | 6  |
| `cooldown_bars`     | 5   | 5   | 5  |
| `risk_target_pct`   | 0.005 | 0.005 | 0.005 |
| fee + slippage / fill | 4 + 1 bps | 4 + 1 bps | 4 + 1 bps |

Round-trip cost = 2 × (fee + slippage) = 10 bps.

## Metrics

`sharpe_daily` is the **only** Sharpe emitted. It is computed by:

1. resample per-bar equity to 1D (`.resample("1D").last()`),
2. `daily_returns = daily_equity.pct_change().dropna()`,
3. `sharpe = mean(daily_returns) / std(daily_returns) * sqrt(365.25)`.

This is the convention mandated by the quant-analyst audit
(SMA-34787). Per-trade and per-bar Sharpe are deliberately **not**
emitted in `metrics.json` to avoid the audit finding they correct.

## Out of scope (deliberate)

- Walk-forward / bootstrap CI / Bonferroni. 30 days is too short
  for a real OOS test. The `metrics.json` envelope is explicitly a
  prototype readout, not a G1–G7 ship check.
- Asymmetric exit, vol-target sizing, regime filter, funding
  carry. All out of scope per SMA-34803.
- Trade-tape–level iceberg clusterer (SMA-34796's
  `trade_size_clusterer.py`). We only have OHLCV; the bar-level
  detector is the only entry point that works on the staged data.

## Upstream dependency notes

- `iceberg_detector.py` (SMA-34796) is imported as-is. We do not
  modify the upstream module.
- `_indicators/vpvr_levels.py` (SMA-34790) is imported as-is. We
  do not modify the upstream module.
- If a future iteration needs `taker_buy_base` for side-aware
  signals, the staging data must be replaced — flagged here rather
  than as an interface change to the upstream modules.
