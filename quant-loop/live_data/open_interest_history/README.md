# Open Interest History Backfill

Open-interest (OI) historical data pipeline for perpetual swap symbols
across Binance USDT-margined futures and OKX USDT-SWAP swaps. Complements
the existing K-line pipeline (`live_data/fetch_binance_*`) by adding
periodic open-interest data needed for OI-aware factor research and
strategy work.

**Migration provenance**: this package was migrated verbatim from the
archived `trading` repo at
`da0020de89575c0694b5763c0628a486612d6256` (the trading repo was
archived in `a80a927` / `4c052b2`; new work lives here).

## Why

Open interest is a leading indicator for futures positioning: a sudden
spike in OI often precedes volatile breakouts, while a divergence
between OI and price (OI rising, price flat) hints at crowded positions
about to unwind. Holding years of 5-minute OI locally lets us:

- Run offline factor / regression research without touching the
  exchange.
- Backtest OI-aware strategies (e.g. OI-momentum, OI-divergence) over
  realistic market conditions.
- Sanity-check live OI charts against an authoritative history.

## Source

`OIBackfiller` queries `ccxt.fetchOpenInterestHistory`, which on Binance
USDT-M wraps `fapi/v1/openInterestHist`. The endpoint:

- Accepts `period ∈ {5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d}`.
- Returns at most **500 rows per call**.
- For the 5m period the lookback per call is ~30 days (≈ 8,640 rows in
  a calendar month, well under the 500-row cap, so a single request
  covers up to ~30 days).

The same endpoint is available on OKX USDT-SWAP via ccxt (different
internal route). The backfiller normalises the unified symbol
(`BTC/USDT:USDT`) for both exchanges.

## Package layout

```
live_data/open_interest_history/
├── __init__.py           # public API re-exports
├── _helpers.py           # constants + parse_timestamp + chunk math + windowed_iter
├── manager.py            # OpenInterestDataManager (parquet I/O)
├── backfiller.py         # OIBackfiller (ccxt + retry + idempotent merge)
├── __main__.py           # CLI entry point (python -m live_data.open_interest_history)
├── tests/
│   └── test_open_interest_history.py   # 55 offline unit tests
└── README.md             # this file
```

`__init__.py` re-exports the public surface so callers can do
`from live_data.open_interest_history import OIBackfiller,
OpenInterestDataManager` without caring which submodule they live in.

## How it pages

`windowed_iter(start, end, period)` yields `Window(since_ms, until_ms)`
chunks no larger than `chunk_seconds_for_period(period)`. For 5m with the
default safety ratio (5/6 ≈ 0.83) each chunk is ~34.6 hours; for 1d the
chunk is ~303 days. A 100-day 5m backfill therefore issues ~70 requests
instead of one giant query.

Worst-case volume per request:

| Period | Rows in chunk | Span          |
|--------|---------------|---------------|
| 5m     | 415           | ~34.6 h       |
| 15m    | 415           | ~4.3 d        |
| 30m    | 415           | ~8.6 d        |
| 1h     | 415           | ~17.3 d       |
| 2h     | 415           | ~34.6 d       |
| 4h     | 415           | ~69.2 d       |
| 6h     | 415           | ~103.8 d      |
| 12h    | 415           | ~207.6 d      |
| 1d     | 415           | ~415 d        |

(Always under the exchange's 500-row ceiling.)

## Parquet layout

```
{base_path}/{exchange_id}_{safe_symbol}/{period}.parquet
```

- One parquet file per `(exchange, symbol, period)` triple — matches
  the canonical K-line "one file per key" contract under
  `multica/quant-loop/live_data/`.
- `_safe_symbol` strips `/`, `-`, and `:` so the directory name is
  filesystem-safe (e.g. `binance_BTC_USDT_USDT/`).
- Default `base_path` is `./data/open_interest` (overridable via
  `--data-dir` or the `OpenInterestDataManager` constructor).

## Idempotency

`OIBackfiller.backfill` reads any existing parquet file for
`(exchange, symbol, period)`, fetches new data in windowed chunks, then
merges them and writes back. The merge dedupes on the DatetimeIndex
with `keep="last"` so re-runs are safe: the latest fetched value always
wins on overlap.

## Usage

### Python

```python
from live_data.open_interest_history import (
    OIBackfiller, OpenInterestDataManager,
)

manager = OpenInterestDataManager("./data/open_interest")
loader = OIBackfiller("binance")

df = loader.backfill(
    "BTC",
    period="5m",
    start_ms=1_700_000_000_000,
    end_ms=1_700_086_400_000,
    manager=manager,
)
# df is a DatetimeIndex'd DataFrame with columns:
#   sumOpenInterest, sumOpenInterestValue, countOpenInterest
```

### CLI

```bash
# Show resolved config (exchanges, periods, default data dir) and exit.
python -m live_data.open_interest_history --show-config

# Plan only — no network
python -m live_data.open_interest_history \
    --symbol BTC --period 5m --dry-run

# Real backfill: BTC + ETH for all supported periods
python -m live_data.open_interest_history \
    --symbol BTC --symbol ETH

# Pin a window
python -m live_data.open_interest_history \
    --symbol BTC --period 5m \
    --start-ms 1700000000000 --end-ms 1702000000000
```

## Tests

`tests/test_open_interest_history.py` contains 55 pure / mock-patched
tests covering:

- Constants and chunk math (no network)
- `parse_timestamp` (ms / sec / ISO / datetime / None / error paths)
- `windowed_iter` ordering, boundaries, rejection of bad inputs
- `OpenInterestDataManager` parquet round-trip + edge cases
- `format_symbol` per exchange + bad inputs
- `_to_dataframe` / `_merge` normalisation
- `backfill` with the network monkey-patched: paging fires for long
  ranges, outliers outside `[since, until)` are dropped, invalid inputs
  raise before any request.

Run:

```bash
cd multica
pytest live_data/open_interest_history/tests/test_open_interest_history.py -v
```

All 55 pass without Redis or network access.

## Operational notes

- The ccxt exchange object is constructed with `enableRateLimit=True`
  and `adjustForTimeDifference=True`, so we don't blow past Binance's
  rate limits even on the 5m cadence.
- Each request retries up to 5 times with exponential backoff
  (0.5s, 1s, 2s, 4s, 8s capped at 10s + jitter). Worst-case wall time
  for a fully-failed window is ~20 seconds before we move on.
- `format_symbol` returns `None` on unparseable input rather than
  guessing, so callers fail loudly instead of silently hitting a wrong
  market.

## Future work

- Multi-symbol batch driver in `_shared/data_fetch.py` style (YAML
  config).
- Auto-resume: when the local parquet is fresh enough, skip directly to
  forward-fill instead of a full backward walk.
- Cross-exchange reconciliation: download the same window from Binance
  and OKX, log any divergence > 0.5 %.
- Live streaming OI via WebSocket for sub-minute freshness.