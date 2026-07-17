"""Smoke tests for the VPVR micro-reversion 1h data loader.

We assert:
    * the 1h cache has the expected schema
    * the SHA256 manifest written to disk matches a freshly computed digest
      of the upstream 1h parquets (no ETL drift sneaks in)
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
    build_source_manifest,
    load_symbol_1h,
)

CONFIG_PATH = ROOT / "config.json"


def test_1h_source_has_expected_schema():
    df = load_symbol_1h("BTCUSDT")
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is not None  # tz-aware, UTC
    assert df.index.name == "openTime"
    expected_cols = {"open", "high", "low", "close", "volume"}
    assert set(df.columns) == expected_cols
    # No null close values
    assert not df["close"].isna().any()


def test_1h_index_is_sorted_ascending():
    df = load_symbol_1h("BTCUSDT")
    assert df.index.is_monotonic_increasing


def test_manifest_matches_actual_source_sha256():
    """Round-trip: the manifest we wrote must still match a fresh digest."""
    manifest_path = DATA_DIR / "manifest.parquet.sha256"
    if not manifest_path.exists():
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
        df = load_symbol_1h(sym)
        assert len(df) > 1_000  # at least ~42 days of 1h bars