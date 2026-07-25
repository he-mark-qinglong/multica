"""Authoritative unified data loader for quant-loop strategies.

This module is the single entry point that all new strategies should use to
load market data. It encodes the canonical path layout of the repo's ``data/``
tree and normalises the schemas to a UTC ``DatetimeIndex``.

Supported datasets and their canonical paths
--------------------------------------------
- **Klines** (``data/perp_{tf}/{SYM}_{tf}.parquet``) — tf ∈
  ``1m, 5m, 15m, 30m, 2h``. Different timeframes carry different schemas:

  - ``1m``  — 12 columns: ``open_time, open, high, low, close, volume,
    close_time, quote_volume, trades, taker_buy_base, taker_buy_quote, ignore``
  - ``5m`` / ``15m`` — 10 columns (no ``close_time`` / ``ignore``)
  - ``30m`` / ``2h``  — 8 columns (no ``close_time``, ``ignore``, ``trades``,
    ``taker_buy_*``; only 7 symbols available)

  Column *count* differs across timeframes; this loader preserves the
  source columns verbatim and only normalises the index.

- **Funding** (``data/funding/{SYM}.parquet``) — 4 columns:
  ``ts (timestamp[ms, tz=UTC]), symbol, fundingRate, markPrice``.
  Coverage starts 2021-11-20 (Binance USDT-M perp funding history).

- **AggTrades** (``data/trades/{SYM}_aggtrades.parquet``) — this is a
  *hive-partitioned directory* (``year=YYYY/month=M/data.parquet``), NOT a
  single file. 8 columns:
  ``ts (timestamp[ms, tz=UTC]), symbol, agg_id, price, qty, first_id,
  last_id, is_buyer_maker``. Coverage is 2026-01→present only and the
  full pool is ~9.8G — always load with a ``start``/``end`` window AND
  column projection. ``load_aggtrades`` enforces both.

Known layout difference with ``_shared/templates/run_strategy.py``
------------------------------------------------------------------
``_shared/templates/run_strategy.py:82-97`` (``load_bars_dir``) expects a
flat ``{SYMBOL}.parquet`` layout in the supplied directory, which does
NOT match the per-timeframe ``{SYM}_{tf}.parquet`` layout this loader
targets. ``run_strategy.py`` is owned by another task; this loader is
the authoritative entry for new strategies. The template's adapter is
deferred to that task — do not "fix" ``run_strategy.py`` from here.

Index normalisation
-------------------
Every loader returns a ``DataFrame`` indexed by a UTC ``DatetimeIndex``
(``index.tz == 'UTC'``), sorted ascending. Time-window arguments
(``start``/``end``) accept anything ``pd.Timestamp`` accepts (str,
``datetime``, ``Timestamp``); ``end`` is **exclusive** on the left
boundary (consistent with pandas slice semantics).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

# --- quant-loop root resolution ---------------------------------------------
#
# data_loader.py is imported both as a package module
# (``_shared.data_loader``) and as a bare top-level module
# (``import data_loader`` after a ``sys.path.insert(0, '.../_shared')``
# in strategy directories). T1 (``_shared/paths.py``) is the canonical
# source of the data root for the rest of the codebase; this module is
# in wave-1 alongside T1, so we:
#   1. First try to import ``_shared.paths.data_root`` (the T1 interface).
#   2. If that fails (T1 not yet landed), fall back to deriving the
#      root from ``__file__`` so the loader still works in isolation
#      and the tests can be run independently of T1's branch state.
#
# The ``QUANT_LOOP_ROOT`` env var is honored by both branches, matching
# the pattern that ``paths.py`` is expected to expose.

BAR_TFS: tuple[str, ...] = ("1m", "5m", "15m", "30m", "2h")


def _to_utc_datetime(series: pd.Series) -> pd.Series:
    """Robustly coerce a column to tz-aware ``datetime64[ns, UTC]``.

    Real Binance klines store ``open_time`` as **int64 epoch ms** (no
    timezone info). Synthetic callers typically build the column from
    ``pd.to_datetime([...], utc=True)`` which yields tz-aware
    ``datetime64[ns, UTC]``. A naive ``pd.to_datetime(s, utc=True)``
    treats raw ints as *nanoseconds* and produces year-1970 garbage.

    Accepts both shapes; always returns a UTC tz-aware datetime64 column.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        if getattr(series.dtype, "tz", None) is None:
            return series.dt.tz_localize("UTC")
        return series.dt.tz_convert("UTC")
    if pd.api.types.is_integer_dtype(series) or pd.api.types.is_float_dtype(series):
        # Binance klines store ms; assume ms unless caller is passing
        # seconds (which would need very small ints near epoch anyway).
        return pd.to_datetime(series, unit="ms", utc=True)
    return pd.to_datetime(series, utc=True)


