"""BTC regime classifier — shared across strategies.

Three orthogonal regime dimensions, each returns a label. Strategies call
the relevant dimension(s) to gate entries.

References:
- Hamilton (1989) "A New Approach to the Economic Analysis of Nonstationary
  Time Series and the Business Cycle" — 2-state HMM
- Pagan-Sossounov (2003) for bull/bear dating
- Christoffersen et al. (2010) for vol regimes
"""
from dataclasses import dataclass
from enum import Enum
import numpy as np
import pandas as pd


class TrendRegime(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    RANGE = "range"


class VolRegime(str, Enum):
    CALM = "calm"        # ATR percentile < 33
    NORMAL = "normal"    # 33-66
    VOLATILE = "volatile"  # > 66


class FundingRegime(str, Enum):
    NEUTRAL = "neutral"  # |funding_ema| < 0.005%/8h
    LONG_FAVOR = "long_favor"  # shorts pay longs (longs get carry)
    SHORT_FAVOR = "short_favor"  # longs pay shorts
    EXTREME = "extreme"  # |funding_ema| > 0.05%/8h — pay attention


@dataclass
class RegimeSnapshot:
    timestamp: pd.Timestamp
    trend: TrendRegime
    vol: VolRegime
    funding: FundingRegime
    # raw values for transparency
    ema_fast: float
    ema_slow: float
    atr_percentile: float
    funding_ema_7d: float


def classify_trend(ema_fast: float, ema_slow: float, adx: float = 0.0) -> TrendRegime:
    """Trend from EMA cross, optionally gated by ADX."""
    if adx > 25:
        # Strong trend, follow EMA
        return TrendRegime.BULL if ema_fast > ema_slow else TrendRegime.BEAR
    # Weak ADX → range regardless of EMA
    if abs(ema_fast - ema_slow) / max(ema_slow, 1e-9) < 0.005:
        return TrendRegime.RANGE
    return TrendRegime.BULL if ema_fast > ema_slow else TrendRegime.BEAR


def classify_vol(atr_series: pd.Series, window: int = 100) -> VolRegime:
    """Vol regime from ATR percentile over rolling window."""
    if len(atr_series) < window:
        return VolRegime.NORMAL
    pct = float(atr_series.iloc[-window:].rank(pct=True).iloc[-1])
    if pct < 0.33:
        return VolRegime.CALM
    if pct > 0.66:
        return VolRegime.VOLATILE
    return VolRegime.NORMAL


def classify_funding(funding_series: pd.Series, window: int = 21) -> FundingRegime:
    """Funding regime from EMA of funding rate (per-8h values)."""
    if len(funding_series) < window:
        return FundingRegime.NEUTRAL
    ema = funding_series.ewm(span=window).mean().iloc[-1]
    abs_ema = abs(ema)
    if abs_ema > 0.0005:  # 0.05%/8h = extreme
        return FundingRegime.EXTREME
    if ema > 0.00005:  # 0.005%/8h
        return FundingRegime.LONG_FAVOR
    if ema < -0.00005:
        return FundingRegime.SHORT_FAVOR
    return FundingRegime.NEUTRAL


def regime_snapshot(
    ohlcv_4h: pd.DataFrame,
    funding_8h: pd.Series | None = None,
    ema_fast_period: int = 20,
    ema_slow_period: int = 50,
) -> RegimeSnapshot:
    """Compute regime at the latest bar of `ohlcv_4h`.

    Args:
        ohlcv_4h: DataFrame with columns [open, high, low, close, volume], indexed by timestamp
        funding_8h: optional Series of per-8h funding rates (decimal, e.g. 0.0001 = 1bp)
        ema_fast_period, ema_slow_period: EMA periods in 4h bars

    Returns:
        RegimeSnapshot at the latest bar.
    """
    close = ohlcv_4h["close"]
    ema_fast = close.ewm(span=ema_fast_period).mean().iloc[-1]
    ema_slow = close.ewm(span=ema_slow_period).mean().iloc[-1]

    # ATR (Wilder)
    high, low = ohlcv_4h["high"], ohlcv_4h["low"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()

    trend = classify_trend(ema_fast, ema_slow)
    vol = classify_vol(atr)

    funding_ema = 0.0
    if funding_8h is not None and len(funding_8h) > 0:
        funding_ema = float(funding_8h.ewm(span=21).mean().iloc[-1])
        funding_regime = classify_funding(funding_8h)
    else:
        funding_regime = FundingRegime.NEUTRAL

    return RegimeSnapshot(
        timestamp=ohlcv_4h.index[-1],
        trend=trend,
        vol=vol,
        funding=funding_regime,
        ema_fast=float(ema_fast),
        ema_slow=float(ema_slow),
        atr_percentile=float(atr.iloc[-100:].rank(pct=True).iloc[-1]) if len(atr) >= 100 else 0.5,
        funding_ema_7d=funding_ema,
    )


def regime_series(
    ohlcv_4h: pd.DataFrame,
    funding_8h: pd.Series | None = None,
    ema_fast_period: int = 20,
    ema_slow_period: int = 50,
) -> pd.DataFrame:
    """Compute regime at every bar of `ohlcv_4h` (vectorized where possible).

    Returns DataFrame with columns [timestamp, trend, vol, funding, ema_fast, ema_slow, atr_pct, funding_ema].
    Useful for backtesting regime-gated strategies.
    """
    close = ohlcv_4h["close"]
    ema_fast = close.ewm(span=ema_fast_period).mean()
    ema_slow = close.ewm(span=ema_slow_period).mean()
    high, low = ohlcv_4h["high"], ohlcv_4h["low"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    atr_pct = atr.rolling(100, min_periods=20).rank(pct=True)

    # Trend: vectorized
    adx_threshold = 0.005
    spread = ((ema_fast - ema_slow) / ema_slow).fillna(0)
    trend = pd.Series("range", index=close.index)
    trend.loc[spread > adx_threshold] = "bull"
    trend.loc[spread < -adx_threshold] = "bear"

    # Vol: vectorized
    vol = pd.Series("normal", index=close.index)
    vol.loc[atr_pct < 0.33] = "calm"
    vol.loc[atr_pct > 0.66] = "volatile"

    # Funding: resample 8h → 4h, forward fill
    if funding_8h is not None:
        funding_aligned = funding_8h.reindex(ohlcv_4h.index, method="ffill")
        funding_ema = funding_aligned.ewm(span=21).mean()
    else:
        funding_ema = pd.Series(0.0, index=close.index)

    funding_regime = pd.Series("neutral", index=close.index)
    funding_regime.loc[funding_ema > 0.00005] = "long_favor"
    funding_regime.loc[funding_ema < -0.00005] = "short_favor"
    funding_regime.loc[funding_ema.abs() > 0.0005] = "extreme"

    return pd.DataFrame({
        "timestamp": ohlcv_4h.index,
        "trend": trend.values,
        "vol": vol.values,
        "funding": funding_regime.values,
        "ema_fast": ema_fast.values,
        "ema_slow": ema_slow.values,
        "atr_pct": atr_pct.fillna(0.5).values,
        "funding_ema": funding_ema.values,
    }).set_index("timestamp")
