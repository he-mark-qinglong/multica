"""Edge-case and correctness tests for _shared/strategy_kit/indicators.py (A6)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd
import pytest

from _shared.strategy_kit import indicators as ta


def _series(n: int = 200, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), index=idx)


def _ohlc(n: int = 200, seed: int = 1):
    c = _series(n, seed)
    rng = np.random.default_rng(seed + 1)
    h = c * (1 + np.abs(rng.normal(0, 0.003, n)))
    l = c * (1 - np.abs(rng.normal(0, 0.003, n)))
    v = pd.Series(rng.uniform(100, 1000, n), index=c.index)
    return h, l, c, v


CONST = pd.Series([5.0] * 50,
                  index=pd.date_range("2026-01-01", periods=50, freq="1h", tz="UTC"))
EMPTY = pd.Series(dtype=float)
ONE = pd.Series([42.0], index=pd.DatetimeIndex(["2026-01-01"], tz="UTC"))


# ---------------------------------------------------------------------------
# Correctness spot-checks
# ---------------------------------------------------------------------------
def test_sma_matches_manual():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = ta.sma(s, 3)
    assert np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_ema_first_value_is_seed():
    s = _series(50)
    out = ta.ema(s, 10)
    assert out.iloc[0] == pytest.approx(s.iloc[0])


def test_wma_weights_recent_more():
    s = pd.Series([1.0, 2.0, 3.0])
    out = ta.wma(s, 3)
    # weights 1,2,3 -> (1*1 + 2*2 + 3*3)/6 = 14/6
    assert out.iloc[2] == pytest.approx(14.0 / 6.0)


def test_macd_hist_equals_line_minus_signal():
    s = _series()
    df = ta.macd(s)
    assert np.allclose(df["hist"], df["macd"] - df["signal"])


def test_rsi_pure_uptrend_is_100():
    s = pd.Series(np.arange(1.0, 31.0))
    out = ta.rsi(s, 14)
    assert out.iloc[-1] == pytest.approx(100.0)


def test_rsi_pure_downtrend_is_0():
    s = pd.Series(np.arange(30.0, 0.0, -1.0))
    out = ta.rsi(s, 14)
    assert out.iloc[-1] == pytest.approx(0.0)


def test_rsi_bounded():
    out = ta.rsi(_series(), 14).dropna()
    assert ((out >= 0.0) & (out <= 100.0)).all()


def test_atr_constant_bars_zero():
    out = ta.atr(CONST, CONST, CONST, 14)
    assert (out.dropna() == 0.0).all()


def test_true_range_first_bar_is_high_minus_low():
    h, l, c, _ = _ohlc(30)
    tr = ta.true_range(h, l, c)
    assert tr.iloc[0] == pytest.approx(h.iloc[0] - l.iloc[0])


def test_bollinger_mid_is_sma_and_bands_symmetric():
    s = _series()
    df = ta.bollinger_bands(s, 20, 2.0)
    mid = ta.sma(s, 20)
    assert np.allclose(df["mid"].dropna(), mid.dropna())
    mask = df["upper"].notna()
    assert np.allclose((df.loc[mask, "upper"] - df.loc[mask, "mid"]),
                       (df.loc[mask, "mid"] - df.loc[mask, "lower"]))


def test_bollinger_pct_b_inside_when_price_inside():
    s = _series()
    df = ta.bollinger_bands(s, 20, 3.0).dropna()
    # with 3-sigma bands price is essentially always inside
    assert ((df["pct_b"] > -0.1) & (df["pct_b"] < 1.1)).all()


def test_keltner_upper_above_lower():
    h, l, c, _ = _ohlc()
    df = ta.keltner_channels(h, l, c).dropna()
    assert (df["upper"] > df["lower"]).all()


def test_obv_flat_on_constant_close():
    v = pd.Series([100.0] * 50, index=CONST.index)
    assert (ta.obv(CONST, v) == 0.0).all()


def test_obv_accumulates_signed_volume():
    c = pd.Series([1.0, 2.0, 3.0, 2.0])
    v = pd.Series([10.0, 10.0, 10.0, 10.0])
    out = ta.obv(c, v)
    assert list(out) == [0.0, 10.0, 20.0, 10.0]


def test_ad_flat_when_high_equals_low():
    v = pd.Series([100.0] * 50, index=CONST.index)
    out = ta.accumulation_distribution(CONST, CONST, CONST, v)
    assert (out == 0.0).all()


def test_stochastic_bounds():
    h, l, c, _ = _ohlc()
    df = ta.stochastic(h, l, c).dropna()
    assert ((df["k"] >= 0.0) & (df["k"] <= 100.0)).all()


def test_cci_constant_is_nan():
    assert ta.cci(CONST, CONST, CONST).isna().all()


def test_willr_range():
    h, l, c, _ = _ohlc()
    out = ta.willr(h, l, c).dropna()
    assert ((out >= -100.0) & (out <= 0.0)).all()


def test_roc_and_mom_consistent():
    s = _series()
    r = ta.roc(s, 10)
    m = ta.mom(s, 10)
    assert np.allclose(r.dropna() / 100.0 * s.shift(10).dropna(), m.dropna())


def test_trix_constant_is_zero_or_nan():
    out = ta.trix(CONST, 15)
    assert ((out.dropna()).abs() < 1e-12).all()


def test_donchian_upper_geq_lower():
    h, l, _, _ = _ohlc()
    df = ta.donchian_channels(h, l, 20).dropna()
    assert (df["upper"] >= df["lower"]).all()
    assert np.allclose(df["mid"], (df["upper"] + df["lower"]) / 2)


def test_parabolic_sar_no_nan_and_tracks_price():
    h, l, _, _ = _ohlc(300)
    out = ta.parabolic_sar(h, l)
    assert out.notna().all()
    # SAR stays within the observed price envelope
    assert (out >= l.min() * 0.5).all() and (out <= h.max() * 2).all()


def test_parabolic_sar_reverses():
    # strong up then strong down -> SAR flips from below to above price
    idx = pd.date_range("2026-01-01", periods=40, freq="1h", tz="UTC")
    prices = np.concatenate([np.linspace(100, 130, 20), np.linspace(130, 95, 20)])
    h = pd.Series(prices + 0.5, index=idx)
    l = pd.Series(prices - 0.5, index=idx)
    sar = ta.parabolic_sar(h, l)
    assert sar.iloc[10] < prices[10]      # below in uptrend
    assert sar.iloc[-1] > prices[-1]      # above in downtrend


def test_vwap_session_first_bar_equals_typical_price():
    h, l, c, v = _ohlc(50)
    out = ta.vwap_session(h, l, c, v)
    assert out.iloc[0] == pytest.approx((h.iloc[0] + l.iloc[0] + c.iloc[0]) / 3.0)


def test_vwap_session_resets_at_day_boundary():
    idx = pd.date_range("2026-01-01 22:00", periods=6, freq="1h", tz="UTC")
    c = pd.Series([10.0, 10.0, 50.0, 50.0, 50.0, 50.0], index=idx)
    v = pd.Series([1.0] * 6, index=idx)
    out = ta.vwap_session(c, c, c, v)
    assert out.iloc[1] == pytest.approx(10.0)   # day 1 VWAP
    assert out.iloc[2] == pytest.approx(50.0)   # reset at day 2


def test_vwap_session_weighted():
    idx = pd.date_range("2026-01-01", periods=2, freq="1h", tz="UTC")
    c = pd.Series([10.0, 20.0], index=idx)
    v = pd.Series([3.0, 1.0], index=idx)
    out = ta.vwap_session(c, c, c, v)
    assert out.iloc[1] == pytest.approx((10 * 3 + 20 * 1) / 4)


def test_vwap_requires_datetime_index():
    s = pd.Series([1.0, 2.0])
    with pytest.raises(ValueError):
        ta.vwap_session(s, s, s, s)


# ---------------------------------------------------------------------------
# Boundary inputs: empty / single-point / constant
# ---------------------------------------------------------------------------
SERIES_FNS = [
    ("sma", lambda: ta.sma(EMPTY, 5)),
    ("ema", lambda: ta.ema(EMPTY, 5)),
    ("wma", lambda: ta.wma(EMPTY, 5)),
    ("rsi", lambda: ta.rsi(EMPTY, 14)),
    ("atr", lambda: ta.atr(EMPTY, EMPTY, EMPTY, 14)),
    ("obv", lambda: ta.obv(EMPTY, EMPTY)),
    ("ad", lambda: ta.accumulation_distribution(EMPTY, EMPTY, EMPTY, EMPTY)),
    ("cci", lambda: ta.cci(EMPTY, EMPTY, EMPTY)),
    ("willr", lambda: ta.willr(EMPTY, EMPTY, EMPTY)),
    ("roc", lambda: ta.roc(EMPTY, 5)),
    ("mom", lambda: ta.mom(EMPTY, 5)),
    ("trix", lambda: ta.trix(EMPTY, 15)),
    ("psar", lambda: ta.parabolic_sar(EMPTY, EMPTY)),
    ("vwap", lambda: ta.vwap_session(EMPTY, EMPTY, EMPTY, EMPTY)),
    ("true_range", lambda: ta.true_range(EMPTY, EMPTY, EMPTY)),
]

FRAME_FNS = [
    ("macd", lambda: ta.macd(EMPTY)),
    ("bollinger", lambda: ta.bollinger_bands(EMPTY)),
    ("keltner", lambda: ta.keltner_channels(EMPTY, EMPTY, EMPTY)),
    ("stochastic", lambda: ta.stochastic(EMPTY, EMPTY, EMPTY)),
    ("donchian", lambda: ta.donchian_channels(EMPTY, EMPTY)),
]


@pytest.mark.parametrize("name,fn", SERIES_FNS, ids=[n for n, _ in SERIES_FNS])
def test_empty_series_input(name, fn):
    out = fn()
    assert isinstance(out, pd.Series)
    assert len(out) == 0


@pytest.mark.parametrize("name,fn", FRAME_FNS, ids=[n for n, _ in FRAME_FNS])
def test_empty_frame_input(name, fn):
    out = fn()
    assert isinstance(out, pd.DataFrame)
    assert len(out) == 0


def test_single_point_inputs():
    for fn in (lambda: ta.sma(ONE, 5), lambda: ta.ema(ONE, 5),
               lambda: ta.wma(ONE, 5), lambda: ta.rsi(ONE, 14),
               lambda: ta.atr(ONE, ONE, ONE, 14), lambda: ta.obv(ONE, ONE),
               lambda: ta.accumulation_distribution(ONE, ONE, ONE, ONE),
               lambda: ta.cci(ONE, ONE, ONE), lambda: ta.willr(ONE, ONE, ONE),
               lambda: ta.roc(ONE, 5), lambda: ta.mom(ONE, 5),
               lambda: ta.trix(ONE, 15), lambda: ta.parabolic_sar(ONE, ONE),
               lambda: ta.macd(ONE), lambda: ta.bollinger_bands(ONE),
               lambda: ta.keltner_channels(ONE, ONE, ONE),
               lambda: ta.stochastic(ONE, ONE, ONE),
               lambda: ta.donchian_channels(ONE, ONE),
               lambda: ta.vwap_session(ONE, ONE, ONE, ONE)):
        out = fn()
        assert len(out) == 1  # never crashes, never invents rows


def test_constant_column_no_crash():
    v = pd.Series([7.0] * 50, index=CONST.index)
    outs = [
        ta.sma(CONST, 5), ta.ema(CONST, 5), ta.wma(CONST, 5), ta.rsi(CONST, 14),
        ta.atr(CONST, CONST, CONST, 14), ta.obv(CONST, v),
        ta.accumulation_distribution(CONST, CONST, CONST, v),
        ta.stochastic(CONST, CONST, CONST), ta.cci(CONST, CONST, CONST),
        ta.willr(CONST, CONST, CONST), ta.roc(CONST, 5), ta.mom(CONST, 5),
        ta.trix(CONST, 15), ta.donchian_channels(CONST, CONST),
        ta.parabolic_sar(CONST, CONST), ta.vwap_session(CONST, CONST, CONST, v),
        ta.macd(CONST), ta.bollinger_bands(CONST),
        ta.keltner_channels(CONST, CONST, CONST),
    ]
    assert all(len(o) == 50 for o in outs)
    # constant input -> zero momentum/vol signals
    assert (ta.mom(CONST, 5).dropna() == 0.0).all()
    assert (ta.roc(CONST, 5).dropna() == 0.0).all()


def test_ema_on_constant_equals_constant():
    assert np.allclose(ta.ema(CONST, 10), 5.0)


def test_index_preserved():
    s = _series(100)
    assert ta.sma(s, 5).index.equals(s.index)
    assert ta.bollinger_bands(s).index.equals(s.index)
    h, l, c, v = _ohlc(100)
    assert ta.vwap_session(h, l, c, v).index.equals(c.index)
