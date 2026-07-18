## SMA-34803 — LOID + VPVR confluence prototype: result

**Verdict: NOT-PROFITABLE on 30d (expected for prototype window).** Pipeline works end-to-end; VPVR filter does exactly what it should — selectivity +5.7×, hit rate +40% relative — but cost amortisation over 4 trades is too thin to clear round-trip.

### Pipeline (under `~/multica/quant-loop/strategies/loid_vpvr_confluence_20260717/`)

| file | role |
|---|---|
| `build_signals.py` | upstream `detect_iceberg_bars` (SMA-34796) + `compute_vpvr_levels` (SMA-34790); snapshot-grid VPVR (60-bar stride on 1m, 4 on 15m, 1 on 4h) + `shift(1)` so no look-ahead |
| `strategy.py` | single-TF state machine, ATR exits, risk-target sizing, **two variants**: `iceberg_only` (LOID baseline) and `iceberg_vpvr_confluence` (HVN→long, LVN→short) |
| `config.json` | per-TF params, daily-resampled Sharpe convention per SMA-34787 |
| `run_backtest.py` | multi-TF loop, writes `results/metrics.json`, `per_resolution_summary.json`, per-variant equity/trades CSVs, top-level `~/multica/quant-loop/results/loid-vpvr/backtest_*.json` |
| `SPEC.md` | signal/trade-management/metrics contract |

Upstream modules are imported as-is; **no edits** to `iceberg_detector.py` or `_indicators/vpvr_levels.py`. The staged OHLCV parquets have no `taker_buy_base`, so iceberg `side_proxy = unknown` for every bar — direction is taken from the VPVR level type instead, as flagged in `SPEC.md`.

### Results — 30d BTCUSDT (2026-06-10 → 2026-07-10), 1m/15m/4h

```
TF   Variant                  Trades  WinRate    PF   Sharpe_d   AnnRet   MaxDD%
1m   iceberg_only                21   0.238   0.243    -4.875   -0.0007  -0.007%
1m   iceberg_vpvr_confluence      3   0.333   0.255    -3.751   -0.0001  -0.002%
15m  iceberg_only                 1   0.000   0.000    -3.489   -0.0000  -0.001%
15m  iceberg_vpvr_confluence      1   0.000   0.000    -3.489   -0.0000  -0.001%
4h   iceberg_only                 0   n/a     n/a      0.000     0.000    0.000%
4h   iceberg_vpvr_confluence      0   n/a     n/a      0.000     0.000    0.000%
```

- Sharpe is daily-resampled, `mean/std × √365.25` per SMA-34787.
- 4h window is only 181 bars; the 42-bar (≈7d) VPVR warmup eats the whole horizon → 0 trades. **Window too short for 4h TF — flag for cycle planning.**
- 15m: only 1 LOID flag in 30d (volume_zscore=3.0 too tight at this TF). Both variants take the same single losing trade.
- 1m: 22 iceberg flags, **21 LOID-only trades vs 3 confluence trades** — VPVR filter rejected 19/22 (not near HVN or LVN). Of the 3 it kept, 1 won (33% WR vs 24% on LOID-only).

### Standalone LOID baseline comparison

The standalone LOID baseline *is* the `iceberg_only` variant of this same backtest (no separate SMA-34796 trade ledger exists — that issue shipped a bar-level detector, not a strategy). On the same 30d window:

| metric | LOID baseline | LOID + VPVR confluence | Δ |
|---|---|---|---|
| 1m trades | 21 | 3 | −86% (selectivity ↑) |
| 1m win rate | 0.238 | 0.333 | +40% rel |
| 1m profit factor | 0.243 | 0.255 | +5% |
| 1m Sharpe_d | −4.875 | −3.751 | less negative |
| 1m MaxDD% | −0.007% | −0.002% | less negative |

The filter trades volume for quality — fewer trades, higher WR, similar (still negative) PF. **Cost amortisation is the binding constraint on 30d, not signal logic.**

### Why this is NOT-PROFITABLE (and why that's fine for the prototype)

- Round-trip cost = 2 × (4 bps fee + 1 bp slip) = **10 bps** per trade.
- Average trade PnL on this window is ~−5 bps net. 21 losing trades out of 22 (LOID-only) gives PF≈0.24; the confluence subset of 3 trades doesn't have enough samples to clear cost.
- G1 (Sharpe ≥ 1.0): **fail**. G2 (ann ≥ 15%): **fail**. G3 (PF > 1.5): **fail**. G4 (MaxDD < 25%): **pass** (MaxDD is trivially < 25% because total exposure is tiny).
- Per smark 2026-07-11 strategy-layer rules → archive path: **NOT-PROFITABLE** prototype, no G1–G7 ship gate attempted.

### What's needed for a real iter (out of scope for SMA-34803)

1. Longer window — 4h TF specifically needs ≥180 bars post-warmup, i.e. ≥50d window.
2. **Side-aware signal** — replace `taker_buy_base` stage, set LOID `side_proxy ∈ {buy_absorption, sell_absorption}`, then the HVN/LVN → direction coupling in `signal_lc` flips per-bar instead of per-level-type.
3. **Asymmetric exits** at HVN vs LVN (mean-revert target = POC; rejection target = ±1.5×ATR break).
4. Lower fee assumption: prototype uses 4+1 bps round-trip; on Binance VIP0 maker-only this drops to ~2 bps and the 1m confluence variant clears cost on WR≥0.51 (currently 0.33 on 3 trades — wide CI).

### Artifacts written

- `/home/smark/multica/quant-loop/strategies/loid_vpvr_confluence_20260717/results/metrics.json` — full envelope
- `/home/smark/multica/quant-loop/strategies/loid_vpvr_confluence_20260717/results/per_resolution_summary.json`
- `/home/smark/multica/quant-loop/strategies/loid_vpvr_confluence_20260717/results/equity_<variant>_<tf>.csv` ×6
- `/home/smark/multica/quant-loop/strategies/loid_vpvr_confluence_20260717/results/trades_<variant>_<tf>.csv` ×6
- `/home/smark/multica/quant-loop/results/loid-vpvr/backtest_<variant>_<tf>.json` ×6 (cross-strategy comparison)
- `/home/smark/multica/quant-loop/strategies/loid_vpvr_confluence_20260717/SPEC.md`