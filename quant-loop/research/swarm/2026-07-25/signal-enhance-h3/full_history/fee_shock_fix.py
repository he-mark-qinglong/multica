"""fee_shock_fix — corrected fee-shock replay for SE-H3 / H3-baseline family.

Background (SMA-36566, 2026-07-26)
---------------------------------
The original ``fee_shock_metrics`` (run_btcsol_variants_fixed.py L313,
repro_h3_baseline.py L282) computes daily cost drag as

    drag = counts * (pair_rt_bps / 10_000) * per_trade_fraction(0.005)

This ``per_trade_fraction = 0.005`` is the bug. The engine debits cost in
**full pair pct** (se_h3_loop.py L227: ``cost = 2*2*(fee+slip)/10000``,
full-pair pct per trade) while the trade log records ``pnl_pct`` in the same
full-pair pct basis. The equity curve compounds ``pos*(a_ret-b_ret)/2 * scale``
(half-spread per-bar returns), but a per-trade equity contribution at exit
equals the trade log's ``pnl_pct`` (= full pair pct - cost). So cost MUST be
debited from equity at the same full-pair pct basis as ``pnl_pct``.

Applying the drag at 0.5% of notional (0.005) under-states it by 200× relative
to the trade log's full-pair cost basis. With pair_rt_bps=24 and ~5 trades/day
the actual drag should be ~120 bps/day, not ~6 bps/day. The claimed
"Sharpe 10.41 at 60bps pair RT" is therefore an artefact: at the corrected
basis the strategy is dead long before 24 bps.

This script is the standalone re-runnable fix. It reads existing equity +
trade CSVs (or pickles) and emits a corrected fee-shock JSON. No upstream
files are modified — the original (buggy) outputs are preserved as
``*.buggy.json`` next to the corrected ones for transparency.

Usage
-----
    python fee_shock_fix.py \
        --equity-csv results/se_h3_equity_daily.csv \
        --trades-csv results/se_h3_trades.csv \
        --out-json results/se_h3_fee_shock.fixed.json

Per_trade_fraction is fixed at 1.0 (full-pair pct basis). Other values are
exposed as CLI flags for sensitivity:

    --per-trade-fraction {0.5,1.0,2.0}

0.5  = half-spread basis (matches bar-return normalisation exactly).
1.0  = full-pair pct basis (matches trade log ``pnl_pct``, default).
2.0  = sanity upper bound.

The default is the value that matches the trade log's accounting semantics.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


def fee_shock_metrics_fixed(
    equity: pd.Series,
    trades: list[dict],
    pair_rt_bps: float,
    per_trade_fraction: float = 1.0,
) -> dict:
    """Fee-shock replay with corrected notional basis.

    The bug fix is the default ``per_trade_fraction=1.0`` (= full-pair pct
    basis, matching the engine's ``cost = 2*2*(fee+slip)/10000`` and the
    trade log's ``pnl_pct`` definition). All other math is unchanged from
    the upstream ``fee_shock_metrics`` so the methodology diff is just the
    notional scaling.

    Returns dict with: pair_round_trip_bps, sharpe_daily_resampled,
    annualized_return, total_return, max_drawdown_pct,
    per_trade_fraction_used, drag_per_trade_pct, mean_daily_trades,
    mean_daily_drag_pct.
    """
    daily_eq = equity.resample("1D").last().dropna()
    daily_ret = daily_eq.pct_change().fillna(0.0)

    drag = pd.Series(0.0, index=daily_eq.index)
    if trades:
        exit_dates = pd.to_datetime([t["exit_ts"] for t in trades], errors="coerce")
        if exit_dates.tz is not None:
            exit_dates = exit_dates.tz_convert(None)
        exit_dates = exit_dates.floor("D")
        counts = exit_dates.value_counts()
        if counts.index.tz is not None:
            counts.index = counts.index.tz_convert(None)
        drag = drag.add(
            counts * (pair_rt_bps / 10_000.0) * per_trade_fraction,
            fill_value=0.0,
        )

    adj_ret = daily_ret - drag.reindex(daily_eq.index, fill_value=0.0)
    adj_eq = (1.0 + adj_ret).cumprod() * float(daily_eq.iloc[0])

    rets = adj_eq.pct_change().dropna()
    sharpe = 0.0
    if len(rets) > 1 and float(rets.std(ddof=1)) > 1e-12:
        sharpe = float(rets.mean() / rets.std(ddof=1) * math.sqrt(365.0))
    total = float(adj_eq.iloc[-1] / adj_eq.iloc[0] - 1.0)
    span = (adj_eq.index[-1] - adj_eq.index[0]).total_seconds() / (365.25 * 24 * 3600)
    ann = float((1.0 + total) ** (1.0 / span) - 1.0) if span > 0 else 0.0
    max_dd = float((adj_eq / adj_eq.cummax() - 1.0).min())

    # Diagnostic fields: how much drag we applied per trade + per day mean.
    drag_per_trade_pct = float(pair_rt_bps / 10_000.0 * per_trade_fraction) * 100.0
    mean_daily_trades = (
        float(counts.reindex(daily_eq.index, fill_value=0).mean())
        if trades else 0.0
    )
    mean_daily_drag_pct = float(drag.mean()) * 100.0

    return {
        "pair_round_trip_bps": float(pair_rt_bps),
        "sharpe_daily_resampled": sharpe,
        "annualized_return": ann,
        "total_return": total,
        "max_drawdown_pct": max_dd,
        "per_trade_fraction_used": float(per_trade_fraction),
        "drag_per_trade_pct": drag_per_trade_pct,
        "mean_daily_trades": mean_daily_trades,
        "mean_daily_drag_pct": mean_daily_drag_pct,
        "n_trades": int(len(trades)),
        "n_days": int(len(daily_eq)),
    }


def breakeven_table(trades: list[dict]) -> dict:
    """Sizing-independent break-even table: net margin vs cost tier.

    Computes mean / median / std of trade ``gross_pct`` and ``pnl_pct``,
    then sweeps cost tiers to show what fraction of trades remain net
    profitable at each tier. This is the most direct verdict evidence
    because it does NOT depend on sizing, equity curve, or compounding.

    If ``gross_pct`` is missing (only ``pnl_pct`` is stamped by the base
    engine for non-SE-H3 strategies), back it out as
    ``gross_pct = pnl_pct + 8bps`` (= engine cost at 1+1 bps/side/leg),
    which is the cost the engine debits in ``mtf_xs_pairs_base_20260718``
    ``_backtest_pair``.

    Returns dict with: per_tier list of {pair_rt_bps, mean_gross_bps,
    mean_net_bps, pct_trades_net_positive, pct_pnl_net_positive}.
    """
    if not trades:
        return {"per_tier": [], "n_trades": 0}

    has_gross = "gross_pct" in trades[0]
    if has_gross:
        gross = np.array([float(t["gross_pct"]) for t in trades]) * 10_000.0  # to bps
        gross_source = "trade.gross_pct"
    else:
        # Engine cost = 2*2*(fee+slip)/10000 = 8 bps at fee=slip=1
        gross = (np.array([float(t["pnl_pct"]) for t in trades]) + 8e-4) * 10_000.0
        gross_source = "pnl_pct + 8bps (back-out)"
    pnl = np.array([float(t["pnl_pct"]) for t in trades]) * 10_000.0

    out = {
        "n_trades": int(len(trades)),
        "gross_source": gross_source,
        "gross_stats_bps": {
            "mean": float(gross.mean()),
            "median": float(np.median(gross)),
            "std": float(gross.std(ddof=1)),
            "min": float(gross.min()),
            "max": float(gross.max()),
        },
        "pnl_stats_bps_at_engine_cost_8bps": {
            "mean": float(pnl.mean()),
            "median": float(np.median(pnl)),
            "std": float(pnl.std(ddof=1)),
            "min": float(pnl.min()),
            "max": float(pnl.max()),
        },
        "per_tier": [],
    }

    for rt in (0.0, 4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 32.0, 40.0, 60.0, 80.0, 120.0):
        # net_bps = gross_bps - rt_bps (cost debited in full pair pct)
        net = gross - rt
        out["per_tier"].append({
            "pair_round_trip_bps": float(rt),
            "mean_gross_bps": float(gross.mean()),
            "mean_net_bps": float(net.mean()),
            "median_net_bps": float(np.median(net)),
            "pct_trades_net_positive": float((net > 0).mean()),
            "pct_pnl_net_positive": float((pnl > (rt - 8.0)).mean()) if rt >= 8.0
                else float((pnl > 0).mean()),
            "engine_cost_included": rt >= 8.0,
            "break_even_tier": "DIE" if net.mean() < 0 else "LIVE",
        })

    # Find the tier where mean_net_bps crosses zero (= break-even).
    tiers = out["per_tier"]
    out["breakeven_pair_rt_bps"] = next(
        (t["pair_round_trip_bps"] for t in tiers if t["mean_net_bps"] < 0),
        None,
    )

    return out


def load_equity_csv(path: Path) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["openTime"])
    df = df.set_index("openTime").sort_index()
    return df["equity"].astype(float)


def load_trades_csv(path: Path) -> list[dict]:
    df = pd.read_csv(path)
    return df.to_dict("records")


FEE_LEVELS = (
    ("inhouse_4bps_rt", 4.0),
    ("freqtrade_24bps_rt", 24.0),
    ("backtrader_60bps_rt", 60.0),
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--equity-csv", required=True, type=Path)
    ap.add_argument("--trades-csv", required=True, type=Path)
    ap.add_argument("--out-json", required=True, type=Path)
    ap.add_argument("--buggy-out-json", type=Path, default=None,
                    help="Optional: also write the BUGGY (per_trade_fraction=0.005) "
                         "fee-shock JSON for direct comparison.")
    ap.add_argument("--per-trade-fraction", type=float, default=1.0,
                    choices=[0.5, 1.0, 2.0],
                    help="Notional basis. 1.0 (default) = full pair pct, matches "
                         "trade log pnl_pct. 0.5 = half-spread (matches bar-return "
                         "normalisation). 2.0 = sanity upper bound.")
    ap.add_argument("--label", default="se_h3",
                    help="Label for the artefact (e.g. se_h3, h1_baseline).")
    args = ap.parse_args()

    equity = load_equity_csv(args.equity_csv)
    trades = load_trades_csv(args.trades_csv)
    print(f"[fix] loaded equity: {len(equity)} daily pts | trades: {len(trades)}",
          file=sys.stderr)

    fixed = {
        label: fee_shock_metrics_fixed(equity, trades, rt,
                                       per_trade_fraction=args.per_trade_fraction)
        for label, rt in FEE_LEVELS
    }

    payload = {
        "label": args.label,
        "source_equity_csv": str(args.equity_csv),
        "source_trades_csv": str(args.trades_csv),
        "per_trade_fraction": args.per_trade_fraction,
        "per_trade_fraction_rationale": (
            "1.0 = full-pair pct basis (default), matches trade log "
            "pnl_pct (cost = 2*2*(fee+slip)/10000 in full pair pct). "
            "0.005 was the bug (200x too small)."
        ),
        "fee_shock": fixed,
        "breakeven_table": breakeven_table(trades),
    }
    args.out_json.write_text(json.dumps(payload, indent=2, default=float))
    print(f"[fix] wrote {args.out_json}", file=sys.stderr)

    if args.buggy_out_json:
        buggy = {
            label: fee_shock_metrics_fixed(equity, trades, rt,
                                           per_trade_fraction=0.005)
            for label, rt in FEE_LEVELS
        }
        args.buggy_out_json.write_text(
            json.dumps(buggy, indent=2, default=float)
        )
        print(f"[fix] wrote buggy baseline {args.buggy_out_json}", file=sys.stderr)

    # Print headline summary so the caller sees the impact immediately.
    print("\n=== fee_shock_fix headline ===", file=sys.stderr)
    print(f"  per_trade_fraction = {args.per_trade_fraction}", file=sys.stderr)
    for label, rt in FEE_LEVELS:
        f = fixed[label]
        print(f"  {label:<22} (pair_rt_bps={rt:5.1f}) "
              f"sharpe={f['sharpe_daily_resampled']:+.3f} "
              f"ann={f['annualized_return']*100:+8.2f}% "
              f"total={f['total_return']*100:+10.2f}% "
              f"MDD={f['max_drawdown_pct']*100:+6.2f}% "
              f"drag/trade={f['drag_per_trade_pct']:.3f}bps "
              f"daily_trades={f['mean_daily_trades']:.2f} "
              f"daily_drag={f['mean_daily_drag_pct']:.3f}bps",
              file=sys.stderr)

    be = payload["breakeven_table"]
    print(f"\n  gross stats: mean={be['gross_stats_bps']['mean']:.3f}bps "
          f"median={be['gross_stats_bps']['median']:.3f}bps "
          f"std={be['gross_stats_bps']['std']:.3f}bps",
          file=sys.stderr)
    print(f"  net@engine-8bps: mean={be['pnl_stats_bps_at_engine_cost_8bps']['mean']:.3f}bps",
          file=sys.stderr)
    print(f"  break-even pair_rt_bps: {be['breakeven_pair_rt_bps']}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())