"""U5 funding_carry long-only strategy (SMA-34930) — event-driven.

A pure long-only funding-carry signal that harvests the carry when
funding is **negative** (Binance USDT-M convention: funding < 0 means
shorts pay longs, so the long perp earns carry). Per the issue:

> positive funding → short perp or stay flat;
> negative funding → long perp → earn carry

Public API
----------
``VARIANT_KEY``
``build_signals(df, params)`` — per-bar signal (+1/0). Per-bar use
    only — backtest uses the event-driven API.
``run_backtest(df, cfg)``     — state machine + equity curve + trades.

Costs follow the cycle-46 convention: 4 bps fee + 1 bp slippage per
fill, applied round-trip. Funding carry is paid at the 8h funding
event, NOT per bar; the event-driven state machine below credits
carry to a trade only when the position is held across an event.

Event-driven semantics
----------------------
The first version of this harness used a per-bar signal that fired
on every minute the funding rate was below threshold. That over-
counted trades by a factor of ~480 (every 1m bar inside an 8h
funding interval that met the gate), inflating the round-trip
cost load and crushing Sharpe even though the carry earned would
have been credited only once. The corrected harness below:

  1. Identifies funding events on the 8h cadence (00:00, 08:00, 16:00 UTC).
  2. For each consecutive event pair (E_i, E_{i+1}):
       - At event E_i, decide to enter long if r_i < carry_gate
         (gate is absolute threshold OR rolling-percentile-based).
       - Entry price = close at the 1m bar whose open_time == E_i.
       - Exit price = close at the 1m bar whose open_time == E_{i+1}.
       - Funding received during the trade = r_{i+1} (paid at E_{i+1}
         since we are still long at that event).
       - Net P&L = price_move + funding_received - round_trip_cost.
  3. Trades are 1 per funding event, so the natural max trade
     count equals (n_events_in_window - 1). For the 90d window
     this is ~269 trades per symbol.

The signal entry condition (per-event carry harvest, long-only):

    r_E < -funding_threshold           (absolute gate, raw rate)
    OR
    r_E < funding_percentile(E)        (rolling q-th percentile of prior N events)

The percentile gate fires when funding is below the rolling q-th
percentile of the prior N funding events — adapting to the recent
regime rather than relying on a fixed absolute level (the U3 fix
referenced in the issue).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

VARIANT_KEY = "funding_carry_u5_eth_sol_1m"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_FUNDING_THRESHOLD: float = 0.0001        # 0.01% = 1 bp per 8h event
DEFAULT_PERCENTILE_LOOKBACK_EVENTS: int = 90      # ~30d @ 8h events
DEFAULT_ATR_PERIOD: int = 14
DEFAULT_FEE_BPS_PER_FILL: float = 4.0
DEFAULT_SLIPPAGE_BPS_PER_FILL: float = 1.0


# ---------------------------------------------------------------------------
# Trade record
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    variant: str
    symbol: str
    direction: str
    entry_event_ts: str         # funding event ts at entry
    entry_bar_ts: str           # 1m bar ts at entry
    entry_price: float
    exit_event_ts: str          # funding event ts at exit
    exit_bar_ts: str            # 1m bar ts at exit
    exit_price: float
    price_pnl_pct: float
    funding_pnl_pct: float
    pnl_pct: float
    funding_at_entry: float     # rate paid at entry event (r_E)
    funding_received: float     # rate received during hold (r_{E+1})
    bars_held: int
    exit_reason: str            # "next_event" — always this for event-driven


# ---------------------------------------------------------------------------
# Pure-function signal core (per-event)
# ---------------------------------------------------------------------------
def compute_signal_at_event(
    rate: float,
    *,
    funding_threshold: Optional[float] = None,
    funding_percentile: Optional[float] = None,
) -> bool:
    """Per-event signal: should we enter long at this funding event?

    Returns True iff (rate < -funding_threshold) OR
                    (rate < funding_percentile).
    """
    if funding_threshold is not None and rate < -float(funding_threshold):
        return True
    if funding_percentile is not None and np.isfinite(funding_percentile) \
            and rate < float(funding_percentile):
        return True
    return False


def _build_event_percentile(funding_events: pd.Series, q: float, lookback_n: int) -> pd.Series:
    """Rolling q-th percentile of funding events over the prior
    ``lookback_n`` events. Strict no-look-ahead: at event E, the
    percentile is taken over events strictly before E.
    """
    events = funding_events.dropna().sort_index()
    roll = events.shift(1).rolling(lookback_n, min_periods=max(20, lookback_n // 4))
    pct_at_event = roll.quantile(q / 100.0)
    return pct_at_event.reindex(events.index)


# ---------------------------------------------------------------------------
# Bar-level signal builder (kept for the per-bar harness path; not the
# default event-driven path)
# ---------------------------------------------------------------------------
def build_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Per-bar signal builder (for back-compat / diagnostics only).

    For the canonical event-driven backtest, use ``run_backtest``,
    which uses ``_run_event_driven`` directly.
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
    if "funding" not in df.columns:
        raise ValueError("df must include a 'funding' column")

    funding_raw = df["funding"].astype(np.float64)
    funding = funding_raw.shift(1)

    funding_threshold = params.get("funding_threshold", DEFAULT_FUNDING_THRESHOLD)
    pct_q = params.get("funding_percentile_q")
    lookback_n = int(params.get("funding_lookback_events",
                                 DEFAULT_PERCENTILE_LOOKBACK_EVENTS))

    funding_pct = None
    if pct_q is not None:
        q = float(pct_q)
        if not (0.0 < q < 100.0):
            raise ValueError(f"funding_percentile_q must be in (0,100), got {q!r}")
        funding_pct = _build_event_percentile(funding_raw.drop_duplicates(), q, lookback_n)
        funding_pct = funding_pct.reindex(df.index, method="ffill")

    if funding_threshold is not None:
        cond_abs = (funding < -float(funding_threshold)).fillna(False)
    else:
        cond_abs = pd.Series(False, index=df.index)
    if funding_pct is not None:
        cond_pct = ((funding < funding_pct) & np.isfinite(funding_pct)).fillna(False)
    else:
        cond_pct = pd.Series(False, index=df.index)
    signal = (cond_abs | cond_pct).astype(np.int64)

    h = df["high"].astype(np.float64)
    l = df["low"].astype(np.float64)
    c = df["close"].astype(np.float64).shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(int(params.get("atr_period", DEFAULT_ATR_PERIOD)),
                     min_periods=int(params.get("atr_period", DEFAULT_ATR_PERIOD))
                     ).mean()

    out = pd.DataFrame({
        "signal": signal,
        "funding_used": funding,
        "atr": atr,
    })
    if funding_pct is not None:
        out["funding_pct"] = funding_pct
    return out


# ---------------------------------------------------------------------------
# Event-driven state machine (the canonical backtest path)
# ---------------------------------------------------------------------------
def _event_driven_backtest(
    df: pd.DataFrame,
    funding_events: pd.Series,
    cfg: dict,
) -> dict:
    """Run the U5 funding-carry strategy in event-driven mode.

    ``funding_events`` is a pd.Series of event-time-indexed
    ``fundingRate`` values (UTC-aware DatetimeIndex, 8h cadence).
    ``df`` is the 1m OHLCV+funding bar frame.

    Logic per consecutive event pair (E_i, E_{i+1}):

      - At E_i, compute the gate: if r_i < carry_threshold OR
        r_i < percentile_i → enter long.
      - entry_bar_ts = the 1m bar whose open_time equals E_i
        (or the first bar after, if E_i falls between bars).
      - entry_price = close[entry_bar_ts].
      - exit_bar_ts = the 1m bar whose open_time equals E_{i+1}.
      - exit_price = close[exit_bar_ts].
      - price_pnl = exit_price / entry_price - 1.
      - funding_pnl = -r_{i+1}   (Binance USDT-M convention: longs PAY
        when r > 0 and RECEIVE when r < 0, so for a long position the
        realised carry at the funding event is -r).
      - net_pnl = price_pnl + funding_pnl - round_trip_cost.

    The trade ledger is anchored to funding events, not 1m bars.
    """
    p = cfg["params"]
    fee = float(p.get("fee_bps_per_fill", DEFAULT_FEE_BPS_PER_FILL)) / 10000.0
    slip = float(p.get("slippage_bps_per_fill", DEFAULT_SLIPPAGE_BPS_PER_FILL)) / 10000.0
    round_trip_cost = 2.0 * (fee + slip)
    sym = cfg["instruments"][0]
    risk_target = float(p.get("risk_target_pct", 0.005))
    starting = float(cfg.get("starting_capital_usd", 100000.0))

    funding_threshold = p.get("funding_threshold", None)
    pct_q = p.get("funding_percentile_q", None)
    lookback_n = int(p.get("funding_lookback_events",
                            DEFAULT_PERCENTILE_LOOKBACK_EVENTS))

    # Build event-time-aligned percentile if requested.
    pct_at_event = None
    if pct_q is not None:
        pct_at_event = _build_event_percentile(funding_events, float(pct_q), lookback_n)

    events_sorted = funding_events.dropna().sort_index()
    event_idx = events_sorted.index
    n_events = len(event_idx)

    # Pre-build a lookup from event ts → 1m bar (by open_time).
    bar_idx = df.index
    bar_close = df["close"].astype(np.float64)

    def _bar_at(ts: pd.Timestamp) -> Optional[pd.Timestamp]:
        """Return the bar whose open_time >= ts (or the last bar <= ts
        if ts is past the last bar)."""
        if ts < bar_idx[0] or ts > bar_idx[-1]:
            return None
        pos = bar_idx.searchsorted(ts, side="left")
        if pos >= len(bar_idx):
            return bar_idx[-1]
        return bar_idx[pos]

    trades: List[Trade] = []
    equity = [starting]
    funding_below_threshold = 0
    funding_below_percentile = 0

    for i in range(n_events - 1):
        e_in = event_idx[i]
        e_out = event_idx[i + 1]
        r_in = float(events_sorted.iloc[i])
        r_out = float(events_sorted.iloc[i + 1])

        if funding_threshold is not None and r_in < -float(funding_threshold):
            funding_below_threshold += 1
            fire = True
        else:
            fire = False

        pct_val = None
        if pct_at_event is not None:
            pct_val = pct_at_event.iloc[i]
            if np.isfinite(pct_val) and r_in < float(pct_val):
                funding_below_percentile += 1
                fire = True

        if not fire:
            equity.append(equity[-1])
            continue

        bar_in = _bar_at(e_in)
        bar_out = _bar_at(e_out)
        if bar_in is None or bar_out is None:
            equity.append(equity[-1])
            continue
        px_in = float(bar_close.loc[bar_in])
        px_out = float(bar_close.loc[bar_out])
        if px_in <= 0:
            equity.append(equity[-1])
            continue
        price_pnl = px_out / px_in - 1.0
        # Long perp: receives -r_out (Binance: longs pay when r > 0,
        # receive when r < 0).
        funding_pnl = -r_out
        net = price_pnl + funding_pnl - round_trip_cost

        trades.append(Trade(
            variant=VARIANT_KEY,
            symbol=sym,
            direction="long",
            entry_event_ts=str(e_in),
            entry_bar_ts=str(bar_in),
            entry_price=px_in,
            exit_event_ts=str(e_out),
            exit_bar_ts=str(bar_out),
            exit_price=px_out,
            price_pnl_pct=float(price_pnl),
            funding_pnl_pct=float(funding_pnl),
            pnl_pct=float(net),
            funding_at_entry=float(r_in),
            funding_received=float(r_out),
            bars_held=int((bar_out - bar_in).total_seconds() // 60),
            exit_reason="next_event",
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
            "funding_below_threshold_events": int(funding_below_threshold),
            "funding_below_percentile_events": int(funding_below_percentile),
            "funding_threshold": float(funding_threshold) if funding_threshold is not None else None,
            "funding_percentile_q": float(pct_q) if pct_q is not None else None,
        },
    }


def run_backtest(df: pd.DataFrame, cfg: dict) -> dict:
    """Run the U5 funding-carry strategy on a single-symbol bar frame
    in event-driven mode.

    ``df`` must include OHLCV + ``funding`` (per-bar rate, raw).
    The funding events are reconstructed from the bar index: events
    are the bars where the ``funding`` value differs from the prior
    bar (i.e., a new funding rate became effective).
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

    # Reconstruct event series: where funding changes, that's a new event.
    funding = df["funding"].astype(np.float64)
    # Detect changes — a "new event" occurs at the first bar where the
    # funding value differs from the prior bar (i.e., the funding rate
    # has been refreshed by ffill from a new 8h event).
    new_event_mask = funding != funding.shift(1)
    if df.index.tz is None:
        idx_utc = pd.to_datetime(df.index, utc=True)
    else:
        idx_utc = df.index.tz_convert("UTC")
    event_idx_utc = idx_utc[new_event_mask.fillna(True).values]
    # Snap to known 8h funding boundaries: 00:00, 08:00, 16:00 UTC.
    snapped = event_idx_utc.floor("8h")
    funding_events = pd.Series(funding[new_event_mask.fillna(True).values].values,
                                index=snapped).sort_index()
    funding_events = funding_events[~funding_events.index.duplicated(keep="first")]

    return _event_driven_backtest(df, funding_events, cfg)


__all__ = [
    "VARIANT_KEY", "Trade",
    "compute_signal_at_event", "build_signals", "run_backtest",
]