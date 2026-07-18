"""Data loader for vpvr_funding_aware_v1_20260711 (iter#82, 4h, Rule A rev2).

Reads pre-built 4h OHLCV parquets and 8h funding-event parquets from
`data/`, joins funding onto the 4h timeline via forward-fill, and exposes
helpers for funding-sum-24h, funding-vol regime and CarryLedger inputs.

Public API: load_symbol(symbol), load_all(symbols=None)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CFG_PATH = ROOT / "config.json"


def _load_4h(symbol: str) -> pd.DataFrame:
    p = DATA_DIR / f"{symbol}__4h.parquet"
    if not p.is_file():
        raise FileNotFoundError(f"missing 4h parquet: {p}")
    df = pd.read_parquet(p).sort_index()
    return df.astype({c: np.float64 for c in ["open", "high", "low", "close", "volume"]})


def _load_funding(symbol: str) -> pd.DataFrame:
    p = DATA_DIR / f"{symbol}__funding.parquet"
    if not p.is_file():
        raise FileNotFoundError(f"missing funding parquet: {p}")
    f = pd.read_parquet(p)
    f["ts"] = pd.to_datetime(f["ts"], utc=True)
    f = f.set_index("ts").sort_index()
    return f[["fundingRate", "funding_bps"]].astype(np.float64)


def load_symbol(symbol: str) -> pd.DataFrame:
    """Load a symbol's 4h OHLCV and merge funding onto the 4h timeline.

    The resulting frame has columns: open/high/low/close/volume plus
    `fundingRate`, `funding_bps` (forward-filled from the 8h funding
    events; bps scaled by 10000 to match the bps definition).
    """
    df = _load_4h(symbol)
    funding = _load_funding(symbol)

    # Forward-fill funding onto the 4h timeline. Funding events occur every 8h
    # but the column on the 4h timeline is the most recently observed funding
    # rate as of that 4h bar's open. Funding at bar t will be charged to a
    # position held during bar[t+1] (carry is realised in the bar after).
    fund_ff = funding["fundingRate"].reindex(df.index, method="ffill").fillna(0.0)
    fund_bps_ff = funding["funding_bps"].reindex(df.index, method="ffill").fillna(0.0)
    out = df.copy()
    out["fundingRate"] = fund_ff
    out["funding_bps"] = fund_bps_ff
    return out


def load_all(symbols: Optional[Iterable[str]] = None) -> Dict[str, pd.DataFrame]:
    cfg = json.loads(CFG_PATH.read_text())
    if symbols is None:
        symbols = cfg["instruments"]
    return {s: load_symbol(s) for s in symbols}


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for sym, df in load_all().items():
        nonzero_fund = int((df["funding_bps"].abs() > 0.01).sum())
        print(
            f"{sym}: {len(df)} bars  {df.index[0]} -> {df.index[-1]}  "
            f"nonzero_funding_bars={nonzero_fund}  "
            f"funding_bps_mean={df['funding_bps'].mean():.3f}  "
            f"funding_bps_std={df['funding_bps'].std():.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
