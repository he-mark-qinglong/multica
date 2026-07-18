"""Unit tests for V3 (iter#75) — xs-pair z-score + VPVR confluence axis."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT))

from data_loader import load_all
from strategy import (
    _annualisation_factor,
    _rolling_vpvr,
    pair_zscore,
    resample_ohlcv,
    run_backtest,
    true_range,
    wilder_atr,
)
from strategy import run_pair_backtest


def _toy_pair_ohlcv(n: int = 1500, seed: int = 31) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Two correlated instruments."""
    rng = np.random.default_rng(seed)
    common = rng.normal(0.001, 0.02, size=n)
    spec_a = rng.normal(0.0, 0.005, size=n)
    spec_b = rng.normal(0.0, 0.005, size=n)
    p_a = 100.0 * np.cumprod(1 + common + spec_a)
    p_b = 60.0 * np.cumprod(1 + common * 0.5 + spec_b)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h")
    a = pd.DataFrame({
        "open": p_a, "high": p_a * 1.005, "low": p_a * 0.995,
        "close": p_a, "volume": np.abs(rng.normal(1000, 200, size=n)),
    }, index=idx)
    b = pd.DataFrame({
        "open": p_b, "high": p_b * 1.005, "low": p_b * 0.995,
        "close": p_b, "volume": np.abs(rng.normal(800, 150, size=n)),
    }, index=idx)
    return a, b


def test_annualisation_factor_4h():
    assert _annualisation_factor("4h") > 0


def test_resample_ohlcv_aligns_bars():
    idx = pd.date_range("2024-01-01", periods=100, freq="1h")
    df = pd.DataFrame({
        "open": np.linspace(100, 101, 100),
        "high": np.linspace(101, 102, 100),
        "low": np.linspace(99, 100, 100),
        "close": np.linspace(100, 101, 100),
        "volume": np.ones(100) * 10.0,
    }, index=idx)
    df_4h = resample_ohlcv(df, rule="4h")
    # 100 1h bars → 25 4h bars exactly
    assert len(df_4h) == 25


def test_pair_zscore_signs():
    a, b = _toy_pair_ohlcv(n=200)
    z = pair_zscore(a["close"], b["close"], lookback=60).to_numpy()
    # After warmup, the z-score oscillates around 0 by construction.
    warm = z[60:]
    finite = warm[np.isfinite(warm)]
    assert finite.size > 0
    assert abs(np.mean(finite)) < 1.0  # mean-reverting by construction


def test_rolling_vpvr():
    a, _ = _toy_pair_ohlcv()
    out = _rolling_vpvr(a["close"].to_numpy(), a["volume"].to_numpy(), window=60, n_bins=24)
    poc = out["poc"]
    warm = poc[60:]
    assert np.all(np.isfinite(warm))


def test_run_pair_backtest_toy():
    a, b = _toy_pair_ohlcv(n=2000)
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["entry"]["require_vpvr_confluence"] = False  # remove convergence constraint for toy data
    res = run_pair_backtest(a, b, cfg, pair_label="TOY/TOY2")
    assert "trades" in res
    assert isinstance(res["equity"], np.ndarray)
    assert len(res["equity"]) == len(a)
    # On random data we don't enforce profitability — just confirm pipeline runs.
    assert res["n_trades"] >= 0


def test_load_all_end_to_end_smoke():
    """Real data path smoke test (full BTCUSDT/ETHUSDT/SOLUSDT set)."""
    btc_1h = ROOT / "data" / "fapi_BTCUSDT__1h.parquet"
    eth_1h = ROOT / "data" / "fapi_ETHUSDT__1h.parquet"
    sol_4h = ROOT / "data" / "fapi_SOLUSDT__4h.parquet"
    if not (btc_1h.is_file() and eth_1h.is_file() and sol_4h.is_file()):
        pytest.skip("required data parquets missing in unit-test env")
    data = load_all(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    assert set(data.keys()) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    for sym, df in data.items():
        assert isinstance(df.index, pd.DatetimeIndex)
        assert np.isfinite(df["close"].to_numpy()).all()
        if sym == "SOLUSDT":
            # Native 4h — check the index is on 4h boundaries
            assert (df.index.hour % 4 == 0).all()


def test_run_backtest_full_portfolio():
    btc_1h = ROOT / "data" / "fapi_BTCUSDT__1h.parquet"
    sol_4h = ROOT / "data" / "fapi_SOLUSDT__4h.parquet"
    if not (btc_1h.is_file() and sol_4h.is_file()):
        pytest.skip("required data parquets missing in unit-test env")
    data = load_all(["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    cfg = json.loads((ROOT / "config.json").read_text())
    out = run_backtest(data, cfg)
    assert "per_pair" in out
    assert len(out["per_pair"]) == 2
    for pr in out["per_pair"]:
        assert "trades" in pr
        assert isinstance(pr["equity"], np.ndarray)
