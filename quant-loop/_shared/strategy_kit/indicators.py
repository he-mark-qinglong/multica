"""Technical indicator library (metric A6) — vectorized, pure functions.

Every indicator follows the project convention: ``pd.Series`` /
``pd.DataFrame`` in, ``pd.Series`` (or ``pd.DataFrame`` for multi-output
indicators) out, no I/O, no globals, causal (value at ``t`` uses only data
``<= t``). Warm-up windows produce ``NaN`` rather than fabricated values,
so downstream research code can drop/inspect them explicitly.

Edge-case contract (enforced by ``test_indicators.py``):
  - empty input -> empty output of the right dtype/index
  - single-row input -> all-NaN (or the degenerate defined value) row
  - constant input -> no crash; RSI returns NaN (0/0), ATR returns 0.0,
    Bollinger %b returns NaN (zero bandwidth)

References (classical definitions):
- Wilder, J.W. (1978) "New Concepts in Technical Trading Systems"
  (RSI, ATR, Parabolic SAR, ADX-family smoothing).
- Appel, G. (1979) MACD; Stochastic: George Lane (1950s).
- Bollinger, J. (1992); Keltner, C. (1960) / ATR bands per Kaufman.
- Chaikin, M. (Accumulation/Distribution); Granville, J. (1963) OBV.
- Lambert, D. (1980) CCI; Williams, L. (%R); Hutson, J. (1991) TRIX.
- Donchian, R. (channel); VWAP: session-anchored institutional benchmark.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "sma", "ema", "wma", "macd", "rsi", "atr", "bollinger_bands",
    "keltner_channels", "obv", "accumulation_distribution", "stochastic",
    "cci", "willr", "roc", "mom", "trix", "donchian_channels",
    "parabolic_sar", "vwap_session", "true_range",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _empty_like(s: pd.Series) -> pd.Series:
    """Empty float Series preserving index (empty-input contract)."""
    return pd.Series(dtype=float, index=s.index, name=s.name)


def _wilder_ema(s: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing: EMA with alpha = 1/period (Wilder 1978)."""
    return s.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------
def sma(s: pd.Series, window: int) -> pd.Series:
    """Simple moving average. NaN until ``window`` samples available."""
    if len(s) == 0:
        return _empty_like(s)
    return s.astype(float).rolling(window, min_periods=window).mean()


