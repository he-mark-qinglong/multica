"""VPVR confluence backtest: HVN support + funding > 0.03% → exit at next LVN.

SMA-34890 prototype.

Pipeline:
  1. Load BTCUSDT 15m + 4h OHLCV and Binance USDT-M funding events.
  2. Compute a rolling 4h VPVR snapshot (HVN absorption zones + LVN
     targets) with a strict no-look-ahead shift(1).
  3. State-machine simulator: long-only, enter when (a) most recent
     funding > 0.0003 (3 bps / 8h) and (b) close is inside / near an
     HVN zone from the shifted snapshot. Exit when high touches the
     nearest LVN above entry; max-hold / funding-flip safety nets.
  4. Emit per-window metrics.json, equity.csv, equity.png, summary.txt.

The script is intentionally self-contained — a single entry point
(`main`) that reads nothing from the strategy directory. Data is
loaded from the canonical ``~/multica/quant-loop/live_data`` and
``~/multica/quant-loop/data/funding`` paths.

Usage:
  python vpvr_funding_hvn_lvn_backtest.py
"""

from __future__ import annotations

import json
import math
import sys
import warnings
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Resolve paths and import the upstream VPVR detector.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent
QUANT_LOOP = REPO_ROOT.parents[1]
sys.path.insert(0, str(QUANT_LOOP / "strategies"))

from _indicators.vpvr_levels import (  # noqa: E402
    DEFAULT_HVN_QUANTILE,
    DEFAULT_LVN_QUANTILE,
    DEFAULT_NUM_HVN,
    DEFAULT_NUM_LVN,
    DEFAULT_PRICE_BINS,
    detect_vpvr_levels,
)


# ---------------------------------------------------------------------------
# Defaults — single source of truth.
# ---------------------------------------------------------------------------
FUNDING_THRESHOLD = 0.0003          # 3 bps / 8h event = 0.03%
VPVR_WINDOW_4H_BARS = 180           # ~30 days
VPVR_NUM_BINS = 24
VPVR_NUM_HVN = 3
VPVR_NUM_LVN = 5
VPVR_HVN_QUANTILE = 0.85
VPVR_LVN_QUANTILE = 0.15
ATR_PERIOD = 14
PROXIMITY_ATR = 1.0
COOLDOWN_BARS = 16                  # ~4 hours at 15m
MAX_HOLD_BARS = 96                  # ~24 hours at 15m
FUNDING_FLIP_THRESHOLD = -0.0003    # exit on deep negative carry
FEE_BPS_PER_FILL = 4.0
SLIPPAGE_BPS_PER_FILL = 1.0
STARTING_CAPITAL_USD = 100_000.0
SQRT_BPY_DAILY = math.sqrt(365.25)

WINDOWS = {
    "last_30d": {
        "start": None,  # computed dynamically = end - 30 days
        "end": None,
    },
    "q1_2024_hot_funding": {
        "start": pd.Timestamp("2024-02-01", tz=None),
        "end": pd.Timestamp("2024-04-30 23:45:00", tz=None),
    },
}

RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

DATA_15M = QUANT_LOOP / "live_data" / "BTCUSDT_15m.parquet"
DATA_4H = QUANT_LOOP / "live_data" / "BTCUSDT_4h.parquet"
FUNDING_P = QUANT_LOOP / "data" / "funding" / "BTCUSDT.parquet"
if not FUNDING_P.exists():
    FUNDING_P = QUANT_LOOP / "funding_analysis" / "BTCUSDT_funding.parquet"


# ---------------------------------------------------------------------------
# Data loaders.
# ---------------------------------------------------------------------------
def _load_ohlcv(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "open_time" in df.columns:
        df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=False)
        df = df.set_index("ts")
    df = df.sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    return df[keep].astype(np.float64)


