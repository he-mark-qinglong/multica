"""Smoke tests for V4 data_loader.py (1d resample)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_loader import (  # noqa: E402
    DATA_DIR, DEFAULT_SOURCE_ROOT, build_source_manifest, load_all, load_symbol,
)

CONFIG_PATH = ROOT / "config.json"


def test_1d_cache_has_expected_schema():
    cfg = json.loads(CONFIG_PATH.read_text())
    df = load_symbol("BTCUSDT", cfg["timeframe"], refresh=True)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is not None
    assert df.index.name == "openTime"
    expected_cols = {"open", "high", "low", "close", "volume"}
    assert set(df.columns) == expected_cols
    assert not df["close"].isna().any()


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


def test_all_config_instruments_loadable_and_aligned():
    cfg = json.loads(CONFIG_PATH.read_text())
    data = load_all(cfg["instruments"], cfg["timeframe"])
    assert set(data.keys()) == set(cfg["instruments"])
    for sym, df in data.items():
        assert len(df) > 200  # at least 200 daily bars