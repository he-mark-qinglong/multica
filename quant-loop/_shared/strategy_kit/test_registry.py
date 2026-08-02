import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.strategy_kit import registry
from _shared.strategy_kit.registry import (
    IndicatorNotFoundError,
    ParamSpec,
    ParamValidationError,
    get_indicator,
    get_spec,
    list_indicators,
    register_indicator,
)


def _ohlcv(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    spread = close * 0.005
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.001, n)),
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": rng.uniform(1e5, 1e6, n),
        }
    )


# ---------------------------------------------------------------------------
# Registration & retrieval
# ---------------------------------------------------------------------------

def test_register_and_get():
    @register_indicator("test_momentum", params={
        "lookback": ParamSpec("int", default=10, min=1),
    })
    def momentum(data: pd.DataFrame, lookback: int = 10) -> pd.Series:
        return data["close"].pct_change(lookback)

    fn = get_indicator("test_momentum", lookback=5)
    df = _ohlcv(60)
    out = fn(df)
    expected = df["close"].pct_change(5)
    pd.testing.assert_series_equal(out, expected, check_names=False)


def test_duplicate_registration_rejected():
    with pytest.raises(ValueError, match="already registered"):
        register_indicator("vol_target_weight")(lambda df: df["close"])


def test_unknown_indicator_raises():
    with pytest.raises(IndicatorNotFoundError):
        get_indicator("does_not_exist")


def test_list_and_spec():
    names = list_indicators()
    assert "vpvr_poc_distance" in names
    assert "vol_target_weight" in names
    assert "amihud_illiquidity" in names
    spec = get_spec("vol_target_weight")
    assert spec.source == "_shared/sizing/vol_target.py"
    assert "target_vol" in spec.params


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_defaults_filled():
    fn = get_indicator("amihud_illiquidity")  # lookback defaults to 20
    out = fn(_ohlcv(80))
    assert len(out) == 80
    assert out.notna().all()


def test_unknown_param_rejected():
    with pytest.raises(ParamValidationError, match="unknown params"):
        get_indicator("amihud_illiquidity", lookbak=5)


def test_wrong_type_rejected():
    with pytest.raises(ParamValidationError, match="must be int"):
        get_indicator("amihud_illiquidity", lookback="five")
    # bool must not pass for int
    with pytest.raises(ParamValidationError):
        get_indicator("amihud_illiquidity", lookback=True)


def test_bounds_enforced():
    with pytest.raises(ParamValidationError, match="min"):
        get_indicator("amihud_illiquidity", lookback=0)
    with pytest.raises(ParamValidationError, match="max"):
        get_indicator("vol_target_weight", target_vol=99.0)


def test_required_param():
    @register_indicator("test_required", params={
        "window": ParamSpec("int", required=True, min=1),
    })
    def roll(data: pd.DataFrame, window: int) -> pd.Series:
        return data["close"].rolling(window).mean()

    with pytest.raises(ParamValidationError, match="missing required"):
        get_indicator("test_required")
    fn = get_indicator("test_required", window=3)
    assert fn(_ohlcv(30)).notna().sum() == 28


# ---------------------------------------------------------------------------
# Built-in indicators actually compute
# ---------------------------------------------------------------------------

def test_builtin_vol_target_weight_matches_source():
    from _shared.sizing.vol_target import vol_target_weights

    df = _ohlcv(120)
    fn = get_indicator("vol_target_weight", target_vol=0.15, lookback=20)
    out = fn(df)
    expected = vol_target_weights(df["close"].pct_change().fillna(0.0),
                                  target_vol=0.15, lookback=20)
    pd.testing.assert_series_equal(out, expected, check_names=False)


def test_builtin_vpvr_poc_distance_runs_and_is_bounded():
    df = _ohlcv(120)
    fn = get_indicator("vpvr_poc_distance", lookback=60, num_bins=20)
    out = fn(df)
    # warm-up rows are NaN; afterwards finite and of plausible magnitude
    assert out.iloc[:60].isna().all()
    tail = out.iloc[60:]
    assert np.isfinite(tail).all()
    assert tail.abs().max() < 50  # ATR units, sanity bound


def test_builtin_amihud_nonnegative():
    out = get_indicator("amihud_illiquidity", lookback=10)(_ohlcv(100))
    assert (out >= 0).all()
