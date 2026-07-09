"""Multi-pair portfolio state machine for `pairs_cointegration_1d_20260709`.

B2 owns this file. Responsibilities:

  - Track open positions per pair and project size at entry.
  - Enforce the spec's **monthly max-loss pause** rules:
        pair_monthly_max_loss_pct  = -3% (configurable)
        pair_pause_days            = 30
        →  if a pair's trailing-30d cumulative PnL hits -3%, the pair is
            paused for 30 calendar days.
  - Enforce the spec's **portfolio monthly max-loss kill switch**:
        portfolio_monthly_max_loss_pct = -5%
        portfolio_pause_days           = 30
        →  if the portfolio's trailing-30d cumulative PnL hits -5%, flatten
            every open position and pause the whole book for 30 days.
  - Enforce `position_sizing.max_active_pairs` so the strategy never trades
    more than N pairs at once. New entries past the cap are rejected (the
    existing positions are not affected).

`PairAllocation` keeps per-pair bookkeeping (current position leg notionals,
monthly PnL tracking, pause window). `PortfolioState` holds all pair
allocations plus a single portfolio-level pause flag.

The state machine is pure / side-effect-free except for the in-place updates
on `record_entry` / `record_exit`. The monthly-PnL window is computed from
each pair's exits — no global clock is required: callers just feed exits in
order.

This module is read by:
  strategy.simulate_pair_trades()  — calls allow_entry / record_entry / record_exit
  run_backtest.run_multi_pair_backtest() — wires everything together
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Config accessors — kept in one place so the rest of the file doesn't have
# to dig into nested dicts.
# ---------------------------------------------------------------------------
@dataclass
class _RiskCfg:
    pair_monthly_max_loss_pct: float       # e.g. -0.03
    pair_pause_days: int                   # e.g. 30
    portfolio_monthly_max_loss_pct: float  # e.g. -0.05
    portfolio_pause_days: int              # e.g. 30

    @classmethod
    def from_cfg(cls, cfg: dict) -> "_RiskCfg":
        r = cfg["risk"]
        return cls(
            pair_monthly_max_loss_pct=float(r["pair_monthly_max_loss_pct"]),
            pair_pause_days=int(r["pair_pause_days"]),
            portfolio_monthly_max_loss_pct=float(r["portfolio_monthly_max_loss_pct"]),
            portfolio_pause_days=int(r["portfolio_pause_days"]),
        )


# ---------------------------------------------------------------------------
# PairAllocation — per-pair bookkeeping, mutated as entries/exits happen.
# ---------------------------------------------------------------------------
@dataclass
class PairAllocation:
    """Per-pair position state."""

    pair_key: str                       # e.g. "BTCUSDT-SOLUSDT"
    alpha: float = float("nan")
    beta: float = float("nan")
    # Open-position ledger.
    in_position: bool = False
    side: str = ""                      # "long_spread" | "short_spread" | ""
    entry_date: Optional[pd.Timestamp] = None
    entry_spread: float = float("nan")
    entry_z: float = float("nan")
    # Risk controls.
    is_paused: bool = False
    pause_until: Optional[pd.Timestamp] = None
    # Recent exits within the lookback window — used for monthly PnL.
    recent_pnls: Deque[Tuple[pd.Timestamp, float]] = field(default_factory=deque)
    cumulative_pnl_pct_window: float = 0.0

    def __post_init__(self) -> None:
        # ``recent_pnls`` should support appending/popping from both ends.
        if not isinstance(self.recent_pnls, deque):
            self.recent_pnls = deque(self.recent_pnls)


class _PairsDict(dict):
    """A dict[str, PairAllocation] that auto-creates on missing-key access.

    `PortfolioState.pairs["A-B"]` returns a fresh `PairAllocation(pair_key="A-B")`
    the first time the key is read, which makes both internal callers
    (`_pair`) and external test code work with the same idiom.

    We can't use `collections.defaultdict` directly because its
    `default_factory` doesn't receive the missing key — and `PairAllocation`
    requires the pair_key in its constructor.
    """

    def __missing__(self, key: str) -> "PairAllocation":
        v = PairAllocation(pair_key=key)
        self[key] = v
        return v


# ---------------------------------------------------------------------------
# PortfolioState — top-of-book view across all active pairs.
# ---------------------------------------------------------------------------
@dataclass
class PortfolioState:
    """Top-of-book state across all selected pairs.

    The state machine is event-driven: callers feed entries and exits
    through `record_entry` and `record_exit`. Each call may flip the
    `is_paused` flag on the pair or the portfolio, which is what
    `allow_entry` checks on the next bar.

    The `pairs` attribute is a `dict`-like that auto-creates a
    `PairAllocation` on missing-key access, so `state.pairs["A-B"]`
    always succeeds.
    """

    starting_capital_usd: float
    cfg: dict
    pairs: Dict[str, PairAllocation] = field(default_factory=_PairsDict)
    is_portfolio_paused: bool = False
    portfolio_pause_until: Optional[pd.Timestamp] = None
    _portfolio_recent_pnls: Deque[Tuple[pd.Timestamp, float]] = field(default_factory=deque)
    _portfolio_cum_window: float = 0.0
    _active_count: int = 0

    # ------------------------------------------------------------------
    # Config-derived accessors — kept as plain methods so callers don't
    # touch the cfg dict.
    # ------------------------------------------------------------------
    @property
    def risk(self) -> _RiskCfg:
        return _RiskCfg.from_cfg(self.cfg)

    @property
    def max_active_pairs(self) -> int:
        return int(self.cfg["position_sizing"]["max_active_pairs"])

    @property
    def leg_pct_per_pair(self) -> float:
        return float(self.cfg["position_sizing"]["leg_pct_per_pair"])

    @property
    def max_gross_per_pair(self) -> float:
        return float(self.cfg["position_sizing"]["max_gross_per_pair"])

    @property
    def active_count(self) -> int:
        return self._active_count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _pair(self, pair_key: str) -> PairAllocation:
        if pair_key not in self.pairs:
            self.pairs[pair_key] = PairAllocation(pair_key=pair_key)
        return self.pairs[pair_key]

    @staticmethod
    def _expire(
        recent: Deque[Tuple[pd.Timestamp, float]],
        cum: float,
        as_of: pd.Timestamp,
        window_days: int,
    ) -> Tuple[Deque[Tuple[pd.Timestamp, float]], float, int]:
        """Drop exits older than `window_days` from `as_of`; recompute cum."""
        cutoff = as_of - pd.Timedelta(days=window_days)
        kept: Deque[Tuple[pd.Timestamp, float]] = deque()
        new_cum = 0.0
        for dt, p in recent:
            if dt >= cutoff:
                kept.append((dt, p))
                new_cum += p
        return kept, new_cum, len(kept)

    def _refresh_pair_window(
        self, p: PairAllocation, as_of: pd.Timestamp
    ) -> None:
        kept, cum, _ = self._expire(
            p.recent_pnls, p.cumulative_pnl_pct_window, as_of,
            window_days=self.risk.pair_pause_days,
        )
        p.recent_pnls = kept
        p.cumulative_pnl_pct_window = cum

    def _refresh_portfolio_window(self, as_of: pd.Timestamp) -> None:
        kept, cum, _ = self._expire(
            self._portfolio_recent_pnls, self._portfolio_cum_window, as_of,
            window_days=self.risk.portfolio_pause_days,
        )
        self._portfolio_recent_pnls = kept
        self._portfolio_cum_window = cum

    # ------------------------------------------------------------------
    # Public state-machine API
    # ------------------------------------------------------------------
    def allow_entry(self, pair_key: str, entry_date: pd.Timestamp) -> bool:
        """Decide whether a new entry is allowed *right now*.

        Returns False when:
          - portfolio is paused (and `entry_date` is within the window);
          - the pair is paused;
          - the active-pair cap (`max_active_pairs`) is hit AND the pair
            is not currently in a position (i.e. it's a fresh entry).
        True otherwise (including when the pair already has an open
        position — re-entry is gated upstream by the strategy itself).
        """
        # Portfolio-level pause.
        if self.is_portfolio_paused and (
            self.portfolio_pause_until is None
            or entry_date < self.portfolio_pause_until
        ):
            return False

        p = self._pair(pair_key)
        # Pair-level pause.
        if p.is_paused and (
            p.pause_until is None or entry_date < p.pause_until
        ):
            return False

        # Active-pair cap: only matters when this is a fresh entry
        # (the strategy never double-enters anyway, but check anyway).
        if not p.in_position and self._active_count >= self.max_active_pairs:
            return False

        return True

    def record_entry(
        self,
        pair_key: str,
        entry_date: pd.Timestamp,
        side: str,
        *,
        alpha: float,
        beta: float,
    ) -> None:
        """Mark a pair as open; bump active_pair count."""
        p = self._pair(pair_key)
        if p.in_position:
            # Defensive: the strategy shouldn't double-enter, but if it
            # does, the second entry just refreshes the snapshot.
            return
        p.in_position = True
        p.side = side
        p.entry_date = entry_date
        p.alpha = float(alpha)
        p.beta = float(beta)
        self._active_count += 1

    def record_exit(
        self,
        pair_key: str,
        exit_date: pd.Timestamp,
        pnl_pct: float,
        reason: str,
    ) -> None:
        """Mutate state on a fill: log exit, refresh windows, fire pause."""
        p = self._pair(pair_key)
        if not p.in_position:
            # A spurious record_exit shouldn't blow up the state machine.
            return
        p.in_position = False
        self._active_count = max(0, self._active_count - 1)

        # Per-pair tracking
        p.recent_pnls.append((exit_date, float(pnl_pct)))
        self._refresh_pair_window(p, exit_date)
        if p.cumulative_pnl_pct_window <= self.risk.pair_monthly_max_loss_pct:
            p.is_paused = True
            p.pause_until = exit_date + pd.Timedelta(
                days=self.risk.pair_pause_days
            )
            # Clear any half-open entry_state on the pair; the strategy
            # will reject re-entries via allow_entry until the window ends.

        # Portfolio tracking
        self._portfolio_recent_pnls.append((exit_date, float(pnl_pct)))
        self._refresh_portfolio_window(exit_date)
        if self._portfolio_cum_window <= self.risk.portfolio_monthly_max_loss_pct:
            self.is_portfolio_paused = True
            self.portfolio_pause_until = exit_date + pd.Timedelta(
                days=self.risk.portfolio_pause_days
            )

    def force_flatten(self, as_of: pd.Timestamp, reason: str) -> int:
        """Manual flatten: mark every open position closed and apply portfolio
        pause rules. Returns the number of pairs flattened.

        Used by the backtest to simulate the \"portfolio kill switch\" once a
        strategy owner decides to abort the book for reasons other than
        per-trade exits. (Not currently called by `simulate_pair_trades`,
        but exposed for tests and for live-trading kill-switch wiring.)
        """
        n = 0
        for p in self.pairs.values():
            if p.in_position:
                p.in_position = False
                p.side = ""
                n += 1
        self._active_count = max(0, self._active_count - n)
        if self._portfolio_cum_window <= 0.0:
            self.is_portfolio_paused = True
            self.portfolio_pause_until = as_of + pd.Timedelta(
                days=self.risk.portfolio_pause_days
            )
        return n


# ---------------------------------------------------------------------------
# Pure helper — the only capital-allocator with a single deterministic answer
# ---------------------------------------------------------------------------
def apply_pair_constraints(
    desired_a_usd: float,
    desired_b_usd: float,
    cfg: dict,
) -> tuple[float, float]:
    """Clamp each leg to the configured ceiling; pure function.

    Parameters
    ----------
    desired_a_usd, desired_b_usd : float
        Sign-and-magnitude USD notionals the strategy wants to take on each leg.
        `desired_b_usd` already carries the hedge-beta sign (long A → short β*B,
        so desired_b_usd should be negative when desired_a_usd is positive).
    cfg : dict
        Strategy config; reads `position_sizing.leg_pct_per_pair` and
        `position_sizing.max_gross_per_pair`. The constraints are:
            |leg| <= starting_capital * leg_pct_per_pair  (per-leg cap)
            |A| + |β*B| <= starting_capital * max_gross_per_pair  (gross cap)

    Returns
    -------
    (actual_a, actual_b) USD notionals, signed, both within the configured
    bounds. If a clamp kicks in we scale both legs by the same multiplier to
    preserve the market-neutral ratio.

    The `starting_capital_usd` is read from `cfg` — callers should ensure
    the cfg in scope matches the *current* portfolio equity, not the initial
    one, since the cap is in absolute USD not pct-of-equity.
    """
    starting = float(cfg["starting_capital_usd"])
    leg_cap = starting * float(cfg["position_sizing"]["leg_pct_per_pair"])
    gross_cap = starting * float(cfg["position_sizing"]["max_gross_per_pair"])

    a, b = float(desired_a_usd), float(desired_b_usd)
    if a == 0.0 and b == 0.0:
        return 0.0, 0.0

    # Per-leg cap: independent clip, then gross cap: same-multiplier shrink.
    if abs(a) > leg_cap:
        scale = leg_cap / abs(a)
        a *= scale
        b *= scale
    if abs(b) > leg_cap:
        scale = leg_cap / abs(b)
        a *= scale
        b *= scale

    gross = abs(a) + abs(b)
    if gross > gross_cap and gross > 0:
        scale = gross_cap / gross
        a *= scale
        b *= scale

    return float(a), float(b)


__all__ = [
    "PairAllocation",
    "PortfolioState",
    "apply_pair_constraints",
]
