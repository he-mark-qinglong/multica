"""Tests for ``scripts.freshness_dashboard``.

Covers the freshness budget math, classification, symlink/stub
detection, end-to-end CLI behavior, and HTML rendering. Runs entirely
on synthetic parquet files written under ``tmp_path`` — no real
``quant-loop`` workspace required.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from freshness_dashboard import (  # noqa: E402  (path tweak above)
    DEFAULT_FIND_ARGS,
    FreshnessReport,
    STALENESS_BUDGET_MS,
    _fmt_age,
    audit_freshness,
    audit_path,
    classify,
    enumerate_data_files,
    enumerate_data_files_walk,
    main,
    render_html,
    rollup,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_parquet(path: Path, *, last_open_time_ms: int, interval: str = "1m",
                   rows: int = 200) -> None:
    """Write a small synthetic OHLCV parquet with ``open_time`` covering
    ``interval`` bars up to ``last_open_time_ms``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    bar_ms_map = {
        "1m": 60_000, "5m": 5 * 60_000, "15m": 15 * 60_000,
        "30m": 30 * 60_000, "1h": 60 * 60_000, "2h": 2 * 60 * 60_000,
        "4h": 4 * 60 * 60_000, "1d": 24 * 60 * 60_000,
    }
    step = bar_ms_map[interval]
    open_times = [last_open_time_ms - (rows - 1 - i) * step for i in range(rows)]
    df = pd.DataFrame({
        "open_time": open_times,
        "open": [100.0 + i * 0.01 for i in range(rows)],
        "high": [101.0 + i * 0.01 for i in range(rows)],
        "low":  [99.0 + i * 0.01 for i in range(rows)],
        "close": [100.5 + i * 0.01 for i in range(rows)],
        "volume": [10.0 + i for i in range(rows)],
    })
    df.to_parquet(path)


