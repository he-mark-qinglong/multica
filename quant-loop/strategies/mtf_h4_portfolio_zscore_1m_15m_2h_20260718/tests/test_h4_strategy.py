"""Tests for the H4 strategy module (mtf_xs_pairs_1m_15m_2h_h4_20260718)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_STRATEGY = _HERE.parent
_QUANT = _STRATEGY.parent
_INDICATORS = _QUANT / "_indicators"
for p in (str(_STRATEGY), str(_INDICATORS), str(_QUANT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from _indicators.mtf_xs_pairs_base_20260718 import (  # noqa: E402
    aggregate_ohlcv,
    build_h4_portfolio,
    build_h4_signals,
    daily_returns,
    ema,
    run_backtest,
    sharpe_daily_resampled,
)


def _make_1m_df(n=2400, start="2026-01-01", seed=0, drift=0.0001):
    """Build a synthetic 1m OHLCV DataFrame with optional drift."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1min")
    ret = rng.normal(drift, 0.001, size=n)
    close = 100.0 * np.exp(np.cumsum(ret))
    high = close * (1.0 + np.abs(rng.normal(0, 0.0005, size=n)))
    low = close * (1.0 - np.abs(rng.normal(0, 0.0005, size=n)))
    open_ = close * (1.0 + rng.normal(0, 0.0002, size=n))
    volume = np.abs(rng.normal(100, 20, size=n))
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                          "close": close, "volume": volume}, index=idx)


def _load_config():
    cfg_path = _STRATEGY / "config.json"
    return json.loads(cfg_path.read_text())


def test_h4_config_keys_present():
    """The H4 config exposes all keys required by build_h4_signals + portfolio caps."""
    cfg = _load_config()
    for key in ("instruments", "pairs", "indicators", "sizing",
                "fees_bps_per_side", "slippage_bps_per_side",
                "hard_gates", "walk_forward"):
        assert key in cfg, f"missing config key: {key}"
    ind = cfg["indicators"]
    for k in ("zscore_lookback_bars", "zscore_entry_threshold",
              "zscore_exit_threshold", "ema_15m_fast", "ema_15m_slow",
              "trend_2h_fast", "trend_2h_slow", "max_holding_bars"):
        assert k in ind, f"missing indicator key: {k}"
    sizing = cfg["sizing"]
    for k in ("per_pair_notional_pct", "gross_cap", "net_cap",
              "corr_window_days", "max_pairs_active"):
        assert k in sizing, f"missing sizing key: {k}"
    assert cfg["sharpe_method"] == "daily_resampled"
    assert cfg["pairs"] == ["BTCUSDT/ETHUSDT", "BTCUSDT/SOLUSDT",
                            "ETHUSDT/SOLUSDT"]


def test_h4_signals_smoke():
    """build_h4_signals returns well-formed per-pair dicts."""
    d1m = {
        "BTCUSDT": _make_1m_df(n=2400, seed=11, drift=0.0002),
        "ETHUSDT": _make_1m_df(n=2400, seed=22, drift=0.0001),
        "SOLUSDT": _make_1m_df(n=2400, seed=33, drift=0.0003),
    }
    cfg = _load_config()
    ind = cfg["indicators"].copy()
    ind["zscore_lookback_bars"] = 240
    ind["ema_15m_fast"] = 8
    ind["ema_15m_slow"] = 21
    cfg["indicators"] = ind
    sig = build_h4_signals(d1m, cfg)
    assert set(sig.keys()) == set(cfg["pairs"])
    for pair, s in sig.items():
        assert "z" in s
        assert "price_ema_15m" in s
        assert "trend_a" in s["price_ema_15m"]
        assert "trend_b" in s["price_ema_15m"]
        assert "trend_2h" in s
        # trend_2h is integer-valued (-1, 0, +1)
        assert s["trend_2h"].dropna().isin([-1, 0, 1]).all()


