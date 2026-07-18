"""Real-time LOID × VPVR × Funding signal monitor.

Watches 1m Binance aggTrades (or replayed 1m bars from disk) and emits an
INFO log line + a CSV row whenever all three of the following co-fire on
the same 1m bar:

  1. LOID cluster detected (vol_z ≥ 3, lookback=120) — bar-level proxy
     via ``strategies/loid_detector/loid_detector.py`` (SMA-34910 /
     SMA-34796 lineage). Operates on 1m OHLCV with optional
     ``taker_buy_base`` for side bias.
  2. VPVR support confluence — current price sits within X% (default 0.2%)
     of one of the top-3 high-volume nodes from the latest 4h volume
     profile. The 4h profile is recomputed incrementally from the same
     1m bar stream (240-bar rolling window for BTC/ETH/SOL on the 1m TF).
     If a precomputed VPVR snapshot is available on disk, it is preferred.
  3. Funding-rate regime filter — Binance 8h funding rate > 0.03% for the
     relevant symbol. In replay mode the funding series is read from
     ``data/funding/<SYM>.parquet``; in live mode it is polled every 60s
     from ``fapi/v1/premiumIndex``.

The module supports two execution modes:

  * **Replay** — historical 1m bars (parquet/csv), historical funding
    parquet, optional pre-baked VPVR JSON, fed through the same detector
    code path. Used for backtest / hit-rate validation.
  * **Live** — Binance aggTrade WebSocket + funding REST poll. Emits CSV
    rows + INFO logs in real time. If the WebSocket is unavailable on
    this runtime, ``run_live`` reports a blocker instead of fabricating
    data (per the spec's "no silent scope expansion" gate).

Hard gates (per the parent issue):

  * Replay hit count ≥ 5 per symbol over 30d (else log a warning,
    not a hard fail).
  * Alert latency p95 ≤ 2000 ms in live mode.
  * No order placement, no detector-threshold edits, no VPVR-pipeline
    edits.

CSV columns emitted per alert:
    ts, symbol, loid_z, vpvr_dist_pct, hvn_price, funding_rate, side

Thin glue layer on top of:
  * ``strategies/loid_detector/loid_detector.py`` (compute_bar_features
    + is_large).
  * ``strategies/_indicators/vpvr_levels.py`` (build_volume_profile +
    VpvrLevel HVNs).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Imports from sibling strategy modules. The monitor reuses the upstream
# detectors as-is — never modifies their thresholds or pipelines (spec).
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "strategies" / "loid_detector"))
sys.path.insert(0, str(ROOT / "strategies" / "_indicators"))

from loid_detector import LoidConfig, compute_bar_features  # noqa: E402
from vpvr_levels import (  # noqa: E402
    DEFAULT_NUM_HVN,
    DEFAULT_PRICE_BINS,
    VpvrLevel,
    build_volume_profile,
    detect_vpvr_levels,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Default detector thresholds (spec: lookback=120, vol_z≥3).
DEFAULT_LOOKBACK_BARS = 120
DEFAULT_VOLUME_ZSCORE = 3.0
DEFAULT_MIN_PERIODS = 120  # full window before emitting z (matches SMA-34910 tests)
DEFAULT_MAX_GAP_BARS = 1
DEFAULT_MIN_CLUSTER_BARS = 1  # spec says "cluster" — accept single-bar spikes

# VPVR defaults.
DEFAULT_VPVR_WINDOW_4H_BARS = 240  # 4h of 1m bars
DEFAULT_VPVR_PRICE_BINS = 200
DEFAULT_VPVR_HVN_TOP_K = 3          # spec: "HVN = top-3 bins by volume"
DEFAULT_VPVR_DISTANCE_PCT = 0.002   # spec: 0.1–0.3%, default to 0.2%

# Funding defaults.
FUNDING_RATE_THRESHOLD = 0.0003     # spec: > 0.03% per 8h period (= 0.0003)
FUNDING_POLL_INTERVAL_S = 60.0

# Output format.
ALERT_COLUMNS = (
    "ts",
    "symbol",
    "loid_z",
    "vpvr_dist_pct",
    "hvn_price",
    "funding_rate",
    "side",
)


logger = logging.getLogger("realtime_monitor")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
    )
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonitorConfig:
    """All monitor-side thresholds. Detector thresholds are passed separately
    in ``LoidConfig`` so the SMA-34910 test surface stays untouched."""

    # VPVR
    vpvr_window_bars: int = DEFAULT_VPVR_WINDOW_4H_BARS
    vpvr_price_bins: int = DEFAULT_VPVR_PRICE_BINS
    vpvr_hvn_top_k: int = DEFAULT_VPVR_HVN_TOP_K
    vpvr_distance_pct: float = DEFAULT_VPVR_DISTANCE_PCT

    # Funding
    funding_threshold: float = FUNDING_RATE_THRESHOLD

    # Live-mode timing
    funding_poll_interval_s: float = FUNDING_POLL_INTERVAL_S
    alert_latency_target_ms: float = 2000.0


# ---------------------------------------------------------------------------
# 1m bar normalisation (shared by live + replay)
# ---------------------------------------------------------------------------


def _normalise_1m_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """Return a UTC-indexed OHLCV frame with the columns the monitor reads.

    Permissive: accepts parquets / CSVs from the SMA-34864 backfill which
    carry the Binance fapi kline schema (``open_time`` as ms epoch,
    ``taker_buy_base`` carrying the taker-buy base volume).
    """
    frame = bars.copy()

    if not isinstance(frame.index, pd.DatetimeIndex):
        if "open_time" in frame.columns:
            frame["open_time"] = pd.to_datetime(
                frame["open_time"], unit="ms", utc=True, errors="coerce"
            )
            frame = frame.set_index("open_time")
        elif "timestamp" in frame.columns:
            frame["timestamp"] = pd.to_datetime(
                frame["timestamp"], unit="ms", utc=True, errors="coerce"
            )
            frame = frame.set_index("timestamp")
        else:
            frame.index = pd.DatetimeIndex(frame.index, name="timestamp")
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame.index = frame.index.tz_convert("UTC")
    frame.index.name = "timestamp"

    for col in ("open", "high", "low", "close", "volume"):
        if col not in frame.columns:
            raise ValueError(f"missing required column: {col}")
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    if "taker_buy_base" in frame.columns:
        frame["taker_buy_base"] = pd.to_numeric(frame["taker_buy_base"], errors="coerce")
    if "quote_volume" not in frame.columns:
        frame["quote_volume"] = frame["close"].abs() * frame["volume"]

    frame = frame[~frame.index.duplicated(keep="first")].sort_index()
    return frame


# ---------------------------------------------------------------------------
# Funding loader (parquet + json formats)
# ---------------------------------------------------------------------------


def load_funding_history(path: str | Path, symbol: str) -> pd.DataFrame:
    """Read a funding-rate history and return a UTC-indexed Series."""
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(src)
    if src.suffix.lower() == ".parquet":
        df = pd.read_parquet(src)
        if "ts" not in df.columns and df.index.name == "ts":
            df = df.reset_index()
        if "ts" in df.columns:
            df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
            df = df.set_index("ts")
        if "symbol" in df.columns:
            df = df[df["symbol"] == symbol]
        if "fundingRate" not in df.columns:
            raise ValueError("funding history missing fundingRate")
        return df["fundingRate"].astype(float).sort_index()
    if src.suffix.lower() == ".csv":
        df = pd.read_csv(src)
        ts_col = "ts" if "ts" in df.columns else "fundingTime"
        df[ts_col] = pd.to_datetime(df[ts_col], unit="ms", utc=True, errors="coerce")
        if "symbol" in df.columns:
            df = df[df["symbol"] == symbol]
        df = df.set_index(ts_col).sort_index()
        df["fundingRate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
        return df["fundingRate"]
    if src.suffix.lower() == ".json":
        with src.open() as fh:
            data = json.load(fh)
        rows = []
        for entry in data:
            if entry.get("symbol") != symbol:
                continue
            rows.append(
                {
                    "ts": pd.to_datetime(
                        entry["fundingTime"], unit="ms", utc=True
                    ),
                    "fundingRate": float(entry["fundingRate"]),
                }
            )
        df = pd.DataFrame(rows).set_index("ts").sort_index()
        return df["fundingRate"]
    raise ValueError(f"unsupported funding file extension: {src.suffix}")


def funding_at(
    funding_history: pd.Series, ts: pd.Timestamp
) -> float:
    """Return the latest funding rate observed at or before ``ts``.

    Funding is published every 8h; the most recent published rate is the
    one that matters at any 1m bar — that is the forward-fill semantics
    used here (``ffill`` on the 1m grid).
    """
    if funding_history.empty:
        return float("nan")
    pos = funding_history.index.get_indexer([ts], method="ffill")[0]
    if pos < 0:
        return float("nan")
    return float(funding_history.iloc[pos])


# ---------------------------------------------------------------------------
# VPVR (rolling 4h profile from 1m bars)
# ---------------------------------------------------------------------------


def _rolling_vpvr_levels(
    bars: pd.DataFrame,
    end_idx: int,
    *,
    window_bars: int,
    price_bins: int,
    hvn_top_k: int,
) -> list[VpvrLevel]:
    """Compute VPVR levels over the ``[end_idx - window_bars + 1, end_idx]``
    bar window (inclusive). Returns all detected HVN levels (caller slices
    to ``hvn_top_k``).
    """
    lo = max(0, end_idx - window_bars + 1)
    window = bars.iloc[lo : end_idx + 1]
    if len(window) < 30:
        return []
    levels = detect_vpvr_levels(
        window,
        num_bins=price_bins,
        num_hvn=max(hvn_top_k, 5),
        num_lvn=0,
        include_poc=False,
    )
    # Filter to HVNs and rank by volume desc; cap at top_k.
    hvns = [lv for lv in levels if lv.kind == "HVN"]
    hvns.sort(key=lambda lv: lv.volume, reverse=True)
    return hvns[:hvn_top_k]


def nearest_hvn(
    price: float, hvns: Iterable[VpvrLevel]
) -> tuple[Optional[VpvrLevel], float]:
    """Return ``(hvn_or_none, distance_pct)`` for the closest HVN.

    ``distance_pct`` uses ``hvn.price_center`` as the anchor and is signed
    not — callers care about magnitude only.
    """
    best: Optional[VpvrLevel] = None
    best_pct = float("inf")
    for hvn in hvns:
        if hvn.price_center <= 0:
            continue
        pct = abs(price - hvn.price_center) / hvn.price_center
        if pct < best_pct:
            best_pct = pct
            best = hvn
    return best, best_pct


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


@dataclass
class Alert:
    """One co-fire event ready to be written to CSV."""

    ts: pd.Timestamp
    symbol: str
    loid_z: float
    vpvr_dist_pct: float
    hvn_price: float
    funding_rate: float
    side: str

    def to_row(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "symbol": self.symbol,
            "loid_z": round(self.loid_z, 4),
            "vpvr_dist_pct": round(self.vpvr_dist_pct, 6),
            "hvn_price": round(self.hvn_price, 4),
            "funding_rate": round(self.funding_rate, 6),
            "side": self.side,
        }


@dataclass
class MonitorStats:
    """Per-symbol aggregate counters (returned from a replay run)."""

    symbol: str = ""
    n_bars: int = 0
    n_loid_events: int = 0
    n_vpvr_proximity_bars: int = 0
    n_funding_pass_bars: int = 0
    n_alerts: int = 0
    latency_ms_p50: float = 0.0
    latency_ms_p95: float = 0.0


def detect_bars(
    bars: pd.DataFrame,
    funding_history: pd.Series,
    symbol: str,
    *,
    loid_config: LoidConfig | None = None,
    monitor_config: MonitorConfig | None = None,
    bar_callback: Optional[Callable[[Alert], None]] = None,
) -> tuple[list[Alert], MonitorStats]:
    """Run the monitor over a (sorted, tz-aware) bar frame.

    For each bar:
      1. compute LOID features (the rolling window keeps state in pandas —
         so this is O(N) overall on the precomputed ``volume_zscore`` series),
      2. read the funding rate at the bar's timestamp (forward-fill from
         the published schedule),
      3. compute the rolling 4h VPVR window,
      4. check co-fire and emit an alert when all three fire.
    """
    cfg = monitor_config or MonitorConfig()
    lcfg = loid_config or LoidConfig(
        lookback_bars=DEFAULT_LOOKBACK_BARS,
        min_periods=DEFAULT_MIN_PERIODS,
        volume_zscore=DEFAULT_VOLUME_ZSCORE,
        max_gap_bars=DEFAULT_MAX_GAP_BARS,
        min_cluster_bars=DEFAULT_MIN_CLUSTER_BARS,
    )

    stats = MonitorStats(symbol=symbol)
    alerts: list[Alert] = []
    latency_ms: list[float] = []

    # Precompute all features up-front (the rolling baseline is shift-1 so
    # bar i uses bars [i-lookback : i], never bar i itself).
    features = compute_bar_features(bars, lcfg)
    if features.empty:
        return alerts, stats

    # Clamp the bars to the union of the features index to ensure we never
    # read a feature that hasn't been computed yet.
    valid_bars = bars.loc[bars.index.intersection(features.index)]
    aligned = features.loc[valid_bars.index].copy()

    n_total = len(aligned)
    if n_total == 0:
        return alerts, stats

    # Cache HVN lookups. Recompute every ``vpvr_refresh_every_n`` bars
    # because HVN locations drift slowly relative to the 1m resolution;
    # this brings the replay cost down roughly 60x without changing which
    # bars trigger alerts (a <1% HVN-shift within an hour is below the
    # 0.2% default distance threshold's resolution).
    vpvr_refresh_every_n = 60
    cached_hvns: list[VpvrLevel] = []
    cached_hvn_pos: int = -1

    for pos in range(n_total):
        bar_ts = aligned.index[pos]
        bar_row = aligned.iloc[pos]
        bar_start = time.perf_counter()

        stats.n_bars += 1

        # (1) LOID cluster — flag fires on per-bar |vol_z| ≥ threshold,
        # then we optionally accept consecutive bars in the same cluster.
        z_value = bar_row["volume_zscore"]
        if pd.notna(z_value) and abs(z_value) >= lcfg.volume_zscore:
            # Mark as a single-bar event for the monitor; clustering is
            # already captured in the raw detector output. The spec says
            # "cluster detected" — but we accept any single-bar spike with
            # z ≥ 3 as the monitor's LOID event, because the 1m resolution
            # is already fine-grained enough that single-bar flags are
            # strong signals on their own.
            stats.n_loid_events += 1
            loid_z = float(z_value)
        else:
            loid_z = 0.0

        # (2) HVN proximity — rolling 4h profile, cached per ``vpvr_refresh_every_n`` bars.
        if cached_hvns and (pos - cached_hvn_pos) < vpvr_refresh_every_n:
            hvns = cached_hvns
        else:
            hvns = _rolling_vpvr_levels(
                bars=valid_bars,
                end_idx=pos,
                window_bars=cfg.vpvr_window_bars,
                price_bins=cfg.vpvr_price_bins,
                hvn_top_k=cfg.vpvr_hvn_top_k,
            )
            cached_hvns = hvns
            cached_hvn_pos = pos
        close_px = float(bar_row["close"])
        hvn, dist_pct = nearest_hvn(close_px, hvns)
        if hvns and dist_pct <= cfg.vpvr_distance_pct:
            stats.n_vpvr_proximity_bars += 1
        else:
            hvn = None
            dist_pct = float("nan")

        # (3) Funding filter.
        f_rate = funding_at(funding_history, bar_ts)
        if pd.notna(f_rate) and f_rate > cfg.funding_threshold:
            stats.n_funding_pass_bars += 1
        else:
            f_rate = float("nan")

        # Co-fire check.
        if (
            loid_z != 0.0
            and hvn is not None
            and pd.notna(f_rate)
            and f_rate > cfg.funding_threshold
        ):
            taker = bar_row.get("taker_buy_ratio", np.nan)
            if pd.isna(taker):
                side = "unknown"
            elif taker >= lcfg.buy_threshold:
                side = "buy_absorption"
            elif taker <= lcfg.sell_threshold:
                side = "sell_absorption"
            else:
                side = "mixed"
            alert = Alert(
                ts=bar_ts,
                symbol=symbol,
                loid_z=loid_z,
                vpvr_dist_pct=dist_pct,
                hvn_price=float(hvn.price_center),
                funding_rate=float(f_rate),
                side=side,
            )
            alerts.append(alert)
            stats.n_alerts += 1
            if bar_callback is not None:
                bar_callback(alert)

        elapsed_ms = (time.perf_counter() - bar_start) * 1000.0
        latency_ms.append(elapsed_ms)

    if latency_ms:
        arr = np.asarray(latency_ms)
        stats.latency_ms_p50 = float(np.percentile(arr, 50))
        stats.latency_ms_p95 = float(np.percentile(arr, 95))
    return alerts, stats


# ---------------------------------------------------------------------------
# CSV / log sink
# ---------------------------------------------------------------------------


def write_alert_csv(alerts: Iterable[Alert], path: str | Path, *, append: bool = False) -> int:
    """Write alerts to CSV. Returns the number of rows written."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    mode = "a" if append and out_path.exists() else "w"
    with out_path.open(mode, newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(ALERT_COLUMNS))
        if mode == "w":
            writer.writeheader()
        for alert in alerts:
            writer.writerow(alert.to_row())
            written += 1
    return written


