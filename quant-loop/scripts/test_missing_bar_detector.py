"""pytest tests for scripts/missing_bar_detector.py.

Uses small synthetic parquet files in tmp_path — never touches the real
quant-loop pool. Run:

    pytest scripts/test_missing_bar_detector.py -v

or, from the repo root:

    pytest quant-loop/scripts/test_missing_bar_detector.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import missing_bar_detector as mbd


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _synthetic_ohlcv(
    n_bars: int,
    bar_ms: int,
    start_ms: int,
    *,
    gap_at: dict[int, int] | None = None,
    boundary_drift_ms: int = 0,
    drop_close_nans: int = 0,
) -> pd.DataFrame:
    """Build a synthetic 12-col Binance-schema OHLCV frame.

    Args:
        n_bars: number of bars to emit.
        bar_ms: bar interval in ms (60_000 for 1m, etc.).
        start_ms: open_time of bar 0; rounded DOWN to the bar_ms grid so
            the series is boundary-aligned (pass an already-aligned value
            when you need a specific timestamp).
        gap_at: {bar_index: extra_ms} — adds extra time after bar_index,
            producing a gap of (bar_ms + extra_ms) between bar_index and
            bar_index+1. Use to inject internal missing bars.
        boundary_drift_ms: constant added to every open_time; used to
            produce boundary misalignment.
        drop_close_nans: number of trailing bars whose ``close`` should be
            NaN (to test the nan_close counter).
    """
    gap_at = gap_at or {}
    cursor = (start_ms // bar_ms) * bar_ms
    rows = []
    for i in range(n_bars):
        ot = cursor + i * bar_ms + boundary_drift_ms
        if i in gap_at:
            # shift the next bar forward by the gap amount
            cursor += gap_at[i]
        rows.append({
            "open_time": ot,
            "open": 100.0 + i,
            "high": 100.5 + i,
            "low": 99.5 + i,
            "close": (100.2 + i) if i < n_bars - drop_close_nans else float("nan"),
            "volume": 1.0,
            "close_time": ot + bar_ms - 1,
            "quote_volume": 100.0,
            "trades": 10,
            "taker_buy_base": 0.5,
            "taker_buy_quote": 50.0,
            "ignore": 0,
        })
    return pd.DataFrame(rows)


@pytest.fixture()
def tmp_quant_root(tmp_path: Path) -> Path:
    """Return a tmp quant-loop root with empty canonical bucket dirs."""
    (tmp_path / "data" / "perp_1m").mkdir(parents=True)
    (tmp_path / "data" / "perp_2h").mkdir(parents=True)
    (tmp_path / "data" / "perp_30m").mkdir(parents=True)
    (tmp_path / "live_data").mkdir(parents=True)
    (tmp_path / "freqtrade_v10" / "user_data" / "data").mkdir(parents=True)
    (tmp_path / "strategies").mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stem,expected", [
    ("BTCUSDT_15m", ("BTCUSDT", "15m")),
    ("ETHUSDT_1m", ("ETHUSDT", "1m")),
    ("SOLUSDT_4h", ("SOLUSDT", "4h")),
    ("BTCUSDT-30m", ("BTCUSDT", "30m")),
    ("BTCUSDT__1m", ("BTCUSDT", "1m")),
    ("fapi_BTCUSDT__1m", ("BTCUSDT", "1m")),
    ("fapi_ETHUSDT__15m", ("ETHUSDT", "15m")),
])
def test_parse_symbol_interval_happy(stem, expected):
    assert mbd._parse_symbol_interval(stem) == expected


@pytest.mark.parametrize("stem", [
    "BTCUSDT_zz",  # unknown interval
    "README",  # not even a symbol/interval
    "btcusdt_15m",  # lowercase — binance is uppercase
])
def test_parse_symbol_interval_rejects(stem):
    assert mbd._parse_symbol_interval(stem) is None


# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------


def test_find_canonical_specs_walks_all_buckets(tmp_quant_root: Path):
    (tmp_quant_root / "data" / "perp_1m" / "BTCUSDT_1m.parquet").write_bytes(b"")
    (tmp_quant_root / "live_data" / "ETHUSDT_15m.parquet").write_bytes(b"")
    ft_dir = tmp_quant_root / "freqtrade_v10" / "user_data" / "data"
    (ft_dir / "SOLUSDT-30m.feather").write_bytes(b"")
    strat = tmp_quant_root / "strategies" / "vpvr_demo" / "data"
    strat.mkdir(parents=True)
    (strat / "fapi_BTCUSDT__1m.parquet").write_bytes(b"")

    specs = mbd.find_canonical_specs(tmp_quant_root)
    keys = sorted((s.symbol, s.interval, s.bucket) for s in specs)
    assert ("BTCUSDT", "1m", "shared_pool") in keys
    assert ("ETHUSDT", "15m", "shared_pool") in keys
    assert ("SOLUSDT", "30m", "freqtrade_user_data") in keys
    assert ("BTCUSDT", "1m", "strategy_local") in keys


def test_find_canonical_specs_skips_unknown_intervals(tmp_quant_root: Path):
    (tmp_quant_root / "live_data" / "BTCUSDT_zz.parquet").write_bytes(b"")
    (tmp_quant_root / "live_data" / "BTCUSDT_15m.parquet").write_bytes(b"")
    specs = mbd.find_canonical_specs(tmp_quant_root)
    keys = [(s.symbol, s.interval) for s in specs]
    assert ("BTCUSDT", "zz") not in keys
    assert ("BTCUSDT", "15m") in keys


def test_find_canonical_specs_handles_missing_bucket(tmp_quant_root: Path):
    # freqtrade_v10 not created at all -> no crash, no specs from that bucket.
    (tmp_quant_root / "live_data" / "BTCUSDT_15m.parquet").write_bytes(b"")
    specs = mbd.find_canonical_specs(tmp_quant_root)
    buckets = {s.bucket for s in specs}
    assert "shared_pool" in buckets
    assert "freqtrade_user_data" not in buckets


# ---------------------------------------------------------------------------
# detect_path / detect_series
# ---------------------------------------------------------------------------


def _write_parquet(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def test_detect_missing_file(tmp_path: Path):
    spec = mbd.SeriesSpec(
        symbol="BTCUSDT", interval="1m",
        path=tmp_path / "missing.parquet", bucket="ad_hoc",
    )
    r = mbd.detect_series(spec, now_ms=1_700_000_000_000)
    assert r.missing is True
    assert r.ok is False
    assert r.rows_in_window == 0


def test_detect_too_small_file(tmp_path: Path):
    p = tmp_path / "BTCUSDT_15m.parquet"
    p.write_bytes(b"x" * 100)
    spec = mbd.SeriesSpec(
        symbol="BTCUSDT", interval="15m", path=p, bucket="ad_hoc",
    )
    r = mbd.detect_series(spec, now_ms=1_700_000_000_000)
    assert r.too_small is True
    assert r.ok is False


def test_detect_clean_series_is_ok(tmp_path: Path):
    # 1h bars, last bar 30 min before now -> trailing_short_bars=0
    bar_ms = mbd.BAR_MS["1h"]
    start = 1_700_000_000_000 - 50 * bar_ms
    df = _synthetic_ohlcv(n_bars=50, bar_ms=bar_ms, start_ms=start)
    p = _write_parquet(df, tmp_path / "BTCUSDT_1h.parquet")
    spec = mbd.SeriesSpec(symbol="BTCUSDT", interval="1h", path=p, bucket="shared_pool")

    now = int(df["open_time"].iloc[-1]) + 30 * 60_000  # 30 min later
    r = mbd.detect_series(spec, now_ms=now, window_ms=7 * 24 * 60 * 60_000)
    assert r.missing is False
    assert r.schema_ok is True
    assert r.ts_monotonic is True
    assert r.boundary_misalign_count == 0
    assert r.rows_total == 50
    assert r.missing_bars_in_window == 0
    assert r.full_file_missing_bars == 0
    assert r.internal_gap is False
    assert r.trailing_edge_short is False
    assert r.ok is True


def test_detect_internal_gap_is_hard_fail(tmp_path: Path):
    bar_ms = mbd.BAR_MS["1h"]
    start = 1_700_000_000_000 - 50 * bar_ms
    # Inject a 5-bar gap (3 missing bars) after bar index 20.
    df = _synthetic_ohlcv(
        n_bars=50, bar_ms=bar_ms, start_ms=start,
        gap_at={20: 5 * bar_ms},  # gap = 6*bar_ms -> missing = 5
    )
    p = _write_parquet(df, tmp_path / "BTCUSDT_1h.parquet")
    spec = mbd.SeriesSpec(symbol="BTCUSDT", interval="1h", path=p, bucket="shared_pool")

    now = int(df["open_time"].iloc[-1]) + 30 * 60_000
    r = mbd.detect_series(spec, now_ms=now, window_ms=7 * 24 * 60 * 60_000)
    assert r.internal_gap is True
    assert r.ok is False
    # Largest-gap record present and correctly attributed.
    assert len(r.largest_gaps_in_window) >= 1
    top = r.largest_gaps_in_window[0]
    assert top["missing_bars"] >= 5


def test_detect_boundary_misalignment(tmp_path: Path):
    bar_ms = mbd.BAR_MS["1h"]
    start = 1_700_000_000_000 - 50 * bar_ms
    df = _synthetic_ohlcv(
        n_bars=20, bar_ms=bar_ms, start_ms=start,
        boundary_drift_ms=37_000,  # 37s offset -> misaligned on every bar
    )
    p = _write_parquet(df, tmp_path / "BTCUSDT_1h.parquet")
    spec = mbd.SeriesSpec(symbol="BTCUSDT", interval="1h", path=p, bucket="shared_pool")
    now = int(df["open_time"].iloc[-1]) + 30 * 60_000
    r = mbd.detect_series(spec, now_ms=now, window_ms=7 * 24 * 60 * 60_000)
    assert r.boundary_misalign_count == 20


def test_detect_trailing_stale_reported_not_hard_fail(tmp_path: Path):
    bar_ms = mbd.BAR_MS["1h"]
    start = 1_700_000_000_000 - 50 * bar_ms
    df = _synthetic_ohlcv(n_bars=50, bar_ms=bar_ms, start_ms=start)
    p = _write_parquet(df, tmp_path / "BTCUSDT_1h.parquet")
    spec = mbd.SeriesSpec(symbol="BTCUSDT", interval="1h", path=p, bucket="shared_pool")
    now = int(df["open_time"].iloc[-1]) + 5 * bar_ms  # 5h stale
    r = mbd.detect_series(spec, now_ms=now, window_ms=7 * 24 * 60 * 60_000)
    assert r.trailing_edge_short is True
    assert r.trailing_short_bars == 5
    # Trailing staleness alone is not hard-fail per the docstring.
    assert r.ok is True


def test_detect_schema_missing(tmp_path: Path):
    bar_ms = mbd.BAR_MS["1h"]
    start = 1_700_000_000_000 - 10 * bar_ms
    df = _synthetic_ohlcv(n_bars=10, bar_ms=bar_ms, start_ms=start)
    df = df.drop(columns=["volume"])  # break schema
    p = _write_parquet(df, tmp_path / "BTCUSDT_1h.parquet")
    spec = mbd.SeriesSpec(symbol="BTCUSDT", interval="1h", path=p, bucket="shared_pool")
    now = int(df["open_time"].iloc[-1]) + 30 * 60_000
    r = mbd.detect_series(spec, now_ms=now, window_ms=7 * 24 * 60 * 60_000)
    assert r.schema_ok is False
    assert "volume" in r.schema_missing
    assert r.ok is False


def test_detect_nan_close_is_hard_fail(tmp_path: Path):
    bar_ms = mbd.BAR_MS["1h"]
    start = 1_700_000_000_000 - 10 * bar_ms
    df = _synthetic_ohlcv(n_bars=10, bar_ms=bar_ms, start_ms=start,
                          drop_close_nans=2)
    p = _write_parquet(df, tmp_path / "BTCUSDT_1h.parquet")
    spec = mbd.SeriesSpec(symbol="BTCUSDT", interval="1h", path=p, bucket="shared_pool")
    now = int(df["open_time"].iloc[-1]) + 30 * 60_000
    r = mbd.detect_series(spec, now_ms=now, window_ms=7 * 24 * 60 * 60_000)
    assert r.nan_close_total == 2
    assert r.ok is False


def test_detect_symlink_is_flagged_and_hard_failed(tmp_path: Path):
    real = _write_parquet(
        _synthetic_ohlcv(n_bars=10, bar_ms=mbd.BAR_MS["1h"],
                         start_ms=1_700_000_000_000 - 10 * mbd.BAR_MS["1h"]),
        tmp_path / "real_BTCUSDT_1h.parquet",
    )
    link = tmp_path / "BTCUSDT_1h.parquet"
    link.symlink_to(real)
    spec = mbd.SeriesSpec(symbol="BTCUSDT", interval="1h", path=link, bucket="shared_pool")
    now = int(real.stat().st_size) and 1_700_000_000_000  # any valid
    r = mbd.detect_series(spec, now_ms=1_700_000_000_000)
    assert r.is_symlink is True
    assert r.ok is False


def test_detect_full_file_sampled_stride(tmp_path: Path):
    bar_ms = mbd.BAR_MS["1m"]
    start = 1_700_000_000_000 - 20_000 * bar_ms
    df = _synthetic_ohlcv(n_bars=20_500, bar_ms=bar_ms, start_ms=start,
                          gap_at={10_000: 5 * bar_ms})
    p = _write_parquet(df, tmp_path / "BTCUSDT_1m.parquet")
    spec = mbd.SeriesSpec(symbol="BTCUSDT", interval="1m", path=p, bucket="shared_pool")
    now = int(df["open_time"].iloc[-1]) + 30_000
    r = mbd.detect_series(spec, now_ms=now, window_ms=7 * 24 * 60 * 60_000,
                          full_file_stride=100)
    assert r.full_file_audit_sampled is True
    # With stride=100 the missing-bars count is scaled up.
    assert r.full_file_missing_bars >= 5
    if r.full_file_largest_gaps:
        assert r.full_file_largest_gaps[0].get("sampled") is True


def test_run_grid_runs_every_spec(tmp_quant_root: Path):
    (tmp_quant_root / "live_data" / "BTCUSDT_15m.parquet").write_bytes(b"x" * 2000)
    (tmp_quant_root / "live_data" / "ETHUSDT_15m.parquet").write_bytes(b"x" * 2000)
    specs = mbd.find_canonical_specs(tmp_quant_root)
    reports = mbd.run_grid(specs, now_ms=1_700_000_000_000)
    assert len(reports) == len(specs) == 2


def test_main_cli_writes_report(tmp_quant_root: Path, tmp_path: Path, capsys):
    (tmp_quant_root / "live_data" / "BTCUSDT_15m.parquet").write_bytes(b"x" * 2000)
    out = tmp_path / "report.json"
    rc = mbd.main([
        "--root", str(tmp_quant_root),
        "--out", str(out),
        "--buckets", "shared_pool",
        "--quiet",
    ])
    # 1 file present but too_small -> 1 failed, 0 missing -> rc=1
    assert rc == 1
    assert out.is_file()
    import json
    obj = json.loads(out.read_text())
    assert obj["totals"]["files"] == 1
    assert "bar_ms" in obj
    keys = list(obj["files"].keys())
    assert len(keys) == 1
    assert keys[0].startswith("BTCUSDT_15m@shared_pool::")


# ---------------------------------------------------------------------------
# Schema variants (alpaca `date` column, freqtrade `.feather`)
# ---------------------------------------------------------------------------


def _alpaca_ohlcv(n_bars: int, bar_ms: int, start_ms: int) -> pd.DataFrame:
    """Build an alpaca-style frame: `date` (datetime64[ns, UTC]) + OHLCV."""
    idx = pd.to_datetime(
        [start_ms + i * bar_ms for i in range(n_bars)],
        unit="ms", utc=True,
    )
    return pd.DataFrame({
        "date": idx,
        "open": 100.0 + pd.Series(range(n_bars), dtype="float64"),
        "high": 100.5 + pd.Series(range(n_bars), dtype="float64"),
        "low": 99.5 + pd.Series(range(n_bars), dtype="float64"),
        "close": 100.2 + pd.Series(range(n_bars), dtype="float64"),
        "volume": 1.0,
    })


def test_detect_alpaca_date_column_schema_ok(tmp_path: Path):
    """Strategy-local snapshots use `date` instead of `open_time`."""
    bar_ms = mbd.BAR_MS["1h"]
    start = 1_700_000_000_000 - 50 * bar_ms
    df = _alpaca_ohlcv(n_bars=50, bar_ms=bar_ms, start_ms=start)
    p = _write_parquet(df, tmp_path / "BTCUSDT_1h.parquet")
    spec = mbd.SeriesSpec(symbol="BTCUSDT", interval="1h", path=p, bucket="strategy_local")
    now = int(df["date"].iloc[-1].timestamp() * 1000) + 30 * 60_000
    r = mbd.detect_series(spec, now_ms=now, window_ms=7 * 24 * 60 * 60_000)
    assert r.schema_ok is True
    assert r.schema_missing == []
    assert r.rows_total == 50
    assert r.missing_bars_in_window == 0
    assert r.ok is True


def test_detect_alpaca_with_internal_gap(tmp_path: Path):
    bar_ms = mbd.BAR_MS["1h"]
    start = 1_700_000_000_000 - 50 * bar_ms
    df = _alpaca_ohlcv(n_bars=50, bar_ms=bar_ms, start_ms=start)
    # Inject a 4-bar gap by shifting timestamps at index 25 forward.
    df["date"] = pd.concat([
        df["date"].iloc[:25],
        df["date"].iloc[25:] + pd.Timedelta(milliseconds=5 * bar_ms),
    ]).reset_index(drop=True)
    p = _write_parquet(df, tmp_path / "ETHUSDT_1h.parquet")
    spec = mbd.SeriesSpec(symbol="ETHUSDT", interval="1h", path=p, bucket="strategy_local")
    now = int(df["date"].iloc[-1].timestamp() * 1000) + 30 * 60_000
    r = mbd.detect_series(spec, now_ms=now, window_ms=7 * 24 * 60 * 60_000)
    assert r.internal_gap is True
    assert r.missing_bars_in_window >= 4
    assert r.ok is False


def test_detect_feather_file(tmp_path: Path):
    """Freqtrade user_data exports as `.feather`; detector should read it."""
    import pandas as pd
    bar_ms = mbd.BAR_MS["30m"]
    start = 1_700_000_000_000 - 50 * bar_ms
    df = _alpaca_ohlcv(n_bars=50, bar_ms=bar_ms, start_ms=start)
    p = tmp_path / "BTCUSDT-30m.feather"
    df.to_feather(p)
    spec = mbd.SeriesSpec(symbol="BTCUSDT", interval="30m", path=p, bucket="freqtrade_user_data")
    now = int(df["date"].iloc[-1].timestamp() * 1000) + 30 * 60_000
    r = mbd.detect_series(spec, now_ms=now, window_ms=7 * 24 * 60 * 60_000)
    assert r.error is None
    assert r.schema_ok is True
    assert r.rows_total == 50
    assert r.ok is True


def test_detect_no_timestamp_column_is_schema_fail(tmp_path: Path):
    """A frame with OHLCV but neither `open_time` nor `date` must fail schema."""
    df = pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC"),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
    })
    p = _write_parquet(df, tmp_path / "BTCUSDT_1h.parquet")
    spec = mbd.SeriesSpec(symbol="BTCUSDT", interval="1h", path=p, bucket="ad_hoc")
    r = mbd.detect_series(spec, now_ms=1_700_000_000_000)
    assert r.schema_ok is False
    assert "open_time" in r.schema_missing
    assert r.ok is False