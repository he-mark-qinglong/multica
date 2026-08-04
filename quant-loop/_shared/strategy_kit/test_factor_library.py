"""Tests for _shared/strategy_kit/factor_library.py (A5)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.strategy_kit import factor_library as fl
from _shared.strategy_kit.registry import get_indicator, get_spec

EXPECTED = {
    "momentum_12_1", "reversal_5d", "vol_realized", "vol_of_vol",
    "volume_zscore", "funding_level", "funding_change", "basis_perp_spot",
    "oi_change_proxy", "amihud_illiq", "kyle_lambda", "vpin_proxy",
}


def _ohlcv(n: int = 400, seed: int = 11, with_funding: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    df = pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.001, n)),
            "high": close * (1 + np.abs(rng.normal(0, 0.002, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.002, n))),
            "close": close,
            "volume": rng.uniform(1e3, 1e5, n),
        },
        index=idx,
    )
    if with_funding:
        df["funding"] = rng.normal(0.0001, 0.0003, n)
        df["basis"] = rng.normal(0.0002, 0.0005, n)
    return df


# ---------------------------------------------------------------------------
# Library completeness & metadata
# ---------------------------------------------------------------------------
def test_all_twelve_factors_present():
    # Subset, not equality: extension packs (e.g.
    # _shared/factor_analysis/orderbook_factors.py) register additional
    # factors into the same library namespace when imported in the same
    # pytest process — the core library must contain at least these 12.
    assert EXPECTED.issubset(set(fl.list_factors()))


def test_specs_are_frozen_dataclasses_with_metadata():
    for name, spec in fl.list_factors().items():
        assert spec.name == name
        assert spec.direction in (+1, -1)
        assert spec.reference  # every factor cites a paper
        assert spec.required_columns
        assert spec.description
        with pytest.raises(Exception):  # frozen
            object.__getattribute__(spec, "__dataclass_fields__")
            import dataclasses
            dataclasses.replace  # noqa - spec frozen check below
            setattr(spec, "name", "x")


def test_every_factor_registered_in_shared_registry():
    for name in EXPECTED:
        spec = get_spec(name)
        assert spec.source == "_shared/strategy_kit/factor_library.py"


def test_registry_param_binding_works():
    data = _ohlcv()
    f = get_indicator("reversal_5d", window=10)
    out = f(data)
    close = data["close"]
    assert np.allclose(out.dropna(), (close / close.shift(10) - 1.0).dropna())


# ---------------------------------------------------------------------------
# Per-factor correctness
# ---------------------------------------------------------------------------
def test_momentum_12_1_skips_recent_month():
    data = _ohlcv()
    out = fl.compute_factor("momentum_12_1", data, lookback=100, skip=10)
    close = data["close"]
    expected = close.shift(10) / close.shift(100) - 1.0
    assert np.allclose(out.dropna(), expected.dropna())


def test_reversal_direction_is_minus_one():
    assert fl.get_factor_spec("reversal_5d").direction == -1


def test_vol_realized_nonnegative_and_annualised():
    data = _ohlcv()
    out = fl.compute_factor("vol_realized", data, window=20).dropna()
    assert (out >= 0).all()
    log_ret = np.log(data["close"]).diff()
    manual = log_ret.rolling(20).std(ddof=1) * np.sqrt(365)
    assert np.allclose(out, manual.dropna())


def test_vol_of_vol_constant_price_is_zero_or_nan():
    n = 100
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    data = pd.DataFrame({"close": np.full(n, 100.0),
                         "volume": np.full(n, 10.0)}, index=idx)
    out = fl.compute_factor("vol_of_vol", data, vol_window=10, vov_window=10)
    assert (out.dropna() < 1e-12).all()


def test_volume_zscore_matches_manual():
    data = _ohlcv()
    out = fl.compute_factor("volume_zscore", data, window=30)
    v = data["volume"]
    expected = (v - v.rolling(30).mean()) / v.rolling(30).std(ddof=1)
    assert np.allclose(out.dropna(), expected.dropna())


def test_funding_level_uses_funding_column():
    data = _ohlcv()
    out = fl.compute_factor("funding_level", data, window=8)
    expected = data["funding"].rolling(8, min_periods=1).mean()
    assert np.allclose(out, expected)


def test_funding_factors_degrade_to_zero_without_column():
    data = _ohlcv(with_funding=False)
    for name in ("funding_level", "funding_change", "basis_perp_spot"):
        out = fl.compute_factor(name, data, window=5)
        assert (out.dropna() == 0.0).all()


def test_basis_perp_spot_mean():
    data = _ohlcv()
    out = fl.compute_factor("basis_perp_spot", data, window=12)
    assert np.allclose(out, data["basis"].rolling(12, min_periods=1).mean())


def test_oi_change_proxy_signed_volume():
    data = _ohlcv()
    out = fl.compute_factor("oi_change_proxy", data, window=20)
    assert out.notna().sum() > 0
    assert out.dropna().abs().max() < 10  # it's a z-score


def test_amihud_matches_definition():
    data = _ohlcv()
    out = fl.compute_factor("amihud_illiq", data, window=20)
    close = data["close"]
    raw = (close.pct_change().abs()
           / (close * data["volume"]).replace(0.0, np.nan)).fillna(0.0)
    expected = raw.rolling(20, min_periods=1).mean() * 1e9
    assert np.allclose(out, expected)


def test_kyle_lambda_positive_in_trending_market():
    # persistent trend: signed volume correlates with price change
    n = 200
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    close = pd.Series(np.cumsum(np.full(n, 0.5)) + 100.0, index=idx)
    data = pd.DataFrame({"close": close, "volume": np.full(n, 100.0)},
                        index=idx)
    out = fl.compute_factor("kyle_lambda", data, window=20).dropna()
    assert (out > 0).all()


def test_vpin_proxy_bounded_in_unit_interval():
    data = _ohlcv()
    out = fl.compute_factor("vpin_proxy", data, window=20, vol_window=30).dropna()
    assert ((out >= 0.0) & (out <= 1.0)).all()


# ---------------------------------------------------------------------------
# Error handling / causality
# ---------------------------------------------------------------------------
def test_compute_factor_missing_columns_raises():
    data = _ohlcv().drop(columns=["volume"])
    with pytest.raises(ValueError, match="missing columns"):
        fl.compute_factor("amihud_illiq", data)


def test_unknown_factor_raises():
    with pytest.raises(KeyError):
        fl.compute_factor("nope", _ohlcv())


def test_causality_no_lookahead():
    """Truncating the future must not change past factor values."""
    data = _ohlcv(n=300)
    full = fl.compute_factor("reversal_5d", data)
    cut = fl.compute_factor("reversal_5d", data.iloc[:200])
    assert np.allclose(full.iloc[:200].dropna(), cut.dropna())


def test_empty_frame_returns_empty_or_nan():
    data = _ohlcv(n=0)
    out = fl.compute_factor("reversal_5d", data)
    assert len(out) == 0
