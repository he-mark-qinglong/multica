"""Tests for _shared/data/stream_ingest.py (F16) — no live websocket."""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pandas as pd

from _shared.data.stream_ingest import (
    RingBuffer,
    StreamConfig,
    StreamIngester,
    aggregate_bars,
    parse_aggtrade,
)


def _trade(ts, price=100.0, qty=1.0, maker=False):
    return {"ts": ts, "price": price, "qty": qty, "is_buyer_maker": maker}


def test_parse_aggtrade_raw_and_envelope():
    payload = {"T": 1000, "p": "101.5", "q": "0.25", "m": True}
    t1 = parse_aggtrade(json.dumps(payload))
    assert t1 == {"ts": 1000, "price": 101.5, "qty": 0.25, "is_buyer_maker": True}
    t2 = parse_aggtrade(json.dumps({"stream": "btcusdt@aggTrade", "data": payload}))
    assert t2 == t1


def test_parse_aggtrade_bad():
    assert parse_aggtrade("not json") is None
    assert parse_aggtrade(json.dumps({"x": 1})) is None
    assert parse_aggtrade(json.dumps({"T": "a", "p": "1", "q": "1", "m": False})) is None


def test_ring_buffer_overflow_drops_oldest():
    rb = RingBuffer(maxlen=3)
    for i in range(5):
        rb.append(_trade(i))
    assert len(rb) == 3
    assert rb.dropped == 2
    items = rb.drain()
    assert [t["ts"] for t in items] == [2, 3, 4]
    assert len(rb) == 0


def test_aggregate_bars_ohlcv():
    trades = [
        _trade(0, price=100.0, qty=1.0),
        _trade(10_000, price=102.0, qty=2.0),
        _trade(20_000, price=99.0, qty=0.5),
        _trade(61_000, price=101.0, qty=1.5),  # second minute
    ]
    bars = aggregate_bars(trades, bar_ms=60_000)
    assert len(bars) == 2
    first = bars.iloc[0]
    assert first["timestamp"] == 0
    assert first["open"] == 100.0 and first["close"] == 99.0
    assert first["high"] == 102.0 and first["low"] == 99.0
    assert first["volume"] == 3.5 and first["trades"] == 3
    second = bars.iloc[1]
    assert second["timestamp"] == 60_000 and second["open"] == 101.0


def test_aggregate_bars_empty():
    bars = aggregate_bars([])
    assert len(bars) == 0
    assert "timestamp" in bars.columns


def _config(tmp_path):
    return StreamConfig(
        symbol="BTCUSDT", out_dir=str(tmp_path), bar_ms=60_000,
        flush_interval_ms=60_000, buffer_size=1000,
    )


def test_ingester_flush_writes_only_closed_bars(tmp_path):
    ing = StreamIngester(_config(tmp_path))
    # minute 0 trades
    ing.on_trade(_trade(1_000, price=100.0))
    ing.on_trade(_trade(30_000, price=101.0))
    # a trade 61s in triggers the flush check (now - last >= interval)
    out = ing.on_trade(_trade(61_000, price=102.0))
    assert out is not None and len(out) == 1  # only minute 0 is closed
    assert out.iloc[0]["timestamp"] == 0
    # the minute-1 trade was re-buffered, not persisted
    assert len(ing.buffer) == 1
    stored = pd.read_parquet(tmp_path / "BTCUSDT_1m.parquet")
    assert list(stored["timestamp"]) == [0]
    assert stored.iloc[0]["open"] == 100.0 and stored.iloc[0]["close"] == 101.0


def test_ingester_no_flush_before_interval(tmp_path):
    ing = StreamIngester(_config(tmp_path))
    assert ing.on_trade(_trade(1_000)) is None
    assert ing.on_trade(_trade(30_000)) is None  # only 29s elapsed
    assert not (tmp_path / "BTCUSDT_1m.parquet").exists()


def test_ingester_crash_recovery_and_resume(tmp_path):
    cfg = _config(tmp_path)
    ing1 = StreamIngester(cfg)
    ing1.on_trade(_trade(1_000, price=100.0))
    ing1.flush(now_ms=60_000)  # minute 0 closed and persisted
    assert ing1.last_persisted_ts() == 0

    # "restart": new ingester reads the high-water mark from disk
    ing2 = StreamIngester(cfg)
    assert ing2.last_persisted_ts() == 0
    ing2.on_trade(_trade(61_000, price=105.0))
    out = ing2.flush(now_ms=120_000)
    assert list(out["timestamp"]) == [60_000]
    stored = pd.read_parquet(tmp_path / "BTCUSDT_1m.parquet")
    assert list(stored["timestamp"]) == [0, 60_000]  # merged, no dupes


def test_ingester_refush_is_idempotent(tmp_path):
    ing = StreamIngester(_config(tmp_path))
    for t in (1_000, 2_000):
        ing.buffer.append(_trade(t))
    ing.flush(now_ms=60_000)
    # simulate a double-flush of the same trades (e.g. redelivery after crash)
    for t in (1_000, 2_000):
        ing.buffer.append(_trade(t))
    ing.flush(now_ms=60_000)
    stored = pd.read_parquet(tmp_path / "BTCUSDT_1m.parquet")
    assert len(stored) == 1


# --- run_ws: reconnect loop, pings, receipt timestamps (injected connector) ----
def test_backoff_delays_exponential_and_capped():
    from _shared.data.stream_ingest import backoff_delays

    it = backoff_delays(base_sec=1.0, factor=2.0, max_sec=8.0)
    assert [next(it) for _ in range(6)] == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


