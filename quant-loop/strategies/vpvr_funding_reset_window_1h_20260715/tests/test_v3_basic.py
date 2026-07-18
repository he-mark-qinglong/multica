"""Smoke test for V3_funding_reset_window (iter#108)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import (  # noqa: E402
    VARIANT_KEY, build_signal, run_backtest,
    _funding_z, _reset_window_flag,
)

CFG_BASE = {
    "iteration": 108,
    "starting_capital_usd": 100000.0,
    "params": {
        "vpvr_window_bars": 48,
        "vpvr_bins": 20,
        "value_area_pct": 0.70,
        "atr_period": 14,
        "poc_distance_z_entry": 1.2,
        "near_poc_atr_k": 1.0,
        "pre_reset_blackout_bars": 1,
        "post_reset_drift_bars": 1,
        "funding_reset_hours_utc": [0, 4, 8, 12, 16, 20],
        "funding_z_window_bars": 96,
        "funding_z_entry_threshold": 1.2,
        "tp_atr_k": 2.0,
        "sl_atr_k": 1.0,
        "vol_target_horizon_bars": 6,
        "time_stop_bars": 6,
        "max_holding_bars": 12,
        "min_gap_bars_between_trades": 4,
        "fee_bps_per_fill": 4.0,
        "slippage_bps_per_fill": 2.0,
        "risk_per_trade_pct": 0.01,
    },
}


def _toy_df(n=2000):
    rng = np.random.default_rng(707)
    rets = rng.normal(0.0, 0.01, size=n)
    price = 30000.0 * np.cumprod(1.0 + rets)
    # Funding: 8h cadence (every 8 bars in 1h timeframe)
    funding = np.zeros(n)
    for i in range(0, n, 8):
        funding[i] = rng.normal(0.0, 0.0005)
    df = pd.DataFrame({
        "open": price * (1.0 + rng.normal(0.0, 0.001, size=n)),
        "high": price * (1.0 + np.abs(rng.normal(0.0, 0.002, size=n))),
        "low": price * (1.0 - np.abs(rng.normal(0.0, 0.002, size=n))),
        "close": price,
        "volume": rng.uniform(1000.0, 5000.0, size=n),
        "quote_volume": rng.uniform(1e7, 5e7, size=n),
        "trades": rng.integers(100, 5000, size=n).astype(float),
        "taker_buy_base": rng.uniform(500.0, 2500.0, size=n),
        "taker_buy_quote": rng.uniform(5e6, 2.5e7, size=n),
        "fundingRate": funding.astype(np.float64),
    })
    # Start at 2024-01-01 00:00 UTC to align reset windows
    df.index = pd.date_range("2024-01-01 00:00", periods=n, freq="1h")
    return df


def test_v3_imports():
    assert VARIANT_KEY == "vpvr_funding_reset_window_1h_20260715"


def test_v3_funding_z_no_crash():
    df = _toy_df(500)
    z = _funding_z(df["fundingRate"], 96)
    assert len(z) == len(df)
    assert np.isfinite(z[200])  # after enough bars for window


def test_v3_reset_window_flag_aligment():
    # Build a 24h index starting at midnight UTC: 3 resets per day at 00/08/16
    idx = pd.date_range("2024-01-01 00:00", periods=24, freq="1h")
    flag = _reset_window_flag(idx, [0, 8, 16], pre_bars=1, post_bars=1)
    # 00:00 -> reset, 07-09 drift covers 07,08,09; 15-17 covers 15,16,17
    assert flag[0] == 1
    assert flag[8] == 1
    assert flag[16] == 1
    assert flag[1] == 1
    assert flag[7] == 1
    assert flag[9] == 1
    # Far from any reset
    assert flag[5] == 0
    assert flag[12] == 0


def test_v3_build_signal_no_crash():
    df = _toy_df(2000)
    sig = build_signal(df, CFG_BASE)
    assert isinstance(sig, pd.Series)
    assert len(sig) == len(df)
    assert sig.index.equals(df.index)
    assert set(sig.unique()).issubset({-1, 0, 1})


def test_v3_run_backtest_no_crash():
    df = _toy_df(2500)
    cfg = dict(CFG_BASE); cfg["_symbol"] = "BTCUSDT"
    res = run_backtest(df, cfg)
    assert "trades" in res and "equity" in res
    assert len(res["equity"]) == len(df)
