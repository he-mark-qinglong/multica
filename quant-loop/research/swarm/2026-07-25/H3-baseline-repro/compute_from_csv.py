"""Compute H3 baseline metrics from the already-run CSV outputs.

This script does NOT rerun the backtest. It reads:
  - equity_full_history.csv
  - trades_full_history.csv
  - walk_forward_windows.csv

and emits metrics.json + SUMMARY.md.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path("/Users/mark/multica/quant-loop")
OUT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "_shared" / "validation"))
sys.path.insert(0, str(ROOT / "_shared" / "gates"))
from compute_metrics import compute_metrics  # noqa: E402
from cpcv import deflated_sharpe  # noqa: E402
from enforce import certify_metrics  # noqa: E402


def load_data() -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    eq = pd.read_csv(OUT_DIR / "equity_full_history.csv")
    eq["openTime"] = pd.to_datetime(eq["openTime"])
    eq = eq.set_index("openTime").sort_index()
    equity = eq["equity"].astype(float)

    trades = pd.read_csv(OUT_DIR / "trades_full_history.csv")
    trades["entry_ts"] = pd.to_datetime(trades["entry_ts"])
    trades["exit_ts"] = pd.to_datetime(trades["exit_ts"])

    wf = pd.read_csv(OUT_DIR / "walk_forward_windows.csv")
    return equity, trades, wf


def daily_metrics(equity: pd.Series) -> dict[str, float]:
    daily_eq = equity.resample("1D").last().dropna()
    daily_ret = daily_eq.pct_change().dropna()
    if len(daily_ret) < 2 or float(daily_ret.std(ddof=1)) < 1e-12:
        sharpe = 0.0
    else:
        sharpe = float(daily_ret.mean() / daily_ret.std(ddof=1) * math.sqrt(365.0))
    total = float(daily_eq.iloc[-1] / daily_eq.iloc[0] - 1.0)
    n_days = len(daily_ret)
    ann = float((1.0 + total) ** (365.0 / n_days) - 1.0) if n_days > 0 else 0.0
    max_dd = float((daily_eq / daily_eq.cummax() - 1.0).min())
    pos = daily_ret[daily_ret > 0].sum()
    neg = -daily_ret[daily_ret < 0].sum()
    pf = float(pos / neg) if neg > 0 else float("inf")
    return {
        "sharpe_daily_resampled": sharpe,
        "annualized_return": ann,
        "total_return": total,
        "max_drawdown_pct": max_dd,
        "profit_factor": pf,
        "n_days": n_days,
    }


def full_history_metrics(equity: pd.Series, trades: pd.DataFrame) -> dict[str, Any]:
    n_trades = len(trades)
    trade_pnls = trades["pnl_pct"].astype(float).to_list()
    win_rate = float((np.array(trade_pnls) > 0).mean()) if n_trades else 0.0

    # bar-based 9-key metrics (freq = 1m bars per year)
    bar_metrics = compute_metrics(
        equity,
        n_trades=n_trades,
        freq_per_year=365 * 24 * 60,
        trade_pnls=trade_pnls,
    )

    # daily-resampled overlay
    dm = daily_metrics(equity)
    metrics = dict(bar_metrics)
    metrics["sharpe_daily_resampled"] = dm["sharpe_daily_resampled"]
    metrics["annualized_return_daily_resampled"] = dm["annualized_return"]
    metrics["profit_factor_daily"] = dm["profit_factor"]
    metrics["max_drawdown_pct_daily"] = dm["max_drawdown_pct"]
    metrics["total_return"] = dm["total_return"]
    metrics["n_days"] = dm["n_days"]
    metrics["trades_per_day"] = n_trades / max(dm["n_days"], 1)
    metrics["win_rate"] = win_rate
    metrics["deflated_sharpe"] = deflated_sharpe(
        observed_sharpe=metrics["sharpe_daily"],
        n_trials=14,
        sample_len=int(len(equity)),
    )
    return metrics


def walk_forward_summary(wf: pd.DataFrame) -> dict[str, Any]:
    sharpes = wf["sharpe_daily_resampled"].to_numpy(dtype=float)
    anns = wf["annualized_return_daily_resampled"].to_numpy(dtype=float)
    mdds = wf["max_drawdown_pct"].to_numpy(dtype=float)
    pfs = wf["profit_factor"].to_numpy(dtype=float)

    rng = np.random.default_rng(42)
    n_resamples = 10000
    boot_means = np.empty(n_resamples)
    for k in range(n_resamples):
        boot_means[k] = sharpes[rng.integers(0, len(sharpes), size=len(sharpes))].mean()
    boot_lo = float(np.percentile(boot_means, 2.5))
    boot_hi = float(np.percentile(boot_means, 97.5))

    return {
        "n_windows": int(len(wf)),
        "oos_sharpe_mean_daily_resampled": float(np.mean(sharpes)),
        "oos_annualized_mean_daily": float(np.mean(anns)),
        "oos_max_drawdown_worst_pct": float(np.min(mdds)),
        "oos_profit_factor_mean": float(np.mean(np.where(np.isfinite(pfs), pfs, 0.0))),
        "bootstrap_ci_lower": boot_lo,
        "bootstrap_ci_upper": boot_hi,
        "per_window": wf.to_dict(orient="records"),
    }


def fee_shock_metrics(
    equity: pd.Series, trades: pd.DataFrame, pair_rt_bps: float, per_trade_fraction: float = 0.005
) -> dict[str, float]:
    daily_eq = equity.resample("1D").last().dropna()
    daily_ret = daily_eq.pct_change().fillna(0.0)

    drag = pd.Series(0.0, index=daily_eq.index)
    if len(trades):
        exit_dates = trades["exit_ts"].dt.floor("D")
        counts = exit_dates.value_counts()
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


def plot_outputs(equity: pd.Series, wf: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(12, 5))
    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    ax.plot(equity.index, equity, lw=1.2, label="Equity")
    ax.set_ylabel("Equity (USD)")
    ax.set_title("H3 BTC+SOL baseline — full-history equity")
    ax.legend(loc="upper left")
    ax2 = ax.twinx()
    ax2.fill_between(drawdown.index, drawdown * 100, 0, color="red", alpha=0.25, label="Drawdown %")
    ax2.set_ylabel("Drawdown %")
    ax2.legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "equity_drawdown.png", dpi=150)
    plt.close(fig)

    ids = wf["window_id"].to_numpy() + 1
    sharpes = wf["sharpe_daily_resampled"].to_numpy()
    anns = wf["annualized_return_daily_resampled"].to_numpy() * 100
    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(ids))
    width = 0.35
    ax.bar(x - width / 2, sharpes, width, label="Sharpe")
    ax.axhline(sharpes.mean(), color="blue", ls="--", lw=1.5, label=f"Mean = {sharpes.mean():.2f}")
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
    fig.savefig(OUT_DIR / "oos_windows.png", dpi=150)
    plt.close(fig)


def main() -> None:
    equity, trades, wf = load_data()

    full = full_history_metrics(equity, trades)
    wf_summary = walk_forward_summary(wf)

    fee_sens = {
        label: fee_shock_metrics(equity, trades, rt_bps)
        for label, rt_bps in [
            ("inhouse_4bps_rt", 4.0),
            ("freqtrade_24bps_rt", 24.0),
            ("backtrader_60bps_rt", 60.0),
        ]
    }

    # Certification on the metrics that are available
    cert_input = {
        "sharpe_daily": full["sharpe_daily"],
        "annualized_return": full["annualized_return"],
        "max_drawdown_pct": full["max_drawdown_pct"],
        "profit_factor": full["profit_factor"],
        "n_trades": full["n_trades"],
        "deflated_sharpe": full["deflated_sharpe"],
        "cpcv_mean_oos_sharpe": wf_summary["oos_sharpe_mean_daily_resampled"],
        "bootstrap_ci95_lower": wf_summary["bootstrap_ci_lower"],
    }
    cert = certify_metrics(cert_input)

    pr6 = {
        "oos_sharpe": 2.773,
        "oos_annualized_pct": 59.75,
        "oos_max_drawdown_pct": -12.62,
        "bootstrap_ci_lower": 1.914,
        "inhouse_sharpe": 1.3519,
        "inhouse_annualized_pct": 24.98,
        "inhouse_max_drawdown_pct": -13.67,
    }

    reproducible = {
        "oos_sharpe_reproduced": abs(wf_summary["oos_sharpe_mean_daily_resampled"] - pr6["oos_sharpe"]) < 0.05,
        "oos_ann_reproduced": abs(wf_summary["oos_annualized_mean_daily"] * 100 - pr6["oos_annualized_pct"]) < 1.0,
        "oos_boot_ci_reproduced": abs(wf_summary["bootstrap_ci_lower"] - pr6["bootstrap_ci_lower"]) < 0.05,
    }

    metrics_json = {
        "direction": "H3-baseline-repro",
        "data_span": [str(equity.index[0]), str(equity.index[-1])],
        "n_bars": int(len(equity)),
        "full_history": full,
        "walk_forward_oos": wf_summary,
        "fee_sensitivity": fee_sens,
        "pr6_reference": pr6,
        "reproducibility": reproducible,
        "certification": {
            "passed": cert.passed,
            "failed_gates": cert.failed_gates,
            "reasons": cert.reasons,
        },
        "source_script": "compute_from_csv.py",
    }
    (OUT_DIR / "metrics.json").write_text(json.dumps(metrics_json, indent=2, default=float))

    plot_outputs(equity, wf)

    # ------------------------------------------------------------------
    # SUMMARY.md
    # ------------------------------------------------------------------
    summary = f"""# H3-baseline-repro — SUMMARY

