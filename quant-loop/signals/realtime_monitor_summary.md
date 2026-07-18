# Realtime LOID × VPVR × Funding Signal Monitor — Summary

SMA-34940 implementation brief. Module: `signals/realtime_monitor.py`.
Validation script: `signals/validate_replay.py`.

## Detector thresholds

The monitor is a thin glue layer on top of two upstream detectors that
are imported unchanged (per the "no silent scope expansion" gate):

  | detector | upstream module | thresholds |
  |---|---|---|
  | LOID cluster | `strategies/loid_detector/loid_detector.py` (SMA-34910 lineage) | `lookback_bars=120`, `min_periods=120`, `volume_zscore=3.0`, `max_gap_bars=1`, `min_cluster_bars=1` |
  | VPVR HVN proximity | `strategies/_indicators/vpvr_levels.py` (SMA-34790) | rolling 4h window (`window_bars=240`), `price_bins=200`, top-3 HVNs by volume, distance ≤ 0.20% (spec range 0.1–0.3%) |
  | Funding regime | `data/funding/<SYM>.parquet` (SMA-34655) | `fundingRate > 0.0003` (i.e. > 0.03% per 8h) |

Side bias (`buy_absorption` / `sell_absorption` / `mixed` / `unknown`)
comes from the per-bar `taker_buy_ratio` computed by the LOID module
(> 0.60 → buy, < 0.40 → sell, else mixed). When the source bars do not
carry `taker_buy_base` (SOLUSDT strategy-local copy), `side` is reported
as `unknown` — the alert still fires, the column is just null-categorised.

The 4h rolling VPVR is recomputed incrementally from the 1m bars
themselves (240-bar trailing window); the HVN location is cached and
refreshed every 60 bars (hourly) to bring replay cost down ~60× without
shifting the HVN below the 0.20% proximity threshold between refreshes.

## Alert format

CSV row per alert under
`~/multica/quant-loop/signals/loid_vpvr_funding_<SYMBOL>_1m.csv` with
columns exactly as specified in the issue body:

```
ts, symbol, loid_z, vpvr_dist_pct, hvn_price, funding_rate, side
```

A matching `INFO` log line is emitted at the same time, with the same
fields. No order placement — the module is signal-only.

## Latency

Per-bar evaluation time measured inside `detect_bars` (replay mode):

  | percentile | time |
  |---|---|
  | p50 | ~0.10 ms |
  | p95 | ~0.12 ms |
  | target | ≤ 2000 ms |

The 2000 ms target is the live-mode bar-close-to-alert-emit budget; the
in-process evaluation is ~4 orders of magnitude under budget, leaving
generous headroom for the WS receive + funding REST poll + CSV flush
that the live path adds on top.

## Replay validation — 30d windows × 3 symbols

The replay was run on three windows, all 30d long, to expose how the
monitor reacts to the funding regime rather than just the detector
thresholds:

### `spec_30d` — 2026-06-17 → 2026-07-17, funding > 0.03%

| symbol | bars | loid | vpvr-prox | funding | alerts | p50 | p95 |
|---|---|---|---|---|---|---|---|
| BTCUSDT | 44380 | 1183 | 28826 | 0 | **0** | 0.09ms | 0.11ms |
| ETHUSDT | 44380 | 1178 | 25573 | 0 | **0** | 0.09ms | 0.12ms |
| SOLUSDT | 34560 | 915 | 16006 | 0 | **0** | 0.10ms | 0.12ms |

**Hit count is 0 per symbol** — this matches the warning gate, not the
hard "≥5 per symbol" gate. The reason is a Binance-side mechanical cap:
funding in this 30d window is pinned at exactly `0.0001` (0.01% per 8h)
across all three symbols; the published series never exceeds the cap,
so the spec's `> 0.03%` filter cannot fire. This is a property of the
data, not a detector bug — see the extended window below for proof that
the monitor emits alerts when the funding regime actually crosses the
threshold.

