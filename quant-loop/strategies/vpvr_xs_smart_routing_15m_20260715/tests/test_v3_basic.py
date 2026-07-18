"""Smoke test for V3_xs_smart_routing (iter#105)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import VARIANT_KEY, build_signal, run_backtest  # noqa: E402


def _toy_df(n: int = 1500) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    rets = rng.normal(0.0, 0.005, size=n)
    price = 30000.0 * np.cumprod(1.0 + rets)
    df = pd.DataFrame({
        "open": price * (1.0 + rng.normal(0.0, 0.0005, size=n)),
        "high": price * (1.0 + np.abs(rng.normal(0.0, 0.0015, size=n))),
        "low": price * (1.0 - np.abs(rng.normal(0.0, 0.0015, size=n))),
        "close": price,
        "volume": rng.uniform(50.0, 200.0, size=n),
        "quote_volume": rng.uniform(5e5, 2e6, size=n),
        "trades": rng.integers(10, 500, size=n).astype(float),
        "taker_buy_base": rng.uniform(20.0, 130.0, size=n),
        "taker_buy_quote": rng.uniform(2e5, 1.3e6, size=n),
    })
    df.index = pd.date_range("2024-01-01", periods=n, freq="15min")
    return df


def test_v3_imports() -> None:
    assert VARIANT_KEY == "vpvr_xs_smart_routing_15m_20260715"


def test_v3_build_signal_no_crash() -> None:
    df = _toy_df(1500)
    cfg = {
        "iteration": 105,
        "starting_capital_usd": 100000.0,
        "params": {
            "vpvr_window_bars": 96, "vpvr_bins": 24, "value_area_pct": 0.70,
            "atr_period": 14, "microprice_lookback_bars": 32,
            "microprice_z_entry_threshold": 1.8, "near_poc_atr_k": 1.0,
            "cooldown_bars": 6,
        },
    }
    sig = build_signal(df, cfg)
    assert isinstance(sig, pd.Series)
    assert len(sig) == len(df)
    assert set(sig.unique()).issubset({-1, 0, 1})


def test_v3_run_backtest_no_crash() -> None:
    df = _toy_df(1500)
    cfg = {
        "iteration": 105, "_symbol": "BTCUSDT",
        "starting_capital_usd": 100000.0,
        "params": {
            "vpvr_window_bars": 96, "vpvr_bins": 24, "value_area_pct": 0.70,
            "atr_period": 14, "microprice_lookback_bars": 32,
            "microprice_z_entry_threshold": 1.8, "microprice_z_extreme_threshold": 3.5,
            "microprice_z_exit_threshold": 0.4, "twap_slices": 4,
            "twap_slice_atr_fraction": 0.25, "volaware_cancel_replace_atr_k": 1.5,
            "near_poc_atr_k": 1.0, "max_concurrent_trades": 1, "cooldown_bars": 6,
            "time_stop_bars": 96, "fee_bps_per_fill": 4.0,
            "slippage_bps_per_fill": 2.0, "risk_per_trade_pct": 0.005,
        },
    }
    res = run_backtest(df, cfg)
    assert "trades" in res and "equity" in res
    assert len(res["equity"]) == len(df)
    assert res["equity"][0] == cfg["starting_capital_usd"]
    for t in res["trades"]:
        assert t.exit_reason != ""
        assert 1 <= t.twap_slices_used <= 4
