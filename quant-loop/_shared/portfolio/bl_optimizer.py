"""Black-Litterman optimizer — incorporate subjective views into portfolio weights.

The Black-Litterman model starts from market-equilibrium returns and
adjusts them with investor views, producing posterior expected returns
that drive mean-variance optimization.

Three-optimizer portfolio toolkit: ERC (existing) + HRP (existing) + BL (new).

Usage:
    bl = BlackLitterman()
    result = bl.optimize(
        cov_matrix=cov,          # NxN covariance
        market_weights=w_mkt,    # N market-cap weights
        views=ViewMatrix(
            P=[[1, 0, -1],         # "BTC outperforms SOL by 2%"
               [0, 1, 0]],          # "ETH returns 5%"
            Q=[0.02, 0.05],
            confidences=[0.5, 0.3],
        ),
        assets=["BTC", "ETH", "SOL"],
    )
    print(result.posterior_weights)

References:
  - Black & Litterman (1991) "Global Asset Allocation with Equities, Bonds, and Currencies"
  - Idzorek (2005) "A Step-by-Step Guide to the Black-Litterman Model"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize


@dataclass(frozen=True)
class ViewMatrix:
    """Investor views for Black-Litterman.

    Attributes:
        P: K×N picking matrix. Each row selects assets for one view.
        Q: K×1 view vector. Expected excess return for each view.
        confidences: K confidence levels (0-1). Higher = stronger view.
            If None, uses Idzorek's method with equal confidence.
    """
    P: np.ndarray
    Q: np.ndarray
    confidences: Sequence[float] | None = None


@dataclass(frozen=True)
class BLResult:
    """Black-Litterman optimization result."""
    posterior_returns: np.ndarray    # posterior expected returns
    posterior_weights: np.ndarray    # optimized weights
    equilibrium_returns: np.ndarray  # market-implied returns (prior)
    assets: list
    views_applied: int
    method: str                      # "mean_variance" or "maximum_sharpe"


class BlackLitterman:
    """Black-Litterman portfolio optimizer.

    Combines market equilibrium with investor views to produce
    stable, well-conditioned portfolio weights that don't suffer
    from the extreme sensitivity of pure mean-variance optimization.
    """

    def __init__(
        self,
        risk_aversion: float = 2.5,
        tau: float = 0.05,
    ):
        """
        Args:
            risk_aversion: δ (delta) — market risk aversion coefficient.
                Typical: 2-4 for institutional, ~2.5 default.
            tau: uncertainty scaling of prior. Small (0.025-0.05) means
                high confidence in the equilibrium prior.
        """
        self.delta = risk_aversion
        self.tau = tau

    def equilibrium_returns(
        self, cov_matrix: np.ndarray, market_weights: np.ndarray
    ) -> np.ndarray:
        """Compute market-implied equilibrium returns.

        π = δ · Σ · w_mkt

        Args:
            cov_matrix: NxN covariance matrix.
            market_weights: Nx1 market portfolio weights.

        Returns:
            Nx1 equilibrium excess returns.
        """
        return self.delta * cov_matrix @ market_weights

    def optimize(
        self,
        cov_matrix: np.ndarray | pd.DataFrame,
        market_weights: np.ndarray | pd.Series,
        views: ViewMatrix,
        assets: list | None = None,
        method: str = "maximum_sharpe",
        weight_bounds: tuple = (0.0, 1.0),
    ) -> BLResult:
        """Run Black-Litterman optimization.

        Args:
            cov_matrix: NxN covariance matrix.
            market_weights: N market portfolio weights.
            views: investor views (P, Q, confidences).
            assets: asset names.
            method: "mean_variance" or "maximum_sharpe".
            weight_bounds: (min, max) weight bounds.

        Returns:
            BLResult with posterior returns and weights.
        """
        # Convert to numpy
        if isinstance(cov_matrix, pd.DataFrame):
            if assets is None:
                assets = list(cov_matrix.columns)
            cov_matrix = cov_matrix.values
        if isinstance(market_weights, pd.Series):
            market_weights = market_weights.values

        Sigma = np.asarray(cov_matrix, dtype=float)
        w_mkt = np.asarray(market_weights, dtype=float)
        N = len(w_mkt)

        # Step 1: Equilibrium returns (prior)
        pi = self.equilibrium_returns(Sigma, w_mkt)

        # Step 2: View matrices
        P = np.atleast_2d(np.asarray(views.P, dtype=float))
        Q = np.atleast_1d(np.asarray(views.Q, dtype=float))
        K = len(Q)

        # Step 3: View covariance (Ω)
        if views.confidences is not None:
            # Idzorek-style: confidence determines Ω scaling
            conf = np.asarray(views.confidences, dtype=float)
            omega = np.zeros((K, K))
            for k in range(K):
                # Higher confidence → smaller Ω → view has more impact
                omega[k, k] = (1 - conf[k]) * (P[k] @ (self.tau * Sigma) @ P[k].T)
        else:
            # Standard: Ω = diag(P @ τΣ @ P') — proportional to view uncertainty
            omega = np.diag(np.diag(P @ (self.tau * Sigma) @ P.T))

        # Step 4: Posterior returns
        # E[R] = [(τΣ)^-1 + P'Ω^-1P]^-1 [(τΣ)^-1π + P'Ω^-1Q]
        tau_sigma = self.tau * Sigma
        tau_sigma_inv = np.linalg.pinv(tau_sigma)
        omega_inv = np.linalg.pinv(omega)

        A = tau_sigma_inv + P.T @ omega_inv @ P
        b = tau_sigma_inv @ pi + P.T @ omega_inv @ Q

        posterior_returns = np.linalg.solve(A, b)

        # Step 5: Optimize weights
        posterior_cov = Sigma + np.linalg.pinv(A)  # uncertainty-adjusted cov

        if method == "mean_variance":
            weights = self._mean_variance(
                posterior_returns, posterior_cov, weight_bounds, N
            )
        else:
            weights = self._maximum_sharpe(
                posterior_returns, posterior_cov, weight_bounds, N
            )

        return BLResult(
            posterior_returns=posterior_returns,
            posterior_weights=weights,
            equilibrium_returns=pi,
            assets=assets or [f"A{i}" for i in range(N)],
            views_applied=K,
            method=method,
        )

    def _mean_variance(
        self, mu: np.ndarray, cov: np.ndarray,
        bounds: tuple, n: int,
    ) -> np.ndarray:
        """Solve mean-variance: max w'μ - (δ/2)w'Σw."""
        def neg_objective(w):
            ret = w @ mu
            risk = 0.5 * self.delta * w @ cov @ w
            return -(ret - risk)

        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
        result = minimize(
            neg_objective,
            x0=np.ones(n) / n,
            method="SLSQP",
            bounds=[bounds] * n,
            constraints=constraints,
            options={"ftol": 1e-12, "maxiter": 500},
        )
        return result.x

    def _maximum_sharpe(
        self, mu: np.ndarray, cov: np.ndarray,
        bounds: tuple, n: int,
    ) -> np.ndarray:
        """Solve maximum Sharpe: max (w'μ) / sqrt(w'Σw)."""
        def neg_sharpe(w):
            ret = w @ mu
            vol = np.sqrt(max(w @ cov @ w, 1e-20))
            return -ret / vol

        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
        result = minimize(
            neg_sharpe,
            x0=np.ones(n) / n,
            method="SLSQP",
            bounds=[bounds] * n,
            constraints=constraints,
            options={"ftol": 1e-12, "maxiter": 500},
        )
        return result.x


def bl_from_views_dict(
    cov_matrix: np.ndarray | pd.DataFrame,
    market_weights: np.ndarray | pd.Series,
    assets: list,
    views: list,  # [("BTC", ">", "SOL", 0.02, 0.5), ...]
    **kwargs,
) -> BLResult:
    """Convenience: build BL from human-readable views.

    Args:
        views: list of (asset1, op, asset2, magnitude, confidence).
            e.g., ("BTC", ">", "SOL", 0.02, 0.5) means
            "BTC outperforms SOL by 2%, confidence 50%".
            For absolute views: ("ETH", "=", None, 0.05, 0.3)
            means "ETH returns 5%, confidence 30%".

    Returns:
        BLResult.
    """
    n = len(assets)
    asset_idx = {a: i for i, a in enumerate(assets)}
    P_rows = []
    Q_vals = []
    confs = []

    for a1, op, a2, mag, conf in views:
        row = np.zeros(n)
        if op == ">":  # a1 outperforms a2
            row[asset_idx[a1]] = 1
            if a2 and a2 in asset_idx:
                row[asset_idx[a2]] = -1
        elif op == "=":  # absolute view on a1
            row[asset_idx[a1]] = 1
        P_rows.append(row)
        Q_vals.append(mag)
        confs.append(conf)

    P = np.array(P_rows)
    Q = np.array(Q_vals)

    vm = ViewMatrix(P=P, Q=Q, confidences=confs)
    bl = BlackLitterman(**kwargs)
    return bl.optimize(cov_matrix, market_weights, vm, assets=assets)
