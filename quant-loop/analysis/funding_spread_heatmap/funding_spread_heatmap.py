"""Funding-rate spread heatmap: BTC vs ETH vs SOL, 1h intervals, last 30 days.

Loads 8h-cadence Binance USDT-M funding rates from
``~/multica/quant-loop/data/funding/{SYM}.parquet``, upsamples to a 1h grid
(via forward-fill — funding rate applies for the full 8h window until the
next funding event at 00:00/08:00/16:00 UTC), and computes pairwise funding
spreads:

  * BTC - ETH
  * BTC - SOL
  * ETH - SOL

Outputs
-------
  * ``heatmap.png``                — 3-panel heatmap (date × hour-of-day).
  * ``daily_mean_spread.png``      — daily-mean spread line chart for trend.
  * ``divergence_windows.csv``     — top divergence / convergence windows.
  * ``summary.json``               — machine-readable summary + key stats.

Run:
    python3 funding_spread_heatmap.py
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# matplotlib is non-interactive; Agg backend so it works headless.
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm

# ---------- Constants -----------------------------------------------------

DATA_DIR = Path("/home/smark/multica/quant-loop/data/funding")
OUT_DIR = Path("/home/smark/multica/quant-loop/analysis/funding_spread_heatmap")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Calendar window anchored to the issue (30d, ending at the latest funding
# timestamp available in the parquet store — 2026-07-17 08:00 UTC).
END_TS = pd.Timestamp("2026-07-17 08:00:00", tz="UTC")
WINDOW_DAYS = 30
START_TS = END_TS - pd.Timedelta(days=WINDOW_DAYS)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
PAIRS = [
    ("BTCUSDT", "ETHUSDT"),
    ("BTCUSDT", "SOLUSDT"),
    ("ETHUSDT", "SOLUSDT"),
]

# In funding-rate units (0.0001 = 1 bp per 8h). Spreads typically tiny.
BPS = 1e-4


# ---------- Loaders -------------------------------------------------------

@dataclass
class FundingSeries:
    symbol: str
    df: pd.DataFrame  # indexed by ts (UTC), cols: fundingRate


def load_funding(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> FundingSeries:
    """Read one symbol's funding parquet and slice to [start, end)."""
    path = DATA_DIR / f"{symbol}.parquet"
    df = pd.read_parquet(path)
    df = df[["ts", "fundingRate"]].copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts"]).set_index("ts").sort_index()
    df = df.loc[(df.index >= start) & (df.index < end)]
    return FundingSeries(symbol=symbol, df=df)


def build_hourly_grid(series_by_symbol: dict[str, FundingSeries]) -> pd.DataFrame:
    """Forward-fill 8h funding to a 1h grid for [START_TS, END_TS).

    Funding rates on Binance are *constant* between funding events; the rate
    set at 00:00/08:00/16:00 UTC applies for the next 8h. Forward-fill is
    therefore the correct upsampling policy (not interpolation).
    """
    hourly_index = pd.date_range(
        start=START_TS.ceil("h"),
        end=END_TS.ceil("h") - pd.Timedelta(hours=1),
        freq="1h",
        tz="UTC",
    )
    out = pd.DataFrame(index=hourly_index)
    for sym, fs in series_by_symbol.items():
        out[sym] = fs.df["fundingRate"].reindex(hourly_index, method="ffill")
    # If the first row is NaN (no prior funding before window start), back-fill.
    out = out.ffill().bfill()
    return out


def compute_spreads(hourly: pd.DataFrame) -> pd.DataFrame:
    """Pairwise funding spreads (rate_A - rate_B) at each 1h bar."""
    out = pd.DataFrame(index=hourly.index)
    for a, b in PAIRS:
        out[f"{a}-{b}"] = hourly[a] - hourly[b]
    return out


# ---------- Divergence / convergence detection ---------------------------