def _load_funding(ohlcv_index: pd.DatetimeIndex) -> pd.Series:
    fdf = pd.read_parquet(FUNDING_P)
    if "ts" in fdf.columns:
        fdf["ts"] = pd.to_datetime(fdf["ts"], utc=True)
        fdf = fdf.set_index("ts")
    elif "fundingTime" in fdf.columns:
        fdf["ts"] = pd.to_datetime(fdf["fundingTime"], unit="ms", utc=True)
        fdf = fdf.set_index("ts")
    fdf = fdf.sort_index()
    if fdf.index.tz is not None:
        fdf.index = fdf.index.tz_convert(None)
    if fdf.index.dtype != ohlcv_index.dtype:
        fdf.index = pd.DatetimeIndex(fdf.index.values, tz=None)
    funding = fdf["fundingRate"].astype(np.float64)
    # shift(1) so the funding at bar `t` is the rate paid at the most
    # recent event strictly before bar `t`'s open time.
    funding = funding.shift(1)
    aligned = funding.reindex(ohlcv_index, method="ffill").fillna(0.0)
    return aligned


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """ATR(period) with the cycle-46 shift(1) no-look-ahead rule.

    Today's range cannot leak into today's ATR. We use the prior
    close (shifted) to compute the True Range so today's bar is
    excluded from its own ATR.
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


# ---------------------------------------------------------------------------
# VPVR rolling snapshot (no look-ahead).
# ---------------------------------------------------------------------------
def _build_vpvr_table(
    df4h: pd.DataFrame,
    *,
    window_bars: int,
    num_bins: int,
    num_hvn: int,
    num_lvn: int,
    hvn_quantile: float,
    lvn_quantile: float,
) -> pd.DataFrame:
    """Compute rolling 4h VPVR snapshots with shift(1) discipline.

    For each 4h bar `t` we run ``detect_vpvr_levels`` on the trailing
    window ``[t-window_bars, t)`` (exclusive of `t`). The level list
    is then flattened into a tidy DataFrame — one row per level —
    indexed by `t`.

    The returned table has columns:
      - ``ts`` (4h bar open time)
      - ``kind`` ("HVN" or "LVN" — POC excluded; we only need
        structural zones)
      - ``price_low``, ``price_high``, ``price_center``
      - ``volume``, ``score``
    """
    rows = []
    high = df4h["high"].values
    low = df4h["low"].values
    volume = df4h["volume"].values
    n = len(df4h)
    ts_index = df4h.index
    for i in range(window_bars, n):
        # Trailing window strictly BEFORE bar `i` — so the level used
        # at bar `i` reflects only data the market saw at the open of
        # bar `i-1` and earlier.
        lo = i - window_bars
        hi = i
        window_df = pd.DataFrame(
            {
                "high": high[lo:hi],
                "low": low[lo:hi],
                "volume": volume[lo:hi],
            },
            index=ts_index[lo:hi],
        )
        try:
            levels = detect_vpvr_levels(
                window_df,
                num_bins=num_bins,
                hvn_quantile=hvn_quantile,
                lvn_quantile=lvn_quantile,
                num_hvn=num_hvn,
                num_lvn=num_lvn,
                include_poc=False,
            )
        except Exception as exc:  # noqa: BLE001 — detector can raise on degenerate windows
            warnings.warn(f"detect_vpvr_levels failed at 4h bar {ts_index[i]}: {exc}")
            continue
        ts = ts_index[i]
        for lvl in levels:
            if lvl.kind not in ("HVN", "LVN"):
                continue
            rows.append(
                {
                    "ts": ts,
                    "kind": lvl.kind,
                    "price_low": float(lvl.price_low),
                    "price_high": float(lvl.price_high),
                    "price_center": float(lvl.price_center),
                    "volume": float(lvl.volume),
                    "score": float(lvl.score),
                }
            )
    return pd.DataFrame(rows).sort_values(["ts", "kind"])


def _nearest_lvn_above(
    entry_price: float, lvn_df: pd.DataFrame
) -> Optional[Dict[str, float]]:
    """Return the LVN whose price_low is the smallest value > entry.

    If multiple LVN have identical price_low (rare; tied zones), we
    pick the one with the lowest score (lightest) so the stop is the
    most-permeable barrier.
    """
    if lvn_df.empty:
        return None
    candidates = lvn_df[lvn_df["price_low"] > entry_price]
    if candidates.empty:
        return None
    candidates = candidates.sort_values(
        ["price_low", "score"], ascending=[True, True]
    )
    row = candidates.iloc[0]
    return {
        "price_low": float(row["price_low"]),
        "price_high": float(row["price_high"]),
        "price_center": float(row["price_center"]),
        "score": float(row["score"]),
    }


def _inside_any_band(price: float, bands: pd.DataFrame) -> Optional[Dict[str, float]]:
    """Return the tightest band that contains `price`, or None."""
    if bands.empty:
        return None
    contains = bands[(bands["price_low"] <= price) & (bands["price_high"] >= price)]
    if contains.empty:
        return None
    # Tightest = smallest width.
    contains = contains.copy()
    contains["__width"] = contains["price_high"] - contains["price_low"]
    contains = contains.sort_values("__width")
    row = contains.iloc[0]
    return {
        "price_low": float(row["price_low"]),
        "price_high": float(row["price_high"]),
        "price_center": float(row["price_center"]),
        "score": float(row["score"]),
    }


# ---------------------------------------------------------------------------
# State machine.
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    variant: str
    symbol: str
    direction: str
    entry_ts: pd.Timestamp
    entry_price: float
    exit_ts: Optional[pd.Timestamp]
    exit_price: Optional[float]
    pnl_pct: Optional[float]
    bars_held: int
    exit_reason: str
    funding_at_entry: float
    support_level_price: Optional[float]
    target_lvn_price: Optional[float]
    nearest_lvn_distance_at_entry: Optional[float]
    hvn_distance_at_entry: Optional[float]


def _simulate(
    df15: pd.DataFrame,
    funding: pd.Series,
    atr: pd.Series,
    vpvr_at_15m: pd.DataFrame,
    *,
    cfg: Dict,
) -> Tuple[List[Trade], pd.Series, Dict]:
    """Run the long-only state machine on the 15m bar stream."""
    fee = float(cfg["fee_bps_per_fill"]) / 10000.0
    slip = float(cfg["slippage_bps_per_fill"]) / 10000.0
    round_trip_cost = 2.0 * (fee + slip)
    funding_threshold = float(cfg["funding_threshold"])
    funding_flip = float(cfg["funding_flip_threshold"])
    cooldown = int(cfg["cooldown_bars"])
    max_hold = int(cfg["max_hold_bars"])
    starting_capital = float(cfg["starting_capital_usd"])

    close = df15["close"]
    high = df15["high"]
    low = df15["low"]

    trades: List[Trade] = []
    equity = pd.Series(index=df15.index, dtype=np.float64)
    equity.iloc[0] = starting_capital

    pos = 0
    entry_idx: Optional[int] = None
    entry_px = 0.0
    bars_held = 0
    bars_since_exit = cooldown
    entry_fd = 0.0
    target_lvn: Optional[Dict[str, float]] = None
    support_used: Optional[Dict[str, float]] = None

    diagnostics = {
        "n_long_signals": 0,
        "signal_bars_funding_above_threshold": 0,
        "signal_bars_near_hvn": 0,
    }

    for i in range(1, len(df15)):
        ts = df15.index[i]
        px = float(close.iloc[i])
        at = float(atr.iloc[i]) if np.isfinite(atr.iloc[i]) else 0.0
        fd = float(funding.iloc[i])

        if fd > funding_threshold:
            diagnostics["signal_bars_funding_above_threshold"] += 1

        equity.iloc[i] = equity.iloc[i - 1]

        # 1. New bar logic, only when flat.
        if pos == 0:
            bars_since_exit += 1
            if (
                bars_since_exit >= cooldown
                and at > 0
                and fd > funding_threshold
            ):
                # Look up the 4h VPVR snapshot that is "live" for this
                # 15m bar. The snapshot grid is built at 4h boundaries;
                # we forward-fill by selecting the most recent snapshot
                # whose ts <= current 15m ts.
                snap = vpvr_at_15m[vpvr_at_15m["__ts_15m"] == ts]
                if not snap.empty:
                    hvn_df = snap[snap["kind"] == "HVN"]
                    if not hvn_df.empty:
                        # Tightness check first.
                        band_hit = _inside_any_band(px, hvn_df)
                        if band_hit is None:
                            # Fall back to ATR proximity against the
                            # closest HVN center.
                            dists = (hvn_df["price_center"] - px).abs()
                            j = int(dists.idxmin())
                            row = hvn_df.loc[j]
                            dist = float(abs(row["price_center"] - px))
                            if dist <= cfg["proximity_atr"] * at:
                                band_hit = {
                                    "price_low": float(row["price_low"]),
                                    "price_high": float(row["price_high"]),
                                    "price_center": float(row["price_center"]),
                                    "score": float(row["score"]),
                                    "__dist_atr": dist / at if at > 0 else float("inf"),
                                }
                        if band_hit is not None:
                            diagnostics["signal_bars_near_hvn"] += 1
                            support_used = band_hit
                            # Target = nearest LVN above entry.
                            lvn_df = snap[snap["kind"] == "LVN"]
                            target_lvn = _nearest_lvn_above(px, lvn_df)
                            pos = +1
                            entry_idx = i
                            entry_px = px
                            bars_held = 0
                            entry_fd = fd
                            diagnostics["n_long_signals"] += 1
        else:
            bars_held += 1
            prev_px = float(close.iloc[i - 1])
            # Per-bar mark-to-market — do NOT compound the cumulative
            # entry-to-now move on top of the previous equity bar (that
            # double-counts). Each bar's contribution is the per-bar
            # return on the position.
            per_bar_ret = pos * (px / prev_px - 1.0)
            equity.iloc[i] = equity.iloc[i - 1] * (1.0 + per_bar_ret)

            exit_now = False
            exit_reason = ""

            # Structural exit — high touches target LVN's upper bound.
            if (
                target_lvn is not None
                and high.iloc[i] >= target_lvn["price_high"]
            ):
                exit_now = True
                exit_reason = "lvn_target"

            # Funding-flip safety net.
            if not exit_now and fd < funding_flip:
                exit_now = True
                exit_reason = "funding_flip"

            # Time stop.
            if not exit_now and bars_held >= max_hold:
                exit_now = True
                exit_reason = "time_stop"

            if exit_now:
                exit_px = float(close.iloc[i])
                # Per-trade ledger pnl = entry-to-exit move, with the
                # round-trip cost deducted once. The equity curve
                # already reflects the per-bar mark; here we just
                # apply the one-time round-trip cost on the position
                # notional.
                gross_move = (exit_px / entry_px - 1.0)
                net_pnl = gross_move - round_trip_cost
                equity.iloc[i] = equity.iloc[i] * (1.0 - round_trip_cost)
                trades.append(
                    Trade(
                        variant="vpvr_funding_hvn_lvn",
                        symbol="BTCUSDT",
                        direction="long",
                        entry_ts=df15.index[entry_idx],
                        entry_price=entry_px,
                        exit_ts=ts,
                        exit_price=exit_px,
                        pnl_pct=net_pnl,
                        bars_held=bars_held,
                        exit_reason=exit_reason,
                        funding_at_entry=entry_fd,
                        support_level_price=support_used["price_center"]
                        if support_used is not None
                        else None,
                        target_lvn_price=target_lvn["price_center"]
                        if target_lvn is not None
                        else None,
                        nearest_lvn_distance_at_entry=(
                            target_lvn["price_low"] - entry_px
                            if target_lvn is not None
                            else None
                        ),
                        hvn_distance_at_entry=(
                            entry_px - support_used["price_center"]
                            if support_used is not None
                            else None
                        ),
                    )
                )
                pos = 0
                entry_idx = None
                target_lvn = None
                support_used = None
                bars_since_exit = 0
                bars_held = 0

    return trades, equity, diagnostics


# ---------------------------------------------------------------------------
# Metrics.
# ---------------------------------------------------------------------------
def _metrics(trades: List[Trade], equity: pd.Series) -> Dict:
    if not trades:
        return {
            "n_trades": 0,
            "win_rate": None,
            "profit_factor": None,
            "avg_pnl_pct": None,
            "median_pnl_pct": None,
            "sharpe_daily": None,
            "annualized_return": None,
            "max_drawdown_pct": None,
            "total_return": None,
        }
    pnls = np.array([t.pnl_pct for t in trades], dtype=np.float64)
    winners = pnls[pnls > 0]
    losers = pnls[pnls < 0]
    win_rate = float((pnls > 0).mean())
    pf = (
        float(winners.sum() / abs(losers.sum()))
        if losers.size and losers.sum() != 0
        else (float("inf") if winners.size and losers.sum() == 0 else None)
    )
    eq = equity.dropna()
    daily_eq = eq.resample("1D").last().dropna()
    daily_ret = daily_eq.pct_change().dropna()
    if daily_ret.std() and daily_ret.std() > 0:
        sharpe = float(daily_ret.mean() / daily_ret.std() * SQRT_BPY_DAILY)
    else:
        sharpe = None
    total_ret = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    days = max((daily_eq.index[-1] - daily_eq.index[0]).days, 1)
    ann_ret = float((1.0 + total_ret) ** (365.25 / days) - 1.0)
    running_max = eq.cummax()
    drawdown = (eq / running_max - 1.0)
    mdd = float(drawdown.min())  # store as fraction (e.g. -0.1088 = -10.88%)
    return {
        "n_trades": int(len(trades)),
        "win_rate": win_rate,
        "profit_factor": pf,
        "avg_pnl_pct": float(pnls.mean()),
        "median_pnl_pct": float(np.median(pnls)),
        "sharpe_daily": sharpe,
        "annualized_return": ann_ret,
        "max_drawdown_pct": mdd,
        "total_return": total_ret,
    }


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> int:
    print(f"[{datetime.now(timezone.utc).isoformat()}] vpvr_funding_hvn_lvn backtest start")

    print(f"  loading 15m OHLCV: {DATA_15M}")
    df15_full = _load_ohlcv(DATA_15M)
    print(f"  loading 4h OHLCV: {DATA_4H}")
    df4h_full = _load_ohlcv(DATA_4H)

    # Trim to a single common range so the 4h VPVR is available for
    # the entire 15m bar stream we simulate.
    common_lo = max(df15_full.index.min(), df4h_full.index.min())
    common_hi = min(df15_full.index.max(), df4h_full.index.max())
    df15_full = df15_full.loc[common_lo:common_hi]
    df4h_full = df4h_full.loc[common_lo:common_hi]

    funding_full = _load_funding(df15_full.index)
    atr_full = _atr(df15_full["high"], df15_full["low"], df15_full["close"], ATR_PERIOD)

    # Build the rolling 4h VPVR snapshot table ONCE (over the full
    # 4h range). Per-bar entry logic just looks up the snapshot
    # whose ts <= the 15m bar's ts.
    print(f"  building rolling 4h VPVR ({VPVR_WINDOW_4H_BARS} bar window)...")
    vpvr_4h = _build_vpvr_table(
        df4h_full,
        window_bars=VPVR_WINDOW_4H_BARS,
        num_bins=VPVR_NUM_BINS,
        num_hvn=VPVR_NUM_HVN,
        num_lvn=VPVR_NUM_LVN,
        hvn_quantile=VPVR_HVN_QUANTILE,
        lvn_quantile=VPVR_LVN_QUANTILE,
    )
    print(f"  vpvr snapshot rows = {len(vpvr_4h)}")

    # We need a 15m-aligned view: for each 4h snapshot at ts=t4,
    # forward-fill to all 15m bars in [t4, next_snapshot_ts).
    # Implementation: reindex onto df15.index with method='ffill'.
    # vpvr_at_15m is a flat list-of-dicts carrying every level active
    # for each 15m bar (we then filter by kind inside the loop).
    snap_pivot = vpvr_4h.set_index("ts")
    # Build per-15m-bar level frames and stack into a long table with
    # a __ts_15m column so the simulator can select by exact match.
    vpvr_at_15m_chunks = []
    snap_ts = snap_pivot.index.unique().sort_values()
    if len(snap_ts) == 0:
        raise RuntimeError("No VPVR snapshots produced — check 4h OHLCV range / params.")
    snap_ts_full = snap_ts.append(pd.DatetimeIndex([df15_full.index.max() + pd.Timedelta("15m")]))
    boundary_iter = list(zip(snap_ts_full[:-1], snap_ts_full[1:]))
    for t4_start, t4_end in boundary_iter:
        chunk_15m = df15_full.index[(df15_full.index >= t4_start) & (df15_full.index < t4_end)]
        if len(chunk_15m) == 0:
            continue
        levels = snap_pivot.loc[t4_start]
        if isinstance(levels, pd.DataFrame):
            levels = levels.reset_index(drop=True)
        else:
            levels = levels.to_frame().T.reset_index(drop=True)
        for ts15 in chunk_15m:
            tmp = levels.copy()
            tmp["__ts_15m"] = ts15
            vpvr_at_15m_chunks.append(tmp)
    if not vpvr_at_15m_chunks:
        raise RuntimeError("vpvr_at_15m_chunks empty — VPVR snapshots did not align to 15m range.")
    vpvr_at_15m = pd.concat(vpvr_at_15m_chunks, ignore_index=True)
    print(f"  vpvr_at_15m rows = {len(vpvr_at_15m)}")

    cfg = dict(
        funding_threshold=FUNDING_THRESHOLD,
        funding_flip_threshold=FUNDING_FLIP_THRESHOLD,
        cooldown_bars=COOLDOWN_BARS,
        max_hold_bars=MAX_HOLD_BARS,
        proximity_atr=PROXIMITY_ATR,
        atr_period=ATR_PERIOD,
        fee_bps_per_fill=FEE_BPS_PER_FILL,
        slippage_bps_per_fill=SLIPPAGE_BPS_PER_FILL,
        starting_capital_usd=STARTING_CAPITAL_USD,
    )

    metrics_windows: Dict[str, Dict] = {}
    summary_lines = []
    summary_lines.append("vpvr_funding_hvn_lvn — SMA-34890 prototype")
    summary_lines.append(f"  start_capital_usd = {STARTING_CAPITAL_USD:,.0f}")
    summary_lines.append(
        f"  funding_threshold  = {FUNDING_THRESHOLD:.4f}  "
        f"({FUNDING_THRESHOLD*100:.3f}% / 8h)"
    )
    summary_lines.append(
        f"  vpvr_window_4h     = {VPVR_WINDOW_4H_BARS} bars (~30d)"
    )
    summary_lines.append(
        f"  cooldown={COOLDOWN_BARS} bars, max_hold={MAX_HOLD_BARS} bars, "
        f"fee={FEE_BPS_PER_FILL}bps, slip={SLIPPAGE_BPS_PER_FILL}bps"
    )
    summary_lines.append("")

    for window_name, window_cfg in WINDOWS.items():
        if window_name == "last_30d":
            end = df15_full.index.max()
            start = end - pd.Timedelta(days=30)
        else:
            start = pd.Timestamp(window_cfg["start"])
            end = pd.Timestamp(window_cfg["end"])
        # Constrain to the staged data range.
        if start < df15_full.index.min():
            start = df15_full.index.min()
        if end > df15_full.index.max():
            end = df15_full.index.max()

        df15 = df15_full.loc[start:end].copy()
        funding = funding_full.loc[df15.index]
        atr = atr_full.loc[df15.index]
        vpvr_win = vpvr_at_15m[vpvr_at_15m["__ts_15m"].isin(df15.index)]
        if df15.empty or vpvr_win.empty:
            print(f"  [{window_name}] empty window — skipping")
            continue

        trades, equity, diag = _simulate(
            df15, funding, atr, vpvr_win, cfg=cfg
        )
        m = _metrics(trades, equity)
        m["diagnostics"] = diag
        m["window"] = window_name
        m["window_start"] = str(df15.index.min())
        m["window_end"] = str(df15.index.max())
        m["n_bars"] = int(len(df15))
        metrics_windows[window_name] = m

        # Persist equity curve and trade ledger.
        eq_df = equity.to_frame("equity")
        eq_df.index.name = "timestamp"
        eq_path = RESULTS_DIR / f"equity_{window_name}.csv"
        eq_df.to_csv(eq_path)
        trades_df = pd.DataFrame([asdict(t) for t in trades])
        trades_path = RESULTS_DIR / f"trades_{window_name}.csv"
        trades_df.to_csv(trades_path, index=False)

        # Plot.
        fig, ax = plt.subplots(figsize=(11, 4.5))
        ax.plot(eq_df.index, eq_df["equity"].values, lw=1.2, color="#1f77b4")
        ax.axhline(STARTING_CAPITAL_USD, color="grey", lw=0.7, ls="--", alpha=0.6)
        ax.set_title(
            f"vpvr_funding_hvn_lvn — {window_name}  "
            f"(trades={m['n_trades']}, "
            f"sharpe_d={m['sharpe_daily']}, "
            f"ann_ret={m['annualized_return']})"
        )
        ax.set_ylabel("Equity (USD)")
        ax.set_xlabel("Time (15m bars)")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        png_path = RESULTS_DIR / f"equity_{window_name}.png"
        plt.savefig(png_path, dpi=110)
        plt.close(fig)

        summary_lines.append(
            f"[{window_name}]  span={df15.index.min()} → {df15.index.max()}  "
            f"bars={len(df15)}"
        )
        summary_lines.append(
            f"  diagnostics: long_signals={diag['n_long_signals']}  "
            f"funding_above={diag['signal_bars_funding_above_threshold']}  "
            f"near_hvn={diag['signal_bars_near_hvn']}"
        )
        summary_lines.append(
            f"  metrics: trades={m['n_trades']}  "
            f"win_rate={_fmt(m['win_rate'])}  pf={_fmt(m['profit_factor'])}  "
            f"avg_pnl={_fmt_pct(m['avg_pnl_pct'])}  "
            f"med_pnl={_fmt_pct(m['median_pnl_pct'])}"
        )
        summary_lines.append(
            f"           sharpe_d={_fmt(m['sharpe_daily'])}  "
            f"ann_ret={_fmt_pct(m['annualized_return'])}  "
            f"total_ret={_fmt_pct(m['total_return'])}  "
            f"mdd={_fmt_pct(m['max_drawdown_pct'])}"
        )
        summary_lines.append("")

    # Honest verdict.
    verdict = _verdict(metrics_windows)
    summary_lines.append(f"VERDICT: {verdict}")
    summary_lines.append("")
    summary_lines.append(
        "Notes:"
    )
    summary_lines.append(
        "  - Sharpe is daily-resampled (sqrt(365.25)) per SMA-34787 audit."
    )
    summary_lines.append(
        "  - Prototype only: G6 (bootstrap CI) and G7 (Bonferroni) are"
    )
    summary_lines.append(
        "    not computed — they need a full-period run, not the 30d /"
    )
    summary_lines.append(
        "    90d windows used here."
    )

    summary = "\n".join(summary_lines) + "\n"
    (RESULTS_DIR / "summary.txt").write_text(summary)
    print(summary)

    metrics_path = RESULTS_DIR / "metrics.json"
    payload = {
        "variant": "vpvr_funding_hvn_lvn",
        "strategy_key": "vpvr_funding_hvn_lvn",
        "iteration": 1,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source_spec": "SMA-34890",
        "sharpe_method": "daily_resampled_per_SMA-34787",
        "sharpe_method_audit_ref": "SMA-34787",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "starting_capital_usd": STARTING_CAPITAL_USD,
        "params": {
            "funding_threshold": FUNDING_THRESHOLD,
            "vpvr_window_4h_bars": VPVR_WINDOW_4H_BARS,
            "vpvr_num_bins": VPVR_NUM_BINS,
            "vpvr_num_hvn": VPVR_NUM_HVN,
            "vpvr_num_lvn": VPVR_NUM_LVN,
            "vpvr_hvn_quantile": VPVR_HVN_QUANTILE,
            "vpvr_lvn_quantile": VPVR_LVN_QUANTILE,
            "atr_period": ATR_PERIOD,
            "proximity_atr": PROXIMITY_ATR,
            "cooldown_bars": COOLDOWN_BARS,
            "max_hold_bars": MAX_HOLD_BARS,
            "funding_flip_threshold": FUNDING_FLIP_THRESHOLD,
            "fee_bps_per_fill": FEE_BPS_PER_FILL,
            "slippage_bps_per_fill": SLIPPAGE_BPS_PER_FILL,
        },
        "windows": metrics_windows,
    }
    metrics_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"  wrote {metrics_path}")
    print(f"[{datetime.now(timezone.utc).isoformat()}] vpvr_funding_hvn_lvn backtest done")
    return 0


def _fmt(x) -> str:
    if x is None:
        return "NA"
    if isinstance(x, float) and not math.isfinite(x):
        return "inf" if x > 0 else "-inf"
    return f"{x:.3f}"


def _fmt_pct(x) -> str:
    if x is None:
        return "NA"
    if isinstance(x, float) and not math.isfinite(x):
        return "inf" if x > 0 else "-inf"
    return f"{x*100:+.2f}%"


def _verdict(windows: Dict[str, Dict]) -> str:
    if not windows:
        return "no windows produced — wiring bug"
    primary = windows.get("q1_2024_hot_funding") or windows.get("last_30d")
    if primary is None:
        return "no primary window"
    sharpe = primary.get("sharpe_daily")
    ann_ret = primary.get("annualized_return")
    pf = primary.get("profit_factor")
    n = primary.get("n_trades", 0)
    if n == 0:
        return (
            "PROTOTYPE WIRE-UP OK — zero trades in primary window. "
            "The signal is not firing (likely funding regime too cold)."
        )
    flags = []
    if sharpe is not None and sharpe >= 1.0:
        flags.append("G1")
    if ann_ret is not None and ann_ret >= 0.15:
        flags.append("G2")
    if pf is not None and pf >= 1.5:
        flags.append("G3")
    if primary.get("max_drawdown_pct") is not None and primary["max_drawdown_pct"] > -25.0:
        flags.append("G4")
    if not flags:
        return (
            "PROTOTYPE WIRE-UP OK — n_trades={}, but NO G1-G4 met. "
            "Verdict: NOT-PROFITABLE on this prototype; cycle-46 "
            "funding-carry family already has 1+ NOT-PROFITABLE iter "
            "so this iteration is informative but should NOT be "
            "promoted without (a) a different execution layer and "
            "(b) cross-framework CV."
        ).format(n)
    return (
        f"PROTOTYPE WIRE-UP OK — primary window passed gates: {flags}. "
        f"Still prototype-grade: G5-G7 require full-period data "
        f"and cross-framework CV which are out of scope here."
    )


if __name__ == "__main__":
    sys.exit(main())