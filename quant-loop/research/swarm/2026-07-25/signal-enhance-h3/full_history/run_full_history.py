"""W4-T07 — full-history run + 4/24/60 bps fee shock.

Loads BTC+SOL 1m + funding via the T02/T04 contracts, runs the SE-H3 backtest
on the full aligned history (2 448 219 bars), dumps portfolio metrics, daily
equity, flat trade list, and a three-tier fee shock replay.

Six phases; phase 2 pickles the heavy backtest result so a re-run after a
crash picks up at phase 3 without redoing 10-25 min of compute.

Read-only anchors: strategies/, _shared/, H3-variants-h1h2h4/, H3-baseline-repro/, data/.
Outputs land only in FH/results/ (and FH/results/checkpoints/).
"""
from __future__ import annotations

import json
import math
import pickle
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from se_h3_common import (  # noqa: E402
    load_aligned_data,
    load_se_h3_config,
    portfolio_metrics,
    fee_shock_metrics,
)
from se_h3_loop import run_se_h3  # noqa: E402

FH = Path(__file__).resolve().parent
RES = FH / "results"
CKPT = RES / "checkpoints"
CKPT.mkdir(parents=True, exist_ok=True)

FEE_LEVELS = (
    ("inhouse_4bps_rt", 4.0),
    ("freqtrade_24bps_rt", 24.0),
    ("backtrader_60bps_rt", 60.0),
)  # matches fixed runner L417-422


def main() -> int:
    # phase 1: data + config (~1 min)
    t0 = time.time()
    d1m, funding, common_idx = load_aligned_data()
    cfg = load_se_h3_config()
    assert len(common_idx) == 2448219, len(common_idx)
    print(f"[T07] phase 1 done in {time.time()-t0:.0f}s "
          f"(n_bars={len(common_idx)})", flush=True)

    # phase 2: full-history backtest (10-25 min, pickle checkpointed)
    ckpt = CKPT / "phase2_result.pkl"
    if ckpt.exists():
        res = pickle.loads(ckpt.read_bytes())
        print(f"[T07] phase 2 loaded from checkpoint "
              f"({ckpt.stat().st_size} bytes)", flush=True)
    else:
        t0 = time.time()
        res = run_se_h3(d1m, cfg, funding)
        ckpt.write_bytes(pickle.dumps(res))
        print(f"[T07] phase 2 done in {time.time()-t0:.0f}s "
              f"(n_bars={res['portfolio']['n_bars']})", flush=True)

    # phase 3: portfolio_metrics → equity daily csv
    t0 = time.time()
    full_metrics, equity = portfolio_metrics(res, common_idx, cfg)
    daily_eq = equity.resample("1D").last().dropna()
    daily_eq.to_frame("equity").to_csv(RES / "se_h3_equity_daily.csv")
    print(f"[T07] phase 3 done in {time.time()-t0:.0f}s "
          f"(n_days={len(daily_eq)})", flush=True)

    # phase 4: trades flattened to csv
    t0 = time.time()
    trades = [t for pp in res["per_pair"] for t in pp["trades"]]
    pd.DataFrame(trades).to_csv(RES / "se_h3_trades.csv", index=False)
    print(f"[T07] phase 4 done in {time.time()-t0:.0f}s "
          f"(n_trades={len(trades)})", flush=True)

    # phase 5: fee shock (three tiers, per_trade_fraction default 0.005)
    t0 = time.time()
    fee_sens = {label: fee_shock_metrics(equity, trades, rt)
                for label, rt in FEE_LEVELS}
    (RES / "se_h3_fee_shock.json").write_text(
        json.dumps(fee_sens, indent=2, default=float))
    print(f"[T07] phase 5 done in {time.time()-t0:.0f}s", flush=True)

    # phase 6: summary json
    t0 = time.time()
    summary = {
        "source_script": "run_full_history.py",
        "n_bars": int(len(common_idx)),
        "data_span": [str(common_idx[0]), str(common_idx[-1])],
        "config_snapshot": cfg,
        "full_history": full_metrics,
    }
    (RES / "se_h3_full_history_metrics.json").write_text(
        json.dumps(summary, indent=2, default=float))
    print(f"[T07] phase 6 done in {time.time()-t0:.0f}s", flush=True)
    print("[T07] ALL DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())