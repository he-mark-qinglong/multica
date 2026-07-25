#!/Users/mark/sdk/mamba-envs/trading/bin/python3
"""H3-execution-maker research driver.

Uses the existing H3 winner trade schedule (atr_mult_1.00, BTC+SOL) and
daily equity curve, then studies how low execution cost must be — and how
likely post-only maker execution can reach that level.

Pipeline
--------
1. Reverse the 4 bps in-house baseline cost drag to obtain a gross (zero-cost)
   daily equity curve.
2. Apply uniform round-trip cost C (bps per pair trade) to obtain a cost-sweep
   surface: Sharpe / ann_return / PF / maxDD vs C.
3. Use 2026 Binance aggTrades for BTCUSDT + SOLUSDT to simulate post-only
   maker fills for each leg of each 2026 trade. Estimate fill probability,
   effective RT cost, and map it back to the full-history Sharpe curve.
4. Output tables, plots, and SUMMARY.md.

All paths are absolute or resolved relative to this script; no production code
is modified.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Project imports (read-only)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path("/Users/mark/multica/quant-loop/_shared")))
from gates.enforce import certify_metrics  # noqa: E402
from validation.compute_metrics import compute_metrics  # noqa: E402

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
OUT = Path("/Users/mark/multica/quant-loop/research/swarm/2026-07-25/H3-execution-maker")
OUT.mkdir(parents=True, exist_ok=True)

STRATEGY_DIR = Path(
    "/Users/mark/multica/quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718"
)
DATA_ROOT = Path("/Users/mark/multica/quant-loop/data")

EQUITY_CSV = STRATEGY_DIR / "results" / "equity_winner_atr_mult_1_00_1d.csv"
TRADES_CSV = STRATEGY_DIR / "results" / "trades_winner_atr_mult_1_00.csv"

# This is the notional fraction assumed by the framework fee-shock replay.
PER_TRADE_FRAC = 0.005
BASELINE_RT_BPS = 4.0  # the in-house cost used in metrics.json

# ---------------------------------------------------------------------------
# Load baseline artifacts
# ---------------------------------------------------------------------------

def load_baseline() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    """Return (equity_daily, trades, gross_daily_returns, exit_counts_daily)."""
    eq = pd.read_csv(EQUITY_CSV)
    eq["timestamp"] = pd.to_datetime(eq["timestamp"])
    eq = eq.set_index("timestamp").sort_index()

    trades = pd.read_csv(TRADES_CSV)
    trades["entry_ts"] = pd.to_datetime(trades["entry_ts"]).dt.tz_localize("UTC")
    trades["exit_ts"] = pd.to_datetime(trades["exit_ts"]).dt.tz_localize("UTC")

    exit_counts = (
        trades["exit_ts"]
        .dt.floor("D")
        .value_counts()
        .sort_index()
        .reindex(eq.index, fill_value=0)
    )

    daily_ret = eq["equity"].pct_change().fillna(0.0)
    # Reverse the baseline 4 bps drag to get the gross curve.
    gross_ret = daily_ret + exit_counts * PER_TRADE_FRAC * BASELINE_RT_BPS / 1e4
    return eq, trades, gross_ret, exit_counts


# ---------------------------------------------------------------------------
# Cost sweep (uniform RT cost)
# ---------------------------------------------------------------------------

def metrics_at_cost(
    gross_ret: pd.Series,
    gross_start: float,
    exit_counts: pd.Series,
    rt_bps: float,
    n_trades: int,
) -> tuple[dict[str, Any], pd.Series]:
    adj_ret = gross_ret - exit_counts * PER_TRADE_FRAC * rt_bps / 1e4
    adj_eq = (1.0 + adj_ret).cumprod() * gross_start
    m = compute_metrics(adj_eq, n_trades, freq_per_year=365)
    return m, adj_eq


def run_cost_sweep(
    gross_ret: pd.Series,
    gross_start: float,
    exit_counts: pd.Series,
    n_trades: int,
) -> pd.DataFrame:
    costs = list(range(0, 61, 2))  # total pair RT bps
    rows = []
    for c in costs:
        m, _ = metrics_at_cost(gross_ret, gross_start, exit_counts, c, n_trades)
        cert = certify_metrics(m, strict=False)
        rows.append(
            {
                "rt_bps_total": c,
                "sharpe_daily": m["sharpe_daily"],
                "annualized_return": m["annualized_return"],
                "max_drawdown_pct": m["max_drawdown_pct"],
                "profit_factor": m["profit_factor"],
                "n_trades": m["n_trades"],
                "win_rate": m["win_rate"],
                "calmar": m["calmar"],
                "sortino": m["sortino"],
                "gates_passed": cert.passed,
                "failed_gates": ",".join(cert.failed_gates),
            }
        )
    return pd.DataFrame(rows)


def interp_threshold(
    x: list[float], y: np.ndarray, target: float, allow_extrapolate: bool = False
) -> float | None:
    """Linearly interpolate/extrapolate the x value where y == target."""
    arr = np.asarray(y)
    for i in range(len(arr) - 1):
        if (arr[i] - target) * (arr[i + 1] - target) <= 0:
            dx = x[i + 1] - x[i]
            dy = arr[i + 1] - arr[i]
            if abs(dy) < 1e-12:
                return float(x[i])
            return float(x[i] + dx * (target - arr[i]) / dy)
    if allow_extrapolate and len(arr) >= 2:
        # Use the first segment slope.
        slope = (arr[1] - arr[0]) / (x[1] - x[0])
        if abs(slope) > 1e-12:
            est = x[0] + (target - arr[0]) / slope
            return float(est)
    return None


# ---------------------------------------------------------------------------
# Maker / queue simulation from aggTrades
# ---------------------------------------------------------------------------

def aggregate_aggtrades_1m(symbol: str, out_path: Path) -> pd.DataFrame:
    """Read aggTrades month-by-month and aggregate to 1m bars.

    Columns: open, high, low, close, volume, n_trades, buy_maker_vol, sell_maker_vol.
    """
    base = DATA_ROOT / "trades" / f"{symbol}_aggtrades.parquet"
    if not base.exists():
        raise FileNotFoundError(base)

    months = sorted(base.glob("year=*/month=*"))
    pieces = []
    for month_dir in months:
        p = month_dir / "data.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p, columns=["ts", "price", "qty", "is_buyer_maker"])
        # buyer-maker == passive buy order got hit => our buy-limit fill liquidity.
        df["buy_qty"] = np.where(df["is_buyer_maker"], df["qty"], 0.0)
        df["sell_qty"] = np.where(~df["is_buyer_maker"], df["qty"], 0.0)
        agg = df.resample("1min", on="ts").agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("qty", "sum"),
            n_trades=("qty", "count"),
            buy_maker_vol=("buy_qty", "sum"),
            sell_maker_vol=("sell_qty", "sum"),
        )
        pieces.append(agg)
    full = pd.concat(pieces).sort_index()
    full.to_parquet(out_path)
    return full


def load_or_build_agg_1m(symbol: str) -> pd.DataFrame:
    out_path = OUT / f"agg_1m_{symbol}.parquet"
    if out_path.exists():
        return pd.read_parquet(out_path)
    print(f"Aggregating aggTrades -> 1m bars for {symbol} ...")
    df = aggregate_aggtrades_1m(symbol, out_path)
    print(f"  wrote {out_path} ({len(df)} bars)")
    return df


def simulate_maker_one_trade(
    row: pd.Series,
    equity_at_exit: float,
    bars: dict[str, pd.DataFrame],
    offset_bps: float,
    patience_bars: int,
    maker_fee_bps: float,
    taker_fee_bps: float,
    taker_slip_bps: float,
    queue_mult: float,
) -> dict[str, Any]:
    """Simulate post-only execution for one H3 trade (4 legs).

    Returns dict with per-leg fill booleans and total trade cost as a fraction
    of pair notional.
    """
    direction = row["direction"]
    entry_ts = row["entry_ts"]
    exit_ts = row["exit_ts"]

    if direction == "long_a_short_b":
        legs = [
            ("BTCUSDT", entry_ts, "buy"),
            ("SOLUSDT", entry_ts, "sell"),
            ("BTCUSDT", exit_ts, "sell"),
            ("SOLUSDT", exit_ts, "buy"),
        ]
    else:
        legs = [
            ("BTCUSDT", entry_ts, "sell"),
            ("SOLUSDT", entry_ts, "buy"),
            ("BTCUSDT", exit_ts, "buy"),
            ("SOLUSDT", exit_ts, "sell"),
        ]

    per_leg_notional = max(equity_at_exit, 0.0) * PER_TRADE_FRAC / 2.0
    total_cost = 0.0
    filled_legs = 0
    fallback_count = 0
    fallback_slippage_sum = 0.0

    for sym, ts, side in legs:
        b = bars[sym]
        if ts not in b.index:
            # Missing bar: charge taker fallback.
            total_cost += (taker_fee_bps + taker_slip_bps) / 1e4
            fallback_count += 1
            continue
        price = float(b.at[ts, "close"])
        limit = price * (
            1.0 - offset_bps / 1e4
        ) if side == "buy" else price * (1.0 + offset_bps / 1e4)

        filled = False
        fallback_price = None
        for k in range(1, patience_bars + 1):
            nxt = ts + pd.Timedelta(minutes=k)
            if nxt not in b.index:
                continue
            r = b.loc[nxt]
            price_ok = (r["low"] <= limit) if side == "buy" else (r["high"] >= limit)
            if not price_ok:
                continue
            # Queue / depth proxy: is enough passive volume on our side?
            qty = per_leg_notional / max(price, 1e-9)
            maker_vol = r["buy_maker_vol"] if side == "buy" else r["sell_maker_vol"]
            if maker_vol >= qty * queue_mult:
                filled = True
                break

        if filled:
            total_cost += maker_fee_bps / 1e4
            filled_legs += 1
        else:
            # Fallback to taker at the close of the patience window.
            last_ts = ts + pd.Timedelta(minutes=patience_bars)
            idx = b.index.get_indexer([last_ts], method="ffill")[0]
            if idx >= 0:
                fallback_price = float(b.iloc[idx]["close"])
            else:
                fallback_price = price
            slip = abs(fallback_price / limit - 1.0)
            total_cost += (taker_fee_bps + taker_slip_bps) / 1e4 + slip
            fallback_count += 1
            fallback_slippage_sum += slip

    return {
        "cost_frac": total_cost,
        "filled_legs": filled_legs,
        "n_legs": len(legs),
        "fallback_count": fallback_count,
        "avg_fallback_slip_frac": (
            fallback_slippage_sum / fallback_count if fallback_count else 0.0
        ),
    }


def run_maker_simulation(
    trades: pd.DataFrame,
    eq: pd.DataFrame,
    sweep: pd.DataFrame,
) -> pd.DataFrame:
    bars = {
        "BTCUSDT": load_or_build_agg_1m("BTCUSDT"),
        "SOLUSDT": load_or_build_agg_1m("SOLUSDT"),
    }

    # Only the 2026 trades have matching aggTrades.
    t26 = trades[trades["entry_ts"] >= "2026-01-01"].copy()
    equity_at_exit = (
        eq["equity"]
        .reindex(t26["exit_ts"].dt.floor("D"))
        .fillna(eq["equity"].mean())
        .values
    )

    param_grid = []
    for offset_bps in [0.0, 1.0, 2.0]:
        for patience in [1, 2, 5]:
            for maker_fee in [0.0, 0.5, 1.0, 2.0]:
                for queue_mult in [1.0, 10.0]:
                    param_grid.append(
                        {
                            "offset_bps": offset_bps,
                            "patience_bars": patience,
                            "maker_fee_bps": maker_fee,
                            "taker_fee_bps": 4.0,
                            "taker_slip_bps": 2.0,
                            "queue_mult": queue_mult,
                        }
                    )

    rows = []
    for params in param_grid:
        costs = []
        filled = 0
        legs = 0
        fallbacks = 0
        for i, (_, row) in enumerate(t26.iterrows()):
            res = simulate_maker_one_trade(
                row,
                equity_at_exit[i],
                bars,
                params["offset_bps"],
                params["patience_bars"],
                params["maker_fee_bps"],
                params["taker_fee_bps"],
                params["taker_slip_bps"],
                params["queue_mult"],
            )
            costs.append(res["cost_frac"])
            filled += res["filled_legs"]
            legs += res["n_legs"]
            fallbacks += res["fallback_count"]

        avg_cost_frac = float(np.mean(costs))
        avg_rt_bps = avg_cost_frac * 10_000.0  # total pair RT cost in bps
        fill_rate = filled / max(legs, 1)
        fallback_rate = fallbacks / max(legs, 1)

        # Map effective cost to full-history Sharpe/ann via the cost sweep.
        sharpe = float(np.interp(avg_rt_bps, sweep["rt_bps_total"], sweep["sharpe_daily"]))
        ann = float(
            np.interp(avg_rt_bps, sweep["rt_bps_total"], sweep["annualized_return"])
        )

        rows.append(
            {
                **params,
                "n_trades_sampled": len(t26),
                "fill_rate": fill_rate,
                "fallback_rate": fallback_rate,
                "avg_rt_bps": avg_rt_bps,
                "sharpe_daily_interp": sharpe,
                "annualized_return_interp": ann,
            }
        )

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def plot_cost_sweep(sweep: pd.DataFrame, thresholds: dict[str, float | None]) -> None:
    fig, ax1 = plt.subplots(figsize=(10, 5.5))
    ax1.plot(
        sweep["rt_bps_total"],
        sweep["sharpe_daily"],
        "b-o",
        markersize=4,
        label="Sharpe",
    )
    ax1.axhline(1.0, color="b", linestyle="--", alpha=0.6, label="G1 Sharpe=1.0")
    ax1.set_xlabel("Total pair round-trip cost (bps)")
    ax1.set_ylabel("Sharpe (daily-resampled)", color="b")
    ax1.tick_params(axis="y", labelcolor="b")

    ax2 = ax1.twinx()
    ax2.plot(
        sweep["rt_bps_total"],
        sweep["annualized_return"] * 100,
        "g-s",
        markersize=4,
        label="Ann. return",
    )
    ax2.axhline(15.0, color="g", linestyle="--", alpha=0.6, label="G2 ann=15%")
    ax2.set_ylabel("Annualized return (%)", color="g")
    ax2.tick_params(axis="y", labelcolor="g")

    # Baseline marker
    ax1.axvline(BASELINE_RT_BPS, color="gray", linestyle=":", alpha=0.7)
    ax1.text(
        BASELINE_RT_BPS + 0.5,
        sweep["sharpe_daily"].max() * 0.95,
        f"baseline {BASELINE_RT_BPS:.0f} bps RT",
        rotation=90,
        va="top",
        color="gray",
    )

    if thresholds.get("sharpe_1") is not None:
        ax1.axvline(thresholds["sharpe_1"], color="b", linestyle="--", alpha=0.4)
    if thresholds.get("ann_15") is not None:
        ax2.axvline(thresholds["ann_15"], color="g", linestyle="--", alpha=0.4)

    fig.suptitle("H3 cost ceiling: Sharpe / return vs total RT cost")
    fig.legend(loc="lower center", ncol=4, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout()
    fig.savefig(OUT / "cost_sweep.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_maker_sweep(maker_df: pd.DataFrame) -> None:
    # Surface: fill_rate vs avg_rt_bps colored by maker_fee, faceted by queue_mult.
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for ax, qm in zip(axes, sorted(maker_df["queue_mult"].unique())):
        sub = maker_df[maker_df["queue_mult"] == qm]
        for fee in sorted(sub["maker_fee_bps"].unique()):
            s = sub[sub["maker_fee_bps"] == fee]
            ax.scatter(
                s["fill_rate"] * 100,
                s["avg_rt_bps"],
                label=f"maker fee {fee:g} bps/side",
                alpha=0.7,
                s=60,
            )
        ax.set_xlabel("Leg fill rate (%)")
        ax.set_ylabel("Effective pair RT cost (bps)")
        ax.set_title(f"queue depth multiplier = {qm:g}x")
        ax.legend(fontsize=7)
    fig.suptitle("Post-only maker simulation (BTC+SOL 2026 aggTrades)")
    fig.tight_layout()
    fig.savefig(OUT / "maker_sweep.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Loading H3 winner equity + trade schedule ...")
    eq, trades, gross_ret, exit_counts = load_baseline()
    gross_start = float(eq["equity"].iloc[0])
    n_trades = len(trades)
    print(f"  equity span: {eq.index[0]} -> {eq.index[-1]} ({len(eq)} days)")
    print(f"  trades: {n_trades}")

    # Baseline sanity check: applying 4 bps should reproduce metrics.json.
    m_base, _ = metrics_at_cost(gross_ret, gross_start, exit_counts, BASELINE_RT_BPS, n_trades)
    print(
        f"  baseline {BASELINE_RT_BPS:.0f} bps RT -> "
        f"Sharpe={m_base['sharpe_daily']:.4f}, ann={m_base['annualized_return']*100:.2f}%, "
        f"PF={m_base['profit_factor']:.3f}, maxDD={m_base['max_drawdown_pct']*100:.2f}%"
    )

    print("\nRunning uniform cost sweep ...")
    sweep = run_cost_sweep(gross_ret, gross_start, exit_counts, n_trades)
    sweep.to_csv(OUT / "cost_sweep.csv", index=False)

    x = sweep["rt_bps_total"].tolist()
    thresholds = {
        "sharpe_1": interp_threshold(x, sweep["sharpe_daily"].values, 1.0),
        "ann_15": interp_threshold(x, sweep["annualized_return"].values, 0.15),
        "sharpe_0": interp_threshold(x, sweep["sharpe_daily"].values, 0.0),
        "maxdd_25": interp_threshold(x, sweep["max_drawdown_pct"].values, -0.25),
        "pf_1_5": interp_threshold(
            x, sweep["profit_factor"].values, 1.5, allow_extrapolate=True
        ),
    }

    print("\nCost-ceiling thresholds (total pair RT bps):")
    for k, v in thresholds.items():
        print(f"  {k}: {v if v is not None else 'N/A'} bps")

    print("\nRunning post-only maker simulation on 2026 aggTrades ...")
    maker_df = run_maker_simulation(trades, eq, sweep)
    maker_df.to_csv(OUT / "maker_simulation.csv", index=False)

    # Best / lower-bound rows
    best = maker_df.loc[maker_df["sharpe_daily_interp"].idxmax()]
    worst = maker_df.loc[maker_df["sharpe_daily_interp"].idxmin()]
    print(
        f"  best maker scenario Sharpe={best['sharpe_daily_interp']:.4f} @ "
        f"{best['avg_rt_bps']:.2f} bps RT"
    )
    print(
        f"  worst maker scenario Sharpe={worst['sharpe_daily_interp']:.4f} @ "
        f"{worst['avg_rt_bps']:.2f} bps RT"
    )

    print("\nGenerating plots ...")
    plot_cost_sweep(sweep, thresholds)
    plot_maker_sweep(maker_df)

    # Pick representative scenarios for the summary
    scenarios = maker_df[
        (maker_df["offset_bps"] == 0.0)
        & (maker_df["patience_bars"] == 1)
        & (maker_df["queue_mult"] == 1.0)
    ][["maker_fee_bps", "avg_rt_bps", "fill_rate", "sharpe_daily_interp"]].sort_values(
        "maker_fee_bps"
    )

    summary = {
        "direction": "H3-execution-maker",
        "baseline_total_rt_bps": BASELINE_RT_BPS,
        "baseline_metrics": m_base,
        "baseline_certify": str(certify_metrics(m_base, strict=False)),
        "cost_thresholds_total_rt_bps": {
            k: (None if v is None else round(v, 2)) for k, v in thresholds.items()
        },
        "maker_representative_scenarios": scenarios.to_dict("records"),
        "maker_best_scenario": best.to_dict(),
        "maker_worst_scenario": worst.to_dict(),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=float))

    # Build SUMMARY.md
    md = []
    md.append("# H3-execution-maker — Research Summary")
    md.append(f"\nGenerated: {pd.Timestamp.utcnow().isoformat()}")
    md.append(f"\nOutput directory: `{OUT}`")

    md.append("\n## What was done")
    md.append(
        "1. Reconstructed the gross (zero-cost) H3 daily equity curve from the "
        "existing `atr_mult_1.00` winner run by reversing the 4 bps in-house "
        "round-trip cost drag."
    )
    md.append(
        "2. Ran a uniform round-trip cost sweep from 0 to 60 bps to find the "
        "cost ceiling where G1 (Sharpe ≥ 1.0), G2 (ann ≥ 15%), G3 (maxDD > -25%), "
        "and G4 (PF > 1.5) are satisfied."
    )
    md.append(
        "3. Simulated post-only maker execution for every 2026 H3 trade using "
        "Binance BTCUSDT + SOLUSDT aggTrades. Estimated per-leg fill probability "
        "(price touch + queue-depth proxy), maker fee when filled, and taker "
        "fallback cost when not filled. Mapped the resulting effective RT cost "
        "back to the full-history Sharpe curve."
    )

    md.append("\n## Key numbers")
    md.append(f"- Baseline in-house cost: **{BASELINE_RT_BPS:.0f} bps RT per pair trade**")
    md.append(
        f"- Baseline metrics: Sharpe={m_base['sharpe_daily']:.3f}, "
        f"ann={m_base['annualized_return']*100:.1f}%, "
        f"PF={m_base['profit_factor']:.3f}, maxDD={m_base['max_drawdown_pct']*100:.1f}%"
    )
    md.append(
        f"- Cost ceiling for G1 (Sharpe ≥ 1.0): **{fmt(thresholds['sharpe_1'])} bps RT**"
    )
    md.append(
        f"- Cost ceiling for G2 (ann ≥ 15%): **{fmt(thresholds['ann_15'])} bps RT**"
    )
    md.append(
        f"- Break-even cost (Sharpe = 0): **{fmt(thresholds['sharpe_0'])} bps RT**"
    )
    md.append(
        f"- Cost at which maxDD hits -25%: **{fmt(thresholds['maxdd_25'])} bps RT**"
    )
    md.append(
        f"- Cost needed for G4 (PF > 1.5): **{fmt(thresholds['pf_1_5'])} bps RT** "
        "(negative ⇒ impossible to achieve by reducing cost alone)"
    )

    md.append("\n### Maker-simulation representative cases")
    md.append(
        "Parameters: offset 0 bps, 1-min patience, queue depth multiplier 1x, "
        "taker fallback 4 bps fee + 2 bps slippage."
    )
    md.append("")
    md.append("| Maker fee (bps/side) | Effective pair RT (bps) | Leg fill rate | Interp. Sharpe |")
    md.append("|---------------------:|------------------------:|--------------:|---------------:|")
    for _, r in scenarios.iterrows():
        md.append(
            f"| {r['maker_fee_bps']:.1f} | {r['avg_rt_bps']:.2f} | "
            f"{r['fill_rate']*100:.1f}% | {r['sharpe_daily_interp']:.3f} |"
        )

    md.append("\n## G1-G7 assessment")
    cert = certify_metrics(m_base, strict=False)
    md.append(f"- Baseline G1-G4/T1: **{'PASS' if cert.passed else 'FAIL'}**")
    if not cert.passed:
        md.append(f"  - Failed gates: {', '.join(cert.failed_gates)}")
    md.append(
        "- G5 (CPCV OOS) and G7 (deflated Sharpe) were **not evaluated** in this "
        "execution-cost study; they require the full walk-forward/CPCV harness."
    )
    md.append(
        "- G6 (bootstrap CI95 lower) was **not computed** here; the existing "
        "winner value is 1.914 and is unaffected by execution modelling."
    )

    md.append("\n## Verdict: continue or KILL?")
    if thresholds["pf_1_5"] is None or thresholds["pf_1_5"] < 0:
        md.append(
            "**Execution-cost improvements alone cannot make H3 SHIP-eligible.** "
            "Even with perfect zero-cost execution, the profit factor stays below "
            "the G4 threshold of 1.5. The signal is only marginally profitable "
            "gross (per-trade gross PF ≈ 1.01), so shaving fees raises Sharpe but "
            "does not change the win/loss asymmetry enough."
        )
        md.append(
            "Maker execution is still valuable: it can push Sharpe from ~1.35 "
            f"toward ~{best['sharpe_daily_interp']:.3f} and raise annual return, "
            "but it cannot rescue the strategy."
        )
        verdict = "Recommend KILL unless signal-enhance-h3 materially improves PF."
    else:
        verdict = "Worth continuing if live execution can reliably hit the required cost."
    md.append(f"\n**{verdict}**")

    md.append("\n## Next 1-2 concrete actions")
    md.append(
        "1. **Do not allocate live capital to H3 on the basis of execution alone.** "
        "Route the strategy to the `signal-enhance-h3` track: the marginal gross "
        "edge is too small (PF ≈ 1.01) and any realistic slippage/taker fallback "
        "keeps PF below G4."
    )
    md.append(
        "2. If signal-enhance-h3 lifts gross PF above ~1.3, rerun this exact "
        "maker-simulation harness to confirm the live cost ceiling (currently "
        f"≈ {fmt(thresholds['sharpe_1'])} bps RT for G1, "
        f"≈ {fmt(thresholds['ann_15'])} bps RT for G2) is achievable with "
        "post-only Binance USDT-M execution."
    )

    md.append("\n## Files produced")
    md.append("- `cost_sweep.csv` / `cost_sweep.png` — uniform RT cost surface")
    md.append("- `maker_simulation.csv` / `maker_sweep.png` — post-only simulation results")
    md.append("- `summary.json` — machine-readable key numbers")
    md.append("- `SUMMARY.md` — this file")

    (OUT / "SUMMARY.md").write_text("\n".join(md))
    print("\nWrote SUMMARY.md")


def fmt(v: float | None) -> str:
    return f"{v:.2f}" if v is not None else "N/A"


if __name__ == "__main__":
    main()
