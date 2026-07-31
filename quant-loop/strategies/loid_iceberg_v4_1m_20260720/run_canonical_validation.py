"""Canonical-engine validation for loid_iceberg_v4 (SMA-34992 follow-up).

Root cause this exposes
-----------------------
The legacy ``backtest.py`` builds an *additive step-jump* equity curve:
``equity = notional + cumulative_sum(pnl_usd)``, flat between trades, jumping
only at exit bars. ``compute_metrics`` then takes ``pct_change()`` of that
step function: the many flat bars deflate the Sharpe denominator (std → small)
while ``annualised_return = end/start`` honestly reflects the cumulative loss.
Cached result: ``sharpe_daily +2.468`` next to ``annualized_return -1.0`` and
``max_drawdown -130%`` — physically impossible (max_dd > 100% means NAV went
negative). The Sharpe is a step-jump artefact.

Fix
---
Re-run the SAME cached trade schedule (results/sma-34992/*_trades.json) — no
re-detection of the 5.5GB aggTrades needed — through the authoritative
per-bar compounding engine (``_shared/run_backtest.py``), which produces an
honest Sharpe / annualised / max_dd triple and prints a side-by-side comparison.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _shared.run_backtest import Trade, run_backtest  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TRADES_JSON = REPO / "results/sma-34992/loid_iceberg_v4_btc_90d_trades.json"
OLD_METRICS = REPO / "results/sma-34992/loid_iceberg_v4_btc_90d_metrics.json"
BARS_PARQUET = REPO / "data/perp_1m/BTCUSDT_1m.parquet"
OUT_JSON = REPO / "results/sma-34992/loid_iceberg_v4_canonical_vs_legacy.json"
FREQ_1M = 365 * 24 * 60  # 1-minute bar annualisation factor


def load_trades() -> list[Trade]:
    raw = json.loads(TRADES_JSON.read_text())
    return [
        Trade(
            entry_ts=pd.Timestamp(t["entry_ts"]),
            exit_ts=pd.Timestamp(t["exit_ts"]),
            direction=t["direction"],
            size_fraction=1.0,
        )
        for t in raw
    ]


def load_bars(trades: list[Trade]) -> pd.DataFrame:
    df = pd.read_parquet(BARS_PARQUET)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    lo = min(t.entry_ts for t in trades)
    hi = max(t.exit_ts for t in trades)
    return df.loc[lo:hi, ["close"]].copy()


def main() -> None:
    trades = load_trades()
    bars = load_bars(trades)
    old = json.loads(OLD_METRICS.read_text())
    print(f"trades={len(trades)}  bars={len(bars)}  window={bars.index.min()} -> {bars.index.max()}")
    print()
    header = f"{'metric':22s} {'LEGACY(step-jump)':>20s} | {'CANONICAL(per-bar)':>20s}"
    print(header)
    print("-" * len(header))

    out: dict = {"legacy": old, "canonical": {}, "cost_modes": {}}
    for cost_rt in (8.0, 10.0, 24.0):
        res = run_backtest(
            bars,
            trades,
            initial_capital=100_000.0,
            cost_bps_rt=cost_rt,
            cost_mode="fill",
            freq_per_year=FREQ_1M,
        )
        m = res["metrics"]
        out["canonical"][f"cost_{cost_rt}bp_rt"] = m
        print(f"--- cost_bps_rt = {cost_rt}  (n_applied={res['n_trades']}, n_skipped={res['n_skipped']}) ---")
        rows = (
            ("sharpe", "sharpe_daily", "sharpe"),
            ("annualised_pct", "annualized_return", "annualised"),
            ("max_drawdown_pct", "max_drawdown_pct", "max_dd"),
            ("total_return_pct", None, "total_return"),
        )
        for k_new, k_old, label in rows:
            ov = old.get(k_old) if k_old else None
            cv = m.get(k_new)
            ov_s = f"{ov:>20.4f}" if isinstance(ov, (int, float)) else f"{'—':>20s}"
            cv_s = f"{cv:>20.4f}" if isinstance(cv, (int, float)) else f"{'—':>20s}"
            print(f"{label:22s} {ov_s} | {cv_s}")
        print()

    OUT_JSON.write_text(json.dumps(out, indent=2, default=str))
    print(f"[write] {OUT_JSON}")


if __name__ == "__main__":
    main()
