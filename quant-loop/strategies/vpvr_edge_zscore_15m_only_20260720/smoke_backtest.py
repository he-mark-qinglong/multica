"""Smoke backtest for the 15m-only strategy. Loads BTCUSDT 15m, runs the
single-TF strategy on a small slice, dumps to results/smoke.json.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
QUANT_LOOP = REPO_ROOT.parents[1]
MULTI_TF_DIR = QUANT_LOOP / "strategies" / "vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720"
# Insert 15m_only dir LAST so its `strategy` module shadows the multi-TF one.
sys.path.insert(0, str(MULTI_TF_DIR))
sys.path.insert(0, str(REPO_ROOT))

from data_loader import load_tf  # type: ignore  # noqa: E402  # multi-TF
from strategy import VARIANT_KEY, run_backtest  # type: ignore  # noqa: E402  # 15m_only

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
    print(f"[{datetime.now(timezone.utc).isoformat()}] SMOKE 15m-only on {SMOKE_SYMBOL} [{SMOKE_START}..{SMOKE_END}]", flush=True)

    df_15m = load_tf(SMOKE_SYMBOL, "15m")
    print(f"  full 15m: {len(df_15m)} rows", flush=True)
    if df_15m.index.tz is None:
        df_15m = df_15m.copy()
        df_15m.index = df_15m.index.tz_localize("UTC")

    s = pd.Timestamp(SMOKE_START, tz="UTC")
    e = pd.Timestamp(SMOKE_END, tz="UTC")
    df = df_15m.loc[s:e].copy()
    print(f"  smoke slice: {len(df)} 15m bars", flush=True)

    if df.empty:
        print("  ERROR: empty slice", flush=True)
        return 2

    result = run_backtest(df, cfg)
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
        eq_idx = pd.date_range(start=result["span_start"], periods=len(eq), freq="15min", tz="UTC")
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
        "n_15m_bars": int(len(df)),
        "n_trades": n_trades,
        "win_rate": round(win_rate, 4),
        "avg_bars_held": round(avg_bars, 2),
        "total_return": round(total_return, 6),
        "max_drawdown_pct": round(mdd, 4),
        "sharpe_daily": round(sharpe, 4),
        "diagnostics": _sanitize(result["diagnostics"]),
    }
    out = RESULTS_DIR / "smoke.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    print(f"[{datetime.now(timezone.utc).isoformat()}] SMOKE done -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
