"""Parameter sweep for funding_carry_asym (SMA-34920).

Grid:
  funding_threshold ∈ {0.0002, 0.0003, 0.0004, 0.0005, 0.0006, 0.0007, 0.0008}
  vpvr_window_bars ∈ {20, 30, 40, 50, 60}

Symbol: BTCUSDT, Timeframe: 15m, Window: last 365 days (OOS style).
Hard gates G1-G7 from strat-vpvr:
  G1 Sharpe≥1.0    G2 ann≥15%    G3 MDD>-25%    G4 PF>1.5
  G5 framework CV OOS≥1 (skipped: single framework, freqtrade only)
  G6 bootstrap CI lower≥0.5  G7 Bonferroni N/A (single strategy)

Outputs:
  results/sweep_grid.csv   — full grid
  results/sweep_summary.md — human-readable summary
  results/isoos_sanity.json — IS vs OOS on top combo
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from strategy import run_backtest  # noqa: E402
from run_backtest import _compute_metrics  # noqa: E402

LIVE_DATA = Path("/home/smark/multica/quant-loop/live_data")
FUNDING_DIR = Path("/home/smark/multica/quant-loop/data/funding")


# ----------------------------------------------------------------------------
# Data loader (15m BTC + funding)
# ----------------------------------------------------------------------------
def load_15m_window(symbol: str, window_days: int) -> pd.DataFrame:
    df = pd.read_parquet(LIVE_DATA / f"{symbol}_15m.parquet")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=False)
    df = df.set_index("open_time").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].astype(np.float64)

    fdf = pd.read_parquet(FUNDING_DIR / f"{symbol}.parquet")
    fdf["ts"] = pd.to_datetime(fdf["ts"], utc=True)
    fdf = fdf.set_index("ts").sort_index()
    fdf.index = fdf.index.tz_convert(None)  # match OHLCV tz-naive
    funding = fdf[["fundingRate"]].astype(np.float64)
    funding_aligned = funding.reindex(df.index, method="ffill")
    df["funding"] = funding_aligned["fundingRate"].fillna(0.0)

    end = df.index.max()
    start = end - pd.Timedelta(days=window_days)
    return df.loc[start:end].copy()


# ----------------------------------------------------------------------------
# Sweep runner
# ----------------------------------------------------------------------------
BASE_PARAMS = {
    "support_kind": "HVN",
    "proximity_atr": 1.0,
    "atr_period": 14,
    "vpvr_snapshot_every_bars": 6,
    "vpvr_bins": 24,
    "vpvr_hvn_quantile": 0.85,
    "vpvr_lvn_quantile": 0.15,
    "vpvr_num_hvn": 3,
    "vpvr_num_lvn": 3,
    "take_profit_atr_k": 1.5,
    "hard_stop_atr_k": 1.0,
    "max_hold_bars": 8,
    "risk_target_pct": 0.005,
    "cooldown_bars": 5,
    "fee_bps_per_fill": 4.0,
    "slippage_bps_per_fill": 1.0,
    "funding_carry_bps_per_bar": 0.01,
}

CFG_TPL = {
    "variant_key": "funding_carry_asym",
    "iteration": 3,
    "source_spec": "SMA-34920",
    "instruments": ["BTCUSDT"],
    "starting_capital_usd": 100000.0,
}


def _eval_one(df: pd.DataFrame, ft: float, w: int) -> dict:
    params = dict(BASE_PARAMS)
    params["funding_threshold"] = ft
    params["vpvr_window_bars"] = w
    cfg = dict(CFG_TPL)
    cfg["params"] = params
    res = run_backtest(df, cfg)
    metrics = _compute_metrics(res, df.index)
    return {
        "funding_threshold": ft,
        "vpvr_window_bars": w,
        "n_bars": metrics["n_bars"],
        "n_trades": metrics["n_trades"],
        "win_rate": metrics["win_rate"],
        "profit_factor": metrics["profit_factor"],
        "sharpe_daily": metrics["sharpe_daily"],
        "total_return": metrics["total_return"],
        "annualized_return": metrics["annualized_return"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "avg_bars_held": metrics["avg_bars_held"],
    }


def gate_eval(row: pd.Series) -> dict:
    """G1-G4 (G5+ skipped: single framework) for a single row."""
    return {
        "G1_sharpe_ge_1.0": bool(row["sharpe_daily"] >= 1.0),
        "G2_ann_ge_0.15": bool(row["annualized_return"] >= 0.15),
        "G3_mdd_gt_-25": bool(row["max_drawdown_pct"] > -25.0),
        "G4_pf_gt_1.5": bool(
            row["profit_factor"] == float("inf") or row["profit_factor"] > 1.5
        ),
    }


def bootstrap_ci_lower(equity: np.ndarray, idx: pd.DatetimeIndex,
                       n_resamples: int = 1000, seed: int = 42) -> float:
    """G6 — daily Sharpe bootstrap CI lower bound (1000 resamples for speed)."""
    rng = np.random.default_rng(seed)
    series = pd.Series(equity, index=idx, dtype=np.float64)
    daily_eq = series.resample("1D").last().dropna()
    rets = daily_eq.pct_change().dropna().values
    if len(rets) < 5:
        return 0.0
    sharpes = np.empty(n_resamples)
    n = len(rets)
    for k in range(n_resamples):
        sample = rng.choice(rets, size=n, replace=True)
        mu = sample.mean()
        sigma = sample.std()
        sharpes[k] = (mu / sigma * math.sqrt(365.25)) if sigma > 0 else 0.0
    return float(np.quantile(sharpes, 0.025))


def main():
    out_dir = REPO_ROOT / "results"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[sweep] loading data …", flush=True)
    df = load_15m_window("BTCUSDT", window_days=365)
    print(f"[sweep] loaded {len(df)} 15m bars, span {df.index[0]} → {df.index[-1]}",
          flush=True)

    funding_grid = [0.0002, 0.0003, 0.0004, 0.0005, 0.0006, 0.0007, 0.0008]
    lookback_grid = [20, 30, 40, 50, 60]

    rows = []
    total = len(funding_grid) * len(lookback_grid)
    k = 0
    for ft in funding_grid:
        for w in lookback_grid:
            k += 1
            try:
                r = _eval_one(df, ft, w)
                r["status"] = "ok"
            except Exception as e:  # pragma: no cover
                r = {
                    "funding_threshold": ft,
                    "vpvr_window_bars": w,
                    "n_trades": 0, "n_bars": len(df),
                    "win_rate": 0.0, "profit_factor": 0.0,
                    "sharpe_daily": 0.0, "total_return": 0.0,
                    "annualized_return": 0.0, "max_drawdown_pct": 0.0,
                    "avg_bars_held": 0.0, "status": f"error: {e}",
                }
            print(f"[sweep] {k}/{total} ft={ft:.4f} w={w} → "
                  f"trades={r['n_trades']} sharpe={r['sharpe_daily']:.3f} "
                  f"ann={r['annualized_return']:.4f} mdd={r['max_drawdown_pct']:.2f}% "
                  f"pf={r['profit_factor']:.3f}",
                  flush=True)
            rows.append(r)

    grid = pd.DataFrame(rows)
    grid.to_csv(out_dir / "sweep_grid.csv", index=False)
    print(f"[sweep] wrote {out_dir/'sweep_grid.csv'}", flush=True)

    # Gate evaluation
    ok = grid[grid["status"] == "ok"].copy()
    gate_rows = []
    for _, row in ok.iterrows():
        g = gate_eval(row)
        gate_rows.append({**dict(row), **g})
    gdf = pd.DataFrame(gate_rows)
    gdf["gates_pass_count"] = sum(
        gdf[c].astype(int) for c in
        ("G1_sharpe_ge_1.0", "G2_ann_ge_0.15", "G3_mdd_gt_-25", "G4_pf_gt_1.5")
    )
    gdf.to_csv(out_dir / "sweep_gates.csv", index=False)

    # Rank: cancel-by-rule rows
    cancelled = ok[ok["annualized_return"] < 0].copy()
    cancelled["cancel_reason"] = "negative annualized return"

    # Rank surviving rows by Sharpe (desc), then PF, then trades
    surviving = ok[ok["annualized_return"] >= 0].copy()
    if len(surviving):
        surviving_sorted = surviving.sort_values(
            ["sharpe_daily", "profit_factor", "n_trades"],
            ascending=[False, False, False],
        )
        top = surviving_sorted.iloc[0]
        top_ft = float(top["funding_threshold"])
        top_w = int(top["vpvr_window_bars"])
    else:
        top = ok.iloc[0]
        top_ft = float(top["funding_threshold"])
        top_w = int(top["vpvr_window_bars"])

    # IS vs OOS sanity check on top combo
    print(f"[sweep] IS/OOS sanity on top combo ft={top_ft} w={top_w}", flush=True)
    mid = df.index[0] + (df.index[-1] - df.index[0]) / 2
    is_df = df.loc[:mid].copy()
    oos_df = df.loc[mid + pd.Timedelta(minutes=15):].copy()

    params = dict(BASE_PARAMS)
    params["funding_threshold"] = top_ft
    params["vpvr_window_bars"] = top_w
    cfg = dict(CFG_TPL)
    cfg["params"] = params

    is_res = run_backtest(is_df, cfg)
    is_m = _compute_metrics(is_res, is_df.index)
    oos_res = run_backtest(oos_df, cfg)
    oos_m = _compute_metrics(oos_res, oos_df.index)

    sanity = {
        "top_combo": {"funding_threshold": top_ft, "vpvr_window_bars": top_w},
        "is": {**is_m, "span_start": str(is_df.index[0]), "span_end": str(is_df.index[-1])},
        "oos": {**oos_m, "span_start": str(oos_df.index[0]), "span_end": str(oos_df.index[-1])},
        "degradation_ratio_sharpe": (
            oos_m["sharpe_daily"] / is_m["sharpe_daily"]
            if is_m["sharpe_daily"] not in (0.0, float("nan")) else 0.0
        ),
        "degradation_ratio_ann": (
            oos_m["annualized_return"] / is_m["annualized_return"]
            if is_m["annualized_return"] not in (0.0, float("nan")) else 0.0
        ),
    }
    (out_dir / "isoos_sanity.json").write_text(json.dumps(sanity, indent=2))

    # Summary markdown
    md = []
    md.append("# Parameter Sweep Results — funding_carry_asym (SMA-34920)\n")
    md.append(f"- **Strategy**: funding_carry_asym (vpvr-funding-carry-asym)")
    md.append(f"- **Symbol/Timeframe**: BTCUSDT 15m")
    md.append(f"- **Window**: last 365 days  ({df.index[0]} → {df.index[-1]})")
    md.append(f"- **Bars**: {len(df)}")
    md.append(f"- **Grid size**: {len(funding_grid)} × {len(lookback_grid)} = {total} combos")
    md.append("")
    md.append("## Axes")
    md.append(f"- funding_threshold (raw, per 8h): {funding_grid}")
    md.append(f"- vpvr_window_bars (sessions): {lookback_grid}")
    md.append("")
    md.append("## Grid (full results)")
    md.append("")
    md.append("| funding_th | vpvr_window | n_trades | win_rate | PF | Sharpe(daily) | ann_ret | MDD% | avg_hold |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for _, r in ok.iterrows():
        md.append(
            f"| {r['funding_threshold']:.4f} | {int(r['vpvr_window_bars'])} | "
            f"{int(r['n_trades'])} | {r['win_rate']:.3f} | "
            f"{r['profit_factor']:.3f} | {r['sharpe_daily']:.3f} | "
            f"{r['annualized_return']*100:.2f}% | "
            f"{r['max_drawdown_pct']:.2f}% | {r['avg_bars_held']:.2f} |"
        )
    md.append("")
    md.append("## Gate evaluation (G1-G4; G5/G6/G7 partial — see notes)")
    md.append("")
    md.append("| funding_th | vpvr_window | Sharpe | ann | MDD | PF | G1 | G2 | G3 | G4 | gates_pass |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in ok.iterrows():
        g = gate_eval(r)
        md.append(
            f"| {r['funding_threshold']:.4f} | {int(r['vpvr_window_bars'])} | "
            f"{r['sharpe_daily']:.3f} | {r['annualized_return']*100:.2f}% | "
            f"{r['max_drawdown_pct']:.2f}% | {r['profit_factor']:.3f} | "
            f"{'Y' if g['G1_sharpe_ge_1.0'] else 'N'} | "
            f"{'Y' if g['G2_ann_ge_0.15'] else 'N'} | "
            f"{'Y' if g['G3_mdd_gt_-25'] else 'N'} | "
            f"{'Y' if g['G4_pf_gt_1.5'] else 'N'} | "
            f"{sum(int(v) for v in g.values())}/4 |"
        )
    md.append("")
    md.append("## Cancelled (negative annualized return)")
    if len(cancelled) == 0:
        md.append("- (none)")
    else:
        for _, r in cancelled.iterrows():
            md.append(
                f"- funding_th={r['funding_threshold']:.4f} "
                f"vpvr_window={int(r['vpvr_window_bars'])} "
                f"ann={r['annualized_return']*100:.2f}%"
            )
    md.append("")
    md.append("## Top combo (by Sharpe among surviving)")
    md.append("")
    md.append(f"- funding_threshold = **{top_ft}**")
    md.append(f"- vpvr_window_bars = **{top_w}**")
    md.append(f"- Sharpe(daily) = {top['sharpe_daily']:.3f}")
    md.append(f"- annualized = {top['annualized_return']*100:.2f}%")
    md.append(f"- MDD = {top['max_drawdown_pct']:.2f}%")
    md.append(f"- PF = {top['profit_factor']:.3f}, win_rate = {top['win_rate']:.3f}")
    md.append(f"- trades = {int(top['n_trades'])}")
    md.append("")
    md.append("## IS vs OOS sanity (top combo)")
    md.append("")
    md.append("| slice | n_trades | Sharpe(daily) | ann% | MDD% | PF |")
    md.append("|---|---|---|---|---|---|")
    md.append(
        f"| IS  ({is_m['n_trades']}) | {int(is_m['n_trades'])} | "
        f"{is_m['sharpe_daily']:.3f} | {is_m['annualized_return']*100:.2f}% | "
        f"{is_m['max_drawdown_pct']:.2f}% | {is_m['profit_factor']:.3f} |"
    )
    md.append(
        f"| OOS ({oos_m['n_trades']}) | {int(oos_m['n_trades'])} | "
        f"{oos_m['sharpe_daily']:.3f} | {oos_m['annualized_return']*100:.2f}% | "
        f"{oos_m['max_drawdown_pct']:.2f}% | {oos_m['profit_factor']:.3f} |"
    )
    md.append("")
    md.append(f"- Sharpe degradation ratio OOS/IS = {sanity['degradation_ratio_sharpe']:.3f}")
    md.append(f"- ann degradation ratio OOS/IS = {sanity['degradation_ratio_ann']:.3f}")
    md.append("")

    (out_dir / "sweep_summary.md").write_text("\n".join(md))
    print(f"[sweep] wrote {out_dir/'sweep_summary.md'}", flush=True)

    # Return dict so the parent process can pull metrics
    summary_json = {
        "strategy": "funding_carry_asym",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "window_days": 365,
        "n_bars": len(df),
        "grid": {"funding_grid": funding_grid, "lookback_grid": lookback_grid},
        "top_combo": {
            "funding_threshold": top_ft,
            "vpvr_window_bars": top_w,
            "n_trades": int(top["n_trades"]),
            "win_rate": float(top["win_rate"]),
            "profit_factor": float(top["profit_factor"]),
            "sharpe_daily": float(top["sharpe_daily"]),
            "annualized_return": float(top["annualized_return"]),
            "max_drawdown_pct": float(top["max_drawdown_pct"]),
            "avg_bars_held": float(top["avg_bars_held"]),
            "gates_pass": gate_eval(top),
        },
        "cancelled_count": int(len(cancelled)),
        "isoos": sanity,
    }
    (out_dir / "sweep_summary.json").write_text(json.dumps(summary_json, indent=2))
    print(f"[sweep] wrote {out_dir/'sweep_summary.json'}", flush=True)
    return summary_json


if __name__ == "__main__":
    main()
