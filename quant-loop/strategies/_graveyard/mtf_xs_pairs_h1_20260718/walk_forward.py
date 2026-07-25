"""Walk-forward OOS for mtf_xs_pairs_1m_15m_2h_h1_20260718 (H1)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "_indicators"))

from data_loader import load_all, load_funding  # noqa: E402
from _indicators.mtf_xs_runner_20260718 import walk_forward  # noqa: E402

CONFIG_PATH = _HERE / "config.json"
RESULTS_DIR = _HERE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def main():
    cfg = json.loads(CONFIG_PATH.read_text())
    syms = list(cfg["instruments"])
    print("Loading 1m data for", syms)
    data = load_all(syms)
    funding = load_funding(syms) if cfg.get("hypothesis") == "H3" else None
    for s, df in data.items():
        print(" ", s, len(df), "span", df.index[0], "->", df.index[-1])

    print("Running walk-forward …")
    wf = walk_forward(data, cfg, funding=funding)
    out_path = RESULTS_DIR / "walk_forward.json"
    out_path.write_text(json.dumps(wf, indent=2, default=float))
    print("=== walk_forward (" + cfg["strategy"] + ") ===")
    print("n_windows              :", wf["n_windows"])
    print("oos_sharpe_mean        :", f"{wf['oos_sharpe_mean_daily_resampled']:.3f}")
    print("oos_annualized_mean    :", f"{wf['oos_annualized_mean_daily']:.4f}")
    print("oos_max_drawdown_worst :", f"{wf['oos_max_drawdown_worst']:.4f}")
    print("bootstrap_ci_lower     :", f"{wf['bootstrap_ci_lower']:.3f}")
    print("bootstrap_ci_upper     :", f"{wf['bootstrap_ci_upper']:.3f}")
    print("gates.passed           :", wf["gates"]["passed"])
    print("tag                    :", "[" + wf["tag"] + "]")
    print("verdict                :", wf["verdict"])
    print("sharpe_method          :", wf["sharpe_method"])
    print("walk_forward.json      :", str(out_path))


if __name__ == "__main__":
    main()