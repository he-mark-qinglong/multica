"""Unified data access layer (F20).

One function per dataset, one canonical schema, zero knowledge of the
underlying file layouts required by callers:

    get_klines(symbol, start, end, interval="1m", venue=None)
        -> timestamp, open, high, low, close, volume, quote_volume, trades
    get_trades(symbol, start, end, venue=None)
        -> timestamp, price, qty, is_buyer_maker, agg_id
    get_funding(symbol, start, end, venue=None)
        -> timestamp, funding_rate, mark_price
    get_oi(symbol, start, end, venue=None)
        -> timestamp, open_interest, open_interest_value
    get_ls_ratio(symbol, start, end, venue=None)
        -> timestamp, long_short_ratio, long_account, short_account
    get_liquidations(symbol, start, end, venue=None)
        -> timestamp, symbol, side, price, qty, usd_value

Conventions:
  - ``timestamp`` is always a tz-aware UTC datetime column, ascending.
  - ``start`` / ``end`` accept int ms-since-epoch, ``pd.Timestamp``, or
    date strings; the range is half-open ``[start, end)``. Either may be
    None (unbounded).
  - ``venue`` is reserved for multi-venue support; only ``"binance"`` (the
    default) exists today — anything else raises ``ValueError``.
  - Missing datasets raise ``FileNotFoundError`` with the expected path.

References:
  - Command Query Responsibility Separation (single read facade in front of
    heterogeneous stores): Fowler, "CQRS" (2011).
  - Arrow/pandas interchange: canonical tz-aware timestamps at the boundary
    so downstream code never re-derives units (the workspace mixes int-ms
    perp files, datetime funding files, and jsonl liquidation logs).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

PathLike = str | Path
TimeLike = int | float | str | pd.Timestamp | None

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"

SUPPORTED_VENUES = ("binance",)


def _check_venue(venue: str | None) -> None:
    if venue is not None and venue not in SUPPORTED_VENUES:
        raise ValueError(f"unsupported venue {venue!r}; supported: {SUPPORTED_VENUES}")


def _to_ms(t: TimeLike) -> int | None:
    """Normalise a time bound to ms-since-epoch; None passes through."""
    if t is None:
        return None
    if isinstance(t, (int, float)):
        return int(t)
    ts = pd.Timestamp(t)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp() * 1000)


_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def _ts_col_ms(df: pd.DataFrame) -> pd.Series:
    """Canonical ``timestamp`` column → int64 ms (pandas-3-safe)."""
    return (df["timestamp"] - _EPOCH) // pd.Timedelta(milliseconds=1)


def _slice(df: pd.DataFrame, start: TimeLike, end: TimeLike) -> pd.DataFrame:
    """Half-open [start, end) slice on the canonical ``timestamp`` column."""
    start_ms, end_ms = _to_ms(start), _to_ms(end)
    if start_ms is None and end_ms is None:
        return df.reset_index(drop=True)
    ts_ms = _ts_col_ms(df)
    mask = pd.Series(True, index=df.index)
    if start_ms is not None:
        mask &= ts_ms >= start_ms
    if end_ms is not None:
        mask &= ts_ms < end_ms
    return df[mask].reset_index(drop=True)


def _ms_to_utc(col: pd.Series) -> pd.Series:
    return pd.to_datetime(col.astype("int64"), unit="ms", utc=True)


def _require(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {path}")
    return path


def get_klines(
    symbol: str,
    start: TimeLike = None,
    end: TimeLike = None,
    interval: str = "1m",
    venue: str | None = None,
    data_root: PathLike = DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Perp OHLCV bars (from ``data/perp_{interval}/{SYMBOL}_{interval}.parquet``)."""
    _check_venue(venue)
    path = _require(Path(data_root) / f"perp_{interval}" / f"{symbol}_{interval}.parquet")
    df = pd.read_parquet(path)
    out = pd.DataFrame(
        {
            "timestamp": _ms_to_utc(df["open_time"]),
            "open": df["open"],
            "high": df["high"],
            "low": df["low"],
            "close": df["close"],
            "volume": df["volume"],
            "quote_volume": df["quote_volume"],
            "trades": df["trades"],
        }
    )
    return _slice(out, start, end)