def _resolve_data_root() -> Path:
    """Return the quant-loop root used to anchor ``data/``.

    Prefers ``_shared.paths.data_root`` (T1) and falls back to deriving
    the root from this file's location when T1 is not yet present.
    """
    try:
        from _shared.paths import data_root as _paths_data_root  # type: ignore

        return Path(_paths_data_root())
    except Exception:
        # Fallback: anchor at the parent of ``_shared/`` (i.e. quant-loop/).
        # Honor ``QUANT_LOOP_ROOT`` if set, matching paths.py convention.
        import os

        env = os.environ.get("QUANT_LOOP_ROOT")
        if env:
            return Path(env)
        here = Path(__file__).resolve()
        # ``_shared/data_loader.py`` -> parents[1] == quant-loop/
        if here.parent.name == "_shared":
            return here.parents[1]
        # Bare-module mode (strategy did ``sys.path.insert(0, '.../_shared')``):
        # ``__file__`` already lives directly in quant-loop/.
        return here.parent


def data_root() -> Path:
    """Return the canonical ``data/`` directory for quant-loop.

    Public function so strategy code can locate other data files
    (e.g. manifests) without re-deriving the root.
    """
    return _resolve_data_root() / "data"


# --- Bars (klines) ----------------------------------------------------------


def _bars_path(symbol: str, tf: str) -> Path:
    if tf not in BAR_TFS:
        raise ValueError(f"unknown tf {tf!r}; expected one of {BAR_TFS}")
    return data_root() / f"perp_{tf}" / f"{symbol}_{tf}.parquet"


def load_bars(
    symbol: str,
    tf: str,
    start=None,
    end=None,
    columns: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Load klines for ``symbol``/``tf``.

    Returns a ``DataFrame`` indexed by a sorted UTC ``DatetimeIndex``
    derived from the ``open_time`` column. The original columns are
    preserved verbatim (column count differs across timeframes — see
    module docstring).

    Parameters
    ----------
    symbol : str
        e.g. ``"BTCUSDT"``.
    tf : str
        One of ``BAR_TFS`` (``"1m"``, ``"5m"``, ``"15m"``, ``"30m"``, ``"2h"``).
    start, end : optional
        ``pd.Timestamp``-compatible (str / datetime / Timestamp), both
        interpreted in UTC. ``start`` is inclusive, ``end`` is exclusive.
    columns : optional
        If given, restricts the parquet read to this column subset
        (pyarrow ``columns=`` argument — requires pyarrow engine).

    Raises
    ------
    ValueError
        ``tf`` is not in ``BAR_TFS``.
    FileNotFoundError
        The expected parquet file does not exist.
    """
    path = _bars_path(symbol, tf)
    if not path.is_file():
        raise FileNotFoundError(f"no klines for {symbol} {tf}: {path}")

    if columns is not None:
        cols = list(columns)
        # ``open_time`` is needed to build the index; pull it in addition
        # to the caller-requested columns when it's missing, and drop it
        # after the index promotion.
        if "open_time" not in cols:
            cols = ["open_time", *cols]
        df = pd.read_parquet(path, columns=cols)
        has_open_time_col = "open_time" in columns
    else:
        df = pd.read_parquet(path)
        has_open_time_col = True

    df["open_time"] = _to_utc_datetime(df["open_time"])
    df = df.set_index("open_time").sort_index()

    if start is not None:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df.index < pd.Timestamp(end, tz="UTC")]

    if not has_open_time_col:
        df = df.reset_index().drop(columns=["open_time"])
    return df


# --- Funding ----------------------------------------------------------------


def load_funding(
    symbol: str,
    start=None,
    end=None,
    columns: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Load 8h funding-rate history for ``symbol``.

    The ``ts`` column becomes a sorted UTC ``DatetimeIndex``. Coverage
    starts 2021-11-20 (Binance USDT-M perp history).
    """
    path = data_root() / "funding" / f"{symbol}.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"no funding for {symbol}: {path}")

    if columns is not None:
        df = pd.read_parquet(path, columns=list(columns))
    else:
        df = pd.read_parquet(path)

    df["ts"] = _to_utc_datetime(df["ts"])
    df = df.set_index("ts").sort_index()

    if start is not None:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df.index < pd.Timestamp(end, tz="UTC")]
    return df


