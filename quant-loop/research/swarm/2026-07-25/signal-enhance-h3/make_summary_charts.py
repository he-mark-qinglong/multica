"""Quick diagnostic charts for SUMMARY.md (uses existing files only)."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def chart_exit_reasons():
    p = "/Users/mark/multica/quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/results/trades_winner_atr_mult_1_00.csv"
    df = pd.read_csv(p)
    df["gross_pct"] = df["pnl_pct"] + 0.0008
    grp = df.groupby("exit_reason").agg(
        count=("pnl_pct", "size"),
        mean_net_bps=("pnl_pct", lambda x: float(np.mean(x)) * 1e4),
        mean_gross_bps=("gross_pct", lambda x: float(np.mean(x)) * 1e4),
    ).reset_index()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    ax1.bar(grp["exit_reason"], grp["count"], color="steelblue")
    ax1.set_title("H3 baseline: exit reason counts")
    ax1.set_ylabel("trades")
    ax2.bar(grp["exit_reason"], grp["mean_net_bps"], color="coral")
    ax2.axhline(0, color="black", lw=0.8)
    ax2.set_title("mean net PnL by exit reason (bps)")
    ax2.set_ylabel("bps")
    fig.tight_layout()
    fig.savefig(HERE / "chart_diag_exit_reasons.png", dpi=150)
    plt.close(fig)


def chart_quick_verify():
    rows = json.loads((HERE / "quick_verify_2024.json").read_text())
    df = pd.DataFrame(rows)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    axes[0].barh(df["variant"], df["sharpe_daily_resampled"], color="steelblue")
    axes[0].axvline(1.0, color="red", lw=0.8, linestyle="--")
    axes[0].set_title("2024 daily-resampled Sharpe")
    axes[1].barh(df["variant"], df["mean_net_bps"], color="coral")
    axes[1].axvline(0, color="black", lw=0.8)
    axes[1].set_title("2024 mean net PnL (bps/trade)")
    fig.tight_layout()
    fig.savefig(HERE / "chart_quick_verify_2024.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    chart_exit_reasons()
    chart_quick_verify()
    print("Charts saved to", HERE)
