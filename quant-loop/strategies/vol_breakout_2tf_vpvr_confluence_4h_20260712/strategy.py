"""Strategy logic for vol_breakout_2tf_vpvr_confluence_4h_20260712 (iter#84).

V8 is **single-TF 4h**: the backtest loop iterates over 4h bars only.
There is no 1h clock and no merge_asof. All indicators live on the 4h
frame, and the entry/exit ladder runs entirely on 4h closes.

State machine
-------------

At each 4h bar ``t``:

1. **Apply pending fills** scheduled for ``bar[t].open`` (entries/exits
   queued on bar ``t-1``).

2. **Record NAV** after today's fills.

3. **Evaluate signals** on ``close[t]``:

   - If ``state.in_pos`` is True: run the exit priority ladder on the
     4h frame's indicators (``range_low_4h``, ``atr_4h``,
     ``vol_regime_4h``). First match queues an exit for bar ``t+1``.
   - If ``state.in_pos`` is False: check ``long_entry``. If True, queue
     an entry for bar ``t+1``.

4. **End-of-data**: force-close any open positions (every trade must
   appear in the trade list).

Fill convention
---------------

Signal evaluated on ``bar[t].close``. Fill at ``bar[t+1].open + cost``
for entries and ``bar[t+1].open - cost`` for exits. No look-ahead.

Look-ahead discipline
---------------------

- All indicators use ``shift(1)`` so the value at bar ``t`` reflects
  bars ``[t-W, t-1]``.
- VPVR POC's outer ``shift(1)`` already drops the current bar; the
  inner rolling window uses ``[t-window, t-1]``.
- range_high/range_low: ``pd.rolling().max().shift(1)``.

This is enforced in ``run_backtest`` via the ``PendingOrder`` queue.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from indicators import BARS_PER_YEAR_4H, annotate_4h

CONFIG_PATH = Path(__file__).parent / "config.json"


# ---------------------------------------------------------------------------
# Constants — single source of truth.
# ---------------------------------------------------------------------------

SQRT_BARS_PER_YEAR_4H: float = math.sqrt(BARS_PER_YEAR_4H)  # ≈ 46.818


# ---------------------------------------------------------------------------
# Trade dataclass — emitted per closed round-trip.
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    direction: str  # "long" (this strategy is long-only)
    entry_signal_date: pd.Timestamp
    entry_fill_date: pd.Timestamp
    entry_price: float  # net of entry cost
    exit_signal_date: pd.Timestamp
    exit_fill_date: pd.Timestamp
    exit_price: float  # net of exit cost
    reason: str  # which exit rule fired
    pnl_usd: float
    pnl_pct: float  # return on entry price (after costs)
    bars_held: int  # from entry fill to exit fill (4h bars)
    atr_4h_at_entry: float
    vpvr_dist_atr_4h_at_entry: float
    size_units: float
    nav_at_entry: float  # NAV at the fill bar (not signal bar)


# ---------------------------------------------------------------------------
# Per-symbol state and pending-order queue.
# ---------------------------------------------------------------------------

@dataclass
class SymbolState:
    """One per symbol. Tracks in_pos, entry price, and the signal-only
    fields needed to fill an entry on the next bar."""

    symbol: str
    in_pos: bool = False
    entry_price: float = 0.0  # net of entry cost, set at fill
    entry_signal_date: Optional[pd.Timestamp] = None
    entry_fill_date: Optional[pd.Timestamp] = None
    entry_signal_idx: int = -1  # union-bar position of signal
    fill_idx: int = -1  # union-bar position of entry fill
    atr_4h_at_entry: float = 0.0
    vpvr_dist_atr_4h_at_entry: float = 0.0
    realized_vol_4h_at_entry: float = 0.0
    size_units: float = 0.0
    nav_at_entry: float = 0.0


@dataclass
class PendingOrder:
    """A signal that fires on bar[t] but fills on bar[t+1]."""

    symbol: str
    side: str  # "entry" or "exit"
    fill_union_idx: int  # bar index where the fill executes
    reason: str
    signal_date: pd.Timestamp
    signal_close: float
    signal_realized_vol: float
    signal_atr: float


@dataclass
class Portfolio:
    cfg: dict
    states: Dict[str, SymbolState]
    pending: List[PendingOrder] = field(default_factory=list)

    @classmethod
    def from_symbols(cls, symbols: List[str], cfg: dict) -> "Portfolio":
        return cls(cfg=cfg, states={s: SymbolState(symbol=s) for s in symbols})

    @property
    def open_symbols(self) -> List[str]:
        return [s for s, st in self.states.items() if st.in_pos]

    @property
    def max_concurrent(self) -> int:
        return len(self.states)


# ---------------------------------------------------------------------------
# Sizing.
# ---------------------------------------------------------------------------

def vol_target_size(
    nav: float,
    close: float,
    realized_vol: float,
    vol_target_pct: float,
    max_position_pct_nav: float,
) -> float:
    """Vol-targeted position size in *units* (tokens) at the 4h TF.

    Formula:
        size_units_unbounded = vol_target_pct * nav /
                                (close * realized_vol * sqrt(BARS_PER_YEAR_4H))
        size_units_capped    = max_position_pct_nav * nav / close
        size_units           = min(unbounded, capped)
    """
    if not np.isfinite(nav) or nav <= 0:
        return 0.0
    if not np.isfinite(close) or close <= 0:
        return 0.0
    if not np.isfinite(realized_vol) or realized_vol <= 0:
        return 0.0
    size_unbounded = (
        (vol_target_pct * nav)
        / (close * realized_vol * SQRT_BARS_PER_YEAR_4H)
    )
    size_capped = (max_position_pct_nav * nav) / close
    return float(max(0.0, min(size_unbounded, size_capped)))


# ---------------------------------------------------------------------------
# Exit evaluation.
# ---------------------------------------------------------------------------

EXIT_REASON_TREND = "trend_fail"
EXIT_REASON_TRAIL = "trailing_stop"
EXIT_REASON_VOL = "vol_cool"
EXIT_REASON_TIME = "time_stop"


def evaluate_exit(
    state: SymbolState,
    cur_close: float,
    cur_low: float,
    cur_atr: float,
    cur_regime: float,
    cur_range_low: float,
    bars_held: int,
    cfg: dict,
) -> Tuple[bool, str]:
    """V8 exit priority order:
        1. close < range_low_4h            (trend_fail — PRIMARY)
        2. low < entry - 2.0 * ATR_4h     (trailing stop)
        3. vol_regime_4h < 0.8            (vol cooling)
        4. bars_held >= 30                (time stop, 30 * 4h = 120h)
    """
    if not state.in_pos:
        return False, ""

    exit_cfg = cfg["exit"]
    atr_k = float(exit_cfg["atr_trailing_k"])
    time_stop_bars = int(exit_cfg["time_stop_bars"])
    regime_max = float(exit_cfg["vol_regime_max"])

    # 1. trend_fail: close < range_low on 4h TF (PRIMARY)
    if np.isfinite(cur_range_low) and cur_close < cur_range_low:
        return True, EXIT_REASON_TREND

    # 2. trailing stop: low < entry - k * ATR
    if np.isfinite(cur_atr) and np.isfinite(cur_low):
        stop_price = state.entry_price - atr_k * cur_atr
        if cur_low < stop_price:
            return True, f"{EXIT_REASON_TRAIL}<entry-{atr_k}*ATR"

    # 3. vol_cool
    if np.isfinite(cur_regime) and cur_regime < regime_max:
        return True, EXIT_REASON_VOL

    # 4. time stop
    if bars_held >= time_stop_bars:
        return True, f"{EXIT_REASON_TIME}>={time_stop_bars}bars"

    return False, ""


# ---------------------------------------------------------------------------
# Multi-symbol backtest.
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    equity_path: List[Tuple[pd.Timestamp, float]] = field(default_factory=list)
    starting_capital: float = 0.0
    final_equity: float = 0.0

    @property
    def n_trades(self) -> int:
        return len(self.trades)


def _cost_per_side(cfg: dict) -> float:
    return (cfg["fees_bps_per_side"] + cfg["slippage_bps_per_side"]) / 10000.0


def run_backtest(
    data: Dict[str, pd.DataFrame],
    cfg: dict,
    starting_capital: Optional[float] = None,
) -> BacktestResult:
    """Cross-symbol 4h backtest with per-symbol 1-position-max and
    fill-at-next-bar.

    Parameters
    ----------
    data : dict
        ``{symbol: df_4h}``. Each frame is OHLCV (no 1h needed).
    cfg : dict
        Strategy config; ``cfg["instruments"]`` is the symbol list.
    """
    if starting_capital is None:
        starting_capital = float(cfg["starting_capital_usd"])

    symbols = list(data.keys())
    annotated: Dict[str, pd.DataFrame] = {
        s: annotate_4h(data[s], cfg) for s in symbols
    }

    canonical_ts = annotated[symbols[0]].index
    n_bars = len(canonical_ts)

    portfolio = Portfolio.from_symbols(symbols, cfg)
    cost = _cost_per_side(cfg)
    nav = starting_capital
    trades: List[Trade] = []
    equity_path: List[Tuple[pd.Timestamp, float]] = []

    sym_idx_at: Dict[str, Dict[pd.Timestamp, int]] = {
        s: {ts: i for i, ts in enumerate(df.index)}
        for s, df in annotated.items()
    }

    for union_t in range(n_bars):
        ts_t = canonical_ts[union_t]

        # ------------------------------------------------------------------
        # Step 1: process pending fills scheduled for bar t (= open[t]).
        # ------------------------------------------------------------------
        if portfolio.pending:
            still_pending: List[PendingOrder] = []
            for order in portfolio.pending:
                if order.fill_union_idx != union_t:
                    still_pending.append(order)
                    continue
                sym = order.symbol
                if sym not in annotated:
                    still_pending.append(order)
                    continue
                state = portfolio.states[sym]
                df = annotated[sym]
                if ts_t not in sym_idx_at[sym]:
                    still_pending.append(order)
                    continue
                sym_idx = sym_idx_at[sym][ts_t]
                bar = df.iloc[sym_idx]
                open_price = float(bar["open"])

                if order.side == "entry":
                    if state.in_pos:
                        continue
                    fill_price = open_price * (1.0 + cost)
                    rv_at_fill = float(bar["realized_vol_4h"])
                    if not np.isfinite(rv_at_fill) or rv_at_fill <= 0:
                        state.entry_signal_date = None
                        state.entry_signal_idx = -1
                        continue
                    fresh_size = vol_target_size(
                        nav=nav,
                        close=open_price,
                        realized_vol=rv_at_fill,
                        vol_target_pct=cfg["sizing"]["vol_target_annual_pct"],
                        max_position_pct_nav=cfg["sizing"]["max_position_pct_nav"],
                    )
                    if fresh_size <= 0:
                        state.entry_signal_date = None
                        state.entry_signal_idx = -1
                        continue
                    state.entry_price = fill_price
                    state.entry_fill_date = ts_t
                    state.fill_idx = union_t
                    state.atr_4h_at_entry = order.signal_atr
                    state.vpvr_dist_atr_4h_at_entry = float(
                        bar.get("vpvr_dist_atr_4h", np.nan)
                    )
                    state.realized_vol_4h_at_entry = rv_at_fill
                    state.size_units = fresh_size
                    state.nav_at_entry = nav
                    state.in_pos = True
                elif order.side == "exit":
                    if not state.in_pos:
                        continue
                    fill_price = open_price * (1.0 - cost)
                    pnl_pct = (fill_price / state.entry_price) - 1.0
                    pnl_usd = pnl_pct * state.size_units * state.entry_price
                    trades.append(
                        Trade(
                            symbol=sym,
                            direction="long",
                            entry_signal_date=state.entry_signal_date,
                            entry_fill_date=state.entry_fill_date,
                            entry_price=state.entry_price,
                            exit_signal_date=order.signal_date,
                            exit_fill_date=ts_t,
                            exit_price=fill_price,
                            reason=order.reason,
                            pnl_usd=pnl_usd,
                            pnl_pct=pnl_pct,
                            bars_held=union_t - state.fill_idx,
                            atr_4h_at_entry=state.atr_4h_at_entry,
                            vpvr_dist_atr_4h_at_entry=state.vpvr_dist_atr_4h_at_entry,
                            size_units=state.size_units,
                            nav_at_entry=state.nav_at_entry,
                        )
                    )
                    nav += pnl_usd
                    state.in_pos = False
                    state.entry_price = 0.0
                    state.entry_signal_date = None
                    state.entry_fill_date = None
                    state.entry_signal_idx = -1
                    state.fill_idx = -1
                    state.atr_4h_at_entry = 0.0
                    state.vpvr_dist_atr_4h_at_entry = 0.0
                    state.realized_vol_4h_at_entry = 0.0
                    state.size_units = 0.0
                    state.nav_at_entry = 0.0
            portfolio.pending = still_pending

        # Record NAV at end of bar (after today's fills).
        equity_path.append((ts_t, nav))

        # Step 2 & 3: evaluate new signals on close[t]. No signals fired
        # on the final bar (no fill is possible on bar t+1).
        if union_t >= n_bars - 1:
            continue

        for sym in symbols:
            if ts_t not in sym_idx_at[sym]:
                continue
            sym_idx = sym_idx_at[sym][ts_t]
            df = annotated[sym]
            if sym_idx >= len(df):
                continue
            bar = df.iloc[sym_idx]
            state = portfolio.states[sym]

            cur_close = float(bar["close"])
            cur_low = float(bar.get("low", cur_close))
            cur_atr = float(bar.get("atr_4h", np.nan))
            cur_regime_4h = float(bar.get("vol_regime_4h", np.nan))
            cur_range_low = float(bar.get("range_low_4h", np.nan))
            bars_held = (
                union_t - state.fill_idx if state.in_pos and state.fill_idx >= 0 else 0
            )

            if state.in_pos:
                exit_now, reason = evaluate_exit(
                    state=state,
                    cur_close=cur_close,
                    cur_low=cur_low,
                    cur_atr=cur_atr,
                    cur_regime=cur_regime_4h,
                    cur_range_low=cur_range_low,
                    bars_held=bars_held,
                    cfg=cfg,
                )
                if exit_now:
                    portfolio.pending.append(
                        PendingOrder(
                            symbol=sym,
                            side="exit",
                            fill_union_idx=union_t + 1,
                            reason=reason,
                            signal_date=ts_t,
                            signal_close=cur_close,
                            signal_realized_vol=float(
                                bar.get("realized_vol_4h", np.nan)
                            ),
                            signal_atr=cur_atr,
                        )
                    )
            else:
                if bool(bar.get("long_entry", False)):
                    rv = float(bar.get("realized_vol_4h", np.nan))
                    if np.isfinite(rv) and rv > 0:
                        portfolio.pending.append(
                            PendingOrder(
                                symbol=sym,
                                side="entry",
                                fill_union_idx=union_t + 1,
                                reason="long_entry",
                                signal_date=ts_t,
                                signal_close=cur_close,
                                signal_realized_vol=rv,
                                signal_atr=cur_atr,
                            )
                        )
                        state.entry_signal_date = ts_t
                        state.entry_signal_idx = union_t
                        state.atr_4h_at_entry = cur_atr
                        state.vpvr_dist_atr_4h_at_entry = float(
                            bar.get("vpvr_dist_atr_4h", np.nan)
                        )
                        state.realized_vol_4h_at_entry = rv

    # End-of-data: force-close any positions still open.
    last_ts = canonical_ts[-1]
    for sym in symbols:
        state = portfolio.states[sym]
        if not state.in_pos:
            continue
        if sym not in annotated:
            continue
        df = annotated[sym]
        if last_ts not in sym_idx_at[sym]:
            continue
        sym_idx = sym_idx_at[sym][last_ts]
        if sym_idx >= len(df):
            continue
        bar = df.iloc[sym_idx]
        last_close = float(bar["close"])
        exit_price = last_close * (1.0 - cost)
        pnl_pct = (exit_price / state.entry_price) - 1.0
        pnl_usd = pnl_pct * state.size_units * state.entry_price
        trades.append(
            Trade(
                symbol=sym,
                direction="long",
                entry_signal_date=state.entry_signal_date,
                entry_fill_date=state.entry_fill_date,
                entry_price=state.entry_price,
                exit_signal_date=last_ts,
                exit_fill_date=last_ts,
                exit_price=exit_price,
                reason="force_close_eod",
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                bars_held=(n_bars - 1) - state.fill_idx if state.fill_idx >= 0 else 0,
                atr_4h_at_entry=state.atr_4h_at_entry,
                vpvr_dist_atr_4h_at_entry=state.vpvr_dist_atr_4h_at_entry,
                size_units=state.size_units,
                nav_at_entry=state.nav_at_entry,
            )
        )
        nav += pnl_usd
        state.in_pos = False
        state.entry_price = 0.0
        state.entry_signal_date = None
        state.entry_fill_date = None
        state.entry_signal_idx = -1
        state.fill_idx = -1
        state.atr_4h_at_entry = 0.0
        state.vpvr_dist_atr_4h_at_entry = 0.0
        state.realized_vol_4h_at_entry = 0.0
        state.size_units = 0.0
        state.nav_at_entry = 0.0

    return BacktestResult(
        trades=trades,
        equity_path=equity_path,
        starting_capital=starting_capital,
        final_equity=nav,
    )


def run_backtest_single(
    df_4h: pd.DataFrame,
    cfg: dict,
    symbol: str = "TEST",
    starting_capital: Optional[float] = None,
) -> BacktestResult:
    """Wrap a single-symbol 4h frame in a dict and run the multi-symbol
    backtest. Tests use this so they don't have to mock the multi-symbol
    scaffolding."""
    return run_backtest(
        {symbol: df_4h},
        cfg,
        starting_capital=starting_capital,
    )