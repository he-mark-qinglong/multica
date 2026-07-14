"""Unit tests for `data_loader.py`.

These tests exercise the SHA256 manifest round-trip and the parquet
cache hit/miss path using a temporary source root with synthetic 1m
parquet files. They do NOT depend on the canonical Binance ETL; the
sandbox may or may not have it, and the test must be hermetic.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import data_loader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_synthetic_1m_parquet(path: Path, n: int = 5000, seed: int = 0) -> None:
    """Write a valid fapi_*USDT__1m.parquet at `path`.

    5000 rows = ~3.5 days of 1m bars. Schema matches the canonical
    Binance USD-M layout: openTime index, OHLCV columns.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC")
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.05, size=n))
    open_ = close + rng.normal(0.0, 0.02, size=n)
    high = np.maximum(close, open_) + 0.01
    low = np.minimum(close, open_) - 0.01
    volume = rng.uniform(1.0, 100.0, size=n)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    df.index.name = "openTime"
    df.to_parquet(path)


@pytest.fixture
def temp_source_root(tmp_path: Path) -> Path:
    """Create a temp dir with 3 synthetic 1m parquet files."""
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        _make_synthetic_1m_parquet(src / f"fapi_{sym}__1m.parquet", n=4000, seed=hash(sym) % 2**32)
    return src


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "cache"


# ---------------------------------------------------------------------------
# Manifest round-trip
# ---------------------------------------------------------------------------
class TestSourceManifest:
    def test_build_manifest_finds_all_parquets(self, temp_source_root: Path):
        manifest = data_loader.build_source_manifest(source_root=temp_source_root)
        rels = sorted(manifest.files)
        # Three parquets, named per Binance USD-M layout.
        assert rels == [
            "fapi_BTCUSDT__1m.parquet",
            "fapi_ETHUSDT__1m.parquet",
            "fapi_SOLUSDT__1m.parquet",
        ]
        # SHA256 is 64 hex chars.
        for sha in manifest.files.values():
            assert len(sha) == 64
            assert all(c in "0123456789abcdef" for c in sha)

    def test_manifest_round_trip(self, temp_source_root: Path, tmp_path: Path):
        manifest = data_loader.build_source_manifest(source_root=temp_source_root)
        out = tmp_path / "manifest.parquet.sha256"
        manifest.write(out)
        # File exists, three lines, two whitespace-separated fields each.
        text = out.read_text().strip().splitlines()
        assert len(text) == 3
        for line in text:
            sha, rel = line.split("  ")
            assert len(sha) == 64
            assert rel.startswith("fapi_")

    def test_verify_passes_for_unchanged_files(self, temp_source_root: Path):
        manifest = data_loader.build_source_manifest(source_root=temp_source_root)
        drift = manifest.verify()
        assert drift == []

    def test_verify_detects_drift_when_file_mutated(self, temp_source_root: Path):
        manifest = data_loader.build_source_manifest(source_root=temp_source_root)
        # Overwrite BTCUSDT with different data -> SHA must change.
        target = temp_source_root / "fapi_BTCUSDT__1m.parquet"
        _make_synthetic_1m_parquet(target, n=4000, seed=99999)
        drift = manifest.verify()
        assert "fapi_BTCUSDT__1m.parquet" in drift

    def test_build_manifest_raises_on_empty_dir(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError):
            data_loader.build_source_manifest(source_root=empty)


