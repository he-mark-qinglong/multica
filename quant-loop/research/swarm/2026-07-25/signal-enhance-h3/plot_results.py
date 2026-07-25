"""Generate diagnostic charts for signal-enhance-h3 research."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def load_metrics(variant: str) -> dict:
    return json.loads((HERE / f"metrics_{variant}.json").read_text())


def load_trades(variant: str) -> pd.DataFrame:
    p = HERE / f"trades_{variant}.csv"
    if not p.is_file():
        return pd.DataFrame()
    df = pd.read_csv(p, parse_dates=["entry_ts", "exit_ts"])
    return df


def load_equity(variant: str) -> pd.Series:
    p = HERE / f"equity_{variant}_1d.csv"
    if not p.is_file():
        return pd.Series(dtype=float)
    df = pd.read_csv(p, parse_dates=["timestamp"])
    return pd.Series(df["equity"].to_numpy(), index=df["timestamp"])


def plot_equity(baseline: str, best: str):
    eq_base = load_equity(baseline)
    eq_best = load_equity(best)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(eq_base.index, eq_base / eq_base.iloc[0] - 1.0, label=baseline, alpha=0.8)
    ax.plot(eq_best.index, eq_best / eq_best.iloc[0] - 1.0, label=best, alpha=0.8)
    ax.set_title("H3 signal enhancement: cumulative daily equity")
    ax.set_ylabel("total return")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(HERE / "chart_equity.png", dpi=150)
    plt.close(fig)


def plot_pnl_distribution(baseline: str, best: str):
    df_base = load_trades(baseline)
    df_best = load_trades(best)
    fig, ax = plt.subplots(figsize=(10, 5))
    bins = np.linspace(-0.01, 0.01, 101)
    ax.hist(df_base["net_pct"] * 1e4, bins=bins, alpha=0.5, label=baseline, density=True)
    ax.hist(df_best["net_pct"] * 1e4, bins=bins, alpha=0.5, label=best, density=True)
    ax.axvline(0, color="black", lw=0.8)
    ax.set_title("Per-trade net PnL distribution (bps)")
    ax.set_xlabel("net PnL (bps)")
    ax.set_ylabel("density")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(HERE / "chart_pnl_distribution.png", dpi=150)
    plt.close(fig)


def plot_z_decile(baseline: str):
    df = load_trades(baseline)
    if df.empty:
        return
    df["z_abs"] = df["z_at_entry"].abs()
    df["decile"] = pd.qcut(df["z_abs"], 10, labels=False, duplicates="drop")
    grp = df.groupby("decile").agg(
        mean_net_bps=("net_pct", lambda x: float(np.mean(x)) * 1e4),
        win_rate=("net_pct", lambda x: float(np.mean(x > 0))),
        count=("net_pct", "size"),
    ).reset_index()
    fig, ax1 = plt.subplots(figsize=(10, 5))
    ax1.bar(grp["decile"], grp["mean_net_bps"], color="steelblue", alpha=0.7)
    ax1.axhline(0, color="black", lw=0.8)
    ax1.set_xlabel("|z_entry| decile (0=lowest)")
    ax1.set_ylabel("mean net PnL (bps)", color="steelblue")
    ax1.tick_params(axis="y", labelcolor="steelblue")
    ax2 = ax1.twinx()
    ax2.plot(grp["decile"], grp["win_rate"] * 100, color="orange", marker="o")
    ax2.set_ylabel("win rate (%)", color="orange")
    ax2.tick_params(axis="y", labelcolor="orange")
    ax1.set_title("Baseline H3: trade PnL and win rate by |z_entry| decile")
    fig.tight_layout()
    fig.savefig(HERE / "chart_z_decile.png", dpi=150)
    plt.close(fig)


def plot_exit_reasons(baseline: str, best: str):
    df_base = load_trades(baseline)
    df_best = load_trades(best)
    fig, ax = plt.subplots(figsize=(10, 5))
    base_counts = df_base["exit_reason"].value_counts(normalize=True).sort_index()
    best_counts = df_best["exit_reason"].value_counts(normalize=True).sort_index()
    reasons = sorted(set(base_counts.index) | set(best_counts.index))
    x = np.arange(len(reasons))
    width = 0.35
    ax.bar(x - width / 2, [base_counts.get(r, 0) * 100 for r in reasons], width, label=baseline, alpha=0.8)
    ax.bar(x + width / 2, [best_counts.get(r, 0) * 100 for r in reasons], width, label=best, alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(reasons, rotation=15, ha="right")
    ax.set_ylabel("share of trades (%)")
    ax.set_title("Exit reason composition")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(HERE / "chart_exit_reasons.png", dpi=150)
    plt.close(fig)


def plot_variant_bars():
    df = pd.read_csv(HERE / "variant_metrics.csv")
    if df.empty:
        return
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    ax = axes[0, 0]
    ax.barh(df["variant"], df["sharpe_daily_resampled"], color="steelblue")
    ax.set_title("daily-resampled Sharpe")
    ax.axvline(1.0, color="red", lw=0.8, linestyle="--")
    ax = axes[0, 1]
    ax.barh(df["variant"], df["profit_factor"], color="green")
    ax.set_title("profit factor")
    ax.axvline(1.5, color="red", lw=0.8, linestyle="--")
    ax = axes[1, 0]
    ax.barh(df["variant"], df["mean_net_bps"], color="orange")
    ax.set_title("mean net PnL (bps/trade)")
    ax.axvline(0, color="black", lw=0.8)
    ax = axes[1, 1]
    ax.barh(df["variant"], df["win_rate"] * 100, color="purple")
    ax.set_title("win rate (%)")
    fig.tight_layout()
    fig.savefig(HERE / "chart_variant_summary.png", dpi=150)
    plt.close(fig)


def main():
    summary = json.loads((HERE / "metrics_baseline.json").read_text())
    best = (HERE / "best_variant.txt").read_text().strip()
    plot_equity("baseline", best)
    plot_pnl_distribution("baseline", best)
    plot_z_decile("baseline")
    plot_exit_reasons("baseline", best)
    plot_variant_bars()
    print("Charts written to", HERE)


if __name__ == "__main__":
    main()
