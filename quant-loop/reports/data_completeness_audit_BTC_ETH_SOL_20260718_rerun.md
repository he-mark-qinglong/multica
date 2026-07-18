# Data Completeness Audit (Re-run) — BTC / ETH / SOL

**Issue:** SMA-34869 (re-audit after SMA-34864..34867 backfills)
**Prior baseline:** SMA-34855 (`reports/data_completeness_audit_BTC_ETH_SOL_20260718.md`)
**Date:** 2026-07-18 (UTC) — re-verified post-backfill
**Scope:** BTCUSDT, ETHUSDT, SOLUSDT × 1m / 15m / 4h (and adjacent 30m / 1h), full available history
**Method:** Direct file inspection via pandas + `os.path.islink`/`os.readlink`; expected-step gap detection.

---

## 1. Headline

The post-backfill state is **substantially better** than the SMA-34855 baseline but the 3×3 × {1m,15m,4h} matrix is **NOT yet fully complete**. Three cells are still missing or compromised:

| TF | BTCUSDT | ETHUSDT | SOLUSDT |
|----|---------|---------|---------|
| **1m**    | ✅ real, 3.6 M bars, 1-min gap (≤2 min) | ✅ real, 3.5 M bars, **zero gaps** | ❌ **missing** (no shared-pool file) |
| **15m**   | ✅ real, 158 587 bars, 5-bar gap (~75 min) | ✅ real, 158 587 bars, 5-bar gap (~75 min) | ❌ **missing** |
| **4h**    | ⚠️ still symlink → `BTCUSD_4h.parquet` (BTCUSD ≈ BTCUSDT within basis; label-only) | ✅ real, 9 954 bars, **zero gaps** | ✅ real, 9 954 bars, **zero gaps** |
| **30m** (adjacent) | ✅ real, 79 296 bars, **zero gaps** | ✅ real, 79 296 bars, **zero gaps** | ✅ real, 79 296 bars, **zero gaps** |
| **1h** (adjacent) | ✅ real, 39 647 bars, 1-bar gap (~2 h) | ✅ real, 39 648 bars, **zero gaps** | ✅ real, 39 646 bars, 1-bar gap (~2 h) |

**Delta vs SMA-34855 baseline:**

| TF | BTC | ETH | SOL |
|----|-----|-----|-----|
| 1m | was ❌ → ✅ | was ❌ → ✅ | was ❌ → ❌ still missing in shared pool (exists per-strategy, see §4) |
| 15m | was ✅ | was ❌ → ✅ | was ❌ → ❌ still missing |
| 4h | was ✅ (symlink) | was ⚠️ symlink-bug → ✅ real | was ⚠️ symlink-bug → ✅ real |

**Verdict:** Not yet fully done. Three follow-ups remain:
1. `data/perp_1m/SOLUSDT_1m.parquet` is missing (1m backfill is partial).
2. `live_data/SOLUSDT_15m.parquet` is missing (15m backfill covered only ETH).
3. `live_data/BTCUSDT_4h.parquet` is still a symlink to `BTCUSD_4h.parquet`. Price-wise this is benign (BTCUSD coin-m ≈ BTCUSDT USDT-m within basis) but the labeling is wrong; if any code branch reads the symbol from the path or filename it will see `BTCUSDT` while reading coin-m data. Recommend replacing with a real USDT-M fetch for cleanliness.

---

## 2. Per-file inventory (verified)

### 2.1 `data/perp_1m/` (1-minute, USDT-margined) — *new since baseline*

| Symbol | Rows | First ts | Last ts | Step | Max gap | Severity | File |
|--------|------|----------|---------|------|---------|----------|------|
| BTCUSDT | 3 605 862 | 2019-09-08 17:57 UTC | 2026-07-17 19:39 UTC | 60 000 ms | **1 bar** | **ok** (≤2 min) | `data/perp_1m/BTCUSDT_1m.parquet` (213 MB) + `.csv` (391 MB) |
| ETHUSDT | 3 491 275 | 2019-11-27 07:45 UTC | 2026-07-17 19:39 UTC | 60 000 ms | **0 bars** | **ok** | `data/perp_1m/ETHUSDT_1m.parquet` (200 MB) + `.csv` (380 MB) |
| SOLUSDT | — | — | — | — | — | **missing** | no file |

CSV provenance footers verified (`# source: binance fapi klines, fetched 2026-07-17, total bars: N, symbol: SYMBOL`). Both files have the canonical 12-column schema (open_time, open, high, low, close, volume, close_time, quote_volume, trades, taker_buy_base, taker_buy_quote, ignore).

### 2.2 `live_data/` 15-minute bars — *ETH added since baseline*

| Symbol | Rows | First ts | Last ts | Step | Max gap | Severity |
|--------|------|----------|---------|------|---------|----------|
| BTCUSDT | 158 587 | 2022-01-01 00:00 UTC | 2026-07-10 23:45 UTC | 900 000 ms | **5 bars** | **minor** (same root gap as baseline — 2023-03-24 ~12:30 UTC) |
| ETHUSDT | 158 587 | 2022-01-01 00:00 UTC | 2026-07-10 23:45 UTC | 900 000 ms | **5 bars** | **minor** (same root gap, ETH mirrors BTC) |
| SOLUSDT | — | — | — | — | — | **missing** |

