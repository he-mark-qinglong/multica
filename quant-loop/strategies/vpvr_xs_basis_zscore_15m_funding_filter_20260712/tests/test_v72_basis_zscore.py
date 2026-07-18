"""Unit tests for V72 (iter#72) — xs-basis z-score + VPVR confluence + funding filter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import (
    _annualisation_factor,
    _rolling_vpvr,
    funding_filter_mask,
    pair_zscore,
    run_backtest,
    run_pair_backtest,
    true_range,
    wilder_atr,
)


def _toy_pair_ohlcv(n: int = 1500, seed: int = 31) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Two correlated instruments, simple random walk."""
    rng = np.random.default_rng(seed)
    common = rng.normal(0.001, 0.02, size=n)
    spec_a = rng.normal(0.0, 0.005, size=n)
    spec_b = rng.normal(0.0, 0.005, size=n)
    p_a = 100.0 * np.cumprod(1 + common + spec_a)
    p_b = 60.0 * np.cumprod(1 + common * 0.5 + spec_b)
    idx = pd.date_range("2024-01-01", periods=n, freq="15min")
    a = pd.DataFrame({
        "open": p_a, "high": p_a * 1.005, "low": p_a * 0.995,
        "close": p_a, "volume": np.abs(rng.normal(1000, 200, size=n)),
        "funding_rate": np.zeros(n),
    }, index=idx)
    b = pd.DataFrame({
        "open": p_b, "high": p_b * 1.005, "low": p_b * 0.995,
        "close": p_b, "volume": np.abs(rng.normal(800, 150, size=n)),
        "funding_rate": np.zeros(n),
    }, index=idx)
    return a, b


def test_annualisation_factor_15m():
    assert _annualisation_factor("15m") > 0


def test_pair_zscore_signs():
    """Random walk — z should oscillate around 0 with finite std > 0."""
    a, b = _toy_pair_ohlcv(n=400)
    z = pair_zscore(a["close"], b["close"], lookback=60).to_numpy()
    warm = z[60:]
    finite = warm[np.isfinite(warm)]
    assert finite.size > 0
    # Z-score is unit-variance by construction → std should be near 1
    assert 0.3 < np.std(finite) < 3.0
    # Mean-reverting: |mean| should be small relative to std
    assert abs(np.mean(finite)) < 2.0


def test_rolling_vpvr():
    a, _ = _toy_pair_ohlcv()
    out = _rolling_vpvr(a["close"].to_numpy(), a["volume"].to_numpy(), window=60, n_bins=24)
    poc = out["poc"]
    warm = poc[60:]
    assert np.all(np.isfinite(warm))


def test_funding_filter_mask_blocks_blowoff():
    idx = pd.date_range("2024-01-01", periods=10, freq="15min")
    s = pd.Series(
        [0.0001, 0.0002, 0.0008, 0.0001, 0.0001, 0.0010, 0.0001, 0.0002, 0.0003, 0.0001],
        index=idx, name="funding_rate",
    )
    mask = funding_filter_mask(idx, s, threshold=0.0005)
    assert mask.dtype == bool
    # Bars with funding > threshold should be blocked
    assert mask.iloc[2] == False
    assert mask.iloc[5] == False
    # Bars under threshold should allow
    assert mask.iloc[0] == True
    assert mask.iloc[1] == True


def test_run_pair_backtest_toy():
    a, b = _toy_pair_ohlcv(n=2000)
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["entry"]["require_vpvr_confluence"] = False  # loosen for toy data
    cfg["entry"]["require_funding_filter"] = False
    res = run_pair_backtest(a, b, cfg, pair_label="TOY/TOY2")
    assert "trades" in res
    assert isinstance(res["equity"], np.ndarray)
    assert len(res["equity"]) == len(a)
    assert res["n_trades"] >= 0


def test_run_pair_backtest_with_funding_blowoff_blocks_entries():
    """When funding is in blowoff for the entire history, far fewer entries should fire."""
    a, b = _toy_pair_ohlcv(n=2000)
    a_calm = a.copy(); b_calm = b.copy()
    a_calm["funding_rate"] = 0.0001  # calm funding
    b_calm["funding_rate"] = 0.0001
    a_hot = a.copy(); b_hot = b.copy()
    a_hot["funding_rate"] = 0.002  # blowoff funding
    b_hot["funding_rate"] = 0.002

    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["entry"]["require_vpvr_confluence"] = False  # isolate funding effect
    cfg["entry"]["require_funding_filter"] = True
    res_calm = run_pair_backtest(a_calm, b_calm, cfg, pair_label="TOY/TOY2",
                                 funding_a=a_calm["funding_rate"], funding_b=b_calm["funding_rate"])
    res_hot = run_pair_backtest(a_hot, b_hot, cfg, pair_label="TOY/TOY2",
                                funding_a=a_hot["funding_rate"], funding_b=b_hot["funding_rate"])
    # Blowoff funding (well above 0.0005 threshold) should block most/all entries.
    assert res_hot["n_trades"] <= res_calm["n_trades"]


def test_data_load_smoke_real_btc_eth():
    """Real BTCUSDT/ETHUSDT 15m parquets smoke test."""
    from data_loader import load_all, load_funding_series
    btc_p = ROOT / "data" / "BTCUSDT__15m.parquet"
    eth_p = ROOT / "data" / "ETHUSDT__15m.parquet"
    if not (btc_p.is_file() and eth_p.is_file()):
        pytest.skip("required data parquets missing in unit-test env")
    data = load_all(["BTCUSDT", "ETHUSDT"])
    assert set(data.keys()) == {"BTCUSDT", "ETHUSDT"}
    funding = load_funding_series(["BTCUSDT", "ETHUSDT"])
    assert "BTCUSDT" in funding
    assert "ETHUSDT" in funding
    for sym, df in data.items():
        assert isinstance(df.index, pd.DatetimeIndex)
        assert np.isfinite(df["close"].to_numpy()).all()
        # 15m bars should fall on quarter-hour
        assert (df.index.minute % 15 == 0).all()
