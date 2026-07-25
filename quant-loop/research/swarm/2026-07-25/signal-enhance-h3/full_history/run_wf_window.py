"""W4-T08 — single-window walk-forward runner for the signal-enhance-h3 sweep.

Re-runnable per `--window K` (K in 0..6). Each invocation writes exactly:
  FH/results/se_h3_wf_window_{K}.json
  FH/results/se_h3_wf_trades_{K}.csv
T09-T14 reuse this script to fill windows 1-6.

Window algorithm + signal-only-on-test-slice + funding clipping + cost override
mirror H3-variants-h1h2h4/run_btcsol_variants_fixed.py L242-275 (read-only).
Boundary table cross-checked against H3-baseline-repro/metrics.json
walk_forward_oos.per_window (same fixed runner output). A boundary mismatch
exits non-zero — do NOT paper over EXPECTED to make it pass.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from se_h3_common import load_aligned_data, load_se_h3_config, portfolio_metrics  # noqa: E402
from se_h3_loop import run_se_h3  # noqa: E402

import pandas as pd  # noqa: E402

FH = Path(__file__).resolve().parent
RES = FH / "results"
RES.mkdir(parents=True, exist_ok=True)

TRAIN, TEST, STEP = 525_600, 262_800, 262_800  # fixed runner L44-48 (locked)

# Boundary table: verbatim from H3-baseline-repro/metrics.json
# walk_forward_oos.per_window — that file has NO `test_bars` key, only ISO
# strings. ISO (end) is the LAST bar in the test slice, hence `te_e - 1` here.
EXPECTED = [
    ("2022-11-20 16:01:00", "2023-05-22 04:00:00"),
    ("2023-05-22 04:01:00", "2023-11-20 16:00:00"),
    ("2023-11-20 16:01:00", "2024-05-21 04:00:00"),
    ("2024-05-21 04:01:00", "2024-11-19 16:00:00"),
    ("2024-11-19 16:01:00", "2025-05-21 04:00:00"),
    ("2025-05-21 04:01:00", "2025-11-19 16:00:00"),
    ("2025-11-19 16:01:00", "2026-05-21 04:00:00"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, required=True)
    args = ap.parse_args()
    k = args.window

    t_load0 = time.time()
    d1m, funding, common_idx = load_aligned_data()
    cfg = load_se_h3_config()
    n_bars = len(common_idx)
    if n_bars != 2_448_219:
        raise SystemExit(f"[T08] n_bars drift: got {n_bars} want 2448219")
    t_load = time.time() - t_load0

    windows = []
    te_s = TRAIN
    while te_s + TEST <= n_bars:  # fixed runner L254-257
        windows.append((te_s, te_s + TEST))
        te_s += STEP
    if len(windows) != 7:
        raise SystemExit(f"[T08] window count drift: got {len(windows)} want 7")
    if not (0 <= k < 7):
        raise SystemExit(f"[T08] window {k} out of range 0..6")

    te_s, te_e = windows[k]
    start_iso = str(common_idx[te_s])
    end_iso = str(common_idx[te_e - 1])
    exp = EXPECTED[k]
    if (start_iso, end_iso) != exp:  # data drift guard — do NOT modify EXPECTED
        print(f"[T08] BOUNDARY MISMATCH window {k}: got {(start_iso, end_iso)} want {exp}")
        raise SystemExit(1)

    d_win = {sym: df.iloc[te_s:te_e].copy() for sym, df in d1m.items()}  # mirror L263
    start_ts = common_idx[te_s]
    end_ts = common_idx[te_e - 1]
    funding_win = {
        sym: f[(f.index >= start_ts) & (f.index <= end_ts)].copy()
        for sym, f in funding.items()
    }  # mirror L266-269
    cfg_cost = json.loads(json.dumps(cfg))
    cfg_cost["fees_bps_per_side"] = 1.0
    cfg_cost["slippage_bps_per_side"] = 1.0  # mirror L270-272

    t0 = time.time()
    res = run_se_h3(d_win, cfg_cost, funding_win)  # signals built inside slice
    metrics_win, _eq = portfolio_metrics(res, common_idx[te_s:te_e], cfg)  # mirror L274-275
    n_trades = sum(len(pp["trades"]) for pp in res["per_pair"])
    elapsed_backtest = time.time() - t0

    trades = [t for pp in res["per_pair"] for t in pp["trades"]]
    pd.DataFrame(trades).to_csv(RES / f"se_h3_wf_trades_{k}.csv", index=False)

    out = {
        "window_id": k,
        "test_bars": [int(te_s), int(te_e)],
        "test_start_iso": start_iso,
        "test_end_iso": end_iso,
        "n_trades": n_trades,
        "sharpe_daily_resampled": metrics_win["sharpe_daily_resampled"],
        "annualized_return_daily_resampled": metrics_win["annualized_return_daily_resampled"],
        "max_drawdown_pct": metrics_win["max_drawdown_pct_daily_method"],
        "profit_factor": metrics_win["profit_factor_daily_method"],
        "elapsed_sec_load": round(t_load, 1),
        "elapsed_sec_backtest": round(elapsed_backtest, 1),
        "elapsed_sec_total": round(t_load + elapsed_backtest, 1),
        "source_script": "run_wf_window.py",
    }
    (RES / f"se_h3_wf_window_{k}.json").write_text(
        json.dumps(out, indent=2, default=float)
    )
    print(
        f"[T08] window {k} OK trades={n_trades} "
        f"sharpe={out['sharpe_daily_resampled']:.3f} "
        f"elapsed={out['elapsed_sec_total']}s",
        flush=True,
    )


if __name__ == "__main__":
    main()