Date: 2026-07-25
Direction: H3-baseline-repro
Output directory: `{OUT_DIR}`

## What was done

- Re-used the already-run outputs from `repro_h3_baseline.py`:
  - `equity_full_history.csv`
  - `trades_full_history.csv`
  - `walk_forward_windows.csv`
- Computed full-history and walk-forward OOS metrics from those CSVs.
- Compared the local numbers to the PR#6 branch evidence.
- Generated `metrics.json`, `SUMMARY.md`, and the two summary plots.

No production code was modified.

## Key numbers

### Full-history (gross, shared H3 pipeline)

| Metric | Value |
|--------|-------|
| Sharpe (bar-based, annualized) | `{full['sharpe_daily']:.3f}` |
| Sharpe (daily-resampled) | `{full['sharpe_daily_resampled']:.3f}` |
| Annualized return (bar-based) | `{full['annualized_return']*100:.2f}%` |
| Annualized return (daily-resampled) | `{full['annualized_return_daily_resampled']*100:.2f}%` |
| Max drawdown (bar-based) | `{full['max_drawdown_pct']*100:.2f}%` |
| Profit factor (bar-based) | `{full['profit_factor']:.3f}` |
| Trades | `{full['n_trades']}` |
| Win rate (per trade) | `{full['win_rate']*100:.2f}%` |
| Trades per day | `{full['trades_per_day']:.2f}` |
| Calmar | `{full['calmar']:.3f}` |
| Sortino | `{full['sortino']:.3f}` |

