"""Tests for scripts/collect_okx_book_ws.py — data-format correctness.

Covers the pure layer only (no network): books5 frame parsing, the raw
5-level JSONL record schema (bid_p1..ask_q5 flat columns + nested ladder),
dual timestamps via ``_shared/data/ingest_ts.py``, and daily file rotation.

Run:

    pytest scripts/test_okx_book_collector_ws.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent))

from collect_okx_book_ws import (  # noqa: E402
    LEVEL_COLUMNS,
    N_LEVELS,
    BookJsonlWriter,
    normalize_snapshot,
    parse_message,
)


def _frame(action: str = "snapshot", inst: str = "BTC-USDT-SWAP",
           ts: str = "1597026383085") -> str:
    """A realistic OKX books5 frame: levels are [px, sz, liq, n_orders]."""
    return json.dumps({
        "arg": {"channel": "books5", "instId": inst},
        "action": action,
        "data": [{
            "bids": [
                ["43210.0", "1.1", "0", "4"], ["43209.5", "0.7", "0", "2"],
                ["43208.0", "1.5", "0", "6"], ["43206.2", "0.9", "0", "3"],
                ["43205.0", "2.4", "0", "8"],
            ],
            "asks": [
                ["43211.1", "0.5", "0", "3"], ["43212.0", "1.2", "0", "5"],
                ["43213.5", "0.8", "0", "2"], ["43215.0", "2.0", "0", "7"],
                ["43216.9", "0.3", "0", "1"],
            ],
            "ts": ts,
            "checksum": -1430111963,
        }],
    })


# ---------------------------------------------------------------------------
# normalize_snapshot / parse_message
# ---------------------------------------------------------------------------

class TestParseMessage:
    def test_snapshot_frame_yields_one_row(self):
        rows = parse_message(_frame("snapshot"))
        assert len(rows) == 1
        symbol, row = rows[0]
        assert symbol == "BTC-USDT-SWAP"

    def test_record_schema_raw_levels_preserved(self):
        _, row = parse_message(_frame())[0]
        assert row["ts"] == 1597026383085
        assert row["ts_ns"] == 1597026383085 * 1_000_000
        assert row["symbol"] == "BTC-USDT-SWAP"
        assert row["checksum"] == -1430111963
        # raw ladder: 5 levels each side, best first, floats
        assert len(row["bids"]) == 5 and len(row["asks"]) == 5
        assert row["bids"][0] == [43210.0, 1.1]
        assert row["asks"][0] == [43211.1, 0.5]

    def test_flat_level_columns_match_factor_input_schema(self):
        _, row = parse_message(_frame())[0]
        # bid_p1..p5/q1..q5 + ask_p1..p5/q1..q5 — the orderbook_factors input
        assert len(LEVEL_COLUMNS) == 4 * N_LEVELS
        for col in LEVEL_COLUMNS:
            assert col in row
        assert row["bid_p1"] == 43210.0 and row["bid_q1"] == 1.1
        assert row["ask_p1"] == 43211.1 and row["ask_q1"] == 0.5
        assert row["bid_p5"] == 43205.0 and row["ask_p5"] == 43216.9

    def test_short_ladder_zero_padded(self):
        frame = json.loads(_frame())
        frame["data"][0]["bids"] = frame["data"][0]["bids"][:2]
        _, row = parse_message(json.dumps(frame))[0]
        assert len(row["bids"]) == 2
        assert row["bid_p3"] == 0.0 and row["bid_q5"] == 0.0
        assert row["bid_p1"] == 43210.0

    def test_update_action_also_parsed(self):
        assert len(parse_message(_frame("update"))) == 1

    def test_string_payloads_converted_to_float(self):
        _, row = parse_message(_frame())[0]
        assert isinstance(row["bid_p1"], float) and isinstance(row["bid_q1"], float)

    def test_subscribe_ack_and_pong_yield_nothing(self):
        ack = json.dumps({"event": "subscribe",
                          "arg": {"channel": "books5", "instId": "BTC-USDT-SWAP"}})
        assert parse_message(ack) == []
        assert parse_message("pong") == []

    def test_malformed_entry_raises(self):
        frame = json.loads(_frame())
        del frame["data"][0]["ts"]
        with pytest.raises(ValueError, match="malformed"):
            parse_message(json.dumps(frame))

    def test_multi_entry_frame_yields_multiple_rows(self):
        frame = json.loads(_frame())
        frame["data"].append(dict(frame["data"][0], ts="1597026383090"))
        rows = parse_message(json.dumps(frame))
        assert [r["ts"] for _, r in rows] == [1597026383085, 1597026383090]

    def test_normalize_snapshot_direct(self):
        entry = json.loads(_frame())["data"][0]
        row = normalize_snapshot(entry, "ETH-USDT-SWAP")
        assert row["symbol"] == "ETH-USDT-SWAP"
        assert row["ts"] == 1597026383085
        assert row["bid_p1"] == 43210.0 and row["ask_p5"] == 43216.9


# ---------------------------------------------------------------------------
# BookJsonlWriter — dual timestamps + daily rotation
# ---------------------------------------------------------------------------

class TestBookJsonlWriter:
    def test_file_path_by_exchange_day(self):
        p = BookJsonlWriter.file_path(Path("/x"), "SOL-USDT-SWAP", 1597026383085)
        assert p.parent == Path("/x")
        assert p.name.startswith("SOL_") and p.name.endswith(".jsonl")

    def test_flush_writes_dual_timestamped_jsonl(self, tmp_path):
        w = BookJsonlWriter(tmp_path)
        for ts in ("1597026383085", "1597026383090"):
            _, row = parse_message(_frame(ts=ts))[0]
            w.add("BTC-USDT-SWAP", row)
        n = w.flush(now_ms=1597026384000)
        assert n == 2 and w.n_written == 2 and w.pending() == 0
        path = BookJsonlWriter.file_path(tmp_path, "BTC-USDT-SWAP", 1597026383085)
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            rec = json.loads(line)
            assert rec["ingest_ts"] == 1597026384000  # local persist stamp
            assert rec["ingest_ts"] >= rec["ts"]      # latency measurable
            assert len(rec["bids"]) == 5

    def test_flush_empty_buffer_is_noop(self, tmp_path):
        w = BookJsonlWriter(tmp_path)
        assert w.flush() == 0
        assert list(tmp_path.glob("*.jsonl")) == []

    def test_rows_split_by_exchange_day(self, tmp_path):
        w = BookJsonlWriter(tmp_path)
        day1 = 1597017600000  # 2020-08-10T00:00:00Z
        day2 = day1 + 86_400_000  # next UTC day
        _, row1 = parse_message(_frame(ts=str(day1)))[0]
        _, row2 = parse_message(_frame(ts=str(day2)))[0]
        w.add("BTC-USDT-SWAP", row1)
        w.add("BTC-USDT-SWAP", row2)
        assert w.flush(now_ms=day2 + 5) == 2
        p1 = BookJsonlWriter.file_path(tmp_path, "BTC-USDT-SWAP", day1)
        p2 = BookJsonlWriter.file_path(tmp_path, "BTC-USDT-SWAP", day2)
        assert p1.exists() and p2.exists() and p1 != p2

    def test_multiple_symbols_separate_files(self, tmp_path):
        w = BookJsonlWriter(tmp_path)
        for inst in ("BTC-USDT-SWAP", "ETH-USDT-SWAP"):
            _, row = parse_message(_frame(inst=inst))[0]
            w.add(inst, row)
        w.flush(now_ms=1597026384000)
        names = {p.name for p in tmp_path.glob("*.jsonl")}
        assert any(n.startswith("BTC_") for n in names)
        assert any(n.startswith("ETH_") for n in names)

    def test_append_mode_across_flushes(self, tmp_path):
        w = BookJsonlWriter(tmp_path)
        _, row = parse_message(_frame())[0]
        w.add("BTC-USDT-SWAP", row)
        w.flush(now_ms=1)
        w.add("BTC-USDT-SWAP", row)
        w.flush(now_ms=2)
        path = BookJsonlWriter.file_path(tmp_path, "BTC-USDT-SWAP", 1597026383085)
        assert len(path.read_text().strip().split("\n")) == 2


# ---------------------------------------------------------------------------
# Supervisor wrapper wiring
# ---------------------------------------------------------------------------

class TestRunWrapper:
    def test_build_collector_cmd_points_at_worker(self):
        import run_okx_book_collector as wrapper

        cmd = wrapper.build_collector_cmd(proxy="http://127.0.0.1:7890",
                                          data_dir="/tmp/books")
        assert cmd[1].endswith("collect_okx_book_ws.py")
        assert "--proxy" in cmd and "--data-dir" in cmd

    def test_worker_script_exists(self):
        import run_okx_book_collector as wrapper

        assert wrapper.WORKER_SCRIPT.exists()