Both BTC and ETH 15m files share the 5-bar (~75-min) gap at 2023-03-24 ~12:30 UTC. This is the same exchange-side outage documented in SMA-34855 — both files were fetched with the same window, so they inherit the same hole. Severity remains `minor` (<1 % of bars).

### 2.3 `live_data/` 4-hour bars — *symlink hazard resolved for ETH/SOL*

| Symbol | Symlink? | Rows | First ts | Last ts | Severity |
|--------|----------|------|----------|---------|----------|
| BTCUSDT | ⚠️ **yes** → `BTCUSD_4h.parquet` | 9 912 | 2022-01-01 00:00 UTC | 2026-07-10 20:00 UTC | ok-data, **label-only** issue |
| ETHUSDT | ✅ no | 9 954 | 2022-01-01 00:00 UTC | 2026-07-17 20:00 UTC | ok |
| SOLUSDT | ✅ no | 9 954 | 2022-01-01 00:00 UTC | 2026-07-17 20:00 UTC | ok |

Price-range sanity check (proves ETH/SOL files contain real ETH/SOL data, not BTC):
- `ETHUSDT_4h.parquet` close range: **914.1 – 4832.1** (BTC is 15 695 – 125 419) ✅
- `SOLUSDT_4h.parquet` close range: **8.8 – 286.1** (BTC is 15 695 – 125 419) ✅
- `BTCUSD_4h.parquet` close range: **15 695.5 – 125 419.0** ✅

The P0 symlink hazard from SMA-34855 is fixed: ETH and SOL 4h files are now real USDT-M bars. SMA-34866 is verifiable complete.

### 2.4 `data/perp_30m/` (30-minute) — unchanged, all-green

| Symbol | Rows | First ts | Last ts | Max gap | Severity |
|--------|------|----------|---------|---------|----------|
| BTCUSDT | 79 296 | 2022-01-01 00:00 UTC | 2026-07-10 23:30 UTC | 0 | ok |
| ETHUSDT | 79 296 | 2022-01-01 00:00 UTC | 2026-07-10 23:30 UTC | 0 | ok |
| SOLUSDT | 79 296 | 2022-01-01 00:00 UTC | 2026-07-10 23:30 UTC | 0 | ok |

### 2.5 `live_data/` 1-hour bars — *SOL added since baseline*

| Symbol | Rows | First ts | Last ts | Max gap | Severity |
|--------|------|----------|---------|---------|----------|
| BTCUSDT | 39 647 | 2022-01-01 00:00 UTC | 2026-07-10 23:00 UTC | 1 bar (~2 h) | minor (same 2023-03-24 root) |
| ETHUSDT | 39 648 | 2022-01-01 00:00 UTC | 2026-07-10 23:00 UTC | 0 | ok |
| SOLUSDT | 39 646 | 2022-01-01 00:00 UTC | 2026-07-10 22:00 UTC | 1 bar (~2 h) | minor (same 2023-03-24 root) |

### 2.6 `data/funding/` — unchanged, all-green

All three (BTC/ETH/SOL) `*USDT.parquet` files exist, 95–103 KB. The funding schema does **not** use `open_time` (uses `fundingTime` / `fundingRate`), so the pandas reader error in the script above is expected — files are non-empty and parseable via the funding schema.

---

## 3. Symlink audit

```text
$ ls -la ~/multica/quant-loop/live_data/*.parquet
BTCUSD_4h.parquet                real   (coin-m, 813 KB)
BTCUSDT_4h.parquet -> BTCUSD_4h.parquet   ⚠️ label-only
BTCUSDT_15m.parquet              real   (12.1 MB)
BTCUSDT_1h.parquet               real   (3.2 MB)
ETHUSDT_15m.parquet              real   (10.9 MB)   [NEW]
ETHUSDT_1h.parquet               real   (3.0 MB)
ETHUSDT_4h.parquet               real   (837 KB)    [NEW — symlink removed]
SOLUSDT_1h.parquet               real   (2.5 MB)    [NEW]
SOLUSDT_4h.parquet               real   (795 KB)    [NEW — symlink removed]
```

Symlink counts:
- Before backfill (SMA-34855): 3 symlinks (BTCUSDT_4h, ETHUSDT_4h, SOLUSDT_4h) — all pointing at BTCUSD_4h
- After backfill: **1 symlink** (BTCUSDT_4h) — pointing at BTCUSD_4h, label-only

The `.new_4h/` staging directory is still present (`live_data/.new_4h/{ETHUSDT,SOLUSDT}_4h.parquet`) — these are the freshly-fetched USDT-M files. The promotion step (`mv .new_4h/* live_data/`) already executed, and `.new_4h/fetch_report_usdm_4h.json` records 9 954 rows × 2 symbols, max_gap_bars=1, no boundary misalignment.

