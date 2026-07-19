"""
spillover_fevd.py — Diebold-Yilmaz (2012) generalized forecast-error variance
decomposition on a multivariate realized-volatility panel.

Reference:
    Diebold, F. X. & Yilmaz, K. (2012). "Better to Give than to Receive:
    Predictive Directional Measurement of Volatility Spillovers."
    International Journal of Forecasting 28(1), 57-66.

This module is a research artifact — it is the engine that the SMA-35000
strategy would call. It is NOT a strategy itself; it returns the
spillover matrix + NET/FROM/TO vectors + total-connectedness series.

API
---
    spillover_engine.fit_spillover(log_rv_panel, p, H, window=..., step=...)
        → (net_df, from_df, to_df, total_series, spillover_matrices)
    spillover_engine.spillover_metrics(psi_norm)
        → (to, from_, net, total)

Notes
-----
* The generalized FEVD follows Pesaran-Shin (1998) — robust to VAR ordering.
* Companion-form recursion for MA coefficients Psi_h, h=1..H.
* Row-normalized per DY 2012 Eq.(4).
* Tested on BTC/ETH/SOL 1h log-RV; see feasibility_check.py.

Limitations surfaced by the feasibility check (Gate D, Gate F)
-------------------------------------------------------------
* At N=3, the framework is degenerate (1 constraint per row sums to 1).
* Total connectedness is stable (mean 0.61, std 0.02); mean NET_i is
  ≈ 1-2 SE for ETH/SOL → not statistically distinguishable from
  refit noise for the "wrong" assets. BTC-vs-SOL Spearman across
  24 rolling 90d windows is -0.65 (mechanical: NET sums to 0); the
  true rank-stability signal is WEAK.
* Magnitudes halve from p=1 (μ_BTC=+0.044) to p=4 (μ_BTC=+0.020) →
  signal at lag-1 is microstructural, not fundamental spillover.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

try:
    from statsmodels.tsa.api import VAR
except Exception as _e:  # pragma: no cover
    VAR = None
    _IMPORT_ERR = _e


def generalized_fevd_psi(Phi: np.ndarray, Sigma: np.ndarray, H: int = 10) -> np.ndarray:
    """DY 2012 generalized FEVD with row-normalization.

    Parameters
    ----------
    Phi : (K, K, p) VAR coefficient slabs in lag order
    Sigma : (K, K) residual covariance
    H : forecast horizon (default 10)

    Returns
    -------
    psi_norm : (K, K) row-normalized FEVD matrix
    """
    Phi = np.asarray(Phi, dtype=float)
    Sigma = np.asarray(Sigma, dtype=float)
    K, p = Phi.shape[0], Phi.shape[2]
    F = np.zeros((K * p, K * p))
    F[:K, :K * p] = np.hstack([Phi[:, :, j] for j in range(p)])
    if p > 1:
        F[K:, :K * (p - 1)] = np.eye(K * (p - 1))
    Fh = np.eye(K * p)
    sigma_diag = np.diag(Sigma)
    theta = np.zeros((K, K))
    for h in range(1, H + 1):
        Fh = Fh @ F
        A = np.asarray(Fh[:K, :K]) @ Sigma
        for i in range(K):
            for j in range(K):
                theta[i, j] += (A[i, j] ** 2) / sigma_diag[j]
    row_sum = theta.sum(axis=1, keepdims=True)
    return theta / row_sum


def spillover_metrics(psi_norm: np.ndarray):
    """TO, FROM, NET_i (asset-level directional spillover) and total connectedness.

    Returns
    -------
    to, from_, net : (K,) vectors
    total : scalar, the Diebold-Yilmaz spillover index (mean off-diagonal mass)
    """
    diag = np.diag(psi_norm)
    to = psi_norm.sum(axis=0) - diag
    from_ = psi_norm.sum(axis=1) - diag
    net = to - from_
    total = to.sum() / psi_norm.shape[0]
    return to, from_, net, total


def fit_rolling_var_spillover(
    panel: pd.DataFrame,
    p: int,
    H: int = 10,
    window_bars: int | None = None,
    step_bars: int | None = None,
):
    """Fit rolling-window VAR(p) and compute the DY spillover table per window.

    Parameters
    ----------
    panel : (T, K) log-realized-volatility panel, no NaN
    p : VAR lag order
    H : forecast horizon for FEVD
    window_bars : size of rolling window
    step_bars : slide between consecutive windows

    Returns
    -------
    net_df, from_df, to_df : (n_windows × K) DataFrames
    total_series : (n_windows,) total connectedness series
    matrices : list of (K, K) row-normalized FEVD matrices, one per window
    """
    if VAR is None:  # pragma: no cover
        raise RuntimeError("statsmodels is required for VAR fitting")
    panel = panel.replace([np.inf, -np.inf], np.nan).dropna()
    K = panel.shape[1]
    if window_bars is None:
        window_bars = panel.shape[0]
    if step_bars is None:
        step_bars = window_bars
    starts = list(range(0, panel.shape[0] - window_bars + 1, step_bars))
    net_rows, from_rows, to_rows, total_vals, mats = [], [], [], [], []
    for s in starts:
        sub = panel.iloc[s : s + window_bars]
        if sub.shape[0] < max(50, 4 * p * K):
            continue
        try:
            res = VAR(sub).fit(p)
            Phi = np.zeros((K, K, p))
            for j in range(p):
                Phi[:, :, j] = np.asarray(res.coefs[j])
            Sigma = np.asarray(res.sigma_u)
            if not np.all(np.isfinite(Sigma)):
                continue
            psi = generalized_fevd_psi(Phi, Sigma, H=H)
            to, from_, net, total = spillover_metrics(psi)
            net_rows.append(net); from_rows.append(from_); to_rows.append(to)
            total_vals.append(total); mats.append(psi)
        except Exception:
            continue
    syms = panel.columns.tolist()
    net_df = pd.DataFrame(net_rows, columns=syms).reset_index(drop=True)
    from_df = pd.DataFrame(from_rows, columns=syms).reset_index(drop=True)
    to_df = pd.DataFrame(to_rows, columns=syms).reset_index(drop=True)
    total_series = pd.Series(total_vals, name="total_connectedness").reset_index(drop=True)
    return net_df, from_df, to_df, total_series, mats


__all__ = [
    "generalized_fevd_psi",
    "spillover_metrics",
    "fit_rolling_var_spillover",
]