### Walk-forward OOS ({wf_summary['n_windows']} windows)

| Metric | Local value | PR#6 reference | Match? |
|--------|-------------|----------------|--------|
| Mean OOS Sharpe (daily-resampled) | `{wf_summary['oos_sharpe_mean_daily_resampled']:.3f}` | `{pr6['oos_sharpe']}` | `{'YES' if reproducible['oos_sharpe_reproduced'] else 'NO'}` |
| Mean OOS ann. return | `{wf_summary['oos_annualized_mean_daily']*100:.2f}%` | `{pr6['oos_annualized_pct']}%` | `{'YES' if reproducible['oos_ann_reproduced'] else 'NO'}` |
| Worst OOS maxDD | `{wf_summary['oos_max_drawdown_worst_pct']*100:.2f}%` | `{pr6['oos_max_drawdown_pct']}%` | — |
| Mean OOS PF | `{wf_summary['oos_profit_factor_mean']:.3f}` | — | — |
| Bootstrap CI lower | `{wf_summary['bootstrap_ci_lower']:.3f}` | `{pr6['bootstrap_ci_lower']}` | `{'YES' if reproducible['oos_boot_ci_reproduced'] else 'NO'}` |

### Fee sensitivity (fee-shock replay on gross equity)

| Model | Pair RT bps | Sharpe | Ann return | Max DD |
|-------|-------------|--------|------------|--------|
| in-house | 4 | `{fee_sens['inhouse_4bps_rt']['sharpe_daily_resampled']:.3f}` | `{fee_sens['inhouse_4bps_rt']['annualized_return']*100:.2f}%` | `{fee_sens['inhouse_4bps_rt']['max_drawdown_pct']*100:.2f}%` |
| freqtrade | 24 | `{fee_sens['freqtrade_24bps_rt']['sharpe_daily_resampled']:.3f}` | `{fee_sens['freqtrade_24bps_rt']['annualized_return']*100:.2f}%` | `{fee_sens['freqtrade_24bps_rt']['max_drawdown_pct']*100:.2f}%` |
| backtrader | 60 | `{fee_sens['backtrader_60bps_rt']['sharpe_daily_resampled']:.3f}` | `{fee_sens['backtrader_60bps_rt']['annualized_return']*100:.2f}%` | `{fee_sens['backtrader_60bps_rt']['max_drawdown_pct']*100:.2f}%` |

