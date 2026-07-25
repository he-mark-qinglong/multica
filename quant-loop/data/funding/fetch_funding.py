"""Reusable Binance USDT-M perpetual funding-rate fetcher.

Pulls 8h funding history for any list of symbols over a rolling lookback window
via the public REST API on ``fapi.binance.com`` and writes both CSV and Parquet
into ``~/multica/quant-loop/data/funding/``.

Why this exists
---------------
The data-folder at ``~/multica/quant-loop/data/funding/`` is the canonical drop
location for funding-rate series consumed by the VPVR/funding-carry strategy
family (cycle-46 funding-rate-delta etc.). Prior ad-hoc scripts that lived
under ``scripts/`` coupled absolute start dates to the script invocation, had
a reporting bug (``fundingTime`` column dropped before the report was built),
and only wrote Parquet. This module fixes both and exposes a small Python API
that the prototype step (VPVR level + funding carry on 15m bars) can call
directly instead of shelling out.

Endpoint
--------
``GET https://fapi.binance.com/fapi/v1/fundingRate``

Query parameters used::

    symbol     str               e.g. BTCUSDT
    startTime  int (ms, UTC)     inclusive lower bound
    endTime    int (ms, UTC)     exclusive upper bound   (optional, we cap at lookback)
    limit      int (1..1000)     page size (Binance max = 1000)

Response (list of dicts)::

    { "symbol": "BTCUSDT",
      "fundingTime": 1784217600002,            # ms since epoch, UTC
      "fundingRate": "0.00006102",             # string, 1e-4 precision
      "markPrice":   "64677.70000000" }        # string, 8 decimals

Rate limits
-----------
Binance applies a ``REQUEST_WEIGHT`` budget (1200/min per IP) plus an explicit
``X-MBX-USED-WEIGHT-1M`` header. ``fundingRate`` is weight 1 per call, so 1000
pages fit comfortably under the cap. We default to a 60 ms sleep between pages
(~16 req/s = 1000 req/min ceiling but well below the 1200 weight budget) and
exponentially back off on 429 / 418.

Pagination
----------
Each page is fetched with ``startTime`` set to ``last_fundingTime + 1``.
Funding is paid every 8h at 00:00 / 08:00 / 16:00 UTC, so a 90-day window is
~90*3 = 270 rows => single page. A 4-year window is ~4380 rows => 5 pages.

Schema written to disk (matches existing ``*.parquet`` in the folder)::

    ts            datetime64[ms, UTC]    # = fundingTime
    symbol        str                    # e.g. BTCUSDT
    fundingRate   float64                # 0.0001 == 1 bps per 8h
    markPrice     float64                # mark price at fundingTime

Usage
-----
Python::

    from fetch_funding import fetch_funding
    res = fetch_funding(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"], days=90)
    print(res.symbols["BTCUSDT"].rows)

CLI::

    python3 fetch_funding.py \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT \\
        --days 90 \\
        --end 2026-07-17 \\
        --out-dir data/funding \\
        --format parquet,csv
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd
import requests

try:
    from _shared.paths import data_root
except ImportError:  # bare-script mode
    _QL = str(Path(__file__).resolve().parents[2])
    if _QL not in sys.path:
        sys.path.insert(0, _QL)
    from _shared.paths import data_root

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://fapi.binance.com"
PATH_FUNDING = "/fapi/v1/fundingRate"

PAGE_LIMIT = 1000           # Binance max for fundingRate endpoint
MAX_RETRIES = 6
BACKOFF_BASE_S = 1.0        # base for exponential backoff
REQUEST_TIMEOUT_S = 15.0
PAGE_SLEEP_S = 0.06         # ~16 req/s

# Funding cadence for BTC/ETH/SOL: every 8h at 00:00/08:00/16:00 UTC.
EXPECTED_FUNDING_BAR_MS = 8 * 60 * 60 * 1000
GAP_TOLERANCE_MS = 60 * 60 * 1000  # tolerate one missed payment

DEFAULT_OUT_DIR = str(data_root() / "funding" / "binance")
DEFAULT_DAYS = 90
DEFAULT_FORMATS: tuple[str, ...] = ("parquet", "csv")

USER_AGENT = "vpvr-funding-fetch/fetch_funding"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(ms: int) -> str:
    """Millisecond timestamp -> ISO-8601 UTC string."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _ms(dt: datetime) -> int:
    """datetime -> millisecond timestamp (UTC). Naive datetimes are assumed UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _normalise_symbols(symbols: str | Sequence[str]) -> list[str]:
    """Accept any of: 'BTCUSDT', 'BTCUSDT,ETHUSDT', ['BTCUSDT', 'ETHUSDT']."""
    if isinstance(symbols, str):
        parts = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        parts = [str(s).strip().upper() for s in symbols if str(s).strip()]
    if not parts:
        raise ValueError("symbols must contain at least one non-empty entry")
    return parts


def _normalise_formats(formats: str | Sequence[str]) -> tuple[str, ...]:
    """Accept 'parquet,csv' or ['parquet','csv']; return a tuple."""
    if isinstance(formats, str):
        parts = [f.strip().lower() for f in formats.split(",") if f.strip()]
    else:
        parts = [str(f).strip().lower() for f in formats if str(f).strip()]
    bad = [f for f in parts if f not in ("parquet", "csv")]
    if bad:
        raise ValueError(f"unsupported formats: {bad} (allowed: parquet, csv)")
    if not parts:
        raise ValueError("formats must contain at least one of: parquet, csv")
    # de-dup while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for f in parts:
        if f not in seen:
            seen.add(f); out.append(f)
    return tuple(out)


# ---------------------------------------------------------------------------
# Core fetch + dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SymbolFetchResult:
    symbol: str
    rows: int
    first_fundingTime_ms: int
    last_fundingTime_ms: int
    first_iso: str
    last_iso: str
    max_gap_bars: int
    boundary_misalign_count: int
    coverage: float
    parquet_path: str | None
    csv_path: str | None
    elapsed_s: float
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FetchResult:
    endpoint: str
    start_iso: str
    end_iso: str
    days: int
    formats: tuple[str, ...]
    out_dir: str
    symbols: dict[str, SymbolFetchResult] = field(default_factory=dict)
    overall_ok: bool = True

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "start_iso": self.start_iso,
            "end_iso": self.end_iso,
            "days": self.days,
            "formats": list(self.formats),
            "out_dir": self.out_dir,
            "overall_ok": self.overall_ok,
            "symbols": {s: r.to_dict() for s, r in self.symbols.items()},
        }


def _fetch_one_page(
    session: requests.Session,
    symbol: str,
    start_ms: int,
    end_ms: int,
    request_timeout_s: float = REQUEST_TIMEOUT_S,
) -> list[dict]:
    """Fetch a single page; retries with exponential backoff on 418/429/transport errors."""
    params = {"symbol": symbol, "limit": PAGE_LIMIT,
              "startTime": start_ms, "endTime": end_ms}
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(BASE_URL + PATH_FUNDING, params=params,
                            timeout=request_timeout_s)
            if r.status_code == 200:
                payload = r.json()
                if not isinstance(payload, list):
                    raise RuntimeError(f"unexpected payload type {type(payload).__name__}")
                return payload
            if r.status_code in (418, 429):
                wait = BACKOFF_BASE_S * (4 ** attempt)
                print(f"  rate limit hit for {symbol} (status {r.status_code}); "
                      f"sleeping {wait:.1f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
        except requests.RequestException as e:
            last_err = e
            wait = BACKOFF_BASE_S * (2 ** attempt)
            print(f"  request failed for {symbol} (attempt {attempt + 1}/{MAX_RETRIES}): "
                  f"{e}; sleeping {wait:.1f}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"funding fetch failed for {symbol} after "
                       f"{MAX_RETRIES} retries: {last_err}")


def _walk_symbol(
    session: requests.Session,
    symbol: str,
    start_ms: int,
    end_ms: int,
    request_timeout_s: float = REQUEST_TIMEOUT_S,
) -> pd.DataFrame:
    """Page forward from start_ms until end_ms, accumulating rows."""
    all_rows: list[dict] = []
    cursor_ms = start_ms
    while cursor_ms < end_ms:
        rows = _fetch_one_page(session, symbol, cursor_ms, end_ms,
                               request_timeout_s=request_timeout_s)
        if not rows:
            break
        # Binance returns rows ascending by fundingTime already; filter strictly < end.
        rows = [r for r in rows
                if start_ms <= int(r["fundingTime"]) < end_ms]
        if not rows:
            break
        all_rows.extend(rows)
        next_cursor = int(rows[-1]["fundingTime"]) + 1
        if next_cursor <= cursor_ms:
            break
        cursor_ms = next_cursor
        time.sleep(PAGE_SLEEP_S)
    if not all_rows:
        raise RuntimeError(f"no funding rows returned for {symbol} in "
                           f"[{_iso(start_ms)} .. {_iso(end_ms)}]")
    df = pd.DataFrame(all_rows)
    # Type casting — Binance returns fundingRate/markPrice as strings.
    df["fundingTime"] = pd.to_numeric(df["fundingTime"], errors="raise").astype("int64")
    df["fundingRate"] = pd.to_numeric(df["fundingRate"], errors="raise").astype("float64")
    df["markPrice"]   = pd.to_numeric(df["markPrice"],   errors="raise").astype("float64")
    df["symbol"]      = df["symbol"].astype(str)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df = df[["ts", "symbol", "fundingRate", "markPrice"]]
    df = df.sort_values("ts").drop_duplicates(subset=["symbol", "ts"],
                                               keep="last").reset_index(drop=True)
    return df


def _validate(df: pd.DataFrame) -> tuple[int, int]:
    """Return (max_gap_in_units_of_8h, boundary_misalign_count).

    Funding cadence is every 8h UTC. We tolerate a single missed payment.
    """
    if df.empty:
        return 0, 0
    diffs = df["ts"].diff().dt.total_seconds().mul(1000).fillna(EXPECTED_FUNDING_BAR_MS)
    gap_units = (diffs / EXPECTED_FUNDING_BAR_MS).round().astype("int64")
    max_gap = int(gap_units.max())
    # Boundary check: each ts should fall on an 8h grid (UTC).
    boundary_misalign = int(
        (df["ts"].astype("int64") % EXPECTED_FUNDING_BAR_MS).abs().gt(GAP_TOLERANCE_MS).sum()
    )
    return max_gap, boundary_misalign


def _write_outputs(
    df: pd.DataFrame,
    symbol: str,
    out_dir: Path,
    formats: Iterable[str],
) -> tuple[str | None, str | None]:
    """Write the requested formats. Returns (parquet_path, csv_path)."""
    parquet_path: str | None = None
    csv_path: str | None = None
    for fmt in formats:
        if fmt == "parquet":
            p = out_dir / f"{symbol}.parquet"
            df.to_parquet(p, engine="pyarrow", index=False)
            parquet_path = str(p)
        elif fmt == "csv":
            c = out_dir / f"{symbol}.csv"
            # ISO-8601 UTC string in CSV keeps the column human-readable
            # without losing precision (epoch-ms would also work).
            df_csv = df.copy()
            df_csv["ts"] = df_csv["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
            df_csv.to_csv(c, index=False)
            csv_path = str(c)
    return parquet_path, csv_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_funding(
    symbols: str | Sequence[str],
    days: int = DEFAULT_DAYS,
    end: datetime | str | None = None,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    formats: str | Sequence[str] = DEFAULT_FORMATS,
    write_report: bool = True,
    request_timeout_s: float = REQUEST_TIMEOUT_S,
    extra_session_headers: dict | None = None,
) -> FetchResult:
    """Fetch Binance USDT-M funding history for ``symbols`` over ``days``.

    Parameters
    ----------
    symbols : str | Sequence[str]
        One symbol (``'BTCUSDT'``), a comma-separated list (``'BTCUSDT,ETHUSDT'``)
        or a list/tuple of strings. Whitespace is stripped, casing normalised.
    days : int
        Lookback window in days ending at ``end`` (or now if ``end`` is None).
        Defaults to 90 to match the work-pool's "last 90 days" requirement.
    end : datetime | str | None
        Upper bound (exclusive) for fundingTime. ``None`` -> now UTC.
        Accepts ISO-8601 strings (e.g. ``'2026-07-17'`` or ``'2026-07-17T00:00:00Z'``).
    out_dir : str | Path
        Directory for ``{SYMBOL}.parquet`` / ``{SYMBOL}.csv`` outputs and the
        ``fetch_report_funding.json`` summary.
    formats : str | Sequence[str]
        Any subset of ``'parquet'`` and ``'csv'``.
    write_report : bool
        If True (default), also emits ``fetch_report_funding.json`` next to
        the data files.
    request_timeout_s : float
        Per-request HTTP timeout in seconds.
    extra_session_headers : dict | None
        Optional dict merged into the requests session (e.g. custom User-Agent).

    Returns
    -------
    FetchResult
        With one ``SymbolFetchResult`` per symbol (including any errors) and
        an ``overall_ok`` flag (True if every symbol has rows > 0 and
        coverage >= 0.95 of expected 8h cadence).
    """
    symbols_clean = _normalise_symbols(symbols)
    formats_clean = _normalise_formats(formats)
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    end_dt: datetime
    if end is None:
        end_dt = datetime.now(timezone.utc).replace(microsecond=0)
    elif isinstance(end, str):
        s = end.replace("Z", "+00:00")
        end_dt = datetime.fromisoformat(s)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
    else:
        end_dt = end
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
    end_ms = _ms(end_dt)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000

    expected_rows = int(days * 24 * 60 * 60 * 1000 / EXPECTED_FUNDING_BAR_MS)  # ~days*3
    result = FetchResult(
        endpoint=BASE_URL + PATH_FUNDING,
        start_iso=_iso(start_ms), end_iso=_iso(end_ms),
        days=days, formats=formats_clean, out_dir=str(out_dir_path),
    )

    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT, **(extra_session_headers or {})})
        for sym in symbols_clean:
            t0 = time.time()
            print(f"[fund] {sym} lookback={days}d end={_iso(end_ms)}", file=sys.stderr)
            try:
                df = _walk_symbol(session, sym, start_ms, end_ms,
                                  request_timeout_s=request_timeout_s)
            except Exception as e:
                print(f"[fund] {sym}: FAILED {e}", file=sys.stderr)
                r = SymbolFetchResult(
                    symbol=sym, rows=0, first_fundingTime_ms=0, last_fundingTime_ms=0,
                    first_iso="", last_iso="", max_gap_bars=0,
                    boundary_misalign_count=0, coverage=0.0,
                    parquet_path=None, csv_path=None, elapsed_s=time.time() - t0,
                    error=str(e),
                )
                result.symbols[sym] = r
                result.overall_ok = False
                continue
            elapsed = time.time() - t0
            max_gap, misalign = _validate(df)
            parquet_path, csv_path = _write_outputs(df, sym, out_dir_path, formats_clean)
            coverage = len(df) / max(1, expected_rows)
            sym_res = SymbolFetchResult(
                symbol=sym, rows=int(len(df)),
                first_fundingTime_ms=int(df["ts"].iloc[0].timestamp() * 1000),
                last_fundingTime_ms=int(df["ts"].iloc[-1].timestamp() * 1000),
                first_iso=str(df["ts"].iloc[0].isoformat()),
                last_iso=str(df["ts"].iloc[-1].isoformat()),
                max_gap_bars=max_gap, boundary_misalign_count=misalign,
                coverage=round(coverage, 4),
                parquet_path=parquet_path, csv_path=csv_path,
                elapsed_s=round(elapsed, 2),
            )
            print(f"[fund] {sym}: {sym_res.rows} rows, gap={max_gap}, "
                  f"misalign={misalign}, coverage={coverage:.2%}, "
                  f"{elapsed:.1f}s -> parquet={parquet_path} csv={csv_path}",
                  file=sys.stderr)
            result.symbols[sym] = sym_res
            if sym_res.rows == 0 or coverage < 0.95 or max_gap > 1 or misalign > 0:
                result.overall_ok = False

    if write_report:
        rp = out_dir_path / "fetch_report_funding.json"
        rp.write_text(json.dumps(result.to_dict(), indent=2))
        print(f"[fund] report: {rp}", file=sys.stderr)
    print(f"[fund] overall_ok={result.overall_ok}", file=sys.stderr)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT",
                   help="Comma-separated list of USDT-M perp symbols (default: %(default)s)")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help=f"Lookback window in days ending at --end "
                        f"(default: {DEFAULT_DAYS})")
    p.add_argument("--end", default=None,
                   help="ISO-8601 end timestamp (UTC). Default: now.")
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                   help=f"Output directory (default: {DEFAULT_OUT_DIR})")
    p.add_argument("--format", dest="formats", default=",".join(DEFAULT_FORMATS),
                   help="Comma-separated subset of parquet,csv (default: parquet,csv)")
    p.add_argument("--no-report", action="store_true",
                   help="Skip writing fetch_report_funding.json")
    p.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT_S,
                   help="Per-request HTTP timeout in seconds "
                        f"(default: {REQUEST_TIMEOUT_S})")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    res = fetch_funding(
        symbols=args.symbols,
        days=args.days,
        end=args.end,
        out_dir=args.out_dir,
        formats=args.formats,
        write_report=not args.no_report,
        request_timeout_s=args.timeout,
    )
    return 0 if res.overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
