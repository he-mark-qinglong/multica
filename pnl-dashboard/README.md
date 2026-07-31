# P&L Dashboard (`pnl-dashboard`)

Portfolio-wide profit-and-loss aggregator for the multica workspace.
One-shot, stdlib-only, no daemon or network side-effects.

> **Status:** shipped for [`SMA-35851` (Monitor #80 — P&L dashboard)](https://github.com/he-mark-qinglong/multica/issues/…/35851) under [`SMA-35770` (MAP-P9 Monitoring & Observability)](https://github.com/he-mark-qinglong/multica/issues/…/35770).

## What it does

`pnl_dashboard.py` walks every strategy under `<root>/strategies/`, reads
each strategy's `results/trades_*.csv` and `results/summary.json`, and
emit a single JSON snapshot that answers:

- **How much P&L has the workspace produced in total?** (sum / mean / median / std / Sharpe-style metrics)
- **Which strategies are profitable?** (per-strategy ranking, joined to the `summary.json` Sharpe / max-DD / tag)
- **Which symbols dominate?** (per-symbol totals, win rate, profit factor)
- **Where is the equity curve?** (downsampled to ≤ `equity_max_points` points)
- **Worst drawdown?** (peak-to-trough in absolute pnl % units)
- **Top winners / losers** (verbatim trade rows)
- **Daily tally** (per-day trade count and net pnl)

The aggregator mirrors the column-fallback convention used by
`strategy-display-engine/backend/data.py` (`pnl_pct` → `pnl` → `ret_pct`,
`exit_ts` → `ts` → `time`), so the two views never disagree on which
value to read.

## Files

| path | purpose |
|---|---|
| `pnl_dashboard.py` | library — `TradeRecord`, `Snapshot`, `build_snapshot`, `write_snapshot`, `parse_trades_csv`, `parse_summary_json`, `iter_strategy_dirs` |
| `run.py` | CLI entry point — produces a snapshot and writes it to `state/` |
| `tests/test_pnl_dashboard.py` | 42 stdlib `unittest` cases |
| `state/last-snapshot.json` | freshest snapshot (overwritten each run) |
| `state/snapshot-<UTC>.json` | timestamped run record (history) |

## CLI

```bash
python3 /Users/mark/multica/pnl-dashboard/run.py \
    --root /Users/mark/multica/quant-loop/strategies \
    --state-dir /Users/mark/multica/pnl-dashboard/state \
    --equity-max-points 500 \
    --top-n 10
```

Defaults are baked in and match the runbook below.

## Runbook (evidence collection)

```bash
# 1. Confirm the unit tests pass.
python3 -m unittest tests.test_pnl_dashboard -v

# 2. Run against the real strategies tree.
python3 /Users/mark/multica/pnl-dashboard/run.py

# 3. Inspect the freshest snapshot.
jq '.totals, .drawdown, (.by_strategy | .[0:3])' \
    /Users/mark/multica/pnl-dashboard/state/last-snapshot.json
```

## Output schema (abridged)

```jsonc
{
  "generated_at": "2026-07-26T07:35:00+00:00",
  "version": "0.1.0",
  "root_scanned": "/Users/mark/multica/quant-loop/strategies",
  "totals": {
    "n_strategies_scanned": 160,
    "n_strategies_with_trades": 59,
    "n_strategies_profitable": 12,
    "n_trades": 248132,
    "sum_pnl_pct": 4.21,
    "win_rate": 0.41,
    "profit_factor": 1.07,
    "unique_days": 1672,
    "span_start": "2022-01-01",
    "span_end": "2026-07-25"
  },
  "drawdown": {
    "max_drawdown_pct": 0.83,
    "peak_ts": "...",
    "trough_ts": "..."
  },
  "by_strategy": [ {"strategy": "...", "n_trades": ..., "sum_pnl_pct": ...,
                    "summary_sharpe": ..., "summary_tag": "PROFITABLE"}, ... ],
  "by_symbol":   [ {"symbol": "BTCUSDT", "n_trades": ..., "sum_pnl_pct": ...}, ... ],
  "by_day":      [ {"date": "2024-09-06", "n_trades": ..., "sum_pnl_pct": ...}, ... ],
  "top_winners": [ { ...trade row... }, ... ],
  "top_losers":  [ { ...trade row... }, ... ],
  "equity_curve": {
    "trade_count": 248132,
    "downsampled": true,
    "points": [ {"ts": "...", "cum_pnl_pct": ..., "drawdown_pct": ...}, ... ]
  },
  "missing_sources": [ {"strategy": "...", "file": "...", "reason": "..."}, ... ],
  "elapsed_ms": 8800.0
}
```

## Scope and boundaries

- **Read-only.** No daemon, no network, no auth. The aggregator writes
  only to `state/` (or wherever `--output` points).
- **Stdlib-only.** No duckdb, no pandas, no fastapi. The dashboard runs
  on a fresh Python install with zero installs.
- **Resilient.** A malformed CSV row, missing column, or unreadable
  file is recorded in `missing_sources` and counted under
  `skipped_rows` / `skipped_files`. The dashboard always ships a
  partial-but-useful snapshot.
- **Equity-curve bounded.** Default cap is 500 points (`--equity-max-points`).
  A 4-year backtest with 250k trades is downsampled to a chartable size
  while preserving the last point verbatim.
- **Mirrors the display-engine.** Column-fallback order matches
  `strategy-display-engine/backend/data.py` so the two views read
  identical trades; if the display-engine says `pnl_pct`, the dashboard
  says `pnl_pct`.

## Out of scope (intentionally)

- **No new HTTP endpoint in display-engine.** The dashboard is a
  standalone aggregator; wiring it into the FastAPI app would be a
  separate task with stronger buy-in (and a UI to back it).
- **No live trading source.** The aggregator reads backtest results
  from disk, not broker feeds. Live trading is owned by the
  `execution/` subsystem; this dashboard reflects *what the backtests
  say*, not what the live account is doing.
- **No alerting.** The dashboard reports; it does not page. Alerting
  belongs to the existing alert-routing siblings (Monitor #15, #16, #17).
- **No portfolio rebalancing.** The dashboard sums `pnl_pct` per
  strategy as if every strategy had equal weight. Real portfolio
  weights are owned by the quant campaign layer.

## Caveat to the parent project (MAP-P9)

This is one of seven "P&L dashboard" siblings (#0, #10, #20, #30, #40,
#50, #60, #70, #80, #90, plus a handful of `#8X` variants) all sharing
the exact same description template. As the Monitor #99 sibling
commented, the MAP-P9 100-monitor batch looks like the "1000
housekeeping issue" antipattern called out in the [ROOT GOAL] doc.
This dashboard ships a real aggregator because the workspace does have
real `trades_*.csv` files to read; the other 9 P&L siblings can reuse
this artifact if a real P&L view is needed elsewhere, otherwise treat
them as no-ops.

## Verification

```text
$ python3 -m unittest tests.test_pnl_dashboard -v
…
Ran 42 tests in 0.115s
OK
```

## Reference

- Parent issue: [`SMA-35770` (MAP-P9 Monitoring & Observability)](…/issues/35770)
- This issue: [`SMA-35851` Monitor #80 — P&L dashboard](…/issues/35851)
- Sibling precedents: [`SMA-35870` log aggregator (#99)](…/issues/35870), [`SMA-35864` position monitor (#91)](…/issues/35864), [`SMA-35863` data freshness alert (#92)](…/issues/35863)
- Display-engine column conventions: [`strategy-display-engine/backend/data.py`](https://github.com/he-mark-qinglong/strategy-display-engine)
