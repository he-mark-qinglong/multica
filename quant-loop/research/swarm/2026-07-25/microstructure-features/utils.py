"""Shared helpers for the microstructure feasibility study."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
QUANT_LOOP = Path("/Users/mark/multica/quant-loop")
DATA_ROOT = QUANT_LOOP / "data"
OHLCV_ROOT = DATA_ROOT / "perp_1m"
FUNDING_ROOT = DATA_ROOT / "funding"
H3_DIR = QUANT_LOOP / "strategies" / "mtf_xs_pairs_1m_15m_2h_h3_20260718"

# Allow importing strategy base modules
sys.path.insert(0, str(QUANT_LOOP / "strategies"))
sys.path.insert(0, str(QUANT_LOOP / "strategies" / "_indicators"))
sys.path.insert(0, str(QUANT_LOOP))

from _shared.validation.compute_metrics import compute_metrics  # noqa: E402
from _shared.gates.enforce import certify_metrics  # noqa: E402


def load_ohlcv_shared(symbols: list, start: Optional[pd.Timestamp] = None,
                      end: Optional[pd.Timestamp] = None) -> Dict[str, pd.DataFrame]:
    """Load canonical 1m OHLCV parquets and set a tz-naive DatetimeIndex."""
    out = {}
    for sym in symbols:
        p = OHLCV_ROOT / f"{sym}_1m.parquet"
        df = pd.read_parquet(p)
        if "open_time" not in df.columns:
            raise ValueError(f"{p}: missing open_time")
        df.index = pd.DatetimeIndex(pd.to_datetime(df["open_time"], unit="ms", utc=True)).tz_convert(None)
        df.index.name = "openTime"
        df = df.sort_index()
        # drop artificial future bars if any (close == open == high == low == 10000)
        fake = (df["close"] == 10000.0) & (df["volume"] <= 0.001)
        df = df[~fake]
        if start is not None:
            df = df[df.index >= pd.to_datetime(start)]
        if end is not None:
            df = df[df.index <= pd.to_datetime(end)]
        out[sym] = df
    return out


def load_funding_shared(symbols: list, start: Optional[pd.Timestamp] = None,
                        end: Optional[pd.Timestamp] = None) -> Dict[str, pd.Series]:
    """Load funding-rate parquets and return Series indexed by event ts."""
    out = {}
    for sym in symbols:
        # try parquet first, then csv
        p = FUNDING_ROOT / f"{sym}.parquet"
        if p.is_file():
            df = pd.read_parquet(p)
        else:
            csv = FUNDING_ROOT / f"{sym}.csv"
            df = pd.read_csv(csv)
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(None)
        if start is not None:
            df = df[df["ts"] >= pd.to_datetime(start)]
        if end is not None:
            df = df[df["ts"] <= pd.to_datetime(end)]
        s = pd.Series(df["fundingRate"].astype(float).to_numpy(),
                      index=pd.DatetimeIndex(df["ts"]), name="fundingRate")
        s = s.sort_index()
        s.index.name = "fundingTime"
        out[sym] = s
    return out


def load_h3_config() -> dict:
    """Load the H3 BTC+SOL config."""
    cfg_path = H3_DIR / "config_btcsol.json"
    return json.loads(cfg_path.read_text())


def daily_equity_from_bar_return(bar_return: np.ndarray, index: pd.DatetimeIndex,
                                 starting_capital: float = 100_000.0) -> pd.Series:
    """Build an equity curve from per-bar returns, then resample to daily."""
    eq = np.empty(len(bar_return))
    eq[0] = starting_capital
    for i in range(1, len(bar_return)):
        eq[i] = eq[i - 1] * (1.0 + bar_return[i])
    eq_s = pd.Series(eq, index=index)
    daily_eq = eq_s.resample("1D").last().dropna()
    return daily_eq


def evaluate_metrics(equity_daily: pd.Series, n_trades: int,
                     trade_pnls: Optional[np.ndarray] = None) -> dict:
    """Compute the 9-key metrics on daily equity and certify G1-G7/T1."""
    m = compute_metrics(equity_daily, n_trades=n_trades,
                        freq_per_year=365, trade_pnls=trade_pnls)
    cert = certify_metrics(m, strict=False)
    m["passed_all_gates"] = cert.passed
    m["failed_gates"] = cert.failed_gates
    m["gate_reasons"] = cert.reasons
    return m


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=float))
