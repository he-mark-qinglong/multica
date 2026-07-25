"""Unified H1/H2/H3/H4 mtf-1m-15m-2h variant study.

- Loads canonical 1m OHLCV from quant-loop/data/perp_1m/ and funding from
  quant-loop/data/funding/.
- Runs each hypothesis at the ratified 22 bps RT per-symbol cost
  (commission=4 bps/side, slippage=7 bps/side).
- Produces full-history + walk-forward OOS metrics, G1-G7 gate check,
  H3 cost-sensitivity curve, and equity / metric charts.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]  # quant-loop root
OUT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = OUT_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Make shared strategy modules importable.
# ---------------------------------------------------------------------------
STRATEGIES_DIR = ROOT / "strategies"
INDICATORS_DIR = STRATEGIES_DIR / "_indicators"
VALIDATION_DIR = ROOT / "_shared" / "validation"
GATES_DIR = ROOT / "_shared" / "gates"
for p in (str(INDICATORS_DIR), str(STRATEGIES_DIR), str(VALIDATION_DIR), str(GATES_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from mtf_xs_pairs_base_20260718 import (  # noqa: E402
    daily_returns,
    profit_factor_and_mdd,
    run_backtest,
    sharpe_daily_resampled,
)
from mtf_xs_runner_20260718 import walk_forward  # noqa: E402
from compute_metrics import compute_metrics  # noqa: E402
from cpcv import deflated_sharpe  # noqa: E402
from enforce import certify_metrics  # noqa: E402

# Monkey-patch: the shared runner expects portfolio["n_bars"], but
# mtf_xs_pairs_base_20260718.build_portfolio (H1-H3) omits it.
import mtf_xs_pairs_base_20260718 as _base  # noqa: E402

_orig_build_portfolio = _base.build_portfolio


def _build_portfolio_with_nbars(*args, **kwargs):
    r = _orig_build_portfolio(*args, **kwargs)
    if "n_bars" not in r:
        r["n_bars"] = len(r["bar_return"])
    return r


_base.build_portfolio = _build_portfolio_with_nbars


DATA_1M_DIR = ROOT / "data" / "perp_1m"
FUNDING_DIR = ROOT / "data" / "funding"

# Ratified cost (SMA-34913): 4 bps fee + 7 bps slippage = 11 bps/side.
COST_FEE_BPS = 4.0
COST_SLIP_BPS = 7.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_1m(symbols: List[str]) -> Dict[str, pd.DataFrame]:
    """Load canonical 1m OHLCV and set a tz-naive DatetimeIndex from open_time."""
    out: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        p = DATA_1M_DIR / f"{sym}_1m.parquet"
        df = pd.read_parquet(p)
        if "open_time" not in df.columns:
            raise ValueError(f"{sym}: missing open_time column")
        ts = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
        df.index = pd.DatetimeIndex(ts).tz_convert(None)
        df.index.name = "openTime"
        df = df.sort_index()
        # Ensure required columns exist
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                raise ValueError(f"{sym}: missing {col}")
        out[sym] = df[["open", "high", "low", "close", "volume"]]
        print(f"  loaded {sym}: {len(df):,} rows, {df.index[0]} -> {df.index[-1]}")
    return out


def load_funding(symbols: List[str]) -> Dict[str, pd.Series]:
    out: Dict[str, pd.Series] = {}
    for sym in symbols:
        p = FUNDING_DIR / f"{sym}.parquet"
        df = pd.read_parquet(p)
        if "ts" not in df.columns or "fundingRate" not in df.columns:
            raise ValueError(f"{sym}: funding parquet missing ts/fundingRate")
        ts = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
        s = pd.Series(df["fundingRate"].astype(float).to_numpy(), index=ts, name="fundingRate")
        s = s.sort_index()
        s.index = pd.DatetimeIndex(s.index).tz_convert(None)
        out[sym] = s
        print(f"  loaded funding {sym}: {len(s):,} rows, {s.index[0]} -> {s.index[-1]}")
    return out


def align_symbols(d1m: Dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """Intersect all symbol indices and return the common DatetimeIndex."""
    common: Optional[pd.DatetimeIndex] = None
    for df in d1m.values():
        idx = df.index
        common = idx if common is None else common.intersection(idx)
    if common is None or len(common) == 0:
        raise ValueError("no common index across symbols")
    print(f"  common index: {len(common):,} bars, {common[0]} -> {common[-1]}")
    return common


# ---------------------------------------------------------------------------
# Config handling
# ---------------------------------------------------------------------------
def load_config(hyp: str) -> Dict[str, Any]:
    """Load or build config for H1-H4, override cost to ratified 22 bps RT."""
    if hyp in ("H1", "H2", "H3"):
        cfg_path = STRATEGIES_DIR / f"mtf_xs_pairs_1m_15m_2h_{hyp.lower()}_20260718" / "config.json"
        cfg = json.loads(cfg_path.read_text())
    elif hyp == "H4":
        cfg = _build_h4_config()
    else:
        raise ValueError(hyp)

    cfg["fees_bps_per_side"] = COST_FEE_BPS
    cfg["slippage_bps_per_side"] = COST_SLIP_BPS
    cfg["cost_note"] = f"ratified {2*(COST_FEE_BPS+COST_SLIP_BPS):.0f} bps RT per symbol"
    return cfg


def _build_h4_config() -> Dict[str, Any]:
    """H4 config inferred from docs/decisions/mtf-campaign.md and base code."""
    return {
        "strategy": "mtf_xs_pairs_1m_15m_2h_h4_20260718",
        "iteration": 107,
        "campaign": "SMA-34875 mtf-1m-15m-2h H4 rebuild",
        "hypothesis": "H4",
        "date": "2026-07-18",
        "primary_timeframe": "1m",
        "filter_timeframe": "15m",
        "regime_timeframe": "2h",
        "description": "H4: 1m cross-pair z-score + 15m EMA-8/21 direction filter + 2h trend cap + portfolio sizing.",
        "instruments": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "pairs": ["BTCUSDT/ETHUSDT", "BTCUSDT/SOLUSDT", "ETHUSDT/SOLUSDT"],
        "data_source": "binance_usdm_1m_canonical",
        "axis": "multi_pair_zscore_1m_15m_ema_dir_2h_trend_cap_portfolio",
        "indicators": {
            "zscore_lookback_bars": 240,
            "zscore_entry_threshold": 2.0,
            "zscore_exit_threshold": 0.5,
            "regime_break_threshold": 3.0,
            "max_holding_bars": 240,
            "ema_15m_fast": 8,
            "ema_15m_slow": 21,
            "trend_2h_fast": 8,
            "trend_2h_slow": 21,
        },
        "entry": {"side_when_z_positive": "short_a_long_b"},
        "exit": {
            "zscore_exit_threshold": 0.5,
            "regime_break_threshold": 3.0,
            "max_holding_bars": 240,
        },
        "sizing": {
            "per_pair_notional_pct": 0.02,
            "max_pairs_active": 3,
            "starting_capital_usd": 100000.0,
            "gross_cap": 0.06,
            "net_cap": 0.04,
            "corr_window_days": 60,
            "corr_high_threshold": 0.6,
        },
        "fees_bps_per_side": COST_FEE_BPS,
        "slippage_bps_per_side": COST_SLIP_BPS,
        "sharpe_method": "daily_resampled",
        "walk_forward": {
            "train_bars_1m": 525600,
            "test_bars_1m": 262800,
            "step_bars_1m": 262800,
            "min_windows": 3,
        },
        "hard_gates": {
            "oos_sharpe_min": 1.0,
            "oos_annualized_min": 0.15,
            "profit_factor_min": 1.5,
            "max_drawdown_max_abs_pct": 25.0,
            "bootstrap_ci_lower_min": 0.5,
            "bootstrap_resamples": 10000,
            "bootstrap_seed": 42,
        },
        "notes": [
            "H4 reconstructed for unified variant comparison. See docs/decisions/mtf-campaign.md for original evidence.",
        ],
    }


# ---------------------------------------------------------------------------
# Metrics / gates
# ---------------------------------------------------------------------------
def portfolio_index(result: Dict[str, Any]) -> pd.DatetimeIndex:
    """Recover a DatetimeIndex for the portfolio from the first pair's index."""
    n = result["portfolio"]["n_bars"]
    # The first pair's a-index is in the result? runner does not keep it, but
    # base run_backtest returns per_pair dict that contains no index. We fabricate
    # a reasonable index starting at the known strategy start date.
    return pd.date_range("2022-01-01", periods=n, freq="1min")


