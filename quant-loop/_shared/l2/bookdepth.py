"""Loader for Binance public-data ``bookDepth`` snapshots (B4).

Data source: https://data.binance.vision — ``data/futures/um/daily/
bookDepth/<SYMBOL>/``.  Note what this dataset actually is: **not** a
raw diff-depth stream, but aggregated depth snapshots taken roughly
every 30 seconds.  Each snapshot has one row per percentage band
(-5%..-1% bids, +1%..+5% asks) with columns:

* ``percentage`` — band edge, distance from the reference price in %;
* ``depth``      — *cumulative* base-asset quantity within that band;
* ``notional``   — *cumulative* quote-asset notional within that band.

:func:`snapshots_from_bookdepth` de-cumulates the bands into a
5-level-per-side :class:`~_shared.l2.book.BookState`:

* band quantity = ``depth(p) - depth(p-1)``;
* band price    = volume-weighted average price inside the band, from
  the notional difference (``notional(p) - notional(p-1)``) divided by
  the band quantity — the honest average execution price inside that
  band, so no reference price needs to be assumed.

Because each timestamp is a full snapshot (not a diff), replaying this
dataset drives :func:`_shared.l2.replay.replay` with a sequence of
:class:`BookState` events, each replacing the previous book.

This module does file I/O (parquet via pandas); the conversion itself
is a pure function of the dataframe rows.

References
----------
- Binance public data documentation, "bookDepth" dataset schema
  (data.binance.vision).
- Bouchaud, Mézard & Potters (2002) — average depth profile from
  cumulative depth bands.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, Tuple

from _shared.l2.book import BookState, Level, snapshot

__all__ = [
    "bookdepth_rows_to_snapshots",
    "load_bookdepth_parquet",
]

L2_DATA_DIR = Path("/Users/mark/multica/quant-loop/data/l2")


def bookdepth_rows_to_snapshots(
    rows: Sequence[Tuple[int, int, float, float]],
) -> List[BookState]:
    """Convert ``(ts_ns, percentage, depth, notional)`` rows to snapshots.

    Rows must be grouped by ``ts_ns`` (order within a group does not
    matter).  Groups are emitted in first-seen timestamp order.  A band
    whose de-cumulated quantity is non-positive is skipped (it carries
    no marginal liquidity).
    """
    groups: dict[int, List[Tuple[int, float, float]]] = {}
    order: List[int] = []
    for ts_ns, pct, depth, notional in rows:
        if ts_ns not in groups:
            groups[ts_ns] = []
            order.append(ts_ns)
        groups[ts_ns].append((int(pct), float(depth), float(notional)))

    states: List[BookState] = []
    for ts_ns in order:
        bids = _de_cumulate([r for r in groups[ts_ns] if r[0] < 0])
        asks = _de_cumulate([r for r in groups[ts_ns] if r[0] > 0])
        if bids and asks:
            states.append(snapshot(ts_ns=ts_ns, bids=bids, asks=asks))
    return states


def _de_cumulate(
    bands: Sequence[Tuple[int, float, float]],
) -> List[Level]:
    """Turn cumulative ``(pct, depth, notional)`` bands into price levels.

    Bands are sorted from the reference price outward (|pct| ascending)
    before differencing, so band ``p`` covers ``(p-1, p]`` percent.
    """
    levels: List[Level] = []
    prev_depth = 0.0
    prev_notional = 0.0
    for _pct, depth, notional in sorted(bands, key=lambda r: abs(r[0])):
        band_qty = depth - prev_depth
        band_notional = notional - prev_notional
        if band_qty > 0.0 and band_notional > 0.0:
            levels.append((band_notional / band_qty, band_qty))
        prev_depth = depth
        prev_notional = notional
    return levels


def load_bookdepth_parquet(path: "str | Path") -> List[BookState]:
    """Load a bookDepth parquet file into a list of :class:`BookState`.

    The parquet must carry columns ``ts_ns, percentage, depth,
    notional`` (the layout written to ``data/l2/``).  Requires pandas.
    """
    import pandas as pd  # local import: keep the package import light

    df = pd.read_parquet(path, columns=["ts_ns", "percentage", "depth", "notional"])
    rows = [
        (int(r.ts_ns), int(r.percentage), float(r.depth), float(r.notional))
        for r in df.itertuples(index=False)
    ]
    return bookdepth_rows_to_snapshots(rows)
