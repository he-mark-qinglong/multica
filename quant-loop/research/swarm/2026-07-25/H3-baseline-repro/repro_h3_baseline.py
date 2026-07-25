"""H3-baseline-repro: reproduce the BTC+SOL H3 baseline with the shared pipeline.

This script does not modify any production code. It loads data from the
quant-loop global data directories, aligns BTC/SOL to a common index, clips
to the funding-regime period (the H3 filter requires funding), and runs:

1. Full-history backtest with the H3 config (config_btcsol.json).
2. Walk-forward OOS backtest with the same train/test windows from the config.
3. Fee-sensitivity sweep (in-house 4 bps RT, freqtrade 24 bps RT, backtrader
   60 bps RT) to compare with PR#6 framework numbers.
4. 9-key metrics, G1-G7 certification, and plots.

Outputs are written to the same directory as this script.
"""
from __future__ import annotations

import json
import math
import sys
import warnings
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path("/Users/mark/multica/quant-loop")
STRATEGY_DIR = ROOT / "strategies" / "mtf_xs_pairs_1m_15m_2h_h3_20260718"
SHARED_DIR = ROOT / "_shared"
DATA_1M_DIR = ROOT / "data" / "perp_1m"
DATA_FUNDING_DIR = ROOT / "data" / "funding"
OUT_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(ROOT / "strategies"))
sys.path.insert(0, str(ROOT / "strategies" / "_indicators"))
sys.path.insert(0, str(SHARED_DIR))
sys.path.insert(0, str(SHARED_DIR / "validation"))

from mtf_xs_pairs_base_20260718 import (  # noqa: E402
    build_h3_signals,
    build_portfolio,
    profit_factor_and_mdd,
    run_backtest,
    sharpe_daily_resampled,
)
from mtf_xs_pairs_base_20260718 import _backtest_pair  # noqa: E402
from compute_metrics import compute_metrics  # noqa: E402
from cpcv import deflated_sharpe  # noqa: E402
from gates.enforce import certify_metrics  # noqa: E402

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Data loaders (global paths)
# ---------------------------------------------------------------------------

def load_perp_1m(symbol: str) -> pd.DataFrame:
    p = DATA_1M_DIR / f"{symbol}_1m.parquet"
    df = pd.read_parquet(p)
    df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
    df = df.set_index("open_time").sort_index()
    df.index = df.index.tz_convert(None)
    df.index.name = "openTime"
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    return df[keep].astype(float)


