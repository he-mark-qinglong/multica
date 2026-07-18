# V6 Multi-TF VPVR-Confluence Vol Breakout — SMA-32226 / iter#80

> Parent spec: **SMA-32226** (`[SPEC] V6 vpvr_vol_breakout_2tf_v1_20260711 (iter#80)`)
> This file is a working copy of the spec for code-level reference. The single
> source of truth lives on the parent issue; this file is updated only when the
> spec changes.

## Strategy identity

| field | value |
|---|---|
| name | `vpvr_vol_breakout_2tf_v1_20260711` |
| iteration | 80 |
| universe | BTCUSDT, ETHUSDT, SOLUSDT |
| source | Binance spot klines, real (NO synthetic) |
| window | 2022-01-01 → 2026-07-11 (~ 4.5y) |
| coarse TF | 4h (filter + VPVR confluence) |
| fine TF | 1h (entry + exit) |
| fill convention | `bar[t].close` → `bar[t+1].open ± cost` |
| cost | 1 bp fees + 1 bp slippage = 2 bp/side |

## Indicators

### 4h coarse filter

- `realized_vol_4h(N=20)` — rolling std of log returns, 20 × 4h = ~ 3.3d
- `vol_median_4h(M=120)` — rolling median of realized_vol, 120 × 4h = ~ 20d
- `vol_regime_4h` = `realized_vol_4h / vol_median_4h`
- `ATR_4h(20)` — Wilder ATR over 20 × 4h bars
- `vpvr_poc_4h(240, 24 bins)` — Volume Profile Visible Range POC, rolling
  240-bar (40d) window, 24 price bins
- `vpvr_dist_atr_4h` = `|close_4h − vpvr_poc_4h| / ATR_4h(20)`

### 1h fine entry/exit

- `range_high_1h(20)` — Donchian upper, **shift(1)** to drop look-ahead
- `range_low_1h(20)` — Donchian lower, **shift(1)**
- `ATR_1h(14)` — Wilder ATR over 14 × 1h bars
- `realized_vol_1h(20)` — for vol-targeted sizing on 1h

## Entry (long-only, 1 position max per symbol)

ALL must hold at `1h bar[t]`:

1. `vol_regime_4h > 1.2` at the most-recently-closed 4h bar
2. `vpvr_dist_atr_4h < 0.5` at the most-recently-closed 4h bar (price
   near VPVR POC = confluence with "fair value")
3. `close_1h > range_high_1h(20)[shift(1)]` — 1h breakout, no look-ahead
4. No open position for that symbol

## Exit (priority order, 1h precision)

ANY of:

1. `close_1h < range_low_1h(20)[shift(1)]` — trend-fail on fine TF (PRIMARY)
2. `low_1h − 2.0 × ATR_1h(14)` — trailing stop
3. `vol_regime_4h < 0.8` at most-recently-closed 4h bar (vol cooling)
4. `bars_held >= 30` — time stop, 30 × 1h = 30 hours

## Position sizing (vol-targeted)

```
size_units = 0.10 * NAV / (close_1h * realized_vol_1h(20) * sqrt(BARS_PER_YEAR_1H))
size_capped = 0.10 * NAV / close_1h              # 10% NAV hard cap
size = min(unbounded, capped)
```

with `BARS_PER_YEAR_1H = 8766` (`365.25 × 24`, 24/7 crypto, 1h bar).
`sqrt(8766) ≈ 93.612`.

- Per-symbol cap: 1 position
- Concurrent cap: 3 (one per BTC / ETH / SOL)

## Hard rules

- Real Binance data only — 4h + 1h parquet, both 2022-01-01 → 2026-07-11
- SHA256 manifest covers both 4h AND 1h source files
- Fill-at-next-bar-open, **no look-ahead** — verified by
  `test_no_lookahead_1h_or_4h`
- 4h indicator values: take from the most-recently-closed 4h bar
  (i.e. the 4h bar whose `open_time <= 1h bar[t].open_time`)
- Per-symbol state machine; up to 3 concurrent

## Cycle-44 defect warning (re-stated for V6)

V5's 3 `equity_<SYM>.csv` files were byte-identical (sha256
`5359992d6df11d8b` × 3) — all were the portfolio equity curve, NOT
per-symbol PnL paths. V6 fixes this in `run_backtest.py`:

```
equity_sym[t] = starting_capital
                + sum(pnl_usd for sym's trades whose exit_fill_date <= t)
```

The 3 CSVs MUST be **distinct** and reconcile with `summary.json`
per-symbol `final_equity`.

## Hard gates (ship gate)

1. `sharpe_oos >= 1.0`
2. `min(annualized_return_full, mean(annualized_return_oos)) >= 15%`
3. `profit_factor > 1.5`
4. `max_drawdown < 25%`
5. `freqtrade_oos_sharpe >= 1.0` AND `backtrader_oos_sharpe >= 1.0`

Anything below → archive `[NOT-PROFITABLE]`. **No V7**.

## Axis-difference vs V5 (campaign discipline)

| axis | V5 | V6 | diff |
|---|---|---|---|
| timeframe | 4h only | 4h filter + 1h entry | ✓ different |
| signal mix | vol breakout only | vol breakout + VPVR POC proximity | ✓ different |
| position cap | 5% NAV | 10% NAV (vol-target is risk-control already) | different by design |
| exit priority | vol_contraction first | trend_fail first | ✓ different |
| time-stop | 60 × 4h = 240h | 30 × 1h = 30h | ✓ different |

3 axes differ (timeframe, signal mix, exit priority). Family is
**trend-following with multi-TF filter and VPVR confluence** — adjacent
to V5 but distinct.

## Pipeline

- **B1** indicator-engineer (vpvr-specialist as primary): `indicators.py`
- **B2** vpvr-specialist: `strategy.py` + `run_backtest.py` + tests
- **B3** backtest-runner: 8-window walk-forward
- **B4–B6** quant-researcher: critique + freqtrade + backtrader CV
- **B7** code-reviewer: sign-off
- Phase 6: ship or archive per gates. **No V7** either way.