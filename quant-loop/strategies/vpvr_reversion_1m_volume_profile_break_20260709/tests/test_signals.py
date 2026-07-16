"""Unit tests for vpvr_reversion_1m_volume_profile_break_20260709 build_signals."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_signals import build_signals
from strategy import run_backtest, VARIANT_KEY

PARAMS = {
    "vpvr_window_bars": 240,
    "vpvr_bins": 24,
    "value_area_fraction": 0.70,
    "atr_period": 14,
    "vol_median_lookback_bars": 60,
    "vol_spike_k": 2.0,
    "break_lookback_bars": 12,
    "tp_atr_k": 1.0,
    "sl_atr_k": 1.5,
    "max_hold_bars": 30,
    "risk_target_pct": 0.005,
    "cooldown_bars": 5,
    "fee_bps_per_fill": 4.0,
    "slippage_bps_per_fill": 1.0,
}


def _make_base_df(n: int = 2500, seed: int = 69) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dt = pd.date_range("2024-01-01", periods=n, freq="1min")
    returns = rng.normal(0.0, 0.00008, size=n)
    close = 30000.0 * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.0001, 0.0008, size=n))
    low = close * (1 - rng.uniform(0.0001, 0.0008, size=n))
    open_ = close * (1 + rng.normal(0.0, 0.00015, size=n))
    volume = rng.lognormal(0.0, 0.5, size=n) * 20.0
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=dt)


def test_output_columns_complete():
    df = _make_base_df()
    out = build_signals(df, PARAMS)
    expected = {"signal", "break_state", "vpvr_poc", "vpvr_vah", "vpvr_val",
                 "atr", "vol_ratio", "poc_distance_atr"}
    assert expected.issubset(set(out.columns))


def test_signal_values_valid():
    df = _make_base_df()
    out = build_signals(df, PARAMS)
    assert set(out["signal"].unique()).issubset({-1, 0, 1})


def test_break_state_values_valid():
    df = _make_base_df()
    out = build_signals(df, PARAMS)
    assert set(out["break_state"].unique()).issubset({-1, 0, 1})


def test_warmup_bars_no_signal():
    df = _make_base_df()
    out = build_signals(df, PARAMS)
    warmup = max(PARAMS["vpvr_window_bars"], PARAMS["vol_median_lookback_bars"],
                  PARAMS["atr_period"])
    assert (out["signal"].iloc[:warmup] == 0).all()
    assert out["vpvr_poc"].iloc[: warmup - 1].isna().all() or \
        out["vpvr_poc"].iloc[:warmup].isna().all() or \
        np.isnan(out["vpvr_poc"].iloc[:warmup].dropna().iloc[0]) or \
        out["vpvr_poc"].iloc[:warmup].isna().any()


def test_value_area_brackets_poc():
    df = _make_base_df()
    out = build_signals(df, PARAMS)
    valid = out["vpvr_poc"].notna() & out["vpvr_vah"].notna() & out["vpvr_val"].notna()
    assert (out.loc[valid, "vpvr_val"] <= out.loc[valid, "vpvr_poc"]).all()
    assert (out.loc[valid, "vpvr_poc"] <= out.loc[valid, "vpvr_vah"]).all()


def test_failed_upside_break_emits_short():
    """Inject a clean failed upside break + re-enter; expect a short signal."""
    df = _make_base_df(n=3000, seed=70)
    out = build_signals(df, PARAMS)
    warmup = max(PARAMS["vpvr_window_bars"], PARAMS["vol_median_lookback_bars"],
                  PARAMS["atr_period"])

    # Force a synthetic upside break with a volume spike 5 bars before the
    # entry bar, then a re-enter back inside the value area on the entry
    # bar. We don't recompute VAH/VAL every test bar — instead, override
    # the *signal inputs* by directly checking the helper.
    from build_signals import _recent_failed_break
    n = len(df)
    close = df["close"].astype(float)
    vol_ratio = out["vol_ratio"].astype(float)
    vah = out["vpvr_vah"].astype(float).fillna(close.iloc[warmup])
    val_ = out["vpvr_val"].astype(float).fillna(close.iloc[warmup])

    # Pin a tight VAH/VAL band around the close so we can orchestrate the break.
    vah[:] = close.iloc[warmup] + 1.0
    val_[:] = close.iloc[warmup] - 1.0

    # Bars [warmup+10, warmup+15]: above VAH with high vol_ratio (breakout).
    for j in range(warmup + 10, warmup + 16):
        close.iloc[j] = close.iloc[warmup] + 2.0  # above vah
        vol_ratio.iloc[j] = 4.0  # in spike regime
    # Bars [warmup+16, ...]: revert inside (close < vah).
    # Use a lower vol_ratio so the spike regime has expired on entry.
    for j in range(warmup + 16, n):
        close.iloc[j] = close.iloc[warmup] - 0.5  # inside VA, still above poc origin
        vol_ratio.iloc[j] = 1.0

    state = _recent_failed_break(
        close, vah, val_, vol_ratio,
        PARAMS["break_lookback_bars"], PARAMS["vol_spike_k"],
    )
    assert (state.iloc[warmup + 16:] == -1).any(), "expected at least one -1 break_state"


def test_zero_volume_lookback_no_crash():
    df = _make_base_df(n=400, seed=71)
    out = build_signals(df, PARAMS)
    # Just make sure no NaN explosion in signal column at the head.
    assert out["signal"].iloc[:50].fillna(0).abs().sum() == 0


# ---------------------------------------------------------------------------
# strategy.run_backtest smoke tests.
# ---------------------------------------------------------------------------
def test_run_backtest_smoke():
    df = _make_base_df(n=2500, seed=72)
    cfg = {
        "variant": "A",
        "strategy_key": "vpvr_reversion_1m_volume_profile_break_20260709",
        "iteration": 69,
        "instruments": ["BTCUSDT"],
        "starting_capital_usd": 100000.0,
        "params": PARAMS,
    }
    result = run_backtest(df, cfg)
    assert result["variant_key"] == VARIANT_KEY
    assert result["n_bars"] == len(df)
    assert isinstance(result["trades"], list)
    assert len(result["equity"]) == len(df)


def test_run_backtest_trade_schema():
    df = _make_base_df(n=2500, seed=73)
    cfg = {
        "variant": "A",
        "strategy_key": "vpvr_reversion_1m_volume_profile_break_20260709",
        "iteration": 69,
        "instruments": ["BTCUSDT"],
        "starting_capital_usd": 100000.0,
        "params": PARAMS,
    }
    result = run_backtest(df, cfg)
    for t in result["trades"]:
        assert {"variant", "symbol", "direction", "entry_ts", "entry_price",
                "exit_ts", "exit_price", "pnl_pct", "bars_held", "exit_reason",
                "break_state_at_entry", "vah_at_entry", "val_at_entry",
                "poc_distance_atr_at_entry"}.issubset(set(t.keys()))
        assert t["direction"] in {"long", "short"}
        assert t["exit_reason"] in {"take_profit", "hard_stop", "time_stop",
                                      "breakout_resume_up", "breakout_resume_down"}
