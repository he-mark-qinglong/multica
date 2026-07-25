"""Data loader for vpvr_edge_zscore_multi_tf (SMA-34991).

Loads per-symbol OHLCV bars for the three TFs required by the spec:

  - 1m  : ``data/perp_1m/{BTC,ETH,SOL}USDT_1m.parquet``  (shared pool)
  - 15m : ``live_data/{BTC,ETH,SOL}USDT_15m.parquet``     (shared pool)
  - 2h  : ``data/perp_2h/{BTC,ETH,SOL}USDT_2h.parquet``   (resampled 2026-07-19)

SOL 1m / 15m are intentionally absent on disk (per the SMA-34869 audit
close as of 2026-07-18T22:59+08 the SOL leg was data-blocked). This
loader falls back to BTC + ETH when SOL files are missing and reports
the set actually loaded — never silently.

Funding is NOT attached: the spec is OHLCV-only (volume-profile zscore
on the price distribution, not funding-carry-asym).

No-look-ahead invariant: rolling baselines are shifted by 1 bar,
snapshot grids are shifted by 1, the 15m EMA(20) and POC-slope are
shifted(1) before any threshold comparison.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

try:
    from _shared.paths import quant_loop_root
except ImportError:  # bare-script mode
    _QL = str(Path(__file__).resolve().parents[2])
    if _QL not in sys.path:
        sys.path.insert(0, _QL)
    from _shared.paths import quant_loop_root

QUANT_LOOP = quant_loop_root()
LIVE_DATA = QUANT_LOOP / "live_data"
PERP_1M = QUANT_LOOP / "data" / "perp_1m"
PERP_2H = QUANT_LOOP / "data" / "perp_2h"

DATA_DIR = Path(__file__).resolve().parent / "data"

# Symbols whose 1m and 15m OHLCV is confirmed on disk per the SMA-34869
# audit + the 2026-07-19 resample. SOL leg is data-blocked (1m/15m
# missing) and is intentionally NOT in the default requested set; callers
# who explicitly request SOL will get a loud FileNotFoundError rather
# than a silent skip.
CONFIRMED_SYMBOLS_1M_15M = ["BTCUSDT", "ETHUSDT"]


def _load_ohlcv(path: Path) -> pd.DataFrame:
    """Load a per-TF OHLCV parquet and normalize the index/columns.

    The shared-pool parquets use ``open_time`` (ms epoch) for the
    timestamp; some legacy copies use ``ts``. We coerce to a tz-naive
    ``DatetimeIndex`` (the resampled 2h pool is already UTC-aligned
    but tz-naive, matching the live_data convention) and keep only
    OHLCV columns.
    """
    if not path.exists():
        raise FileNotFoundError(f"OHLCV parquet not found: {path}")
    df = pd.read_parquet(path)
    if "open_time" in df.columns:
        df["__ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=False)
        df = df.set_index("__ts").drop(columns=["open_time"])
    elif "ts" in df.columns:
        df["__ts"] = pd.to_datetime(df["ts"], utc=True)
        df = df.set_index("__ts").drop(columns=["ts"])
    df = df.sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    if not keep:
        raise ValueError(f"no OHLCV columns in {path}")
    return df[keep].astype(np.float64)


def load_tf(symbol: str, tf: str) -> pd.DataFrame:
    """Load a single (symbol, timeframe) OHLCV frame. No funding column.

    Args:
        symbol: one of ``BTCUSDT``, ``ETHUSDT``, ``SOLUSDT`` — but
                SOL will fail for 1m / 15m because the shared-pool
                parquets are missing (per SMA-34869 audit).
        tf: one of ``"1m"``, ``"15m"``, ``"2h"``.

    Raises:
        FileNotFoundError: if the requested (symbol, tf) parquet is
            missing — the spec forbids silent fallback.
    """
    if tf == "1m":
        path = PERP_1M / f"{symbol}_1m.parquet"
    elif tf == "15m":
        path = LIVE_DATA / f"{symbol}_15m.parquet"
    elif tf == "2h":
        path = PERP_2H / f"{symbol}_2h.parquet"
    else:
        raise ValueError(f"unsupported tf {tf!r} (expected 1m/15m/2h)")

    return _load_ohlcv(path)


def load_all(
    symbols: List[str],
    tfs: List[str],
    *,
    allow_missing: bool = False,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    """Load all (symbol, tf) combinations.

    Returns a nested dict ``{symbol: {tf: df}}``. If ``allow_missing``
    is ``False`` (default), a missing parquet raises immediately. If
    ``True``, missing entries are omitted from the result and logged
    in the ``DATA_DIR/manifest.txt`` summary.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Dict[str, pd.DataFrame]] = {}
    manifest_lines: List[str] = []
    for sym in symbols:
        out[sym] = {}
        for tf in tfs:
            try:
                df = load_tf(sym, tf)
                out[sym][tf] = df
                manifest_lines.append(
                    f"OK\t{sym}\t{tf}\t{len(df)}\t{df.index.min()}\t{df.index.max()}"
                )
            except FileNotFoundError as exc:
                msg = f"MISSING\t{sym}\t{tf}\t{exc}"
                manifest_lines.append(msg)
                if not allow_missing:
                    raise
                # else: omit and continue
    (DATA_DIR / "manifest.txt").write_text("\n".join(manifest_lines) + "\n")
    return out


def default_symbols() -> List[str]:
    """Default symbol set: BTC + ETH. SOL leg is intentionally excluded
    per the SMA-34869 data-block close as of 2026-07-19 — see issue
    body for the SOLUSDT 1m/15m gap.
    """
    return list(CONFIRMED_SYMBOLS_1M_15M)


__all__ = [
    "QUANT_LOOP",
    "DATA_DIR",
    "CONFIRMED_SYMBOLS_1M_15M",
    "load_tf",
    "load_all",
    "default_symbols",
]