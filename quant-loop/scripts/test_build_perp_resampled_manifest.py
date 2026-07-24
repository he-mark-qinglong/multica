"""pytest tests for scripts/build_perp_resampled_manifest.py.

Uses small synthetic 1m klines in tmp_path — never touches the real
data/perp_1m pool. Run: pytest scripts/test_build_perp_resampled_manifest.py
"""
import hashlib
import sys
from pathlib import Path

import pandas as pd
import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import build_perp_resampled_manifest as bprm


# --- Fixtures ----------------------------------------------------------------

def _synthetic_1m(n_bars: int = 120, start_ms: int = 1_600_000_200_000) -> pd.DataFrame:
    """n_bars of synthetic Binance-schema (12-col) 1m klines.

    Default start is 2020-09-13T12:30:00Z — aligned to a 15m boundary so
    resample bins contain whole 5m/15m groups.
    """
    open_time = [start_ms + i * 60_000 for i in range(n_bars)]
    return pd.DataFrame({
        "open_time": open_time,
        "open": [100.0 + i for i in range(n_bars)],
        "high": [100.5 + i for i in range(n_bars)],
        "low": [99.5 + i for i in range(n_bars)],
        "close": [100.2 + i for i in range(n_bars)],
        "volume": [1.0] * n_bars,
        "close_time": [t + 59_999 for t in open_time],
        "quote_volume": [100.0] * n_bars,
        "trades": [10] * n_bars,
        "taker_buy_base": [0.5] * n_bars,
        "taker_buy_quote": [50.0] * n_bars,
        "ignore": [0] * n_bars,
    })


@pytest.fixture()
def tmp_pool(tmp_path: Path) -> Path:
    """tmp root with data/perp_1m/TESTUSDT_1m.parquet (synthetic)."""
    src_dir = tmp_path / "data" / "perp_1m"
    src_dir.mkdir(parents=True)
    _synthetic_1m().to_parquet(src_dir / "TESTUSDT_1m.parquet", index=False)
    return tmp_path


# --- Unit tests --------------------------------------------------------------

def test_normalize_1m_drops_close_time_and_ignore():
    out = bprm.normalize_1m(_synthetic_1m(10))
    assert out.columns.tolist() == bprm.OUT_COLUMNS
    assert "close_time" not in out.columns
    assert "ignore" not in out.columns
    assert out["open_time"].dtype == "int64"
    assert out["trades"].dtype == "float64"


def test_resample_ohlcv_aggregation_and_alignment():
    df = bprm.normalize_1m(_synthetic_1m(120))
    out = bprm.resample_ohlcv(df, "5min")
    assert len(out) == 24
    assert out.columns.tolist() == bprm.OUT_COLUMNS
    first = out.iloc[0]
    # Bin label = left edge, ms int64, aligned to 5m.
    assert first["open_time"] == df["open_time"].iloc[0]
    assert out["open_time"].dtype == "int64"
    assert (out["open_time"] % (5 * 60_000) == 0).all()
    # OHLCV aggregation of the first 5 1m bars.
    assert first["open"] == df["open"].iloc[0]
    assert first["high"] == df["high"].iloc[:5].max()
    assert first["low"] == df["low"].iloc[:5].min()
    assert first["close"] == df["close"].iloc[4]
    assert first["volume"] == 5.0
    assert first["trades"] == 50.0
    # Timestamps strictly increasing, continuous.
    assert (out["open_time"].diff().dropna() == 5 * 60_000).all()


def test_resample_ohlcv_15m_row_count():
    df = bprm.normalize_1m(_synthetic_1m(120))
    out = bprm.resample_ohlcv(df, "15min")
    assert len(out) == 8
    assert (out["open_time"].diff().dropna() == 15 * 60_000).all()


def test_identity_check_passes_on_own_resample():
    df = bprm.normalize_1m(_synthetic_1m(120))
    out = bprm.resample_ohlcv(df, "5min")
    ic = bprm.identity_check(df, out, "5m", "5min")
    assert ic["passed"] is True
    assert ic["n_1m_in_bin"] == 5


def test_identity_check_detects_corruption():
    df = bprm.normalize_1m(_synthetic_1m(120))
    out = bprm.resample_ohlcv(df, "5min")
    out.loc[out.index[-1], "close"] += 1.0  # corrupt last close
    ic = bprm.identity_check(df, out, "5m", "5min")
    assert ic["passed"] is False


def test_continuity_audit_detects_gap():
    df = bprm.normalize_1m(_synthetic_1m(120))
    out = bprm.resample_ohlcv(df, "5min")
    assert bprm.continuity_audit(out, "5min")["n_gaps"] == 0
    broken = out.drop(out.index[5]).reset_index(drop=True)
    audit = bprm.continuity_audit(broken, "5min")
    assert audit["n_gaps"] == 1
    assert audit["strictly_increasing"] is True
    assert audit["gaps"][0]["diff_ms"] == 2 * 5 * 60_000


# --- End-to-end on tmp pool --------------------------------------------------

def test_build_manifest_end_to_end(tmp_pool: Path):
    m = bprm.build_manifest("2099-01-01", root=tmp_pool, symbols=["TESTUSDT"])

    # Contract fields.
    assert m["market"] == "usdm_perp"
    assert m["source"] == "perp_1m"
    assert m["schema"] == bprm.OUT_COLUMNS

    for tf, bin_ms in (("5m", 5 * 60_000), ("15m", 15 * 60_000)):
        info = m["resampled"]["TESTUSDT"][tf]
        out_path = tmp_pool / info["path"]
        assert out_path.exists()
        # sha256 recorded matches file on disk.
        assert info["sha256"] == hashlib.sha256(out_path.read_bytes()).hexdigest()
        # Readable by pandas, uniform 10-col schema, continuous timestamps.
        df = pd.read_parquet(out_path)
        assert df.columns.tolist() == bprm.OUT_COLUMNS
        assert len(df) == info["rows"]
        assert (df["open_time"].diff().dropna() == bin_ms).all()
        # Time range recorded in manifest matches the data.
        assert info["first_open_time_ms"] == int(df["open_time"].iloc[0])
        assert info["last_open_time_ms"] == int(df["open_time"].iloc[-1])
        # Identity + continuity recorded and clean.
        assert m["identity_checks"]["TESTUSDT"][tf]["passed"] is True
        assert m["continuity"]["TESTUSDT"][tf]["n_gaps"] == 0

    # YAML written with required contract keys.
    yaml_text = (tmp_pool / m["yaml_path"]).read_text()
    assert "market: usdm_perp" in yaml_text
    assert "source: perp_1m" in yaml_text
    assert "sha256:" in yaml_text
    assert "data/perp_5m/TESTUSDT_5m.parquet" in yaml_text
    assert "data/perp_15m/TESTUSDT_15m.parquet" in yaml_text


def test_build_manifest_dry_run_writes_nothing(tmp_pool: Path):
    m = bprm.build_manifest("2099-01-01", root=tmp_pool, symbols=["TESTUSDT"], dry_run=True)
    assert not (tmp_pool / "data" / "perp_5m").exists()
    assert not (tmp_pool / "data" / "perp_15m").exists()
    assert not (tmp_pool / m["yaml_path"]).exists()
    # Manifest content still computed.
    assert m["resampled"]["TESTUSDT"]["5m"]["rows"] == 24


def test_build_manifest_missing_source_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        bprm.build_manifest("2099-01-01", root=tmp_path, symbols=["NOPE"], dry_run=True)
