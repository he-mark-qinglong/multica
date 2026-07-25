"""Standalone predictive power analysis of microstructure features."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
from scipy.stats import spearmanr  # type: ignore


def add_forward_returns(ohlcv: pd.DataFrame, feats: pd.DataFrame, horizons: List[int]) -> pd.DataFrame:
    """Add forward log returns at 1m horizons to the feature frame."""
    df = feats.copy()
    close = ohlcv["close"].reindex(df.index)
    log_close = np.log(close)
    for h in horizons:
        df[f"fwd_ret_{h}m"] = log_close.shift(-h) - log_close
    return df


def feature_target_correlations(df: pd.DataFrame, feature_cols: List[str],
                                horizons: List[int]) -> pd.DataFrame:
    """Spearman correlation of each feature with each forward return."""
    rows = []
    for col in feature_cols:
        for h in horizons:
            tgt = f"fwd_ret_{h}m"
            mask = df[col].notna() & df[tgt].notna() & np.isfinite(df[col]) & np.isfinite(df[tgt])
            if mask.sum() < 30:
                rho, p = np.nan, np.nan
            else:
                rho, p = spearmanr(df.loc[mask, col], df.loc[mask, tgt])
            rows.append({
                "feature": col,
                "horizon_m": h,
                "rho": float(rho) if not np.isnan(rho) else None,
                "pvalue": float(p) if not np.isnan(p) else None,
                "n": int(mask.sum()),
            })
    return pd.DataFrame(rows)


def logistic_predictive_power(df: pd.DataFrame, feature_cols: List[str],
                              horizon: int, train_frac: float = 0.7) -> dict:
    """Simple logistic regression sign prediction for a forward return."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    tgt = f"fwd_ret_{horizon}m"
    sub = df[feature_cols + [tgt]].dropna().replace([np.inf, -np.inf], np.nan).dropna()
    if len(sub) < 200:
        return {"error": "insufficient samples"}

    X = sub[feature_cols].to_numpy()
    y = (sub[tgt] > 0).astype(int).to_numpy()

    split = int(len(sub) * train_frac)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    model = LogisticRegression(max_iter=1000, class_weight="balanced")
    model.fit(X_train_s, y_train)
    train_acc = float(model.score(X_train_s, y_train))
    test_acc = float(model.score(X_test_s, y_test))
    baseline = float(y_test.mean())

    return {
        "horizon_m": horizon,
        "n_train": int(len(y_train)),
        "n_test": int(len(y_test)),
        "train_accuracy": train_acc,
        "test_accuracy": test_acc,
        "baseline_accuracy": max(baseline, 1.0 - baseline),
        "coefficients": {feature_cols[i]: float(model.coef_[0][i]) for i in range(len(feature_cols))},
        "intercept": float(model.intercept_[0]),
    }


def run_feature_analysis(ohlcv_map: Dict[str, pd.DataFrame],
                         micro_map: Dict[str, pd.DataFrame],
                         out_dir: Path) -> dict:
    """Produce correlation matrix + logistic results per symbol."""
    horizons = [1, 5, 15]
    feature_cols = [
        "flow_pressure", "flow_pressure_z", "flow_cum_5m", "flow_cum_15m",
        "buy_notional_ratio", "buy_count_ratio", "whale_total_pct",
        "whale_buy_pct", "size_skew", "volume_imbalance",
        "ohlcv_taker_buy_ratio", "range_ratio", "close_loc",
    ]
    summary = {}
    for sym in micro_map:
        feats = micro_map[sym]
        ohlcv = ohlcv_map[sym]
        df = add_forward_returns(ohlcv, feats, horizons)
        corr = feature_target_correlations(df, feature_cols, horizons)
        corr_path = out_dir / f"feature_correlations_{sym}.csv"
        corr.to_csv(corr_path, index=False)
        logistic = {}
        for h in [1, 5, 15]:
            logistic[f"{h}m"] = logistic_predictive_power(df, feature_cols, h)
        summary[sym] = {
            "n_bars": int(len(df)),
            "correlation_csv": str(corr_path),
            "top_correlations": corr.dropna().sort_values("rho", key=lambda x: x.abs(), ascending=False).head(10).to_dict(orient="records"),
            "logistic_regression": logistic,
        }
    return summary
