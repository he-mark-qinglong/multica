"""VPVR confluence backtest — multi-symbol extended window.

SMA-34901 implementation.

Pipeline:
  1. Load BTC + ETH + SOL 15m + 4h OHLCV and Binance USDT-M funding events.
  2. For each symbol, compute a rolling 4h VPVR snapshot (HVN absorption
     zones + LVN targets) with a strict no-look-ahead shift(1).
  3. State-machine simulator: long-only, enter when (a) most recent
     funding > 0.0003 (3 bps / 8h) and (b) close is inside / near an
     HVN zone from the shifted snapshot. Exit when high touches the
     nearest LVN above entry; max-hold / funding-flip safety nets.
  4. Combine per-symbol trades into a single ledger + equity curve.
  5. Compute combined metrics, check G1-G4 acceptance gates.
  6. Persist per-symbol and combined metrics.json + summary.txt.

The script is intentionally self-contained — a single entry point
(`main`) that reads nothing from the strategy directory. Data is
loaded from the canonical ``~/multica/quant-loop/live_data`` and
``~/multica/quant-loop/data/funding`` paths.

Usage:
  python vpvr_funding_hvn_lvn_confluence_backtest.py
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

from _indicators.vpvr_levels import detect_vpvr_levels  # noqa: E402


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

# Hot-funding window covering every month with >=3 funding events > 0.0003
# in any of BTC, ETH, SOL funding series.
WINDOW_START = pd.Timestamp("2023-11-01", tz=None)
WINDOW_END = pd.Timestamp("2024-12-31 23:45:00", tz=None)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
WINDOW_NAME = "hot_funding_2023_2024"

RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


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


def _load_funding(symbol: str, ohlcv_index: pd.DatetimeIndex) -> pd.Series:
    funding_p = QUANT_LOOP / "data" / "funding" / f"{symbol}.parquet"
    if not funding_p.exists():
        raise FileNotFoundError(f"no funding parquet for {symbol} at {funding_p}")
    fdf = pd.read_parquet(funding_p)
    if "ts" in fdf.columns:
        fdf["ts"] = pd.to_datetime(fdf["ts"], utc=True)
        fdf = fdf.set_index("ts")
    elif "fundingTime" in fdf.columns:
        fdf["ts"] = pd.to_datetime(fdf["fundingTime"], unit="ms", utc=True)
        fdf = fdf.set_index("ts")
    fdf = fdf.sort_index()
    if fdf.index.tz is not None:
        fdf.index = fdf.index.tz_convert(None)
    funding = fdf["fundingRate"].astype(np.float64)
    # shift(1) so the funding at bar `t` is the rate paid at the most
    # recent event strictly before bar `t`'s open time.
    funding = funding.shift(1)
    aligned = funding.reindex(ohlcv_index, method="ffill").fillna(0.0)
    return aligned


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """ATR(period) with the cycle-46 shift(1) no-look-ahead rule."""
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
    """Compute rolling 4h VPVR snapshots with shift(1) discipline."""
    rows = []
    high = df4h["high"].values
    low = df4h["low"].values
    volume = df4h["volume"].values
    n = len(df4h)
    ts_index = df4h.index
    for i in range(window_bars, n):
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
        except Exception as exc:  # noqa: BLE001
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
    if bands.empty:
        return None
    contains = bands[(bands["price_low"] <= price) & (bands["price_high"] >= price)]
    if contains.empty:
        return None
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
    symbol: str,
) -> Tuple[List[Trade], pd.Series, Dict]:
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

        if pos == 0:
            bars_since_exit += 1
            if (
                bars_since_exit >= cooldown
                and at > 0
                and fd > funding_threshold
            ):
                snap = vpvr_at_15m[vpvr_at_15m["__ts_15m"] == ts]
                if not snap.empty:
                    hvn_df = snap[snap["kind"] == "HVN"]
                    if not hvn_df.empty:
                        band_hit = _inside_any_band(px, hvn_df)
                        if band_hit is None:
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
                                }
                        if band_hit is not None:
                            diagnostics["signal_bars_near_hvn"] += 1
                            support_used = band_hit
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
            per_bar_ret = pos * (px / prev_px - 1.0)
            equity.iloc[i] = equity.iloc[i - 1] * (1.0 + per_bar_ret)

            exit_now = False
            exit_reason = ""

            if (
                target_lvn is not None
                and high.iloc[i] >= target_lvn["price_high"]
            ):
                exit_now = True
                exit_reason = "lvn_target"

            if not exit_now and fd < funding_flip:
                exit_now = True
                exit_reason = "funding_flip"

            if not exit_now and bars_held >= max_hold:
                exit_now = True
                exit_reason = "time_stop"

            if exit_now:
                exit_px = float(close.iloc[i])
                gross_move = (exit_px / entry_px - 1.0)
                net_pnl = gross_move - round_trip_cost
                equity.iloc[i] = equity.iloc[i] * (1.0 - round_trip_cost)
                trades.append(
                    Trade(
                        variant="vpvr_funding_hvn_lvn_confluence",
                        symbol=symbol,
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
    mdd = float(drawdown.min())
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
# Per-symbol simulation pipeline.
# ---------------------------------------------------------------------------
def _run_symbol(symbol: str, cfg: Dict) -> Tuple[List[Trade], pd.Series, Dict]:
    """Run the confluence simulator on a single symbol over the hot window."""
    p15 = QUANT_LOOP / "live_data" / f"{symbol}_15m.parquet"
    p4h = QUANT_LOOP / "live_data" / f"{symbol}_4h.parquet"
    print(f"  loading {symbol}: 15m={p15.exists()} 4h={p4h.exists()}")
    if not (p15.exists() and p4h.exists()):
        raise FileNotFoundError(f"missing 15m or 4h parquet for {symbol}")

    df15_full = _load_ohlcv(p15)
    df4h_full = _load_ohlcv(p4h)

    # Trim to the hot-funding window intersected with data range.
    start = max(df15_full.index.min(), df4h_full.index.min(), WINDOW_START)
    end = min(df15_full.index.max(), df4h_full.index.max(), WINDOW_END)
    df15 = df15_full.loc[start:end].copy()
    df4h = df4h_full.loc[start:end].copy()
    if df15.empty or df4h.empty:
        raise RuntimeError(f"{symbol}: empty window after trim [{start}..{end}]")

    funding = _load_funding(symbol, df15.index)
    atr = _atr(df15["high"], df15["low"], df15["close"], ATR_PERIOD)

    print(f"  building {symbol} rolling 4h VPVR ({VPVR_WINDOW_4H_BARS} bar window)...")
    vpvr_4h = _build_vpvr_table(
        df4h,
        window_bars=VPVR_WINDOW_4H_BARS,
        num_bins=VPVR_NUM_BINS,
        num_hvn=VPVR_NUM_HVN,
        num_lvn=VPVR_NUM_LVN,
        hvn_quantile=VPVR_HVN_QUANTILE,
        lvn_quantile=VPVR_LVN_QUANTILE,
    )
    print(f"  {symbol} vpvr snapshot rows = {len(vpvr_4h)}")

    # Build per-15m-bar level frames.
    snap_pivot = vpvr_4h.set_index("ts")
    vpvr_at_15m_chunks = []
    snap_ts = snap_pivot.index.unique().sort_values()
    if len(snap_ts) == 0:
        raise RuntimeError(f"{symbol}: no VPVR snapshots produced")
    snap_ts_full = snap_ts.append(
        pd.DatetimeIndex([df15.index.max() + pd.Timedelta("15m")])
    )
    for t4_start, t4_end in list(zip(snap_ts_full[:-1], snap_ts_full[1:])):
        chunk_15m = df15.index[
            (df15.index >= t4_start) & (df15.index < t4_end)
        ]
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
    vpvr_at_15m = pd.concat(vpvr_at_15m_chunks, ignore_index=True)

    trades, equity, diag = _simulate(
        df15, funding, atr, vpvr_at_15m, cfg=cfg, symbol=symbol
    )
    diag["window_start"] = str(df15.index.min())
    diag["window_end"] = str(df15.index.max())
    diag["n_bars"] = int(len(df15))
    return trades, equity, diag


def _fmt(x) -> str:
    if x is None:
        return "NA"
    if isinstance(x, float) and not math.isfinite(x):
        return "inf" if x > 0 else "-inf"
    if isinstance(x, float):
        return f"{x:.3f}"
    return str(x)


def _fmt_pct(x) -> str:
    if x is None:
        return "NA"
    if isinstance(x, float) and not math.isfinite(x):
        return "inf" if x > 0 else "-inf"
    return f"{x*100:+.2f}%"


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> int:
    print(f"[{datetime.now(timezone.utc).isoformat()}] vpvr_funding_hvn_lvn_confluence backtest start")
    print(f"  window = [{WINDOW_START} .. {WINDOW_END}]")
    print(f"  symbols = {SYMBOLS}")
    print(f"  funding_threshold = {FUNDING_THRESHOLD} (0.03% / 8h)")

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

    per_symbol: Dict[str, Dict] = {}
    all_trades: List[Trade] = []
    equity_chunks: List[pd.Series] = []

    for sym in SYMBOLS:
        try:
            trades, equity, diag = _run_symbol(sym, cfg)
        except Exception as exc:
            print(f"  [{sym}] FAILED: {exc}")
            per_symbol[sym] = {"error": str(exc)}
            continue
        per_trade_metrics = _metrics(trades, equity)
        per_symbol[sym] = {
            "diagnostics": diag,
            "metrics": per_trade_metrics,
        }
        all_trades.extend(trades)
        equity_chunks.append(equity)

    if not all_trades:
        print("ERROR: no trades across all symbols — strategy never fired")
        return 1

    # Sort trades chronologically; combined equity is a per-bar pnl concat
    # BUT each symbol's equity curve already reflects only its own
    # mark-to-market. To combine, we treat them as independent fixed-fraction
    # books starting at starting_capital each (per spec: fixed-fraction 1.0
    # per trade). Sum the per-trade PnL against the starting capital.
    all_trades.sort(key=lambda t: t.entry_ts)

    # Combined "equity curve" — simulate a portfolio where each trade is
    # sized at the per-symbol starting capital independently (per spec:
    # "fixed-fraction 1.0 of equity per trade" — i.e. each symbol book
    # starts with STARTING_CAPITAL_USD and trades at 1× notional). This
    # is equivalent to summing per-symbol equity curves, but on a
    # time-aligned bar-by-bar basis which preserves MDD/Sharpe.
    combined_equity = None
    for chunk in equity_chunks:
        # Subtract starting capital so symbols sum correctly, then add back
        # once at the end.
        if combined_equity is None:
            combined_equity = chunk.copy()
        else:
            # Align indices — bars that don't exist in both are carried at
            # last value (forward-fill) so the per-bar sum is honest.
            aligned_self = combined_equity.reindex(chunk.index).ffill()
            aligned_new = chunk.reindex(combined_equity.index).ffill()
            # Sum across the union of indices.
            full_idx = combined_equity.index.union(chunk.index)
            a = combined_equity.reindex(full_idx).ffill()
            b = chunk.reindex(full_idx).ffill()
            combined_equity = a + b - STARTING_CAPITAL_USD  # avoid double-counting initial

    combined_metrics = _metrics(all_trades, combined_equity)

    # Gate evaluation.
    gates = {
        "G1_sharpe_daily": combined_metrics["sharpe_daily"],
        "G1_pass": (combined_metrics["sharpe_daily"] is not None and combined_metrics["sharpe_daily"] >= 1.0),
        "G2_annualized_return": combined_metrics["annualized_return"],
        "G2_pass": (combined_metrics["annualized_return"] is not None and combined_metrics["annualized_return"] >= 0.15),
        "G3_max_drawdown_pct": combined_metrics["max_drawdown_pct"],
        "G3_pass": (combined_metrics["max_drawdown_pct"] is not None and combined_metrics["max_drawdown_pct"] > -0.25),
        "G4_profit_factor": combined_metrics["profit_factor"],
        "G4_pass": (combined_metrics["profit_factor"] is not None and combined_metrics["profit_factor"] > 1.5),
        "G5_n_trades_min": 30,
        "G5_pass": combined_metrics["n_trades"] >= 30,
    }
    n_passed = sum(1 for k in gates if k.endswith("_pass") and gates[k])
    if n_passed == len([k for k in gates if k.endswith("_pass")]):
        verdict = "PROFITABLE"
    elif not gates["G5_pass"]:
        verdict = f"OBSOLETE — fail G5 (n_trades={combined_metrics['n_trades']} < 30)"
    else:
        failed = [k.replace("_pass", "") for k in gates if k.endswith("_pass") and not gates[k]]
        verdict = f"OBSOLETE — fail gates {failed}"

    # Persist artefacts.
    eq_df = combined_equity.to_frame("equity")
    eq_df.index.name = "timestamp"
    eq_df.to_csv(RESULTS_DIR / "equity.csv")
    trades_df = pd.DataFrame([asdict(t) for t in all_trades])
    trades_df.to_csv(RESULTS_DIR / "trades.csv", index=False)

    # Plot combined equity.
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(eq_df.index, eq_df["equity"].values, lw=1.0, color="#1f77b4")
    ax.axhline(
        STARTING_CAPITAL_USD * len(equity_chunks),
        color="grey",
        lw=0.7,
        ls="--",
        alpha=0.6,
        label="starting capital sum",
    )
    ax.set_title(
        f"vpvr_funding_hvn_lvn_confluence — combined {len(SYMBOLS)} symbols\n"
        f"trades={combined_metrics['n_trades']}  "
        f"sharpe_d={_fmt(combined_metrics['sharpe_daily'])}  "
        f"ann={_fmt_pct(combined_metrics['annualized_return'])}  "
        f"PF={_fmt(combined_metrics['profit_factor'])}  "
        f"MDD={_fmt_pct(combined_metrics['max_drawdown_pct'])}"
    )
    ax.set_ylabel("Combined Equity (USD)")
    ax.set_xlabel("Time (15m bars)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    plt.tight_layout()
    plt.savefig(RESULTS_DIR / "equity.png", dpi=110)
    plt.close(fig)

    # metrics.json (combined)
    payload = {
        "variant": "vpvr_funding_hvn_lvn_confluence",
        "strategy_key": "vpvr_funding_hvn_lvn_confluence_20260718",
        "iteration": 1,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source_spec": "SMA-34901",
        "predecessor_specs": ["SMA-34890", "SMA-34793", "SMA-34897"],
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
        "window": {
            "name": WINDOW_NAME,
            "start": str(WINDOW_START),
            "end": str(WINDOW_END),
        },
        "symbols": SYMBOLS,
        "combined_metrics": combined_metrics,
        "gates": gates,
        "verdict": verdict,
        "baseline_comparison": {
            "funding_carry_asym_sma34897": {
                "sharpe_daily": -1.5216,
                "annualized_return": -0.000936,
                "max_drawdown_pct": -0.0272,
                "n_trades": 63,
                "win_rate": 0.4286,
                "profit_factor": 0.59,
                "window": "Q1 2024 hot-funding",
            }
        },
        "per_symbol": per_symbol,
    }
    (RESULTS_DIR / "metrics.json").write_text(json.dumps(payload, indent=2, default=str))

    # summary.txt
    lines: List[str] = []
    lines.append("vpvr_funding_hvn_lvn_confluence — SMA-34901")
    lines.append(f"  Window        = [{WINDOW_START} .. {WINDOW_END}]")
    lines.append(f"  Symbols       = {SYMBOLS}")
    lines.append(f"  Funding gate  = {FUNDING_THRESHOLD} ({FUNDING_THRESHOLD*100:.3f}% / 8h)")
    lines.append(f"  Position size = fixed-fraction 1.0 per trade")
    lines.append("")
    lines.append("=== Per-symbol ===")
    for sym in SYMBOLS:
        info = per_symbol.get(sym, {})
        if "error" in info:
            lines.append(f"  {sym}: ERROR {info['error']}")
            continue
        d = info["diagnostics"]
        m = info["metrics"]
        lines.append(
            f"  {sym:8s}  span={d['window_start']} → {d['window_end']}  bars={d['n_bars']}"
        )
        lines.append(
            f"             diag: long_signals={d['n_long_signals']}  "
            f"funding_above={d['signal_bars_funding_above_threshold']}  "
            f"near_hvn={d['signal_bars_near_hvn']}"
        )
        lines.append(
            f"             met: trades={m['n_trades']}  "
            f"wr={_fmt(m['win_rate'])}  pf={_fmt(m['profit_factor'])}  "
            f"avg_pnl={_fmt_pct(m['avg_pnl_pct'])}  "
            f"med_pnl={_fmt_pct(m['median_pnl_pct'])}"
        )
        lines.append(
            f"             sharpe_d={_fmt(m['sharpe_daily'])}  "
            f"ann={_fmt_pct(m['annualized_return'])}  "
            f"total_ret={_fmt_pct(m['total_return'])}  "
            f"mdd={_fmt_pct(m['max_drawdown_pct'])}"
        )
    lines.append("")
    lines.append("=== Combined (all symbols) ===")
    m = combined_metrics
    lines.append(
        f"  trades={m['n_trades']}  wr={_fmt(m['win_rate'])}  "
        f"pf={_fmt(m['profit_factor'])}  "
        f"avg_pnl={_fmt_pct(m['avg_pnl_pct'])}  "
        f"med_pnl={_fmt_pct(m['median_pnl_pct'])}"
    )
    lines.append(
        f"  sharpe_d={_fmt(m['sharpe_daily'])}  "
        f"ann={_fmt_pct(m['annualized_return'])}  "
        f"total_ret={_fmt_pct(m['total_return'])}  "
        f"mdd={_fmt_pct(m['max_drawdown_pct'])}"
    )
    lines.append("")
    lines.append("=== Acceptance gates ===")
    lines.append(f"  G1 Sharpe_d >= 1.0          : {_fmt(gates['G1_sharpe_daily'])}  pass={gates['G1_pass']}")
    lines.append(f"  G2 annualized >= 15%        : {_fmt_pct(gates['G2_annualized_return'])}  pass={gates['G2_pass']}")
    lines.append(f"  G3 max_drawdown > -25%      : {_fmt_pct(gates['G3_max_drawdown_pct'])}  pass={gates['G3_pass']}")
    lines.append(f"  G4 profit_factor > 1.5      : {_fmt(gates['G4_profit_factor'])}  pass={gates['G4_pass']}")
    lines.append(f"  G5 n_trades >= 30           : {combined_metrics['n_trades']}  pass={gates['G5_pass']}")
    lines.append("")
    lines.append("=== Cross-check vs SMA-34897 (funding_carry_asym baseline) ===")
    lines.append(
        "  baseline (Q1 2024, 1 symbol): Sharpe=-1.522  ann=-0.094%  PF=0.59  MDD=-2.72%  n=63"
    )
    lines.append(
        f"  confluence (hot 23-24, 3 symbols): Sharpe={_fmt(combined_metrics['sharpe_daily'])}  "
        f"ann={_fmt_pct(combined_metrics['annualized_return'])}  "
        f"PF={_fmt(combined_metrics['profit_factor'])}  "
        f"MDD={_fmt_pct(combined_metrics['max_drawdown_pct'])}  "
        f"n={combined_metrics['n_trades']}"
    )
    lines.append("")
    lines.append(f"VERDICT: {verdict}")

    summary = "\n".join(lines) + "\n"
    (RESULTS_DIR / "summary.txt").write_text(summary)
    print(summary)
    print(f"  wrote {RESULTS_DIR}/metrics.json")
    print(f"  wrote {RESULTS_DIR}/equity.csv")
    print(f"  wrote {RESULTS_DIR}/trades.csv")
    print(f"  wrote {RESULTS_DIR}/equity.png")
    print(f"[{datetime.now(timezone.utc).isoformat()}] vpvr_funding_hvn_lvn_confluence backtest done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
