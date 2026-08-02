"""Tests for _shared/data/api.py (F20) — synthetic datasets under tmp_path."""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pandas as pd
import pytest

from _shared.data import api


def _build_root(tmp_path):
    root = tmp_path / "data"
    # perp klines (int-ms schema like data/perp_1m/)
    (root / "perp_1m").mkdir(parents=True)
    pd.DataFrame(
        {
            "open_time": [0, 60_000, 120_000],
            "open": [1.0, 2.0, 3.0], "high": [1.5, 2.5, 3.5],
            "low": [0.5, 1.5, 2.5], "close": [1.2, 2.2, 3.2],
            "volume": [10.0, 11.0, 12.0], "close_time": [59_999, 119_999, 179_999],
            "quote_volume": [100.0, 110.0, 120.0], "trades": [5, 6, 7],
            "taker_buy_base": [1.0, 1.0, 1.0], "taker_buy_quote": [10.0, 10.0, 10.0],
            "ignore": [0, 0, 0],
        }
    ).to_parquet(root / "perp_1m" / "BTCUSDT_1m.parquet", index=False)

    # funding (datetime schema)
    (root / "funding").mkdir()
    pd.DataFrame(
        {
            "ts": pd.to_datetime([0, 8 * 3_600_000], unit="ms", utc=True),
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "fundingRate": [0.0001, -0.0002],
            "markPrice": [97_000.0, 97_100.0],
        }
    ).to_parquet(root / "funding" / "BTCUSDT.parquet", index=False)

    # oi / ls_ratio (int-ms, F7/F8 canonical schema)
    (root / "oi").mkdir()
    pd.DataFrame(
        {
            "timestamp": [0, 3_600_000], "symbol": ["BTCUSDT", "BTCUSDT"],
            "open_interest": [1000.0, 1001.0],
            "open_interest_value": [97e6, 97.1e6],
        }
    ).to_parquet(root / "oi" / "BTCUSDT.parquet", index=False)
    (root / "ls_ratio").mkdir()
    pd.DataFrame(
        {
            "timestamp": [0, 3_600_000], "symbol": ["BTCUSDT", "BTCUSDT"],
            "long_short_ratio": [1.5, 1.6],
            "long_account": [0.6, 0.62], "short_account": [0.4, 0.38],
        }
    ).to_parquet(root / "ls_ratio" / "BTCUSDT.parquet", index=False)

    # liquidations (consolidated parquet)
    (root / "liquidations").mkdir()
    pd.DataFrame(
        {
            "timestamp": [500, 1_500], "symbol": ["BTCUSDT", "BTCUSDT"],
            "side": ["SELL", "BUY"], "price": [97_000.0, 96_900.0],
            "qty": [0.1, 0.2], "usd_value": [9_700.0, 19_380.0],
        }
    ).to_parquet(root / "liquidations" / "BTCUSDT.parquet", index=False)

    # trades (hive-partitioned dataset like data/trades/)
    trades = pd.DataFrame(
        {
            "ts": pd.to_datetime([0, 60_000], unit="ms", utc=True),
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "agg_id": [1, 2], "price": [97_000.0, 97_001.0],
            "qty": [0.1, 0.2], "first_id": [1, 2], "last_id": [1, 2],
            "is_buyer_maker": [False, True],
            "year": [1970, 1970], "month": [1, 1],
        }
    )
    trades.to_parquet(root / "trades" / "BTCUSDT_aggtrades.parquet",
                      partition_cols=["year", "month"], index=False)
    return root


def test_get_klines_canonical_schema_and_slice(tmp_path):
    root = _build_root(tmp_path)
    df = api.get_klines("BTCUSDT", data_root=root)
    assert list(df.columns) == [
        "timestamp", "open", "high", "low", "close", "volume", "quote_volume", "trades",
    ]
    assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])
    assert df["timestamp"].dt.tz is not None  # tz-aware UTC
    assert df["timestamp"].is_monotonic_increasing
    sliced = api.get_klines("BTCUSDT", start=60_000, end=120_000, data_root=root)
    assert len(sliced) == 1
    assert sliced.iloc[0]["open"] == 2.0


