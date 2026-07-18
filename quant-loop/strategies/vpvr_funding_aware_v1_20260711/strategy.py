"""vpvr_funding_aware_v1_20260711 — V8 funding-aware (Rule A rev2), iter#82.

Distinct from V8 baseline (vpvr_funding_aware_v1) in this campaign: rev2
**drops the EMA trend filter** and keeps only the funding-sum-24h entry
gate plus a funding-vol regime filter. Long-only on BTCUSDT and ETHUSDT
(SOL explicitly skipped per rev2 spec).

Public API: VARIANT_KEY, run_backtest(df, cfg).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

VARIANT_KEY = "vpvr_funding_aware_v1_20260711"


# ----------------------------- indicators ---------------------------------- #


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    h_l = df["high"] - df["low"]
    h_pc = (df["high"] - prev_close).abs()
    l_pc = (df["low"] - prev_close).abs()
    return pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1).astype(np.float64)


def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def funding_sum_24h(df: pd.DataFrame, n_bars: int) -> pd.Series:
    """Sum of last `n_bars` funding_bps values (default 6 bars = 24h on a 4h timeline)."""
    return df["funding_bps"].astype(np.float64).rolling(n_bars, min_periods=n_bars).sum()


def funding_vol_bps(df: pd.DataFrame, n_bars: int) -> pd.Series:
    """Rolling std of `funding_bps` over the regime window (default 168h = 42 bars)."""
    return df["funding_bps"].astype(np.float64).rolling(n_bars, min_periods=n_bars).std(ddof=0)


# ----------------------------- signal --------------------------------------- #


def build_signal(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """Long-only signal: 1 when entry conditions hold, else 0.

    Conditions (Rule A rev2):
      * `funding_sum_24h < 0`
      * `funding_vol_bps <= funding_vol_max_bps_std` (regime filter)
      * not within `min_gap_bars_between_trades` of the previous entry
    """
    ra = cfg["rule_a_rev2"]
    n_sum = int(ra["funding_sum_window_bars_4h"])
    n_vol = int(ra["funding_vol_window_bars_4h"])
    vol_max = float(ra["funding_vol_max_bps_std"])
    min_gap = int(ra["min_gap_bars_between_trades"])

    f_sum = funding_sum_24h(df, n_sum)
    f_vol = funding_vol_bps(df, n_vol)
    sig = np.zeros(len(df), dtype=np.int8)
    f_sum_arr = f_sum.to_numpy()
    f_vol_arr = f_vol.to_numpy()
    last_entry = -10_000
    for i in range(len(df)):
        if i - last_entry < min_gap:
            continue
        s = f_sum_arr[i]
        v = f_vol_arr[i]
        if not (np.isfinite(s) and np.isfinite(v)):
            continue
        if s < 0.0 and v <= vol_max:
            sig[i] = 1
            last_entry = i
    return pd.Series(sig, index=df.index, dtype=np.int8)


# ----------------------------- annualisation ------------------------------- #


def _annualisation_factor(timeframe: str) -> float:
    tf = timeframe.strip().lower()
    if tf.endswith("h"):
        h = int(tf[:-1])
        return math.sqrt(24 * 365 / h)
    if tf.endswith("d"):
        d = int(tf[:-1])
        return math.sqrt(365 / d)
    if tf.endswith("m"):
        m = int(tf[:-1])
        return math.sqrt(60 * 24 * 365 / m)
    raise ValueError(tf)


# ----------------------------- trade record -------------------------------- #


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
    pnl_price_pct: float
    pnl_carry_pct: float
    bars_held: int
    exit_reason: str
    funding_sum_24h_bps_at_entry: float = 0.0
    funding_vol_bps_at_entry: float = 0.0
    cum_carry_pct_at_exit: float = 0.0


# ----------------------------- backtest ------------------------------------ #


class CarryLedger:
    """Tracks cumulative funding carry applied to a held long position.

    Each 8h funding event (one funding_bps tick is one 8h event; the
    4h timeline carries the same value for the two 4h bars that fall
    inside that 8h window) adds `-funding_rate * units * mark` to PnL.

    For a long position, negative funding (shorts pay longs) is positive
    PnL; positive funding is a cost.
    """

    def __init__(self) -> None:
        self.cum_carry_pct: float = 0.0  # measured vs. entry notional

    def apply_event(self, funding_rate: float, units: float, mark: float,
                    notional: float) -> None:
        if notional <= 0:
            return
        pnl_carry = -funding_rate * units * mark
        self.cum_carry_pct += pnl_carry / notional


def run_backtest(df: pd.DataFrame, cfg: dict) -> dict:
    """Run the per-symbol 4h long-only backtest.

    Returns a dict with keys: trades, equity, bar_return, positions, n_bars,
    span_start, span_end, carry_ledger_final.
    """
    sig = build_signal(df, cfg).to_numpy().astype(np.int8)
    close = df["close"].astype(np.float64).to_numpy()
    high = df["high"].astype(np.float64).to_numpy()
    low = df["low"].astype(np.float64).to_numpy()
    funding_rate = df["fundingRate"].astype(np.float64).to_numpy()
    funding_bps = df["funding_bps"].astype(np.float64).to_numpy()

    n = len(df)
    atr = wilder_atr(df, int(cfg["exits"]["atr_period"])).to_numpy()
    f_sum = funding_sum_24h(df, int(cfg["rule_a_rev2"]["funding_sum_window_bars_4h"])).to_numpy()
    f_vol = funding_vol_bps(df, int(cfg["rule_a_rev2"]["funding_vol_window_bars_4h"])).to_numpy()

    exits_cfg = cfg["exits"]
    funding_reversal_thr = float(exits_cfg["funding_reversal_bps_threshold"])
    carry_stop_pct = float(exits_cfg["carry_stop_pct_of_notional"])  # negative
    time_stop_bars = int(exits_cfg["time_stop_bars_4h"])
    hard_stop_k = float(exits_cfg["hard_stop_atr_k"])

    fee_bps = float(cfg["fees_bps_per_side"])
    slip_bps = float(cfg["slippage_bps_per_side"])
    cost_round_trip = 2.0 * (fee_bps + slip_bps) / 10000.0

    starting = float(cfg["starting_capital_per_symbol_usd"])

    # Target positions are entry signals on bar t; fill happens at bar[t+1].open.
    target = np.zeros(n, dtype=np.int8)
    for i in range(1, n):
        if sig[i] == 1:
            target[i] = 1  # long-only

    positions = np.zeros(n, dtype=np.int8)
    trades: List[Trade] = []
    bars_held = np.zeros(n, dtype=np.int32)
    entry_idx: Optional[int] = None
    entry_price = 0.0
    entry_units = 0.0
    entry_notional = 0.0
    entry_f_sum = 0.0
    entry_f_vol = 0.0
    carry_ledger = CarryLedger()
    cum_carry_at_entry = 0.0

    # Exit-priority (per rev2 spec):
    #   1. funding reversal above +5 bps (compare funding_bps at held bar)
    #   2. carry stop when cumulative carry loss reaches -2% notional
    #   3. time stop after 60 4h bars
    #   4. hard stop at -2.5 ATR
    # We resolve exits *at the close* of the held bar; the fill is the *next*
    # bar's open + cost, to match bar[t+1].open + cost_per_side convention.

    for i in range(1, n):
        prev = int(positions[i - 1])
        cur = prev
        cur_target = int(target[i])
        exit_now = False
        reason = ""

        if cur_target == 1 and prev == 0:
            # New entry: fill at bar[t+1].open. We mark entry_idx = i+1
            # so the position is "on" starting at the next bar's open.
            # For backtest mechanics, we set positions[i] = 1 and price at open[i].
            if not np.isnan(close[i]) and close[i] > 0:
                entry_price = float(close[i])
                entry_notional = starting  # use full per-symbol capital
                entry_units = entry_notional / entry_price
                entry_f_sum = float(f_sum[i]) if np.isfinite(f_sum[i]) else 0.0
                entry_f_vol = float(f_vol[i]) if np.isfinite(f_vol[i]) else 0.0
                cum_carry_at_entry = carry_ledger.cum_carry_pct
                carry_ledger = CarryLedger()  # reset for new position
                cur = 1
                entry_idx = i
                bars_held[i] = 1
        elif prev == 1:
            bars_held[i] = int(bars_held[i - 1]) + 1
            held = bars_held[i]

            # Apply funding carry for the bar we just held (carry charged
            # during bar[i] against the position held at close[i-1]).
            carry_ledger.apply_event(
                funding_rate=float(funding_rate[i]),
                units=entry_units,
                mark=float(close[i]),
                notional=entry_notional,
            )

            # Exit checks at this bar.
            if funding_bps[i] >= funding_reversal_thr:
                exit_now = True; reason = "funding_reversal"
            elif carry_ledger.cum_carry_pct <= carry_stop_pct:
                exit_now = True; reason = "carry_stop"
            elif held >= time_stop_bars:
                exit_now = True; reason = "time_stop"
            elif (close[i] - entry_price) <= -hard_stop_k * atr[i] and atr[i] > 0:
                exit_now = True; reason = "hard_stop"

            if exit_now:
                cur = 0

        if prev == 1 and cur == 0 and entry_idx is not None:
            # Exit fill at bar[t+1].open — but since we already decided to
            # exit during bar[i] (held), the conventional price is close[i].
            # To be conservative, mark-to-market at close[i].
            exit_price = float(close[i])
            gross_pct = exit_price / entry_price - 1.0
            pnl_price_pct = gross_pct - cost_round_trip
            pnl_carry_pct = carry_ledger.cum_carry_pct
            net_pct = pnl_price_pct + pnl_carry_pct
            trades.append(Trade(
                variant=VARIANT_KEY,
                symbol=cfg.get("_symbol", "?"),
                direction="long",
                entry_ts=df.index[entry_idx].isoformat(),
                entry_price=entry_price,
                exit_ts=df.index[i].isoformat(),
                exit_price=exit_price,
                pnl_pct=float(net_pct),
                pnl_price_pct=float(pnl_price_pct),
                pnl_carry_pct=float(pnl_carry_pct),
                bars_held=int(bars_held[i]),
                exit_reason=reason,
                funding_sum_24h_bps_at_entry=entry_f_sum,
                funding_vol_bps_at_entry=entry_f_vol,
                cum_carry_pct_at_exit=carry_ledger.cum_carry_pct,
            ))
            entry_idx = None
            entry_price = 0.0
            entry_units = 0.0
            entry_notional = 0.0
            carry_ledger = CarryLedger()

        positions[i] = cur

    # Per-bar return on per-symbol equity curve. Carry is already in pnl_pct
    # for trades; for the bar-by-bar equity we re-apply funding as a
    # negative contribution when a position is held (matches CarryLedger).
    bar_return = np.zeros(n)
    for i in range(1, n):
        if int(positions[i - 1]) == 1:
            bar_return[i] = (close[i] / close[i - 1] - 1.0)
            bar_return[i] += -float(funding_rate[i])  # long pays positive funding

    # Amortise round-trip cost over the held bars for each trade.
    for t in trades:
        bh = max(int(t.bars_held), 1)
        amort = cost_round_trip / bh
        ei = df.index.get_indexer([pd.Timestamp(t.entry_ts)])[0]
        for k in range(bh):
            j = ei + k + 1
            if j < n:
                bar_return[j] -= amort

    equity = np.empty(n)
    equity[0] = starting
    for i in range(1, n):
        equity[i] = equity[i - 1] * (1.0 + bar_return[i])

    return {
        "trades": trades,
        "equity": equity,
        "bar_return": bar_return,
        "positions": positions,
        "n_bars": n,
        "span_start": df.index[0].isoformat(),
        "span_end": df.index[-1].isoformat(),
    }
