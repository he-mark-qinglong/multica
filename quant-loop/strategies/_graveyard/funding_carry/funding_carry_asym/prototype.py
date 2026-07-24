#!/usr/bin/env python3
"""Prototype: VPVR level + funding carry signal -> backtest on 15m bars.

This script wires together three upstream building blocks:

  * SMA-34789 funding-rate series (Binance USDT-M perpetual, 8h cadence)
  * SMA-34790 VPVR level detector (4h HVN/LVN zones)
  * SMA-34793 funding-carry-asym signal (funding > 0.03% at VPVR support)

It runs a long-only backtest on BTCUSDT 15m bars, using 4h VPVR levels
aligned to the 15m grid, and reports the metrics requested in SMA-34806.

Regime assumptions
------------------
* The signal harvests positive funding carry while price is near a 4h
  high-volume-node (HVN) support level. It therefore prefers choppy or
  mean-reverting regimes where HVN zones act as absorption.
* In strong directional trends HVN levels are often sliced through and
  the VPVR-break stop fires quickly; expect many small losses.
* Funding mean-reversion is the primary timing exit: once the per-8h
  funding rate drops back below the 0.03% threshold the carry rationale
  is gone and the position is closed.
* The 4h VPVR window is 180 bars (~30 days). Level stability depends on
  that window not being dominated by a single fast move; in a sharp
  trend the trailing profile re-centers and support levels can lag.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Make the local strategy modules and shared indicators importable when
# this script is run directly.
HERE = Path(__file__).resolve().parent
_INDICATORS = Path("/home/smark/multica/quant-loop/strategies/_indicators")
for p in (str(HERE), str(_INDICATORS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from build_signals import compute_signal  # noqa: E402
from data_loader import _load_funding  # noqa: E402
from vpvr_levels import VpvrLevel, detect_vpvr_levels  # noqa: E402

# ---------------------------------------------------------------------------
# Reproducible config block.
# ---------------------------------------------------------------------------
CONFIG: Dict = {
    "symbol": "BTCUSDT",
    "timeframe": "15m",
    "vpvr_timeframe": "4h",
    "window_days": 30,
    "funding_threshold": 0.0003,  # 0.03% per 8h funding event
    "support_kind": "HVN",
    "proximity_atr": 1.0,
    "atr_period": 14,
    "vpvr_window_bars_4h": 180,
    "vpvr_snapshot_every_bars_4h": 6,  # once per day on 4h bars
    "vpvr_bins": 24,
    "vpvr_hvn_quantile": 0.85,
    "vpvr_lvn_quantile": 0.15,
    "vpvr_num_hvn": 3,
    "vpvr_num_lvn": 3,
    "stop_atr_k": 1.0,
    "max_hold_bars": 8,
    "fee_bps_per_fill": 4.0,
    "slippage_bps_per_fill": 1.0,
    "funding_carry_bps_per_bar": 0.01,
    "starting_capital_usd": 100000.0,
    "risk_target_pct": 0.005,
    "cooldown_bars": 5,
    "data_paths": {
        "ohlcv_15m": "/home/smark/multica/quant-loop/strategies/funding_carry_asym/data/BTCUSDT__15m.parquet",
        "ohlcv_4h": "/home/smark/multica/quant-loop/live_data/BTCUSDT_4h.parquet",
        "funding": "/home/smark/multica/quant-loop/data/funding/BTCUSDT.parquet",
    },
    "output_path": "/home/smark/multica/quant-loop/strategies/funding_carry_asym/results/prototype_15m.json",
}


# ---------------------------------------------------------------------------
# Data loading.
# ---------------------------------------------------------------------------
def _load_ohlcv(path: Path, timeframe: str) -> pd.DataFrame:
    """Load OHLCV parquet and return a tz-naive DatetimeIndex frame."""
    df = pd.read_parquet(path)
    if "open_time" in df.columns:
        df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=False)
        df = df.set_index("ts")
    df = df.sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    keep = ["open", "high", "low", "close", "volume"]
    present = [c for c in keep if c in df.columns]
    if len(present) != len(keep):
        raise ValueError(f"missing OHLCV columns in {path}; present={list(df.columns)}")
    return df[present].astype(np.float64)


def _load_funding_15m(path: Path, idx_15m: pd.DatetimeIndex) -> pd.Series:
    """Load Binance funding events and ffill onto the 15m index.

    The returned series is shifted by one bar so that bar `t` only sees
    the funding rate paid strictly before bar `t` opens.
    """
    funding = _load_funding(CONFIG["symbol"])
    funding = funding[["fundingRate"]].astype(np.float64)
    if funding.index.tz is not None:
        funding.index = funding.index.tz_convert(None)
    aligned = funding.reindex(idx_15m, method="ffill")
    return aligned["fundingRate"].fillna(0.0).astype(np.float64).shift(1)


# ---------------------------------------------------------------------------
# 4h VPVR levels aligned to the 15m grid.
# ---------------------------------------------------------------------------
def _compute_4h_vpvr_levels(df_4h: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    """Compute rolling 4h VPVR levels at a snapshot cadence.

    Returns a DataFrame indexed by 4h snapshot timestamp with columns
    ``hvn_top/bot/mid``, ``lvn_top/bot/mid`` and ``levels`` (list of
    VpvrLevel objects). The result is shifted by one snapshot so that a
    15m bar never sees the level computed from its own 4h candle.
    """
    window = int(cfg["vpvr_window_bars_4h"])
    stride = max(1, int(cfg["vpvr_snapshot_every_bars_4h"]))
    snapshot_idx = df_4h.index[::stride]
    if len(df_4h.index) and df_4h.index[-1] not in snapshot_idx:
        snapshot_idx = snapshot_idx.append(pd.DatetimeIndex([df_4h.index[-1]]))

    pos = {ts: i for i, ts in enumerate(df_4h.index)}
    out = {
        "hvn_top": np.full(len(snapshot_idx), np.nan),
        "hvn_bot": np.full(len(snapshot_idx), np.nan),
        "hvn_mid": np.full(len(snapshot_idx), np.nan),
        "lvn_top": np.full(len(snapshot_idx), np.nan),
        "lvn_bot": np.full(len(snapshot_idx), np.nan),
        "lvn_mid": np.full(len(snapshot_idx), np.nan),
        "levels": np.full(len(snapshot_idx), None, dtype=object),
    }

    for k, ts in enumerate(snapshot_idx):
        end = pos[ts]
        start = max(0, end - window + 1)
        if end - start + 1 < max(20, window // 4):
            continue
        try:
            lv = detect_vpvr_levels(
                pd.DataFrame({
                    "high": df_4h["high"].iloc[start: end + 1],
                    "low": df_4h["low"].iloc[start: end + 1],
                    "volume": df_4h["volume"].iloc[start: end + 1],
                }),
                num_bins=int(cfg["vpvr_bins"]),
                hvn_quantile=float(cfg["vpvr_hvn_quantile"]),
                lvn_quantile=float(cfg["vpvr_lvn_quantile"]),
                num_hvn=int(cfg["vpvr_num_hvn"]),
                num_lvn=int(cfg["vpvr_num_lvn"]),
            )
        except (ValueError, ZeroDivisionError):
            continue

        out["levels"][k] = lv
        hvns = [x for x in lv if x.kind == "HVN"]
        lvns = [x for x in lv if x.kind == "LVN"]
        if hvns:
            top = max(x.price_high for x in hvns)
            bot = min(x.price_low for x in hvns)
            out["hvn_top"][k] = top
            out["hvn_bot"][k] = bot
            out["hvn_mid"][k] = 0.5 * (top + bot)
        if lvns:
            top = max(x.price_high for x in lvns)
            bot = min(x.price_low for x in lvns)
            out["lvn_top"][k] = top
            out["lvn_bot"][k] = bot
            out["lvn_mid"][k] = 0.5 * (top + bot)

    snap = pd.DataFrame(out, index=snapshot_idx)
    # No-look-ahead: the level *used* at snapshot t is computed from the
    # window ending at the previous snapshot.
    return snap.shift(1)


def _atr_ohlcv(df: pd.DataFrame, period: int) -> pd.Series:
    """Rolling-mean ATR using previous close (no look-ahead)."""
    h = df["high"].astype(np.float64)
    l = df["low"].astype(np.float64)
    c = df["close"].astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean().rename("atr")


def _build_signals_15m(
    df_15m: pd.DataFrame,
    df_4h: pd.DataFrame,
    cfg: Dict,
) -> pd.DataFrame:
    """Build the per-15m signal using 4h VPVR levels.

    Steps:
      1. Compute 4h VPVR levels on a rolling/snapshot grid.
      2. Shift and ffill those levels onto the 15m index.
      3. Compute 15m ATR.
      4. Align funding onto 15m and shift(1).
      5. Group 15m bars by their aligned 4h snapshot and call
         ``compute_signal`` for each group.
    """
    close = df_15m["close"].astype(np.float64)

    snap_shifted = _compute_4h_vpvr_levels(df_4h, cfg)
    snap_per_bar = snap_shifted.reindex(df_15m.index).ffill()

    atr = _atr_ohlcv(df_15m, int(cfg["atr_period"]))
    funding = _load_funding_15m(Path(cfg["data_paths"]["funding"]), df_15m.index)

    # Helper: re-materialise VpvrLevel objects from the per-bar snapshot.
    def _levels_at_bar(ts) -> List[VpvrLevel]:
        row = snap_per_bar.loc[ts]
        lv: List[VpvrLevel] = []
        for kind, top_col, bot_col, mid_col in (
            ("HVN", "hvn_top", "hvn_bot", "hvn_mid"),
            ("LVN", "lvn_top", "lvn_bot", "lvn_mid"),
        ):
            top = row.get(top_col, np.nan)
            bot = row.get(bot_col, np.nan)
            mid = row.get(mid_col, np.nan)
            if np.isfinite(top) and np.isfinite(bot):
                lv.append(VpvrLevel(
                    kind=kind,
                    price_low=float(bot),
                    price_high=float(top),
                    price_center=float(mid) if np.isfinite(mid) else 0.5 * (float(top) + float(bot)),
                    volume=0.0,
                    score=1.0,
                ))
        # Also include the raw stored list if available (contains richer metadata).
        raw = row.get("levels")
        if isinstance(raw, list):
            lv = [x for x in raw if x.kind in ("HVN", "LVN")]
        return lv

    # Build levels once per unique snapshot timestamp.
    unique_lv = {ts: _levels_at_bar(ts) for ts in snap_per_bar.index.unique()}

    # Group bars by their aligned snapshot timestamp to share compute.
    snapshot_groups = snap_per_bar["hvn_mid"].groupby(snap_per_bar["hvn_mid"].index).groups

    out_signal = pd.Series(0, index=df_15m.index, dtype=np.int64)
    out_funding = pd.Series(np.nan, index=df_15m.index, dtype=np.float64)
    out_funding_ok = pd.Series(False, index=df_15m.index, dtype=bool)
    out_support_px = pd.Series(np.nan, index=df_15m.index, dtype=np.float64)
    out_support_kind = pd.Series("", index=df_15m.index, dtype=object)
    out_support_dist = pd.Series(np.nan, index=df_15m.index, dtype=np.float64)
    out_near = pd.Series(False, index=df_15m.index, dtype=bool)
    out_atr = atr.copy()
    out_hvn_top = snap_per_bar["hvn_top"].copy()
    out_hvn_bot = snap_per_bar["hvn_bot"].copy()

    for ts, group_idx in snapshot_groups.items():
        levels = unique_lv.get(ts, [])
        sub_close = close.loc[group_idx]
        sub_funding = funding.loc[group_idx]
        sub_atr = atr.loc[group_idx]
        sig = compute_signal(
            sub_close,
            sub_funding,
            levels,
            funding_threshold=float(cfg["funding_threshold"]),
            support_kind=str(cfg["support_kind"]),
            proximity_atr=float(cfg["proximity_atr"]),
            atr=sub_atr,
        )
        out_signal.loc[group_idx] = sig["signal"].values
        out_funding.loc[group_idx] = sig["funding"].values
        out_funding_ok.loc[group_idx] = sig["funding_above_threshold"].values
        out_support_px.loc[group_idx] = sig["support_level_price"].values
        out_support_kind.loc[group_idx] = sig["support_level_kind"].values
        out_support_dist.loc[group_idx] = sig["support_distance_atr"].values
        out_near.loc[group_idx] = sig["near_support"].values
        if "atr" in sig.columns:
            out_atr.loc[group_idx] = sig["atr"].values

    return pd.DataFrame({
        "signal": out_signal,
        "funding": out_funding,
        "funding_above_threshold": out_funding_ok,
        "support_level_price": out_support_px,
        "support_level_kind": out_support_kind,
        "support_distance_atr": out_support_dist,
        "near_support": out_near,
        "atr": out_atr,
        "hvn_top": out_hvn_top,
        "hvn_bot": out_hvn_bot,
    })


# ---------------------------------------------------------------------------
# Backtest state machine.
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    symbol: str
    direction: str
    entry_ts: str
    entry_price: float
    exit_ts: str
    exit_price: float
    pnl_pct: float
    bars_held: int
    exit_reason: str
    funding_at_entry: float
    support_level_price: float
    support_distance_atr: float
    risk_pct: float
    r_multiple: float


def _run_backtest(
    df_15m: pd.DataFrame,
    sig: pd.DataFrame,
    cfg: Dict,
) -> Tuple[List[Trade], np.ndarray, Dict]:
    """Long-only state machine with the SMA-34806 exit rules.

    Exits:
      (a) funding mean-reverts below threshold
      (b) price breaks below the VPVR support level by ``stop_atr_k * ATR``
      (c) max-hold bars elapsed
    """
    close = df_15m["close"].astype(np.float64).values
    sig_arr = sig["signal"].astype(np.int64).values
    funding_arr = sig["funding"].fillna(0.0).astype(np.float64).values
    support_px_arr = sig["support_level_price"].astype(np.float64).values
    atr_arr = sig["atr"].astype(np.float64).values
    near_support_arr = sig["near_support"].astype(bool).values

    fee = float(cfg["fee_bps_per_fill"]) / 10000.0
    slip = float(cfg["slippage_bps_per_fill"]) / 10000.0
    round_trip_cost = 2.0 * (fee + slip)
    fund_carry_per_bar = float(cfg["funding_carry_bps_per_bar"]) / 10000.0
    stop_k = float(cfg["stop_atr_k"])
    max_hold = int(cfg["max_hold_bars"])
    funding_threshold = float(cfg["funding_threshold"])
    cooldown = int(cfg["cooldown_bars"])
    risk_target = float(cfg["risk_target_pct"])
    starting_capital = float(cfg["starting_capital_usd"])

    # 4h VPVR levels are computed on the full 4h history, so the only
    # 15m-side warmup required is the ATR rolling window.
    warmup = int(cfg["atr_period"]) + 1

    trades: List[Trade] = []
    equity = [starting_capital]
    pos = 0
    entry_idx: Optional[int] = None
    entry_px = 0.0
    bars_held = 0
    bars_since_exit = cooldown
    entry_fd = 0.0
    entry_sup_px = 0.0
    entry_sup_dist = 0.0
    entry_risk_pct = 0.0

    for i in range(1, len(df_15m)):
        if i < warmup:
            equity.append(equity[-1])
            continue

        px = float(close[i])
        at = float(atr_arr[i]) if np.isfinite(atr_arr[i]) else 0.0
        sig_i = int(sig_arr[i])

        if pos == 0:
            bars_since_exit += 1
            if bars_since_exit >= cooldown and sig_i == 1 and at > 0:
                pos = +1
                entry_idx = i
                entry_px = px
                bars_held = 0
                entry_fd = float(funding_arr[i])
                entry_sup_px = float(support_px_arr[i]) if np.isfinite(support_px_arr[i]) else float("nan")
                entry_sup_dist = float(sig["support_distance_atr"].iat[i]) if np.isfinite(sig["support_distance_atr"].iat[i]) else float("nan")
                stop_px = entry_sup_px - stop_k * at
                entry_risk_pct = abs(entry_px - stop_px) / entry_px if entry_px > 0 else 0.0
        else:
            bars_held += 1
            move = (px / entry_px - 1.0) * pos
            exit_now = False
            exit_reason = ""

            # (a) funding mean-reversion below threshold
            if funding_arr[i] < funding_threshold:
                exit_now = True
                exit_reason = "funding_revert"
            # (b) price breaks below VPVR level by k * ATR
            elif at > 0 and px < entry_sup_px - stop_k * at:
                exit_now = True
                exit_reason = "vpvr_break"
            # (c) max-hold bars
            elif bars_held >= max_hold:
                exit_now = True
                exit_reason = "time_stop"

            if exit_now:
                gross = move
                funding_carry = -fund_carry_per_bar * bars_held * pos
                net = gross - round_trip_cost + funding_carry
                r_multiple = net / entry_risk_pct if entry_risk_pct > 0 else float("nan")
                trades.append(Trade(
                    symbol=cfg["symbol"],
                    direction="long",
                    entry_ts=str(df_15m.index[entry_idx]),
                    entry_price=entry_px,
                    exit_ts=str(df_15m.index[i]),
                    exit_price=px,
                    pnl_pct=float(net),
                    bars_held=bars_held,
                    exit_reason=exit_reason,
                    funding_at_entry=entry_fd,
                    support_level_price=entry_sup_px,
                    support_distance_atr=entry_sup_dist,
                    risk_pct=float(entry_risk_pct),
                    r_multiple=float(r_multiple),
                ))
                equity.append(equity[-1] * (1.0 + risk_target * net))
                pos = 0
                entry_idx = None
                bars_since_exit = 0
                continue

        if pos != 0:
            bar_pnl = (px / float(close[i - 1]) - 1.0) * pos
            equity.append(equity[-1] * (1.0 + risk_target * bar_pnl))
        else:
            equity.append(equity[-1])

    diagnostics = {
        "n_long_signals": int((sig["signal"] == 1).sum()),
        "signal_bars_funding_above_threshold": int(sig["funding_above_threshold"].sum()),
        "signal_bars_near_support": int(sig["near_support"].sum()),
        "warmup_bars": warmup,
    }
    return trades, np.array(equity, dtype=np.float64), diagnostics


# ---------------------------------------------------------------------------
# Metrics.
# ---------------------------------------------------------------------------
def _daily_resampled_sharpe(equity: np.ndarray, idx: pd.DatetimeIndex) -> float:
    series = pd.Series(equity, index=idx, dtype=np.float64)
    daily_eq = series.resample("1D").last().dropna()
    if len(daily_eq) < 2:
        return 0.0
    rets = daily_eq.pct_change().dropna()
    if rets.std() == 0 or not np.isfinite(rets.std()):
        return 0.0
    return float(rets.mean() / rets.std() * math.sqrt(365.25))


def _compute_metrics(
    trades: List[Trade],
    equity: np.ndarray,
    idx: pd.DatetimeIndex,
    cfg: Dict,
) -> Dict:
    starting = float(equity[0]) if len(equity) else 0.0
    final = float(equity[-1]) if len(equity) else 0.0
    n_trades = len(trades)

    if len(equity) < 2 or starting <= 0:
        return {
            "n_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "mean_r": 0.0,
            "mean_pnl_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "total_return": 0.0,
            "annualized_return": 0.0,
            "sharpe_daily": 0.0,
            "avg_bars_held": 0.0,
        }

    total_return = (final / starting) - 1.0
    eq_idx = idx[: len(equity)]
    daily_eq = pd.Series(equity, index=eq_idx, dtype=np.float64).resample("1D").last().dropna()
    if len(daily_eq) >= 2:
        n_days = max(1, (daily_eq.index[-1] - daily_eq.index[0]).days)
        n_years = n_days / 365.25
    else:
        n_years = len(equity) / (365.25 * 1440 / 15)
    annualized = (final / starting) ** (1.0 / n_years) - 1.0 if n_years > 0 else 0.0

    sharpe = _daily_resampled_sharpe(equity, eq_idx)
    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / running_max
    max_dd_pct = float(np.min(drawdowns)) * 100.0 if drawdowns.size else 0.0

    if n_trades:
        pnls = np.array([t.pnl_pct for t in trades], dtype=np.float64)
        rs = np.array([t.r_multiple for t in trades], dtype=np.float64)
        gross_profit = float(pnls[pnls > 0].sum())
        gross_loss = float(abs(pnls[pnls < 0].sum()))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        win_rate = float((pnls > 0).sum() / n_trades)
        mean_r = float(np.nanmean(rs)) if np.any(np.isfinite(rs)) else 0.0
        mean_pnl_pct = float(np.mean(pnls))
        avg_bars_held = float(np.mean([t.bars_held for t in trades]))
    else:
        profit_factor = 0.0
        win_rate = 0.0
        mean_r = 0.0
        mean_pnl_pct = 0.0
        avg_bars_held = 0.0

    return {
        "n_trades": n_trades,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 4) if math.isfinite(profit_factor) else None,
        "mean_r": round(mean_r, 4),
        "mean_pnl_pct": round(mean_pnl_pct, 6),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized, 6),
        "sharpe_daily": round(sharpe, 4),
        "avg_bars_held": round(avg_bars_held, 2),
    }


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (np.floating,)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return obj


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
def main() -> int:
    cfg = CONFIG

    # Load data.
    df_15m_full = _load_ohlcv(Path(cfg["data_paths"]["ohlcv_15m"]), cfg["timeframe"])
    df_4h_full = _load_ohlcv(Path(cfg["data_paths"]["ohlcv_4h"]), cfg["vpvr_timeframe"])

    # Restrict to the requested window.
    end = df_15m_full.index.max()
    start = end - pd.Timedelta(days=cfg["window_days"])
    df_15m = df_15m_full.loc[start:end].copy()
    df_4h = df_4h_full.loc[: df_15m.index.max()].copy()

    print(f"[prototype] 15m bars={len(df_15m)} range={df_15m.index[0]} -> {df_15m.index[-1]}")
    print(f"[prototype] 4h bars={len(df_4h)} range={df_4h.index[0]} -> {df_4h.index[-1]}")

    # Signals.
    sig = _build_signals_15m(df_15m, df_4h, cfg)
    print(
        f"[prototype] signals: long={int((sig['signal'] == 1).sum())} "
        f"funding_above={int(sig['funding_above_threshold'].sum())} "
        f"near_support={int(sig['near_support'].sum())}"
    )

    # Backtest.
    trades, equity, diagnostics = _run_backtest(df_15m, sig, cfg)
    metrics = _compute_metrics(trades, equity, df_15m.index, cfg)

    # Prepare output.
    payload = {
        "variant": "funding_carry_asym_15m_prototype",
        "source_spec": "SMA-34806",
        "symbol": cfg["symbol"],
        "timeframe": cfg["timeframe"],
        "vpvr_timeframe": cfg["vpvr_timeframe"],
        "window_days": cfg["window_days"],
        "span_start": str(df_15m.index[0]),
        "span_end": str(df_15m.index[-1]),
        "n_bars": len(df_15m),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": cfg,
        "diagnostics": diagnostics,
        "metrics": metrics,
        "trades": [asdict(t) for t in trades],
    }

    out_path = Path(cfg["output_path"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(_sanitize(payload), indent=2))
    print(f"[prototype] wrote {out_path}")

    # Human-readable summary.
    print("\n=== Headline metrics ===")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print(f"  trades: {len(trades)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
