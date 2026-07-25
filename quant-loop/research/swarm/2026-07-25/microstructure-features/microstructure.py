"""Microstructure feature builder from Binance aggTrades.

Processes aggTrades parquet partitions month-by-month (required: the full
history does not fit in memory). Computes per-1m trade-flow features and
aligns them to the canonical 1m OHLCV index.

Features are strictly past-boundary:
  - bar-aggregates use trades inside the 1m bar only;
  - rolling z-scores are computed over shifted windows (bar i never sees
    bar i's own statistic).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


def _month_files(symbol: str, agg_root: Path) -> List[Path]:
    """Return sorted list of monthly data.parquet files for a symbol."""
    base = agg_root / f"{symbol}_aggtrades.parquet"
    if not base.is_dir():
        return []
    files = sorted(base.rglob("data.parquet"))
    # ensure year/month ordering
    def _key(p: Path) -> tuple:
        m = re.search(r"year=(\d+)/month=(\d+)", str(p))
        if m:
            return (int(m.group(1)), int(m.group(2)))
        return (0, 0)
    return sorted(files, key=_key)


def _aggregate_month(df: pd.DataFrame, ohlcv: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Aggregate one month of aggTrades to 1m features (vectorised)."""
    df = df.copy()
    df["minute"] = df["ts"].dt.floor("min")

    # buyer-maker = False -> taker buy (aggressive buyer)
    buy_mask = ~df["is_buyer_maker"].astype(bool)
    df["notional"] = df["price"] * df["qty"]
    whale_thr = 100_000.0

    gb = df.groupby("minute")
    gb_buy = df[buy_mask].groupby("minute")
    gb_sell = df[~buy_mask].groupby("minute")

    volume_total = gb["qty"].sum()
    volume_buy = gb_buy["qty"].sum().reindex(volume_total.index, fill_value=0.0)
    volume_sell = volume_total - volume_buy

    notional_total = gb["notional"].sum()
    notional_buy = gb_buy["notional"].sum().reindex(notional_total.index, fill_value=0.0)
    notional_sell = notional_total - notional_buy

    trades_total = gb.size()
    trades_buy = gb_buy.size().reindex(trades_total.index, fill_value=0)
    trades_sell = trades_total - trades_buy

    whale_mask = df["notional"] >= whale_thr
    whale_buy = df[buy_mask & whale_mask].groupby("minute")["notional"].sum().reindex(notional_total.index, fill_value=0.0)
    whale_sell = df[~buy_mask & whale_mask].groupby("minute")["notional"].sum().reindex(notional_total.index, fill_value=0.0)

    avg_buy_size = notional_buy / trades_buy.replace(0, np.nan)
    avg_sell_size = notional_sell / trades_sell.replace(0, np.nan)

    grouped = pd.DataFrame({
        "volume_total": volume_total,
        "volume_buy": volume_buy,
        "volume_sell": volume_sell,
        "trades_total": trades_total,
        "trades_buy": trades_buy,
        "trades_sell": trades_sell,
        "notional_total": notional_total,
        "notional_buy": notional_buy,
        "notional_sell": notional_sell,
        "avg_buy_size": avg_buy_size,
        "avg_sell_size": avg_sell_size,
        "whale_buy_notional": whale_buy,
        "whale_sell_notional": whale_sell,
    })
    grouped.index = pd.DatetimeIndex(grouped.index, tz="UTC")
    return grouped.fillna(0.0)


def _add_rolling_features(s: pd.Series, lookback: int = 240, min_periods: int = 60) -> pd.Series:
    """Return z-score of a series using only past bars (shift=1)."""
    shifted = s.shift(1)
    mu = shifted.rolling(lookback, min_periods=min_periods).mean()
    sigma = shifted.rolling(lookback, min_periods=min_periods).std(ddof=0)
    return ((s - mu) / sigma.replace(0.0, np.nan)).rename(f"{s.name}_z")


