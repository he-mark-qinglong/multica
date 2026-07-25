"""FIXED H1/H2/H3/H4 BTC+SOL variant study (SMA-35145 follow-up, 2026-07-25).

Why this script exists
----------------------
``run_btcsol_variants.py`` produced impossible numbers (H3 Sharpe ~ -43.65)
because its ``_backtest_pair_with_cost`` monkey-patch debited the pair
round-trip cost in FULL-spread units (2*2*(fee+slip) bps) from bar returns
that are computed in HALF-spread units (``pos * (a_ret - b_ret) / 2``),
double-counting every fill. It also ran at 22 bps RT/symbol while the
established H3 baseline (``../H3-baseline-repro/``) uses 1+1 bps/side with
costs recorded in the trade log only, plus a fee-shock replay for
sensitivity.

This fixed runner reproduces the baseline methodology exactly, generalized
to all four hypotheses:

- Data: BTC+SOL 1m, aligned to common index and clipped to funding
  availability (2021-11-20 16:01 -> 2026-07-17), identical to the baseline.
- Cost model: fee = 1 bps + slippage = 1 bps per side per leg (baseline
  in-house 4 bps pair RT). No bar-return cost patch: the base engine
  records costs in the trade log, matching the baseline.
- Metrics: ``compute_metrics`` on the real-indexed equity + daily-resampled
  Sharpe, expanding-train walk-forward OOS (test-slice signals, 7 windows),
  bootstrap CI (seed 42, 10000 resamples) — verbatim from
  ``repro_h3_baseline.py``.
- Fee sensitivity: baseline fee-shock replay at 4 / 24 / 60 bps pair RT
  (per_trade_fraction = 0.005).

Outputs (do NOT overwrite the original buggy artifacts):
- results/metrics.fixed.json
- results/equity_{H1,H2,H3,H4}_daily.fixed.csv
- results/equity_curves.fixed.png, results/oos_metrics.fixed.png
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
OUT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = OUT_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
ROOT = Path(__file__).resolve().parents[4]  # quant-loop root
STRATEGIES_DIR = ROOT / "strategies"
SHARED_DIR = ROOT / "_shared"
DATA_1M_DIR = ROOT / "data" / "perp_1m"
DATA_FUNDING_DIR = ROOT / "data" / "funding"
BASELINE_DIR = OUT_DIR.parent / "H3-baseline-repro"

for p in (str(STRATEGIES_DIR), str(STRATEGIES_DIR / "_indicators"),
          str(SHARED_DIR), str(SHARED_DIR / "validation"), str(SHARED_DIR / "gates")):
    if p not in sys.path:
        sys.path.insert(0, p)

from mtf_xs_pairs_base_20260718 import (  # noqa: E402
    profit_factor_and_mdd,
    run_backtest,
    sharpe_daily_resampled,
)
from compute_metrics import compute_metrics  # noqa: E402
from cpcv import deflated_sharpe  # noqa: E402
from gates.enforce import certify_metrics  # noqa: E402

warnings.filterwarnings("ignore")

# Baseline cost model: 1 bps fee + 1 bps slippage per side per leg
# (= 4 bps pair round-trip, in-house convention). Costs are recorded in the
# trade log by the base engine; the equity curve is gross, exactly like the
# established H3 baseline.
BASELINE_FEE_BPS = 1.0
BASELINE_SLIP_BPS = 1.0

# ---------------------------------------------------------------------------
# Data loaders + alignment — verbatim from repro_h3_baseline.py
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
    ts = pd.to_datetime(df["ts"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    ts = ts.dt.tz_convert(None)
    s = pd.Series(df["fundingRate"].astype(float).to_numpy(), index=ts, name="fundingRate")
    return s.sort_index()


def align_and_clip(d1m, funding):
    """Align to common index, clip to funding availability (baseline method)."""
    common = d1m["BTCUSDT"].index
    for sym in d1m:
        common = common.intersection(d1m[sym].index)
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
# Configs
# ---------------------------------------------------------------------------

def load_config(hyp: str) -> dict:
    if hyp in ("H1", "H2", "H3"):
        cfg = json.loads(
            (STRATEGIES_DIR / f"mtf_xs_pairs_1m_15m_2h_{hyp.lower()}_20260718" / "config.json").read_text()
        )
    elif hyp == "H4":
        cfg = _build_h4_config()
    else:
        raise ValueError(hyp)
    # BTC+SOL only (same scope as the buggy study and the H3 baseline).
    cfg["instruments"] = ["BTCUSDT", "SOLUSDT"]
    cfg["pairs"] = ["BTCUSDT/SOLUSDT"]
    # Baseline cost model.
    cfg["fees_bps_per_side"] = BASELINE_FEE_BPS
    cfg["slippage_bps_per_side"] = BASELINE_SLIP_BPS
    return cfg


def _build_h4_config() -> dict:
    """H4 config (same reconstruction as the original variant study)."""
    return {
        "strategy": "mtf_xs_pairs_1m_15m_2h_h4_20260718",
        "iteration": 107,
        "campaign": "SMA-34875 mtf-1m-15m-2h H4 BTC+SOL",
        "hypothesis": "H4",
        "date": "2026-07-18",
        "primary_timeframe": "1m",
        "filter_timeframe": "15m",
        "regime_timeframe": "2h",
        "description": "H4: 1m cross-pair z-score + 15m EMA-8/21 direction filter + 2h trend cap.",
        "instruments": ["BTCUSDT", "SOLUSDT"],
        "pairs": ["BTCUSDT/SOLUSDT"],
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
        "fees_bps_per_side": BASELINE_FEE_BPS,
        "slippage_bps_per_side": BASELINE_SLIP_BPS,
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
    }


# ---------------------------------------------------------------------------
# Metrics — verbatim methodology from repro_h3_baseline.py
# ---------------------------------------------------------------------------

def portfolio_metrics(result, idx, cfg, freq_per_year=365 * 24 * 60):
    port = result["portfolio"]
    equity = pd.Series(port["equity"], index=idx[: port["n_bars"]], dtype=float)
    n_trades = sum(len(pp["trades"]) for pp in result["per_pair"])
    trade_pnls = [t["pnl_pct"] for pp in result["per_pair"] for t in pp["trades"]]

    bar_metrics = compute_metrics(
        equity, n_trades=n_trades, freq_per_year=freq_per_year, trade_pnls=trade_pnls,
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


def walk_forward_oos(d1m, funding, cfg, fee_bps, slip_bps):
    """Expanding-train walk-forward on test slices only (baseline method)."""
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
        start_ts = first_index[te_s]
        end_ts = first_index[te_e - 1]
        funding_win = {
            sym: f[(f.index >= start_ts) & (f.index <= end_ts)].copy()
            for sym, f in funding.items()
        }
        cfg_cost = json.loads(json.dumps(cfg))
        cfg_cost["fees_bps_per_side"] = fee_bps
        cfg_cost["slippage_bps_per_side"] = slip_bps
        res = run_backtest(d_win, cfg_cost, funding_win)
        idx_win = first_index[te_s:te_e]
        metrics_win, _equity_win = portfolio_metrics(res, idx_win, cfg)
        per_window.append({
            "window_id": len(per_window),
            "test_bars": [int(te_s), int(te_e)],
            "test_start_iso": str(idx_win[0]),
            "test_end_iso": str(idx_win[-1]),
            "n_trades": sum(len(pp["trades"]) for pp in res["per_pair"]),
            "sharpe_daily_resampled": metrics_win["sharpe_daily_resampled"],
            "annualized_return_daily_resampled": metrics_win["annualized_return_daily_resampled"],
            "max_drawdown_pct": metrics_win["max_drawdown_pct_daily_method"],
            "profit_factor": metrics_win["profit_factor_daily_method"],
        })

    sharpes = np.array([w["sharpe_daily_resampled"] for w in per_window])
    anns = np.array([w["annualized_return_daily_resampled"] for w in per_window])
    mdds = np.array([w["max_drawdown_pct"] for w in per_window])
    pfs = np.array([w["profit_factor"] for w in per_window])

    rng = np.random.default_rng(int(cfg["hard_gates"].get("bootstrap_seed", 42)))
    n_resamples = int(cfg["hard_gates"].get("bootstrap_resamples", 10000))
    boot_means = np.empty(n_resamples)
    for k in range(n_resamples):
        boot_means[k] = sharpes[rng.integers(0, len(sharpes), size=len(sharpes))].mean()
    boot_lo = float(np.percentile(boot_means, 2.5))
    boot_hi = float(np.percentile(boot_means, 97.5))

    return {
        "n_windows": len(per_window),
        "per_window": per_window,
        "oos_sharpe_mean_daily_resampled": float(np.mean(sharpes)),
        "oos_annualized_mean_daily": float(np.mean(anns)),
        "oos_max_drawdown_worst_pct": float(np.min(mdds)),
        "oos_profit_factor_mean": float(np.mean(np.where(np.isfinite(pfs), pfs, 0.0))),
        "bootstrap_ci_lower": boot_lo,
        "bootstrap_ci_upper": boot_hi,
    }


def fee_shock_metrics(equity, trades, pair_rt_bps, per_trade_fraction=1.0):
    """Fee-shock replay with corrected notional basis (SMA-36566 fix).

    Upstream default was ``per_trade_fraction=0.005`` — 200x too small relative
    to the trade log's full-pair pct basis. The engine debits
    ``cost = 2*2*(fee+slip)/10000`` (full pair pct, e.g. 8 bps at 1+1 bps/side)
    and the trade log records ``pnl_pct`` in the same full-pair pct terms, so
    the fee-shock drag MUST be debited at the same full-pair pct basis.

    Default 1.0 = full pair pct (= ``pair_rt_bps / 10000`` per trade).
    Half-spread basis 0.5 = matches bar-return normalisation exactly.
    Buggy baseline 0.005 = the original under-statement (do NOT use for verdict).
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
    }


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def plot_equity_curves(daily_eqs, out_path):
    fig, ax = plt.subplots(figsize=(10, 5))
    for hyp, eq in daily_eqs.items():
        norm = eq / eq.iloc[0]
        ax.plot(norm.index, norm.values, label=hyp, linewidth=1.2)
    ax.set_yscale("log")
    ax.set_title("H1-H4 BTC+SOL equity, baseline cost model (1+1 bps/side), daily, log scale")
    ax.set_xlabel("Date")
    ax.set_ylabel("Growth of $1")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_oos_metrics(records, out_path):
    labels = list(records.keys())
    sharpes = [records[h]["oos"]["oos_sharpe_mean_daily_resampled"] for h in labels]
    ci_lo = [records[h]["oos"]["bootstrap_ci_lower"] for h in labels]
    ci_hi = [records[h]["oos"]["bootstrap_ci_upper"] for h in labels]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(x, sharpes, 0.5, label="OOS Sharpe (mean of windows)")
    ax.errorbar(x, sharpes,
                yerr=[np.array(sharpes) - np.array(ci_lo), np.array(ci_hi) - np.array(sharpes)],
                fmt="none", ecolor="black", capsize=4, label="bootstrap 95% CI")
    ax.axhline(1.0, color="k", linestyle="--", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_title("Walk-forward OOS Sharpe, fixed pipeline (baseline cost model)")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_hypothesis(hyp, d1m, funding, common_idx):
    print(f"\n=== {hyp} (BTC+SOL, fixed pipeline) ===")
    cfg = load_config(hyp)

    print("Full-history backtest ...")
    res = run_backtest(d1m, cfg, funding)
    full_metrics, equity = portfolio_metrics(res, common_idx, cfg)
    print(f"  sharpe_daily={full_metrics['sharpe_daily']:.3f} "
          f"sharpe_daily_resampled={full_metrics['sharpe_daily_resampled']:.3f} "
          f"ann={full_metrics['annualized_return'] * 100:.2f}% "
          f"MDD={full_metrics['max_drawdown_pct'] * 100:.2f}% "
          f"trades={full_metrics['n_trades']}")

    print("Walk-forward OOS ...")
    wf = walk_forward_oos(d1m, funding, cfg, BASELINE_FEE_BPS, BASELINE_SLIP_BPS)
    print(f"  OOS Sharpe={wf['oos_sharpe_mean_daily_resampled']:.3f} "
          f"ann={wf['oos_annualized_mean_daily'] * 100:.2f}% "
          f"MDD={wf['oos_max_drawdown_worst_pct'] * 100:.2f}% "
          f"CI=[{wf['bootstrap_ci_lower']:.3f}, {wf['bootstrap_ci_upper']:.3f}] "
          f"windows={wf['n_windows']}")

    trades = [t for pp in res["per_pair"] for t in pp["trades"]]
    fee_sens = {
        label: fee_shock_metrics(equity, trades, rt)
        for label, rt in (("inhouse_4bps_rt", 4.0),
                          ("freqtrade_24bps_rt", 24.0),
                          ("backtrader_60bps_rt", 60.0))
    }
    print("  fee shock 4/24/60 bps pair RT Sharpe: "
          f"{fee_sens['inhouse_4bps_rt']['sharpe_daily_resampled']:.3f} / "
          f"{fee_sens['freqtrade_24bps_rt']['sharpe_daily_resampled']:.3f} / "
          f"{fee_sens['backtrader_60bps_rt']['sharpe_daily_resampled']:.3f}")

    return {
        "hypothesis": hyp,
        "full_history": full_metrics,
        "oos": wf,
        "fee_sensitivity": fee_sens,
        "equity": equity,
    }


def main() -> None:
    print("Loading BTC/SOL 1m + funding (baseline loaders) ...")
    d1m = {"BTCUSDT": load_perp_1m("BTCUSDT"), "SOLUSDT": load_perp_1m("SOLUSDT")}
    funding = {"BTCUSDT": load_funding("BTCUSDT"), "SOLUSDT": load_funding("SOLUSDT")}
    d1m, funding = align_and_clip(d1m, funding)
    common_idx = d1m["BTCUSDT"].index
    print(f"  aligned/clipped rows: {len(common_idx)}  span: {common_idx[0]} -> {common_idx[-1]}")

    records = {}
    for hyp in ("H1", "H2", "H3", "H4"):
        records[hyp] = run_hypothesis(hyp, d1m, funding, common_idx)

    # Sanity check against the established H3 baseline.
    baseline = json.loads((BASELINE_DIR / "metrics.json").read_text())
    b_oos = baseline["walk_forward_oos"]
    h3 = records["H3"]["oos"]
    sanity = {
        "baseline_oos_sharpe": b_oos["oos_sharpe_mean_daily_resampled"],
        "fixed_h3_oos_sharpe": h3["oos_sharpe_mean_daily_resampled"],
        "delta_oos_sharpe": h3["oos_sharpe_mean_daily_resampled"] - b_oos["oos_sharpe_mean_daily_resampled"],
        "baseline_bootstrap_ci_lower": b_oos["bootstrap_ci_lower"],
        "fixed_h3_bootstrap_ci_lower": h3["bootstrap_ci_lower"],
        "delta_bootstrap_ci_lower": h3["bootstrap_ci_lower"] - b_oos["bootstrap_ci_lower"],
        "baseline_n_trades_full": baseline["full_history"]["n_trades"],
        "fixed_h3_n_trades_full": records["H3"]["full_history"]["n_trades"],
    }
    print("\n=== H3 sanity vs baseline ===")
    for k, v in sanity.items():
        print(f"  {k}: {v}")

    summary = {
        "note": ("FIXED H1-H4 BTC+SOL comparison. Baseline cost model "
                 "(1 bps fee + 1 bps slippage per side per leg = 4 bps pair RT; "
                 "costs in trade log, gross equity) + baseline fee-shock replay. "
                 "Data clipped to funding availability 2021-11-20, identical to "
                 "H3-baseline-repro. Supersedes results/metrics.json whose "
                 "cost patch double-counted costs (full-spread cost debited "
                 "from half-spread bar returns)."),
        "cost": {"fee_bps_per_side": BASELINE_FEE_BPS, "slippage_bps_per_side": BASELINE_SLIP_BPS},
        "data_span": [str(common_idx[0]), str(common_idx[-1])],
        "n_bars": int(len(common_idx)),
        "variants": {
            h: {
                "full_history": records[h]["full_history"],
                "walk_forward_oos": records[h]["oos"],
                "fee_sensitivity": records[h]["fee_sensitivity"],
            }
            for h in records
        },
        "h3_sanity_vs_baseline": sanity,
        "source_script": Path(__file__).name,
    }
    (RESULTS_DIR / "metrics.fixed.json").write_text(json.dumps(summary, indent=2, default=float))

    daily_eqs = {}
    for h, r in records.items():
        daily_eq = r["equity"].resample("1D").last().dropna()
        daily_eqs[h] = daily_eq
        daily_eq.to_frame("equity").to_csv(RESULTS_DIR / f"equity_{h}_daily.fixed.csv")

    plot_equity_curves(daily_eqs, RESULTS_DIR / "equity_curves.fixed.png")
    plot_oos_metrics(records, RESULTS_DIR / "oos_metrics.fixed.png")

    print(f"\nFixed results written to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
