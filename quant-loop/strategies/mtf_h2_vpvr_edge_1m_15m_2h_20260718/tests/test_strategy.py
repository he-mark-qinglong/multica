"""Tests for mtf_h2_vpvr_edge_1m_15m_2h_20260718.

Goal: at least 1 PASSING pytest with focused, deterministic assertions on
the H2 single-pair VPVR edge-touch strategy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_STRAT = _HERE.parent
_ROOT = _STRAT.parent
_INDICATORS = _ROOT / "_indicators"

for _p in (str(_STRAT), str(_ROOT), str(_INDICATORS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from strategy import (  # noqa: E402
    DEFAULT_INDICATORS,
    build_signals,
    daily_returns,
    profit_factor_and_mdd,
    run_backtest,
    sharpe_daily_resampled,
)


def _make_1m_df(n: int = 5000, start: str = "2026-01-01",
                seed: int = 7, drift: float = 0.00002) -> pd.DataFrame:
    """Synthetic 1m OHLCV with mild upward drift and bounded volatility.

    Volume is positive so that the rolling VPVR helper has real input.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1min")
    ret = drift + rng.normal(0, 0.0008, size=n)
    close = 100.0 * np.exp(np.cumsum(ret))
    high = close * (1.0 + np.abs(rng.normal(0, 0.0004, size=n)))
    low = close * (1.0 - np.abs(rng.normal(0, 0.0004, size=n)))
    open_ = close * (1.0 + rng.normal(0, 0.00015, size=n))
    volume = np.abs(rng.normal(80, 25, size=n))
    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }, index=idx)


def test_build_signals_aligns_to_1m_index() -> None:
    """All per-symbol signal series must align to the 1m close index."""
    data = {"BTCUSDT": _make_1m_df(n=3000, seed=11)}
    sigs = build_signals(data, {"indicators": dict(DEFAULT_INDICATORS)})
    assert "BTCUSDT" in sigs
    sig = sigs["BTCUSDT"]
    idx = sig["df"].index
    assert len(idx) == 3000
    # feature series length and index equality with 1m df
    for key in ("poc_15m", "vah_15m", "val_15m", "poc_2h", "vah_2h", "val_2h",
                "poc_avg", "vah_avg", "val_avg", "atr", "trend_2h",
                "side_15m", "touch_vah", "touch_val", "side_hint"):
        s = sig[key]
        assert len(s) == len(idx)
        assert (s.index == idx).all(), f"{key} not aligned to 1m index"


def test_synthetic_run_backtest_executes_and_records_trades() -> None:
    """End-to-end on a synthetic single-symbol dataset: must complete,
    record >0 trades, and produce a finite Sharpe + MDD."""
    cfg = {"indicators": dict(DEFAULT_INDICATORS)}
    data = {"BTCUSDT": _make_1m_df(n=5000, seed=42)}
    res = run_backtest(data, cfg)
    assert "per_symbol" in res and "portfolio" in res
    assert len(res["per_symbol"]) == 1
    sym = res["per_symbol"][0]
    n_trades = len(sym["trades"])
    assert n_trades > 0, "expected at least one H2 trade on the synthetic tape"
    # every trade must have a finite pnl_pct
    pnls = [t["pnl_pct"] for t in sym["trades"]]
    assert all(np.isfinite(p) for p in pnls)
    # daily-resampled Sharpe must be finite
    idx = sym["trades"][0]["entry_ts"]
    bar_return = sym["bar_return"]
    n_bars = sym["n_bars"]
    full_idx = pd.date_range(sym["span_start"], periods=n_bars, freq="1min")
    sr = sharpe_daily_resampled(bar_return, full_idx)
    assert np.isfinite(sr["sharpe_daily_resampled"])
    assert np.isfinite(sr["annualized_return_daily"])
    pfdd = profit_factor_and_mdd(bar_return, 100000.0)
    assert np.isfinite(pfdd["max_drawdown_pct"])


def test_min_holding_bars_prevents_immediate_exit() -> None:
    """With min_holding_bars=8, no trade may exit in fewer than 8 bars."""
    cfg = {"indicators": dict(DEFAULT_INDICATORS,
                               **{"min_holding_bars": 8})}
    data = {"BTCUSDT": _make_1m_df(n=4000, seed=99)}
    res = run_backtest(data, cfg)
    sym = res["per_symbol"][0]
    if sym["trades"]:
        min_bars = min(int(t["bars_held"]) for t in sym["trades"])
        assert min_bars >= 8, (
            f"expected every trade to be held >= 8 bars, got min {min_bars}"
        )


def test_daily_returns_resamples_to_daily_frequency() -> None:
    """daily_returns must aggregate to ~1 sample per day on 1m bar returns."""
    n = 60 * 24 * 3  # 3 days at 1m
    idx = pd.date_range("2026-01-01", periods=n, freq="1min")
    rng = np.random.default_rng(0)
    bar_ret = rng.normal(0, 0.001, size=n)
    dr = daily_returns(bar_ret, idx)
    # 3 days -> 2 daily pct_changes between consecutive day-end equities
    assert len(dr) == 2, f"expected 2 daily points, got {len(dr)}"
    # All entries finite
    assert np.isfinite(dr.to_numpy()).all()