# ---------------------------------------------------------------------------
# load_symbol_1d
# ---------------------------------------------------------------------------
class TestLoadSymbol1d:
    def test_returns_dataframe_with_correct_schema(
        self, temp_source_root: Path, temp_data_dir: Path
    ):
        df = data_loader.load_symbol_1d(
            "BTCUSDT", source_root=temp_source_root, data_dir=temp_data_dir
        )
        # Index is openTime, UTC tz.
        assert df.index.name == "openTime"
        assert df.index.tz is not None
        assert str(df.index.tz) == "UTC"
        # Columns match the spec.
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}
        # 4000 1m bars -> ~3 days of 1d bars.
        assert 1 <= len(df) <= 10
        # Cached parquet was written.
        assert (temp_data_dir / "fapi_BTCUSDT__1d.parquet").exists()

    def test_cache_hit_skips_rebuild(
        self, temp_source_root: Path, temp_data_dir: Path
    ):
        # First call -> builds cache.
        data_loader.load_symbol_1d(
            "ETHUSDT", source_root=temp_source_root, data_dir=temp_data_dir
        )
        # Mutate the cached file in a detectable way (insert a row).
        cache = temp_data_dir / "fapi_ETHUSDT__1d.parquet"
        cached = pd.read_parquet(cache)
        mutated = pd.concat([cached, cached.iloc[[0]]])
        mutated.to_parquet(cache)
        # Second call WITHOUT refresh -> returns the mutated cache.
        df2 = data_loader.load_symbol_1d(
            "ETHUSDT", source_root=temp_source_root, data_dir=temp_data_dir
        )
        assert len(df2) == len(cached) + 1
        # With refresh=True -> rebuilt from source.
        df3 = data_loader.load_symbol_1d(
            "ETHUSDT",
            source_root=temp_source_root,
            data_dir=temp_data_dir,
            refresh=True,
        )
        assert len(df3) == len(cached)

    def test_missing_symbol_raises(self, temp_source_root: Path, temp_data_dir: Path):
        with pytest.raises(FileNotFoundError):
            data_loader.load_symbol_1d(
                "DOGEUSDT", source_root=temp_source_root, data_dir=temp_data_dir
            )

    def test_missing_columns_raises(self, tmp_path: Path, temp_data_dir: Path):
        # Write a parquet that lacks the required columns.
        src = tmp_path / "bad_src"
        src.mkdir()
        bad = pd.DataFrame({"foo": [1.0, 2.0], "bar": [3.0, 4.0]})
        bad.index.name = "openTime"
        bad.to_parquet(src / "fapi_BTCUSDT__1m.parquet")
        with pytest.raises(ValueError, match="missing columns"):
            data_loader.load_symbol_1d(
                "BTCUSDT", source_root=src, data_dir=temp_data_dir
            )


# ---------------------------------------------------------------------------
# load_all
# ---------------------------------------------------------------------------
class TestLoadAll:
    def test_loads_all_symbols_from_config(
        self, temp_source_root: Path, temp_data_dir: Path, monkeypatch
    ):
        # Point CONFIG_PATH at our temp config by monkeypatching the module.
        fake_cfg = tmp_path_cfg = temp_data_dir.parent / "cfg.json"
        fake_cfg.write_text(json.dumps({"instruments": ["BTCUSDT", "ETHUSDT"]}))
        monkeypatch.setattr(data_loader, "CONFIG_PATH", fake_cfg)
        result = data_loader.load_all(
            source_root=temp_source_root, data_dir=temp_data_dir
        )
        assert set(result.keys()) == {"BTCUSDT", "ETHUSDT"}
        for sym, df in result.items():
            assert df.index.name == "openTime"
            assert len(df) >= 1


# ---------------------------------------------------------------------------
# main() CLI smoke test
# ---------------------------------------------------------------------------
class TestMainCLI:
    def test_main_writes_manifest_and_caches(
        self, temp_source_root: Path, temp_data_dir: Path, monkeypatch
    ):
        # Redirect the canonical paths to our temp locations.
        monkeypatch.setattr(data_loader, "DEFAULT_SOURCE_ROOT", temp_source_root)
        monkeypatch.setattr(data_loader, "DATA_DIR", temp_data_dir)
        fake_cfg = temp_data_dir.parent / "cfg.json"
        fake_cfg.write_text(
            json.dumps({"instruments": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]})
        )
        monkeypatch.setattr(data_loader, "CONFIG_PATH", fake_cfg)

        rc = data_loader.main()
        assert rc == 0
        # Manifest exists with three files.
        manifest = temp_data_dir / "manifest.parquet.sha256"
        assert manifest.exists()
        assert len(manifest.read_text().strip().splitlines()) == 3
        # Each symbol has its 1d cache file.
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            assert (temp_data_dir / f"fapi_{sym}__1d.parquet").exists()