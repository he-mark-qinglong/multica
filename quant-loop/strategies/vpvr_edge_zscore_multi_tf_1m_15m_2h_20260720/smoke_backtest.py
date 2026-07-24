"""Smoke backtest for vpvr_edge_zscore_multi_tf (SMA-34991).

Runs the full pipeline on a small BTC-only 2024-Q1 slice to verify
the end-to-end path (data loader -> signal builders -> state-machine
simulator -> metrics dict -> JSON) before committing to the full
CPCV. Output is dumped to ``results/smoke_2024_Q1_BTCUSDT.json``.

NOTE: This is NOT an OOS test. It's a pipeline-correctness check.
Any failure here indicates a code/runtime bug, NOT a strategy verdict.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
QUANT_LOOP = REPO_ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from data_loader import load_tf  # noqa: E402
from strategy import VARIANT_KEY, run_backtest  # noqa: E402

CONFIG_PATH = REPO_ROOT / "config.json"
RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

SMOKE_SYMBOL = "BTCUSDT"
SMOKE_START = "2024-01-01"
SMOKE_END = "2024-03-31"


def _sanitize(o):
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    if isinstance(o, float):
        return None if (np.isnan(o) or np.isinf(o)) else o
    if isinstance(o, (np.floating,)):
        f = float(o)
        return None if (np.isnan(f) or np.isinf(f)) else f
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return o


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    cfg["instruments"] = [SMOKE_SYMBOL]
    print(f"[{datetime.now(timezone.utc).isoformat()}] SMOKE {VARIANT_KEY} on {SMOKE_SYMBOL} [{SMOKE_START}..{SMOKE_END}]", flush=True)

    print(f"  loading {SMOKE_SYMBOL} 1m/15m/2h...", flush=True)
    df_1m = load_tf(SMOKE_SYMBOL, "1m")
    df_15m = load_tf(SMOKE_SYMBOL, "15m")
    df_2h = load_tf(SMOKE_SYMBOL, "2h")
    print(f"  full sizes: 1m={len(df_1m)} 15m={len(df_15m)} 2h={len(df_2h)}", flush=True)

    # Localize to UTC if naive
    def _utc(df):
        if df.index.tz is None:
            df = df.copy()
            df.index = df.index.tz_localize("UTC")
        return df
    df_1m = _utc(df_1m)
    df_15m = _utc(df_15m)
    df_2h = _utc(df_2h)

    s = pd.Timestamp(SMOKE_START, tz="UTC")
    e = pd.Timestamp(SMOKE_END, tz="UTC")
    df_1m = df_1m.loc[s:e]
    df_15m = df_15m.loc[s:e]
    df_2h = df_2h.loc[s:e]
    print(f"  smoke slice: 1m={len(df_1m)} 15m={len(df_15m)} 2h={len(df_2h)}", flush=True)

    if df_1m.empty or df_15m.empty or df_2h.empty:
        print("  ERROR: empty slice", flush=True)
        return 2

    result = run_backtest(df_1m, df_15m, df_2h, cfg)

    # Quick metrics
    eq = np.asarray(result["equity"], dtype=np.float64)
    trades = result["trades"]
    n_trades = len(trades)
    if n_trades:
        net_pnls = np.array([t["net_pnl_pct"] for t in trades])
        win_rate = float((net_pnls > 0).mean())
        avg_bars = float(np.mean([t["bars_held"] for t in trades]))
    else:
        win_rate = 0.0
        avg_bars = 0.0

    if len(eq) >= 2:
        eq_idx = pd.date_range(start=result["span_start"], periods=len(eq), freq="1min", tz="UTC")
        daily = pd.Series(eq, index=eq_idx).resample("1D").last().dropna()
        rets = daily.pct_change().dropna()
        sharpe = float(rets.mean() / rets.std() * np.sqrt(365.25)) if (len(rets) > 1 and rets.std() > 0) else 0.0
        total_return = float(eq[-1] / eq[0] - 1)
        running_max = np.maximum.accumulate(eq)
        mdd = float(np.min((eq - running_max) / running_max)) * 100.0
    else:
        sharpe = 0.0
        total_return = 0.0
        mdd = 0.0

    summary = {
        "variant": VARIANT_KEY,
        "symbol": SMOKE_SYMBOL,
        "window": [SMOKE_START, SMOKE_END],
        "n_1m_bars": int(len(df_1m)),
        "n_15m_bars": int(len(df_15m)),
        "n_2h_bars": int(len(df_2h)),
        "n_trades": n_trades,
        "win_rate": round(win_rate, 4),
        "avg_bars_held": round(avg_bars, 2),
        "total_return": round(total_return, 6),
        "max_drawdown_pct": round(mdd, 4),
        "sharpe_daily": round(sharpe, 4),
        "diagnostics": _sanitize(result["diagnostics"]),
    }
    out = RESULTS_DIR / "smoke_2024_Q1_BTCUSDT.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    print(f"[{datetime.now(timezone.utc).isoformat()}] SMOKE done -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())