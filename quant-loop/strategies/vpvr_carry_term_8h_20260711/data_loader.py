"""Data loader for V8 (vpvr_carry_term_8h_20260711, iter#72).

Aggregates 1h Binance klines up to 8h bars, attaches real Binance 8h
funding rate (forward-filled to 8h timeline), and adds a synthetic
'alt-venue' funding proxy (lagged + scaled Binance funding) for the
cross-venue spread calculation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
LIVE_DATA = Path("/home/smark/multica/quant-loop/live_data")
FUNDING_DIR = Path("/home/smark/multica/quant-loop/funding_analysis")
DATA_DIR = ROOT / "data"
CFG_PATH = ROOT / "config.json"


@dataclass
class SourceManifest:
    inputs: Dict[str, str]
    outputs: Dict[str, str]


def _load_1h(symbol: str) -> pd.DataFrame:
    p = LIVE_DATA / f"{symbol}_1h.parquet"
    if not p.is_file():
        raise FileNotFoundError(f"missing 1h parquet: {p}")
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    return df[["open", "high", "low", "close", "volume"]].astype(np.float64)


def _load_funding_8h(symbol: str) -> pd.DataFrame:
    p = FUNDING_DIR / f"{symbol}_funding.parquet"
    if not p.is_file():
        return pd.DataFrame(columns=["fundingRate"])
    df = pd.read_parquet(p)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    return df[["fundingRate"]].astype(np.float64)


def _aggregate_8h(df_1h: pd.DataFrame, funding: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    o = df_1h["open"].resample("8h").first()
    h = df_1h["high"].resample("8h").max()
    l = df_1h["low"].resample("8h").min()
    c = df_1h["close"].resample("8h").last()
    v = df_1h["volume"].resample("8h").sum()
    out = pd.concat([o, h, l, c, v], axis=1)
    out.columns = ["open", "high", "low", "close", "volume"]
    out = out.dropna(subset=["close"])

    # Real Binance funding, snapped to the 8h timeline (funding occurs every 8h).
    if not funding.empty:
        binance_funding = funding["fundingRate"].resample("8h").last()
        # Use observed (not forward-fill) so each 8h bar carries the funding
        # rate that was charged at the *end* of that bar.
        binance_funding = binance_funding.reindex(out.index).ffill().fillna(0.0)
        out["fundingRate_binance"] = binance_funding.astype(np.float64)
    else:
        out["fundingRate_binance"] = 0.0

    # Synthetic alt-venue funding (lagged + scaled Binance funding).
    fp = cfg["funding_proxy"]
    out["fundingRate_alt"] = (
        out["fundingRate_binance"].shift(int(fp["alt_lag_bars"])) * float(fp["alt_scale"])
    ).fillna(0.0)
    out["funding_spread_bps"] = (
        (out["fundingRate_alt"] - out["fundingRate_binance"]) * 10000.0
    )
    return out


def load_symbol(symbol: str, refresh: bool = False) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache = DATA_DIR / f"{symbol}__8h.parquet"
    cfg = json.loads(CFG_PATH.read_text())
    if cache.is_file() and not refresh:
        return pd.read_parquet(cache)
    df_1h = _load_1h(symbol)
    funding = _load_funding_8h(symbol)
    out = _aggregate_8h(df_1h, funding, cfg)
    out.to_parquet(cache)
    return out


def load_all(symbols: Optional[Iterable[str]] = None) -> Dict[str, pd.DataFrame]:
    cfg = json.loads(CFG_PATH.read_text())
    if symbols is None:
        symbols = cfg["instruments"]
    return {s: load_symbol(s) for s in symbols}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_source_manifest() -> SourceManifest:
    inputs: Dict[str, str] = {}
    outputs: Dict[str, str] = {}
    for p in sorted(LIVE_DATA.glob("*_1h.parquet")):
        inputs[p.name] = _sha256(p)
    for p in sorted(FUNDING_DIR.glob("*_funding.parquet")):
        inputs[p.name] = _sha256(p)
    for p in sorted(DATA_DIR.glob("*__8h.parquet")):
        outputs[p.name] = _sha256(p)
    return SourceManifest(inputs=inputs, outputs=outputs)


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = load_all()
    for sym, d in data.items():
        non_zero_spread = int((d["funding_spread_bps"].abs() > 0.1).sum())
        print(
            f"{sym}: {len(d)} bars  {d.index[0]} -> {d.index[-1]}  "
            f"non_zero_spreads={non_zero_spread}  "
            f"spread_mean={d['funding_spread_bps'].mean():.3f}bps"
        )
    m = build_source_manifest()
    print(json.dumps(asdict(m), indent=2)[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())