def test_get_klines_string_bounds(tmp_path):
    root = _build_root(tmp_path)
    df = api.get_klines("BTCUSDT", start="1970-01-01T00:00:01Z", data_root=root)
    assert len(df) == 2  # ts 0 excluded


def test_get_trades(tmp_path):
    root = _build_root(tmp_path)
    df = api.get_trades("BTCUSDT", data_root=root)
    assert list(df.columns) == ["timestamp", "price", "qty", "is_buyer_maker", "agg_id"]
    assert len(df) == 2
    assert df.iloc[1]["is_buyer_maker"] == True  # noqa: E712


def test_get_funding(tmp_path):
    root = _build_root(tmp_path)
    df = api.get_funding("BTCUSDT", data_root=root)
    assert list(df.columns) == ["timestamp", "funding_rate", "mark_price"]
    assert df["funding_rate"].tolist() == [0.0001, -0.0002]


def test_get_oi_and_ls_ratio(tmp_path):
    root = _build_root(tmp_path)
    oi = api.get_oi("BTCUSDT", data_root=root)
    assert list(oi.columns) == ["timestamp", "open_interest", "open_interest_value"]
    assert len(oi) == 2
    ls = api.get_ls_ratio("BTCUSDT", start=3_600_000, data_root=root)
    assert len(ls) == 1
    assert ls.iloc[0]["long_short_ratio"] == 1.6


def test_get_ls_ratio_returns_ascending_even_if_store_unsorted(tmp_path):
    """The API contract promises ascending timestamps regardless of store order."""
    root = tmp_path / "data"
    (root / "ls_ratio").mkdir(parents=True)
    pd.DataFrame(
        {
            "timestamp": [7_200_000, 0, 3_600_000],  # deliberately unsorted
            "symbol": ["BTCUSDT"] * 3,
            "long_short_ratio": [1.7, 1.5, 1.6],
            "long_account": [0.63, 0.6, 0.62],
            "short_account": [0.37, 0.4, 0.38],
        }
    ).to_parquet(root / "ls_ratio" / "BTCUSDT.parquet", index=False)
    ls = api.get_ls_ratio("BTCUSDT", data_root=root)
    assert ls["timestamp"].is_monotonic_increasing
    assert ls["long_short_ratio"].tolist() == [1.5, 1.6, 1.7]


def test_get_liquidations_parquet_and_jsonl_fallback(tmp_path):
    root = _build_root(tmp_path)
    df = api.get_liquidations("BTCUSDT", data_root=root)
    assert list(df.columns) == ["timestamp", "symbol", "side", "price", "qty", "usd_value"]
    assert len(df) == 2

    # remove the parquet, leave only raw jsonl -> loader fallback
    (root / "liquidations" / "BTCUSDT.parquet").unlink()
    line = json.dumps({"ts": 700, "symbol": "BTCUSDT", "side": "SELL",
                       "price": "97000", "qty": "0.05"})
    (root / "liquidations" / "BTCUSDT.jsonl").write_text(line + "\n")
    df2 = api.get_liquidations("BTCUSDT", data_root=root)
    assert len(df2) == 1
    assert df2.iloc[0]["usd_value"] == 97_000.0 * 0.05


def test_venue_validation(tmp_path):
    root = _build_root(tmp_path)
    api.get_klines("BTCUSDT", venue="binance", data_root=root)  # ok
    with pytest.raises(ValueError):
        api.get_klines("BTCUSDT", venue="bybit", data_root=root)


def test_missing_dataset_raises(tmp_path):
    root = _build_root(tmp_path)
    with pytest.raises(FileNotFoundError):
        api.get_oi("SOLUSDT", data_root=root)
    with pytest.raises(FileNotFoundError):
        api.get_liquidations("SOLUSDT", data_root=root)
