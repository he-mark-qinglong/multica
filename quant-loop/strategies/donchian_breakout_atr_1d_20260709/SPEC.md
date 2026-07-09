# SPEC — Donchian Breakout + ATR Trailing (1d crypto)

> Strategy id: `donchian_breakout_atr_1d_20260709`
> Author: trend-strategist (SMA-30709)
> Date: 2026-07-09
> Universe: BTCUSDT, ETHUSDT, SOLUSDT (USD-M futures, 1d bars)
> Direction: long + short (bidirectional)
> Status: bootstrap, paper-trade only

This spec follows the 11-gate STRATEGY_DEV_SPEC used by the quant-loop squad.
Each gate is explicit, checkable, and links to the implementation that satisfies it.

## 1. Goal

Introduce the first **trend-following** strategy into quant-loop so the portfolio
is no longer 100% vpvr_reversion (mean-reverting). Trend strategies make money
when the recent past keeps extending; mean reversion loses money in that regime.
Pairing them produces a directional hedge.

Single primary metric of success for this bootstrap iteration: a deterministic
1d backtest over real 1m-aggregated crypto data that produces a valid trades
list, equity curve, and per-instrument metrics — **not** a vpvr_reversion port.

## 2. Universe & data

| item        | value                                              |
|-------------|----------------------------------------------------|
| symbols     | BTCUSDT, ETHUSDT, SOLUSDT (USD-M perp)             |
| source      | Binance USD-M, 1m klines, canonical parquet         |
| bar freq    | 1d (1440 1m bars resampled UTC, daily close = 00:00 UTC next day) |
| span        | 2024-04-23 → 2026-06-23 (≈791 days)                |
| manifest    | `data/manifest.parquet.sha256` (sha256 per parquet) |

`tests/test_data_loader.py` is the integrity gate; resampled 1d frames must
contain the expected row count and the close of the last 1d bar must equal the
close of the last 1m bar in the source.

## 3. Indicators

All indicators are pure functions of `df = {date, open, high, low, close, volume}`.

| name               | params        | formula                                                                                 |
|--------------------|---------------|-----------------------------------------------------------------------------------------|
| Donchian upper     | N=20          | `df['high'].rolling(N-1).max().shift(1)` — uses prior N-1 bars (no look-ahead)          |
| Donchian lower     | N=20          | `df['low'].rolling(N-1).min().shift(1)`                                                  |
| ATR(14)            | period=14     | Wilder smoothing of true range, output in **price units** (no %-conversion here)        |
| ATR_ma(100)        | period=100    | `ATR.rolling(100).mean()` — long-run volatility baseline                                |
| Volume_ma(20)      | period=20     | `df['volume'].rolling(20).mean()`                                                       |
| ADX(14)            | period=14     | Wilder ADX with DI+/DI- pre-step                                                        |
| True Range         | -             | `max(high-low, \|high-prev_close\|, \|low-prev_close\|)`                                |

Look-ahead discipline: every indicator is `shift`-ed where appropriate so a
signal at bar `t` is computed only from `[t-W, t-1]` data.

## 4. Entry (long)

All four conditions must be true on the same bar:

1. `close[t] > donchian_upper[t]` — close breaks the prior 20-bar high
2. `ATR(14)[t] > 0.7 * ATR_ma(100)[t]` — volatility not collapsed
3. `volume[t] > 1.2 * volume_ma(20)[t]` — participation confirmation
4. `ADX(14)[t] > 20` — directional trend present

## 5. Entry (short)

Mirror of §4: close below `donchian_lower[t]`, plus the same three confirmations.

## 6. Position sizing

| rule                | value                                                              |
|---------------------|--------------------------------------------------------------------|
| per-signal weight   | 1% of account equity                                                |
| max gross exposure  | 5% of equity (sum of absolute weights across all open positions)   |
| per-symbol cap      | 1 position (long OR short, never both)                              |
| allocation basis    | equity at signal bar (mark-to-market, no leverage)                 |

Sizing is computed inside `run_backtest` from the equity series, not from
a hard-coded notional.

## 7. Exits

A position closes on the **first** triggered rule:

1. **ATR trailing stop** (long): `close[t] < entry_price - 3 * ATR(14)[t]`
   (short: `close[t] > entry_price + 3 * ATR(14)[t]`). The trailing anchor is
   `entry - 3*ATR` (not the current low); tightened-from-prior-trailing version
   is deferred to a later variant to keep this bootstrap deterministic.
2. **Donchian opposite break** (long): `close[t] < donchian_lower[t]`
   (short: `close[t] > donchian_upper[t]`).
3. **Time stop**: position age > 30 trading days AND unrealized PnL
   < 1.5 * ATR(14)[t] → forced close at `close[t]`.

Exits apply intra-bar: a bar that satisfies an exit is the **exit** bar; the
next bar is the first one that may re-enter.

## 8. Costs

| item                    | value                    |
|-------------------------|--------------------------|
| fees per side           | 1.0 bps                  |
| slippage per side       | 1.0 bps                  |
| total round-trip cost   | 4.0 bps                  |

Applied at entry (`close * (1+cost_per_side)`) and at exit
(`close * (1-cost_per_side)`).

## 9. Walk-forward / acceptance

This bootstrap iteration ships a single full-period backtest per symbol and a
**naive hold-baseline** (`buy on first bar, sell on last bar`) for sanity
checking. Walk-forward is intentionally deferred to a follow-up variant under
EPIC-D; the bootstrap goal is "the strategy runs end-to-end on real data",
not "the strategy is statistically validated".

Acceptance gates (must all be true to mark this issue done):

- [ ] `python3 -m pytest tests/ -q` → 0 failed
- [ ] `python3 run_backtest.py` exits 0, writes `results/summary.json`,
      `results/equity_*.csv`, `results/trades_*.csv`
- [ ] `data/manifest.parquet.sha256` exists and matches the actual SHA256
      of each source parquet
- [ ] At least 1 trade in at least 1 of the 3 instruments
- [ ] `git log -1` shows the commit on `feat/donchian-breakout-atr-1d-20260709`
- [ ] No `vpvr_reversion` logic imports anywhere in the new strategy

## 10. Out of scope (deferred to EPIC-D variants)

- Walk-forward CV with non-overlapping test folds
- DSR / multiple-testing correction
- Parameter sensitivity sweep
- Combo / portfolio weight with vpvr_reversion
- Trailing-stop ratchet (current uses fixed `entry - 3*ATR`)
- Funding-rate aware position sizing for perpetuals

## 11. Risk & human-in-the-loop

- Paper-trade only — no live orders are placed by this strategy.
- Irreversible operations: none (this iteration is read-only against parquet).
- Configuration changes that could affect real money are blocked by the lack
  of a `LIVE_TRADING=1` env gate in `data_loader.py` and `run_backtest.py`.