def ema(s: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (alpha = 2/(span+1)), seeded at first value."""
    if len(s) == 0:
        return _empty_like(s)
    return s.astype(float).ewm(span=span, adjust=False).mean()


def wma(s: pd.Series, window: int) -> pd.Series:
    """Linearly weighted moving average (recent bars weigh more)."""
    if len(s) == 0:
        return _empty_like(s)
    weights = np.arange(1, window + 1, dtype=float)
    denom = weights.sum()

    def _w(x: np.ndarray) -> float:
        return float(np.dot(x, weights) / denom)

    return s.astype(float).rolling(window, min_periods=window).apply(_w, raw=True)


# ---------------------------------------------------------------------------
# MACD (Appel 1979)
# ---------------------------------------------------------------------------
def macd(s: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    """MACD line / signal line / histogram."""
    if len(s) == 0:
        return pd.DataFrame(
            {"macd": pd.Series(dtype=float), "signal": pd.Series(dtype=float),
             "hist": pd.Series(dtype=float)}, index=s.index)
    line = ema(s, fast) - ema(s, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig},
                        index=s.index)


# ---------------------------------------------------------------------------
# RSI (Wilder 1978)
# ---------------------------------------------------------------------------
def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index in [0, 100].

    Constant series (zero gains AND zero losses) -> NaN (undefined 0/0);
    pure uptrend -> 100, pure downtrend -> 0.
    """
    if len(close) == 0:
        return _empty_like(close)
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = _wilder_ema(gain, period)
    avg_loss = _wilder_ema(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # avg_loss == 0 with avg_gain > 0 -> RSI 100 (pure up moves).
    out = out.where(~((avg_loss == 0.0) & (avg_gain > 0.0)), 100.0)
    return out


# ---------------------------------------------------------------------------
# True range / ATR (Wilder 1978)
# ---------------------------------------------------------------------------
def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True range; first bar falls back to high - low."""
    if len(close) == 0:
        return _empty_like(close)
    prev_close = close.astype(float).shift(1)
    tr = pd.concat(
        [(high - low).astype(float),
         (high - prev_close).abs(),
         (low - prev_close).abs()], axis=1,
    ).max(axis=1)
    tr.iloc[0] = float(high.iloc[0] - low.iloc[0])
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    """Wilder ATR. Constant bars (h=l=c) -> 0.0."""
    return _wilder_ema(true_range(high, low, close), period)


# ---------------------------------------------------------------------------
# Bollinger (1992) / Keltner (1960)
# ---------------------------------------------------------------------------
def bollinger_bands(close: pd.Series, window: int = 20,
                    num_std: float = 2.0) -> pd.DataFrame:
    """Middle/upper/lower band + %b + bandwidth. Constant window -> width 0,
    %b NaN."""
    cols = ["mid", "upper", "lower", "pct_b", "bandwidth"]
    if len(close) == 0:
        return pd.DataFrame({c: pd.Series(dtype=float) for c in cols},
                            index=close.index)
    c = close.astype(float)
    mid = c.rolling(window, min_periods=window).mean()
    sd = c.rolling(window, min_periods=window).std(ddof=0)
    upper, lower = mid + num_std * sd, mid - num_std * sd
    width = (upper - lower)
    pct_b = ((c - lower) / width.replace(0.0, np.nan))
    bandwidth = (width / mid.replace(0.0, np.nan))
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower,
                         "pct_b": pct_b, "bandwidth": bandwidth},
                        index=close.index)


def keltner_channels(high: pd.Series, low: pd.Series, close: pd.Series,
                     ema_window: int = 20, atr_window: int = 10,
                     mult: float = 2.0) -> pd.DataFrame:
    """EMA(center) +/- mult * ATR bands."""
    cols = ["mid", "upper", "lower"]
    if len(close) == 0:
        return pd.DataFrame({c: pd.Series(dtype=float) for c in cols},
                            index=close.index)
    mid = ema(close, ema_window)
    rng = atr(high, low, close, atr_window)
    return pd.DataFrame({"mid": mid, "upper": mid + mult * rng,
                         "lower": mid - mult * rng}, index=close.index)


# ---------------------------------------------------------------------------
# Volume indicators
# ---------------------------------------------------------------------------
def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume (Granville 1963). Unchanged close -> 0 contribution."""
    if len(close) == 0:
        return _empty_like(close)
    direction = np.sign(close.astype(float).diff()).fillna(0.0)
    return (direction * volume.astype(float)).cumsum()


def accumulation_distribution(high: pd.Series, low: pd.Series,
                              close: pd.Series, volume: pd.Series) -> pd.Series:
    """Chaikin Accumulation/Distribution line.

    Money-flow multiplier ((c-l)-(h-c))/(h-l); h == l -> multiplier 0.
    """
    if len(close) == 0:
        return _empty_like(close)
    h, l, c = high.astype(float), low.astype(float), close.astype(float)
    hl = (h - l).replace(0.0, np.nan)
    mfm = (((c - l) - (h - c)) / hl).fillna(0.0)
    return (mfm * volume.astype(float)).cumsum()


# ---------------------------------------------------------------------------
# Stochastic (Lane) / CCI (Lambert 1980) / Williams %R
# ---------------------------------------------------------------------------
def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               k_window: int = 14, d_window: int = 3,
               smooth_k: int = 3) -> pd.DataFrame:
    """%K (smoothed) and %D. h == l over the window -> %K NaN."""
    if len(close) == 0:
        return pd.DataFrame({"k": pd.Series(dtype=float),
                             "d": pd.Series(dtype=float)}, index=close.index)
    hh = high.astype(float).rolling(k_window, min_periods=k_window).max()
    ll = low.astype(float).rolling(k_window, min_periods=k_window).min()
    raw_k = 100.0 * (close.astype(float) - ll) / (hh - ll).replace(0.0, np.nan)
    k = raw_k.rolling(smooth_k, min_periods=smooth_k).mean()
    d = k.rolling(d_window, min_periods=d_window).mean()
    return pd.DataFrame({"k": k, "d": d}, index=close.index)


def cci(high: pd.Series, low: pd.Series, close: pd.Series,
        window: int = 20) -> pd.Series:
    """Commodity Channel Index (Lambert 1980). Constant window -> NaN."""
    if len(close) == 0:
        return _empty_like(close)
    tp = (high.astype(float) + low.astype(float) + close.astype(float)) / 3.0
    ma = tp.rolling(window, min_periods=window).mean()
    md = tp.rolling(window, min_periods=window).apply(
        lambda x: float(np.mean(np.abs(x - x.mean()))), raw=True)
    return (tp - ma) / (0.015 * md.replace(0.0, np.nan))


def willr(high: pd.Series, low: pd.Series, close: pd.Series,
          window: int = 14) -> pd.Series:
    """Williams %R in [-100, 0]. h == l -> NaN."""
    if len(close) == 0:
        return _empty_like(close)
    hh = high.astype(float).rolling(window, min_periods=window).max()
    ll = low.astype(float).rolling(window, min_periods=window).min()
    return -100.0 * (hh - close.astype(float)) / (hh - ll).replace(0.0, np.nan)


# ---------------------------------------------------------------------------
# Rate of change / momentum / TRIX
# ---------------------------------------------------------------------------
def roc(close: pd.Series, window: int = 12) -> pd.Series:
    """Rate of change in percent: 100 * (c/c[n] - 1)."""
    if len(close) == 0:
        return _empty_like(close)
    c = close.astype(float)
    return 100.0 * (c / c.shift(window) - 1.0)


def mom(close: pd.Series, window: int = 10) -> pd.Series:
    """Momentum: c - c[n]."""
    if len(close) == 0:
        return _empty_like(close)
    return close.astype(float).diff(window)


def trix(close: pd.Series, window: int = 15) -> pd.Series:
    """TRIX (Hutson 1991): 1-bar ROC (in %) of a triple EMA."""
    if len(close) == 0:
        return _empty_like(close)
    e3 = ema(ema(ema(close, window), window), window)
    prev = e3.shift(1)
    return 100.0 * (e3 - prev) / prev.replace(0.0, np.nan)


# ---------------------------------------------------------------------------
# Donchian channels
# ---------------------------------------------------------------------------
def donchian_channels(high: pd.Series, low: pd.Series,
                      window: int = 20) -> pd.DataFrame:
    """Upper = rolling max(high), lower = rolling min(low), mid = mean of both."""
    cols = ["upper", "lower", "mid"]
    if len(high) == 0:
        return pd.DataFrame({c: pd.Series(dtype=float) for c in cols},
                            index=high.index)
    upper = high.astype(float).rolling(window, min_periods=window).max()
    lower = low.astype(float).rolling(window, min_periods=window).min()
    return pd.DataFrame({"upper": upper, "lower": lower,
                         "mid": (upper + lower) / 2.0}, index=high.index)


# ---------------------------------------------------------------------------
# Parabolic SAR (Wilder 1978) — loop approximation
# ---------------------------------------------------------------------------
def parabolic_sar(high: pd.Series, low: pd.Series,
                  af_start: float = 0.02, af_step: float = 0.02,
                  af_max: float = 0.2) -> pd.Series:
    """Parabolic SAR (Wilder 1978). Not vectorizable by nature (path-dependent
    acceleration factor) — implemented as a tight python loop over numpy
    arrays. len < 2 -> all-NaN output."""
    n = len(high)
    out = pd.Series(np.nan, index=high.index, dtype=float)
    if n < 2:
        return out
    h = high.to_numpy(dtype=float)
    l = low.to_numpy(dtype=float)
    sar = np.empty(n, dtype=float)
    uptrend = h[1] >= h[0]
    sar[0] = l[0] if uptrend else h[0]
    ep = h[0] if uptrend else l[0]  # extreme point
    af = af_start
    for i in range(1, n):
        prev_sar = sar[i - 1]
        cur = prev_sar + af * (ep - prev_sar)
        if uptrend:
            # SAR may not exceed the two prior lows.
            cur = min(cur, l[i - 1], l[i - 2] if i >= 2 else l[i - 1])
            if l[i] < cur:  # reversal to downtrend
                uptrend = False
                cur = ep
                ep = l[i]
                af = af_start
            else:
                if h[i] > ep:
                    ep = h[i]
                    af = min(af + af_step, af_max)
        else:
            cur = max(cur, h[i - 1], h[i - 2] if i >= 2 else h[i - 1])
            if h[i] > cur:  # reversal to uptrend
                uptrend = True
                cur = ep
                ep = h[i]
                af = af_start
            else:
                if l[i] < ep:
                    ep = l[i]
                    af = min(af + af_step, af_max)
        sar[i] = cur
    out[:] = sar
    return out


# ---------------------------------------------------------------------------
# Session-anchored VWAP
# ---------------------------------------------------------------------------
def vwap_session(high: pd.Series, low: pd.Series, close: pd.Series,
                 volume: pd.Series,
                 session: str = "1D") -> pd.Series:
    """Session-anchored VWAP of the typical price (h+l+c)/3.

    The anchor resets at each ``session`` boundary on the (DatetimeIndex)
    index — ``"1D"`` for daily, ``"1W"`` for weekly. Cumulative sums are
    computed once, then differenced per session: O(n), no groupby-apply.

    Zero-volume session -> NaN for that session's bars.
    """
    if len(close) == 0:
        return _empty_like(close)
    if not isinstance(close.index, pd.DatetimeIndex):
        raise ValueError("vwap_session requires a DatetimeIndex")
    tp = (high.astype(float) + low.astype(float) + close.astype(float)) / 3.0
    v = volume.astype(float)
    tpv = tp * v
    pv = tpv.cumsum()
    cv = v.cumsum()
    keys = close.index.floor(session)
    # Session sums = cumsum[i] - cumsum[s-1]; recover via the first row s of
    # each session: cumsum[s-1] = cumsum[s] - value[s].
    first_pv = pv.groupby(keys).transform("first")
    first_cv = cv.groupby(keys).transform("first")
    first_tpv = tpv.groupby(keys).transform("first")
    first_v = v.groupby(keys).transform("first")
    sess_pv = pv - (first_pv - first_tpv)
    sess_v = cv - (first_cv - first_v)
    return sess_pv / sess_v.replace(0.0, np.nan)
