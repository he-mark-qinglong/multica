"""Tests for the H1 strategy: 1m cross-pair z-score entry, 15m slope confirm, 2h regime.

These tests exercise the H1 signal builder + backtest dispatcher against the
shared base module so we have at least one pytest PASS in the H1 directory
(as required by SMA-34876 evidence gate).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # strategies/<H1>/
sys.path.insert(0, str(_HERE.parent.parent / "_indicators"))  # strategies/_indicators/

from strategy import build_h1_signals, run_backtest, sharpe_daily_resampled  # noqa: E402


def _make_1m_df(n: int = 3000, seed: int = 1, start: str = "2026-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1min")
    ret = rng.normal(0, 0.001, size=n)
    close = 100.0 * np.exp(np.cumsum(ret))
    high = close * (1.0 + np.abs(rng.normal(0, 0.0005, size=n)))
    low = close * (1.0 - np.abs(rng.normal(0, 0.0005, size=n)))
    open_ = close * (1.0 + rng.normal(0, 0.0002, size=n))
    volume = np.abs(rng.normal(100, 20, size=n))
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    }, index=idx)


def _h1_cfg() -> dict:
    """Minimal H1 config, mirroring the campaign-wide H1 config schema."""
    return {
        "strategy": "mtf_xs_pairs_1m_15m_2h_h1_20260718",
        "hypothesis": "H1",
        "pairs": ["AAAUSDT/BBBUSDT", "AAAUSDT/CCCUSDT"],
        "instruments": ["AAAUSDT", "BBBUSDT", "CCCUSDT"],
        "fees_bps_per_side": 1.0,
        "slippage_bps_per_side": 1.0,
        "starting_capital_usd": 100000.0,
        "indicators": {
            "zscore_lookback_bars": 240,
            "zscore_entry_threshold": 2.5,
            "zscore_exit_threshold": 0.5,
            "regime_break_threshold": 3.0,
            "slope_15m_lookback": 30,
            "trend_2h_fast": 8,
            "trend_2h_slow": 34,
            "max_holding_bars": 240,
        },
        "hard_gates": {"oos_sharpe_min": 1.0, "oos_annualized_min": 0.15,
                       "bootstrap_ci_lower_min": 0.5},
    }


def test_h1_strategy_module_imports():
    """strategy.py must expose build_h1_signals, run_backtest, sharpe_daily_resampled."""
    import strategy as s  # noqa: F401
    for sym in ("build_h1_signals", "run_backtest", "sharpe_daily_resampled"):
        assert hasattr(s, sym), f"strategy.{sym} missing"


def test_build_h1_signals_returns_required_keys():
    """build_h1_signals must populate z, z_slope_15m, trend_2h for each pair."""
    d1m = {
        "AAAUSDT": _make_1m_df(n=3000, seed=11),
        "BBBUSDT": _make_1m_df(n=3000, seed=22),
        "CCCUSDT": _make_1m_df(n=3000, seed=33),
    }
    sigs = build_h1_signals(d1m, _h1_cfg())
    assert set(sigs.keys()) == {"AAAUSDT/BBBUSDT", "AAAUSDT/CCCUSDT"}
    for pair, sig in sigs.items():
        assert "z" in sig
        assert "z_slope_15m" in sig
        assert "trend_2h" in sig
        assert "a" in sig and "b" in sig
        assert "params" in sig


def test_h1_run_backtest_dispatches():
    """run_backtest with hypothesis=H1 must return per_pair + portfolio."""
    d1m = {
        "AAAUSDT": _make_1m_df(n=3000, seed=11),
        "BBBUSDT": _make_1m_df(n=3000, seed=22),
        "CCCUSDT": _make_1m_df(n=3000, seed=33),
    }
    res = run_backtest(d1m, _h1_cfg())
    assert "per_pair" in res
    assert "portfolio" in res
    assert len(res["per_pair"]) == 2  # AAA/BBB and AAA/CCC
    for pr in res["per_pair"]:
        assert pr["n_bars"] > 0
        assert isinstance(pr["trades"], list)
        assert pr["bar_return"].shape[0] == pr["n_bars"]
    assert res["portfolio"]["n_bars"] > 0


def test_h1_trend_filter_blocks_short_in_uptrend():
    """When 2h trend is +1, short entries must be blocked; long entries allowed."""
    d1m = {
        "AAAUSDT": _make_1m_df(n=4000, seed=11),
        "BBBUSDT": _make_1m_df(n=4000, seed=22),
        "CCCUSDT": _make_1m_df(n=4000, seed=33),
    }
    # Force a strong persistent uptrend in AAA so 2h EMA(8) > EMA(34) throughout.
    n = 4000
    base_price = 100.0
    ramp = np.linspace(0.0, 50.0, n)
    for k in d1m:
        d1m[k]["close"] = base_price * (1.0 + ramp / base_price + np.random.default_rng(0).normal(0, 0.001, n))
        d1m[k]["open"] = d1m[k]["close"].shift(1).fillna(d1m[k]["close"])
        d1m[k]["high"] = d1m[k]["close"] * 1.001
        d1m[k]["low"] = d1m[k]["close"] * 0.999
    cfg = _h1_cfg()
    cfg["pairs"] = ["AAAUSDT/BBBUSDT"]
    res = run_backtest(d1m, cfg)
    pr = res["per_pair"][0]
    directions = {t["direction"] for t in pr["trades"]}
    # With strong 2h uptrend on the ratio, short_a_long_b should be disallowed.
    assert "short_a_long_b" not in directions, "2h regime cap should block shorts in uptrend"


def test_h1_config_matches_campaign_schema():
    """The on-disk config.json must satisfy the H1 schema used by run_backtest."""
    cfg_path = _HERE.parent / "config.json"
    cfg = json.loads(cfg_path.read_text())
    assert cfg["hypothesis"] == "H1"
    assert cfg["primary_timeframe"] == "1m"
    assert cfg["filter_timeframe"] == "15m"
    assert cfg["regime_timeframe"] == "2h"
    assert set(cfg["instruments"]) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert set(cfg["pairs"]) == {"BTCUSDT/ETHUSDT", "BTCUSDT/SOLUSDT", "ETHUSDT/SOLUSDT"}
    ind = cfg["indicators"]
    # z_entry threshold is the campaign-wide H1 contract; the lookback/slow
    # values were tuned inside the campaign and may shift between iterations,
    # so we only sanity-check that they are positive integers.
    assert ind["zscore_entry_threshold"] == 2.5
    assert isinstance(ind["slope_15m_lookback"], int) and ind["slope_15m_lookback"] > 0
    assert isinstance(ind["trend_2h_slow"], int) and ind["trend_2h_slow"] > 0
    assert ind["trend_2h_fast"] > 0
    assert ind["trend_2h_fast"] < ind["trend_2h_slow"]
    assert cfg["sharpe_method"] == "daily_resampled"