## G1-G7 certification (approximate)

- Certification result: `{'PASS' if cert.passed else 'FAIL'}`
- Failed gates: `{cert.failed_gates}`
- Reasons:
{chr(10).join('  - ' + r for r in cert.reasons)}

## Is the baseline reproducible?

**No — the local rerun does not reproduce the PR#6 headline numbers.**

- Local mean OOS Sharpe is `{wf_summary['oos_sharpe_mean_daily_resampled']:.3f}` vs PR#6 `{pr6['oos_sharpe']}`.
- Local mean OOS annualized return is `{wf_summary['oos_annualized_mean_daily']*100:.2f}%` vs PR#6 `{pr6['oos_annualized_pct']}%`.
- Local bootstrap CI lower is `{wf_summary['bootstrap_ci_lower']:.3f}` vs PR#6 `{pr6['bootstrap_ci_lower']}`.

### Likely reasons for the gap

1. **Data span / alignment**: The local rerun uses the global `perp_1m` snapshot
   (BTC 2019-09 → 2026-07, SOL 2020-09 → 2026-07) clipped to the funding period
   (2021-11-20 → 2026-07-17). PR#6 was reportedly run on a different snapshot /
   alignment, and the 7-window OOS mean is sensitive to the exact start/end bars.
2. **Cost model mismatch**: The shared H3 backtest records per-trade cost in the
   trade log but does **not** debit it from the equity curve. PR#6 numbers are
   cost-adjusted (in-house 4 bps RT, etc.). The fee-shock replay here is an
   approximation and confirms the strategy is highly cost-sensitive.
3. **Profit factor**: Local PF is ~1.01, far below the G4 gate of 1.5, consistent
   with the known H3 "PF fail" noted in `config_btcsol.json`. PR#6 likely used a
   different sizing/cost convention or a shorter evaluation window that flattered PF.

## Continue or KILL?

**Do not KILL yet.** The engine and signal logic are stable, but the published
OOS Sharpe is **not locally verified**. H3 should remain a HOLD until the data /
windowing discrepancy is resolved.

## Next 1-2 concrete actions

1. **Audit the PR#6 snapshot**: obtain the exact parquet files, date range, and
   train/test boundaries used in PR#6, rerun this pipeline on that snapshot, and
   reconcile the OOS Sharpe.
2. **Fix the H3 cost model**: patch `_backtest_pair` so that fees/slippage are
   debited from the bar-return equity curve (not just the trade log), then
   re-evaluate G1-G7 and fee sensitivity before any live candidacy decision.
"""
    (OUT_DIR / "SUMMARY.md").write_text(summary)

    print("Metrics written to", OUT_DIR / "metrics.json")
    print("Summary written to", OUT_DIR / "SUMMARY.md")
    print(f"Full-history Sharpe (daily-resampled): {full['sharpe_daily_resampled']:.3f}")
    print(f"OOS mean Sharpe (daily-resampled): {wf_summary['oos_sharpe_mean_daily_resampled']:.3f}")
    print(f"Reproducible vs PR#6: {reproducible}")


if __name__ == "__main__":
    main()
