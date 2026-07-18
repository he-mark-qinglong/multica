"""Single-TF backtest engine for loid_vpvr_confluence_20260717 (SMA-34803).

Public API:
    VARIANT_KEY
    run_backtest(df: pd.DataFrame, cfg: dict) -> dict

Runs two variants on the same annotated frame:

  - ``iceberg_only``         long on every LOID flag, no VPVR filter
  - ``iceberg_vpvr_confluence``  long at HVN, short at LVN

Both reuse the upstream ``iceberg_detector`` (SMA-34796) and
``vpvr_levels`` (SMA-34790) modules via ``build_signals``.

Cost convention matches the wider framework: 4 bps fee + 1 bp
slippage per fill, applied on entry and exit. Sizing is a fixed
``risk_target_pct`` of equity, bar-marked.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from build_signals import build_signals

VARIANT_KEY = "loid_vpvr_confluence_20260717"


@dataclass
class Trade:
    variant: str
    symbol: str
    direction: str
    entry_ts: str
    entry_price: float
    exit_ts: str
    exit_price: float
    pnl_pct: float
    bars_held: int
    exit_reason: str
    iceberg_evidence: str
    vpvr_level: str
    vpvr_distance_atr: float


def _state_machine(
    df: pd.DataFrame,
    sig_col: str,
    sig_df: pd.DataFrame,
    cfg: dict,
    p: dict,
    label: str,
) -> dict:
    """Generic state machine driven by a single ``signal`` column
    (+1 / -1 / 0) from ``sig_df``. Emits trades + equity curve.
    """
    sym = cfg["instruments"][0]
    close = df["close"].astype(np.float64)
    atr = sig_df["atr"].astype(np.float64)
    hvn_mid = sig_df["hvn_mid"]
    lvn_mid = sig_df["lvn_mid"]
    iceberg_flag = sig_df["iceberg_flag"]
    side_proxy = sig_df["side_proxy"]
    volume_zscore = sig_df["volume_zscore"]
    range_ratio = sig_df["range_ratio"]

    sig_arr = sig_df[sig_col].astype(np.int64).values
    close_arr = close.values
    atr_arr = atr.values

    fee = p["fee_bps_per_fill"] / 10000.0
    slip = p["slippage_bps_per_fill"] / 10000.0
    round_trip_cost = 2.0 * (fee + slip)
    risk_target = p["risk_target_pct"]

    max_hold = int(p.get("max_hold_bars", 30))
    cooldown = int(p["cooldown_bars"])

    trades: List[Trade] = []
    equity = [float(cfg["starting_capital_usd"])]
    pos = 0
    entry_idx: Optional[int] = None
    entry_px = 0.0
    bars_held = 0
    bars_since_exit = cooldown
    entry_evidence = ""
    entry_level = ""
    entry_dist_atr = 0.0

    warmup = max(p["iceberg_lookback"], p["vpvr_window_bars"], p["atr_period"]) + 1

    for i in range(1, len(df)):
        if i < warmup:
            equity.append(equity[-1])
            continue

        px = float(close_arr[i])
        at = float(atr_arr[i]) if np.isfinite(atr_arr[i]) else 0.0
        sig_i = int(sig_arr[i])

        if pos == 0:
            bars_since_exit += 1
            if bars_since_exit >= cooldown and sig_i != 0 and at > 0:
                pos = sig_i
                entry_idx = i
                entry_px = px
                bars_held = 0
                iceb_now = bool(iceberg_flag.iloc[i])
                sp = side_proxy.iloc[i]
                vz = float(volume_zscore.iloc[i]) if np.isfinite(volume_zscore.iloc[i]) else float("nan")
                rr = float(range_ratio.iloc[i]) if np.isfinite(range_ratio.iloc[i]) else float("nan")
                entry_evidence = (
                    f"iceberg={int(iceb_now)} side={sp} "
                    f"vz={vz:.2f} rr={rr:.2f}"
                )
                if sig_i == 1:
                    hm = float(hvn_mid.iloc[i]) if np.isfinite(hvn_mid.iloc[i]) else float("nan")
                    entry_level = f"HVN_mid={hm:.2f}"
                    entry_dist_atr = float((px - hm) / at) if np.isfinite(hm) and at > 0 else float("nan")
                else:
                    lm = float(lvn_mid.iloc[i]) if np.isfinite(lvn_mid.iloc[i]) else float("nan")
                    entry_level = f"LVN_mid={lm:.2f}"
                    entry_dist_atr = float((px - lm) / at) if np.isfinite(lm) and at > 0 else float("nan")
        else:
            bars_held += 1
            move = (px / entry_px - 1.0) * pos
            exit_now = False
            exit_reason = ""
            if move >= p["take_profit_atr_k"] * (at / entry_px):
                exit_now = True
                exit_reason = "take_profit"
            elif move <= -p["hard_stop_atr_k"] * (at / entry_px):
                exit_now = True
                exit_reason = "hard_stop"
            elif bars_held >= max_hold:
                exit_now = True
                exit_reason = "time_stop"

            if exit_now:
                gross = move
                net = gross - round_trip_cost
                trades.append(Trade(
                    variant=label,
                    symbol=sym,
                    direction="long" if pos == 1 else "short",
                    entry_ts=str(df.index[entry_idx]),
                    entry_price=entry_px,
                    exit_ts=str(df.index[i]),
                    exit_price=px,
                    pnl_pct=float(net),
                    bars_held=bars_held,
                    exit_reason=exit_reason,
                    iceberg_evidence=entry_evidence,
                    vpvr_level=entry_level,
                    vpvr_distance_atr=entry_dist_atr,
                ))
                equity.append(equity[-1] * (1.0 + risk_target * net))
                pos = 0
                entry_idx = None
                bars_since_exit = 0
                continue

        if pos != 0:
            bar_pnl = (px / float(close_arr[i - 1]) - 1.0) * pos
            equity.append(equity[-1] * (1.0 + risk_target * bar_pnl))
        else:
            equity.append(equity[-1])

    n_iceberg = int(iceberg_flag.sum())
    n_long_lc = int((sig_df[sig_col] == 1).sum())
    n_short_lc = int((sig_df[sig_col] == -1).sum())

    return {
        "variant_key": label,
        "iteration": cfg["iteration"],
        "symbol": sym,
        "n_bars": len(df),
        "span_start": str(df.index[0]),
        "span_end": str(df.index[-1]),
        "trades": [asdict(t) for t in trades],
        "equity": np.array(equity, dtype=np.float64),
        "diagnostics": {
            "n_iceberg_bars": n_iceberg,
            "n_long_signals": n_long_lc,
            "n_short_signals": n_short_lc,
        },
    }


def run_backtest(df: pd.DataFrame, cfg: dict) -> dict:
    """Run the LOID baseline + the LOID+VPVR confluence variant on
    the same annotated frame and return a combined envelope.
    """
    p = cfg["params"]
    df = df.copy()
    if "openTime" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
        df = df.set_index("openTime")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(np.float64)
    df = df.sort_index()

    sig = build_signals(df, p)
    base = _state_machine(df, "signal_lo", sig, cfg, p, "iceberg_only")
    conf = _state_machine(df, "signal_lc", sig, cfg, p, "iceberg_vpvr_confluence")

    return {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "symbol": cfg["instruments"][0],
        "n_bars": len(df),
        "span_start": str(df.index[0]),
        "span_end": str(df.index[-1]),
        "iceberg_only": base,
        "iceberg_vpvr_confluence": conf,
        "diagnostics": {
            "n_iceberg_bars": int(sig["iceberg_flag"].sum()),
            "n_near_hvn": int(sig["near_hvn"].sum()),
            "n_near_lvn": int(sig["near_lvn"].sum()),
        },
    }


__all__ = ["VARIANT_KEY", "Trade", "run_backtest"]
