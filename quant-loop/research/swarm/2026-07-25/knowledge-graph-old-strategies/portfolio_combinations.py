#!/usr/bin/env python3
"""Combine H3 baseline, H3 signal-enhance candidate, and a weak ledger strategy.

Uses only the 2024 overlap where the signal-enhance candidate is available.
Methods: equal-weight, risk-parity (inverse vol), correlation-off
(inverse-vol penalised by average pairwise correlation).

Outputs:
    portfolio_results.json  — per-method metrics
    portfolio_results.csv   — compact table
    portfolio_equity.csv    — combined equity curves
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def load_daily_equity(path: Path, date_col: str | None = None, value_col: str = "equity") -> pd.Series:
    df = pd.read_csv(path)
    if date_col is None:
        date_col = [c for c in df.columns if c.lower() in ("timestamp", "ts", "date", "opentime")][0]
    df[date_col] = pd.to_datetime(df[date_col], utc=True).dt.tz_convert(None).dt.normalize()
    df = df.drop_duplicates(subset=[date_col], keep="last").set_index(date_col)
    return df[value_col].sort_index().rename(path.stem)


def load_v72_daily() -> pd.Series:
    s = load_daily_equity(
        Path("/Users/mark/multica/quant-loop/strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/results/equity_A_iter72_BTCUSDT_ETHUSDT.csv"),
        value_col="equity",
    )
    return s.rename("v72_xs_basis_zscore")


def compute_metrics(equity: pd.Series, freq: int = 365) -> dict[str, float]:
    rets = equity.pct_change().dropna()
    if len(rets) < 2 or rets.std(ddof=1) == 0:
        return {"sharpe": 0.0, "ann_return": 0.0, "max_dd": 0.0, "volatility": 0.0}
    ann_ret = (equity.iloc[-1] / equity.iloc[0]) ** (freq / (len(equity) - 1)) - 1.0
    sharpe = (rets.mean() / rets.std(ddof=1)) * np.sqrt(freq)
    running_max = equity.cummax()
    max_dd = float(((equity - running_max) / running_max).min())
    return {
        "sharpe": float(sharpe),
        "ann_return": float(ann_ret),
        "max_dd": float(max_dd),
        "volatility": float(rets.std(ddof=1) * np.sqrt(freq)),
    }


def method_weights(rets: pd.DataFrame, method: str) -> np.ndarray:
    """Return static target weights for the three strategies."""
    vols = rets.std(ddof=1).to_numpy()
    if method == "equal_weight":
        return np.ones(3) / 3.0
    if method == "risk_parity":
        inv = 1.0 / np.maximum(vols, 1e-12)
        return inv / inv.sum()
    if method == "correlation_off":
        corr = rets.corr().to_numpy()
        n = len(vols)
        avg_corr = np.array([np.mean([corr[i, j] for j in range(n) if j != i]) for i in range(n)])
        score = (1.0 / np.maximum(vols, 1e-12)) * np.maximum(0.0, 1.0 - avg_corr)
        return score / score.sum()
    raise ValueError(method)


def apply_weights(rets: pd.DataFrame, weights: np.ndarray) -> pd.Series:
    """Daily-rebalanced portfolio return series."""
    port_rets = rets.to_numpy() @ weights
    equity = 100_000.0 * np.cumprod(1.0 + port_rets)
    return pd.Series(equity, index=rets.index, name="portfolio")


def turnover(rets: pd.DataFrame, weights: np.ndarray) -> float:
    """Approximate daily turnover from drift + rebalance back to target weights."""
    vals = rets.to_numpy() + 1.0  # relative daily growth
    n = len(rets)
    actual = weights.copy()
    total_turnover = 0.0
    for t in range(n):
        # drift today's weights
        actual = actual * vals[t]
        actual = actual / actual.sum()
        # rebalance back to target
        trade = np.abs(actual - weights).sum() / 2.0  # fraction of portfolio traded
        total_turnover += trade
        actual = weights.copy()
    return total_turnover


def cost_sensitivity(port_rets: pd.Series, turnover_annual: float,
                     costs_bps: tuple[float, float, float] = (0.0, 8.0, 22.0)) -> dict[str, dict[str, float]]:
    out = {}
    for cost_bps in costs_bps:
        # Turnover is a fraction of portfolio traded per year; round-trip cost
        # is a fraction of notional.  Simple linear drag model.
        drag = turnover_annual * (cost_bps / 10_000.0)
        net_rets = port_rets - drag / len(port_rets)
        equity = 100_000.0 * np.cumprod(1.0 + net_rets)
        m = compute_metrics(pd.Series(equity, index=port_rets.index))
        out[f"{int(cost_bps)}bps_rt"] = m
    return out


def main() -> None:
    h3_base = load_daily_equity(HERE / "equity_h3_baseline_2024.csv", value_col="equity").rename("h3_baseline")
    h3_cand = load_daily_equity(HERE / "equity_h3_slope_fav_4_stop_0_7_2024.csv", value_col="equity").rename("h3_candidate")
    v72 = load_v72_daily()

    df = pd.concat([h3_base, h3_cand, v72], axis=1).dropna()
    df = df.loc["2024-01-01":"2024-12-31"]
    print(f"Common 2024 trading days: {len(df)}")

    rets = df.pct_change().dropna()
    rets.columns = ["h3_baseline", "h3_candidate", "v72_xs_basis_zscore"]

    corr = rets.corr()
    print("Pairwise correlations:\n", corr)

    results = {}
    equity_curves = pd.DataFrame(index=rets.index)
    for method in ("equal_weight", "risk_parity", "correlation_off"):
        w = method_weights(rets, method)
        equity = apply_weights(rets, w)
        equity_curves[method] = equity
        m = compute_metrics(equity)
        to = turnover(rets, w)
        results[method] = {
            "weights": dict(zip(rets.columns, w.round(4).tolist())),
            "sharpe": m["sharpe"],
            "ann_return": m["ann_return"],
            "max_dd": m["max_dd"],
            "volatility": m["volatility"],
            "turnover_annual": float(to),
            "cost_sensitivity": cost_sensitivity(rets @ w, to),
        }

    # Component metrics on the same period
    component_metrics = {}
    for col in rets.columns:
        equity = 100_000.0 * np.cumprod(1.0 + rets[col])
        component_metrics[col] = compute_metrics(pd.Series(equity, index=rets.index))

    out_json = {
        "period": "2024-01-01 to 2024-12-31",
        "n_days": len(rets),
        "correlation_matrix": corr.round(3).to_dict(),
        "component_metrics": component_metrics,
        "portfolio": results,
    }
    (HERE / "portfolio_results.json").write_text(json.dumps(out_json, indent=2, default=float))

    # CSV summary
    rows = []
    for name, m in component_metrics.items():
        rows.append({
            "method": name,
            "weight_h3_base": 1.0 if name == "h3_baseline" else (0.0 if name != "h3_candidate" else 1.0),
            "weight_h3_candidate": 1.0 if name == "h3_candidate" else 0.0,
            "weight_v72": 1.0 if name == "v72_xs_basis_zscore" else 0.0,
            "sharpe": m["sharpe"],
            "ann_return": m["ann_return"],
            "max_dd": m["max_dd"],
            "volatility": m["volatility"],
            "turnover_annual": 0.0,
            "sharpe_0bps": m["sharpe"],
            "sharpe_8bps": "",
            "sharpe_22bps": "",
        })
    for method, r in results.items():
        cs = r["cost_sensitivity"]
        rows.append({
            "method": method,
            "weight_h3_base": r["weights"]["h3_baseline"],
            "weight_h3_candidate": r["weights"]["h3_candidate"],
            "weight_v72": r["weights"]["v72_xs_basis_zscore"],
            "sharpe": r["sharpe"],
            "ann_return": r["ann_return"],
            "max_dd": r["max_dd"],
            "volatility": r["volatility"],
            "turnover_annual": r["turnover_annual"],
            "sharpe_0bps": cs["0bps_rt"]["sharpe"],
            "sharpe_8bps": cs["8bps_rt"]["sharpe"],
            "sharpe_22bps": cs["22bps_rt"]["sharpe"],
        })

    with (HERE / "portfolio_results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    equity_curves.to_csv(HERE / "portfolio_equity.csv")
    print("Saved portfolio_results.json, portfolio_results.csv, portfolio_equity.csv")


if __name__ == "__main__":
    main()
