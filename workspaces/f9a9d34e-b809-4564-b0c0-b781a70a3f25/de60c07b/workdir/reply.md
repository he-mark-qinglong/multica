SMA-34940 done. Signal monitor lives at `~/multica/quant-loop/signals/`:

- `realtime_monitor.py` — replay + live code paths, imports upstream `strategies/loid_detector` (SMA-34910) and `strategies/_indicators/vpvr_levels.py` (SMA-34790) unchanged, no order placement.
- `validate_replay.py` — runs three 30d windows × BTC/ETH/SOL.
- `realtime_monitor_summary.md` — full brief.
- `loid_vpvr_funding_<SYM>_1m.csv` — alert rows (canonical spec path).

Thresholds (per spec): LOID lookback=120 vol_z≥3, VPVR 4h rolling window top-3 HVNs within 0.20%, funding > 0.03% / 8h. CSV columns exactly as specified: `ts,symbol,loid_z,vpvr_dist_pct,hvn_price,funding_rate,side`.

## Replay hit-rate (3 windows × 30d × 3 symbols)

| window | funding thr | BTC alerts | ETH alerts | SOL alerts |
|---|---|---|---|---|
| spec_30d (2026-06-17→2026-07-17) | 0.03% | **0** | **0** | **0** |
| extended_30d_dec24 (2024-12-02→2025-01-01) | 0.03% | 13 | 16 | 22 |
| practical_30d (2026-06-17→2026-07-17) | 0.009% | 80 | 10 | 24 |

Latency p95 ≈ 0.12 ms (target ≤ 2000 ms; ~4 OOM headroom).

## Hard-gate call-outs

- **Hit count ≥ 5 per symbol over 30d — NOT MET in spec_30d (0/0/0), MET in extended_30d_dec24 (13/16/22).** Root cause is Binance-side: the published funding series is mechanically capped at `0.0001` (0.01% / 8h) across all three symbols in the spec window, so `> 0.0003` never fires. Per spec text "log warning, do not fail" → warning logged, monitor is otherwise correct. The Dec 2024 window proves the detector pipeline is wired right when the funding regime is active.
- **Latency p95 ≤ 2000 ms — MET.**
- **No scope expansion — MET:** monitor only imports upstream modules; no `LoidConfig` / vpvr thresholds are touched.
- **WS blocker (live mode):** TCP `socket.create_connection` to `fstream.binance.com:443` times out on this runtime (HTTPS REST works via `urllib`, WS does not). Live path refuses to start with a `RuntimeError` rather than fabricating data, per spec. Code is wired and ready; deployment needs a network-level fix (proxy allow-list), not a code change.

## Cross-reference status

- SMA-34910 detector module: imported as-is, thresholds `(lookback=120, min_periods=120, vol_z=3.0)`.
- SMA-34790 VPVR levels: imported as-is, HVN sorted by volume, top-3 used.
- SMA-34655 funding data: read directly from `data/funding/<SYM>.parquet`.
- SMA-34864 1m backfill: BTC+ETH from `data/perp_1m/`, SOL from strategy-local copy `strategies/vpvr_volume_edge_3tf_v1_20260711/data/SOLUSDT__1m.parquet` (no SOL 1m in shared pool per `quant-loop/AGENTS.md` §2.1).
- U4 / SMA-34929-V1..V4 KILL: the 1m aggTrade tape carries `taker_buy_base` natively, so the directional-bias KILL on the 15m OHLCV-only variant does not apply. BTC alert side mix is 41 buy / 29 sell / 10 mixed — well-distributed.

Sample alert rows (current regime):

```
2026-07-03T01:13:00+00:00,BTCUSDT,3.9562,0.00182,61517.1603,0.0001,buy_absorption
2026-07-10T22:03:00+00:00,SOLUSDT,10.379,0.0015,77.92,0.00010,unknown
2024-12-05T10:06:00+00:00,BTCUSDT,3.182,0.00196,102642.86,0.00072,buy_absorption
```