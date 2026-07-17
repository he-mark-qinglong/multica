"""Unit tests for the VPVR micro-reversion 1h strategy.

These tests use a tiny deterministic OHLCV fixture, not mocks of the
strategy. The fixture is constructed so the VPVR profile + funding-proxy
filter combine to produce predictable signals:

    * bars 0..199      — quiet, low-vol, ranging → indicators seed, no entries
    * bars 200..219    — sharp drop with strongly negative 8h return proxy
                          → at least one long_entry fires (close < VAL)
    * bars 220..260    — slow drift back toward the POC
                          → exit triggers as price reverts

The point of the tests is the *logic*, not the magnitude of returns.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Make the strategy dir importable when running ``pytest`` from any cwd.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import (  # noqa: E402
    annotate,
    baseline_hold,
    funding_proxy,
    run_backtest,
    true_range,
    vpvr_profile,
    wilder_atr,
)

CFG_PATH = ROOT / "config.json"


def _cfg() -> dict:
    return json.loads(CFG_PATH.read_text())


def _build_fixture(n: int = 400, seed: int = 7) -> pd.DataFrame:
    """Deterministic 1h OHLCV fixture.

    Phase 1 (first 200 bars) is flat around 100 with tiny noise so all
    indicators have time to seed (VPVR 168h, ATR 14, funding proxy 8h).
    Phase 2 (bars 200..219) drops sharply with negative 8h return so the
    long_entry gate (close < VAL, funding_proxy <= min) fires.
    Phase 3 (bars 220..260) drifts back up toward the prior band level so the
    exit rules (target_to_poc) trigger.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = np.empty(n)
    p1 = min(200, n)
    p2 = min(220, n)
    p3 = min(300, n)
    close[:p1] = 100.0 + rng.normal(0.0, 0.05, size=p1)
    if p2 > p1:
        n_drop = p2 - p1
        close[p1:p2] = 100.0 - np.cumsum(rng.uniform(0.5, 0.8, size=n_drop)) * 0.4
    if p3 > p2:
        n_rise = p3 - p2
        close[p2:p3] = close[p1] + np.cumsum(rng.uniform(0.05, 0.2, size=n_rise))
    if n > p3:
        close[p3:] = close[p3 - 1] + rng.normal(0.0, 0.1, size=n - p3)

    high = close + rng.uniform(0.05, 0.2, size=n)
    low = close - rng.uniform(0.05, 0.2, size=n)
    open_ = close + rng.normal(0.0, 0.02, size=n)

    volume = np.full(n, 50.0)
    if p2 > p1:
        volume[p1:p2] = 80.0  # participation during the drop

    df = pd.DataFrame(
        {
            "open": open_, "high": high, "low": low,
            "close": close, "volume": volume,
        },
        index=dates,
    )
    df.index.name = "openTime"
    return df


def test_true_range_first_bar_equals_high_minus_low():
    df = _build_fixture(20)
    tr = true_range(df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy())
    assert tr[0] == df["high"].iloc[0] - df["low"].iloc[0]
    assert tr[1] >= tr[0] - 1e-9


def test_wilder_atr_matches_expected_shape():
    df = _build_fixture(300)
    atr = wilder_atr(df, period=14)
    assert atr.isna().iloc[:13].all()
    assert not atr.isna().iloc[14:].any()
    assert (atr.iloc[14:] > 0).all()


def test_funding_proxy_nan_warmup_then_returns():
    df = _build_fixture(300)
    proxy = funding_proxy(df["close"], lookback=8)
    assert proxy.isna().iloc[:8].all()
    assert not proxy.isna().iloc[8:].any()


def test_vpvr_profile_returns_poc_vah_val_with_correct_warmup():
    df = _build_fixture(300)
    poc, vah, val = vpvr_profile(
        df["high"].to_numpy(), df["low"].to_numpy(),
        df["close"].to_numpy(), df["volume"].to_numpy(),
        lookback=168, n_bins=24,
    )
    assert np.isnan(poc[:167]).all()
    assert not np.isnan(poc[167:]).any()
    assert np.isnan(vah[:167]).all()
    assert not np.isnan(vah[167:]).any()
    assert np.isnan(val[:167]).all()
    assert not np.isnan(val[167:]).any()
    # VAH should be >= POC, VAL should be <= POC
    valid = ~np.isnan(poc)
    assert (vah[valid] >= poc[valid] - 1e-9).all()
    assert (val[valid] <= poc[valid] + 1e-9).all()


def test_annotate_emits_expected_columns():
    df = _build_fixture(300)
    cfg = _cfg()
    out = annotate(df, cfg)
    expected = {
        "atr", "funding_proxy", "vpvr_poc", "vpvr_vah", "vpvr_val",
        "long_entry", "short_entry", "entry_signal",
    }
    assert expected.issubset(out.columns)


def test_long_entry_fires_when_close_below_val_and_funding_proxy_negative():
    df = _build_fixture(300)
    cfg = _cfg()
    out = annotate(df, cfg)
    # Phase 2 (bars 200..219) drops sharply so the long_entry gate fires.
    assert out["long_entry"].iloc[200:220].any(), "expected at least one long entry in the drop phase"


def test_run_backtest_produces_trade_and_equity_curve():
    df = _build_fixture(400)
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    result = run_backtest(df, cfg)
    assert result.n_trades >= 1
    assert len(result.trades) >= 1
    assert not result.equity_curve.empty
    assert result.equity_curve.iloc[0] == cfg["starting_capital_usd"]
    assert result.equity_curve.iloc[-1] > 0


def test_baseline_hold_matches_pnl_math():
    df = _build_fixture(40)
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    hold = baseline_hold(df, cfg)
    assert hold.n_trades == 1
    expected_pct = df["close"].iloc[-1] / df["close"].iloc[0] - 1.0
    # Cost-adjust tolerance
    assert abs(hold.total_return - expected_pct) < 0.01


def test_run_backtest_records_exit_reason():
    df = _build_fixture(400)
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    result = run_backtest(df, cfg)
    reasons = {t.reason for t in result.trades}
    assert any(r != "" for r in reasons)
    # At least one of the strategy's exit reasons must appear.
    assert any(
        r.startswith("target_to_poc")
        or r.startswith("stop_loss")
        or r.startswith("take_profit")
        or r.startswith("time_stop")
        or r == "force_close_eod"
        for r in reasons
    )


def test_run_backtest_handles_flat_input():
    """Pure-flat input must not crash and must produce zero trades."""
    dates = pd.date_range("2025-01-01", periods=400, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100.0,
        },
        index=dates,
    )
    df.index.name = "openTime"
    cfg = _cfg()
    cfg["_symbol"] = "TEST"
    result = run_backtest(df, cfg)
    assert result.n_trades == 0