## SMA-34866 — FIX done: ETH/SOL 4h symlinks replaced with real USDT-M data

### Before (`ls -la`)
```
-rw-rw-r-- 1 smark smark 813331 Jul 12 16:51 BTCUSD_4h.parquet
lrwxrwxrwx 1 smark smark     17 Jul 17 06:08 BTCUSDT_4h.parquet -> BTCUSD_4h.parquet
lrwxrwxrwx 1 smark smark     17 Jul 17 06:08 ETHUSDT_4h.parquet -> BTCUSD_4h.parquet
lrwxrwxrwx 1 smark smark     17 Jul 17 06:08 SOLUSDT_4h.parquet -> BTCUSD_4h.parquet
```
Confirmed — `ETHUSDT_4h.parquet` and `SOLUSDT_4h.parquet` were symlinks pointing at the BTC coin-m file.

### After (`ls -la`)
```
-rw-rw-r-- 1 smark smark 813331 Jul 12 16:51 BTCUSD_4h.parquet
lrwxrwxrwx 1 smark smark     17 Jul 17 06:08 BTCUSDT_4h.parquet -> BTCUSD_4h.parquet
-rw-rw-r-- 1 smark smark 837447 Jul 18 04:08 ETHUSDT_4h.parquet
-rw-rw-r-- 1 smark smark 795036 Jul 18 04:18 SOLUSDT_4h.parquet   (ts 04:18 from final mv; file was created at 04:08)
```
No more symlinks for ETH/SOL 4h. (Heads-up: `BTCUSDT_4h.parquet` is **also** a symlink to `BTCUSD_4h.parquet`. It was outside the scope of this ticket (titled "ETH/SOL"), so I left it untouched — flag if you want it fixed in a follow-up; the same fetcher covers it via `--symbols BTCUSDT,ETHUSDT,SOLUSDT`.)

### Fetcher
Wrote `~/multica/quant-loop/live_data/fetch_binance_usdm_4h.py`, modeled on the existing `fetch_binance_usdm_1m.py` and `fetch_binance_coinm_4h.py`. Endpoint: `fapi.binance.com/fapi/v1/klines` (USDT-M perpetual, the right venue for ETHUSDT/SOLUSDT 4h). 12-column schema (open_time, open, high, low, close, volume, close_time, quote_volume, trades, taker_buy_base, taker_buy_quote, ignore) — byte-compatible with the BTCUSD_4h coin-m file already used by the catalog.

Command run (from a staging dir first, validated, then atomically swapped in place):
```
python3 fetch_binance_usdm_4h.py --symbols ETHUSDT,SOLUSDT \
    --out-dir /home/smark/multica/quant-loop/live_data/.new_4h
mv .new_4h/ETHUSDT_4h.parquet .new_4h/SOLUSDT_4h.parquet live_data/
```

Stderr summary:
```
[fetch] ETHUSDT 4h 2022-01-01 -> 2026-07-17
[fetch] ETHUSDT: 9954 bars, max_gap=1 bars, misalign=0, elapsed=5.6s
[fetch] SOLUSDT 4h 2022-01-01 -> 2026-07-17
[fetch] SOLUSDT: 9954 bars, max_gap=1 bars, misalign=0, elapsed=3.5s
[fetch] overall_ok=True
```

### Sample rows

`ETHUSDT_4h.parquet` (9954 rows, 12 cols):
```
       open_time     open     high      low    close      volume
0  1640995200000  3676.01  3747.78  3676.01  3722.07  116271.787   # 2022-01-01 00:00 UTC
1  1641009600000  3722.07  3764.03  3701.16  3714.77  141654.520
2  1641024000000  3714.77  3733.33  3671.86  3692.08  150783.679
...
9951 1784289600000 1838.33  1839.45  1802.46  1830.20 1205607.457   # 2026-07-17 12:00 UTC
9952 1784304000000 1830.20  1855.90  1825.70  1842.81  772232.272
9953 1784318400000 1842.81  1842.81  1836.11  1837.14   17512.651
```

`SOLUSDT_4h.parquet` (9954 rows, 12 cols):
```
       open_time    open    high     low   close    volume
0  1640995200000  170.01  174.36  169.90  172.93  369077.0     # 2022-01-01 00:00 UTC
1  1641009600000  172.94  174.31  171.34  173.16  318583.0
2  1641024000000  173.17  173.37  170.52  171.58  273069.0
...
9951 1784289600000  74.81  74.83  73.32  74.74  4966929.93    # 2026-07-17 12:00 UTC
9952 1784304000000  74.74  75.58  74.55  75.16  2501493.96
9953 1784318400000  75.16  75.17  74.90  74.94   54829.49
```

### Updated counts
| File | Size | Rows | Range (UTC) | Max gap (4h bars) | Boundary misalign |
|---|---|---|---|---|---|
| `BTCUSD_4h.parquet`   | 813 KB | 9912 | 2022-01-01 → 2026-07-10 | 1 | 0 |
| `ETHUSDT_4h.parquet`  | 837 KB | **9954** | **2022-01-01 → 2026-07-17** | **1** | **0** |
| `SOLUSDT_4h.parquet`  | 795 KB | **9954** | **2022-01-01 → 2026-07-17** | **1** | **0** |

Both new files are non-empty, monotonic, span the full window matching the BTCUSD_4h reference (with 1 extra bar from running through 2026-07-17 instead of -10), and price ranges (ETH close $914–$4832; SOL close $8.78–$286) are plausible for the 2022-01-01 → 2026-07-17 window. Fetch report: `live_data/fetch_report_usdm_4h.json`.