class _FakeWS:
    """Minimal async-websocket stand-in: yields payloads, then drops."""

    def __init__(self, payloads):
        self._payloads = list(payloads)

    async def recv(self):
        if not self._payloads:
            raise ConnectionError("socket dropped")
        return self._payloads.pop(0)


class _FakeConn:
    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *exc):
        return False


def _agg(ts, price="100.0", qty="1.0"):
    return json.dumps({"T": ts, "p": price, "q": qty, "m": False})


def test_run_ws_reconnects_after_drop_and_stamps_receipt(tmp_path):
    import asyncio

    from _shared.data.stream_ingest import run_ws

    attempts = {"n": 0}
    # connection 1: two trades (closes bar 0), then the socket drops;
    # connection 2: one trade in a later bar, then drops again.
    feeds = [
        [_agg(1_000), _agg(61_000)],
        [_agg(122_000)],
    ]

    def connector(url):
        attempts["n"] += 1
        if attempts["n"] <= len(feeds):
            return _FakeConn(_FakeWS(feeds[attempts["n"] - 1]))
        return _FakeConn(_FakeWS([]))  # every later connection drops at once

    cfg = _config(tmp_path)
    flushed = asyncio.run(
        run_ws(cfg, duration_sec=0.2, backoff_base_sec=0.001, connector=connector)
    )
    assert attempts["n"] >= 3  # reconnects happened after the drops
    assert flushed >= 1  # bar 0 was closed, flushed and persisted
    stored = pd.read_parquet(tmp_path / "BTCUSDT_1m.parquet")
    assert 0 in set(stored["timestamp"])


def test_run_ws_records_receipt_timestamp(tmp_path):
    import asyncio

    from _shared.data.stream_ingest import run_ws

    seen = {}
    orig_on_trade = StreamIngester.on_trade

    def spy(self, trade, now_ms=None):
        seen.setdefault("recv_ts", trade.get("recv_ts"))
        return orig_on_trade(self, trade, now_ms=now_ms)

    def connector(url):
        return _FakeConn(_FakeWS([_agg(1_000)]))

    StreamIngester.on_trade = spy
    try:
        asyncio.run(
            run_ws(_config(tmp_path), duration_sec=0.05,
                   backoff_base_sec=0.001, connector=connector)
        )
    finally:
        StreamIngester.on_trade = orig_on_trade
    assert isinstance(seen["recv_ts"], int) and seen["recv_ts"] > 0


def test_run_ws_max_reconnects_raises(tmp_path):
    import asyncio

    from _shared.data.stream_ingest import run_ws

    def dead_connector(url):
        raise ConnectionError("refused")

    import pytest as _pytest

    with _pytest.raises(ConnectionError):
        asyncio.run(
            run_ws(_config(tmp_path), duration_sec=0.0, backoff_base_sec=0.001,
                   max_reconnects=2, connector=dead_connector)
        )


# --- dual-timestamp (ingest_ts) on persisted bars (F-Data) -----------------
def test_flush_stamps_ingest_ts(tmp_path):
    """Persisted bars carry both exchange ``timestamp`` and ``ingest_ts``."""
    ing = StreamIngester(_config(tmp_path))
    ing.on_trade(_trade(1_000, price=100.0))
    ing.on_trade(_trade(30_000, price=101.0))
    out = ing.on_trade(_trade(61_000, price=102.0))
    assert out is not None and len(out) == 1
    # the write moment is the flush trigger time (61_000 ms)
    assert out.iloc[0]["ingest_ts"] == 61_000
    stored = pd.read_parquet(tmp_path / "BTCUSDT_1m.parquet")
    assert "ingest_ts" in stored.columns
    assert stored.iloc[0]["ingest_ts"] == 61_000
    # exchange time and ingest time differ as expected
    assert stored.iloc[0]["timestamp"] == 0
    assert stored.iloc[0]["ingest_ts"] == 61_000


def test_flush_ingest_ts_uses_wallclock_when_now_none(tmp_path):
    """Final drain (now_ms=None) stamps with the real wall-clock time."""
    ing = StreamIngester(_config(tmp_path))
    ing.buffer.append(_trade(1_000, price=100.0))
    out = ing.flush()  # no now_ms → wall-clock
    assert out is not None and len(out) == 1
    assert out.iloc[0]["ingest_ts"] > 1_600_000_000_000  # after 2020
    stored = pd.read_parquet(tmp_path / "BTCUSDT_1m.parquet")
    assert "ingest_ts" in stored.columns


def test_flush_idempotent_ingest_ts_on_reflush(tmp_path):
    """Re-flushing the same bars keeps the first ingest_ts (merge-dedupe)."""
    cfg = _config(tmp_path)
    ing = StreamIngester(cfg)
    ing.buffer.append(_trade(1_000))
    ing.flush(now_ms=60_000)  # first stamp: 60_000
    first = pd.read_parquet(tmp_path / "BTCUSDT_1m.parquet")
    assert first.iloc[0]["ingest_ts"] == 60_000
    # simulate redelivery
    ing.buffer.append(_trade(1_000))
    ing.flush(now_ms=120_000)  # would stamp 120_000 if not idempotent
    second = pd.read_parquet(tmp_path / "BTCUSDT_1m.parquet")
    assert len(second) == 1  # deduped
    assert second.iloc[0]["ingest_ts"] == 60_000  # original preserved
