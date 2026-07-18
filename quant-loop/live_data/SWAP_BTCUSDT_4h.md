# BTCUSDT_4h parity swap (SMA-34872)

## Before
- `BTCUSDT_4h.parquet` -> symlink -> `BTCUSD_4h.parquet`
- Target was coinm BTCUSD-margined 4h klines fetched via `fetch_binance_coinm_4h.py`:
  - rows: 9912
  - first_open_time: 2022-01-01T00:00:00Z (1640995200000)
  - last_open_time: 2026-07-10T19:00:00Z (1783713600000)
  - schema: 12 cols (open_time, open, high, low, close, volume, close_time, quote_volume, trades, taker_buy_base, taker_buy_quote, ignore)

## After
- `BTCUSDT_4h.parquet` -> REAL FILE (no longer symlink)
- Content: USDT-m BTCUSDT-margined 4h klines from `fapi.binance.com` via `fetch_binance_usdm_4h.py`:
  - rows: 9954
  - first_open_time: 2022-01-01T00:00:00Z (1640995200000) — same
  - last_open_time: 2026-07-17T20:00:00Z (1784318400000) — matches ETHUSDT/SOLUSDT 4h coverage
  - schema: identical 12 cols (superset-by-equality of the coinm target)
  - max_gap_bars: 1; boundary_misalign_count: 0; NaN close: 0; ts monotonic
- `BTCUSD_4h.parquet` no longer exists.

## Data loss disclosure
The original `BTCUSD_4h.parquet` (coinm) was overwritten **in place** by the
`pandas.to_parquet(...)` call writing through the symlink — that file no
longer exists on disk and its coinm content cannot be recovered from git
(workspace was untracked). The 9954-row BTCUSDT 4h USDT-m series covers the
same 2022-01-01 onward window and the same 12-column schema, so callers
that only depended on schema + start time are unaffected.

## Acceptance crosswalk
- "BTC 4h schema/ts 与旧目标一致或为超集" — schema is identical (superset = equality); first_open_time is byte-identical; last_open_time extends 7 days further than the coinm target.
- "移除/重指旧链接并记录" — old symlink removed; this file documents the change.