def _write_funding_parquet(path: Path, *, last_funding_ms: int, rows: int = 50) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "fundingTime": [last_funding_ms - (rows - 1 - i) * 8 * 60 * 60_000
                        for i in range(rows)],
        "fundingRate": [0.0001 * (i % 5 - 2) for i in range(rows)],
        "symbol": ["BTCUSDT"] * rows,
    })
    df.to_parquet(path)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A minimal workspace mimicking the §2.1 / §2.2 shared pool."""
    ws = tmp_path / "quant-loop"
    (ws / "live_data").mkdir(parents=True)
    (ws / "data" / "perp_1m").mkdir(parents=True)
    (ws / "data" / "perp_2h").mkdir(parents=True)
    (ws / "data" / "perp_30m").mkdir(parents=True)
    (ws / "data" / "funding").mkdir(parents=True)
    # Avoid pytest's cache / __pycache__ being scanned.
    return ws


@pytest.fixture
def now_ms() -> int:
    """A fixed 'now' anchored to 2026-07-26T08:30:00 UTC."""
    return 1784351400000


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------


def test_classify_shared_pool_live_data(workspace: Path):
    p = workspace / "live_data" / "BTCUSDT_15m.parquet"
    assert classify(p, workspace) == ("BTCUSDT", "15m", "shared_pool")


def test_classify_shared_pool_perp_1m(workspace: Path):
    p = workspace / "data" / "perp_1m" / "ETHUSDT_1m.parquet"
    assert classify(p, workspace) == ("ETHUSDT", "1m", "shared_pool")


def test_classify_funding(workspace: Path):
    p = workspace / "data" / "funding" / "BTCUSDT.parquet"
    assert classify(p, workspace) == ("BTCUSDT", "funding", "funding")


def test_classify_strategy_local_double_underscore(workspace: Path):
    p = workspace / "strategies" / "vpvr_v1" / "data" / "BTCUSDT__1m.parquet"
    assert classify(p, workspace) == ("BTCUSDT", "1m", "strategy_local")


def test_classify_strategy_local_fapi(workspace: Path):
    p = workspace / "strategies" / "vpvr_v1" / "data" / "fapi_BTCUSDT__1m.parquet"
    assert classify(p, workspace) == ("BTCUSDT", "1m", "strategy_local")


def test_classify_freqtrade_feather(workspace: Path):
    p = workspace / "freqtrade_v10" / "user_data" / "data" / "BTCUSDT-30m.feather"
    assert classify(p, workspace) == ("BTCUSDT", "30m", "freqtrade_user_data")


def test_classify_unknown_filename(workspace: Path):
    p = workspace / "live_data" / "notes.txt"
    sym, interval, bucket = classify(p, workspace)
    assert sym is None and interval is None
    assert bucket == "unknown"


# ---------------------------------------------------------------------------
# audit_path() — single-file verdicts
# ---------------------------------------------------------------------------


def test_audit_path_fresh_within_budget(workspace: Path, now_ms: int):
    """A 15m file whose last bar is 10 min old is still inside the
    20-min budget -> ``fresh``.
    """
    p = workspace / "live_data" / "BTCUSDT_15m.parquet"
    _write_parquet(p, last_open_time_ms=now_ms - 10 * 60_000, interval="15m")
    r = audit_path(p, workspace=workspace, now_ms=now_ms)
    assert r.status == "fresh"
    assert r.symbol == "BTCUSDT"
    assert r.interval == "15m"
    assert r.age_ms == 10 * 60_000
    assert r.budget_ms == STALENESS_BUDGET_MS["15m"]


def test_audit_path_stale_past_budget(workspace: Path, now_ms: int):
    """A 15m file whose last bar is 90 min old -> ``stale``."""
    p = workspace / "live_data" / "ETHUSDT_15m.parquet"
    _write_parquet(p, last_open_time_ms=now_ms - 90 * 60_000, interval="15m")
    r = audit_path(p, workspace=workspace, now_ms=now_ms)
    assert r.status == "stale"
    assert r.age_ms == 90 * 60_000
    assert "age=" in r.note


def test_audit_path_stale_at_exact_budget_boundary(workspace: Path, now_ms: int):
    """``age == budget`` should be ``fresh`` (inclusive)."""
    p = workspace / "live_data" / "SOLUSDT_15m.parquet"
    budget = STALENESS_BUDGET_MS["15m"]  # 20 min
    _write_parquet(p, last_open_time_ms=now_ms - budget, interval="15m")
    r = audit_path(p, workspace=workspace, now_ms=now_ms)
    assert r.status == "fresh"


def test_audit_path_missing_path(workspace: Path, now_ms: int):
    p = workspace / "live_data" / "DOES_NOT_EXIST.parquet"
    r = audit_path(p, workspace=workspace, now_ms=now_ms)
    assert r.status == "missing"
    assert "does not exist" in r.note


def test_audit_path_stub_below_size_floor(workspace: Path, now_ms: int):
    p = workspace / "live_data" / "BTCUSDT_15m.parquet"
    p.write_bytes(b"x")  # 1 byte — below SIZE_FLOOR_BYTES
    r = audit_path(p, workspace=workspace, now_ms=now_ms)
    assert r.status == "missing"
    assert "stub" in r.note


def test_audit_path_symlink_flagged_not_hard_failed(workspace: Path, now_ms: int):
    """Symlinks are reported (``status=symlink``) but never hard-fail
    on their own — the SMA-34855 BTCUSDT_4h -> BTCUSD_4h bug must
    stay visible, not crash the run.
    """
    target = workspace / "live_data" / "BTCUSDT_4h.parquet"
    _write_parquet(target, last_open_time_ms=now_ms - 10 * 60_000, interval="4h")
    link = workspace / "live_data" / "BTCUSDT_4h_link.parquet"
    link.symlink_to(target)
    r = audit_path(link, workspace=workspace, now_ms=now_ms)
    assert r.status == "symlink"
    assert r.is_symlink
    assert r.symlink_target == str(target)


def test_audit_path_unknown_schema_unreadable_column(workspace: Path, now_ms: int):
    """A parquet whose columns don't include ``open_time``/``date``
    should be reported as ``unknown``, not ``missing``.
    """
    p = workspace / "live_data" / "WEIRD_15m.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"foo": [1, 2, 3], "bar": [4, 5, 6]}).to_parquet(p)
    r = audit_path(p, workspace=workspace, now_ms=now_ms)
    assert r.status == "unknown"


def test_audit_path_funding_uses_8h_cadence_budget(workspace: Path, now_ms: int):
    """Funding has its own budget (9h), not the OHLCV budget for its stem."""
    p = workspace / "data" / "funding" / "BTCUSDT.parquet"
    _write_funding_parquet(p, last_funding_ms=now_ms - 5 * 60 * 60_000)  # 5h ago
    r = audit_path(p, workspace=workspace, now_ms=now_ms)
    assert r.interval == "funding"
    assert r.status == "fresh"
    assert r.budget_ms == STALENESS_BUDGET_MS["funding"]


# ---------------------------------------------------------------------------
# audit_freshness() — workspace-wide behaviour
# ---------------------------------------------------------------------------


def test_audit_freshness_reports_missing_expected_cells(workspace: Path, now_ms: int):
    """An empty workspace produces one ``missing`` report per EXPECTED_OHLCV."""
    reports = audit_freshness(workspace, now_ms=now_ms)
    statuses = [r.status for r in reports]
    # Every expected cell exists but is empty -> ``missing``.
    assert statuses.count("missing") == len(reports)
    # All cells are missing -> exit-equivalent hard fail.
    summary = rollup(reports)
    assert summary["by_status"]["missing"] > 0


def test_audit_freshness_mixes_buckets(workspace: Path, now_ms: int):
    """Files in different buckets are reported under their own bucket,
    not merged silently (AGENTS.md anti-pattern).
    """
    # Shared-pool file (live_data, fresh)
    shared = workspace / "live_data" / "BTCUSDT_15m.parquet"
    _write_parquet(shared, last_open_time_ms=now_ms - 5 * 60_000, interval="15m")
    # Strategy-local copy (different bucket, stale)
    strat_dir = workspace / "strategies" / "vpvr_v1" / "data"
    strat_dir.mkdir(parents=True)
    strat = strat_dir / "BTCUSDT__15m.parquet"
    _write_parquet(strat, last_open_time_ms=now_ms - 60 * 60_000, interval="15m")

    reports = audit_freshness(workspace, now_ms=now_ms,
                              files=[shared, strat])
    bucket_status = {(r.bucket, r.status) for r in reports}
    assert ("shared_pool", "fresh") in bucket_status
    assert ("strategy_local", "stale") in bucket_status


def test_audit_freshness_handles_zero_find_results(tmp_path: Path, now_ms: int):
    """Empty workspace, no ``files`` override -> still completes,
    every expected cell is ``missing``."""
    ws = tmp_path / "empty"
    (ws / "live_data").mkdir(parents=True)
    reports = audit_freshness(ws, now_ms=now_ms)
    assert reports
    assert all(r.status == "missing" for r in reports)


def test_audit_freshness_synthetic_workspace(workspace: Path, now_ms: int):
    """End-to-end on a hand-built workspace: live_data 15m fresh,
    live_data 1h stale, perp_1m fresh, funding fresh, perp_2h missing.
    """
    _write_parquet(workspace / "live_data" / "BTCUSDT_15m.parquet",
                   last_open_time_ms=now_ms - 5 * 60_000, interval="15m")
    _write_parquet(workspace / "live_data" / "ETHUSDT_1h.parquet",
                   last_open_time_ms=now_ms - 2 * 60 * 60_000, interval="1h")
    _write_parquet(workspace / "data" / "perp_1m" / "BTCUSDT_1m.parquet",
                   last_open_time_ms=now_ms - 60_000, interval="1m")
    _write_funding_parquet(workspace / "data" / "funding" / "BTCUSDT.parquet",
                           last_funding_ms=now_ms - 4 * 60 * 60_000)
    # perp_2h is intentionally absent.

    files = enumerate_data_files_walk(workspace)
    reports = audit_freshness(workspace, now_ms=now_ms, files=files)
    by_key = {(r.symbol, r.interval, r.bucket): r.status for r in reports}
    assert by_key[("BTCUSDT", "15m", "shared_pool")] == "fresh"
    assert by_key[("ETHUSDT", "1h", "shared_pool")] == "stale"
    assert by_key[("BTCUSDT", "1m", "shared_pool")] == "fresh"
    assert by_key[("BTCUSDT", "funding", "funding")] == "fresh"
    # At least one perp_2h expected cell is missing.
    assert by_key[("BTCUSDT", "2h", "shared_pool")] == "missing"


# ---------------------------------------------------------------------------
# enumerate_data_files / enumerate_data_files_walk
# ---------------------------------------------------------------------------


def test_enumerate_data_files_walk_matches_find(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "live_data").mkdir(parents=True)
    (ws / "data" / "perp_1m").mkdir(parents=True)
    (ws / "data" / "funding").mkdir(parents=True)
    # Mix of things to include / exclude.
    (ws / "live_data" / "BTCUSDT_15m.parquet").write_bytes(b"x" * 2048)
    (ws / "data" / "perp_1m" / "ETHUSDT_1m.parquet").write_bytes(b"x" * 2048)
    (ws / "data" / "funding" / "BTCUSDT.parquet").write_bytes(b"x" * 2048)
    # Excluded by extension.
    (ws / "live_data" / "notes.md").write_text("ignore me")
    (ws / "live_data" / "verify.py").write_text("# ignore")
    (ws / "live_data" / "verify.json").write_text("{}")
    (ws / "live_data" / "fetch.sh").write_text("#!/bin/bash")
    (ws / "live_data" / "dash.html").write_text("<html></html>")
    # Excluded by path.
    (ws / "live_data" / "__pycache__" / "foo.parquet").parent.mkdir(parents=True)
    (ws / "live_data" / "__pycache__" / "foo.parquet").write_bytes(b"x" * 2048)

    walk = enumerate_data_files_walk(ws)
    walk_rel = sorted(str(p.relative_to(ws)) for p in walk)
    assert walk_rel == [
        "data/funding/BTCUSDT.parquet",
        "data/perp_1m/ETHUSDT_1m.parquet",
        "live_data/BTCUSDT_15m.parquet",
    ]


def test_enumerate_data_files_uses_real_find(workspace: Path):
    """If ``find`` is on PATH (the common case), enumerate_data_files
    returns a list — exact contents depend on the workspace contents.
    """
    (workspace / "live_data" / "BTCUSDT_15m.parquet").write_bytes(b"x" * 2048)
    files = enumerate_data_files(workspace)
    assert any(str(p).endswith("BTCUSDT_15m.parquet") for p in files)


# ---------------------------------------------------------------------------
# rollup()
# ---------------------------------------------------------------------------


def test_rollup_counts_match_reports(workspace: Path, now_ms: int):
    _write_parquet(workspace / "live_data" / "BTCUSDT_15m.parquet",
                   last_open_time_ms=now_ms - 5 * 60_000, interval="15m")
    _write_parquet(workspace / "live_data" / "ETHUSDT_1h.parquet",
                   last_open_time_ms=now_ms - 90 * 60_000, interval="1h")
    reports = audit_freshness(workspace, now_ms=now_ms,
                              files=enumerate_data_files_walk(workspace))
    s = rollup(reports)
    assert s["total"] == len(reports)
    assert s["by_status"]["fresh"] >= 1
    assert s["by_status"]["stale"] >= 1
    assert s["by_status"]["missing"] >= 1  # many EXPECTED cells missing
    assert "shared_pool" in s["by_bucket"]
    assert "15m" in s["by_interval"]
    assert "1h" in s["by_interval"]


# ---------------------------------------------------------------------------
# render_html()
# ---------------------------------------------------------------------------


def test_render_html_contains_every_report_row(workspace: Path, now_ms: int):
    _write_parquet(workspace / "live_data" / "BTCUSDT_15m.parquet",
                   last_open_time_ms=now_ms - 5 * 60_000, interval="15m")
    reports = audit_freshness(workspace, now_ms=now_ms,
                              files=enumerate_data_files_walk(workspace))
    html = render_html(reports, rollup(reports),
                       generated_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    # Status legend is rendered.
    assert "fresh" in html
    assert "stale" in html
    assert "missing" in html
    # The audit-by-replication find command is quoted for replay.
    assert "find " in html
    assert "-path" in html
    # Every report's symbol appears at least once.
    for r in reports:
        assert r.symbol in html


def test_render_html_no_external_assets():
    """Self-contained: no external script/link/img tags."""
    from datetime import datetime, timezone
    html = render_html([], rollup([]), generated_at=datetime.now(timezone.utc))
    assert "<script" not in html
    assert "<link" not in html
    assert "<img" not in html


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_writes_json_and_html(tmp_path: Path, now_ms: int, capsys):
    ws = tmp_path / "ws"
    (ws / "live_data").mkdir(parents=True)
    _write_parquet(ws / "live_data" / "BTCUSDT_15m.parquet",
                   last_open_time_ms=now_ms - 5 * 60_000, interval="15m")
    json_out = tmp_path / "snap.json"
    html_out = tmp_path / "dash.html"
    rc = main([
        "--workspace", str(ws),
        "--json-out", str(json_out),
        "--html-out", str(html_out),
        "--now-ms", str(now_ms),
        "--quiet",
    ])
    # Stale or missing expected cells -> non-zero exit.
    assert rc == 1
    payload = json.loads(json_out.read_text())
    assert payload["workspace"] == str(ws)
    assert payload["files_seen"] >= 1
    assert "summary" in payload
    assert "reports" in payload
    assert html_out.exists()
    html = html_out.read_text()
    assert "freshness dashboard" in html.lower()


def test_cli_emits_json_to_stdout_when_no_out(capsys):
    """Without --json-out, the JSON snapshot is emitted on stdout."""
    from datetime import datetime, timezone
    import json
    rc = main(["--workspace", "/nonexistent", "--quiet"])
    captured = capsys.readouterr()
    # Workspace not found -> exit code 2, no JSON.
    assert rc == 2


def test_cli_exit_zero_when_everything_fresh(tmp_path: Path, now_ms: int):
    """A workspace where every expected cell is in-budget and present
    exits 0.
    """
    ws = tmp_path / "ws"
    (ws / "live_data").mkdir(parents=True)
    (ws / "data" / "perp_1m").mkdir(parents=True)
    (ws / "data" / "perp_2h").mkdir(parents=True)
    (ws / "data" / "perp_30m").mkdir(parents=True)
    (ws / "data" / "funding").mkdir(parents=True)
    # Write every expected cell with a recent bar.
    from freshness_dashboard import EXPECTED_OHLCV, EXPECTED_FUNDING
    from datetime import datetime, timezone
    for sym, interval, rel in EXPECTED_OHLCV:
        budget = STALENESS_BUDGET_MS[interval]
        # Last bar exactly 1 minute ago -> comfortably inside budget.
        last = now_ms - 60_000
        _write_parquet(ws / rel, last_open_time_ms=last, interval=interval)
    for sym, interval, rel in EXPECTED_FUNDING:
        last = now_ms - 60_000
        _write_funding_parquet(ws / rel, last_funding_ms=last)

    rc = main([
        "--workspace", str(ws),
        "--json-out", str(ws / "snap.json"),
        "--now-ms", str(now_ms),
        "--quiet",
    ])
    assert rc == 0, "every expected cell present and within budget must exit 0"


# ---------------------------------------------------------------------------
# _fmt_age — small humanizer sanity check
# ---------------------------------------------------------------------------


def test_fmt_age_humanizer():
    assert _fmt_age(45_000) == "45s"
    assert _fmt_age(3 * 60_000) == "3m"
    assert _fmt_age(2 * 60 * 60_000) == "2h0m"
    assert _fmt_age(26 * 60 * 60_000) == "1d2h"
    assert _fmt_age(0) == "0s"