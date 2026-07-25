#!/usr/bin/env python3
"""Multi-strategy portfolio experiment.

Combines available strategy equity curves (resampled to daily) with three
weighting schemes: equal-weight, inverse-volatility (risk-parity), and
correlation-off (inverse-vol x (1 - avg correlation)).

Outputs:
  - portfolio_metrics.json
  - portfolio_weights.csv
  - portfolio_correlation_matrix.csv
  - portfolio_equity_curves.csv
  - portfolio_experiment.png
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    HAS_MPL = True
except Exception:
    HAS_MPL = False

OUT = Path(__file__).resolve().parent
ROOT = Path("/Users/mark/multica/quant-loop")

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

SOURCES: dict[str, dict[str, Any]] = {
    "h3_baseline": {
        "path": ROOT
        / "strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/results/equity_winner_atr_mult_1_00_1d.csv",
        "date_col": "timestamp",
        "eq_col": "equity",
        "ret_col": "daily_return",
        "freq": "daily",
        "trades_per_year": 39617 / 4.55,  # metrics.json full-history
        "metrics": {
            "sharpe": 1.47,
            "ann_return": 0.277,
            "max_dd": -0.146,
            "profit_factor": 1.23,
        },
    },
    "vpvr_xs_basis_zscore_15m": {
        "path": ROOT
        / "strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/results/equity_A_iter72_BTCUSDT_ETHUSDT.csv",
        "date_col": "ts",
        "eq_col": "equity",
        "freq": "15min",
        "trades_per_year": 2565.9,  # from metrics.json
        "metrics": {
            "sharpe": 0.25,
            "ann_return": 0.075 / 4.55,
            "max_dd": -0.145,
            "profit_factor": 0.098,
        },
    },
    "momentum_trend_btc_1h": {
        "path": ROOT
        / "strategies/momentum_trend_btc_only_softer_stop_1h_20260712/results/equity_BTCUSDT.csv",
        "date_col": "openTime",
        "eq_col": "equity",
        "freq": "hourly",
        "trades_per_year": 214.4,  # from summary.json
        "metrics": {
            "sharpe": 0.257,
            "ann_return": 0.0036,
            "max_dd": -0.0228,
            "profit_factor": 1.088,
        },
    },
    "pairs_cointegration_1d": {
        "path": ROOT
        / "strategies/pairs_cointegration_1d_20260709/results/portfolio_equity.csv",
        "date_col": "index",  # first column is unnamed date index
        "eq_col": "equity_usd",
        "freq": "daily",
        "trades_per_year": 21 / 1.83,
        "metrics": {
            "sharpe": 6.14,
            "ann_return": 0.022 / 1.83,
            "max_dd": -0.0034,
            "profit_factor": None,
        },
    },
    "donchian_breakout_1d": {
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "path_template": ROOT
        / "strategies/donchian_breakout_atr_1d_20260709/results/equity_{symbol}.csv",
        "date_col": "openTime",
        "eq_col": "equity",
        "freq": "daily",
        "trades_per_year": 40 / 2.17,
        "metrics": {
            "sharpe": 0.26,  # average of three symbols
            "ann_return": 0.0028 / 2.17,
            "max_dd": -0.0035,
            "profit_factor": 1.37,
        },
    },
    "signal_enhance_h3_2024": {
        "synthetic": True,
        "trades_per_year": 704.0,  # 2024 only
        "metrics": {
            "sharpe": 8.07,
            "ann_return": 1.116,
            "max_dd": -0.0315,
            "profit_factor": 1.087,
        },
    },
}


def _parse_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True).dt.tz_convert(None).dt.normalize()


def load_daily_returns(name: str) -> pd.Series:
    src = SOURCES[name]
    if "path_template" in src:
        # multi-symbol strategy -> equal-weight average of daily returns
        rets: list[pd.Series] = []
        for sym in src["symbols"]:
            df = pd.read_csv(str(src["path_template"]).format(symbol=sym))
            df["date"] = _parse_date(df[src["date_col"]])
            daily = (
                df.set_index("date")[src["eq_col"]]
                .resample("D")
                .last()
                .ffill()
                .dropna()
            )
            rets.append(daily.pct_change().dropna())
        combined = pd.concat(rets, axis=1).mean(axis=1)
        return combined

    if src["date_col"] == "index":
        df = pd.read_csv(src["path"], index_col=0)
        df.index = _parse_date(pd.Series(df.index, index=df.index))
        eq = df[src["eq_col"]]
    else:
        df = pd.read_csv(src["path"])
        if "ret_col" in src and src["ret_col"] in df.columns:
            df["date"] = _parse_date(df[src["date_col"]])
            eq = df.set_index("date")[src["ret_col"]]
            return eq.ffill().dropna()
        df["date"] = _parse_date(df[src["date_col"]])
        eq = df.set_index("date")[src["eq_col"]]
    # Build a complete daily calendar and forward-fill so sparse strategies
    # (e.g. low-turnout pairs) contribute zero-return days instead of dropping
    # out of the aligned matrix.
    eq = eq.resample("D").last().ffill().dropna()
    return eq.pct_change().dropna()


def simulate_signal_enhance_h3_2024(
    h3_2024: pd.Series,
    target_sharpe: float = 8.0735,
    target_ann: float = 1.1158,
    target_corr: float = 0.55,
    seed: int = 42,
) -> pd.Series:
    """Generate a synthetic daily return series for the 2024 H3 signal-enhanced
    variant that matches the quick_verify_2024 metrics and a target correlation
    to the H3 baseline in 2024."""
    rng = np.random.default_rng(seed)
    h3 = h3_2024.dropna()
    mu_h3 = h3.mean()
    sigma_h3 = h3.std(ddof=1)

    mu_se = target_ann / 252.0
    sigma_se = mu_se * np.sqrt(252.0) / target_sharpe

    beta = target_corr * sigma_se / sigma_h3 if sigma_h3 > 0 else 0.0
    sigma_noise = sigma_se * np.sqrt(max(1.0 - target_corr**2, 0.0))

    noise = rng.normal(0.0, sigma_noise, size=len(h3))
    rets = mu_se + beta * (h3 - mu_h3) + noise

    se = pd.Series(rets, index=h3.index, name="signal_enhance_h3_2024")
    # small calibration to hit exact target mean/std in this draw
    se = (se - se.mean() + mu_se) / se.std(ddof=1) * sigma_se if se.std(ddof=1) > 0 else se
    return se


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def sharpe(r: pd.Series, periods: int = 252) -> float:
    return float(r.mean() / r.std(ddof=1) * np.sqrt(periods)) if r.std(ddof=1) > 0 else 0.0


def max_drawdown_pct(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min())


def profit_factor(r: pd.Series) -> float | None:
    wins = r[r > 0].sum()
    losses = abs(r[r < 0].sum())
    return float(wins / losses) if losses > 0 else None


def calmar(r: pd.Series, equity: pd.Series, periods: int = 252) -> float:
    ann = r.mean() * periods
    mdd = abs(max_drawdown_pct(equity))
    return float(ann / mdd) if mdd > 0 else 0.0


def portfolio_metrics(rets: pd.Series, equity: pd.Series) -> dict[str, Any]:
    return {
        "sharpe": sharpe(rets),
        "annualized_return": float(rets.mean() * 252),
        "max_drawdown_pct": max_drawdown_pct(equity),
        "profit_factor": profit_factor(rets),
        "calmar": calmar(rets, equity),
        "sortino": float(
            rets.mean() / rets[rets < 0].std(ddof=1) * np.sqrt(252)
        )
        if (rets < 0).any() and rets[rets < 0].std(ddof=1) > 0
        else 0.0,
        "n_days": int(len(rets)),
        "volatility": float(rets.std(ddof=1) * np.sqrt(252)),
    }


# ---------------------------------------------------------------------------
# Weighting schemes
# ---------------------------------------------------------------------------


def equal_weights(n: int) -> np.ndarray:
    return np.ones(n) / n


def risk_parity_weights(cov: pd.DataFrame) -> np.ndarray:
    """Inverse-volatility weights (a common risk-parity proxy)."""
    vols = np.sqrt(np.diag(cov))
    inv = 1.0 / vols
    return inv / inv.sum()


def correlation_off_weights(cov: pd.DataFrame) -> np.ndarray:
    """Inverse-volatility penalised by average pairwise correlation."""
    corr = cov_to_corr(cov)
    vols = np.sqrt(np.diag(cov))
    n = len(vols)
    avg_corr = np.zeros(n)
    for i in range(n):
        others = [j for j in range(n) if j != i]
        avg_corr[i] = corr.iloc[i, others].mean()
    raw = (1.0 / vols) * (1.0 - avg_corr)
    raw = np.maximum(raw, 1e-6)
    return raw / raw.sum()


def cov_to_corr(cov: pd.DataFrame) -> pd.DataFrame:
    std = np.sqrt(np.diag(cov))
    return pd.DataFrame(
        cov.values / np.outer(std, std),
        index=cov.index,
        columns=cov.columns,
    )


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------


def build_portfolio(
    returns: pd.DataFrame, weights: np.ndarray, label: str
) -> tuple[pd.Series, pd.Series, dict[str, Any]]:
    w = pd.Series(weights, index=returns.columns)
    port_rets = (returns * w).sum(axis=1)
    equity = (1.0 + port_rets).cumprod() * 100000.0
    metrics = portfolio_metrics(port_rets, equity)
    metrics["label"] = label
    metrics["weights"] = w.to_dict()
    return port_rets, equity, metrics


def fee_drag(port_rets: pd.Series, weights: pd.Series, extra_bps: float) -> pd.Series:
    """Subtract an approximate daily fee drag from the portfolio returns.

    Annual drag = sum(w_i * trades_per_year_i) * extra_bps / 10000.
    Daily drag  = annual_drag / 252.
    """
    tpy = pd.Series({k: SOURCES[k]["trades_per_year"] for k in weights.index})
    annual_drag = float((weights * tpy).sum() * extra_bps / 10000.0)
    daily_drag = annual_drag / 252.0
    return port_rets - daily_drag


def run_experiment(
    names: list[str],
    signal_enhance: bool = False,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    rets = pd.DataFrame({name: load_daily_returns(name) for name in names})
    if signal_enhance:
        h3_slice = rets["h3_baseline"]
        if start or end:
            h3_slice = h3_slice.loc[start:end]
        se = simulate_signal_enhance_h3_2024(h3_slice)
        rets["signal_enhance_h3_2024"] = se

    # restrict to common dates
    rets = rets.dropna(how="any")
    if start:
        rets = rets[rets.index >= pd.Timestamp(start)]
    if end:
        rets = rets[rets.index <= pd.Timestamp(end)]
    rets = rets.replace([np.inf, -np.inf], np.nan).dropna()

    if rets.empty:
        raise ValueError("No overlapping returns after alignment")

    cov = rets.cov()
    corr = rets.corr()

    schemes = {
        "equal": equal_weights(len(rets.columns)),
        "risk_parity": risk_parity_weights(cov),
        "correlation_off": correlation_off_weights(cov),
    }

    results: dict[str, Any] = {
        "period": {"start": str(rets.index[0]), "end": str(rets.index[-1]), "n_days": len(rets)},
        "strategies": {},
        "portfolios": {},
        "correlation_matrix": corr.round(3).to_dict(),
    }

    # strategy-level metrics
    for col in rets.columns:
        eq = (1.0 + rets[col]).cumprod() * 100000.0
        results["strategies"][col] = portfolio_metrics(rets[col], eq)
        results["strategies"][col]["trades_per_year"] = SOURCES.get(col, {}).get(
            "trades_per_year", None
        )

    equity_curves = pd.DataFrame(index=rets.index)
    weights_rows = []
    for label, w in schemes.items():
        port_rets, equity, metrics = build_portfolio(rets, w, label)
        results["portfolios"][label] = metrics
        equity_curves[label] = equity

        for i, col in enumerate(rets.columns):
            weights_rows.append(
                {"portfolio": label, "strategy": col, "weight": round(w[i], 4)}
            )

        # fee sensitivity
        for extra_bps in (10, 22):
            fr = fee_drag(port_rets, pd.Series(w, index=rets.columns), extra_bps)
            fe = (1.0 + fr).cumprod() * 100000.0
            fm = portfolio_metrics(fr, fe)
            results["portfolios"][label][f"fee_shock_{extra_bps}bps"] = {
                "sharpe": fm["sharpe"],
                "annualized_return": fm["annualized_return"],
                "max_drawdown_pct": fm["max_drawdown_pct"],
            }

    return results, equity_curves, pd.DataFrame(weights_rows), corr


def plot_equity(equity: pd.DataFrame, filename: str, title: str) -> None:
    if not HAS_MPL:
        return
    fig, ax = plt.subplots(figsize=(10, 5))
    for col in equity.columns:
        ax.plot(equity.index, equity[col] / equity[col].iloc[0], label=col)
    ax.legend()
    ax.set_title(title)
    ax.set_xlabel("date")
    ax.set_ylabel("normalized equity")
    fig.tight_layout()
    fig.savefig(OUT / filename, dpi=150)
    plt.close(fig)


def main() -> None:
    # Long-history experiment: H3 + vpvr + momentum (common data back to 2022)
    long_results, long_equity, long_weights, long_corr = run_experiment(
        ["h3_baseline", "vpvr_xs_basis_zscore_15m", "momentum_trend_btc_1h"]
    )

    # Full-history experiment with all available strategies (constrained by
    # pairs/donchian start dates; signal-enhance omitted because it only has
    # 2024 evidence).
    full_results, full_equity, full_weights, full_corr = run_experiment(
        ["h3_baseline", "vpvr_xs_basis_zscore_15m", "momentum_trend_btc_1h", "pairs_cointegration_1d", "donchian_breakout_1d"]
    )

    # 2024 subsample experiment with simulated signal-enhance H3
    y2024_results, y2024_equity, y2024_weights, y2024_corr = run_experiment(
        ["h3_baseline", "vpvr_xs_basis_zscore_15m", "momentum_trend_btc_1h", "donchian_breakout_1d"],
        signal_enhance=True,
        start="2024-01-01",
        end="2024-12-31",
    )

    output = {
        "long_history": long_results,
        "full_history": full_results,
        "2024_subsample": y2024_results,
        "notes": {
            "signal_enhance_h3_2024": "Synthetic daily returns calibrated to quick_verify_2024.json (Sharpe 8.07, ann_return 111.6%, maxDD -3.15%) and target correlation 0.55 to H3 baseline 2024.",
            "fee_shock_assumption": "Extra fee drag = sum(weight_i * trades_per_year_i) * extra_bps / 10000 per year, spread evenly over 252 trading days.",
            "weight_schemes": {
                "equal": "1/N",
                "risk_parity": "inverse daily volatility",
                "correlation_off": "inverse-vol x (1 - average pairwise correlation)",
            },
        },
    }

    (OUT / "portfolio_metrics.json").write_text(json.dumps(output, indent=2, default=str))
    long_equity.to_csv(OUT / "portfolio_equity_curves_long_history.csv")
    full_equity.to_csv(OUT / "portfolio_equity_curves_full_history.csv")
    y2024_equity.to_csv(OUT / "portfolio_equity_curves_2024.csv")
    long_weights.to_csv(OUT / "portfolio_weights_long_history.csv", index=False)
    full_weights.to_csv(OUT / "portfolio_weights_full_history.csv", index=False)
    y2024_weights.to_csv(OUT / "portfolio_weights_2024.csv", index=False)
    long_corr.round(3).to_csv(OUT / "portfolio_correlation_matrix_long_history.csv")
    full_corr.round(3).to_csv(OUT / "portfolio_correlation_matrix_full_history.csv")
    y2024_corr.round(3).to_csv(OUT / "portfolio_correlation_matrix_2024.csv")

    plot_equity(long_equity, "portfolio_equity_long_history.png", "Multi-strategy portfolio — long history (H3+vpvr+momentum)")
    plot_equity(full_equity, "portfolio_equity_full_history.png", "Multi-strategy portfolio — full history")
    plot_equity(y2024_equity, "portfolio_equity_2024.png", "Multi-strategy portfolio — 2024 subsample")

    print("[portfolio] wrote metrics, weights, correlations, equity curves and charts to", OUT)


if __name__ == "__main__":
    main()
