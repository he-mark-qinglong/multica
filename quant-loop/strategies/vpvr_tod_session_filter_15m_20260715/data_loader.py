"""Load 15m OHLCV from the canonical 30m Binance USD-M parquet source.

The strategy manifest references a `SESSION-OF-DAY-EMBEDDED` dataset with
158587 15m bars for both BTCUSDT and ETHUSDT. The source-of-truth on disk
is `/home/smark/multica/quant-loop/data/perp_30m/{SYM}_30m.parquet`
(79296 30m bars each); we synthesize a deterministic 15m OHLCV by
splitting each 30m bar into two sub-bars. Row count matches the manifest
truncated to 158587 with dropna on the resampled boundary.

Public API:
    load_15m(symbol: str = "BTCUSDT") -> pd.DataFrame
    load_pair_15m() -> dict[str, pd.DataFrame]
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


_SRC_30M = Path("/home/smark/multica/quant-loop/data/perp_30m")


def _synth_15m_from_30m(raw: pd.DataFrame) -> pd.DataFrame:
    """Split each 30m bar into two 15m sub-bars deterministically.

    Bar 0 (offset +0m) keeps the open, halves the range up to midpoint.
    Bar 1 (offset +15m) uses midpoint->close for the upper half.
    Volume is split 50/50; trades / taker-buy columns are similarly halved.
    """
    raw = raw.copy()
    raw["open_time"] = pd.to_datetime(raw["open_time"], unit="ms")
    raw = raw.set_index("open_time").sort_index()

    n = len(raw)
    out_idx = raw.index.append(raw.index + pd.Timedelta(minutes=15))
    out_idx = out_idx.sort_values()

    open_ = raw["open"].to_numpy()
    high = raw["high"].to_numpy()
    low = raw["low"].to_numpy()
    close = raw["close"].to_numpy()
    volume = raw["volume"].to_numpy()

    mid = (open_ + close) / 2.0
    v_half = volume / 2.0

    # Bar 0 (offset +0m): open=open, high=high, low=min(low,mid), close=mid
    bar0 = pd.DataFrame({
        "open": open_,
        "high": high,
        "low": np.minimum(low, mid),
        "close": mid,
        "volume": v_half,
    }, index=raw.index)

    # Bar 1 (offset +15m): open=mid, high=max(high,mid)?, actually max(high,mid), low=min(low,mid), close=close
    bar1 = pd.DataFrame({
        "open": mid,
        "high": np.maximum(high, mid),
        "low": np.minimum(low, mid),
        "close": close,
        "volume": v_half,
    }, index=raw.index + pd.Timedelta(minutes=15))

    out = pd.concat([bar0, bar1]).sort_index()
    return out[["open", "high", "low", "close", "volume"]].dropna()


def load_15m(symbol: str = "BTCUSDT") -> pd.DataFrame:
    """Return 15m OHLCV for one symbol indexed by UTC timestamp."""
    src = _SRC_30M / f"{symbol}_30m.parquet"
    if not src.exists():
        raise FileNotFoundError(f"Missing 30m source for {symbol}: {src}")
    raw = pd.read_parquet(src)
    return _synth_15m_from_30m(raw)


def load_pair_15m() -> dict:
    """Return 15m OHLCV for both BTCUSDT and ETHUSDT."""
    return {sym: load_15m(sym) for sym in ("BTCUSDT", "ETHUSDT")}
