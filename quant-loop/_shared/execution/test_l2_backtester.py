"""Tests for L2 orderbook-aware backtest runner."""
import numpy as np
import pandas as pd
import pytest
from pathlib import Path
import tempfile

from _shared.execution.l2_backtester import (
    L2Backtester, L2Fill, L2BacktestResult,
    _simulate_limit_fill, _simulate_market_order,
    _extract_book_state,
)


def _make_l2_parquet(n=1000, seed=42):
    """Create synthetic L2 depth data for testing."""
    rng = np.random.default_rng(seed)
    ts = np.arange(n, dtype=np.int64) * 1_000_000_000  # 1-second spacing
    mid = 50000 + np.cumsum(rng.normal(0, 10, n))
    spread = rng.uniform(1, 5, n)

    data = {"ts_ns": ts}
    for i in range(1, 11):
        data[f"bid_p{i}"] = mid - spread * i
        data[f"bid_q{i}"] = rng.exponential(0.5, n)
        data[f"ask_p{i}"] = mid + spread * i
        data[f"ask_q{i}"] = rng.exponential(0.5, n)
    return pd.DataFrame(data)


class TestSimulateLimitFill:
    def test_full_fill_at_best_level(self):
        levels = [(100.0, 1.0), (101.0, 2.0)]
        filled, avg_price, remaining = _simulate_limit_fill(
            "buy", 105.0, 0.5, levels
        )
        assert filled == pytest.approx(0.5)
        assert avg_price == pytest.approx(100.0)
        assert remaining == pytest.approx(0.0)

    def test_partial_fill(self):
        levels = [(100.0, 0.3)]
        filled, avg_price, remaining = _simulate_limit_fill(
            "buy", 105.0, 1.0, levels
        )
        assert filled == pytest.approx(0.3)
        assert remaining == pytest.approx(0.7)

    def test_no_fill_when_price_too_low_for_buy(self):
        levels = [(105.0, 1.0)]
        filled, _, _ = _simulate_limit_fill("buy", 100.0, 1.0, levels)
        assert filled == 0.0

    def test_walks_multiple_levels(self):
        levels = [(100.0, 0.3), (101.0, 0.3), (102.0, 0.4)]
        filled, avg_price, remaining = _simulate_limit_fill(
            "buy", 105.0, 1.0, levels
        )
        assert filled == pytest.approx(1.0)
        assert avg_price == pytest.approx(100 * 0.3 + 101 * 0.3 + 102 * 0.4)


class TestSimulateMarketOrder:
    def test_sweeps_depth(self):
        levels = [(100.0, 0.5), (101.0, 0.5), (102.0, 1.0)]
        filled, avg_price, remaining = _simulate_market_order("buy", 1.0, levels)
        assert filled == pytest.approx(1.0)
        assert remaining == pytest.approx(0.0)
        assert avg_price == pytest.approx(100.5)

    def test_partial_when_insufficient_depth(self):
        levels = [(100.0, 0.3)]
        filled, _, remaining = _simulate_market_order("buy", 1.0, levels)
        assert filled == pytest.approx(0.3)
        assert remaining == pytest.approx(0.7)


class TestExtractBookState:
    def test_extracts_levels(self):
        row = pd.Series({
            "bid_p1": 99.0, "bid_q1": 1.0,
            "bid_p2": 98.0, "bid_q2": 2.0,
            "ask_p1": 101.0, "ask_q1": 1.5,
            "ask_p2": 102.0, "ask_q2": 0.5,
        })
        bids, asks = _extract_book_state(row, n_levels=5)
        assert len(bids) == 2
        assert len(asks) == 2
        assert bids[0] == (99.0, 1.0)
        assert asks[0] == (101.0, 1.5)

    def test_skips_zero_qty(self):
        row = pd.Series({
            "bid_p1": 99.0, "bid_q1": 0.0,
            "ask_p1": 101.0, "ask_q1": 1.0,
        })
        bids, asks = _extract_book_state(row, n_levels=5)
        assert len(bids) == 0
        assert len(asks) == 1


class TestL2Backtester:
    def test_runs_with_synthetic_data(self, tmp_path):
        df = _make_l2_parquet(500)
        path = tmp_path / "test_l2.parquet"
        df.to_parquet(path)

        bt = L2Backtester(l2_data_path=path, initial_capital=10_000)

        # Simple strategy: buy on bar 100, sell on bar 400
        def signal_fn(row, position):
            bar_idx = row.name if isinstance(row.name, int) else 0
            if hasattr(row, 'ts_ns'):
                bar_idx = int(row.ts_ns / 1e9)
            if bar_idx == 100 and position == 0:
                return {"side": "buy", "type": "market", "qty": 0.1}
            if bar_idx == 400 and position > 0:
                return {"side": "sell", "type": "market", "qty": position}
            return None

        result = bt.run(signal_fn)
        assert isinstance(result, L2BacktestResult)
        assert result.n_orders == 2
        assert len(result.fills) >= 1
        assert result.fill_rate > 0

    def test_fill_rate_calculation(self, tmp_path):
        df = _make_l2_parquet(200)
        path = tmp_path / "test.parquet"
        df.to_parquet(path)

        bt = L2Backtester(l2_data_path=path)
        result = bt.run(lambda r, p: None)
        assert result.n_orders == 0
        assert result.fill_rate == 0.0

    def test_equity_curve_length_matches_data(self, tmp_path):
        n = 300
        df = _make_l2_parquet(n)
        path = tmp_path / "test.parquet"
        df.to_parquet(path)

        bt = L2Backtester(l2_data_path=path)
        result = bt.run(lambda r, p: None)
        assert len(result.equity) == n
