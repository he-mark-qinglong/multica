"""Tests for backfill_aggtrades.py. Plain asserts, prints N/N passed.

Run: python3 scripts/test_backfill_aggtrades.py

Mocks urllib.request.urlopen so no network is hit. Builds synthetic aggTrades
JSON in the shape Binance returns, runs backfill for one day, verifies the
parquet shape.
"""
import datetime as dt
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Make scripts/ importable
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import pandas as pd

import backfill_aggtrades as bf

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")


def _fake_aggtrade(ts_ms: int, price: float, qty: float, is_maker: bool) -> dict:
    """One row in the shape Binance fapi/v1/aggTrades returns."""
    return {
        "a": ts_ms,        # aggregate trade id (use ts as a unique-ish id)
        "p": f"{price:.2f}",
        "q": f"{qty:.6f}",
        "f": ts_ms * 10,
        "l": ts_ms * 10 + 1,
        "T": ts_ms,
        "m": is_maker,
    }


def _make_fake_response(day_start: dt.datetime) -> list[dict]:
    """4 hourly windows, 3 trades each → 12 trades total for the day."""
    rows = []
    base_ts = int(day_start.timestamp() * 1000)
    for h in range(4):  # only first 4 windows get hits; rest return []
        for i in range(3):
            ts = base_ts + h * 3600_000 + i * 60_000
            rows.append(_fake_aggtrade(ts, 100.0 + i, 0.5, bool(i % 2)))
    return rows


def main():
    with tempfile.TemporaryDirectory() as tmp:
        out_root = Path(tmp) / "trades"
        start = dt.datetime(2024, 1, 1)
        end = start + dt.timedelta(days=1)  # exactly one day

        fake_payload = _make_fake_response(start)

        # Call-counter so we know how many times urlopen was hit.
        call_state = {"n": 0, "sleeps": 0}

        class _Ctx:
            def __init__(self, payload_bytes):
                self._buf = io.BytesIO(payload_bytes)
            def __enter__(self):
                return self._buf
            def __exit__(self, *a):
                return False

        def fake_urlopen(url, timeout=30):
            call_state["n"] += 1
            # First 4 windows get data, later windows empty — emulate sparse hours.
            # We can't easily map URL→window here without parsing; just return
            # all payload on the first call and empty list after 4 calls so the
            # day total is consistent. Tests below don't assert exact call count.
            if call_state["n"] <= 4:
                return _Ctx(json.dumps(fake_payload).encode())
            return _Ctx(b"[]")

        def fake_sleep(s):
            call_state["sleeps"] += 1

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("time.sleep", side_effect=fake_sleep):
            bf.backfill("BTCUSDT", start, end, out_root)

        # --- T1: partition path exists ---
        part = out_root / "symbol=BTCUSDT" / "date=2024-01-01" / "aggtrades.parquet"
        check("T1 parquet partition created", part.exists())

        # --- T2: shape — load and inspect
        df = pd.read_parquet(part)
        check("T2a is DataFrame", isinstance(df, pd.DataFrame))
        check("T2b non-empty", len(df) > 0)

        # --- T3: expected columns present ---
        expected = {"agg_id", "timestamp", "price", "qty", "first_id",
                    "last_id", "is_buyer_maker"}
        check("T3 expected columns", expected.issubset(set(df.columns)))

        # --- T4: dtypes / value sanity ---
        check("T4a price is float", df["price"].dtype.kind == "f")
        check("T4b qty is float", df["qty"].dtype.kind == "f")
        check("T4c timestamp is datetime", pd.api.types.is_datetime64_any_dtype(df["timestamp"]))
        check("T4d prices in expected range", df["price"].between(99, 103).all())

        # --- T5: idempotent — second run skips existing partition ---
        before_calls = call_state["n"]
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("time.sleep", side_effect=fake_sleep):
            bf.backfill("BTCUSDT", start, end, out_root)
        check("T5 idempotent (no extra calls on re-run)", call_state["n"] == before_calls)

        # --- T6: fetch_aggtrades handles 429 with retry (mocked) ---
        import urllib.error
        attempts = {"n": 0}

        class _HTTP429(urllib.error.HTTPError):
            def __init__(self):
                # minimal init; url, code, msg, hdrs, fp
                super().__init__("http://x", 429, "Too Many", {}, io.BytesIO(b"{}"))

        def urlopen_429(url, timeout=30):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise _HTTP429()
            return _Ctx(json.dumps(fake_payload).encode())

        with patch("urllib.request.urlopen", side_effect=urlopen_429), \
             patch("time.sleep", side_effect=fake_sleep):
            rows = bf.fetch_aggtrades("BTCUSDT", 0, 60_000)
        check("T6 fetch recovers from 429", isinstance(rows, list) and len(rows) > 0)

        # --- T7: constants per spec ---
        check("T7a weight per call is 20", bf.WEIGHT_PER_CALL == 20)
        check("T7b max calls/min is 60", bf.MAX_CALLS_PER_MIN == 60)
        check("T7c window is 1h", bf.WINDOW_MS == 3600_000)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