---

## 4. Cross-cutting note: 1m data exists in `strategies/*/data/` (per SMA-34870)

Per the SMA-34870 audit-by-replication meta issue, audits must enumerate **all** data locations. Doing so here:

```text
$ find ~/multica/quant-loop -path '*data*' -name '*1m*.parquet' | wc -l
16
```

Per-symbol per-strategy 1m coverage (DatetimeIndex named `openTime`, UTC, 1-minute cadence):

| Symbol | Largest snapshot | Window |
|--------|------------------|--------|
| BTCUSDT | `vpvr_volume_edge_3tf_v1_20260711/data/BTCUSDT__1m.parquet` — 2 378 800 rows, 2022-01-01 → 2026-07-10 | full 4.5 y |
| ETHUSDT | `vpvr_volume_edge_3tf_v1_20260711/data/ETHUSDT__1m.parquet` — 2 378 800 rows, 2022-01-01 → 2026-07-10 | full 4.5 y |
| SOLUSDT | `vpvr_volume_edge_3tf_v1_20260711/data/SOLUSDT__1m.parquet` — 2 378 800 rows, 2022-01-01 → 2026-07-10 | full 4.5 y |

So 1m **does exist** for SOL — just not in the shared pool. The shared-pool `data/perp_1m/` BTCUSDT/ETHUSDT files have a longer window (2019 → 2026) than the per-strategy copies (2022 → 2026), so promoting the new shared-pool files to all three symbols is still worth doing for window consistency.

---

## 5. Timezone / DST

All timestamps are UTC milliseconds (`open_time` int64 column on the new files; `openTime` DatetimeIndex UTC on per-strategy files). No local-time strings, no DST exposure. Bar boundaries aligned (15m/30m/1h/4h all start at 00:00 UTC on 2022-01-01). No anomalies found.

---

## 6. Verified vs inferred

| Claim | Status |
|-------|--------|
| File exists / doesn't exist | **verified** (`os.path.exists`, `islink`) |
| Row counts, time ranges | **verified** (`pandas.read_parquet`) |
| Gap detection | **verified** (`open_time.diff()` vs modal step) |
| Symlink resolution | **verified** (`os.readlink`) |
| Price-range sanity (ETH/SOL 4h ≠ BTC) | **verified** (min/max of `close` column) |
| Provenance footers in CSVs | **verified** (`head -1 …` contains `# source: binance fapi klines, …`) |
| SOL 1m exists per-strategy | **verified** (`find … -name '*1m*'`) |
| "15m BTC+ETH gap = same 2023-03-24 event" | **inferred** from identical gap counts and shared fetch window; not asserted |

---

## 7. Recommendations

| Priority | Item | Owner hint |
|----------|------|------------|
| **P1** | `data/perp_1m/SOLUSDT_1m.parquet` missing — finish the 1m backfill (reuse `data/perp_1m/BTCUSDT_1m.csv` provenance, schema already proven on BTC+ETH) | SMA-34864 (in_progress; needs smark-decision per comment `b5c83eb3` to obsolete, since the audit basis was wrong) |
| **P1** | `live_data/SOLUSDT_15m.parquet` missing — fetch using `live_data/fetch_binance_usdt_15m.py` (already covers ETH) | SMA-34865 (only ETH done so far) |
| **P3** | `live_data/BTCUSDT_4h.parquet` is symlink to `BTCUSD_4h.parquet` — replace with real USDT-M fetch for label correctness | SMA-34866 follow-up |
| **P3** | Optional: backfill the BTC 15m/1h 5-bar / 2-h gap at 2023-03-24 ~12:30 UTC | SMA-34855 §7 P3 |
| **P4** | Promote per-strategy 1m snapshots to shared `data/perp_1m/` once SOL is backfilled | SMA-34855 §7 P4 |

---

## 8. Methodology log (reproducible)

```bash
# Symlink + inventory
ls -la ~/multica/quant-loop/data/perp_1m/ ~/multica/quant-loop/live_data/ ~/multica/quant-loop/data/perp_30m/ ~/multica/quant-loop/data/funding/
readlink ~/multica/quant-loop/live_data/{BTCUSDT,ETHUSDT,SOLUSDT}_4h.parquet

# Per-file pandas gap analysis (open_time.diff() vs modal step)
#   python3 audit_data_matrix.py   # scripted summary; same logic as in the audit run

# Per-strategy 1m sweep (SMA-34870 mandate)
find ~/multica/quant-loop -path '*data*' -name '*1m*.parquet' -o -name '*1m*.csv' | sort

# Provenance footer check
head -1 ~/multica/quant-loop/data/perp_1m/{BTCUSDT,ETHUSDT}_1m.csv
```

Report file: `~/multica/quant-loop/reports/data_completeness_audit_BTC_ETH_SOL_20260718_rerun.md`
Prior baseline: `~/multica/quant-loop/reports/data_completeness_audit_BTC_ETH_SOL_20260718.md`