# --- AggTrades --------------------------------------------------------------


def load_aggtrades(
    symbol: str,
    start,
    end,
    columns: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Load an aggTrades window for ``symbol``.

    The aggTrades pool is a hive-partitioned DIRECTORY
    (``year=YYYY/month=M/data.parquet``) of ~9.8G per symbol. ``start``
    and ``end`` are **required** — this loader will not let a caller
    accidentally scan the entire dataset. ``columns`` is honoured via
    pyarrow column projection so callers only pay for what they read.

    Parameters
    ----------
    symbol : str
    start, end : required
        ``pd.Timestamp``-compatible (UTC). ``start`` is inclusive,
        ``end`` is exclusive.
    columns : optional
        Subset of columns to materialise. Defaults to all 8 columns
        (``ts, symbol, agg_id, price, qty, first_id, last_id,
        is_buyer_maker``).

    Returns
    -------
    pandas.DataFrame
        Indexed by integer position; ``ts`` is a UTC datetime64 column
        (matches the on-disk ``timestamp[ms, tz=UTC]`` schema). We do
        not promote ``ts`` to the index because aggTrades is not
        naturally unique on timestamp (multiple trades per ms).

    Raises
    ------
    FileNotFoundError
        The aggTrades directory does not exist.
    """
    if start is None or end is None:
        raise ValueError(
            "load_aggtrades requires both start and end — refusing to "
            "scan the full ~9.8G aggTrades pool without a window."
        )

    import pyarrow.dataset as ds

    path = data_root() / "trades" / f"{symbol}_aggtrades.parquet"
    if not path.is_dir():
        raise FileNotFoundError(f"no aggtrades for {symbol}: {path}")

    dataset = ds.dataset(str(path), format="parquet", partitioning="hive")

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    filt = (ds.field("ts") >= start_ts) & (ds.field("ts") < end_ts)
    table = dataset.to_table(columns=columns, filter=filt)
    return table.to_pandas()


# --- Availability -----------------------------------------------------------


def available(symbol: str) -> dict:
    """Return a coverage report for ``symbol``.

    The result is a dict of the form::

        {
            "bars":    ["1m", "5m", "15m", ...],   # subset of BAR_TFS
            "funding": bool,
            "aggtrades": bool,
        }

    Only file existence is checked; no data is read.
    """
    bars = [tf for tf in BAR_TFS if _bars_path(symbol, tf).is_file()]
    funding = (data_root() / "funding" / f"{symbol}.parquet").is_file()
    aggtrades = (data_root() / "trades" / f"{symbol}_aggtrades.parquet").is_dir()
    return {"bars": bars, "funding": funding, "aggtrades": aggtrades}


__all__ = [
    "BAR_TFS",
    "data_root",
    "load_bars",
    "load_funding",
    "load_aggtrades",
    "available",
]