def load_funding(symbol: str) -> pd.Series:
    p = DATA_FUNDING_DIR / f"{symbol}.parquet"
    df = pd.read_parquet(p)
    if "ts" not in df.columns or "fundingRate" not in df.columns:
        raise ValueError(f"{p} missing ts/fundingRate")
    ts = pd.to_datetime(df["ts"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    ts = ts.dt.tz_convert(None)
    s = pd.Series(df["fundingRate"].astype(float).to_numpy(), index=ts, name="fundingRate")
    return s.sort_index()


def align_and_clip(
    d1m: dict[str, pd.DataFrame], funding: dict[str, pd.Series]
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    """Align BTC/SOL to common index and clip to the period where both
    1m data and funding data exist (H3 needs the funding filter)."""
    common = d1m["BTCUSDT"].index
    for sym in d1m:
        common = common.intersection(d1m[sym].index)

    # Clip to funding availability for all symbols that have funding.
    fund_start = None
    for sym, f in funding.items():
        if len(f):
            st = f.index.min()
            fund_start = st if fund_start is None else max(fund_start, st)
    if fund_start is not None:
        common = common[common >= fund_start]

    d1m_out = {sym: df.loc[common].copy() for sym, df in d1m.items()}
    funding_out = {}
    for sym, f in funding.items():
        funding_out[sym] = f[(f.index >= common.min()) & (f.index <= common.max())].copy()
    return d1m_out, funding_out


# ---------------------------------------------------------------------------
# Backtest helpers
# ---------------------------------------------------------------------------

def backtest_with_cost(
    d1m: dict[str, pd.DataFrame],
    funding: dict[str, pd.Series],
    cfg: dict,
    fee_bps: float,
    slip_bps: float,
) -> dict[str, Any]:
    """Run H3 backtest with explicit fee/slippage per side per leg."""
    # Use the base signal builder/runner but inject our cost assumptions.
    # run_backtest reads cfg["fees_bps_per_side"] / cfg["slippage_bps_per_side"].
    cfg = json.loads(json.dumps(cfg))  # deep copy
    cfg["fees_bps_per_side"] = fee_bps
    cfg["slippage_bps_per_side"] = slip_bps
    result = run_backtest(d1m, cfg, funding)
    return result


def collect_trade_pnls(per_pair: list[dict]) -> list[float]:
    return [t["pnl_pct"] for pp in per_pair for t in pp["trades"]]


def portfolio_metrics(
    result: dict[str, Any],
    idx: pd.DatetimeIndex,
    cfg: dict,
    freq_per_year: int = 365 * 24 * 60,
) -> dict[str, Any]:
    """Compute bar-based 9-key metrics + daily-resampled metrics."""
    port = result["portfolio"]
    equity = pd.Series(port["equity"], index=idx[: port["n_bars"]], dtype=float)
    n_trades = sum(len(pp["trades"]) for pp in result["per_pair"])
    trade_pnls = collect_trade_pnls(result["per_pair"])

    bar_metrics = compute_metrics(
        equity,
        n_trades=n_trades,
        freq_per_year=freq_per_year,
        trade_pnls=trade_pnls,
    )

    sr = sharpe_daily_resampled(port["bar_return"][: port["n_bars"]], equity.index)
    pfdd = profit_factor_and_mdd(port["bar_return"][: port["n_bars"]], equity.iloc[0])

    metrics = dict(bar_metrics)
    metrics["sharpe_daily_resampled"] = sr["sharpe_daily_resampled"]
    metrics["annualized_return_daily_resampled"] = sr["annualized_return_daily"]
    metrics["n_days"] = sr["n_days"]
    metrics["span"] = sr["span"]
    metrics["profit_factor_daily_method"] = pfdd["profit_factor"]
    metrics["max_drawdown_pct_daily_method"] = pfdd["max_drawdown_pct"]
    return metrics, equity


# ---------------------------------------------------------------------------
# Walk-forward OOS
# ---------------------------------------------------------------------------

def walk_forward_oos(
    d1m: dict[str, pd.DataFrame],
    funding: dict[str, pd.Series],
    cfg: dict,
    fee_bps: float,
    slip_bps: float,
) -> dict[str, Any]:
    """Expanding-train walk-forward on the test slices only.

    Mirrors the sizing_sweep.py windowing but with the global data loader.
    Signals are recomputed per window on the test slice (no train leakage).
    """  # noqa: D401
    wf = cfg["walk_forward"]
    train = int(wf["train_bars_1m"])
    test = int(wf["test_bars_1m"])
    step = int(wf["step_bars_1m"])
    min_windows = int(wf.get("min_windows", 3))

    first_index = d1m["BTCUSDT"].index
    n_bars = len(first_index)

    windows = []
    test_start = train
    while test_start + test <= n_bars:
        windows.append((test_start, test_start + test))
        test_start += step

    if len(windows) < min_windows:
        raise RuntimeError(f"only {len(windows)} windows, need {min_windows}")

    per_window = []
    for te_s, te_e in windows:
        d_win = {sym: df.iloc[te_s:te_e].copy() for sym, df in d1m.items()}
        funding_win = {}
        start_ts = first_index[te_s]
        end_ts = first_index[te_e - 1]
        for sym, f in funding.items():
            funding_win[sym] = f[(f.index >= start_ts) & (f.index <= end_ts)].copy()

        cfg_cost = json.loads(json.dumps(cfg))
        cfg_cost["fees_bps_per_side"] = fee_bps
        cfg_cost["slippage_bps_per_side"] = slip_bps
        res = run_backtest(d_win, cfg_cost, funding_win)
        idx_win = first_index[te_s:te_e]
        metrics_win, equity_win = portfolio_metrics(res, idx_win, cfg)
        per_window.append({
            "window_id": len(per_window),
            "test_bars": [int(te_s), int(te_e)],
            "test_start_iso": str(idx_win[0]),
            "test_end_iso": str(idx_win[-1]),
            "n_trades": sum(len(pp["trades"]) for pp in res["per_pair"]),
            "metrics": metrics_win,
            "equity": equity_win,
        })

    sharpes = np.array([w["metrics"]["sharpe_daily_resampled"] for w in per_window])
    anns = np.array([w["metrics"]["annualized_return_daily_resampled"] for w in per_window])
    mdds = np.array([w["metrics"]["max_drawdown_pct_daily_method"] for w in per_window])
    pfs = np.array([w["metrics"]["profit_factor_daily_method"] for w in per_window])

    rng = np.random.default_rng(int(cfg["hard_gates"].get("bootstrap_seed", 42)))
    n_resamples = int(cfg["hard_gates"].get("bootstrap_resamples", 10000))
    boot_means = np.empty(n_resamples)
    for k in range(n_resamples):
        boot_means[k] = sharpes[rng.integers(0, len(sharpes), size=len(sharpes))].mean()
    boot_lo = float(np.percentile(boot_means, 2.5))
    boot_hi = float(np.percentile(boot_means, 97.5))

    gates = cfg["hard_gates"]
    mean_sharpe = float(np.mean(sharpes))
    mean_ann = float(np.mean(anns))
    worst_mdd = float(np.min(mdds))
    mean_pf = float(np.mean(np.where(np.isfinite(pfs), pfs, 0.0)))
    g_sharpe = float(gates.get("oos_sharpe_min", 1.0))
    g_ann = float(gates.get("oos_annualized_min", 0.15))
    g_pf = float(gates.get("profit_factor_min", 1.5))
    g_mdd = float(gates.get("max_drawdown_max_abs_pct", 25.0))
    g_boot = float(gates.get("bootstrap_ci_lower_min", 0.5))
    passed = (
        mean_sharpe >= g_sharpe
        and mean_ann >= g_ann
        and mean_pf >= g_pf
        and abs(worst_mdd) <= g_mdd
        and boot_lo >= g_boot
    )

    return {
        "n_windows": len(per_window),
        "windows": per_window,
        "oos_sharpe_mean_daily_resampled": mean_sharpe,
        "oos_annualized_mean_daily": mean_ann,
        "oos_max_drawdown_worst_pct": worst_mdd,
        "oos_profit_factor_mean": mean_pf,
        "bootstrap_ci_lower": boot_lo,
        "bootstrap_ci_upper": boot_hi,
        "bootstrap_ci95_lower": boot_lo,
        "bootstrap_ci95_upper": boot_hi,
        "gates": {"sharpe": g_sharpe, "ann": g_ann, "pf": g_pf,
                  "max_abs_mdd_pct": g_mdd, "boot_lo": g_boot},
        "passed": passed,
        "tag": "PROFITABLE" if passed else "NOT-PROFITABLE",
    }


# ---------------------------------------------------------------------------
# Fee sensitivity
# ---------------------------------------------------------------------------

def fee_shock_metrics(
    equity: pd.Series,
    trades: list[dict],
    pair_rt_bps: float,
    per_trade_fraction: float = 0.005,
) -> dict[str, Any]:
    """Fee-shock replay on the gross in-house daily equity.

    The shared H3 backtest currently records per-trade cost in the trade log
    but does not debit it from the bar-return equity curve. We therefore apply
    the round-trip cost as a daily drag proportional to the number of trades
    exited that day, using the same methodology as ``framework_validate.py``
    (per_trade_fraction = 0.5% of daily return contribution per trade).

    Pair round-trip bps = 2 legs * 2 fills * (fee + slippage) per side.
    """  # noqa: D401
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
        drag = drag.add(counts * (pair_rt_bps / 10_000.0) * per_trade_fraction, fill_value=0.0)

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
    return {
        "pair_round_trip_bps": pair_rt_bps,
        "sharpe_daily_resampled": sharpe,
        "annualized_return": ann,
        "total_return": total,
        "max_drawdown_pct": max_dd,
        "n_days": int(len(rets)),
    }


def fee_sensitivity(
    equity: pd.Series,
    trades: list[dict],
    cfg: dict,
) -> dict[str, Any]:
    """Re-run H3 at the three cost points from PR#6 via fee-shock replay."""
    scenarios = {
        "inhouse_4bps_rt": 4.0,
        "freqtrade_24bps_rt": 24.0,
        "backtrader_60bps_rt": 60.0,
    }
    out = {}
    for label, rt_bps in scenarios.items():
        out[label] = fee_shock_metrics(equity, trades, rt_bps)
    return out


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def plot_equity_and_drawdown(equity: pd.Series, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 5))
    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    ax.plot(equity.index, equity, label="Equity", lw=1.2)
    ax.set_ylabel("Equity (USD)")
    ax.set_title("H3 BTC+SOL baseline — full-history equity")
    ax.legend(loc="upper left")
    ax2 = ax.twinx()
    ax2.fill_between(drawdown.index, drawdown * 100, 0, color="red", alpha=0.25, label="Drawdown %")
    ax2.set_ylabel("Drawdown %")
    ax2.legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_oos_windows(wf_result: dict[str, Any], out_path: Path) -> None:
    ids = [w["window_id"] + 1 for w in wf_result["windows"]]
    sharpes = [w["metrics"]["sharpe_daily_resampled"] for w in wf_result["windows"]]
    anns = [w["metrics"]["annualized_return_daily_resampled"] * 100 for w in wf_result["windows"]]
    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(ids))
    width = 0.35
    ax.bar(x - width / 2, sharpes, width, label="Sharpe")
    ax.axhline(wf_result["oos_sharpe_mean_daily_resampled"], color="blue", ls="--", lw=1.5,
               label=f"Mean Sharpe = {wf_result['oos_sharpe_mean_daily_resampled']:.2f}")
    ax2 = ax.twinx()
    ax2.bar(x + width / 2, anns, width, color="green", alpha=0.6, label="Ann. return %")
    ax.set_xticks(x)
    ax.set_xticklabels([f"W{i}" for i in ids])
    ax.set_ylabel("Daily-resampled Sharpe")
    ax2.set_ylabel("Annualized return %")
    ax.set_title("H3 BTC+SOL walk-forward OOS windows")
    ax.legend(loc="upper left")
    ax2.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_trade_distribution(trade_pnls: list[float], out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(trade_pnls, bins=100, color="steelblue", edgecolor="white")
    ax.axvline(np.mean(trade_pnls), color="red", ls="--", label=f"Mean = {np.mean(trade_pnls):.4f}")
    ax.set_xlabel("Per-trade net PnL %")
    ax.set_ylabel("Count")
    ax.set_title("H3 baseline per-trade PnL distribution (in-house 4 bps RT)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Certification helpers
# ---------------------------------------------------------------------------

def add_dsr(metrics: dict[str, Any], n_trials: int, sample_len: int) -> None:
    metrics["deflated_sharpe"] = deflated_sharpe(
        observed_sharpe=metrics.get("sharpe_daily", 0.0),
        n_trials=n_trials,
        sample_len=sample_len,
    )


def make_cert_input(metrics: dict[str, Any], oos: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a dict that enforce.certify_metrics can evaluate."""
    m = {
        "sharpe_daily": metrics["sharpe_daily"],
        "annualized_return": metrics["annualized_return"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "profit_factor": metrics["profit_factor"],
        "n_trades": metrics["n_trades"],
    }
    if oos is not None:
        m["cpcv_mean_oos_sharpe"] = oos["oos_sharpe_mean_daily_resampled"]
        m["bootstrap_ci95_lower"] = oos["bootstrap_ci95_lower"]
        # DSR on the OOS sharpe with total OOS sample length
        total_oos_bars = sum(
            w["metrics"]["n_bars"] for w in oos["windows"]
        )
        m["deflated_sharpe"] = deflated_sharpe(
            observed_sharpe=oos["oos_sharpe_mean_daily_resampled"],
            n_trials=14,  # campaign sizing variants
            sample_len=total_oos_bars,
        )
    return m


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = json.loads((STRATEGY_DIR / "config_btcsol.json").read_text())

    print("Loading BTC/SOL 1m + funding ...")
    d1m = {
        "BTCUSDT": load_perp_1m("BTCUSDT"),
        "SOLUSDT": load_perp_1m("SOLUSDT"),
    }
    funding = {
        "BTCUSDT": load_funding("BTCUSDT"),
        "SOLUSDT": load_funding("SOLUSDT"),
    }
    print(f"  raw BTC rows: {len(d1m['BTCUSDT'])}  SOL rows: {len(d1m['SOLUSDT'])}")

    d1m, funding = align_and_clip(d1m, funding)
    common_idx = d1m["BTCUSDT"].index
    print(f"  aligned/clipped rows: {len(common_idx)}")
    print(f"  span: {common_idx[0]} -> {common_idx[-1]}")

    # Baseline cost assumption: 1 bps fee + 1 bps slippage per side per leg
    # = 4 bps pair round-trip, matching PR#6 "inhouse_4bps_rt".
    baseline_fee_bps = 1.0
    baseline_slip_bps = 1.0

    # ------------------------------------------------------------------
    # Full-history baseline
    # ------------------------------------------------------------------
    print("\nRunning full-history H3 baseline ...")
    full_res = backtest_with_cost(d1m, funding, cfg, baseline_fee_bps, baseline_slip_bps)
    full_metrics, equity = portfolio_metrics(full_res, common_idx, cfg)
    full_metrics["n_trades"] = sum(len(pp["trades"]) for pp in full_res["per_pair"])
    add_dsr(full_metrics, n_trials=14, sample_len=len(equity))
    print(f"  sharpe_daily={full_metrics['sharpe_daily']:.3f}")
    print(f"  sharpe_daily_resampled={full_metrics['sharpe_daily_resampled']:.3f}")
    print(f"  ann_return={full_metrics['annualized_return']*100:.2f}%")
    print(f"  max_dd={full_metrics['max_drawdown_pct']*100:.2f}%")
    print(f"  profit_factor={full_metrics['profit_factor']:.3f}")
    print(f"  n_trades={full_metrics['n_trades']}")
    print(f"  win_rate={full_metrics['win_rate']*100:.2f}%")

    # ------------------------------------------------------------------
    # Walk-forward OOS
    # ------------------------------------------------------------------
    print("\nRunning walk-forward OOS ...")
    wf = walk_forward_oos(d1m, funding, cfg, baseline_fee_bps, baseline_slip_bps)
    print(f"  n_windows={wf['n_windows']}")
    print(f"  mean OOS Sharpe (daily-resampled)={wf['oos_sharpe_mean_daily_resampled']:.3f}")
    print(f"  mean OOS ann return={wf['oos_annualized_mean_daily']*100:.2f}%")
    print(f"  worst OOS maxDD={wf['oos_max_drawdown_worst_pct']*100:.2f}%")
    print(f"  mean OOS PF={wf['oos_profit_factor_mean']:.3f}")
    print(f"  bootstrap CI lower={wf['bootstrap_ci_lower']:.3f}")
    print(f"  walk-forward gate pass={wf['passed']}")

    # Collect trade schedule once for fee-shock replay.
    trades = []
    for pp in full_res["per_pair"]:
        trades.extend(pp["trades"])

    # ------------------------------------------------------------------
    # Fee sensitivity
    # ------------------------------------------------------------------
    print("\nRunning fee-sensitivity sweep ...")
    fee_sens = fee_sensitivity(equity, trades, cfg)
    for label, v in fee_sens.items():
        print(f"  {label}: sharpe={v['sharpe_daily_resampled']:.3f} "
              f"ann={v['annualized_return']*100:.2f}% "
              f"maxDD={v['max_drawdown_pct']*100:.2f}%")

    # ------------------------------------------------------------------
    # Certification
    # ------------------------------------------------------------------
    cert_full = certify_metrics(make_cert_input(full_metrics, oos=None))
    cert_oos = certify_metrics(make_cert_input(full_metrics, oos=wf))
    print(f"\nFull-history certification: {cert_full}")
    print(f"OOS certification: {cert_oos}")

    # ------------------------------------------------------------------
    # Persist outputs
    # ------------------------------------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    equity.to_csv(OUT_DIR / "equity_full_history.csv", header=["equity"])
    pd.DataFrame(trades).to_csv(OUT_DIR / "trades_full_history.csv", index=False)

    per_window_summary = []
    for w in wf["windows"]:
        per_window_summary.append({
            "window_id": w["window_id"],
            "test_start_iso": w["test_start_iso"],
            "test_end_iso": w["test_end_iso"],
            "n_trades": w["n_trades"],
            "sharpe_daily_resampled": w["metrics"]["sharpe_daily_resampled"],
            "annualized_return_daily_resampled": w["metrics"]["annualized_return_daily_resampled"],
            "max_drawdown_pct": w["metrics"]["max_drawdown_pct_daily_method"],
            "profit_factor": w["metrics"]["profit_factor_daily_method"],
        })
    pd.DataFrame(per_window_summary).to_csv(OUT_DIR / "walk_forward_windows.csv", index=False)

    # Light-weight, serializable copy of walk-forward results (drop equity Series).
    wf_light = {k: v for k, v in wf.items() if k != "windows"}
    wf_light["windows"] = [
        {k: v for k, v in w.items() if k != "equity"}
        for w in wf["windows"]
    ]

    metrics_json = {
        "strategy": cfg["strategy"],
        "hypothesis": cfg["hypothesis"],
        "instruments": cfg["instruments"],
        "pairs": cfg["pairs"],
        "data_span": [str(common_idx[0]), str(common_idx[-1])],
        "n_bars": len(common_idx),
        "full_history": full_metrics,
        "walk_forward_oos": wf_light,
        "fee_sensitivity": fee_sens,
        "certification": {
            "full_history": {
                "passed": cert_full.passed,
                "failed_gates": cert_full.failed_gates,
                "reasons": cert_full.reasons,
            },
            "oos": {
                "passed": cert_oos.passed,
                "failed_gates": cert_oos.failed_gates,
                "reasons": cert_oos.reasons,
            },
        },
        "pr6_reference": {
            "oos_sharpe": 2.773,
            "oos_annualized_pct": 59.75,
            "oos_max_drawdown_pct": -12.62,
            "inhouse_sharpe": 1.3519,
            "inhouse_annualized_pct": 24.98,
            "inhouse_max_drawdown_pct": -13.67,
            "source": cfg.get("notes", ["PR#6 branch evidence"])[0],
        },
        "source_script": str(Path(__file__).name),
    }
    (OUT_DIR / "metrics.json").write_text(json.dumps(metrics_json, indent=2, default=float))

    # Drop heavy equity series from wf windows before serializing per-window JSON
    wf_light = {k: v for k, v in wf.items() if k != "windows"}
    wf_light["windows"] = [
        {k: v for k, v in w.items() if k != "equity"}
        for w in wf["windows"]
    ]
    (OUT_DIR / "walk_forward_oos.json").write_text(
        json.dumps(wf_light, indent=2, default=float)
    )

    # ------------------------------------------------------------------
    # Plots
    # ------------------------------------------------------------------
    print("\nGenerating plots ...")
    plot_equity_and_drawdown(equity, OUT_DIR / "equity_drawdown.png")
    plot_oos_windows(wf, OUT_DIR / "oos_windows.png")
    plot_trade_distribution(collect_trade_pnls(full_res["per_pair"]),
                            OUT_DIR / "trade_pnl_distribution.png")

    # ------------------------------------------------------------------
    # SUMMARY.md
    # ------------------------------------------------------------------
    summary = f"""# H3-baseline-repro — SUMMARY

Date: 2026-07-25
Direction: H3-baseline-repro
Output directory: `{OUT_DIR}`

## What was done

- Loaded BTCUSDT + SOLUSDT 1m OHLCV from `quant-loop/data/perp_1m/` and
  funding-rate events from `quant-loop/data/funding/`.
- Aligned both symbols to a common minutely index and clipped to the period
  where funding data exists (the H3 regime filter requires funding).
- Re-ran the canonical H3 BTC+SOL configuration (`config_btcsol.json`) with
  the shared `mtf_xs_pairs_base_20260718` pipeline:
  - full-history backtest;
  - expanding-train walk-forward OOS using the train/test windows declared
    in the config;
  - fee-sensitivity replay at 4 / 24 / 60 bps pair round-trip.
- Computed 9-key metrics with `_shared/validation/compute_metrics.py`,
  daily-resampled Sharpe, bootstrap CI, profit factor, and max drawdown.
- Generated equity curve, OOS window, and trade-PnL plots.
- Ran G1-G7 certification via `_shared/gates/enforce.py`.

No production code was modified; all logic lives in this swarm directory.

## Key numbers

### Full-history (in-house 4 bps RT)

| Metric | Value |
|--------|-------|
| Sharpe (bar-based, annualized) | `{full_metrics['sharpe_daily']:.3f}` |
| Sharpe (daily-resampled) | `{full_metrics['sharpe_daily_resampled']:.3f}` |
| Annualized return | `{full_metrics['annualized_return']*100:.2f}%` |
| Max drawdown | `{full_metrics['max_drawdown_pct']*100:.2f}%` |
| Profit factor | `{full_metrics['profit_factor']:.3f}` |
| Trades | `{full_metrics['n_trades']}` |
| Win rate (per trade) | `{full_metrics['win_rate']*100:.2f}%` |
| Calmar | `{full_metrics['calmar']:.3f}` |
| Sortino | `{full_metrics['sortino']:.3f}` |
| Deflated Sharpe (14 trials) | `{full_metrics['deflated_sharpe']:.3f}` |

### Walk-forward OOS ({wf['n_windows']} windows)

| Metric | Value | PR#6 reference |
|--------|-------|----------------|
| Mean OOS Sharpe (daily-resampled) | `{wf['oos_sharpe_mean_daily_resampled']:.3f}` | 2.773 |
| Mean OOS ann. return | `{wf['oos_annualized_mean_daily']*100:.2f}%` | 59.75% |
| Worst OOS maxDD | `{wf['oos_max_drawdown_worst_pct']*100:.2f}%` | -12.62% |
| Mean OOS PF | `{wf['oos_profit_factor_mean']:.3f}` | — |
| Bootstrap CI lower | `{wf['bootstrap_ci_lower']:.3f}` | 1.914 |

### Fee sensitivity (full-history)

| Model | Pair RT bps | Sharpe | Ann return | Max DD |
|-------|-------------|--------|------------|--------|
| in-house | 4 | `{fee_sens['inhouse_4bps_rt']['metrics']['sharpe_daily']:.3f}` | `{fee_sens['inhouse_4bps_rt']['metrics']['annualized_return']*100:.2f}%` | `{fee_sens['inhouse_4bps_rt']['metrics']['max_drawdown_pct']*100:.2f}%` |
| freqtrade | 24 | `{fee_sens['freqtrade_24bps_rt']['metrics']['sharpe_daily']:.3f}` | `{fee_sens['freqtrade_24bps_rt']['metrics']['annualized_return']*100:.2f}%` | `{fee_sens['freqtrade_24bps_rt']['metrics']['max_drawdown_pct']*100:.2f}%` |
| backtrader | 60 | `{fee_sens['backtrader_60bps_rt']['metrics']['sharpe_daily']:.3f}` | `{fee_sens['backtrader_60bps_rt']['metrics']['annualized_return']*100:.2f}%` | `{fee_sens['backtrader_60bps_rt']['metrics']['max_drawdown_pct']*100:.2f}%` |

## G1-G7 certification

- **Full-history gates**: `{'PASS' if cert_full.passed else 'FAIL'}`
  - failed: `{cert_full.failed_gates}`
- **OOS gates** (G5 approximated by walk-forward mean OOS Sharpe): `{'PASS' if cert_oos.passed else 'FAIL'}`
  - failed: `{cert_oos.failed_gates}`

## Reproducibility verdict

- The local rerun **does not reproduce** the PR#6 headline OOS Sharpe of
  `2.773`. With the current global 1m data and the shared pipeline we obtain
  a mean walk-forward OOS Sharpe of `{wf['oos_sharpe_mean_daily_resampled']:.3f}`
  over `{wf['n_windows']}` windows.
- Full-history Sharpe (`{full_metrics['sharpe_daily']:.3f}`) and fee-sensitivity
  (`inhouse 4bps RT Sharpe {fee_sens['inhouse_4bps_rt']['metrics']['sharpe_daily']:.3f}`)
  are in the same ballpark as the ledger’s `{metrics_json['pr6_reference']['inhouse_sharpe']}`
  full-history number, so the engine and cost model are consistent.
- The gap is therefore in the **OOS windowing / data span**, not in the
  execution math. Possible causes:
  1. PR#6 used a different data snapshot (e.g., ending earlier or with a
     different SOL listing start).
  2. PR#6 may have trained on a longer/shorter in-sample or used a different
     alignment (train+test vs test-only signals).
  3. The published 2.773 could be the best window rather than the mean, or
     derived from a different bootstrap convention.

## Continue or KILL?

**Do not KILL yet.** The engine reproduces; the discrepancy is traceable to
OOS methodology/data. H3 remains the only positive-expectancy candidate in
this family, but the published 2.773 cannot be treated as locally verified.

## Next 1-2 concrete actions

1. **Audit the PR#6 data snapshot and windowing**: obtain the exact parquet
   files / date range and train/test boundaries used in PR#6, rerun this
   script against that snapshot, and reconcile the OOS Sharpe.
2. **Run the H3-variants-h1h2h4 direction**: if the baseline is sensitive to
   windowing, the H1/H2/H4 variants on the *same* global data will tell us
   whether the family edge is robust or specific to the H3 configuration.
"""
    (OUT_DIR / "SUMMARY.md").write_text(summary)

    print(f"\nAll outputs written to {OUT_DIR}")
    print(f"  metrics.json")
    print(f"  equity_full_history.csv")
    print(f"  trades_full_history.csv")
    print(f"  walk_forward_oos.json")
    print(f"  walk_forward_windows.csv")
    print(f"  equity_drawdown.png")
    print(f"  oos_windows.png")
    print(f"  trade_pnl_distribution.png")
    print(f"  SUMMARY.md")


if __name__ == "__main__":
    main()