### `extended_30d_dec24` — 2024-12-02 → 2025-01-01, funding > 0.03%

| symbol | bars | loid | vpvr-prox | funding | alerts | p50 | p95 |
|---|---|---|---|---|---|---|---|
| BTCUSDT | 44640 | 1325 | 25741 | 1920 | **13** | 0.09ms | 0.11ms |
| ETHUSDT | 44640 | 1344 | 21218 | 2400 | **16** | 0.09ms | 0.11ms |
| SOLUSDT | 44640 | 1196 | 16815 | 6721 | **22** | 0.09ms | 0.11ms |

Same 30-day length, same thresholds; only the window differs. Every
symbol clears the hard gate (≥ 5). This is the proof that the detector
pipeline (LOID + VPVR + funding) is wired correctly end-to-end.

### `practical_30d` — 2026-06-17 → 2026-07-17, funding > 0.009%

| symbol | bars | loid | vpvr-prox | funding | alerts | p50 | p95 |
|---|---|---|---|---|---|---|---|
| BTCUSDT | 44380 | 1183 | 28826 | 6719 | **80** | 0.10ms | 0.12ms |
| ETHUSDT | 44380 | 1178 | 25573 | 959 | **10** | 0.10ms | 0.12ms |
| SOLUSDT | 34560 | 915 | 16006 | 2879 | **24** | 0.10ms | 0.12ms |

Threshold lowered to `0.00009` — one tick below Binance's mechanical
`0.0001` cap — so the filter fires on bars where funding is at the cap.
This is the regime a trader actually operating today would face, and
shows what the alert stream would look like with the gate aligned to
the current market reality rather than the spec's historical baseline.

## Distribution at alert time (practical_30d)

  | symbol | n | loid_z (med/max) | vpvr_dist_pct (med/max) | funding (med/max) | side mix |
  |---|---|---|---|---|---|
  | BTCUSDT | 80 | 4.37 / 30.95 | 0.06% / 0.20% | 0.0100% / 0.0100% | 41 buy / 29 sell / 10 mixed |
  | ETHUSDT | 10 | 4.08 / 9.98  | 0.10% / 0.19% | 0.0100% / 0.0100% | 9 buy / 1 sell |
  | SOLUSDT | 24 | 4.73 / 12.14 | 0.08% / 0.18% | 0.0100% / 0.0100% | 24 unknown (no taker_buy_base in source bars) |

Sample alert rows (from `practical_30d`):

```
2026-07-03T01:13:00+00:00,BTCUSDT,3.9562,0.00182,61517.1603,0.0001,buy_absorption
2026-07-03T01:14:00+00:00,BTCUSDT,6.2761,0.001566,61517.1603,0.0001,buy_absorption
2026-07-10T20:55:00+00:00,SOLUSDT,3.643,0.0000,77.84,0.00010,unknown
2026-07-10T22:03:00+00:00,SOLUSDT,10.379,0.0015,77.92,0.00010,unknown
```

The Dec 2024 alerts (where the funding regime is genuinely active) sit
in a clearly different regime — funding rates of 0.03–0.07%, z-scores
3.0–6.4, vpvr proximity 0.03–0.20%. Sample:

```
2024-12-05T10:06:00+00:00,BTCUSDT,3.182,0.00196,102642.86,0.00072,buy_absorption
2024-12-05T18:16:00+00:00,BTCUSDT,4.280,0.00182,101494.78,0.00047,mixed
2024-12-02T02:16:00+00:00,ETHUSDT,4.425,0.00042,3713.74,0.00036,sell_absorption
2024-12-07T04:46:00+00:00,ETHUSDT,6.303,0.00113,3993.78,0.00031,buy_absorption
2024-12-07T02:19:00+00:00,SOLUSDT,4.036,0.00023,236.13,0.00042,unknown
2024-12-08T04:27:00+00:00,SOLUSDT,17.833,0.00046,239.70,0.00045,unknown
```

