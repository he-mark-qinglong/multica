"""Dynamic portfolio rebalancing — calendar, threshold, vol-target, drawdown-control.

Manages portfolio weight drift through multiple rebalancing triggers:
1. Calendar: rebalance on fixed schedule (weekly/monthly)
2. Threshold: rebalance when any weight drifts > X% from target
3. Vol-target: scale total exposure to achieve target portfolio vol
4. Drawdown control: reduce exposure during portfolio drawdowns

Usage:
    rb = Rebalancer(
        target_weights={"BTC": 0.4, "ETH": 0.35, "SOL": 0.25},
        mode="threshold",
        drift_threshold=0.10,
        target_vol=0.15,
        max_drawdown=0.20,
    )
    action = rb.check(returns, current_weights)
    if action.should_rebalance:
        new_weights = action.target_weights
        print(f"Turnover: {action.turnover:.2%}")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

import numpy as np
import pandas as pd


class RebalanceMode(str, Enum):
    CALENDAR = "calendar"
    THRESHOLD = "threshold"
    VOL_TARGET = "vol_target"
    DRAWDOWN_CONTROL = "drawdown_control"
    COMBINED = "combined"


@dataclass
class RebalanceAction:
    """Result of a rebalance check."""
    should_rebalance: bool
    target_weights: dict | None = None
    reason: str = ""
    turnover: float = 0.0  # total weight change (sum of |Δw|)
    vol_estimate: float | None = None
    drawdown: float | None = None
    exposure_scale: float = 1.0  # multiplier on total exposure


class Rebalancer:
    """Dynamic portfolio rebalancer with multiple trigger modes.

    The 'combined' mode applies all triggers and rebalances when any fires.
    """

    def __init__(
        self,
        target_weights: dict,
        mode: RebalanceMode | str = "threshold",
        # Threshold mode
        drift_threshold: float = 0.10,  # max weight drift before rebalancing
        # Calendar mode
        rebalance_freq: str = "W",  # pandas freq string: W=weekly, M=monthly
        # Vol-target mode
        target_vol: float | None = 0.15,  # target annualized portfolio vol
        vol_window: int = 60,  # rolling window for vol estimation
        vol_adjustment_range: tuple = (0.5, 2.0),  # min/max exposure scale
        # Drawdown control
        max_drawdown: float = 0.20,  # reduce exposure beyond this DD
        dd_reduction_factor: float = 0.5,  # scale to this fraction when DD breached
    ):
        self.target_weights = target_weights
        self.mode = RebalanceMode(mode) if isinstance(mode, str) else mode
        self.drift_threshold = drift_threshold
        self.rebalance_freq = rebalance_freq
        self.target_vol = target_vol
        self.vol_window = vol_window
        self.vol_adj_min, self.vol_adj_max = vol_adjustment_range
        self.max_drawdown = max_drawdown
        self.dd_reduction = dd_reduction_factor
        self._last_calendar_date: pd.Timestamp | None = None
        self._peak_equity: float = 1.0

    def check(
        self,
        returns: pd.DataFrame | pd.Series,
        current_weights: dict,
        current_date: pd.Timestamp | None = None,
        current_equity: float = 1.0,
    ) -> RebalanceAction:
        """Check if rebalancing is needed.

        Args:
            returns: recent returns (DataFrame for multi-asset, Series for portfolio).
            current_weights: current portfolio weights.
            current_date: date of this check (for calendar mode).
            current_equity: current equity curve value (for DD tracking).

        Returns:
            RebalanceAction with decision and target weights.
        """
        if self.mode == RebalanceMode.COMBINED:
            return self._check_combined(returns, current_weights, current_date, current_equity)

        actions = {
            RebalanceMode.CALENDAR: lambda: self._check_calendar(current_date),
            RebalanceMode.THRESHOLD: lambda: self._check_threshold(current_weights),
            RebalanceMode.VOL_TARGET: lambda: self._check_vol(returns),
            RebalanceMode.DRAWDOWN_CONTROL: lambda: self._check_drawdown(current_equity),
        }
        handler = actions.get(self.mode, actions[RebalanceMode.THRESHOLD])
        return handler()

    def compute_target_weights(
        self,
        returns: pd.DataFrame | None = None,
        current_equity: float = 1.0,
    ) -> dict:
        """Compute target weights including vol/drawdown adjustments."""
        weights = self.target_weights.copy()

        # Volatility targeting adjustment
        if self.target_vol is not None and returns is not None:
            if isinstance(returns, pd.DataFrame) and len(returns) > self.vol_window:
                recent = returns.iloc[-self.vol_window:]
                port_vol = float(recent.mean(axis=1).std() * np.sqrt(365))
                if port_vol > 1e-6:
                    scale = self.target_vol / port_vol
                    scale = np.clip(scale, self.vol_adj_min, self.vol_adj_max)
                    for k in weights:
                        weights[k] *= scale

        # Drawdown control
        self._peak_equity = max(self._peak_equity, current_equity)
        dd = (current_equity - self._peak_equity) / self._peak_equity if self._peak_equity > 0 else 0
        if dd < -self.max_drawdown:
            for k in weights:
                weights[k] *= self.dd_reduction

        return weights

    def _check_threshold(self, current_weights: dict) -> RebalanceAction:
        """Check if weight drift exceeds threshold."""
        max_drift = 0.0
        drifted_assets = []

        for asset, target in self.target_weights.items():
            current = current_weights.get(asset, 0.0)
            drift = abs(current - target)
            if drift > self.drift_threshold:
                drifted_assets.append((asset, current, target))
            max_drift = max(max_drift, drift)

        if drifted_assets:
            target = self.target_weights
            turnover = sum(abs(current_weights.get(a, 0) - target[a]) for a in target)
            return RebalanceAction(
                should_rebalance=True,
                target_weights=target,
                reason=f"Drift exceeded: {drifted_assets}",
                turnover=turnover,
            )
        return RebalanceAction(should_rebalance=False, reason=f"Max drift {max_drift:.2%} < {self.drift_threshold:.2%}")

    def _check_calendar(self, current_date: pd.Timestamp | None) -> RebalanceAction:
        """Check if enough time has passed for calendar rebalance."""
        if current_date is None:
            return RebalanceAction(should_rebalance=False, reason="No date provided")

        if self._last_calendar_date is None:
            self._last_calendar_date = current_date
            return RebalanceAction(should_rebalance=False, reason="First check")

        # Check if we've crossed a frequency boundary
        freq_map = {"D": "D", "W": "W", "M": "MS"}
        try:
            periods = len(pd.date_range(
                start=self._last_calendar_date,
                end=current_date,
                freq=self.rebalance_freq,
            ))
        except Exception:
            periods = 0

        if periods > 1:
            self._last_calendar_date = current_date
            return RebalanceAction(
                should_rebalance=True,
                target_weights=self.target_weights,
                reason=f"Calendar rebalance ({self.rebalance_freq})",
            )
        return RebalanceAction(should_rebalance=False, reason=f"Not yet ({self.rebalance_freq})")

    def _check_vol(self, returns: pd.DataFrame | pd.Series) -> RebalanceAction:
        """Check if portfolio vol is off-target."""
        if isinstance(returns, pd.DataFrame):
            port_returns = returns.mean(axis=1)
        else:
            port_returns = returns

        if len(port_returns) < self.vol_window:
            return RebalanceAction(should_rebalance=False, reason="Insufficient data for vol estimate")

        recent_vol = float(port_returns.iloc[-self.vol_window:].std() * np.sqrt(365))
        vol_ratio = recent_vol / self.target_vol if self.target_vol and recent_vol > 0 else 1.0

        if abs(vol_ratio - 1.0) > 0.2:  # >20% off target
            scale = np.clip(1.0 / vol_ratio, self.vol_adj_min, self.vol_adj_max)
            adjusted = {k: v * scale for k, v in self.target_weights.items()}
            return RebalanceAction(
                should_rebalance=True,
                target_weights=adjusted,
                reason=f"Vol {recent_vol:.1%} vs target {self.target_vol:.1%}",
                vol_estimate=recent_vol,
                exposure_scale=scale,
            )
        return RebalanceAction(
            should_rebalance=False,
            reason=f"Vol {recent_vol:.1%} on target",
            vol_estimate=recent_vol,
        )

    def _check_drawdown(self, current_equity: float) -> RebalanceAction:
        """Check if drawdown control should trigger."""
        self._peak_equity = max(self._peak_equity, current_equity)
        dd = (current_equity - self._peak_equity) / self._peak_equity if self._peak_equity > 0 else 0

        if dd < -self.max_drawdown:
            reduced = {k: v * self.dd_reduction for k, v in self.target_weights.items()}
            return RebalanceAction(
                should_rebalance=True,
                target_weights=reduced,
                reason=f"Drawdown {dd:.1%} < -{self.max_drawdown:.0%}",
                drawdown=dd,
                exposure_scale=self.dd_reduction,
            )
        return RebalanceAction(
            should_rebalance=False,
            reason=f"DD {dd:.1%} within limit",
            drawdown=dd,
        )

    def _check_combined(
        self, returns, current_weights, current_date, current_equity,
    ) -> RebalanceAction:
        """Run all checks; rebalance if any fires."""
        # Start with target weights (possibly vol/DD adjusted)
        adjusted_target = self.compute_target_weights(returns, current_equity)

        # Check each trigger
        triggers = []
        scale = 1.0

        # Threshold drift
        max_drift = max(
            abs(current_weights.get(a, 0) - adjusted_target.get(a, 0))
            for a in adjusted_target
        ) if adjusted_target else 0
        if max_drift > self.drift_threshold:
            triggers.append(f"drift {max_drift:.2%}")

        # Drawdown
        self._peak_equity = max(self._peak_equity, current_equity)
        dd = (current_equity - self._peak_equity) / self._peak_equity if self._peak_equity > 0 else 0
        if dd < -self.max_drawdown:
            triggers.append(f"DD {dd:.1%}")

        # Vol
        if isinstance(returns, pd.DataFrame) and len(returns) > self.vol_window:
            port_vol = float(returns.iloc[-self.vol_window:].mean(axis=1).std() * np.sqrt(365))
            if self.target_vol and port_vol > 0:
                vol_ratio = port_vol / self.target_vol
                if abs(vol_ratio - 1.0) > 0.3:
                    triggers.append(f"vol {port_vol:.1%}")

        if triggers:
            turnover = sum(abs(current_weights.get(a, 0) - adjusted_target.get(a, 0)) for a in adjusted_target)
            return RebalanceAction(
                should_rebalance=True,
                target_weights=adjusted_target,
                reason=f"Combined: {', '.join(triggers)}",
                turnover=turnover,
                drawdown=dd,
            )
        return RebalanceAction(
            should_rebalance=False,
            reason=f"All checks OK (drift={max_drift:.2%}, DD={dd:.1%})",
        )