def find_extreme_windows(spreads: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    """Flag windows with extreme spread magnitude.

    Divergence  = |spread| at >= 90th percentile (top tail, per pair).
    Convergence = |spread| at <= 10th percentile (bottom tail, per pair).

    Returns a single DataFrame sorted by abs-spread descending.
    """
    abs_spreads = spreads.abs()
    upper = abs_spreads.quantile(0.90)  # Series indexed by column
    lower = abs_spreads.quantile(0.10)  # Series indexed by column

    rows: list[dict] = []
    for col in abs_spreads.columns:
        s = spreads[col]
        u = float(upper[col])
        lo = float(lower[col])
        for ts, val in s.items():
            a_val = float(abs(val))
            if a_val >= u:
                regime = "divergence"
            elif a_val <= lo:
                regime = "convergence"
            else:
                continue
            rows.append(
                {
                    "pair": col,
                    "ts": ts,
                    "spread": float(val),
                    "abs_spread": a_val,
                    "regime": regime,
                }
            )
    df = pd.DataFrame(rows).sort_values("abs_spread", ascending=False).reset_index(drop=True)
    # Cap to top_n per pair per regime to keep output bounded.
    keep = (
        df.groupby(["pair", "regime"])
        .head(top_n)
        .sort_values(["pair", "regime", "abs_spread"], ascending=[True, True, False])
        .reset_index(drop=True)
    )
    return keep


# ---------- Visualization --------------------------------------------------

def _heat_data(spread_series: pd.Series) -> tuple[np.ndarray, list, list]:
    """Reshape a 1h spread series into a (hour, day) matrix for imshow."""
    df = pd.DataFrame({"ts": spread_series.index, "v": spread_series.values})
    df["date"] = df["ts"].dt.date
    df["hour"] = df["ts"].dt.hour
    pivot = df.pivot(index="hour", columns="date", values="v").sort_index()
    # Ensure full 24-hour rows even if a day is missing.
    pivot = pivot.reindex(range(24))
    return pivot.values, [str(d) for d in pivot.columns], list(pivot.index)


def render_heatmap(spreads: pd.DataFrame, out_path: Path) -> dict:
    """3-panel heatmap (one per pair): x = date, y = hour-of-day, color = spread."""
    n = len(PAIRS)
    fig, axes = plt.subplots(n, 1, figsize=(16, 4.2 * n), sharex=True)
    if n == 1:
        axes = [axes]

    # Symmetric color scale across panels — pick the panel-wise max abs.
    panel_max = max(np.nanmax(np.abs(spreads[f"{a}-{b}"].values)) for a, b in PAIRS)
    panel_max = float(panel_max) if panel_max and not math.isnan(panel_max) else 1e-4

    rendered = {}
    for ax, (a, b) in zip(axes, PAIRS):
        col = f"{a}-{b}"
        mat, dates, hours = _heat_data(spreads[col])
        norm = TwoSlopeNorm(vmin=-panel_max, vcenter=0.0, vmax=panel_max)
        im = ax.imshow(
            mat,
            aspect="auto",
            cmap="RdBu_r",
            norm=norm,
            extent=(-0.5, len(dates) - 0.5, 23.5, -0.5),
            interpolation="nearest",
        )
        # Tick formatting: show every Nth date to avoid label clutter.
        step = max(1, len(dates) // 10)
        ax.set_xticks(range(0, len(dates), step))
        ax.set_xticklabels(
            [dates[i][5:] for i in range(0, len(dates), step)],  # MM-DD
            rotation=45,
            ha="right",
        )
        ax.set_yticks(range(0, 24, 2))
        ax.set_yticklabels(range(0, 24, 2))
        ax.set_ylabel("Hour (UTC)")
        ax.set_title(
            f"{a} − {b}  funding spread  (max |Δ|={panel_max/BPS:.2f} bps / 8h)",
            fontsize=11,
        )
        cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
        cbar.set_label(f"spread (Δ fundingRate × 10⁴ → bps/8h)")
        rendered[col] = {"panel_max_abs": panel_max}

    axes[-1].set_xlabel("Date (UTC)")
    fig.suptitle(
        f"Funding-rate spread heatmap — BTC / ETH / SOL, 1h grid, "
        f"{START_TS.date()} → {END_TS.date()} (30d)",
        fontsize=13,
        y=1.0,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return rendered


def render_daily_mean(spreads: pd.DataFrame, out_path: Path) -> None:
    """Line chart of daily mean spread per pair — convergence/divergence trend."""
    daily = spreads.resample("1D").mean()
    fig, ax = plt.subplots(figsize=(14, 4.5))
    for col in spreads.columns:
        ax.plot(daily.index, daily[col] / BPS, marker="o", markersize=3, label=col)
    ax.axhline(0, color="black", linewidth=0.6, linestyle="--")
    ax.set_ylabel("Daily mean spread (bps / 8h)")
    ax.set_title(
        f"Daily mean funding-rate spread — {START_TS.date()} → {END_TS.date()}"
    )
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


# ---------- Main ----------------------------------------------------------

def main() -> None:
    series = {sym: load_funding(sym, START_TS, END_TS) for sym in SYMBOLS}
    for sym, fs in series.items():
        print(f"[load] {sym}: {len(fs.df)} rows, "
              f"{fs.df.index.min()} → {fs.df.index.max()}")

    hourly = build_hourly_grid(series)
    print(f"[grid] 1h rows: {len(hourly)} (expected {WINDOW_DAYS * 24})")
    if hourly.isna().any().any():
        nan_cells = hourly.isna().sum().to_dict()
        print(f"[grid] WARNING: NaN cells: {nan_cells}")

    spreads = compute_spreads(hourly)
    print("[spread] head:")
    print(spreads.head(3).to_string())

    heatmap_path = OUT_DIR / "heatmap.png"
    daily_path = OUT_DIR / "daily_mean_spread.png"
    csv_path = OUT_DIR / "divergence_windows.csv"
    summary_path = OUT_DIR / "summary.json"

    render_heatmap(spreads, heatmap_path)
    render_daily_mean(spreads, daily_path)

    extremes = find_extreme_windows(spreads, top_n=5)
    extremes.to_csv(csv_path, index=False)
    print(f"[extreme] divergence/convergence windows: {len(extremes)} → {csv_path}")

    # Summary stats.
    summary = {
        "window": {
            "start": START_TS.isoformat(),
            "end": END_TS.isoformat(),
            "days": WINDOW_DAYS,
            "cadence": "1h (upsampled from 8h funding via ffill)",
        },
        "symbols": SYMBOLS,
        "pairs": [f"{a}-{b}" for a, b in PAIRS],
        "n_funding_obs_per_symbol": {sym: len(fs.df) for sym, fs in series.items()},
        "n_1h_bars": len(hourly),
        "per_pair_stats": {},
    }
    for col in spreads.columns:
        s = spreads[col]
        summary["per_pair_stats"][col] = {
            "mean_bps": float(s.mean() / BPS),
            "std_bps": float(s.std() / BPS),
            "min_bps": float(s.min() / BPS),
            "max_bps": float(s.max() / BPS),
            "abs_mean_bps": float(s.abs().mean() / BPS),
            "abs_p90_bps": float(s.abs().quantile(0.90) / BPS),
            "abs_p10_bps": float(s.abs().quantile(0.10) / BPS),
        }
    summary["divergence_count"] = int((extremes["regime"] == "divergence").sum())
    summary["convergence_count"] = int((extremes["regime"] == "convergence").sum())
    summary["output_files"] = {
        "heatmap_png": str(heatmap_path),
        "daily_mean_png": str(daily_path),
        "extremes_csv": str(csv_path),
    }

    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"[done] summary → {summary_path}")
    print(f"[done] heatmap → {heatmap_path}")
    print(f"[done] daily → {daily_path}")


if __name__ == "__main__":
    main()
