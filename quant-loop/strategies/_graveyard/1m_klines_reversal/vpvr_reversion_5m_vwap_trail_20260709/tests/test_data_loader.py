"""Smoke tests for V1 data_loader.py.

Asserts:
    * the 5m cache is regeneratable from the canonical 1m source
    * the schema is exactly what strategy.annotate expects
    * the SHA256 manifest matches a freshly computed digest
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
    load_symbol,
)

CONFIG_PATH = ROOT / "config.json"


def test_5m_cache_has_expected_schema():
    cfg = json.loads(CONFIG_PATH.read_text())
    df = load_symbol("BTCUSDT", cfg["timeframe"], refresh=True)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is not None
    assert df.index.name == "openTime"
    expected_cols = {"open", "high", "low", "close", "volume"}
    assert set(df.columns) == expected_cols
    assert not df["close"].isna().any()


def test_5m_close_matches_last_1m_close_for_last_bucket():
    cfg = json.loads(CONFIG_PATH.read_text())
    df_5m = load_symbol("ETHUSDT", cfg["timeframe"])
    src = pd.read_parquet(DEFAULT_SOURCE_ROOT / "fapi_ETHUSDT__1m.parquet")
    if src.index.name != "openTime":
        src.index.name = "openTime"
    if src.index.tz is None:
        src.index = src.index.tz_localize("UTC")
    last_5m_close = float(df_5m["close"].iloc[-1])
    # Walk back through 1m bars to find the last 1m close at-or-before the last 5m bar.
    last_bar = df_5m.index[-1]
    last_1m_close = float(src.loc[:last_bar, "close"].iloc[-1])
    assert abs(last_5m_close - last_1m_close) < 1e-6


def test_manifest_matches_actual_source_sha256():
    manifest_path = DATA_DIR / "manifest.parquet.sha256"
    if not manifest_path.exists():
        build_source_manifest().write(manifest_path)
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
        df = load_symbol(sym, cfg["timeframe"])
        assert len(df) > 100  # at least ~8 hours of 5m bars