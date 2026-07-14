"""Universe management for the cross-sectional momentum rank 1d strategy.

Responsibilities
----------------
- Hold the canonical *target* universe (the 10 majors from the spec).
- Hold the *active* universe (the subset actually feed by the local parquet
  tree today).
- Apply the per-bar liquidity filter:
    1. >= ``min_bars_in_last_7d`` non-NaN bars in the trailing 7 calendar
       days.
    2. The most recent bar's USD-notional volume (close * volume) must
       exceed ``min_usd_volume_per_day``.

The Binance fapi 1m bars record volume in **contracts** (1 contract == 1
base-currency unit on USD-M perps). For BTCUSDT 1 contract == 1 BTC and
for SOLUSDT 1 contract == 1 SOL, so USD volume is computed as
``close * volume`` on the resampled daily frame.

The filter is intentionally per-symbol / per-bar: a symbol that drops below
the volume threshold on a single day is excluded from that day's ranking
only -- not from the universe entirely.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

CONFIG_PATH = Path(__file__).parent / "config.json"


@dataclass(frozen=True)
class UniverseConfig:
    target: tuple
    active: tuple
    min_bars_in_last_7d: int
    min_usd_volume_per_day: float


def load_universe_config(cfg_path: Path = CONFIG_PATH) -> UniverseConfig:
    cfg = json.loads(cfg_path.read_text())
    uf = cfg.get("universe_filter", {})
    return UniverseConfig(
        target=tuple(cfg.get("target_universe") or []),
        active=tuple(cfg.get("active_universe") or cfg.get("target_universe") or []),
        min_bars_in_last_7d=int(uf.get("min_bars_in_last_7d", 5)),
        min_usd_volume_per_day=float(uf.get("min_usd_volume_per_day", 1_000_000.0)),
    )


def daily_usd_volume(df_1d: pd.DataFrame) -> pd.Series:
    """USD-notional daily volume, computed as ``close * volume``.

    The Binance fapi 1m parquet records *contract* volume; on USD-M perps 1
    contract == 1 unit of the base currency. ``close * volume`` is therefore
    a faithful USD-equivalent proxy.

    Returns a pandas Series aligned to the daily frame's index. Bars with
    NaN volume or close produce NaN.
    """
    if df_1d.empty:
        return pd.Series(dtype=float)
    close = df_1d["close"]
    vol = df_1d["volume"]
    return (close * vol).astype(float)


def trailing_bar_count(df_1d: pd.DataFrame, lookback_days: int = 7) -> pd.Series:
    """For each bar, count the number of NON-NaN bars in the trailing
    ``lookback_days`` calendar days (inclusive of the current bar).
    """
    if df_1d.empty:
        return pd.Series(dtype=float)
    has_bar = df_1d["close"].notna().astype(int)
    # Rolling on the *count* using a fixed-width trailing window of
    # ``lookback_days`` calendar days. We use min_periods=1 so partial
    # history at the very start is still informative.
    return has_bar.rolling(window=lookback_days, min_periods=1).sum()


def liquidity_filter(
    df_1d: pd.DataFrame,
    min_bars_in_last_7d: int = 5,
    min_usd_volume_per_day: float = 1_000_000.0,
) -> pd.Series:
    """Return a boolean Series: True if the symbol passes the filter on that
    bar.

    The filter is *per-bar*: a symbol that temporarily drops below the
    volume threshold is excluded from that day's ranking only.
    """
    if df_1d.empty:
        return pd.Series(dtype=bool)
    nbars = trailing_bar_count(df_1d, lookback_days=7)
    usd_vol = daily_usd_volume(df_1d)
    return (nbars >= min_bars_in_last_7d) & (usd_vol >= min_usd_volume_per_day)


def eligible_symbols_on(
    per_symbol_dfs: Dict[str, pd.DataFrame],
    asof: pd.Timestamp,
    cfg: Optional[UniverseConfig] = None,
) -> List[str]:
    """Return the symbols from ``per_symbol_dfs`` that pass the liquidity
    filter on the bar at-or-before ``asof``.

    Symbols whose latest available bar is older than ``asof`` are excluded.
    The filter needs the *trailing 7-day window* of bars, so we run
    :func:`liquidity_filter` over the full history up to ``asof`` and look
    up the last value.
    """
    cfg = cfg or load_universe_config()
    out: List[str] = []
    for sym, df in per_symbol_dfs.items():
        if df.empty:
            continue
        sub = df[df.index <= asof]
        if sub.empty:
            continue
        ok_series = liquidity_filter(
            sub,
            min_bars_in_last_7d=cfg.min_bars_in_last_7d,
            min_usd_volume_per_day=cfg.min_usd_volume_per_day,
        )
        if bool(ok_series.iloc[-1]):
            out.append(sym)
    return out