def test_h4_15m_filter_blocks_counter_trend():
    """build_h4_signals produces trend_a/trend_b that can be used to reject entries.

    Concretely, when a is in a clear 15m uptrend and b is in a clear 15m
    downtrend, the 15m-direction filter should ALLOW a long_a_short_b
    entry (the per-pair backtest loop checks ``ta >= 1 and tb <= -1``).
    """
    n = 4000
    # BTC: uptrend on 15m (positive drift); ETH: downtrend on 15m (negative drift)
    btc = _make_1m_df(n=n, seed=101, drift=0.0008)
    eth = _make_1m_df(n=n, seed=202, drift=-0.0008)
    d1m = {"BTCUSDT": btc, "ETHUSDT": eth}
    cfg = {
        "pairs": ["BTCUSDT/ETHUSDT"],
        "indicators": {
            "zscore_lookback_bars": 240,
            "zscore_entry_threshold": 2.5,
            "zscore_exit_threshold": 0.5,
            "regime_break_threshold": 3.5,
            "ema_15m_fast": 8,
            "ema_15m_slow": 21,
            "trend_2h_fast": 8,
            "trend_2h_slow": 34,
            "max_holding_bars": 240,
        },
        "sizing": {"max_pairs_active": 1},
    }
    sig = build_h4_signals(d1m, cfg)
    s = sig["BTCUSDT/ETHUSDT"]
    # In the second half of the synthetic series (after the 15m EMA has
    # warmed up), BTC should be in 15m uptrend (+1) most of the time and
    # ETH in 15m downtrend (-1) most of the time.
    warm = max(int(cfg["indicators"]["ema_15m_slow"]) * 15 * 5, 240)
    ta = s["price_ema_15m"]["trend_a"].iloc[warm:]
    tb = s["price_ema_15m"]["trend_b"].iloc[warm:]
    assert (ta == 1).mean() > 0.6
    assert (tb == -1).mean() > 0.6


def test_h4_portfolio_respects_gross_cap():
    """build_h4_portfolio caps gross exposure at the configured gross_cap."""
    n_bars = 1000
    n_pairs = 3
    per_pair = []
    for k in range(n_pairs):
        # Per-pair bar returns summing to ~2x gross_cap to test the cap
        br = np.full(n_bars, 0.001 * (k + 1) / n_pairs)
        per_pair.append({
            "pair": f"AAAUSDT/BBBUSDT_{k}",
            "bar_return": br,
            "n_bars": n_bars,
            "span_start": None,
            "span_end": None,
            "trades": [],
        })
    cfg = {
        "sizing": {
            "per_pair_notional_pct": 0.05,
            "max_pairs_active": 3,
            "gross_cap": 0.06,
            "net_cap": 0.04,
            "corr_window_days": 60,
            "corr_high_threshold": 0.6,
            "starting_capital_usd": 100000.0,
        }
    }
    res = build_h4_portfolio(per_pair, cfg, starting_capital=100000.0)
    gross = res["sizing"]["gross_notional_pct"]
    assert gross <= cfg["sizing"]["gross_cap"] + 1e-9, (
        f"gross_notional_pct {gross} exceeds gross_cap "
        f"{cfg['sizing']['gross_cap']}"
    )


def test_h4_sharpe_sign_on_synthetic():
    """sharpe_daily_resampled is consistent on synthetic bar returns."""
    rng = np.random.default_rng(7)
    idx = pd.date_range("2025-01-01", periods=20000, freq="1min")
    pos = rng.normal(0.0002, 0.001, size=20000)
    neg = rng.normal(-0.0002, 0.001, size=20000)
    sr_pos = sharpe_daily_resampled(pos, idx)
    sr_neg = sharpe_daily_resampled(neg, idx)
    assert sr_pos["sharpe_daily_resampled"] > 0
    assert sr_neg["sharpe_daily_resampled"] < 0


def test_h4_run_backtest_returns_wellformed():
    """run_backtest dispatching to H4 returns per_pair + portfolio + sizing."""
    d1m = {
        "BTCUSDT": _make_1m_df(n=4000, seed=44, drift=0.0002),
        "ETHUSDT": _make_1m_df(n=4000, seed=55, drift=0.0001),
        "SOLUSDT": _make_1m_df(n=4000, seed=66, drift=0.0003),
    }
    cfg = _load_config()
    cfg["indicators"]["zscore_lookback_bars"] = 240
    res = run_backtest(d1m, cfg, funding=None)
    assert "per_pair" in res
    assert "portfolio" in res
    assert len(res["per_pair"]) == 3
    assert "sizing" in res["portfolio"]
    assert "gross_notional_pct" in res["portfolio"]["sizing"]
    assert "mean_off_diag_corr" in res["portfolio"]["sizing"]
    print("h4 portfolio sizing:", res["portfolio"]["sizing"])
