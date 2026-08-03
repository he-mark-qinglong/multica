"""Bridge: maker simulator → matching engine for realistic fill simulation.

Eliminates optimistic fill bias in market-making backtests by routing
the maker simulator's quote output through the matching engine, which
enforces price-time priority and realistic queue position.

Usage:
    bridge = MakerMatchingBridge(matching_engine=MatchingEngine())
    fills = bridge.submit_quotes(quotes, market_trades)
    # Returns realistic fills accounting for queue position and adverse selection
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np
import pandas as pd

from _shared.execution.matching_engine import MatchingEngine, Order, Fill


@dataclass
class QuoteSnapshot:
    """A maker quote at a point in time."""
    timestamp: int       # nanoseconds
    bid_price: float
    ask_price: float
    bid_qty: float
    ask_qty: float


@dataclass
class AdverseSelectionResult:
    """Analysis of adverse selection on maker fills."""
    n_maker_fills: int
    n_toxic_fills: int        # fills followed by adverse price move
    toxic_ratio: float        # n_toxic / n_maker
    avg_fill_to_move_bps: float  # average adverse move after fill (bps)
    total_adverse_cost_bps: float


class MakerMatchingBridge:
    """Bridges maker simulator output to the matching engine.

    Instead of assuming instant fills at quoted prices (optimistic),
    this bridge submits maker orders to the matching engine and lets
    the FIFO queue determine actual fill timing and probability.
    """

    def __init__(
        self,
        matching_engine: MatchingEngine | None = None,
        adverse_selection_window: int = 10,  # bars to measure adverse move
    ):
        self.engine = matching_engine or MatchingEngine()
        self.adverse_window = adverse_selection_window
        self._fill_history: list[tuple[Fill, float]] = []  # (fill, mid_at_fill)

    def submit_quotes(
        self,
        quotes: list[QuoteSnapshot],
        market_trades: pd.DataFrame,
    ) -> list[Fill]:
        """Submit a sequence of maker quotes and process market trades."""
        maker_fills: list[Fill] = []
        self._fill_history.clear()

        for q in quotes:
            bid_order = Order(
                ack_id=f"maker_bid_{q.timestamp}", side="buy", order_type="limit",
                price=q.bid_price, qty=q.bid_qty, timestamp=q.timestamp,
            )
            ask_order = Order(
                ack_id=f"maker_ask_{q.timestamp}", side="sell", order_type="limit",
                price=q.ask_price, qty=q.ask_qty, timestamp=q.timestamp,
            )
            self.engine.process(bid_order)
            self.engine.process(ask_order)

            ts_trades = market_trades[
                market_trades["timestamp"] == q.timestamp
            ] if "timestamp" in market_trades.columns else pd.DataFrame()

            mid = (q.bid_price + q.ask_price) / 2
            for _, trade in ts_trades.iterrows():
                taker_side = trade.get("side", "buy")
                trade_qty = float(trade.get("qty", 0))
                if trade_qty <= 0:
                    continue
                taker_order = Order(
                    ack_id=f"taker_{q.timestamp}_{trade.name}",
                    side=taker_side, order_type="market",
                    price=0, qty=trade_qty, timestamp=q.timestamp + 1,
                )
                fills = self.engine.process(taker_order)
                for f in fills:
                    if "maker_" in f.maker_ack_id:
                        maker_fills.append(f)
                        self._fill_history.append((f, mid))

        return maker_fills

    def analyze_adverse_selection(
        self,
        price_series: pd.Series,
        bar_freq: str = "1min",
    ) -> AdverseSelectionResult:
        """Analyze adverse selection on recorded maker fills.

        Adverse selection = the tendency for maker fills to be followed
        by price moves against the maker (toxic flow).

        Args:
            price_series: mid-price series indexed by timestamp.
            bar_freq: bar frequency for measuring post-fill moves.

        Returns:
            AdverseSelectionResult with toxic ratio and avg adverse cost.
        """
        if not self._fill_history:
            return AdverseSelectionResult(
                n_maker_fills=0, n_toxic_fills=0, toxic_ratio=0.0,
                avg_fill_to_move_bps=0.0, total_adverse_cost_bps=0.0,
            )

        adverse_moves = []
        n_toxic = 0

        for fill, mid_at_fill in self._fill_history:
            # Find price N bars after fill
            try:
                # The fill moved us long (buy) or short (sell)
                if fill.taker_side == "sell":
                    # Someone sold to our bid → we're long
                    # Adverse = price goes DOWN after we buy
                    future_idx = price_series.index.searchsorted(
                        pd.Timestamp(fill.timestamp, unit="ns"),
                        side="right",
                    )
                    future_idx = min(future_idx + self.adverse_window, len(price_series) - 1)
                    future_price = price_series.iloc[future_idx]
                    adverse_bps = (mid_at_fill - future_price) / mid_at_fill * 1e4
                else:
                    # Someone bought from our ask → we're short
                    # Adverse = price goes UP after we sell
                    future_idx = price_series.index.searchsorted(
                        pd.Timestamp(fill.timestamp, unit="ns"),
                        side="right",
                    )
                    future_idx = min(future_idx + self.adverse_window, len(price_series) - 1)
                    future_price = price_series.iloc[future_idx]
                    adverse_bps = (future_price - mid_at_fill) / mid_at_fill * 1e4
            except (IndexError, KeyError):
                continue

            adverse_moves.append(adverse_bps)
            if adverse_bps > 1.0:  # >1bp adverse = toxic
                n_toxic += 1

        n_fills = len(adverse_moves)
        avg_adverse = float(np.mean(adverse_moves)) if adverse_moves else 0.0
        total_cost = float(np.sum(adverse_moves)) if adverse_moves else 0.0

        return AdverseSelectionResult(
            n_maker_fills=n_fills,
            n_toxic_fills=n_toxic,
            toxic_ratio=n_toxic / n_fills if n_fills > 0 else 0.0,
            avg_fill_to_move_bps=avg_adverse,
            total_adverse_cost_bps=total_cost,
        )
