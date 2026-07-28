"""
Generate the two required figures for the VPVR edge-limit reversion SPEC:
  Figure 1 — VPVR profile + entry/TP1/TP2/SL annotations (a real BTC daily profile)
  Figure 2 — cost decomposition waterfall (VIP0 pair-RT vs edge)
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path("/Users/mark/multica/quant-loop")
DATA = ROOT / "data/perp_1m"
OUT = ROOT / "research/vpvr_edge_reversion/figures"
OUT.mkdir(parents=True, exist_ok=True)


def load_one_day(symbol: str, day: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA / f"{symbol}_1m.parquet",
                         columns=["open_time", "open", "high", "low", "close", "quote_volume"])
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df[(df["ts"] >= pd.Timestamp(day, tz="UTC")) &
            (df["ts"] < pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1))]
    return df.reset_index(drop=True)


def vpvr_profile(bars: pd.DataFrame, n_buckets: int = 200):
    lo = float(bars["low"].min())
    hi = float(bars["high"].max())
    edges = np.linspace(lo, hi, n_buckets + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    mids = bars["close"].to_numpy()
    vols = bars["quote_volume"].to_numpy()
    idx = np.clip(np.searchsorted(edges, mids, side="right") - 1, 0, n_buckets - 1)
    bv = np.zeros(n_buckets)
    np.add.at(bv, idx, vols)
    return centers, bv


def find_hvn_lvn(prices, vol):
    kernel = np.exp(-0.5 * (np.arange(-9, 10) / 3) ** 2)
    kernel = kernel / kernel.sum()
    sv = np.convolve(vol, kernel, mode="same")
    hvn_idx = int(np.argmax(sv))
    hvn_price = float(prices[hvn_idx])
    if hvn_idx < 5:
        lvn_lo = float(prices[0])
    else:
        seg = sv[:hvn_idx]
        thr = np.percentile(seg, 20)
        cands = np.where(seg <= thr)[0]
        lvn_lo = float(prices[cands[np.argmax(cands)]] if len(cands) else prices[0])
    if hvn_idx >= len(sv) - 5:
        lvn_hi = float(prices[-1])
    else:
        seg = sv[hvn_idx + 1:]
        thr = np.percentile(seg, 20)
        cands = np.where(seg <= thr)[0] + hvn_idx + 1
        lvn_hi = float(prices[cands[np.argmin(cands)]] if len(cands) else prices[-1])
    return hvn_price, lvn_lo, lvn_hi


def figure1_structure():
    """Pick a representative BTC daily window with a clean VPVR profile."""
    # 2025-12-15: BTC was trading ~87-89k range; should have a clean profile.
    bars = load_one_day("BTCUSDT", "2025-12-15")
    if len(bars) < 200:
        bars = load_one_day("BTCUSDT", "2025-08-15")
    prices, vol = vpvr_profile(bars, n_buckets=200)
    hvn, lvn_lo, lvn_hi = find_hvn_lvn(prices, vol)
    full_range = lvn_hi - lvn_lo
    half_lo_bp = (hvn - lvn_lo) / hvn * 1e4
    half_hi_bp = (lvn_hi - hvn) / hvn * 1e4

    fig, ax = plt.subplots(figsize=(11, 6))

    # Volume profile (horizontal bars)
    bar_height = (prices.max() - prices.min()) / len(prices) * 0.8
    ax.barh(prices, vol, height=bar_height, color="#9ec5e0", edgecolor="#577a99",
            alpha=0.85, label="Volume-at-price")

    # Annotate HVN (center)
    ax.axhline(hvn, color="#d62728", linestyle="--", linewidth=1.5,
               label=f"HVN center (TP1): ${hvn:,.0f}")
    # Annotate LVN edges
    ax.axhline(lvn_lo, color="#2ca02c", linestyle="--", linewidth=1.5,
               label=f"Lower LVN (long entry): ${lvn_lo:,.0f}  [−{half_lo_bp:.0f}bp]")
    ax.axhline(lvn_hi, color="#ff7f0e", linestyle="--", linewidth=1.5,
               label=f"Upper LVN (short entry / TP2 long): ${lvn_hi:,.0f}  [+{half_hi_bp:.0f}bp]")
    # SL (variant A: opposite LVN + full range, on the long side here shown)
    sl_long = lvn_hi + full_range
    sl_short = lvn_lo - full_range
    ax.axhline(sl_long, color="#9467bd", linestyle=":", linewidth=1.5,
               label=f"Long SL (runaway, beyond upper LVN by full-range): ${sl_long:,.0f}")
    ax.axhline(sl_short, color="#8c564b", linestyle=":", linewidth=1.5,
               label=f"Short SL (runaway, below lower LVN by full-range): ${sl_short:,.0f}")

    # Trade path arrows
    ax.annotate("", xy=(0.85 * vol.max(), hvn), xytext=(0.85 * vol.max(), lvn_lo),
                arrowprops=dict(arrowstyle="->", color="#2ca02c", lw=2.0))
    ax.text(0.88 * vol.max(), (hvn + lvn_lo) / 2, "TP1\n(mean\nreversion)",
            color="#2ca02c", va="center", fontsize=9, fontweight="bold")

    ax.annotate("", xy=(0.85 * vol.max(), lvn_hi), xytext=(0.85 * vol.max(), hvn),
                arrowprops=dict(arrowstyle="->", color="#ff7f0e", lw=2.0))
    ax.text(0.88 * vol.max(), (hvn + lvn_hi) / 2, "TP2\n(runner)",
            color="#ff7f0e", va="center", fontsize=9, fontweight="bold")

    ax.set_xlabel("Volume (quote)")
    ax.set_ylabel("Price (USDT)")
    ax.set_title(f"VPVR profile — BTCUSDT {bars['ts'].iloc[0].date()} (UTC daily window)\n"
                 f"HVN ${hvn:,.0f} | LVN edges ${lvn_lo:,.0f} / ${lvn_hi:,.0f} | "
                 f"half-range {half_lo_bp:.0f}bp / {half_hi_bp:.0f}bp | full-range {(lvn_hi-lvn_lo)/hvn*1e4:.0f}bp")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig1_vpvr_structure.png", dpi=150)
    plt.close(fig)
    print(f"saved {OUT / 'fig1_vpvr_structure.png'} (window BTCUSDT {bars['ts'].iloc[0].date()})")


def figure2_cost_waterfall():
    """Cost decomposition waterfall: gross edge - fee - AS - queue = net edge."""
    # Use medians from measurement results
    # (TP1 hit rate, mean markout filled for scenario_b_defensive, 1d horizon, combined)
    gross_edge_bp = {
        "BTCUSDT": {"median_half_lower_bp": 74.8, "median_half_upper_bp": 85.3,
                    "mean_mark_filled_bp": 53.7, "fill_rate": 0.9993,
                    "tp1_rate": 0.8088, "dropout_rate": 0.1110},
        "ETHUSDT": {"median_half_lower_bp": 118.0, "median_half_upper_bp": 123.6,
                    "mean_mark_filled_bp": 71.9, "fill_rate": 0.9993,
                    "tp1_rate": 0.7759, "dropout_rate": 0.1385},
        "SOLUSDT": {"median_half_lower_bp": 140.7, "median_half_upper_bp": 146.4,
                    "mean_mark_filled_bp": 89.2, "fill_rate": 0.9993,
                    "tp1_rate": 0.7704, "dropout_rate": 0.1316},
    }
    # VIP0 cost decomposition from T10 pre-SPEC (SMA-36598, 2026-07-26)
    # fee 4bp + AS 3.5bp + queue 1.5bp = 9bp pair-RT
    vip0_fee_bp = 4.0
    vip0_as_bp = 3.5
    vip0_queue_bp = 1.5
    vip0_total_bp = vip0_fee_bp + vip0_as_bp + vip0_queue_bp

    fig, ax = plt.subplots(figsize=(11, 6))
    symbols = list(gross_edge_bp.keys())
    x = np.arange(len(symbols))
    width = 0.20

    median_gross = [gross_edge_bp[s]["median_half_lower_bp"] for s in symbols]
    mean_gross = [gross_edge_bp[s]["mean_mark_filled_bp"] for s in symbols]
    median_gross_avg = [(gross_edge_bp[s]["median_half_lower_bp"] + gross_edge_bp[s]["median_half_upper_bp"]) / 2 for s in symbols]

    # Bar 1: median half-range (gross edge if all TP1)
    bars1 = ax.bar(x - 1.5 * width, median_gross_avg, width, color="#9ec5e0",
                   label="Median half-range (gross edge)", edgecolor="#577a99")
    # Bar 2: mean markout filled (scenario B — defensive) — already net of TP1/SL mix
    bars2 = ax.bar(x - 0.5 * width, mean_gross, width, color="#a3d9a3",
                   label="Mean markout filled (defensive mix)", edgecolor="#2ca02c")
    # Bar 3: VIP0 pair-RT floor
    bars3 = ax.bar(x + 0.5 * width, [vip0_total_bp] * len(symbols), width, color="#ffcccc",
                   label=f"VIP0 pair-RT floor (fee+AS+queue) = {vip0_total_bp:.1f}bp", edgecolor="#d62728")
    # Bar 4: NET (mean mark - VIP0 cost) — only meaningful if positive
    net = [mean_gross[i] - vip0_total_bp for i in range(len(symbols))]
    bars4 = ax.bar(x + 1.5 * width, net, width, color="#ffd966",
                   label="NET edge after VIP0 pair-RT", edgecolor="#b8860b")

    # Annotate bars
    for b, v in zip(bars1, median_gross_avg):
        ax.text(b.get_x() + b.get_width() / 2, v + 1, f"{v:.0f}", ha="center", fontsize=8)
    for b, v in zip(bars2, mean_gross):
        ax.text(b.get_x() + b.get_width() / 2, v + 1, f"{v:+.0f}", ha="center", fontsize=8)
    for b, v in zip(bars3, [vip0_total_bp] * len(symbols)):
        ax.text(b.get_x() + b.get_width() / 2, v + 1, f"{v:.1f}", ha="center", fontsize=8)
    for b, v in zip(bars4, net):
        ax.text(b.get_x() + b.get_width() / 2, v + 1, f"{v:+.0f}", ha="center", fontsize=8)

    ax.set_xticks(x)
    ax.set_xticklabels(symbols)
    ax.set_ylabel("Edge (bp)")
    ax.set_title("Cost decomposition — VPVR edge-limit reversion (1d horizon, scenario_b_defensive)\n"
                 "Gross edge (median half-range) → Mean markout filled (mix of TP1/SL) → VIP0 pair-RT floor → NET")
    ax.axhline(30, color="gray", linestyle=":", linewidth=1)
    ax.text(len(symbols) - 0.5, 30, "30bp cost-cap floor", color="gray", fontsize=8, va="bottom", ha="right")
    ax.axhline(0, color="black", linewidth=0.7)
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig2_cost_waterfall.png", dpi=150)
    plt.close(fig)
    print(f"saved {OUT / 'fig2_cost_waterfall.png'}")


def figure3_horizon_decay():
    """Optional 3rd figure: TP1 hit rate and mean markout across horizons 1h/4h/1d."""
    data = {
        "BTCUSDT": {"1h": (-51.5, 0.099), "4h": (5.9, 0.402), "1d": (73.0, 0.891)},
        "ETHUSDT": {"1h": (-86.6, 0.094), "4h": (2.5, 0.386), "1d": (102.6, 0.879)},
        "SOLUSDT": {"1h": (-89.5, 0.092), "4h": (4.8, 0.376), "1d": (126.5, 0.873)},
    }
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    horizons = ["1h", "4h", "1d"]
    syms = list(data.keys())
    x = np.arange(len(horizons))
    width = 0.25

    # Left: mean markout filled (scenario A literal)
    ax = axes[0]
    for i, s in enumerate(syms):
        means = [data[s][h][0] for h in horizons]
        bars = ax.bar(x + (i - 1) * width, means, width, label=s)
        for b, v in zip(bars, means):
            ax.text(b.get_x() + b.get_width() / 2, v + (3 if v >= 0 else -5),
                    f"{v:+.0f}", ha="center", fontsize=8)
    ax.axhline(0, color="black", linewidth=0.7)
    ax.axhline(9, color="gray", linestyle=":", linewidth=1, label="VIP0 pair-RT floor")
    ax.axhline(30, color="gray", linestyle="--", linewidth=1, label="30bp cost-cap floor")
    ax.set_xticks(x)
    ax.set_xticklabels(horizons)
    ax.set_ylabel("Mean markout filled (bp) — scenario A literal")
    ax.set_title("Mean markout filled by horizon")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)

    # Right: TP1 hit rate
    ax = axes[1]
    for i, s in enumerate(syms):
        rates = [data[s][h][1] for h in horizons]
        bars = ax.bar(x + (i - 1) * width, rates, width, label=s)
        for b, v in zip(bars, rates):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.0%}",
                    ha="center", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels(horizons)
    ax.set_ylabel("P(TP1 hit first | filled)")
    ax.set_title("TP1 first-hit rate by horizon")
    ax.set_ylim(0, 1.0)
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.3)

    fig.suptitle("Horizon decay — VPVR edge-limit reversion (2y BTC/ETH/SOL daily signal)")
    fig.tight_layout()
    fig.savefig(OUT / "fig3_horizon_decay.png", dpi=150)
    plt.close(fig)
    print(f"saved {OUT / 'fig3_horizon_decay.png'}")


def main():
    figure1_structure()
    figure2_cost_waterfall()
    figure3_horizon_decay()


if __name__ == "__main__":
    main()