#!/usr/bin/env python3
"""
Unit Tests for Open Interest History Backfiller
===============================================

Tests for ``live_data/open_interest_history/`` (this package). All tests
are pure: they exercise the chunking, formatting, persistence, and merge
logic without touching the network. Network-touching helpers
(``OIBackfiller.backfill``, ``_fetch_window``) are monkey-patched via
fixtures.

Migrated from ``trading/tests/unit/test_open_interest_history.py`` at
``da0020de89575c0694b5763c0628a486612d6256`` (the trading repo is
archived; this is the canonical home going forward).

Coverage:
  * Constants          — periods / seconds / max rows / chunk math
  * parse_timestamp    — ms / sec / ISO / datetime / None / bad input
  * windowed_iter      — boundaries, ordering, single-window short-circuit
  * OpenInterestDataManager — round-trip parquet, missing file, overwrite
  * OIBackfiller.format_symbol — binance / okx / edge cases
  * OIBackfiller._to_dataframe / _merge — normalization + dedupe
  * OIBackfiller.backfill  — monkey-patched network path (verifies paging)

Run with:
    pytest live_data/open_interest_history/tests/test_open_interest_history.py -v

All 55 pass without network access.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Load the OI package hermetically. We treat the package as a standalone
# import (under its canonical name ``live_data.open_interest_history``) by
# inserting the directory that *contains* ``live_data/`` into sys.path.
# This keeps tests independent of any workspace-level install / conftest
# autouse fixtures.
# ---------------------------------------------------------------------------
# Test file:  live_data/open_interest_history/tests/test_open_interest_history.py
# Package:    live_data/open_interest_history/
# Parent of "live_data/": quant-loop/
_QUANT_LOOP_ROOT = Path(__file__).resolve().parents[3]
if str(_QUANT_LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(_QUANT_LOOP_ROOT))

import live_data.open_interest_history as oih  # noqa: E402

OIBackfiller = oih.OIBackfiller
OpenInterestDataManager = oih.OpenInterestDataManager
SUPPORTED_PERIODS = oih.SUPPORTED_PERIODS
PERIOD_SECONDS = oih.PERIOD_SECONDS
MAX_ROWS_PER_CALL = oih.MAX_ROWS_PER_CALL
parse_timestamp = oih.parse_timestamp
chunk_seconds_for_period = oih.chunk_seconds_for_period
windowed_iter = oih.windowed_iter
Window = oih.Window


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def tmp_manager(tmp_path):
    """Fresh OpenInterestDataManager rooted in tmp_path."""
    return OpenInterestDataManager(base_path=str(tmp_path))


@pytest.fixture
def fake_ccxt_rows():
    """Three rows of ccxt-style OI data, all in 2023-11-14."""
    base = 1_700_000_000_000
    return [
        {"timestamp": base,            "symbol": "BTC/USDT:USDT",
         "sumOpenInterest": "100.0",   "sumOpenInterestValue": "5000000",
         "countOpenInterest": 12345},
        {"timestamp": base + 300_000,  "symbol": "BTC/USDT:USDT",
         "sumOpenInterest": "101.5",   "sumOpenInterestValue": "5100000",
         "countOpenInterest": 12400},
        {"timestamp": base + 600_000,  "symbol": "BTC/USDT:USDT",
         "sumOpenInterest": "103.2",   "sumOpenInterestValue": "5200000",
         "countOpenInterest": 12500},
    ]


@pytest.fixture
def make_fake_rows():
    """Factory: build N ccxt-shaped rows spaced by ``step_ms`` starting at ``since_ms``."""

    def _make(since_ms: int, count: int, step_ms: int = 5 * 60 * 1000) -> List[dict]:
        return [
            {
                "timestamp": since_ms + i * step_ms,
                "symbol": "BTC/USDT:USDT",
                "sumOpenInterest": str(100.0 + i),
                "sumOpenInterestValue": str(5_000_000 + i * 1000),
                "countOpenInterest": 12345 + i,
            }
            for i in range(count)
        ]

    return _make


@pytest.fixture
def loader_binance():
    return OIBackfiller("binance")


@pytest.fixture
def loader_okx():
    return OIBackfiller("okx")


# =============================================================================
# Constants
# =============================================================================

class TestConstants:
    def test_supported_periods_is_canonical(self):
        assert SUPPORTED_PERIODS == (
            "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d",
        )

    def test_period_seconds_known_periods(self):
        assert PERIOD_SECONDS["5m"] == 5 * 60
        assert PERIOD_SECONDS["1h"] == 60 * 60
        assert PERIOD_SECONDS["1d"] == 24 * 60 * 60

    def test_max_rows_per_call_is_500(self):
        assert MAX_ROWS_PER_CALL == 500

    def test_chunk_seconds_within_upper_bound(self):
        for period in SUPPORTED_PERIODS:
            cs = chunk_seconds_for_period(period)
            assert 0 < cs <= oih.LOOKBACK_UPPER_BOUND_SECONDS[period]

    def test_chunk_seconds_safety_ratio(self):
        # With ratio=1.0 the chunks should hit the min(upper, 500*period)
        cs_no_safety = chunk_seconds_for_period("5m", safety_ratio=1.0)
        cs_safety = chunk_seconds_for_period("5m")
        assert cs_safety < cs_no_safety
        assert cs_safety == int(cs_no_safety * oih.CHUNK_SAFETY_RATIO)

    def test_chunk_seconds_rejects_bad_ratio(self):
        with pytest.raises(ValueError):
            chunk_seconds_for_period("5m", safety_ratio=0)
        with pytest.raises(ValueError):
            chunk_seconds_for_period("5m", safety_ratio=1.5)


# =============================================================================
# parse_timestamp
# =============================================================================

class TestParseTimestamp:

    def test_none_returns_none(self):
        assert parse_timestamp(None) is None

    def test_ms_int_passes_through(self):
        assert parse_timestamp(1_700_000_000_000) == 1_700_000_000_000

    def test_ms_float_passes_through(self):
        assert parse_timestamp(float(1_700_000_000_000)) == 1_700_000_000_000

    def test_seconds_int_scaled_to_ms(self):
        assert parse_timestamp(1_700_000_000) == 1_700_000_000_000

    def test_seconds_float_scaled_to_ms(self):
        assert parse_timestamp(1_700_000_000.5) == 1_700_000_000_500

    def test_iso_string(self):
        assert parse_timestamp("2023-11-14T22:13:20Z") == 1_700_000_000_000

    def test_iso_with_offset(self):
        assert parse_timestamp("2023-11-14T17:13:20-05:00") == 1_700_000_000_000

    def test_numeric_string_treated_as_ms(self):
        assert parse_timestamp("1700000000000") == 1_700_000_000_000

    def test_datetime_naive_treated_as_utc(self):
        dt = datetime(2023, 11, 14, 22, 13, 20)
        assert parse_timestamp(dt) == 1_700_000_000_000

    def test_datetime_aware_preserved(self):
        dt = datetime(2023, 11, 14, 17, 13, 20, tzinfo=timezone.utc)
        # 5h earlier -> still 1_700_000_000_000
        # Actually no: 17:13:20 UTC is 5h before 22:13:20 UTC
        expected = int(dt.timestamp() * 1000)
        assert parse_timestamp(dt) == expected

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            parse_timestamp("not-a-timestamp")

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            parse_timestamp([1, 2, 3])


# =============================================================================
# windowed_iter
# =============================================================================

class TestWindowedIter:

    def test_yields_at_least_one_window_for_simple_range(self):
        start, end = 0, 10_000  # 10 seconds
        windows = list(windowed_iter(start, end, "1d"))  # chunk is huge
        assert len(windows) == 1
        assert windows[0].since_ms == start
        assert windows[0].until_ms == end

    def test_windows_are_in_descending_order_backwards_growing(self):
        start = 0
        end = 100 * 24 * 3600 * 1000  # 100 days
        windows = list(windowed_iter(start, end, "5m"))
        # Each window's since is >= the previous window's since; windowed_iter
        # walks *backwards* from end, so successive yields go further back.
        for w in windows:
            assert w.since_ms >= start
            assert w.until_ms <= end
        for prev, cur in zip(windows, windows[1:]):
            assert cur.since_ms < prev.until_ms

    def test_windows_are_disjoint_and_cover_range(self):
        start = 1_000_000
        end = 1_000_000 + 50 * 24 * 3600 * 1000  # 50 days
        windows = list(windowed_iter(start, end, "5m"))
        for w in windows:
            assert w.since_ms < w.until_ms
        # First window pinned at end; last window pinned at start.
        assert windows[0].until_ms == end
        assert windows[-1].since_ms == start
        # Union of windows covers [start, end).
        spans = sum(w.duration_seconds * 1000 for w in windows)
        assert spans == end - start

    def test_short_range_fits_in_one_window(self):
        start = 100_000_000
        end = start + 60_000  # 1 minute
        windows = list(windowed_iter(start, end, "1d"))
        assert len(windows) == 1
        assert windows[0].since_ms == start
        assert windows[0].until_ms == end

    def test_rejects_end_lte_start(self):
        with pytest.raises(ValueError):
            list(windowed_iter(1000, 1000, "1d"))
        with pytest.raises(ValueError):
            list(windowed_iter(2000, 1000, "1d"))

    def test_rejects_none_bounds(self):
        with pytest.raises(ValueError):
            list(windowed_iter(None, 100, "1d"))
        with pytest.raises(ValueError):
            list(windowed_iter(0, None, "1d"))

    def test_window_dataclass_is_frozen(self):
        w = Window(since_ms=0, until_ms=1000)
        with pytest.raises(Exception):
            w.since_ms = 5  # frozen dataclass

    def test_rejects_unsupported_period(self):
        with pytest.raises(ValueError):
            list(windowed_iter(0, 1000, "3m"))


# =============================================================================
# OpenInterestDataManager
# =============================================================================

class TestDataManager:
    def test_creates_base_directory(self, tmp_path):
        target = tmp_path / "fresh"
        OpenInterestDataManager(base_path=str(target))
        assert target.exists()

    def test_round_trip(self, tmp_manager, fake_ccxt_rows):
        df = OIBackfiller._to_dataframe(fake_ccxt_rows)
        tmp_manager.save("binance", "BTC-USDT-SWAP", "5m", df)
        loaded = tmp_manager.load("binance", "BTC-USDT-SWAP", "5m")
        assert loaded is not None
        assert len(loaded) == len(df)
        assert "sumOpenInterest" in loaded.columns
        assert isinstance(loaded.index, pd.DatetimeIndex)

    def test_missing_file_returns_none(self, tmp_manager):
        assert tmp_manager.load("binance", "BTC-USDT-SWAP", "5m") is None

    def test_exists_reflects_save(self, tmp_manager):
        assert not tmp_manager.exists("binance", "BTC", "5m")
        tmp_manager.save("binance", "BTC", "5m", pd.DataFrame({"x": [1, 2, 3]}))
        assert tmp_manager.exists("binance", "BTC", "5m")

    def test_safe_symbol_handles_special_chars(self, tmp_manager):
        path = tmp_manager.path_for("binance", "ETH/USDT:USDT", "1h")
        # Inspect only the path *suffix* under the data dir; the dir itself
        # uses '/' as a separator and is allowed to keep it.
        suffix = Path(path).relative_to(tmp_manager.base_path)
        assert "/" not in str(suffix.parent) or str(suffix.parent).count("/") == 0
        assert ":" not in str(suffix)
        assert "ETH_USDT_USDT" in str(suffix)

    def test_save_empty_skips_but_returns_path(self, tmp_manager):
        path = tmp_manager.save("binance", "BTC", "5m", pd.DataFrame())
        assert path.endswith("5m.parquet")
        assert not Path(path).exists()

    def test_overwrite(self, tmp_manager, fake_ccxt_rows):
        df = OIBackfiller._to_dataframe(fake_ccxt_rows)
        tmp_manager.save("binance", "BTC", "5m", df)
        # overwrite with single-row frame
        short = df.iloc[[0]]
        tmp_manager.save("binance", "BTC", "5m", short)
        loaded = tmp_manager.load("binance", "BTC", "5m")
        assert len(loaded) == 1


# =============================================================================
# OIBackfiller.format_symbol
# =============================================================================

class TestFormatSymbolBinance:
    def test_simple(self, loader_binance):
        assert loader_binance.format_symbol("BTC") == "BTC/USDT:USDT"

    def test_with_dash(self, loader_binance):
        assert loader_binance.format_symbol("BTC-USDT-SWAP") == "BTC/USDT:USDT"

    def test_with_slash(self, loader_binance):
        assert loader_binance.format_symbol("BTC/USDT") == "BTC/USDT:USDT"

    def test_empty_returns_none(self, loader_binance):
        assert loader_binance.format_symbol("") is None

    def test_none_returns_none(self, loader_binance):
        assert loader_binance.format_symbol(None) is None


class TestFormatSymbolOkx:
    def test_simple(self, loader_okx):
        assert loader_okx.format_symbol("BTC") == "BTC/USDT:USDT"

    def test_with_dash(self, loader_okx):
        assert loader_okx.format_symbol("BTC-USDT-SWAP") == "BTC/USDT:USDT"


class TestFormatSymbolUnknownExchange:
    def test_rejects_unsupported_exchange(self):
        with pytest.raises(ValueError):
            OIBackfiller("kraken")


# =============================================================================
# _to_dataframe / _merge
# =============================================================================

class TestNormalizeAndMerge:
    def test_to_dataframe_empty(self):
        df = OIBackfiller._to_dataframe([])
        assert isinstance(df, pd.DataFrame)
        assert df.empty
        assert "sumOpenInterest" in df.columns

    def test_to_dataframe_drops_unwanted_columns(self, loader_binance, fake_ccxt_rows):
        df = OIBackfiller._to_dataframe(fake_ccxt_rows)
        assert "symbol" not in df.columns
        assert "sumOpenInterest" in df.columns
        assert "sumOpenInterestValue" in df.columns
        assert "countOpenInterest" in df.columns

    def test_to_dataframe_index_is_datetime(self, fake_ccxt_rows):
        df = OIBackfiller._to_dataframe(fake_ccxt_rows)
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.is_monotonic_increasing

    def test_to_dataframe_dedupes(self):
        rows = [
            {"timestamp": 1_700_000_000_000, "sumOpenInterest": "1.0"},
            {"timestamp": 1_700_000_000_000, "sumOpenInterest": "2.0"},
        ]
        df = OIBackfiller._to_dataframe(rows)
        assert len(df) == 1
        # keep="last" -> the second value wins
        # ccxt returns strings; normalize to float before comparing.
        assert float(df.iloc[0]["sumOpenInterest"]) == 2.0

    def test_merge_with_no_existing(self):
        new = pd.DataFrame({"x": [1, 2]}, index=pd.to_datetime([1000, 2000], unit="ms"))
        merged = OIBackfiller._merge(None, new)
        assert len(merged) == 2

    def test_merge_with_existing_no_overlap(self):
        existing = pd.DataFrame({"x": [1]}, index=pd.to_datetime([500], unit="ms"))
        new = pd.DataFrame({"x": [2]}, index=pd.to_datetime([1500], unit="ms"))
        merged = OIBackfiller._merge(existing, new)
        assert len(merged) == 2
        assert sorted(merged["x"].tolist()) == [1, 2]

    def test_merge_with_overlap_keeps_newer(self):
        existing = pd.DataFrame(
            {"x": [10.0]}, index=pd.to_datetime([1000], unit="ms"),
        )
        new = pd.DataFrame(
            {"x": [99.0]}, index=pd.to_datetime([1000], unit="ms"),
        )
        merged = OIBackfiller._merge(existing, new)
        assert merged.iloc[0]["x"] == 99.0


# =============================================================================
# backfill (network-monkey-patched)
# =============================================================================

class TestBackfillStubbed:
    """backfill() is monkey-patched at the ccxt layer."""

    def test_backfill_paginates_and_saves(self, tmp_manager, monkeypatch, make_fake_rows):
        loader = OIBackfiller("binance")

        # Pick a 1-day window we can size precisely.
        end_ms = 1_700_000_000_000          # anchor
        start_ms = end_ms - 24 * 3600 * 1000  # 24 hours earlier
        # 5m candles -> ~288 rows in 24h.
        rows_in_window = make_fake_rows(start_ms, 100, step_ms=5 * 60 * 1000)

        call_log: List[tuple] = []

        def fake_fetch(symbol, *, timeframe, since, limit, params=None):
            call_log.append((symbol, timeframe, since, limit,
                             (params or {}).get("until")))
            until = (params or {}).get("until")
            return [
                r for r in rows_in_window
                if since <= r["timestamp"] < until
            ]

        monkeypatch.setattr(loader.exchange, "fetch_open_interest_history", fake_fetch)

        df = loader.backfill(
            "BTC-USDT-SWAP",
            period="5m",
            start_ms=start_ms,
            end_ms=end_ms,
            manager=tmp_manager,
        )

        # 24h at 5m -> chunk is ~34.6h, so only 1 window, but paging still
        # wired. We assert that paging exercised the ccxt path correctly.
        assert len(call_log) >= 1
        assert all(c[0] == "BTC/USDT:USDT" for c in call_log)
        assert all(c[1] == "5m" for c in call_log)
        # Each row was kept (the 100 fake rows + no existing => 100 rows).
        assert len(df) == 100
        assert tmp_manager.exists("binance", "BTC-USDT-SWAP", "5m")
        # Reload from disk and confirm dedupe index.
        on_disk = tmp_manager.load("binance", "BTC-USDT-SWAP", "5m")
        assert on_disk.index.is_unique
        assert len(on_disk) == 100

    def test_backfill_paginates_when_range_exceeds_chunk(self, tmp_manager,
                                                       monkeypatch, make_fake_rows):
        """Verify multi-window paging actually fires for a long range."""
        loader = OIBackfiller("binance")
        end_ms = 1_700_000_000_000
        start_ms = end_ms - 7 * 24 * 3600 * 1000  # 7 days
        rows_in_window = make_fake_rows(start_ms, 7 * 24 * 12, step_ms=5 * 60 * 1000)
        call_log: List[tuple] = []

        def fake_fetch(symbol, *, timeframe, since, limit, params=None):
            call_log.append((symbol, timeframe, since,
                             (params or {}).get("until")))
            until = (params or {}).get("until")
            return [r for r in rows_in_window
                    if since <= r["timestamp"] < until]

        monkeypatch.setattr(loader.exchange, "fetch_open_interest_history", fake_fetch)

        loader.backfill(
            "BTC", period="5m",
            start_ms=start_ms, end_ms=end_ms,
            manager=tmp_manager,
        )
        # 7 days / (~34.6 hours) -> at least 5 windows.
        assert len(call_log) >= 5

    def test_backfill_invalid_symbol_raises(self, tmp_manager):
        loader = OIBackfiller("binance")
        with pytest.raises(ValueError):
            loader.backfill("", period="5m",
                            start_ms=0, end_ms=1, manager=tmp_manager)

    def test_backfill_unknown_period_raises(self, tmp_manager):
        loader = OIBackfiller("binance")
        with pytest.raises(ValueError):
            loader.backfill("BTC", period="3m",
                            start_ms=0, end_ms=1, manager=tmp_manager)

    def test_backfill_no_network_for_invalid_range(self, tmp_manager, monkeypatch):
        loader = OIBackfiller("binance")
        def explode(*a, **kw):
            raise AssertionError("network should not be called for invalid range")
        monkeypatch.setattr(loader.exchange, "fetch_open_interest_history", explode)

        # end_ms == start_ms -> ValueError before any network call
        with pytest.raises(ValueError):
            loader.backfill("BTC", period="5m",
                            start_ms=1000, end_ms=1000, manager=tmp_manager)

    def test_backfill_handles_empty_response(self, tmp_manager, monkeypatch):
        loader = OIBackfiller("binance")

        def empty_fetch(*a, **kw):
            return []

        monkeypatch.setattr(loader.exchange, "fetch_open_interest_history", empty_fetch)

        df = loader.backfill(
            "BTC", period="1h",
            start_ms=1_700_000_000_000,
            end_ms=1_700_000_000_000 + 3600 * 1000,
            manager=tmp_manager,
        )
        # Empty result, but no exception, file may or may not exist.
        assert df.empty

    def test_backfill_filter_window_outliers(self, tmp_manager, monkeypatch):
        """The _fetch_window must drop rows outside [since, until)."""
        loader = OIBackfiller("binance")

        since = 1_700_000_000_000
        # Single 11-minute window: [since, since+11min)
        until = since + 11 * 60 * 1000
        # Place one good row inside, the rest clearly outside.
        def fetch_with_outliers(*a, **kw):
            return [
                {"timestamp": since - 60_000,        "sumOpenInterest": "0"},  # too early
                {"timestamp": since,                "sumOpenInterest": "1"},
                {"timestamp": since + 5 * 60_000,   "sumOpenInterest": "2"},
                {"timestamp": until,                "sumOpenInterest": "3"},  # == until, dropped
                {"timestamp": until + 1,            "sumOpenInterest": "4"},  # too late
            ]

        monkeypatch.setattr(loader.exchange, "fetch_open_interest_history", fetch_with_outliers)

        df = loader.backfill(
            "BTC", period="5m",
            start_ms=since, end_ms=until,
            manager=tmp_manager,
        )
        # Only the 2 rows strictly within [since, until) survive.
        assert len(df) == 2
        assert df.index[0].timestamp() * 1000 == since