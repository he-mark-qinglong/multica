"""OI x funding squeeze factor — pure-function core.

Factor family: "who is trapped" positioning.
  a. oi_z      : z-score of daily OI change rate over a rolling window
  b. fund_mean : daily mean of 8h funding rates
  c. squeeze_score = oi_z * sign(fund_mean)
       oi_z high + funding > 0 -> crowded longs  -> short candidate (direction -1)
       oi_z high + funding < 0 -> crowded shorts -> long candidate  (direction +1)

Event study: |squeeze_score| > threshold, forward returns against the crowd
(direction = -sign(squeeze_score) ... equivalently -sign(fund_mean) when oi_z>0).

All functions are pure: dataframes in, dataframes out. No IO here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# alignment helpers
# ---------------------------------------------------------------------------

def oi_to_daily(oi: pd.DataFrame, ts_col: str = "timestamp",
                value_col: str = "open_interest_value") -> pd.Series:
    """Hourly (or any) OI rows -> daily last value, UTC day grid."""
    ts = pd.to_datetime(oi[ts_col], unit="ms", utc=True)
    s = pd.Series(oi[value_col].to_numpy(dtype=float), index=ts)
    return s.resample("1D").last().dropna()


def funding_to_daily(fu: pd.DataFrame, ts_col: str = "ts",
                     rate_col: str = "fundingRate") -> pd.Series:
    """8h funding rows -> daily mean funding rate, UTC day grid."""
    ts = pd.to_datetime(fu[ts_col], unit="ms", utc=True)
    s = pd.Series(fu[rate_col].to_numpy(dtype=float), index=ts)
    return s.resample("1D").mean().dropna()


def price_to_daily(px: pd.DataFrame, ts_col: str = "open_time",
                   close_col: str = "close") -> pd.Series:
    """Intraday bars -> daily last close, UTC day grid."""
    ts = pd.to_datetime(px[ts_col], unit="ms", utc=True)
    s = pd.Series(px[close_col].to_numpy(dtype=float), index=ts)
    return s.resample("1D").last().dropna()


# ---------------------------------------------------------------------------
# factor construction
# ---------------------------------------------------------------------------

def rolling_z(x: pd.Series, window: int) -> pd.Series:
    """Rolling z-score using only past data (no look-ahead).

    z_t = (x_t - mean(x_{t-w+1..t})) / std(x_{t-w+1..t})
    Requires full `window` observations; NaN before that.
    """
    mu = x.rolling(window).mean()
    sd = x.rolling(window).std(ddof=1)
    return (x - mu) / sd.replace(0.0, np.nan)


def oi_change_z(oi_daily: pd.Series, window: int) -> pd.Series:
    """a. z-score of daily OI pct change."""
    chg = oi_daily.pct_change()
    return rolling_z(chg, window)


def squeeze_score(oi_z: pd.Series, fund_mean: pd.Series) -> pd.Series:
    """c. oi_z * sign(funding daily mean), aligned on common days."""
    f = fund_mean.reindex(oi_z.index)
    return oi_z * np.sign(f)


def forward_returns(close_daily: pd.Series, horizons_days: list[int]) -> pd.DataFrame:
    """Forward simple returns from close of day t to close of day t+h.

    Column h: ret_{t->t+h} = close_{t+h}/close_t - 1. NaN at the tail.
    """
    out = {}
    for h in horizons_days:
        out[h] = close_daily.shift(-h) / close_daily - 1.0
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# event study
# ---------------------------------------------------------------------------

def event_table(score: pd.Series, fwd: pd.DataFrame,
                threshold: float = 2.0) -> pd.DataFrame:
    """Rows = events where |score| > threshold.

    direction = -sign(score): crowded longs (score>0) -> short; crowded
    shorts (score<0) -> long. `ret_h` columns are direction-adjusted
    (positive = squeeze theory was right).
    """
    ev = score[score.abs() > threshold]
    rows = pd.DataFrame(index=ev.index)
    rows["score"] = ev
    rows["direction"] = -np.sign(ev)
    for h in fwd.columns:
        rows[f"ret_{h}"] = fwd[h].reindex(ev.index) * rows["direction"]
    return rows


def baseline_table(score: pd.Series, fwd: pd.DataFrame) -> pd.DataFrame:
    """Unconditional baseline: same direction rule applied on EVERY day.

    direction_t = -sign(score_t) on all days with a valid score; isolates the
    incremental information of the |score| > threshold filter.
    """
    valid = score.dropna()
    rows = pd.DataFrame(index=valid.index)
    rows["direction"] = -np.sign(valid)
    for h in fwd.columns:
        rows[f"ret_{h}"] = fwd[h].reindex(valid.index) * rows["direction"]
    return rows


def summarize(rets: pd.DataFrame) -> pd.DataFrame:
    """mean / t / win-rate / n per ret_h column (t on non-NaN obs)."""
    out = []
    for c in [c for c in rets.columns if c.startswith("ret_")]:
        r = rets[c].dropna()
        n = len(r)
        mean = r.mean() if n else np.nan
        sd = r.std(ddof=1) if n > 1 else np.nan
        t = mean / (sd / np.sqrt(n)) if n > 1 and sd and sd > 0 else np.nan
        win = (r > 0).mean() if n else np.nan
        out.append({"col": c, "n": n, "mean": mean, "t": t, "win": win})
    return pd.DataFrame(out).set_index("col")
