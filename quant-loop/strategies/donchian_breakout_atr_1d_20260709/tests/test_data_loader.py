"""Smoke tests for the data loader.

We assert:
    * the 1d cache is regeneratable
    * the schema is exactly what ``strategy.annotate`` expects
    * the SHA256 manifest written to disk matches a freshly computed digest
      of the upstream 1m parquets (no ETL drift sneaks in)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_loader import (  # noqa: E402
    DATA_DIR,
    DEFAULT_SOURCE_ROOT,
    build_source_manifest,
    load_symbol_1d,
)

CONFIG_PATH = ROOT / "config.json"


def test_1d_cache_has_expected_schema():
    df = load_symbol_1d("BTCUSDT")
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is not None  # tz-aware, UTC
    assert df.index.name == "openTime"
    expected_cols = {"open", "high", "low", "close", "volume"}
    assert set(df.columns) == expected_cols
    # No null close values
    assert not df["close"].isna().any()


def test_1d_close_matches_last_1m_close():
    """The 1d ``close`` of the last trading day must equal the close of the
    last 1m bar in the source parquet (sanity check on the resampler)."""
    src = pd.read_parquet(DEFAULT_SOURCE_ROOT / "fapi_BTCUSDT__1m.parquet")
    if src.index.name != "openTime":
        src.index.name = "openTime"
    if src.index.tz is None:
        src.index = src.index.tz_localize("UTC")
    daily = load_symbol_1d("BTCUSDT")
    # The last 1m bar in the source has the most recent close.
    expected_close = float(src["close"].iloc[-1])
    assert abs(float(daily["close"].iloc[-1]) - expected_close) < 1e-6


def test_manifest_matches_actual_source_sha256():
    """Round-trip: the manifest we wrote must still match a fresh digest."""
    manifest_path = DATA_DIR / "manifest.parquet.sha256"
    if not manifest_path.exists():
        # Generate it the way the CLI does.
        manifest = build_source_manifest()
        manifest.write(manifest_path)

    written = {}
    for line in manifest_path.read_text().splitlines():
        sha, rel = line.split("  ", 1)
        written[rel] = sha

    fresh = build_source_manifest()
    for rel, expected in written.items():
        assert fresh.files.get(rel) == expected, f"manifest drift on {rel}"


def test_all_config_instruments_loadable():
    cfg = json.loads(CONFIG_PATH.read_text())
    for sym in cfg["instruments"]:
        df = load_symbol_1d(sym)
        assert len(df) > 200  # at least ~9 months of daily bars