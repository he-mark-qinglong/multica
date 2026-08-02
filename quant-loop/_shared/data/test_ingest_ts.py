"""Tests for _shared/data/ingest_ts.py (F-Data dual-timestamp)."""
from __future__ import annotations

import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import numpy as np
import pandas as pd

from _shared.data.ingest_ts import (
    INGEST_COL,
    has_ingest_ts,
    latency_ms,
    stamp_ingest_ts,
    stamp_parquet,
)


def _bars(n=10, bar_ms=60_000, start=0):
    ts = start + np.arange(n) * bar_ms
    return pd.DataFrame(
        {
            "timestamp": ts,
            "close": 100.0 + np.arange(n),
            "volume": np.ones(n),
        }
    )


def test_stamp_adds_column_with_constant_value():
    df = _bars(5)
    out = stamp_ingest_ts(df, now_ms=999_000)
    assert INGEST_COL in out.columns
    assert (out[INGEST_COL] == 999_000).all()
    # input not mutated
    assert INGEST_COL not in df.columns


def test_stamp_idempotent():
    df = _bars(5)
    once = stamp_ingest_ts(df, now_ms=100)
    twice = stamp_ingest_ts(once, now_ms=200)
    # second stamp must not overwrite the first
    assert (twice[INGEST_COL] == 100).all()


def test_stamp_empty_frame():
    df = pd.DataFrame(columns=["timestamp", "close"])
    out = stamp_ingest_ts(df, now_ms=500)
    assert INGEST_COL in out.columns
    assert len(out) == 0


def test_stamp_custom_column_name():
    df = _bars(3)
    out = stamp_ingest_ts(df, ingest_col="write_ts", now_ms=42)
    assert "write_ts" in out.columns
    assert (out["write_ts"] == 42).all()


def test_stamp_defaults_to_wallclock():
    df = _bars(2)
    out = stamp_ingest_ts(df)
    assert INGEST_COL in out.columns
    assert (out[INGEST_COL] > 1_600_000_000_000).all()  # after 2020


def test_has_ingest_ts():
    df = _bars(3)
    assert not has_ingest_ts(df)
    stamped = stamp_ingest_ts(df, now_ms=1)
    assert has_ingest_ts(stamped)
    assert not has_ingest_ts(df.iloc[:0])


def test_latency_ms_basic():
    df = _bars(3)
    df = stamp_ingest_ts(df, now_ms=5 * 60_000)  # ingest 5 min after epoch
    lat = latency_ms(df)
    assert len(lat) == 3
    # bar 0: ingest(5min) - ts(0) = 5min ; bar 2: ingest(5min) - ts(2min) = 3min
    assert lat.iloc[0] == 5 * 60_000
    assert lat.iloc[2] == 3 * 60_000


def test_latency_ms_missing_columns():
    df = _bars(3)
    # no ingest_ts column → empty
    assert len(latency_ms(df)) == 0
    stamped = stamp_ingest_ts(df, now_ms=1000)
    # wrong ts_col name → empty
    assert len(latency_ms(stamped, ts_col="nope")) == 0


def test_latency_ms_datetime_ts():
    df = _bars(3)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = stamp_ingest_ts(df, now_ms=3 * 60_000)
    lat = latency_ms(df)
    assert lat.iloc[0] == 3 * 60_000
    assert lat.iloc[2] == 1 * 60_000


def test_latency_negative_preserved():
    # clock skew: ingest stamped before exchange timestamp
    df = _bars(3)
    df = stamp_ingest_ts(df, now_ms=-1)
    lat = latency_ms(df)
    assert (lat < 0).all()  # not silently clipped


def test_stamp_parquet_backfill(tmp_path):
    df = _bars(5)
    p = tmp_path / "bars.parquet"
    df.to_parquet(p, index=False)
    n = stamp_parquet(p, now_ms=777)
    assert n == 5
    stored = pd.read_parquet(p)
    assert INGEST_COL in stored.columns
    assert (stored[INGEST_COL] == 777).all()


def test_stamp_parquet_already_stamped(tmp_path):
    df = stamp_ingest_ts(_bars(5), now_ms=10)
    p = tmp_path / "bars.parquet"
    df.to_parquet(p, index=False)
    n = stamp_parquet(p, now_ms=999)
    assert n == 0  # no-op
    stored = pd.read_parquet(p)
    assert (stored[INGEST_COL] == 10).all()


def test_stamp_parquet_missing_file(tmp_path):
    assert stamp_parquet(tmp_path / "nope.parquet") == 0
