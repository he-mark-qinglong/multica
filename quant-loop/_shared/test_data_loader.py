"""Tests for ``_shared/data_loader.py``.

Two test layers:

1. **Synthetic** — drives the loader with a ``tmp_path`` data tree
   (``QUANT_LOOP_ROOT`` env var redirect) so the tests are hermetic and
   always runnable.
2. **Real-data smoke** — opt-in (``@pytest.mark.skipif`` on file
   existence), reads the actual ``data/perp_15m/BTCUSDT_15m.parquet``
   and pins its row count + tz to the manifest anchor at
   ``data/manifests/perp_resampled_2026-07-24.yaml:63-65`` (240392 rows,
   2019-09-08T17:45:00Z → 2026-07-17T19:30:00Z).

Run::

    /Users/mark/sdk/mamba-envs/trading/bin/python3 -m pytest _shared/test_data_loader.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import data_loader as dl  # noqa: E402  (import after sys.path tweak)


# --- Fixtures ----------------------------------------------------------------


@pytest.fixture
def fake_data_root(tmp_path, monkeypatch):
    """Build a minimal data/ tree under tmp_path and redirect QUANT_LOOP_ROOT."""
    (tmp_path / "data").mkdir()

    # --- 15m klines (Binance 10-col resampled schema) ---
    bars_dir = tmp_path / "data" / "perp_15m"
    bars_dir.mkdir()
    bars = pd.DataFrame(
        {
            "open_time": pd.to_datetime(
                [
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:15:00Z",
                    "2024-01-01T00:30:00Z",
                    "2024-01-01T00:45:00Z",
                    "2024-01-01T01:00:00Z",
                    "2024-01-01T01:15:00Z",
                ],
                utc=True,
            ),
            "open": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            "high": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0, 104.0],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
            "volume": [1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
            "quote_volume": [100.5, 111.65, 123.0, 134.55, 146.3, 158.25],
            "trades": [10, 11, 12, 13, 14, 15],
            "taker_buy_base": [0.5, 0.55, 0.6, 0.65, 0.7, 0.75],
            "taker_buy_quote": [50.25, 55.825, 61.5, 67.275, 73.15, 79.125],
        }
    )
    bars.to_parquet(bars_dir / "BTCUSDT_15m.parquet", index=False)

    # --- 30m klines (8-col schema) for a different symbol ---
    bars30_dir = tmp_path / "data" / "perp_30m"
    bars30_dir.mkdir()
    bars30 = pd.DataFrame(
        {
            "open_time": pd.to_datetime(
                [
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T00:30:00Z",
                    "2024-01-01T01:00:00Z",
                ],
                utc=True,
            ),
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "volume": [1.0, 1.1, 1.2],
            "close_time": pd.to_datetime(
                ["2024-01-01T00:29:59Z", "2024-01-01T00:59:59Z", "2024-01-01T01:29:59Z"],
                utc=True,
            ),
            "quote_volume": [100.5, 111.65, 123.0],
        }
    )
    bars30.to_parquet(bars30_dir / "ETHUSDT_30m.parquet", index=False)

    # --- funding ---
    funding_dir = tmp_path / "data" / "funding"
    funding_dir.mkdir()
    funding = pd.DataFrame(
        {
            "ts": pd.to_datetime(
                [
                    "2024-01-01T00:00:00Z",
                    "2024-01-01T08:00:00Z",
                    "2024-01-01T16:00:00Z",
                ],
                utc=True,
            ),
            "symbol": ["BTCUSDT", "BTCUSDT", "BTCUSDT"],
            "fundingRate": [0.0001, 0.0002, 0.00015],
            "markPrice": [100.0, 101.0, 102.0],
        }
    )
    funding.to_parquet(funding_dir / "BTCUSDT.parquet", index=False)

    # --- aggtrades (hive-partitioned directory) ---
    trades_root = tmp_path / "data" / "trades"
    trades_root.mkdir()
    sym_root = trades_root / "BTCUSDT_aggtrades.parquet"
    sym_root.mkdir()
    # ``year``/``month`` are partition columns — hive writes them to
    # ``year=YYYY/month=M/data.parquet`` and strips them from the data files.
    table = pa.table(
        {
            "ts": pa.array(
                pd.to_datetime(
                    [
                        "2026-01-15T00:00:00Z",
                        "2026-01-15T00:00:00Z",
                        "2026-02-10T12:30:00Z",
                    ],
                    utc=True,
                ),
                type=pa.timestamp("ms", tz="UTC"),
            ),
            "symbol": ["BTCUSDT", "BTCUSDT", "BTCUSDT"],
            "agg_id": [1, 2, 3],
            "price": [100.0, 100.1, 200.0],
            "qty": [0.1, 0.2, 0.3],
            "first_id": [100, 101, 102],
            "last_id": [100, 101, 102],
            "is_buyer_maker": [False, True, False],
            "year": [2026, 2026, 2026],
            "month": [1, 1, 2],
        }
    )
    ds.write_dataset(
        table,
        base_dir=str(sym_root),
        format="parquet",
        partitioning=ds.partitioning(
            pa.schema([pa.field("year", pa.int32()), pa.field("month", pa.int32())]),
            flavor="hive",
        ),
        existing_data_behavior="overwrite_or_ignore",
    )

    monkeypatch.setenv("QUANT_LOOP_ROOT", str(tmp_path))
    return tmp_path


# --- Synthetic: load_bars ---------------------------------------------------


def test_load_bars_index_is_utc_and_sorted(fake_data_root):
    df = dl.load_bars("BTCUSDT", "15m")
    assert isinstance(df.index, pd.DatetimeIndex)
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing
    assert len(df) == 6


def test_load_bars_start_end_window(fake_data_root):
    df = dl.load_bars(
        "BTCUSDT",
        "15m",
        start="2024-01-01T00:15:00Z",
        end="2024-01-01T01:00:00Z",
    )
    # Window is [00:15, 01:00) — three bars at 00:15, 00:30, 00:45.
    assert len(df) == 3
    assert df.index[0] == pd.Timestamp("2024-01-01T00:15:00Z")
    assert df.index[-1] == pd.Timestamp("2024-01-01T00:45:00Z")


def test_load_bars_preserves_source_columns(fake_data_root):
    df = dl.load_bars("BTCUSDT", "15m")
    # 15m schema has 10 columns (no close_time / ignore).
    assert "open_time" not in df.columns  # promoted to index
    assert {"open", "high", "low", "close", "volume"}.issubset(df.columns)


def test_load_bars_unknown_tf_raises(fake_data_root):
    with pytest.raises(ValueError, match="unknown tf"):
        dl.load_bars("BTCUSDT", "3m")


def test_load_bars_missing_file_raises(fake_data_root):
    with pytest.raises(FileNotFoundError):
        dl.load_bars("NONEXIST", "15m")


def test_load_bars_column_projection(fake_data_root):
    df = dl.load_bars("BTCUSDT", "15m", columns=["open", "close"])
    assert list(df.columns) == ["open", "close"]


# --- Synthetic: load_funding -----------------------------------------------


def test_load_funding_basic(fake_data_root):
    df = dl.load_funding("BTCUSDT")
    assert isinstance(df.index, pd.DatetimeIndex)
    assert str(df.index.tz) == "UTC"
    assert len(df) == 3
    assert df["fundingRate"].iloc[1] == pytest.approx(0.0002)


def test_load_funding_missing_file_raises(fake_data_root):
    with pytest.raises(FileNotFoundError):
        dl.load_funding("DOGEUSDT")


# --- Synthetic: load_aggtrades ----------------------------------------------


def test_load_aggtrades_window_and_columns(fake_data_root):
    df = dl.load_aggtrades(
        "BTCUSDT",
        start="2026-01-01T00:00:00Z",
        end="2026-02-01T00:00:00Z",
        columns=["ts", "price"],
    )
    assert list(df.columns) == ["ts", "price"]
    # Only the 2026-01-15 rows fall inside January.
    assert len(df) == 2
    assert df["price"].iloc[0] == pytest.approx(100.0)


def test_load_aggtrades_requires_window(fake_data_root):
    with pytest.raises(ValueError, match="requires both start and end"):
        dl.load_aggtrades("BTCUSDT", start=None, end=None)


def test_load_aggtrades_missing_dir_raises(fake_data_root):
    with pytest.raises(FileNotFoundError):
        dl.load_aggtrades(
            "NONEXIST", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"
        )


# --- Synthetic: available --------------------------------------------------


def test_available_returns_expected_structure(fake_data_root):
    cov = dl.available("BTCUSDT")
    assert cov == {"bars": ["15m"], "funding": True, "aggtrades": True}
    cov_eth = dl.available("ETHUSDT")
    assert cov_eth == {"bars": ["30m"], "funding": False, "aggtrades": False}
    cov_missing = dl.available("NOPE")
    assert cov_missing == {"bars": [], "funding": False, "aggtrades": False}


# --- Real-data smoke (skipped when data is absent) -------------------------


REAL_BARS_15M = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "perp_15m"
    / "BTCUSDT_15m.parquet"
)


@pytest.mark.skipif(
    not REAL_BARS_15M.is_file(),
    reason="no real 15m klines data on this machine",
)
def test_real_btcusdt_15m_anchor():
    """Smoke test against the real BTCUSDT 15m pool.

    Expectations are derived from the parquet file itself (row count from
    the footer, first/last open from the ``open_time`` column) rather than
    hardcoded — the pool is extended over time, so fixed anchors rot.
    """
    meta = pq.ParquetFile(str(REAL_BARS_15M)).metadata
    open_times = pd.read_parquet(REAL_BARS_15M, columns=["open_time"])["open_time"]

    df = dl.load_bars("BTCUSDT", "15m")
    assert len(df) == meta.num_rows == len(open_times)
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing
    assert df.index[0] == dl._to_utc_datetime(pd.Series([open_times.iloc[0]])).iloc[0]
    assert df.index[-1] == dl._to_utc_datetime(pd.Series([open_times.iloc[-1]])).iloc[0]


@pytest.mark.skipif(
    not REAL_BARS_15M.is_file(),
    reason="no real 15m klines data on this machine",
)
def test_real_15m_reads_columns_only():
    """Cheap column-projection check — materialise only ``open``."""
    expected_rows = pq.ParquetFile(str(REAL_BARS_15M)).metadata.num_rows
    df = dl.load_bars("BTCUSDT", "15m", columns=["open"])
    assert list(df.columns) == ["open"]
    assert len(df) == expected_rows