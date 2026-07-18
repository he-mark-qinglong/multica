"""Tests for btc_gate.py. Plain asserts, prints N/N passed at end.

Run: python3 _shared/regime/test_btc_gate.py
"""
import os
import sys

import numpy as np
import pandas as pd

# Allow direct execution: repo root on sys.path so `_shared` is importable.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from _shared.regime.btc_gate import (
    FundingRegime,
    RegimeSnapshot,
    TrendRegime,
    VolRegime,
    classify_funding,
    classify_trend,
    classify_vol,
    regime_series,
    regime_snapshot,
)

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def _ohlcv_from_close(closes: list[float], freq: str = "4h") -> pd.DataFrame:
    """Build a minimal OHLCV frame where high/low bracket close with tiny noise."""
    idx = pd.date_range("2024-01-01", periods=len(closes), freq=freq)
    close = pd.Series(closes, dtype=float)
    # small intrabar range so ATR is well-defined but doesn't dominate moves
    high = close * 1.001
    low = close * 0.999
    df = pd.DataFrame({
        "open": close.shift(1).fillna(close.iloc[0]),
        "high": high.values,
        "low": low.values,
        "close": close.values,
        "volume": 100.0,
    }, index=idx)
    return df


# --- T1: synthetic bull (linear up) -> BULL -------------------------------
n = 200
bull_close = list(np.linspace(100, 200, n))  # steady uptrend
df_bull = _ohlcv_from_close(bull_close)
snap_bull = regime_snapshot(df_bull)
check("T1 bull trend == BULL", snap_bull.trend == TrendRegime.BULL)

# --- T2: synthetic bear (linear down) -> BEAR -----------------------------
bear_close = list(np.linspace(200, 100, n))
df_bear = _ohlcv_from_close(bear_close)
snap_bear = regime_snapshot(df_bear)
check("T2 bear trend == BEAR", snap_bear.trend == TrendRegime.BEAR)

# --- T3: sideways chop -> RANGE -------------------------------------------
# Mean-reverting noise around a flat price; spread stays < 0.5%
rng = np.random.default_rng(42)
chop = 100 + np.cumsum(rng.normal(0, 0.05, size=n))  # tiny steps
chop = chop - chop.mean() + 100  # center on 100
df_chop = _ohlcv_from_close(list(chop))
snap_chop = regime_snapshot(df_chop)
check("T3 chop trend == RANGE", snap_chop.trend == TrendRegime.RANGE)

# --- T4: ATR percentile math — known input -> expected label ---------------
# Build an ATR series whose last value is the global max -> pct 1.0 -> VOLATILE
atr_up = pd.Series(np.linspace(1, 100, 100))
check("T4a atr max → VOLATILE", classify_vol(atr_up) == VolRegime.VOLATILE)
# Last value is the global min -> pct ~0 -> CALM
atr_down = pd.Series(np.linspace(100, 1, 100))
check("T4b atr min → CALM", classify_vol(atr_down) == VolRegime.CALM)
# Median last value -> NORMAL (unique values, last ranks ~51/100)
atr_mid = pd.Series(list(range(1, 100)) + [50.5])
check("T4c atr median → NORMAL", classify_vol(atr_mid) == VolRegime.NORMAL)
# Short series fallback
check("T4d short series → NORMAL fallback", classify_vol(pd.Series([1.0, 2.0])) == VolRegime.NORMAL)

# --- T5: funding extreme 0.001/8h → EXTREME -------------------------------
fund_extreme = pd.Series([0.001] * 21)
check("T5 funding 0.001 → EXTREME", classify_funding(fund_extreme) == FundingRegime.EXTREME)
# Mildly positive -> LONG_FAVOR
fund_long = pd.Series([0.0001] * 21)
check("T5b funding +0.0001 → LONG_FAVOR", classify_funding(fund_long) == FundingRegime.LONG_FAVOR)
# Mildly negative -> SHORT_FAVOR
fund_short = pd.Series([-0.0001] * 21)
check("T5c funding -0.0001 → SHORT_FAVOR", classify_funding(fund_short) == FundingRegime.SHORT_FAVOR)
# Near zero -> NEUTRAL
fund_zero = pd.Series([0.0] * 21)
check("T5d funding ~0 → NEUTRAL", classify_funding(fund_zero) == FundingRegime.NEUTRAL)

# --- T6: regime_series returns DataFrame with expected columns -----------
rs = regime_series(df_bull)
expected_cols = {"trend", "vol", "funding", "ema_fast", "ema_slow", "atr_pct", "funding_ema"}
check("T6a regime_series returns DataFrame", isinstance(rs, pd.DataFrame))
check("T6b columns match expected", expected_cols.issubset(set(rs.columns)))
check("T6c row count matches input", len(rs) == len(df_bull))
check("T6d last trend is BULL", rs["trend"].iloc[-1] == TrendRegime.BULL.value)

# --- T7: classify_trend ADX gate ------------------------------------------
# Strong uptrend but ADX strong → BULL regardless of spread magnitude
check("T7a adx>25 strong up → BULL", classify_trend(105.0, 100.0, adx=30) == TrendRegime.BULL)
check("T7b adx>25 strong down → BEAR", classify_trend(100.0, 105.0, adx=30) == TrendRegime.BEAR)
# Tiny spread, weak adx → RANGE
check("T7c tiny spread weak adx → RANGE", classify_trend(100.001, 100.0, adx=10) == TrendRegime.RANGE)

# --- T8: regime_snapshot returns RegimeSnapshot, funding=None ok ---------
check("T8a snapshot type", isinstance(snap_bull, RegimeSnapshot))
check("T8b snapshot funding None → NEUTRAL", snap_bull.funding == FundingRegime.NEUTRAL)

passed = sum(1 for _, ok in results if ok)
total = len(results)
print(f"\n{passed}/{total} passed")
sys.exit(0 if passed == total else 1)