def _build_one_symbol(symbol: str, files: List[Path], ohlcv: pd.DataFrame,
                      start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Aggregate all monthly files for a symbol then add derived features."""
    start = pd.to_datetime(start, utc=True).tz_convert("UTC")
    end = pd.to_datetime(end, utc=True).tz_convert("UTC")

    chunks: List[pd.DataFrame] = []
    for f in files:
        # infer year/month from path
        m = re.search(r"year=(\d+)/month=(\d+)", str(f))
        if not m:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        # skip files fully outside requested range
        file_start = pd.Timestamp(f"{y}-{mo:02d}-01", tz="UTC")
        file_end = file_start + pd.offsets.MonthEnd(1) + pd.Timedelta(days=1)
        if file_end < start or file_start > end:
            continue
        df = pd.read_parquet(f, columns=["ts", "price", "qty", "is_buyer_maker"])
        # filter to requested window before aggregation to keep memory low
        df = df[(df["ts"] >= start) & (df["ts"] <= end + pd.Timedelta(days=1))]
        if len(df) == 0:
            continue
        agg = _aggregate_month(df)
        chunks.append(agg)
        del df

    if not chunks:
        raise ValueError(f"no aggTrade data for {symbol} in {start} .. {end}")

    feats = pd.concat(chunks).sort_index()
    # remove duplicate minute indices if any
    feats = feats[~feats.index.duplicated(keep="first")]
    # align to tz-naive ns index used by OHLCV
    if feats.index.tz is not None:
        feats.index = feats.index.tz_convert(None)

    # Reindex to the OHLCV 1m index and forward-fill ratios where no trades occurred
    ohlcv_idx = pd.DatetimeIndex(ohlcv.index)
    # restrict to overlapping period
    feats = feats.reindex(ohlcv_idx, method="ffill")
    # volume columns for no-trade bars become NaN after reindex; fill with 0
    volume_cols = [c for c in feats.columns if "volume" in c or "trades" in c or "notional" in c or "whale" in c or "size" in c]
    feats[volume_cols] = feats[volume_cols].fillna(0.0)
    # ratio columns forward-filled already, but ensure no NaN at start
    feats = feats.ffill().fillna(0.0)

    # Derived ratios (no look-ahead)
    eps = 1e-12
    feats["flow_pressure"] = (feats["notional_buy"] - feats["notional_sell"]) / (feats["notional_total"] + eps)
    feats["buy_notional_ratio"] = feats["notional_buy"] / (feats["notional_total"] + eps)
    feats["buy_count_ratio"] = feats["trades_buy"] / (feats["trades_total"] + eps)
    feats["whale_buy_pct"] = feats["whale_buy_notional"] / (feats["notional_total"] + eps)
    feats["whale_sell_pct"] = feats["whale_sell_notional"] / (feats["notional_total"] + eps)
    feats["whale_total_pct"] = feats["whale_buy_pct"] + feats["whale_sell_pct"]
    feats["size_skew"] = feats["avg_buy_size"] / (feats["avg_sell_size"] + eps)
    feats["volume_imbalance"] = (feats["notional_buy"] - feats["notional_sell"]).abs() / (feats["notional_total"] + eps)
    feats["trade_intensity"] = feats["trades_total"] / 60.0

    # Cumulative signed flow over short windows
    feats["flow_cum_5m"] = feats["flow_pressure"].rolling(5, min_periods=1).sum()
    feats["flow_cum_15m"] = feats["flow_pressure"].rolling(15, min_periods=1).sum()

    # Rolling z-scores (shift=1, no current-bar leakage)
    for c in ["flow_pressure", "buy_notional_ratio", "buy_count_ratio", "whale_total_pct", "notional_total"]:
        feats[f"{c}_z"] = _add_rolling_features(feats[c], lookback=240, min_periods=60)

    # Merge OHLCV-derived proxies
    if ohlcv is not None:
        feats["ohlcv_close"] = ohlcv["close"]
        feats["ohlcv_taker_buy_ratio"] = ohlcv["taker_buy_base"] / (ohlcv["volume"] + eps)
        feats["range_ratio"] = (ohlcv["high"] - ohlcv["low"]) / (ohlcv["close"] + eps)
        denom = (ohlcv["high"] - ohlcv["low"]).replace(0.0, np.nan)
        feats["close_loc"] = (ohlcv["close"] - ohlcv["low"]) / denom
        feats["close_loc"] = feats["close_loc"].fillna(0.5)

    return feats


def build_microstructure_features(
    symbols: List[str],
    ohlcv_map: Dict[str, pd.DataFrame],
    agg_root: Path = Path("/Users/mark/multica/quant-loop/data/trades"),
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
) -> Dict[str, pd.DataFrame]:
    """Build microstructure features for each symbol.

    Parameters
    ----------
    symbols:
        List of symbols (e.g. ["BTCUSDT", "SOLUSDT"]).
    ohlcv_map:
        Dict symbol -> 1m OHLCV DataFrame with DatetimeIndex (tz-aware or naive).
    agg_root:
        Root directory containing ``{symbol}_aggtrades.parquet`` partitions.
    start, end:
        Optional UTC bounds. Defaults to the overlap of all OHLCV indices.

    Returns
    -------
    Dict symbol -> feature DataFrame indexed on the OHLCV 1m index.
    """
    if start is None:
        start = max(df.index.min() for df in ohlcv_map.values())
    if end is None:
        end = min(df.index.max() for df in ohlcv_map.values())

    out = {}
    for sym in symbols:
        files = _month_files(sym, agg_root)
        if not files:
            raise FileNotFoundError(f"no aggTrade partitions for {sym}")
        out[sym] = _build_one_symbol(sym, files, ohlcv_map[sym], start, end)
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/Users/mark/multica/quant-loop")
    from research.swarm.utils import load_ohlcv_shared
    syms = ["BTCUSDT", "SOLUSDT"]
    ohlcv = load_ohlcv_shared(syms)
    feats = build_microstructure_features(syms, ohlcv)
    for sym, df in feats.items():
        print(sym, df.shape, df.index.min(), df.index.max())
        print(df.head())
