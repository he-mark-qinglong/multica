"""Unit tests for V8 (vpvr_carry_term_8h_20260711)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import (  # noqa: E402
    Trade, _annualisation_factor, _rolling_vpvr, _term_basis_z,
    build_signal, run_backtest, true_range, wilder_atr,
)


def _toy_8h_ohlcv(n: int, seed: int = 13) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.001, 0.015, size=n)
    close = 100.0 * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0.004, 0.002, size=n)))
    low = close * (1 - np.abs(rng.normal(0.004, 0.002, size=n)))
    open_ = close * (1 + rng.normal(0.0, 0.002, size=n))
    volume = np.abs(rng.normal(1_000, 200, size=n))
    funding = rng.normal(0.0001, 0.0003, size=n)
    idx = pd.date_range("2024-01-01", periods=n, freq="8h")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume,
        "fundingRate_binance": funding,
        "fundingRate_alt": np.roll(funding, 1) * 0.85,
        "funding_spread_bps": (np.roll(funding, 1) * 0.85 - funding) * 10000.0,
    }, index=idx)


def test_annualisation_factor_8h():
    import math
    assert abs(_annualisation_factor("8h") - math.sqrt(24 * 365 / 8)) < 1e-9


def test_true_range_and_atr_8h():
    df = _toy_8h_ohlcv(80)
    atr = wilder_atr(df, 14).to_numpy()
    assert np.all(np.isfinite(atr[14:]))
    assert np.all(atr[14:] > 0)


def test_rolling_vpvr_poc_in_window_8h():
    df = _toy_8h_ohlcv(80)
    out = _rolling_vpvr(df["close"].to_numpy(), df["volume"].to_numpy(),
                        window=30, n_bins=20, value_area_pct=0.7)
    poc = out["poc"]
    warm = 30
    assert np.all(np.isfinite(poc[warm:]))


def test_term_basis_z_finite():
    df = _toy_8h_ohlcv(200)
    cfg = json.loads((ROOT / "config.json").read_text())
    z = _term_basis_z(df, cfg["term_basis"]).to_numpy()
    # z_window 60 + window_bars 30 + ewm warmup → safe floor 100.
    assert np.all(np.isfinite(z[150:]))


def test_build_signal_range_8h():
    df = _toy_8h_ohlcv(300)
    cfg = json.loads((ROOT / "config.json").read_text())
    sig = build_signal(df, cfg)
    assert set(sig.unique().tolist()) <= {-1, 0, 1}


def test_run_backtest_records_8h():
    df = _toy_8h_ohlcv(300)
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["_symbol"] = "TEST"
    res = run_backtest(df, cfg)
    assert "trades" in res and "equity" in res
    assert res["n_bars"] == 300
    if res["trades"]:
        t = res["trades"][0]
        assert isinstance(t, Trade)
        assert t.entry_ts < t.exit_ts


def test_run_backtest_end_to_end_real_parquet():
    p = ROOT / "data" / "BTCUSDT__8h.parquet"
    if not p.is_file():
        pytest.skip("8h parquet cache not built yet")
    df = pd.read_parquet(p)
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["_symbol"] = "BTCUSDT"
    res = run_backtest(df, cfg)
    assert res["n_bars"] == len(df)