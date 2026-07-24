"""U5 cycle-46 rebuild — funding-rate oscillator mean-reversion (axis b).

Non-carry axis: take the z-score of the funding rate ITSELF and use
its extremes as a mean-reversion signal on the same asset's price.
We DO NOT assume carry / positioning: we explicitly fade the funding
extremes on the price side. P&L is sourced from price reversion; the
funding event crossed during the hold is treated as friction, not as
a deliberate carry bet.

Event-driven semantics (8h cadence, identical harness philosophy to
the U5 funding-carry rebuild):

  1. Identify funding events on the 8h cadence (00/08/16 UTC).
  2. For each event E_i:
       - compute z_i = (r_i - rolling_mean_N) / rolling_std_N over
         the prior N events (strict no-look-ahead: shift(1) over the
         event series);
       - if z_i > z_in        → SHORT  price at E_i  (fade hot funding)
       - if z_i < -z_in       → LONG   price at E_i  (fade cold funding)
       - hold for H funding events (H = 1, 2, or 3 events ⇒ 8h/16h/24h)
  3. P&L per trade = price move across the holding window
     minus round-trip cost (4 bps fee + 1 bp slippage per fill).
  4. Funding carry is NOT included in the P&L formula. If a holding
     window crosses a funding event, the funding is implicitly paid/
     received by the position, but it is NOT the trade thesis.

This is the cleanest non-carry interpretation of axis (b): funding
extremes are a proxy for over-extension; the strategy bets on
mean reversion of the underlying price rather than on collecting
or paying the funding rate.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

VARIANT_KEY = "funding_oscillator_z_mr_u5_rebuild"

# ---------------------------------------------------------------------------
# Defaults (per spec)
# ---------------------------------------------------------------------------
DEFAULT_Z_IN: float = 2.0                  # enter when |z| > z_in
DEFAULT_LOOKBACK_EVENTS: int = 60           # rolling window over prior N events
DEFAULT_HOLDING_EVENTS: int = 1             # hold for H funding events (8h * H)
DEFAULT_FEE_BPS_PER_FILL: float = 4.0
DEFAULT_SLIPPAGE_BPS_PER_FILL: float = 1.0


# ---------------------------------------------------------------------------
# Trade record
# ---------------------------------------------------------------------------
@dataclass
class MRTrade:
    variant: str
    symbol: str
    direction: str               # "long" / "short"
    entry_event_ts: str
    entry_bar_ts: str
    entry_price: float
    exit_event_ts: str
    exit_bar_ts: str
    exit_price: float
    price_pnl_pct: float         # exit/entry - 1 (signed for direction)
    funding_pnl_pct: float       # funding carried during the hold (NOT used as edge; informational only)
    pnl_pct: float               # direction-adjusted price_pnl + funding_pnl - cost
    z_at_entry: float            # signed z at entry (informational)
    funding_at_entry: float
    funding_at_exit: float
    bars_held: int
    exit_reason: str             # "next_event" | "hold_horizon" | "no_exit_event"


# ---------------------------------------------------------------------------
# Pure signal: rolling z-score of funding events, with strict no-look-ahead.
# ---------------------------------------------------------------------------
def _event_zscore(funding_events: pd.Series, lookback_n: int) -> pd.Series:
    """Compute z-score per event over the prior ``lookback_n`` events.

    ``funding_events`` is a UTC-aware pd.Series of fundingRate values
    indexed by event timestamps. Strict no-look-ahead: at event E_i,
    the mean/std are computed over events [E_{i-lookback_n}, E_{i-1}].
    Returns NaN for the first ``lookback_n`` events.
    """
    s = funding_events.sort_index().astype(np.float64)
    shifted = s.shift(1)
    roll = shifted.rolling(lookback_n, min_periods=max(20, lookback_n // 4))
    mu = roll.mean()
    sd = roll.std(ddof=0)
    z = (shifted - mu) / sd.replace(0.0, np.nan)
    return z.reindex(s.index)


# ---------------------------------------------------------------------------
# Event-driven state machine
# ---------------------------------------------------------------------------
def _bar_lookup(bar_idx: pd.DatetimeIndex, ts: pd.Timestamp) -> Optional[pd.Timestamp]:
    """Return the bar whose open_time >= ts (or the last bar <= ts if ts
    is past the last bar)."""
    if ts < bar_idx[0] or ts > bar_idx[-1]:
        return None
    pos = bar_idx.searchsorted(ts, side="left")
    if pos >= len(bar_idx):
        return bar_idx[-1]
    return bar_idx[pos]


def _event_driven_backtest(
    df: pd.DataFrame,
    funding_events: pd.Series,
    cfg: dict,
) -> dict:
    """Run the funding-oscillator-MR strategy in event-driven mode.

    ``funding_events`` is a tz-aware pd.Series of fundingRate values
    indexed by event timestamps (8h cadence).
    ``df`` is the 1m OHLCV+funding bar frame.

    Logic:
      - Compute z_i per event.
      - When |z_i| > z_in: enter at E_i (long if z<0, short if z>0).
      - Exit at E_{i+H} where H = holding_events (8h * H horizon).
      - P&L = direction-adjusted price move - round_trip_cost.
        Funding carry is NOT a target — it is recorded for the
        audit but does not contribute to pnl_pct.
    """
    p = cfg["params"]
    fee = float(p.get("fee_bps_per_fill", DEFAULT_FEE_BPS_PER_FILL)) / 10000.0
    slip = float(p.get("slippage_bps_per_fill", DEFAULT_SLIPPAGE_BPS_PER_FILL)) / 10000.0
    round_trip_cost = 2.0 * (fee + slip)
    sym = cfg["instruments"][0]
    risk_target = float(p.get("risk_target_pct", 0.005))
    starting = float(cfg.get("starting_capital_usd", 100000.0))

    z_in = float(p.get("z_in", DEFAULT_Z_IN))
    lookback_n = int(p.get("lookback_events", DEFAULT_LOOKBACK_EVENTS))
    holding_events = int(p.get("holding_events", DEFAULT_HOLDING_EVENTS))

    events = funding_events.dropna().sort_index()
    event_idx = events.index
    n_events = len(event_idx)

    z_series = _event_zscore(events, lookback_n)

    bar_idx = df.index
    bar_close = df["close"].astype(np.float64)

    trades: List[MRTrade] = []
    equity = [starting]
    fired_long = 0
    fired_short = 0

    for i in range(n_events):
        z_i = z_series.iloc[i]
        if not np.isfinite(z_i) or abs(float(z_i)) <= z_in:
            equity.append(equity[-1])
            continue

        e_in = event_idx[i]
        exit_i = i + holding_events
        if exit_i >= n_events:
            # No exit event within the event window — skip trade to
            # avoid carrying an open position past the data window.
            equity.append(equity[-1])
            continue
        e_out = event_idx[exit_i]

        direction = "short" if float(z_i) > 0 else "long"

        bar_in = _bar_lookup(bar_idx, e_in)
        bar_out = _bar_lookup(bar_idx, e_out)
        if bar_in is None or bar_out is None or bar_out <= bar_in:
            equity.append(equity[-1])
            continue
        px_in = float(bar_close.loc[bar_in])
        px_out = float(bar_close.loc[bar_out])
        if px_in <= 0:
            equity.append(equity[-1])
            continue

        raw_price_pnl = px_out / px_in - 1.0
        # Direction-adjusted: short inverts the sign.
        price_pnl = -raw_price_pnl if direction == "short" else raw_price_pnl

        r_in = float(events.iloc[i])
        r_out = float(events.iloc[exit_i])
        # Funding carry during the hold (informational only — NOT a target).
        # For a LONG held across E_out: carry = -r_out (longs pay r, receive -r).
        # For a SHORT held across E_out: carry = +r_out (shorts receive r).
        funding_pnl = -r_out if direction == "long" else +r_out

        # Trade P&L = price move (direction-adjusted) minus round-trip cost.
        # Funding carry is recorded for audit but not added to the trade P&L
        # (per axis-b "WITHOUT carry/positioning assumption").
        net = price_pnl - round_trip_cost

        if direction == "long":
            fired_long += 1
        else:
            fired_short += 1

        trades.append(MRTrade(
            variant=VARIANT_KEY,
            symbol=sym,
            direction=direction,
            entry_event_ts=str(e_in),
            entry_bar_ts=str(bar_in),
            entry_price=px_in,
            exit_event_ts=str(e_out),
            exit_bar_ts=str(bar_out),
            exit_price=px_out,
            price_pnl_pct=float(price_pnl),
            funding_pnl_pct=float(funding_pnl),
            pnl_pct=float(net),
            z_at_entry=float(z_i),
            funding_at_entry=float(r_in),
            funding_at_exit=float(r_out),
            bars_held=int((bar_out - bar_in).total_seconds() // 60),
            exit_reason="hold_horizon",
        ))
        equity.append(equity[-1] * (1.0 + risk_target * net))

    n_fired = len(trades)
    return {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "symbol": sym,
        "n_bars": len(df),
        "n_events": int(n_events),
        "n_trades_fired": int(n_fired),
        "span_start": str(df.index[0]),
        "span_end": str(df.index[-1]),
        "trades": [asdict(t) for t in trades],
        "equity": np.array(equity, dtype=np.float64),
        "diagnostics": {
            "fired_long": int(fired_long),
            "fired_short": int(fired_short),
            "z_in": float(z_in),
            "lookback_events": int(lookback_n),
            "holding_events": int(holding_events),
            "z_sign_pos_pct": float((z_series.dropna() > 0).mean()) if z_series.dropna().size else 0.0,
            "z_sign_neg_pct": float((z_series.dropna() < 0).mean()) if z_series.dropna().size else 0.0,
            "z_max_abs": float(z_series.abs().max()) if z_series.dropna().size else 0.0,
            "z_at_z_in_threshold_count_long": int(((z_series.dropna()) < -z_in).sum()),
            "z_at_z_in_threshold_count_short": int(((z_series.dropna()) > z_in).sum()),
        },
    }


def run_backtest(df: pd.DataFrame, cfg: dict) -> dict:
    """Run the U5 funding-oscillator-MR strategy on a single-symbol bar frame.

    ``df`` must include OHLCV + ``funding`` (per-bar rate, raw).
    The funding events are reconstructed from the bar index: events are
    the bars where the ``funding`` value differs from the prior bar
    (i.e., a new funding rate became effective).
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "open_time" in df.columns:
            df = df.set_index("open_time")
        elif "ts" in df.columns:
            df = df.set_index("ts")
        else:
            raise ValueError("df must have a DatetimeIndex or open_time/ts column")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.sort_index()
    for col in ("open", "high", "low", "close", "volume", "funding"):
        if col in df.columns:
            df[col] = df[col].astype(np.float64)

    funding = df["funding"].astype(np.float64)
    new_event_mask = funding != funding.shift(1)
    if df.index.tz is None:
        idx_utc = pd.to_datetime(df.index, utc=True)
    else:
        idx_utc = df.index.tz_convert("UTC")
    event_idx_utc = idx_utc[new_event_mask.fillna(True).values]
    snapped = event_idx_utc.floor("8h")
    funding_events = pd.Series(funding[new_event_mask.fillna(True).values].values,
                                index=snapped).sort_index()
    funding_events = funding_events[~funding_events.index.duplicated(keep="first")]

    return _event_driven_backtest(df, funding_events, cfg)


__all__ = [
    "VARIANT_KEY", "MRTrade",
    "_event_zscore", "run_backtest",
]