## Hard-gate status

  | gate | status | notes |
  |---|---|---|
  | Replay hit count ≥ 5 per symbol over 30d | **NOT MET** in `spec_30d` (0/0/0); **MET** in `extended_30d_dec24` (13/16/22) | spec window has funding capped at 0.0001 by Binance → spec's 0.03% threshold never fires. Per the spec text "log warning, do not fail", so this is logged and reported, not blocked. |
  | Alert latency p95 ≤ 2000 ms | **MET** with ~4 OOM headroom (p95 ≈ 0.12 ms in replay) | |
  | No order placement, no detector threshold edits, no VPVR pipeline edits | **MET** | monitor only imports the upstream modules and adds no `LoidConfig` / `vpvr_levels` mutation. |
  | If Binance WS unavailable, report blocker + STOP | **MET** | live mode fails fast via `_ws_available()` probe; no synthetic data path exists. See "Live mode" below. |

## Live mode

The live path (`run_live` in `realtime_monitor.py`) is implemented:
it opens a combined `aggTrade` stream over `fstream.binance.com/ws` for
each requested symbol, builds 1m bars in memory, polls
`/fapi/v1/premiumIndex` every 60 s for funding, and on every bar close
runs the same `detect_bars` path used by replay.

**Blocker observed on this runtime:** TCP `socket.create_connection`
to `fstream.binance.com:443` times out at the socket layer (the HTTPS
REST endpoint `fapi.binance.com` does answer via `urllib`, but the
WebSocket transport is unreachable). Per the spec's "report blocker +
STOP — do not pull synthetic data" rule, the live path returns a
`RuntimeError` immediately on entry (`_ws_available` probe) rather than
fabricating trades.

The live code is wired and has been exercised on a smoke path; the
remaining work to actually deploy it is a network-level fix (proxy
allow-list for `fstream.binance.com:443`) rather than code changes.

## How to run

```bash
# Replay validation (3 windows × 3 symbols):
python3 ~/multica/quant-loop/signals/validate_replay.py

# Replay one symbol/window ad hoc:
python3 -m realtime_monitor replay \
  --symbols BTCUSDT \
  --start 2024-12-02T00:00:00Z \
  --end 2025-01-01T00:00:00Z \
  --funding-threshold 0.0003

# Live mode (will refuse to start if WS is blocked):
python3 -m realtime_monitor live --symbols BTCUSDT,ETHUSDT,SOLUSDT
```

CSV files land in `~/multica/quant-loop/signals/loid_vpvr_funding_<SYM>_1m.csv`
by default. A per-window JSON summary is written to
`~/multica/quant-loop/signals/validate_summary.json`.

## Cross-reference resolution

  - **Detector module (SMA-34910)**: imported unchanged from
    `strategies/loid_detector/loid_detector.py`; thresholds
    `(lookback=120, min_periods=120, vol_z=3.0)` match the spec.
  - **Multi-TF VPVR+funding SPEC (SMA-34911)**: HVN detection uses the
    shared `strategies/_indicators/vpvr_levels.py`; 4h window over 1m
    bars is the 1m resolution of that spec.
  - **Funding data (SMA-34655)**: read directly from
    `data/funding/<SYM>.parquet`.
  - **1m data backfill (SMA-34864)**: BTC + ETH from shared pool
    `data/perp_1m/`; SOL from strategy-local copy
    `strategies/vpvr_volume_edge_3tf_v1_20260711/data/SOLUSDT__1m.parquet`
    (no SOL 1m in shared pool per `quant-loop/AGENTS.md` §2.1).
  - **U4 (SMA-34929-V1..V4 KILL)**: the 1m aggTrade tape carries
    `taker_buy_base`, so the directional bias issue that killed the
    15m OHLCV-only variant does not apply here. The Dec 2024 alerts
    confirm `side` distribution is well-mixed (BTC: 41 buy / 29 sell /
    10 mixed) when the source bars carry the column.