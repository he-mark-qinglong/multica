"""Ouraq cross-validation adapter.

Re-executes the native engine's trade decisions under a different fill +
sizing convention than native / backtrader / vectorbt / freqtrade, so the
cross-framework CV harness has a fifth leg that exercises an alternative
risk model rather than only alternative fill orderings.

What "ouraq" adds beyond the other adapters
-------------------------------------------
- **Bar-close fill (no next-bar lag).** The native engine applies the
  ``entry_ts``-bar close-to-close return at ``exit_ts``; backtrader's
  default next-bar-open order execution puts the entry fill on
  ``entry_ts + 1 bar``'s open. Ouraq fills at the ``entry_ts`` bar close
  (no lag), so the entry bar earns its close-to-close return immediately.
  This is the convention used by some academic event-study backtests.
- **Volatility-targeted fixed-fraction sizing.** Native / backtrader
  size each trade at ``base_weight * starting_cash``. Vectorbt sizes at
  ``base_weight * current_cash`` (``size_type="percent"``). Ouraq scales
  ``base_weight * current_cash`` by ``target_vol / realised_vol`` over a
  rolling ``vol_window``-bar window of close-to-close returns, clipped to
  ``[size_floor, size_cap]``. When realised vol is below the target,
  position sizes grow; when it spikes, sizes shrink. With the default
  ``vol_window=20`` and ``target_vol=0.01`` the adapter targets ~1% per
  bar and is materially distinct from the constant-fraction legs.
- **No external dependencies.** The other replay adapters wrap
  backtrader / freqtrade / vectorbt — three heavyweight packages that
  require compilation and may not be installable on every host. Ouraq
  is pure numpy/pandas so it is always available and gives the harness
  at least one always-runnable non-native framework leg.

Contract
--------
Mirrors the backtrader / vectorbt replay contract so the generic harness
can swap it in for any of those::

    run_ouraq_replay(
        df, native_trades, *,
        symbol, starting_cash=100_000.0,
        fee=0.0002, base_weight=0.01,
        vol_window=20, target_vol=0.01,
        size_floor=0.0, size_cap=1.0,
    ) -> FrameworkRun

``df`` must carry a ``close`` column (any OHLCV frame whose index covers
the trade window). ``native_trades`` is the same list-of-dict the other
adapters consume (see ``native_engine._load_module`` for the trade
shape).

When ``df`` is missing the required columns or a trade timestamp falls
outside ``df.index`` the adapter raises :class:`OuraqReplayError`. The
generic harness catches it and records the leg under ``framework_skips``,
matching the pattern used for vectorbt / freqtrade when their engines
are not installed.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

from .native_engine import FrameworkRun


class OuraqReplayError(RuntimeError):
    """Raised when the ouraq replay cannot run on the given inputs."""


def _require_columns(df: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise OuraqReplayError(
            f"ouraq replay requires columns {sorted(columns)}; "
            f"missing {sorted(missing)} on df with columns {sorted(df.columns)}"
        )


def _naive(ts: pd.Timestamp) -> pd.Timestamp:
    """Strip tz to UTC-naive for array indexing consistency."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def _resolve_bars(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.index.tz is not None:
        out.index = out.index.tz_convert("UTC").tz_localize(None)
    return out


def _realised_vol(close: pd.Series, window: int, target: float) -> pd.Series:
    """Rolling close-to-close stdev scaled to ``target`` (clipped at 1e-4
    floor to avoid division-by-zero in the scaling factor)."""
    ret = close.pct_change().fillna(0.0)
    sd = ret.rolling(window=window, min_periods=window).std(ddof=1)
    sd = sd.replace(0.0, np.nan).bfill()
    sd = sd.clip(lower=1e-4)
    return (target / sd).clip(lower=0.0, upper=10.0)