def get_trades(
    symbol: str,
    start: TimeLike = None,
    end: TimeLike = None,
    venue: str | None = None,
    data_root: PathLike = DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Aggregate trades (from the hive-partitioned ``data/trades/`` store)."""
    _check_venue(venue)
    path = _require(Path(data_root) / "trades" / f"{symbol}_aggtrades.parquet")
    df = pd.read_parquet(path, columns=["ts", "agg_id", "price", "qty", "is_buyer_maker"])
    out = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(df["ts"], utc=True),
            "price": df["price"],
            "qty": df["qty"],
            "is_buyer_maker": df["is_buyer_maker"],
            "agg_id": df["agg_id"],
        }
    ).sort_values("timestamp")
    return _slice(out, start, end)


def get_funding(
    symbol: str,
    start: TimeLike = None,
    end: TimeLike = None,
    venue: str | None = None,
    data_root: PathLike = DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Funding-rate history (from ``data/funding/{SYMBOL}.parquet``)."""
    _check_venue(venue)
    path = _require(Path(data_root) / "funding" / f"{symbol}.parquet")
    df = pd.read_parquet(path)
    out = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(df["ts"], utc=True),
            "funding_rate": df["fundingRate"].astype("float64"),
            "mark_price": df["markPrice"].astype("float64"),
        }
    ).sort_values("timestamp")
    return _slice(out, start, end)


def get_oi(
    symbol: str,
    start: TimeLike = None,
    end: TimeLike = None,
    venue: str | None = None,
    data_root: PathLike = DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Open-interest history (from ``data/oi/{SYMBOL}.parquet``, see F7)."""
    _check_venue(venue)
    path = _require(Path(data_root) / "oi" / f"{symbol}.parquet")
    df = pd.read_parquet(path)
    out = pd.DataFrame(
        {
            "timestamp": _ms_to_utc(df["timestamp"]),
            "open_interest": df["open_interest"],
            "open_interest_value": df["open_interest_value"],
        }
    )
    return _slice(out, start, end)


def get_ls_ratio(
    symbol: str,
    start: TimeLike = None,
    end: TimeLike = None,
    venue: str | None = None,
    data_root: PathLike = DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Global long/short account ratio (from ``data/ls_ratio/``, see F8)."""
    _check_venue(venue)
    path = _require(Path(data_root) / "ls_ratio" / f"{symbol}.parquet")
    df = pd.read_parquet(path)
    out = pd.DataFrame(
        {
            "timestamp": _ms_to_utc(df["timestamp"]),
            "long_short_ratio": df["long_short_ratio"],
            "long_account": df["long_account"],
            "short_account": df["short_account"],
        }
    ).sort_values("timestamp")
    return _slice(out, start, end)


def get_liquidations(
    symbol: str,
    start: TimeLike = None,
    end: TimeLike = None,
    venue: str | None = None,
    data_root: PathLike = DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Liquidation events (parquet if consolidated, else raw jsonl; see F6)."""
    _check_venue(venue)
    root = Path(data_root) / "liquidations"
    parquet_path = root / f"{symbol}.parquet"
    jsonl_path = root / f"{symbol}.jsonl"
    if parquet_path.exists():
        df = pd.read_parquet(parquet_path)
    elif jsonl_path.exists():
        from _shared.data.liq_loader import load_jsonl

        df = load_jsonl(jsonl_path)
    else:
        raise FileNotFoundError(
            f"dataset not found: {parquet_path} (nor raw {jsonl_path})"
        )
    if len(df) == 0:
        return pd.DataFrame(
            columns=["timestamp", "symbol", "side", "price", "qty", "usd_value"]
        )
    out = pd.DataFrame(
        {
            "timestamp": _ms_to_utc(df["timestamp"]),
            "symbol": df["symbol"],
            "side": df["side"],
            "price": df["price"],
            "qty": df["qty"],
            "usd_value": df["usd_value"],
        }
    )
    return _slice(out, start, end)
