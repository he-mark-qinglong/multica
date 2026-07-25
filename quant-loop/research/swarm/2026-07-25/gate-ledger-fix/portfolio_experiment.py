#!/usr/bin/env python3
"""Multi-strategy portfolio combination experiment.

Reproduces the 2024-window analysis for gate-ledger-fix.

Candidates:
- h3_baseline: actual daily equity from H3 (equity_winner_atr_mult_1_00_1d.csv)
- signal_slope_fav_4, signal_slope_fav_4_stop_0_7, signal_adverse_stop_0_7:
  simulated Gaussian daily returns matching quick_verify_2024.json
- pairs_cointegration_1d: actual daily equity from strategy results
- vpvr_xs_basis_zscore_15m: actual 15m equity resampled to daily
- vpvr_xs_smart_routing_15m: actual 15m equity resampled to daily

Combination methods:
- equal_weight
- risk_parity (inverse vol with 5% annual vol floor)
- decorrelation (1/avg_abs_corr * 1/sigma)

Outputs:
- portfolio_results.json
- weights_*.csv
- portfolio_summary.md
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path(__file__).parent
REPO = OUT_DIR.parents[4]  # .../quant-loop
H3_EQUITY = REPO / "strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/results/equity_winner_atr_mult_1_00_1d.csv"
QUICK_2024 = REPO.parent / "signal-enhance-h3/quick_verify_2024.json"
PAIRS_EQUITY = REPO / "strategies/pairs_cointegration_1d_20260709/results/portfolio_equity.csv"
BASIS_EQUITY = REPO / "strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/results/equity_A_iter72_BTCUSDT_ETHUSDT.csv"
SMART_EQUITY = REPO / "strategies/vpvr_xs_smart_routing_15m_20260715/results/equity_15m_BTCUSDT.csv"


def load_h3() -> pd.Series:
    df = pd.read_csv(H3_EQUITY, parse_dates=["timestamp"], index_col="timestamp")
    return df["equity"].sort_index()


def returns_from_equity(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def synthetic_returns(target_sharpe: float, target_ann_return: float, n_days: int, seed: int) -> pd.Series:
    rng = np.random.default_rng(seed)
    mu = target_ann_return / 365
    sigma = mu / target_sharpe * math.sqrt(365) if target_sharpe != 0 else 0.001
    rets = rng.normal(mu, sigma, n_days)
    return pd.Series(rets, index=pd.date_range("2024-01-01", periods=n_days, freq="D"))


def load_or_simulate(name: str, path: Path | None, source: str, n_days: int, seed: int, metrics: dict | None = None) -> pd.Series:
    if path and path.exists():
        df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
        if "equity" in df.columns:
            series = df["equity"]
        elif "close" in df.columns:
            series = df["close"]
        else:
            series = df.iloc[:, 0]
        series = series.sort_index()
        # resample to daily if needed
        if pd.infer_freq(series.index) in ("15T", "15min", "5T", "5min", "1T", "1min"):
            series = series.resample("D").last().dropna()
        rets = returns_from_equity(series)
        rets = rets.reindex(pd.date_range("2024-01-01", periods=n_days, freq="D"), fill_value=0.0)
        return rets
    if metrics:
        return synthetic_returns(metrics["sharpe_daily_resampled"], metrics["annualized_return_daily"], n_days, seed)
    raise ValueError(f"No data for {name}")


def stats(rets: pd.Series, daily_fee: float = 0.0) -> dict:
    net = rets - daily_fee
    sharpe = net.mean() / net.std() * math.sqrt(365) if net.std() > 0 else 0.0
    cumulative = (1 + net).cumprod()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    max_dd = drawdown.min()
    ann_ret = (1 + net.mean()) ** 365 - 1
    ann_vol = net.std() * math.sqrt(365)
    turnover = net.abs().sum()  # proxy over window
    return {
        "sharpe_daily_resampled": sharpe,
        "annualized_return": ann_ret,
        "annualized_volatility": ann_vol,
        "max_drawdown": max_dd,
        "turnover_proxy": turnover,
        "n_days": len(net),
    }


def equal_weight(rets_df: pd.DataFrame) -> pd.DataFrame:
    weights = pd.DataFrame(1.0 / len(rets_df.columns), index=rets_df.index, columns=rets_df.columns)
    return weights


def risk_parity(rets_df: pd.DataFrame, vol_floor_annual: float = 0.05) -> pd.DataFrame:
    weights = pd.DataFrame(index=rets_df.index, columns=rets_df.columns, dtype=float)
    for i in range(len(rets_df)):
        hist = rets_df.iloc[: max(1, i)]
        vol = hist.std() * math.sqrt(365)
        vol = vol.clip(lower=vol_floor_annual)
        w = 1.0 / vol.replace(0, np.nan)
        w = w.dropna()
        w = w / w.sum()
        weights.iloc[i] = w.reindex(rets_df.columns).fillna(0.0)
    return weights


def decorrelation(rets_df: pd.DataFrame, vol_floor_annual: float = 0.05) -> pd.DataFrame:
    weights = pd.DataFrame(index=rets_df.index, columns=rets_df.columns, dtype=float)
    for i in range(2, len(rets_df)):
        hist = rets_df.iloc[:i]
        corr = hist.corr().abs()
        avg_corr = corr.mean(axis=1)
        vol = hist.std() * math.sqrt(365)
        vol = vol.clip(lower=vol_floor_annual)
        w = (1.0 / avg_corr.replace(0, np.nan)) * (1.0 / vol.replace(0, np.nan))
        w = w.dropna()
        w = w / w.sum()
        weights.iloc[i] = w.reindex(rets_df.columns).fillna(0.0)
    # first two rows: equal weight fallback
    weights.iloc[:2] = 1.0 / len(rets_df.columns)
    return weights


def apply_weights(rets_df: pd.DataFrame, weights: pd.DataFrame) -> pd.Series:
    return (rets_df * weights).sum(axis=1)


def portfolio_turnover(weights: pd.DataFrame) -> float:
    return 0.5 * weights.diff().abs().sum(axis=1).mean() * 365


def main():
    h3_eq = load_h3()
    h3_2024 = h3_eq["2024-01-01":"2024-12-31"]
    h3_rets = returns_from_equity(h3_2024)
    n_days = len(h3_rets)

    with open(QUICK_2024) as f:
        variants = {v["variant"]: v for v in json.load(f)}

    candidates = {
        "h3_baseline": h3_rets,
        "signal_slope_fav_4": synthetic_returns(
            variants["slope_fav_4"]["sharpe_daily_resampled"],
            variants["slope_fav_4"]["annualized_return_daily"],
            n_days,
            seed=20241,
        ),
        "signal_slope_fav_4_stop_0_7": synthetic_returns(
            variants["slope_fav_4_stop_0_7"]["sharpe_daily_resampled"],
            variants["slope_fav_4_stop_0_7"]["annualized_return_daily"],
            n_days,
            seed=20242,
        ),
        "signal_adverse_stop_0_7": synthetic_returns(
            variants["adverse_stop_0_7"]["sharpe_daily_resampled"],
            variants["adverse_stop_0_7"]["annualized_return_daily"],
            n_days,
            seed=20243,
        ),
    }

    # Actual strategies from ledger where equity files exist
    if PAIRS_EQUITY.exists():
        candidates["pairs_cointegration_1d"] = load_or_simulate(
            "pairs_cointegration_1d", PAIRS_EQUITY, "actual", n_days, seed=0
        )
    if BASIS_EQUITY.exists():
        candidates["vpvr_xs_basis_zscore_15m"] = load_or_simulate(
            "vpvr_xs_basis_zscore_15m", BASIS_EQUITY, "actual", n_days, seed=0
        )
    if SMART_EQUITY.exists():
        candidates["vpvr_xs_smart_routing_15m"] = load_or_simulate(
            "vpvr_xs_smart_routing_15m", SMART_EQUITY, "actual", n_days, seed=0
        )

    df = pd.DataFrame(candidates).dropna()
    df = df.loc[:, (df.std() > 0)]  # drop constant columns

    results = {
        "experiment_window": {"start": "2024-01-01", "end": "2024-12-31", "n_days": n_days},
        "assumptions": {
            "fee_model": "Portfolio fee = half sum of abs daily weight changes * fee_rt. Standalone fee = trades_per_year * fee_rt (full-notional conservative).",
            "simulated_variants": "Gaussian daily returns matching reported 2024 Sharpe and ann return.",
            "vol_floor": "5% annual volatility floor in risk parity / decorrelation.",
        },
        "standalone": {},
        "portfolios": {},
        "correlation_matrix": df.corr().to_dict(),
    }

    # Standalone stats
    for name, rets in df.items():
        s = stats(rets)
        results["standalone"][name] = s
        results["standalone"][name]["fee_sensitivity"] = {}
        for fee_rt in (0.0, 8e-4, 22e-4):
            daily_fee = fee_rt / 365
            results["standalone"][name]["fee_sensitivity"][f"{int(fee_rt*1e4)}bps"] = stats(rets, daily_fee)

    # Portfolio combinations
    for method_name, method_fn in [
        ("equal_weight", equal_weight),
        ("risk_parity", risk_parity),
        ("decorrelation", decorrelation),
    ]:
        weights = method_fn(df)
        weights = weights.div(weights.sum(axis=1), axis=0).fillna(0.0)
        combo_rets = apply_weights(df, weights)
        turnover = portfolio_turnover(weights)
        results["portfolios"][method_name] = {
            "gross": stats(combo_rets),
            "avg_weights": weights.mean().to_dict(),
            "turnover_annual": turnover,
        }
        for fee_rt in (0.0, 8e-4, 22e-4):
            daily_fee = turnover * fee_rt / 365
            results["portfolios"][method_name][f"fee_{int(fee_rt*1e4)}bps"] = stats(combo_rets, daily_fee)
        weights.to_csv(OUT_DIR / f"weights_{method_name}.csv")

    (OUT_DIR / "portfolio_results.json").write_text(json.dumps(results, indent=2, default=float))
    print(json.dumps(results, indent=2, default=float))
    print(f"\nResults written to {OUT_DIR}")


if __name__ == "__main__":
    main()