def run_ouraq_replay(
    df: pd.DataFrame,
    native_trades: list[dict],
    *,
    symbol: str,
    starting_cash: float = 100_000.0,
    fee: float = 0.0002,
    base_weight: float = 0.01,
    vol_window: int = 20,
    target_vol: float = 0.01,
    size_floor: float = 0.0,
    size_cap: float = 1.0,
) -> FrameworkRun:
    """Replay native trade decisions on ``df`` under the ouraq fill+sizing model.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV frame (only ``close`` is required) covering the trade window.
    native_trades : list[dict]
        Normalised trade dicts from ``native.trades``. Each must carry
        ``direction`` (``"long"`` | ``"short"``), ``entry_date``,
        ``exit_date``.
    symbol : str
        Echoed into :class:`FrameworkRun.symbol`. No routing.
    starting_cash : float
        Initial cash; mirrors the other adapters.
    fee : float
        Per-fill commission fraction (half round-trip), identical to
        the backtrader adapter's ``commission`` parameter and vectorbt's
        ``fees`` parameter.
    base_weight, vol_window, target_vol : float, int, float
        Sizing model: ``size = base_weight * scaling`` where
        ``scaling = (target_vol / realised_vol)`` over the previous
        ``vol_window`` bars. The result is clipped to
        ``[size_floor, size_cap]``.
    size_floor, size_cap : float
        Bounds on the realised-vol scaling factor before it is applied to
        ``base_weight`` (NOT bounds on the final per-trade notional).

    Returns
    -------
    FrameworkRun
        ``framework="ouraq"``. ``equity`` is indexed by ``df.index``.
        ``trade_pnls`` carries the per-trade pnl fraction as realised
        under the ouraq fill model (entry-bar close, exit-bar close,
        size scaled by rolling vol).

    Raises
    ------
    OuraqReplayError
        If the input frame lacks ``close``, if ``starting_cash`` /
        ``base_weight`` / ``fee`` are non-finite or non-positive, or if
        any trade timestamp falls outside ``df.index``.
    """
    _require_columns(df, ["close"])

    if not math.isfinite(starting_cash) or starting_cash <= 0:
        raise OuraqReplayError(
            f"starting_cash must be a positive finite number, got {starting_cash!r}"
        )
    if not math.isfinite(base_weight) or base_weight <= 0:
        raise OuraqReplayError(
            f"base_weight must be a positive finite number, got {base_weight!r}"
        )
    if not math.isfinite(fee) or fee < 0:
        raise OuraqReplayError(
            f"fee must be a non-negative finite number, got {fee!r}"
        )
    if int(vol_window) < 2:
        raise OuraqReplayError(
            f"vol_window must be >= 2 to compute a sample stddev, got {vol_window!r}"
        )
    if not math.isfinite(target_vol) or target_vol <= 0:
        raise OuraqReplayError(
            f"target_vol must be a positive finite number, got {target_vol!r}"
        )
    if not (0.0 <= size_floor <= size_cap):
        raise OuraqReplayError(
            f"size_floor/size_cap must satisfy 0 <= floor <= cap; "
            f"got floor={size_floor!r}, cap={size_cap!r}"
        )

    bars = _resolve_bars(df)
    if len(bars) < int(vol_window) + 1:
        raise OuraqReplayError(
            f"ouraq replay needs at least vol_window+1 = {int(vol_window) + 1} bars "
            f"to seed the rolling vol; df has {len(bars)}"
        )

    close = bars["close"].astype(float)
    idx = bars.index

    # Build the rolling vol scaler once, then look it up at trade entry.
    scaler = _realised_vol(close, int(vol_window), float(target_vol))
    scaler = scaler.clip(lower=float(size_floor), upper=float(size_cap))

    # Equity walk on a numpy array for clarity (one cash account, one
    # position at a time — matches native's one-position-at-a-time
    # semantics, see _shared/run_backtest.py:46-49).
    cash = float(starting_cash)
    position = 0.0          # signed units of the underlying (positive = long)
    entry_price = math.nan  # fill price of the open position
    entry_size = 0.0        # notional fraction captured at fill (base * scaler)

    equity_records: list[tuple[pd.Timestamp, float]] = []
    trade_pnls: list[float] = []

    # Pre-sort trades by entry so the walk stays deterministic.
    sorted_trades = sorted(
        (dict(t) for t in native_trades),
        key=lambda t: pd.Timestamp(t["entry_date"]),
    )

    for trade in sorted_trades:
        e_ts = _naive(pd.Timestamp(trade["entry_date"]))
        x_ts = _naive(pd.Timestamp(trade["exit_date"]))
        if e_ts not in idx:
            raise OuraqReplayError(
                f"entry_date {e_ts!r} for {symbol} is not on df.index"
            )
        if x_ts not in idx:
            raise OuraqReplayError(
                f"exit_date {x_ts!r} for {symbol} is not on df.index"
            )
        if x_ts <= e_ts:
            raise OuraqReplayError(
                f"exit_date {x_ts!r} must be strictly after entry_date {e_ts!r}"
            )

        direction = trade["direction"]
        if direction not in ("long", "short"):
            raise OuraqReplayError(
                f"trade direction must be 'long'|'short', got {direction!r}"
            )

        # Force-close any leftover position at the new entry bar's close,
        # one-position-at-a-time invariant.
        if position != 0.0 and not math.isnan(entry_price):
            fill = float(close.loc[e_ts])
            gross_ret = (fill / entry_price - 1.0) * (1.0 if position > 0 else -1.0)
            trade_ret = entry_size * gross_ret - entry_size * fee
            cash *= 1.0 + trade_ret
            trade_pnls.append(trade_ret)
            position = 0.0
            entry_price = math.nan
            entry_size = 0.0

        # Open the new position at the entry bar's close (no lag).
        fill = float(close.loc[e_ts])
        scale = float(scaler.loc[e_ts])
        size_fraction = float(base_weight) * scale
        # Cap absolute notional at current cash so the model can never
        # borrow.
        notional = cash * size_fraction
        units = (notional / fill) * (1.0 if direction == "long" else -1.0)
        entry_price = fill
        entry_size = size_fraction
        position = units
        # Pay the entry-fill commission immediately.
        cash -= notional * fee

        # Mark-to-market on every bar from entry (inclusive) to exit
        # (inclusive). Recording mtm on every bar keeps the equity Series
        # dense so the generic harness's Sharpe / max-DD math works
        # without further surgery.
        e_loc = idx.get_loc(e_ts)
        x_loc = idx.get_loc(x_ts)
        for j in range(e_loc, x_loc + 1):
            ts = idx[j]
            px = float(close.iloc[j])
            # Force-close at the exit bar's close (no next-bar lag).
            if j == x_loc:
                gross_ret = (px / entry_price - 1.0) * (1.0 if position > 0 else -1.0)
                trade_ret = entry_size * gross_ret - entry_size * fee
                cash *= 1.0 + trade_ret
                trade_pnls.append(trade_ret)
                position = 0.0
                entry_price = math.nan
                entry_size = 0.0
            mtm = position * (px - entry_price) if not math.isnan(entry_price) else 0.0
            equity_records.append((ts, cash + mtm))

    # If the trade schedule is empty we still need an equity Series that
    # covers every bar (flat starting_cash) so downstream metrics are
    # well-defined.
    if not equity_records:
        for ts in idx:
            equity_records.append((ts, cash))

    eq_index = pd.DatetimeIndex([ts for ts, _ in equity_records])
    equity = pd.Series(
        [v for _, v in equity_records],
        index=eq_index,
        dtype=float,
    )
    # Deduplicate entries that overlap on the same timestamp (only the
    # last write wins, matching a normal walk that updates once per bar).
    equity = equity[~equity.index.duplicated(keep="last")].sort_index()

    return FrameworkRun(
        framework="ouraq",
        symbol=symbol,
        equity=equity,
        trade_pnls=list(trade_pnls),
        trades=[],
    )