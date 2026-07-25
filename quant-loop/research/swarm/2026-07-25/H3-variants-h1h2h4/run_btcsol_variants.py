"""Fast BTC+SOL-only H1/H2/H3/H4 variant study.

Scope:
- Symbols: BTCUSDT + SOLUSDT only.
- Pair: BTCUSDT/SOLUSDT.
- Costs: ratified 22 bps RT per symbol (4 bps fee + 7 bps slippage per side).
- Optionally also 4 bps RT per-symbol for comparison with H3 PR#6 evidence.
- Full history from 2022-01-01 (matches campaign ledger evidence window).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = OUT_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

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

import mtf_xs_pairs_base_20260718 as _base  # noqa: E402

_orig_build_portfolio = _base.build_portfolio


def _build_portfolio_with_nbars(*args, **kwargs):
    r = _orig_build_portfolio(*args, **kwargs)
    if "n_bars" not in r:
        r["n_bars"] = len(r["bar_return"])
    return r


_base.build_portfolio = _build_portfolio_with_nbars

# The base _backtest_pair records cost in the trade log but does NOT deduct it
# from the per-bar return that feeds portfolio equity. Patch it so that the
# ratified 22 bps RT cost is actually reflected in Sharpe/ann/MDD metrics.
_orig_backtest_pair = _base._backtest_pair


def _backtest_pair_with_cost(sig, pair, sizing_scale=None, fee_bps=1.0, slip_bps=1.0):
    res = _orig_backtest_pair(sig, pair, sizing_scale=sizing_scale, fee_bps=fee_bps, slip_bps=slip_bps)
    trades = res.get("trades", [])
    if not trades:
        return res
    # Pair-RT cost in *bar-return* units. bar_return uses HALF notional per
    # leg (pnl = pos * (a_ret - b_ret) / 2), so a round trip costs
    # 2 legs x 2 fills x 0.5 notional x (fee+slip) = 2*(fee+slip) bps here.
    # The trade log's pnl_pct is in FULL-spread units, where 2*2*(fee+slip)
    # is correct — using that formula here double-counted the cost and
    # produced the impossible Sharpe ~ -43 in results/metrics.json
    # (bug fixed 2026-07-25, SMA-35145 follow-up).
    pair_rt_cost = 2.0 * (float(fee_bps) + float(slip_bps)) / 10_000.0
    half_cost = pair_rt_cost / 2.0
    bar_ret = np.asarray(res["bar_return"], dtype=float).copy()
    idx = sig["a"].index
    for t in trades:
        try:
            ei = int(idx.get_loc(pd.Timestamp(t["entry_ts"])))
            xi = int(idx.get_loc(pd.Timestamp(t["exit_ts"])))
        except Exception:
            continue
        # entry bar earns no return; fill cost debited at ei+1. exit cost at xi.
        if ei + 1 < len(bar_ret):
            bar_ret[ei + 1] -= half_cost
        if xi < len(bar_ret):
            bar_ret[xi] -= half_cost
    res["bar_return"] = bar_ret
    return res


_base._backtest_pair = _backtest_pair_with_cost


DATA_1M_DIR = ROOT / "data" / "perp_1m"
FUNDING_DIR = ROOT / "data" / "funding"

COST_FEE_BPS = 4.0
COST_SLIP_BPS = 7.0


def load_1m(symbols):
    out = {}
    for sym in symbols:
        p = DATA_1M_DIR / f"{sym}_1m.parquet"
        df = pd.read_parquet(p)
        ts = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
        df.index = pd.DatetimeIndex(ts).tz_convert(None)
        df.index.name = "openTime"
        df = df.sort_index()[["open", "high", "low", "close", "volume"]]
        out[sym] = df
        print(f"  loaded {sym}: {len(df):,} rows, {df.index[0]} -> {df.index[-1]}")
    return out


def load_funding(symbols):
    out = {}
    for sym in symbols:
        p = FUNDING_DIR / f"{sym}.parquet"
        df = pd.read_parquet(p)
        ts = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
        s = pd.Series(df["fundingRate"].astype(float).to_numpy(), index=ts, name="fundingRate")
        s = s.sort_index()
        s.index = pd.DatetimeIndex(s.index).tz_convert(None)
        out[sym] = s
        print(f"  loaded funding {sym}: {len(s):,} rows, {s.index[0]} -> {s.index[-1]}")
    return out


def load_config(hyp: str) -> Dict[str, Any]:
    if hyp in ("H1", "H2", "H3"):
        cfg_path = STRATEGIES_DIR / f"mtf_xs_pairs_1m_15m_2h_{hyp.lower()}_20260718" / "config.json"
        cfg = json.loads(cfg_path.read_text())
    elif hyp == "H4":
        cfg = {
            "strategy": "mtf_xs_pairs_1m_15m_2h_h4_20260718",
            "iteration": 107,
            "campaign": "SMA-34875 mtf-1m-15m-2h H4 BTC+SOL",
            "hypothesis": "H4",
            "date": "2026-07-18",
            "primary_timeframe": "1m",
            "filter_timeframe": "15m",
            "regime_timeframe": "2h",
            "description": "H4: 1m cross-pair z-score + 15m EMA-8/21 direction filter + 2h trend cap.",
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
        }
    else:
        raise ValueError(hyp)

    # Override to BTC+SOL only
    cfg["instruments"] = ["BTCUSDT", "SOLUSDT"]
    cfg["pairs"] = ["BTCUSDT/SOLUSDT"]
    cfg["fees_bps_per_side"] = COST_FEE_BPS
    cfg["slippage_bps_per_side"] = COST_SLIP_BPS
    return cfg


def portfolio_index(result, start_ts: pd.Timestamp):
    n = result["portfolio"]["n_bars"]
    return pd.date_range(start_ts, periods=n, freq="1min")


def full_history_metrics(result, cfg):
    starting = float(cfg.get("starting_capital_usd", 100_000.0))
    port = result["portfolio"]
    n_bars = int(port["n_bars"])
    if n_bars == 0:
        return {"sharpe_daily": 0.0, "annualized_return": 0.0, "max_drawdown_pct": 0.0,
                "profit_factor": 0.0, "n_trades": 0, "n_bars": 0, "win_rate": 0.0,
                "calmar": 0.0, "sortino": 0.0}
    bar_ret = np.asarray(port["bar_return"], dtype=float)
    eq = np.empty(n_bars)
    eq[0] = starting
    for i in range(1, n_bars):
        eq[i] = eq[i - 1] * (1.0 + bar_ret[i])
    # Use actual start timestamp from the first pair's data.
    start_ts = result["per_pair"][0]["span_start"]
    start_ts = pd.Timestamp(start_ts) if start_ts else pd.Timestamp("2022-01-01")
    daily_eq = pd.Series(eq, index=portfolio_index(result, start_ts)).resample("1D").last().dropna()
    n_trades = sum(len(pp["trades"]) for pp in result["per_pair"])
    trade_pnls = [t["pnl_pct"] for pp in result["per_pair"] for t in pp["trades"]]
    m = compute_metrics(daily_eq, n_trades=n_trades, freq_per_year=365, trade_pnls=trade_pnls)
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


def gate_dict(wfo, fh, cfg):
    n_bars_total = int(fh.get("n_bars", 0))
    return {
        "sharpe_daily": float(wfo["oos_sharpe_mean_daily_resampled"]),
        "annualized_return": float(wfo["oos_annualized_mean_daily"]),
        "max_drawdown_pct": float(wfo["oos_max_drawdown_worst"]),
        "profit_factor": float(fh["profit_factor"]),
        "n_trades": int(fh["n_trades"]),
        "bootstrap_ci95_lower": float(wfo["bootstrap_ci_lower"]),
        "deflated_sharpe": float(
            deflated_sharpe(
                float(wfo["oos_sharpe_mean_daily_resampled"]),
                n_trials=4,
                sample_len=max(n_bars_total, 2),
            )
        ),
    }


def check_gates(metrics):
    res = certify_metrics(metrics, strict=False)
    return {"passed": res.passed, "failed_gates": res.failed_gates, "reasons": res.reasons}


def cost_sensitivity(d1m, funding, cfg, common_index):
    rows = []
    syms = list(cfg["instruments"])
    d = {s: d1m[s].reindex(common_index) for s in syms}
    f = {s: funding[s] for s in syms}
    for rt in [4.0, 12.0, 22.0, 32.0, 44.0, 60.0]:
        base = dict(cfg)
        half = rt / 2.0
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


def plot_equity_curves(records, out_path):
    fig, ax = plt.subplots(figsize=(10, 5))
    for hyp, r in records.items():
        eq = r["equity_daily"]
        if len(eq) == 0:
            continue
        norm = eq / eq.iloc[0]
        ax.plot(norm.index, norm.values, label=hyp, linewidth=1.2)
    ax.set_yscale("log")
    ax.set_title("BTC+SOL portfolio equity at 22 bps RT/symbol (daily, log scale)")
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
    anns = [records[h]["oos"]["oos_annualized_mean_daily"] for h in labels]
    mdds = [abs(records[h]["oos"]["oos_max_drawdown_worst"]) for h in labels]
    x = np.arange(len(labels))
    width = 0.25
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(x - width, sharpes, width, label="OOS Sharpe")
    ax.bar(x, anns, width, label="OOS ann. return")
    ax.bar(x + width, mdds, width, label="|OOS max DD|")
    ax.axhline(1.0, color="k", linestyle="--", linewidth=0.8)
    ax.axhline(0.15, color="gray", linestyle="--", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_title("Walk-forward OOS metrics, BTC+SOL (daily-resampled)")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_cost_sensitivity(df, out_path):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(df["per_symbol_rt_bps"], df["sharpe_daily"], marker="o", label="H3 Sharpe")
    ax.axhline(1.0, color="k", linestyle="--", linewidth=0.8)
    ax.set_xlabel("Per-symbol round-trip cost (bps)")
    ax.set_ylabel("Full-history daily Sharpe")
    ax.set_title("H3 BTC+SOL cost sensitivity (ratified = 22 bps/symbol)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def run_hypothesis(hyp, d1m, funding, common_index):
    print(f"\n=== {hyp} (BTC+SOL) ===")
    cfg = load_config(hyp)
    syms = list(cfg["instruments"])
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

    start_ts = pd.Timestamp(res["per_pair"][0]["span_start"]) if res["per_pair"][0]["span_start"] else pd.Timestamp("2022-01-01")
    n_bars = res["portfolio"]["n_bars"]
    bar_ret = np.asarray(res["portfolio"]["bar_return"], dtype=float)
    eq = np.empty(n_bars)
    starting = float(cfg.get("starting_capital_usd", 100_000.0))
    eq[0] = starting
    for i in range(1, n_bars):
        eq[i] = eq[i - 1] * (1.0 + bar_ret[i])
    daily_eq = pd.Series(eq, index=portfolio_index(res, start_ts)).resample("1D").last().dropna()

    return {
        "hypothesis": hyp,
        "config": cfg,
        "full_history": fh,
        "oos": wfo,
        "gates": gates,
        "gate_metrics": gm,
        "equity_daily": daily_eq,
    }


def main():
    print("Loading BTC+SOL data pool …")
    symbols = ["BTCUSDT", "SOLUSDT"]
    d1m_full = load_1m(symbols)
    funding_full = load_funding(symbols)

    # Truncate to campaign evidence window for speed + comparability.
    start_cut = pd.Timestamp("2022-01-01")
    d1m = {s: df.loc[df.index >= start_cut] for s, df in d1m_full.items()}
    common_index = d1m["BTCUSDT"].index.intersection(d1m["SOLUSDT"].index)
    print(f"  common index: {len(common_index):,} bars, {common_index[0]} -> {common_index[-1]}")

    records = {}
    for hyp in ("H1", "H2", "H3", "H4"):
        records[hyp] = run_hypothesis(hyp, d1m, funding_full, common_index)

    print("\n=== H3 cost sensitivity (BTC+SOL) ===")
    h3_cfg = load_config("H3")
    cost_df = cost_sensitivity(d1m, funding_full, h3_cfg, common_index)
    print(cost_df.to_string(index=False))
    cost_df.to_csv(RESULTS_DIR / "h3_cost_sensitivity.csv", index=False)

    summary = {
        "note": "BTC+SOL-only H1-H4 comparison at ratified 22 bps RT per symbol, 2022-01-01 onward.",
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

    for h, r in records.items():
        r["equity_daily"].to_frame("equity").to_csv(RESULTS_DIR / f"equity_{h}_daily.csv")

    plot_equity_curves(records, RESULTS_DIR / "equity_curves.png")
    plot_oos_metrics(records, RESULTS_DIR / "oos_metrics.png")
    plot_cost_sensitivity(cost_df, RESULTS_DIR / "h3_cost_sensitivity.png")

    print(f"\nResults written to {RESULTS_DIR}")


if __name__ == "__main__":
    main()
