# SPEC: xs_momentum_rank_1d_20260709

> Cross-sectional momentum rank strategy (long top-K, short bottom-K) on Binance
> USD-M 1d bars, paper-trade only.
>
> Iteration: 1 (bootstrap). Branch: 001. Parent: none (first cross-sectional
> strategy in quant-loop).

## Goal

Add a **cross-sectional** (i.e. multi-symbol, rank-based) factor strategy to
quant-loop to complement the existing 12 single-symbol strategies. The
strategy ranks the universe by trailing momentum and runs an equal-weight
long-top-K / short-bottom-K book, rebalanced daily.

## Universe

`config.json::target_universe` declares the 10 spec majors:

```
BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, ADAUSDT, DOGEUSDT,
AVAXUSDT, LINKUSDT, DOTUSDT
```

`config.json::active_universe` declares the symbols actually feed by the
local 1m parquet tree today:

```
BTCUSDT, ETHUSDT, SOLUSDT
```

The remaining 7 symbols have no 1m parquet on disk (Binance USD-M klines
have only been pulled for the top 3 majors in this workspace). Adding them
is **forward-compatible** -- the loader and backtest accept any
non-empty universe list -- and the run will start trading them as soon as
their parquets land in `data/`.  Today the backtest runs on the
3-symbol subset, which means K shrinks at rebalance time
(`strategy.select_long_short` shrinks K to fit when the universe is
smaller than `top_k + bottom_k`).

## Signal

Per symbol at every daily bar:

```
momentum_score = 0.5 * return_30d + 0.3 * return_7d + 0.2 * return_3d
```

Returns are computed on the daily close.  Bars where the 30d return is
not yet defined yield NaN and the symbol is excluded from the ranking.

## Portfolio

| Field              | Spec          | Code default         | Comment |
|--------------------|---------------|----------------------|---------|
| top-K              | 3             | `top_k_default=3`    | shrinkable to N//2 |
| bottom-K           | 3             | `bottom_k_default=3` | shrinkable to N - top-K |
| gross exposure     | ≤ 60% of NAV  | `gross_target_pct=0.6` | clamps if per-symbol cap binds |
| per-leg weight     | 1/6 of gross  | `gross/2/K`, min(`per_symbol_max_pct_nav`) | matches the spec at full 10-symbol universe |
| per-symbol cap     | 10% of NAV    | `per_symbol_max_pct_nav=0.10` | hard floor on individual leg |
| rebalance cadence  | every 1d bar  | one rebalance per daily bar after warmup | signals computed on prior bar |
| turnover           | charged       | `fee + slippage` on each `|delta|` | see Cost |

## Universe filter (per spec)

A symbol is included in a day's ranking iff BOTH:

1. It has ≥ `min_bars_in_last_7d` (default 5) non-NaN bars in the trailing
   7 calendar days -- excludes fresh listings.
2. Its most recent day's USD volume (`close * volume`) is >
   `min_usd_volume_per_day` (default $1M).

Both thresholds are in `config.universe_filter`.  USD volume is computed
as `close * volume` because the Binance fapi 1m parquet records *contract*
volume (1 contract = 1 unit of base currency on USD-M perps).

## Risk overlays

| Rule                       | Threshold (config)        | Behaviour |
|----------------------------|---------------------------|-----------|
| Daily loss flatten         | `daily_loss_flatten_pct = -0.02` | at the next rebalance, force every position to flat |
| Monthly drawdown pause     | `monthly_loss_pause_pct = -0.05` | if trailing-30d drawdown breaches the threshold, pause the strategy for `monthly_pause_days = 30` rebalances |

Both rules are implemented as defensive overlays on top of the
momentum-driven portfolio -- the strategy does not *avoid* taking
positions, but if either risk gate trips, the next rebalance is forced
flat (and stays flat for the configured pause duration).

## Cost

Fees 1.0 bps/side + slippage 1.0 bps/side per `config.fees_bps_per_side`
and `config.slippage_bps_per_side`.  Cost is charged on the *delta*
between successive target portfolios so a held position does not pay
double cost over a rebalance.

## Data source

Canonical 1m Binance USD-M klines at:

```
/home/smark/services/strategy_display_engine_data/canonical/workdir/strategies/
    vpvr_reversion_1m_20260624/data/fapi_<SYM>__1m.parquet
```

`data_loader.py` resamples 1m -> 1d and caches
`data/fapi_<SYM>__1d.parquet` per symbol.  A SHA256 manifest of the
upstream 1m sources is written to `data/manifest.parquet.sha256` so any
upstream ETL swap is detectable on subsequent runs.

## Code surface

```
config.json              -- declarative config (universe, weights, risk)
data_loader.py           -- 1m -> 1d resampling, SHA256 manifest
universe.py              -- liquidity filter + eligible_symbols_on
strategy.py              -- per-symbol momentum score + ranking + selection
portfolio.py             -- equal-weight allocation + gross cap + risk overlays
backtest.py              -- daily-rebalance backtest engine
run_backtest.py          -- CLI runner, emits results/ artifacts
SPEC.md                  -- this document
tests/                   -- pytest: 26 tests across all 4 modules
results/                 -- summary.json, equity_curve.csv, factor_exposure.json,
                            gross_schedule.csv, turnover_schedule.csv, rebalance_log.csv
```

## Result @ iteration 1 (3-symbol active universe)

See `results/summary.json` for full numbers; excerpts:

```
total_return      : +5.34%  (2024-05-23..2026-06-23, 762 rebalances)
annualized_return : +2.53%
annualized_sharpe :  0.32
annualized_sortino:  0.44
max_drawdown      : -9.06%
avg_gross         :  0.30   (spec target 0.60 -- bound by per-symbol cap on 3-leg universe)
total_turnover    : 25.0
paused_days       :  0
daily_flatten_days:  1
```

Caveat: with only 3 symbols in `active_universe`, the strategy selects
1 long + 2 shorts per rebalance and the per-symbol cap of 10% produces a
natural 30% gross (vs the 60% target).  The strategy is **forward-
compatible** with the 10-symbol spec universe; the moment those 7
additional parquets land in `live_data/`, the rankings will widen and
gross will rise to the target 60% without code change.

## Acceptance bars (next iteration)

- Sharpe @ 10bps cost > 0.4286 (parent vpvr baseline at 10bps)
- Profit factor @ 10bps > 1.0
- Cost-robustness: Sharpe & PF both hold at 18bps/side

(Live bars will land on the day-1 parquet tree as `python -m live_data`
writes more symbols.)

## Test coverage

```
$ PYTHONPATH=. python3 -m pytest tests/ -v
============================== 26 passed in 0.66s ==============================
```

Coverage by file:

- `tests/test_strategy.py`     -- 8 tests (trailing return, score weights,
  signal columns, ranking, selection, panel alignment)
- `tests/test_universe.py`     -- 9 tests (USD volume, trailing bar count,
  liquidity filter (vol / history / pass-through), eligible_symbols_on,
  config loader)
- `tests/test_portfolio.py`    -- 9 tests (3+3 + 4-long allocation, gross
  cap enforcement, no-op when under cap, daily-loss breach, monthly
  pause (peak DD), empty-series handling)
- `tests/test_backtest.py`     -- 2 E2E tests (basic pipeline, daily-flatten
  trigger fires)
