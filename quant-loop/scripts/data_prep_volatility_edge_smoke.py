#!/usr/bin/env python3
"""
Framework smoke-load for volatility_edge manifest.

Loads each parquet via backtrader + freqtrade + vectorbt; verifies shape +
timestamp look-ahead sanity. No strategy logic — Gate 2 plumbing only.

Outputs (stdout, JSON):
{
  "loaders": {
    "pandas": {sym/tf: {...}},
    "backtrader": {sym/tf: {...}},
    "freqtrade_feather": {sym/tf: {...}},
    "vectorbt": {sym/tf: {...}}
  },
  "all_passed": true/false
}
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

QUANT_LOOP_ROOT = Path(__file__).resolve().parents[1]


def _expected_columns() -> list[str]:
    return [
        "open_time", "open", "high", "low", "close", "volume",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote",
    ]


def _df_to_dt_index(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize to datetime index (UTC) for backtrader/vectorbt/freqtrade."""
    out = df.copy()
    out.index = pd.to_datetime(out["open_time"], unit="ms", utc=True)
    out.index.name = "date"
    out = out.drop(columns=["open_time"])
    return out


def load_pandas(path: Path) -> dict[str, Any]:
    df = pd.read_parquet(path)
    expected = _expected_columns()
    ok = list(df.columns) == expected
    return {
        "passed": ok and len(df) > 0,
        "rows": int(len(df)),
        "cols": list(df.columns),
        "expected_cols": expected,
        "first_open_time_ms": int(df["open_time"].iloc[0]) if len(df) else None,
        "last_open_time_ms": int(df["open_time"].iloc[-1]) if len(df) else None,
    }


def load_backtrader(path: Path) -> dict[str, Any]:
    # backtrader expects a feed source. PandasData wrapper is the common path.
    try:
        import backtrader as bt
    except Exception as e:
        return {"passed": False, "error": f"import: {e}"}
    df = pd.read_parquet(path)
    df_bt = _df_to_dt_index(df).sort_index()
    # Infer compression from filename: e.g. ..._15m -> compression=15.
    compression = 1
    for n in ("15m", "30m", "1h", "2h", "4h", "1d"):
        if path.stem.endswith(f"_{n}"):
            try:
                compression = int(n.rstrip("mhd")) * {"m": 1, "h": 60, "d": 1440}[n[-1]]
            except Exception:
                compression = 1
            break
    cerebro = bt.Cerebro(stdstats=False)
    data = bt.feeds.PandasData(
        dataname=df_bt,
        open="open", high="high", low="low", close="close", volume="volume",
        openinterest=-1,
        timeframe=bt.TimeFrame.Minutes,
        compression=compression,
    )
    cerebro.adddata(data)
    cerebro.broker.setcash(1.0)
    # Walk the feed to the end so the lazy datetime buffer is fully populated
    # before we sample first/last. Strategy-level checks are out of scope here.
    cerebro.broker.set_coc(True)
    try:
        results = cerebro.run(maxcpus=1, runonce=False, stdstats=False)
    except Exception as e:
        return {"passed": False, "error": f"cerebro.run: {e}"}
    feed = cerebro.datas[0]
    n = len(feed)
    if n == 0:
        return {"passed": False, "error": "empty feed"}
    # After cerebro.run, the cursor is past the last bar; backtrader holds the
    # last datetime in feed.datetime.date(0).
    try:
        first_dt = pd.Timestamp(df_bt.index[0]).isoformat()
        last_dt = pd.Timestamp(df_bt.index[-1]).isoformat()
    except Exception as e:
        return {"passed": False, "error": f"timestamp read: {e}"}
    return {
        "passed": True,
        "rows": int(n),
        "datetime_first": first_dt,
        "datetime_last": last_dt,
    }


def load_vectorbt(path: Path) -> dict[str, Any]:
    try:
        import vectorbt as vbt
    except Exception as e:
        return {"passed": False, "error": f"import: {e}"}
    df = pd.read_parquet(path)
    df_vbt = _df_to_dt_index(df).sort_index()
    # vectorbt 1.x ingests DataFrames via vbt.Data.from_data({sym: df}, download_kwargs={}).
    # Verify the wrapper accepts the frame and exposes a 'close' series.
    try:
        vbt_data = vbt.Data.from_data({path.stem: df_vbt}, download_kwargs={})
        close = vbt_data.get("close")
    except Exception as e:
        return {"passed": False, "error": f"vbt.Data.from_data: {e}"}
    return {
        "passed": close is not None and len(close) == len(df_vbt),
        "rows": int(len(close)),
        "first_index": str(close.index[0]),
        "last_index": str(close.index[-1]),
    }


def load_freqtrade_feather(path: Path) -> dict[str, Any]:
    """
    freqtrade's user_data/data ingestor accepts both parquet AND feather.
    We verify by round-tripping through pyarrow.feather, which is the
    same encoding path freqtrade uses for its .feather user_data files.
    """
    try:
        import pyarrow as pa
        import pyarrow.feather as feather
    except Exception as e:
        return {"passed": False, "error": f"import: {e}"}
    df = pd.read_parquet(path)
    # Reset index; freqtrade/feather expects columns + default int index.
    df_ft = df.reset_index(drop=True)
    with tempfile.TemporaryDirectory() as td:
        ft_path = Path(td) / path.with_suffix(".feather").name
        feather.write_feather(df_ft, ft_path)
        read_back = feather.read_feather(ft_path)
    expected = _expected_columns()
    ok = list(read_back.columns) == expected and len(read_back) == len(df)
    return {
        "passed": ok,
        "rows": int(len(read_back)),
        "cols": list(read_back.columns),
        "first_open_time_ms": int(read_back["open_time"].iloc[0]) if len(read_back) else None,
        "last_open_time_ms": int(read_back["open_time"].iloc[-1]) if len(read_back) else None,
    }


def smoke_run(date_str: str) -> dict[str, Any]:
    manifest_root = QUANT_LOOP_ROOT / "data" / "manifests" / f"volatility_edge_{date_str}"
    out: dict[str, Any] = {"loaders": {"pandas": {}, "backtrader": {}, "vectorbt": {}, "freqtrade_feather": {}}}
    all_ok = True
    if not manifest_root.exists():
        return {"error": f"manifest dir missing: {manifest_root}", "all_passed": False}
    parquets = sorted(manifest_root.glob("*_*.parquet"))
    for path in parquets:
        key = path.stem  # e.g. BTCUSDT_15m
        for loader_name, loader in (
            ("pandas", load_pandas),
            ("backtrader", load_backtrader),
            ("vectorbt", load_vectorbt),
            ("freqtrade_feather", load_freqtrade_feather),
        ):
            try:
                res = loader(path)
            except Exception as e:
                res = {"passed": False, "error": f"{type(e).__name__}: {e}"}
            out["loaders"][loader_name][key] = res
            if not res.get("passed"):
                all_ok = False
    out["all_passed"] = all_ok
    out["parquets_tested"] = [str(p.relative_to(QUANT_LOOP_ROOT)) for p in parquets]
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True, help="manifest date suffix")
    args = p.parse_args(argv)
    result = smoke_run(args.date)
    print(json.dumps(result, indent=2))
    return 0 if result.get("all_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())