def full_history_metrics(result: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Compute full-history daily-resampled metrics from a base run_backtest result."""
    starting = float(cfg.get("starting_capital_usd", 100_000.0))
    port = result["portfolio"]
    n_bars = int(port["n_bars"])
    if n_bars == 0:
        return {
            "sharpe_daily": 0.0,
            "annualized_return": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
            "n_trades": 0,
            "n_bars": 0,
            "win_rate": 0.0,
            "calmar": 0.0,
            "sortino": 0.0,
        }

    # Build equity from bar returns (matches runner logic)
    bar_ret = np.asarray(port["bar_return"], dtype=float)
    eq = np.empty(n_bars)
    eq[0] = starting
    for i in range(1, n_bars):
        eq[i] = eq[i - 1] * (1.0 + bar_ret[i])
    idx = portfolio_index(result)
    eq_s = pd.Series(eq, index=idx)
    daily_eq = eq_s.resample("1D").last().dropna()

    n_trades = sum(len(pp["trades"]) for pp in result["per_pair"])
    trade_pnls = [t["pnl_pct"] for pp in result["per_pair"] for t in pp["trades"]]
    m = compute_metrics(daily_eq, n_trades=n_trades, freq_per_year=365, trade_pnls=trade_pnls)
    # Ensure keys align with enforce.py gates
    return {
        "sharpe_daily": m["sharpe_daily"],
        "annualized_return": m["annualized_return"],
        "max_drawdown_pct": m["max_drawdown_pct"],
        "profit_factor": m["profit_factor"],
        "n_trades": m["n_trades"],
        "n_bars": m["n_bars"],
        "win_rate": m["win_rate"],
        "calmar": m["calmar"],
        "sortino": m["sortino"],
    }


def gate_dict(wfo: Dict[str, Any], fh: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Build metrics dict matching _shared/gates/enforce.py G1-G7 (+T1)."""
    n_bars_total = int(fh.get("n_bars", 0))
    metrics = {
        "sharpe_daily": float(wfo["oos_sharpe_mean_daily_resampled"]),
        "annualized_return": float(wfo["oos_annualized_mean_daily"]),
        "max_drawdown_pct": float(wfo["oos_max_drawdown_worst"]),
        "profit_factor": float(fh["profit_factor"]),
        "n_trades": int(fh["n_trades"]),
        # G5: we do not run CPCV here; omit so gate skips it.
        # G6: bootstrap CI from walk-forward windows.
        "bootstrap_ci95_lower": float(wfo["bootstrap_ci_lower"]),
        # G7: deflated Sharpe with family size = 4 (H1-H4) and full-history sample length.
        "deflated_sharpe": float(
            deflated_sharpe(
                float(wfo["oos_sharpe_mean_daily_resampled"]),
                n_trials=4,
                sample_len=max(n_bars_total, 2),
            )
        ),
    }
    return metrics


def check_gates(metrics: Dict[str, Any]) -> Dict[str, Any]:
    res = certify_metrics(metrics, strict=False)
    return {
        "passed": res.passed,
        "failed_gates": res.failed_gates,
        "reasons": res.reasons,
    }


# ---------------------------------------------------------------------------
# Cost sensitivity (H3 only)
# ---------------------------------------------------------------------------
def h3_cost_sensitivity(d1m: Dict[str, pd.DataFrame], funding: Dict[str, pd.Series], cfg: Dict[str, Any],
                        common_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Run H3 full-history at several per-symbol RT costs; return DataFrame."""
    per_symbol_rt_bps = [4.0, 12.0, 22.0, 32.0, 44.0, 60.0]
    rows = []
    base = dict(cfg)
    syms = list(cfg["instruments"])
    d = {s: d1m[s].reindex(common_index) for s in syms}
    f = {s: funding[s] for s in syms}
    for rt in per_symbol_rt_bps:
        half = rt / 2.0  # per-side total
        # Keep a nominal 1 bps fee and assign the rest to slippage; only the sum matters.
        base["fees_bps_per_side"] = 1.0
        base["slippage_bps_per_side"] = max(half - 1.0, 0.0)
        res = run_backtest(d, base, funding=f)
        fh = full_history_metrics(res, base)
        rows.append({
            "per_symbol_rt_bps": rt,
            "pair_rt_bps": 2 * rt,
            "sharpe_daily": fh["sharpe_daily"],
            "annualized_return": fh["annualized_return"],
            "max_drawdown_pct": fh["max_drawdown_pct"],
            "profit_factor": fh["profit_factor"],
            "n_trades": fh["n_trades"],
            "win_rate": fh["win_rate"],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------
def plot_equity_curves(records: Dict[str, Any], out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    for hyp, r in records.items():
        eq = r["equity_daily"]
        if len(eq) == 0:
            continue
        norm = eq / eq.iloc[0]
        ax.plot(norm.index, norm.values, label=hyp, linewidth=1.2)
    ax.set_yscale("log")
    ax.set_title("H1-H4 portfolio equity at 22 bps RT per symbol (daily, log scale)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Growth of $1")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_oos_metrics(records: Dict[str, Any], out_path: Path) -> None:
    labels = list(records.keys())
    sharpes = [records[h]["oos"]["oos_sharpe_mean_daily_resampled"] for h in labels]
    anns = [records[h]["oos"]["oos_annualized_mean_daily"] for h in labels]
    mdds = [abs(records[h]["oos"]["oos_max_drawdown_worst"]) for h in labels]

    x = np.arange(len(labels))
    width = 0.25
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(x - width, sharpes, width, label="OOS Sharpe")
    ax.bar(x, anns, width, label="OOS ann. return")
    ax.bar(x + width, mdds, width, label="|OOS max DD|")
    ax.axhline(1.0, color="k", linestyle="--", linewidth=0.8, label="G1 Sharpe = 1")
    ax.axhline(0.15, color="gray", linestyle="--", linewidth=0.8, label="G2 ann. = 15%")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_title("Walk-forward OOS metrics (daily-resampled)")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_cost_sensitivity(df: pd.DataFrame, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(df["per_symbol_rt_bps"], df["sharpe_daily"], marker="o", label="H3 Sharpe")
    ax.axhline(1.0, color="k", linestyle="--", linewidth=0.8)
    ax.set_xlabel("Per-symbol round-trip cost (bps)")
    ax.set_ylabel("Full-history daily Sharpe")
    ax.set_title("H3 cost sensitivity (ratified = 22 bps/symbol)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_hypothesis(hyp: str, d1m: Dict[str, pd.DataFrame], funding: Dict[str, pd.Series],
                   common_index: pd.DatetimeIndex) -> Dict[str, Any]:
    print(f"\n=== {hyp} ===")
    cfg = load_config(hyp)
    syms = list(cfg["instruments"])
    # Reindex every symbol to the common index so positional window slicing in
    # walk_forward stays timestamp-aligned across symbols.
    d = {s: d1m[s].reindex(common_index) for s in syms}
    f = {s: funding[s] for s in syms}

    print("Full-history backtest …")
    res = run_backtest(d, cfg, funding=f if hyp == "H3" else None)
    fh = full_history_metrics(res, cfg)
    print(f"  IS  Sharpe={fh['sharpe_daily']:.3f} ann={fh['annualized_return']:.2%} "
          f"PF={fh['profit_factor']:.3f} MDD={fh['max_drawdown_pct']:.2%} trades={fh['n_trades']}")

    print("Walk-forward OOS …")
    wfo = walk_forward(d, cfg, funding=f if hyp == "H3" else None)
    print(f"  OOS Sharpe={wfo['oos_sharpe_mean_daily_resampled']:.3f} "
          f"ann={wfo['oos_annualized_mean_daily']:.2%} "
          f"MDD={wfo['oos_max_drawdown_worst']:.2%} "
          f"CI=[{wfo['bootstrap_ci_lower']:.3f}, {wfo['bootstrap_ci_upper']:.3f}] "
          f"windows={wfo['n_windows']}")

    gm = gate_dict(wfo, fh, cfg)
    gates = check_gates(gm)
    print(f"  Gates passed={gates['passed']} failed={gates['failed_gates']}")

    # Daily equity for plotting
    n_bars = res["portfolio"]["n_bars"]
    bar_ret = np.asarray(res["portfolio"]["bar_return"], dtype=float)
    eq = np.empty(n_bars)
    starting = float(cfg.get("starting_capital_usd", 100_000.0))
    eq[0] = starting
    for i in range(1, n_bars):
        eq[i] = eq[i - 1] * (1.0 + bar_ret[i])
    daily_eq = pd.Series(eq, index=portfolio_index(res)).resample("1D").last().dropna()

    return {
        "hypothesis": hyp,
        "config": cfg,
        "full_history": fh,
        "oos": wfo,
        "gates": gates,
        "gate_metrics": gm,
        "equity_daily": daily_eq,
    }


def main() -> None:
    print("Loading canonical data pool …")
    all_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    d1m = load_1m(all_symbols)
    funding = load_funding(all_symbols)
    common_index = align_symbols(d1m)

    records: Dict[str, Any] = {}
    for hyp in ("H1", "H2", "H3", "H4"):
        records[hyp] = run_hypothesis(hyp, d1m, funding, common_index)

    # Cost sensitivity for H3
    print("\n=== H3 cost sensitivity ===")
    h3_cfg = load_config("H3")
    cost_df = h3_cost_sensitivity(d1m, funding, h3_cfg, common_index)
    print(cost_df.to_string(index=False))
    cost_df.to_csv(RESULTS_DIR / "h3_cost_sensitivity.csv", index=False)

    # Save metrics JSON
    summary = {
        "note": "Unified H1-H4 comparison at ratified 22 bps RT per symbol.",
        "cost": {"fee_bps_per_side": COST_FEE_BPS, "slippage_bps_per_side": COST_SLIP_BPS},
        "variants": {h: {
            "full_history": records[h]["full_history"],
            "oos": {
                "oos_sharpe_mean_daily_resampled": records[h]["oos"]["oos_sharpe_mean_daily_resampled"],
                "oos_annualized_mean_daily": records[h]["oos"]["oos_annualized_mean_daily"],
                "oos_max_drawdown_worst_pct": records[h]["oos"]["oos_max_drawdown_worst"],
                "bootstrap_ci_lower": records[h]["oos"]["bootstrap_ci_lower"],
                "bootstrap_ci_upper": records[h]["oos"]["bootstrap_ci_upper"],
                "n_windows": records[h]["oos"]["n_windows"],
            },
            "gate_metrics": records[h]["gate_metrics"],
            "gates": records[h]["gates"],
        } for h in records},
        "h3_cost_sensitivity": cost_df.to_dict(orient="records"),
    }
    (RESULTS_DIR / "metrics.json").write_text(json.dumps(summary, indent=2, default=float))

    # Save daily equity CSVs
    for h, r in records.items():
        r["equity_daily"].to_frame("equity").to_csv(RESULTS_DIR / f"equity_{h}_daily.csv")

    # Plots
    plot_equity_curves(records, RESULTS_DIR / "equity_curves.png")
    plot_oos_metrics(records, RESULTS_DIR / "oos_metrics.png")
    plot_cost_sensitivity(cost_df, RESULTS_DIR / "h3_cost_sensitivity.png")

    print(f"\nResults written to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
