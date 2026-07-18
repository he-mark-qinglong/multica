# Data gaps — SMA-34901 (vpvr_funding_hvn_lvn_confluence)

## Verification method (per AGENTS.md §1)

```
find ~/multica/quant-loop -path '*data*' -type f \
  -not -path '*/__pycache__/*' \
  -not -path '*/.pytest_cache/*' \
  -not -name '*.pyc' -not -name '*.py' -not -name '*.json' \
  -not -name '*.md' -not -name '*.sha256' -not -name '*.ipynb' \
  > /tmp/quant_loop_data_files.txt
wc -l /tmp/quant_loop_data_files.txt
```

## Files used by SMA-34901

| File | Rows | Range | Verified |
|------|------|-------|----------|
| `live_data/BTCUSDT_15m.parquet` | 158,587 | 2022-01-01 → 2026-07-10 | Yes (read + counted) |
| `live_data/BTCUSDT_4h.parquet` | 9,954 | 2022-01-01 → 2026-07-17 | Yes (read + counted; not a symlink — readlink -f resolves to same path) |
| `live_data/ETHUSDT_15m.parquet` | 158,587 | 2022-01-01 → 2026-07-10 | Yes |
| `live_data/ETHUSDT_4h.parquet` | 9,954 | 2022-01-01 → 2026-07-17 | Yes (not a symlink) |
| `live_data/SOLUSDT_15m.parquet` | 158,587 | 2022-01-01 → 2026-07-10 | Yes |
| `live_data/SOLUSDT_4h.parquet` | 9,954 | 2022-01-01 → 2026-07-17 | Yes (not a symlink) |
| `data/funding/BTCUSDT.parquet` | 5,100 | 2021-11-20 → 2026-07-17 | Yes |
| `data/funding/ETHUSDT.parquet` | 5,100 | 2021-11-20 → 2026-07-17 | Yes |
| `data/funding/SOLUSDT.parquet` | 5,175 | 2021-11-20 → 2026-07-17 | Yes |

## Coverage in the run window (2023-11-01 → 2024-12-31)

For each symbol the run intersects the request window with the staged
data range. All three symbols had full coverage for the entire
14-month window (40,992 × 15m bars each).

## Data gaps discovered

### Funding regime gap (the dominant one)

Per the funding-rate analysis run before backtesting, the BTC/ETH/SOL
perp funding rate crosses the 0.03% (3 bps / 8h) threshold only in
**specific regime windows**:

| Window | BTC events > 0.0003 | ETH | SOL |
|--------|---------------------|-----|-----|
| Nov 2023 | 1 | 3 | 7 |
| Dec 2023 | 16 | 19 | 26 |
| Jan 2024 | 5 | 5 | 6 |
| Feb 2024 | 6 | 8 | 22 |
| Mar 2024 | 48 | 52 | 58 |
| Apr 2024 | 3 | 3 | 5 |
| **May 2024 – Oct 2024** | **0** | **0** | **0** |
| Nov 2024 | 7 | 9 | 9 |
| Dec 2024 | 4 | 5 | 14 |

Total hot-funding events across 3 symbols in the run window:
- BTC: 84
- ETH: 95
- SOL: 141

There is a **6-month funding-cold regime (May 2024 → Oct 2024)**
where the trigger cannot fire. This is not a data gap in the staged
parquets — the data is present — it is a structural regime
limitation of any signal gated on long-side carry > 0.03%.

### Out-of-sample coverage

The run window 2023-11 → 2024-12 covers **all hot-funding regimes
in the staged data**. From 2025-01 onward funding is below
threshold for all three symbols (verified by inspection), so
out-of-sample testing on the staged data is not feasible for this
signal without either (a) extending the funding parquet or (b)
relaxing the funding threshold.

### 15m coverage tail

15m data ends 2026-07-10 while 4h and funding data extend through
2026-07-17. This 7-day mismatch does not affect the run window
(2023-11 → 2024-12) but would matter for any post-2024 OOS run.

### No symlinks

All 4h parquets used are real files (no symlinks); the BTCUSDT_4h
symlink-to-BTCUSD_4h concern noted in AGENTS.md §2.1 is not present
on disk as of 2026-07-18.

## Implications for the verdict

The **49 combined trades** all fall in the 7 hot months (Dec 2023,
Jan–Apr 2024, Nov–Dec 2024); none in the cold regime where the
trigger cannot fire. The strategy is therefore regime-dependent and
the G1 Sharpe ≥ 1.0 / G4 PF > 1.5 results should be interpreted as
**conditional on a hot-funding regime being present**. A multi-year
forward test would need to fold in the cold-regime zero-trade
behavior, which would dilute Sharpe back toward the funding-carry
baseline's negative Sharpe.
