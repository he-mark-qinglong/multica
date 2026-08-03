"""Tests for maker-matching bridge."""
import numpy as np
import pandas as pd
import pytest

from _shared.execution.maker_matching_bridge import (
    MakerMatchingBridge, QuoteSnapshot, AdverseSelectionResult,
)
from _shared.execution.matching_engine import MatchingEngine, Order, Fill


class TestSubmitQuotes:
    def test_no_fills_without_market_trades(self):
        bridge = MakerMatchingBridge()
        quotes = [QuoteSnapshot(
            timestamp=1_000_000_000, bid_price=99.0, ask_price=101.0,
            bid_qty=1.0, ask_qty=1.0,
        )]
        trades = pd.DataFrame(columns=["timestamp", "price", "qty", "side"])
        fills = bridge.submit_quotes(quotes, trades)
        assert len(fills) == 0

    def test_market_sell_hits_maker_bid(self):
        bridge = MakerMatchingBridge()
        ts = 1_000_000_000
        quotes = [QuoteSnapshot(
            timestamp=ts, bid_price=100.0, ask_price=102.0,
            bid_qty=1.0, ask_qty=1.0,
        )]
        trades = pd.DataFrame([{
            "timestamp": ts, "price": 100.0, "qty": 0.5, "side": "sell",
        }])
        fills = bridge.submit_quotes(quotes, trades)
        assert len(fills) >= 1

    def test_market_buy_hits_maker_ask(self):
        bridge = MakerMatchingBridge()
        ts = 1_000_000_000
        quotes = [QuoteSnapshot(
            timestamp=ts, bid_price=98.0, ask_price=100.0,
            bid_qty=1.0, ask_qty=1.0,
        )]
        trades = pd.DataFrame([{
            "timestamp": ts, "price": 100.0, "qty": 0.5, "side": "buy",
        }])
        fills = bridge.submit_quotes(quotes, trades)
        assert len(fills) >= 1


class TestAdverseSelection:
    def test_empty_returns_zeros(self):
        bridge = MakerMatchingBridge()
        prices = pd.Series([100, 101, 102],
            index=pd.date_range("2025-01-01", periods=3, freq="1min"))
        result = bridge.analyze_adverse_selection(prices)
        assert result.n_maker_fills == 0
        assert result.toxic_ratio == 0.0

    def test_detects_adverse_move(self):
        bridge = MakerMatchingBridge(adverse_selection_window=1)
        ts_ns = 1_000_000_000
        # Simulate a fill where we bought at 100, then price dropped to 98
        fill = Fill(
            maker_ack_id="maker_bid_1", taker_ack_id="taker_1",
            price=100.0, qty=0.5, timestamp=float(ts_ns),
            taker_side="sell",
        )
        bridge._fill_history.append((fill, 100.0))
        prices = pd.Series(
            [100.0, 98.0, 97.0],
            index=pd.to_datetime([
                pd.Timestamp(ts_ns, unit="ns"),
                pd.Timestamp(ts_ns + 60_000_000_000, unit="ns"),
                pd.Timestamp(ts_ns + 120_000_000_000, unit="ns"),
            ]),
        )
        result = bridge.analyze_adverse_selection(prices)
        assert result.n_maker_fills == 1
        assert result.avg_fill_to_move_bps > 0  # adverse (price dropped)


class TestQuoteSnapshot:
    def test_creation(self):
        q = QuoteSnapshot(
            timestamp=42, bid_price=99.5, ask_price=100.5,
            bid_qty=0.1, ask_qty=0.1,
        )
        assert q.bid_price == 99.5
        assert q.timestamp == 42
