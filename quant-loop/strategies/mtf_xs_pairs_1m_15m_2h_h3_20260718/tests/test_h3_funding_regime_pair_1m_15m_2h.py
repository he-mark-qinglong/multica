"""Tests for mtf_xs_pairs_1m_15m_2h_h3_20260718 (H3 — funding regime + 1m/15m BTC/SOL pair).

Per SMA-34878 deliverable: tests/ must contain at least one pytest -v PASS.
We cover two surfaces:

1. ``build_h3_signals`` produces the expected series on synthetic BTC/SOL data,
   with funding allowing/blocking entries according to a constructed regime.
2. ``run_backtest`` dispatches the H3 path and returns a well-formed result
   (per_pair, portfolio, bar_return).

The funding-rate skill pack is exercised by building a synthetic funding
Series with a known pattern (positive-and-rising vs flat) and verifying that
``fund_allow`` correctly gates entries.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_STRAT_DIR = _HERE.parent
_INDICATORS_DIR = _STRAT_DIR.parent / "_indicators"
for p in (str(_STRAT_DIR.parent), str(_INDICATORS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import mtf_xs_pairs_base_20260718 as base  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic data builders
# ---------------------------------------------------------------------------

def _make_1m_df(n=4000, start="2026-01-01", seed=0):
    """Synthetic 1m OHLCV with deterministic RNG."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1min")
    ret = rng.normal(0, 0.001, size=n)
    close = 100.0 * np.exp(np.cumsum(ret))
    high = close * (1.0 + np.abs(rng.normal(0, 0.0005, size=n)))
    low = close * (1.0 - np.abs(rng.normal(0, 0.0005, size=n)))
    open_ = close * (1.0 + rng.normal(0, 0.0002, size=n))
    volume = np.abs(rng.normal(100, 20, size=n))
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                          "close": close, "volume": volume}, index=idx)


def _make_funding(start, periods=3, value=0.0002, freq="8h"):
    """Synthetic 8h funding-rate events (canonical Binance usdm cadence)."""
    idx = pd.date_range(start, periods=periods, freq=freq)
    return pd.Series([value] * periods, index=idx, name="fundingRate")


def _h3_cfg():
    """Subset of the campaign config.json fields used by build_h3_signals."""
    return {
        "hypothesis": "H3",
        "pairs": ["BTCUSDT/SOLUSDT"],
        "fees_bps_per_side": 1.0,
        "slippage_bps_per_side": 1.0,
        "starting_capital_usd": 100000.0,
        "indicators": {
            "zscore_lookback_bars": 240,
            "zscore_entry_threshold": 2.0,
            "zscore_exit_threshold": 0.5,
            "regime_break_threshold": 3.5,
            "max_holding_bars": 240,
            "funding_ema_window": 4,
            "funding_filter_threshold": 0.0005,
            "atr_normalize_window": 1440,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_build_h3_signals_with_flat_funding_allows_entry():
    """Flat (zero) funding EMA => fund_allow=1 everywhere, signals carry z."""
    n = 4000
    a = _make_1m_df(n=n, seed=11)
    b = _make_1m_df(n=n, seed=22)
    funding = {
        "BTCUSDT": _make_funding(a.index[0], periods=2, value=0.0),
        "SOLUSDT": _make_funding(a.index[0], periods=2, value=0.0),
    }
    cfg = _h3_cfg()
    out = base.build_h3_signals({"BTCUSDT": a, "SOLUSDT": b}, cfg, funding)
    sig = out["BTCUSDT/SOLUSDT"]
    # Required series
    for k in ("a", "b", "z", "fund_allow", "size_scale"):
        assert k in sig, f"missing key in h3 signals: {k}"
    # Funding is flat at 0 => |funding_ema| < threshold everywhere => allow=1
    allow = sig["fund_allow"]
    assert int(allow.dropna().iloc[0]) == 1
    assert int(allow.dropna().iloc[-1]) == 1
    # z-score has at least some non-nan values after lookback
    assert sig["z"].dropna().shape[0] > 0
    # size_scale stays within clip range
    sc = sig["size_scale"].dropna()
    assert (sc >= 0.5).all() and (sc <= 2.0).all()


def test_build_h3_signals_with_high_funding_blocks_entry():
    """Funding EMA above threshold => fund_allow=0 (entry blocked)."""
    n = 4000
    a = _make_1m_df(n=n, seed=11)
    b = _make_1m_df(n=n, seed=22)
    # 0.001 per 8h event is well above 0.0005 threshold
    funding = {
        "BTCUSDT": _make_funding(a.index[0], periods=3, value=0.001),
        "SOLUSDT": _make_funding(a.index[0], periods=3, value=0.001),
    }
    cfg = _h3_cfg()
    out = base.build_h3_signals({"BTCUSDT": a, "SOLUSDT": b}, cfg, funding)
    sig = out["BTCUSDT/SOLUSDT"]
    # After the first EMA event arrives and 2h bin populates, allow=0.
    # The first ~16h is NaN (no 2h bin yet) and the rest should be 0.
    allow = sig["fund_allow"]
    finite = allow.dropna()
    assert (finite == 0).all(), "high funding should block all entries"


def test_run_backtest_h3_dispatch_smoke():
    """Smoke-test that H3 dispatch returns a well-formed result on tiny data."""
    a = _make_1m_df(n=3000, seed=31)
    b = _make_1m_df(n=3000, seed=32)
    funding = {
        "BTCUSDT": _make_funding(a.index[0], periods=2, value=0.0002),
        "SOLUSDT": _make_funding(a.index[0], periods=2, value=0.0002),
    }
    cfg = _h3_cfg()
    res = base.run_backtest({"BTCUSDT": a, "SOLUSDT": b}, cfg, funding=funding)
    assert "per_pair" in res and "portfolio" in res
    assert len(res["per_pair"]) == 1
    pp = res["per_pair"][0]
    for k in ("trades", "bar_return", "n_bars", "pair"):
        assert k in pp
    assert pp["pair"] == "BTCUSDT/SOLUSDT"
    assert res["portfolio"]["n_bars"] >= 0
    # bar_return has same length as n_bars
    assert len(res["portfolio"]["bar_return"]) == res["portfolio"]["n_bars"]


def test_h3_config_pairs_btc_sol_only():
    """Sanity: campaign config restricts H3 instruments and pairs to BTC/SOL."""
    cfg = json.loads((_STRAT_DIR / "config.json").read_text())
    assert cfg["hypothesis"] == "H3"
    assert set(cfg["instruments"]) == {"BTCUSDT", "SOLUSDT"}
    assert cfg["pairs"] == ["BTCUSDT/SOLUSDT"]
    # Sharpe must be daily-resampled per smark directive
    assert cfg.get("sharpe_method") == "daily_resampled"
    # Hard gates contain the OOS thresholds
    hg = cfg["hard_gates"]
    assert hg["oos_sharpe_min"] == 1.0
    assert hg["oos_annualized_min"] == 0.15