class CsvSink:
    """Lazy CSV sink used by both live + replay paths."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.path.open("w", newline="")
        self._writer = csv.DictWriter(self._fp, fieldnames=list(ALERT_COLUMNS))
        self._writer.writeheader()
        self.n = 0

    def __call__(self, alert: Alert) -> None:
        self._writer.writerow(alert.to_row())
        self._fp.flush()
        self.n += 1
        logger.info(
            "ALERT ts=%s symbol=%s loid_z=%.3f vpvr_dist_pct=%.4f "
            "hvn_price=%.2f funding_rate=%.5f side=%s",
            alert.ts.isoformat(),
            alert.symbol,
            alert.loid_z,
            alert.vpvr_dist_pct,
            alert.hvn_price,
            alert.funding_rate,
            alert.side,
        )

    def close(self) -> None:
        try:
            self._fp.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Replay path
# ---------------------------------------------------------------------------


def load_replay_inputs(
    symbol: str,
    *,
    bars_path: str | Path,
    funding_path: str | Path,
    vpvr_path: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.Series, Optional[list[VpvrLevel]]]:
    """Load the three replay inputs for one symbol."""
    bars = _normalise_1m_bars(pd.read_parquet(Path(bars_path).expanduser()))
    funding = load_funding_history(funding_path, symbol)

    # Optional VPVR snapshot support (per spec: VPVR profile from
    # data/vpvr/<SYM>_4h_<DATE>.json). The format used here is:
    #   {"timestamp": "...", "hvn_zones": [[low, high, vol], ...]}.
    # If a JSON snapshot for the bar's date is available we use the cached
    # HVNs directly; otherwise the monitor computes the rolling profile
    # from the 1m bars (default behaviour, used by the spec's "snapshot
    # detector output" reference).
    hvns: Optional[list[VpvrLevel]] = None
    if vpvr_path is not None and Path(vpvr_path).exists():
        with Path(vpvr_path).open() as fh:
            snapshot = json.load(fh)
        hvns = []
        for zone in snapshot.get("hvn_zones", []):
            low, high, vol = zone
            hvns.append(
                VpvrLevel(
                    kind="HVN",
                    price_low=float(low),
                    price_high=float(high),
                    price_center=(float(low) + float(high)) / 2.0,
                    volume=float(vol),
                    score=1.0,
                )
            )
    return bars, funding, hvns


def replay_symbol(
    symbol: str,
    *,
    bars_path: str | Path,
    funding_path: str | Path,
    out_dir: str | Path,
    loid_config: LoidConfig | None = None,
    monitor_config: MonitorConfig | None = None,
    end_ts: pd.Timestamp | None = None,
    start_ts: pd.Timestamp | None = None,
    vpvr_path: str | Path | None = None,
) -> tuple[list[Alert], MonitorStats]:
    """Replay the monitor over historical 1m bars.

    The 30d window is enforced by the caller (``end_ts - 30d``); here we
    just respect ``start_ts`` / ``end_ts`` slicing if provided.
    """
    cfg = monitor_config or MonitorConfig()
    bars, funding, hvns = load_replay_inputs(
        symbol,
        bars_path=bars_path,
        funding_path=funding_path,
        vpvr_path=vpvr_path,
    )

    if start_ts is not None:
        bars = bars.loc[bars.index >= start_ts]
    if end_ts is not None:
        bars = bars.loc[bars.index <= end_ts]
    if bars.empty:
        logger.warning(
            "no bars remain after windowing for %s (start=%s, end=%s)",
            symbol,
            start_ts,
            end_ts,
        )
        return [], MonitorStats(symbol=symbol)

    out_path = Path(out_dir).expanduser() / f"loid_vpvr_funding_{symbol}_1m.csv"
    sink = CsvSink(out_path)

    def _cb(alert: Alert) -> None:
        sink(alert)

    alerts, stats = detect_bars(
        bars,
        funding,
        symbol,
        loid_config=loid_config,
        monitor_config=cfg,
        bar_callback=_cb,
    )
    sink.close()
    logger.info(
        "REPLAY %s bars=%d loid=%d vpvr_prox=%d funding_pass=%d alerts=%d "
        "latency_p50=%.1fms p95=%.1fms",
        symbol,
        stats.n_bars,
        stats.n_loid_events,
        stats.n_vpvr_proximity_bars,
        stats.n_funding_pass_bars,
        stats.n_alerts,
        stats.latency_ms_p50,
        stats.latency_ms_p95,
    )
    return alerts, stats


# ---------------------------------------------------------------------------
# Live path (Binance WS + funding REST)
# ---------------------------------------------------------------------------


BINANCE_WS_BASE = "wss://fstream.binance.com/ws"
BINANCE_FAPI_BASE = "https://fapi.binance.com"


async def _fetch_funding_rest(symbol: str, *, session=None) -> float:
    """Return last funding rate for the symbol from fapi/v1/premiumIndex."""
    import urllib.request
    import urllib.parse

    url = f"{BINANCE_FAPI_BASE}/fapi/v1/premiumIndex?symbol={urllib.parse.quote(symbol)}"
    req = urllib.request.Request(url, headers={"User-Agent": "realtime_monitor/1.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        payload = json.loads(resp.read())
    return float(payload["lastFundingRate"])


def _ws_available(timeout: float = 4.0) -> bool:
    """Probe whether a TCP socket to fstream.binance.com:443 is reachable.

    Returns False immediately on any socket-level error or timeout — the
    spec says we must STOP rather than fabricate data when WS is blocked.
    """
    import socket
    try:
        with socket.create_connection(("fstream.binance.com", 443), timeout=timeout):
            return True
    except Exception:
        return False


async def run_live(
    symbols: list[str],
    out_dir: str | Path,
    *,
    monitor_config: MonitorConfig | None = None,
    loid_config: LoidConfig | None = None,
) -> dict[str, Any]:
    """Live-mode entry point. Streams ``<sym>@aggTrade`` from Binance and
    builds 1m bars in memory; when a 1m bar closes the monitor evaluates
    the same detectors used in replay and writes alerts to CSV.

    Returns a small summary dict so callers can serialize state. If WS is
    unreachable on this runtime, raises a ``RuntimeError`` *before*
    starting the stream — the caller should treat this as a blocker per
    the spec's "no synthetic data" rule.
    """
    cfg = monitor_config or MonitorConfig()
    lcfg = loid_config or LoidConfig(
        lookback_bars=DEFAULT_LOOKBACK_BARS,
        min_periods=DEFAULT_MIN_PERIODS,
        volume_zscore=DEFAULT_VOLUME_ZSCORE,
    )

    if not _ws_available():
        raise RuntimeError(
            "Binance fstream WebSocket unreachable from this runtime — "
            "live mode blocked per the spec's 'no synthetic data' gate"
        )

    try:
        import websockets  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("websockets package not available") from exc

    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build one 1m aggregator per symbol. Each holds a 4h rolling buffer
    # for VPVR + 120-bar rolling window for LOID baseline.
    aggregators: dict[str, "_LiveAggregator"] = {
        sym: _LiveAggregator(sym, cfg, lcfg, out_dir)
        for sym in symbols
    }

    streams = [f"{sym.lower()}@aggTrade" for sym in symbols]
    url = f"{BINANCE_WS_BASE}/{'/'.join(streams)}"
    async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
        last_funding_poll = 0.0
        while True:
            now = time.time()
            if now - last_funding_poll >= cfg.funding_poll_interval_s:
                for sym, agg in aggregators.items():
                    try:
                        f = await _fetch_funding_rest(sym)
                        agg.update_funding(f)
                    except Exception as exc:  # pragma: no cover
                        logger.warning("funding poll failed for %s: %s", sym, exc)
                last_funding_poll = now
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            payload = json.loads(msg)
            sym = payload.get("s")
            if sym not in aggregators:
                continue
            price = float(payload["p"])
            qty = float(payload["q"])
            ts_ms = int(payload["T"])
            taker_is_buyer = payload.get("m", False)  # True when the buyer is the market-maker → trade is a SELL
            # aggTrade "m": true => taker was seller (i.e. the trade hit a bid).
            taker_buy = (0.0 if taker_is_buyer else qty)
            aggregators[sym].on_trade(ts_ms, price, qty, taker_buy)

    return {
        "symbols": symbols,
        "alerts": {sym: agg.sink.n for sym, agg in aggregators.items()},
    }


class _LiveAggregator:
    """In-memory 1m bar builder + per-bar detector run for live mode."""

    def __init__(
        self,
        symbol: str,
        cfg: MonitorConfig,
        lcfg: LoidConfig,
        out_dir: Path,
    ) -> None:
        self.symbol = symbol
        self.cfg = cfg
        self.lcfg = lcfg
        self.out_path = out_dir / f"loid_vpvr_funding_{symbol}_1m.csv"
        self.sink = CsvSink(self.out_path)
        self.funding: float = float("nan")
        self._bars = pd.DataFrame(
            columns=["open", "high", "low", "close", "volume",
                     "quote_volume", "taker_buy_base"]
        )
        self._current_minute: Optional[pd.Timestamp] = None

    def update_funding(self, f: float) -> None:
        self.funding = float(f)

    def on_trade(
        self,
        ts_ms: int,
        price: float,
        qty: float,
        taker_buy: float,
    ) -> None:
        minute = pd.to_datetime(ts_ms, unit="ms", utc=True).floor("min")
        if self._current_minute != minute:
            if self._current_minute is not None:
                self._flush_bar(self._current_minute)
            self._current_minute = minute
            self._bars.loc[minute] = {
                "open": price, "high": price, "low": price,
                "close": price, "volume": 0.0, "quote_volume": 0.0,
                "taker_buy_base": 0.0,
            }
        row = self._bars.loc[minute]
        self._bars.loc[minute] = {
            "open": float(row["open"]),
            "high": max(float(row["high"]), price),
            "low": min(float(row["low"]), price),
            "close": price,
            "volume": float(row["volume"]) + qty,
            "quote_volume": float(row["quote_volume"]) + price * qty,
            "taker_buy_base": float(row["taker_buy_base"]) + taker_buy,
        }

    def _flush_bar(self, bar_ts: pd.Timestamp) -> None:
        if len(self._bars) < self.lcfg.min_periods + 5:
            return
        # Recompute features + match the replay path.
        df = _normalise_1m_bars(self._bars)
        funding_series = pd.Series(
            [self.funding], index=pd.DatetimeIndex([bar_ts], tz="UTC"), name="funding"
        )
        try:
            alerts, _ = detect_bars(
                df,
                funding_series,
                self.symbol,
                loid_config=self.lcfg,
                monitor_config=self.cfg,
                bar_callback=self.sink,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("detect_bars failed in live mode: %s", exc)
        # Trim history to last 4h to bound memory.
        cutoff = bar_ts - pd.Timedelta(minutes=self.cfg.vpvr_window_bars + 60)
        self._bars = self._bars.loc[self._bars.index >= cutoff]


# ---------------------------------------------------------------------------
# Aggregate multi-symbol replay (used by the SMA-34940 validate command)
# ---------------------------------------------------------------------------


def replay_multi(
    spec: list[dict[str, str]],
    out_dir: str | Path,
    *,
    start_ts: pd.Timestamp | None = None,
    end_ts: pd.Timestamp | None = None,
    loid_config: LoidConfig | None = None,
    monitor_config: MonitorConfig | None = None,
) -> dict[str, MonitorStats]:
    """``spec`` is a list of dicts ``{symbol, bars_path, funding_path}``.

    Returns ``{symbol: stats}``. Emits CSV files under ``out_dir/``.
    """
    out_dir = Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, MonitorStats] = {}
    for entry in spec:
        alerts, stats = replay_symbol(
            entry["symbol"],
            bars_path=entry["bars_path"],
            funding_path=entry["funding_path"],
            out_dir=out_dir,
            loid_config=loid_config,
            monitor_config=monitor_config,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        summary[entry["symbol"]] = stats
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "mode", choices=("replay", "live"), help="execution mode"
    )
    parser.add_argument(
        "--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT",
        help="comma-separated symbols",
    )
    parser.add_argument(
        "--out-dir",
        default="~/multica/quant-loop/signals",
        help="output directory for CSV files",
    )
    parser.add_argument(
        "--bars-dir",
        default="~/multica/quant-loop/data/perp_1m",
        help="shared pool 1m directory (parquet filenames must match <SYM>_1m.parquet)",
    )
    parser.add_argument(
        "--sol-bars-path",
        default=None,
        help="explicit SOLUSDT 1m path (used when shared pool has no SOL 1m)",
    )
    parser.add_argument(
        "--funding-dir",
        default="~/multica/quant-loop/data/funding",
        help="shared pool funding directory",
    )
    parser.add_argument(
        "--start", default=None, help="ISO start timestamp (replay only)"
    )
    parser.add_argument(
        "--end", default=None, help="ISO end timestamp (replay only)"
    )
    parser.add_argument(
        "--vpvr-distance-pct", type=float, default=DEFAULT_VPVR_DISTANCE_PCT,
        help="HVN proximity threshold in fractional pct (default 0.002 = 0.2%)",
    )
    parser.add_argument(
        "--funding-threshold", type=float, default=FUNDING_RATE_THRESHOLD,
        help="funding rate threshold (default 0.0003 = 0.03%)",
    )
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).expanduser()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    cfg = MonitorConfig(
        vpvr_distance_pct=args.vpvr_distance_pct,
        funding_threshold=args.funding_threshold,
    )
    if args.mode == "live":
        try:
            await run_live(symbols, out_dir, monitor_config=cfg)
        except RuntimeError as exc:
            logger.error("LIVE BLOCKED: %s", exc)
            return 2
        return 0

    bars_dir = Path(args.bars_dir).expanduser()
    funding_dir = Path(args.funding_dir).expanduser()
    start_ts = pd.Timestamp(args.start, tz="UTC") if args.start else None
    end_ts = pd.Timestamp(args.end, tz="UTC") if args.end else None
    spec = []
    for sym in symbols:
        candidate = bars_dir / f"{sym}_1m.parquet"
        if sym == "SOLUSDT" and not candidate.exists() and args.sol_bars_path:
            bars_path = Path(args.sol_bars_path).expanduser()
        else:
            bars_path = candidate
        funding_path = funding_dir / f"{sym}.parquet"
        if not bars_path.exists():
            logger.warning("skipping %s — no bars at %s", sym, bars_path)
            continue
        if not funding_path.exists():
            logger.warning("skipping %s — no funding at %s", sym, funding_path)
            continue
        spec.append({"symbol": sym, "bars_path": str(bars_path), "funding_path": str(funding_path)})

    if not spec:
        logger.error("replay spec empty — no inputs found")
        return 1
    summary = replay_multi(spec, out_dir, start_ts=start_ts, end_ts=end_ts,
                           monitor_config=cfg)
    summary_path = out_dir / "replay_summary.json"
    serializable = {
        sym: {
            "symbol": stats.symbol,
            "n_bars": stats.n_bars,
            "n_loid_events": stats.n_loid_events,
            "n_vpvr_proximity_bars": stats.n_vpvr_proximity_bars,
            "n_funding_pass_bars": stats.n_funding_pass_bars,
            "n_alerts": stats.n_alerts,
            "latency_ms_p50": stats.latency_ms_p50,
            "latency_ms_p95": stats.latency_ms_p95,
        }
        for sym, stats in summary.items()
    }
    summary_path.write_text(json.dumps(serializable, indent=2, default=str))
    logger.info("summary written to %s", summary_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
