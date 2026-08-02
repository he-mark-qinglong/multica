"""Lighter CLOB simulation adapter for zero-fee backtesting.

Lighter (lighter.xyz) is a zero-fee perpetual DEX: Standard accounts get
maker 0bp / taker 0bp on all markets, with no volume or balance thresholds.
The trade-off is **artificial latency** imposed by the protocol:
maker orders are delayed 200ms, taker orders 300ms, cancels 200ms.

This module provides a backtest adapter that lets killed strategies be
retested at 0bp while accounting for the latency cost. It is a
**simulation** adapter — it models the economics (fee + latency), not a
live trading client.

Design
------
The adapter wraps three concerns:

1. **Fee override** — configurable maker/taker fees in bps. Default is
   the Lighter Standard account (0/0). Callers can set any value to
   simulate alternative venues (e.g. Binance VIP3 at 1.2bp maker).

2. **Latency cost** — the artificial delay means a taker order submitted
   at signal time fills *latency_ms* later, not instantly. On
   signal-driven entries this is adverse-selection slippage: by the time
   the fill arrives the signal information is already reflected in the
   price. We estimate this from the trade tape itself rather than a
   parametric model.

3. **Venue integration** — exposes a :class:`Venue` compatible with
   :func:`cost_model.apply_cost` and a :class:`CostModel` for
   :mod:`backtest.factor_backtester` so strategies can drop it in.

References
----------
- Lighter Trading Fees docs (lighter.xyz) — maker/taker 0bp Standard,
  200ms/300ms artificial delay.
- ``_shared/execution/cost_model.py`` — the Venue/apply_cost pattern.
- ``_shared/latency_model.py`` — feed/order/cancel latency decomposition.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from _shared.execution.cost_model import Venue


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LighterConfig:
    """Fee and latency configuration for a Lighter simulation run.

    Defaults match the Lighter Standard account (zero fees, protocol
    artificial latency). Override any field to model other venues or
    latency regimes.
    """

    maker_fee_bps: float = 0.0        # bps per side
    taker_fee_bps: float = 0.0        # bps per side
    maker_latency_ms: float = 200.0   # artificial delay on maker orders
    taker_latency_ms: float = 300.0   # artificial delay on taker orders
    cancel_latency_ms: float = 200.0  # artificial delay on cancels
    # Additional non-fee slippage (spread crossing, impact) in bps/side.
    # Zero by default — the latency model captures adverse selection.
    # Set >0 for a conservative overlay if the tape doesn't fully capture it.
    extra_slippage_bps: float = 0.0


# ---------------------------------------------------------------------------
# Venue / CostModel builders
# ---------------------------------------------------------------------------

def lighter_venue(config: Optional[LighterConfig] = None) -> Venue:
    """Build a cost_model.Vue for the Lighter venue.

    The returned Venue has zero fixed_pure_slippage_bps (no ratified
    futures slippage constant). Callers who want the latency overlay
    should use :class:`LighterAdapter` directly.
    """
    cfg = config or LighterConfig()
    return Venue(
        name="lighter_standard",
        taker_fee_bps=cfg.taker_fee_bps,
        maker_fee_bps=cfg.maker_fee_bps,
        has_bnb_discount=False,
        fixed_pure_slippage_bps=None,  # spot-style: no fixed slippage
    )


def lighter_cost_model(config: Optional[LighterConfig] = None):
    """Build a factor_backtester.CostModel for the Lighter venue.

    Returns a CostModel with the configured maker/taker fees and zero
    slippage (slippage is handled by the latency model, not a flat bps).
    """
    cfg = config or LighterConfig()
    try:
        from backtest.factor_backtester import CostModel
    except ImportError:
        from pathlib import Path
        import sys
        _QL = str(Path(__file__).resolve().parents[2])
        if _QL not in sys.path:
            sys.path.insert(0, _QL)
        from backtest.factor_backtester import CostModel

    return CostModel(
        commission_bps_per_side=cfg.taker_fee_bps,
        slippage_bps_per_side=cfg.extra_slippage_bps,
        maker_fee_bps_per_side=cfg.maker_fee_bps,
        taker_fee_bps_per_side=cfg.taker_fee_bps,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class LighterAdapter:
    """Simulation adapter for the Lighter zero-fee perp DEX.

    Combines fee economics with latency-aware fill estimation. Intended
    for backtesting killed strategies at 0bp to see whether the gross
    signal edge survives the protocol's artificial latency.

    Usage (bar-level backtest)::

        adapter = LighterAdapter()
        rt_cost_bps = adapter.round_trip_cost_bps(side="taker")

    Usage (latency-aware, from trade tape)::

        adapter = LighterAdapter()
        fill = adapter.estimate_latency_slippage(
            signal_ts, aggtrades, side="taker",
        )
        # fill.latency_bps is the adverse-selection cost of the delay

    Parameters
    ----------
    config:
        :class:`LighterConfig` with fee/latency settings. Defaults to
        Standard (0bp fees, 200/300ms latency).
    """

    def __init__(self, config: Optional[LighterConfig] = None) -> None:
        self.config = config or LighterConfig()

    @property
    def venue(self) -> Venue:
        """The cost_model Venue for this adapter's fee configuration."""
        return lighter_venue(self.config)

    # -- fee economics ------------------------------------------------------

    def fee_bps(self, side: Literal["maker", "taker"]) -> float:
        """Single-leg fee in bps for the given execution side."""
        if side == "maker":
            return self.config.maker_fee_bps
        elif side == "taker":
            return self.config.taker_fee_bps
        raise ValueError(f"side must be 'maker' or 'taker', got {side!r}")

    def round_trip_fee_bps(self, side: Literal["maker", "taker"] = "taker") -> float:
        """Total fee cost (entry + exit) in bps for a round-trip trade.

        Both legs use the same execution side. For mixed strategies
        (maker entry, taker exit) sum the two single-leg fees manually.
        """
        return 2.0 * self.fee_bps(side)

    def round_trip_cost_bps(
        self,
        side: Literal["maker", "taker"] = "taker",
        latency_bps: float = 0.0,
    ) -> float:
        """Total RT cost in bps: fees + extra slippage + latency overlay.

        Parameters
        ----------
        side:
            Execution side for fee lookup.
        latency_bps:
            Optional pre-computed latency cost (adverse selection) in bps
            for the round trip. When 0, only fee + extra_slippage is
            returned. Obtain this from
            :meth:`estimate_latency_slippage` or a tape replay.
        """
        fee_rt = self.round_trip_fee_bps(side)
        slip_rt = 2.0 * self.config.extra_slippage_bps
        return fee_rt + slip_rt + latency_bps

    # -- latency -----------------------------------------------------------

    def latency_ms(self, side: Literal["maker", "taker"]) -> float:
        """Artificial protocol latency for the given side."""
        if side == "maker":
            return self.config.maker_latency_ms
        elif side == "taker":
            return self.config.taker_latency_ms
        raise ValueError(f"side must be 'maker' or 'taker', got {side!r}")

    def estimate_latency_slippage(
        self,
        signal_ts: pd.Timestamp,
        aggtrades: pd.DataFrame,
        side: Literal["maker", "taker"] = "taker",
        lookforward_ms: Optional[float] = None,
    ) -> "LatencyFill":
        """Estimate the adverse-selection cost of the latency delay.

        Finds the reference price (last trade at or before ``signal_ts``)
        and the fill price (first trade at or after ``signal_ts +
        latency``). The difference is the latency slippage.

        Parameters
        ----------
        signal_ts:
            The timestamp at which the signal fires and the order would
            be submitted in a zero-latency world.
        aggtrades:
            Trade tape with columns ``['ts', 'price']`` and a sorted
            ``ts`` column (datetime, UTC). Typically loaded from
            ``data/trades/BTCUSDT_aggtrades.parquet``.
        side:
            Execution side — determines the latency amount.
        lookforward_ms:
            Override for the look-forward window. Defaults to the side's
            configured latency. Trades arriving within this window after
            the signal are candidate fills; the first one is used.

        Returns
        -------
        LatencyFill
            Named result with reference price, fill price, fill
            timestamp, and slippage in bps.
        """
        delay_ms = lookforward_ms if lookforward_ms is not None else self.latency_ms(side)
        delay = pd.Timedelta(milliseconds=delay_ms)

        ts = aggtrades["ts"]
        prices = aggtrades["price"].to_numpy()

        # Reference: last trade at or before signal_ts
        ref_loc = ts.searchsorted(signal_ts, side="right") - 1
        if ref_loc < 0:
            return LatencyFill(signal_ts, None, None, None, float("nan"))

        ref_price = float(prices[ref_loc])

        # Fill: first trade at or after signal_ts + delay
        fill_ts = signal_ts + delay
        fill_loc = ts.searchsorted(fill_ts, side="left")
        if fill_loc >= len(prices):
            return LatencyFill(signal_ts, ref_price, None, None, float("nan"))

        fill_price = float(prices[fill_loc])
        actual_fill_ts = ts.iloc[fill_loc]
        slip_bps = (fill_price / ref_price - 1.0) * 1e4

        return LatencyFill(
            signal_ts=signal_ts,
            ref_price=ref_price,
            fill_price=fill_price,
            fill_ts=actual_fill_ts,
            latency_bps=slip_bps,
        )

    def batch_latency_slippage(
        self,
        signal_timestamps: pd.DatetimeIndex,
        aggtrades: pd.DataFrame,
        side: Literal["maker", "taker"] = "taker",
    ) -> pd.Series:
        """Vectorized latency slippage for many signal timestamps.

        Returns a Series of slippage in bps (fill_price / ref_price - 1)
        indexed by signal timestamp. ``NaN`` where no trade was found
        within a reasonable window.

        This is the efficient path for backtesting: it uses
        :func:`numpy.searchsorted` on the sorted trade-tape timestamps
        rather than per-row DataFrame lookups.
        """
        delay_ms = self.latency_ms(side)

        prices = aggtrades["price"].to_numpy()

        # Work in int64 nanoseconds for searchsorted compatibility.
        # The tape may be datetime64[ms] (lower resolution) or tz-aware;
        # normalizing both sides to naive datetime64[ns]→int64 avoids
        # unit mismatches and the object-dtype trap from tz-aware arrays.
        tape_ts = aggtrades["ts"]
        if tape_ts.dt.tz is not None:
            tape_ts = tape_ts.dt.tz_localize(None)
        tape_ns = tape_ts.to_numpy().astype("datetime64[ns]").astype("int64")

        sig_idx = signal_timestamps
        if getattr(sig_idx, "tz", None) is not None:
            sig_idx = sig_idx.tz_localize(None)
        sig_ns = np.array(sig_idx, dtype="datetime64[ns]").astype("int64")

        delay_ns = int(delay_ms) * 1_000_000

        # Reference loc: last trade at or before each signal
        ref_locs = np.searchsorted(tape_ns, sig_ns, side="right") - 1
        # Fill loc: first trade at or after signal + delay
        fill_locs = np.searchsorted(tape_ns, sig_ns + delay_ns, side="left")

        n = len(prices)
        ref_valid = ref_locs >= 0
        fill_valid = fill_locs < n

        ref_prices = np.where(ref_valid, prices[np.clip(ref_locs, 0, n - 1)], np.nan)
        fill_prices = np.where(fill_valid, prices[np.clip(fill_locs, 0, n - 1)], np.nan)

        slip_bps = np.where(
            ref_valid & fill_valid & (ref_prices > 0),
            (fill_prices / ref_prices - 1.0) * 1e4,
            np.nan,
        )

        return pd.Series(slip_bps, index=signal_timestamps, name="latency_bps")

    # -- introspection ------------------------------------------------------

    def describe(self) -> str:
        """One-line description for run ledgers."""
        c = self.config
        return (
            f"Lighter(maker={c.maker_fee_bps:g}bp/{c.maker_latency_ms:g}ms, "
            f"taker={c.taker_fee_bps:g}bp/{c.taker_latency_ms:g}ms, "
            f"RT_taker={self.round_trip_fee_bps('taker'):g}bp)"
        )


@dataclass(frozen=True)
class LatencyFill:
    """Result of a single latency-aware fill estimation."""

    signal_ts: pd.Timestamp
    ref_price: Optional[float]      # last trade price at/before signal
    fill_price: Optional[float]     # first trade price after signal + delay
    fill_ts: Optional[pd.Timestamp] # when the fill would have arrived
    latency_bps: float              # (fill/ref - 1) * 1e4; NaN if unavailable

    @property
    def filled(self) -> bool:
        return self.fill_price is not None and not np.isnan(self.latency_bps)


__all__ = [
    "LighterConfig",
    "LighterAdapter",
    "LatencyFill",
    "lighter_venue",
    "lighter_cost_model",
]
