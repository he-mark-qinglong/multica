# BTC / ETH / SOL Return Correlation — 1m / 15m / 4h

## Scope
- Universe: **BTCUSDT, ETHUSDT, SOLUSDT**
- Timeframes: **1m, 15m, 4h**
- Window: **last 30 calendar days** (calendar-aligned for cross-timeframe comparability): **2026-06-17 → 2026-07-10** (data end)
- Return type: **log returns** of close (first difference of `log(close)`)
- Correlation: **Pearson** on aligned return series

## Data source
`~/multica/quant-loop/strategies/vpvr_volume_edge_3tf_v1_20260711/data/{SYM}__{TF}.parquet`
- Indexed by `openTime` (UTC). 5 columns: `open, high, low, close, volume`.
- Coverage ends **2026-07-10 23:59 UTC** for all three tickers / timeframes (uniform).

> Caveat surfaced earlier: `~/multica/quant-loop/live_data/{ETH,SOL}USDT_4h.parquet`
> are symlinks pointing to `BTCUSD_4h.parquet` (wrong symbol). This analysis uses
> the strategy-data directory instead, which holds genuine per-symbol 4h bars.

## Coverage (bars loaded per ticker, then aligned on common timestamps)

| TF | BTC | ETH | SOL | Aligned rows |
|----|----:|----:|----:|-------------:|
| 1m  | 34,559 | 34,559 | 34,559 | 34,559 |
| 15m | 2,303  | 2,303  | 2,303  | 2,303  |
| 4h  | 143    | 143    | 143    | 143    |

All three tickers had identical per-TF bar counts → no rows dropped from the inner join.

## Results

| Pair | 1m | 15m | 4h |
|------|----:|----:|----:|
| BTC–ETH | **0.880** | **0.905** | **0.901** |
| BTC–SOL | 0.813 | 0.833 | 0.816 |
| ETH–SOL | 0.837 | 0.848 | 0.812 |

- **Most correlated pair at every timeframe: BTC–ETH** (0.88 → 0.90 → 0.90)
- **Least correlated pair**:
  - 1m, 15m: **BTC–SOL** (0.813, 0.833)
  - 4h: **ETH–SOL** (0.812)

## Interpretation

1. **Cross-asset correlation is uniformly high** in this 30-day window (all r ≥ 0.81). The three majors are tightly coupled; this is consistent with a beta-on regime where majors drive each other rather than diverge on idiosyncratic news.
2. **BTC–ETH is the tightest pair at every timeframe**, peaking on 15m (0.905) and barely lower on 4h (0.901). On the 1m timeframe it drops to 0.880 — microstructural noise / exchange-specific timing widens the dispersion slightly.
3. **BTC–SOL vs ETH–SOL is regime-dependent**: at 1m/15m SOL tracks ETH more closely than BTC (0.837/0.848 vs 0.813/0.833), but at 4h SOL decouples from ETH (0.812) and sits closer to BTC's level (0.816). A reasonable read: on lower timeframes SOL behaves like an ETH-beta instrument, while on the 4h its higher standalone vol pulls it back toward a flatter cluster mean.
4. **Timeframe aggregation effect is mild but non-monotonic.** Moving from 1m → 15m lifts all correlations (noise averages out). Moving 15m → 4h mostly holds or slightly weakens them — except ETH–SOL, which clearly weakens (0.848 → 0.812), suggesting a 4h-scale ETH/SOL divergence event worth a follow-up regime check.

## Files

- `heatmap_1m.png`, `heatmap_15m.png`, `heatmap_4h.png` — one heatmap per timeframe.
- `heatmap_combined.png` — 3-panel side-by-side comparison.
- `corr_1m.csv`, `corr_15m.csv`, `corr_4h.csv` — raw Pearson matrices.
- `summary.json` — machine-readable summary (window, bar counts, matrices, strongest/weakest pair per TF).
- `compute_corr.py` — analysis script (one-shot, deterministic on this data snapshot).

## Reproducibility

```bash
python3 ~/multica_workspaces/.../workdir/compute_corr.py
```

Window is fixed in the script (`WINDOW_DAYS = 30`, anchored to `TODAY = 2026-07-17 UTC`). If you re-run after newer data lands, edit `TODAY` / `WINDOW_START` at the top to slide the window.
