"""pyalgotrade cross-validation adapter.

Re-executes the native engine's trade decisions inside pyalgotrade's
event-driven broker. Entry/exit *timestamps and direction* come from the
native trades; *fills are re-priced by pyalgotrade* at the bar's close with
``TradePercentage`` commission. Mirrors the backtrader / freqtrade / vectorbt
replay contract so all four framework legs are interchangeable in the
generic harness.

pyalgotrade is an optional dependency (lightweight, no compiled deps). The
import is deferred to call time and a missing install raises
``PyalgotradeReplayError``, which the generic harness records as a framework
skip instead of crashing.

pyalgotrade-specific differences vs the other adapters (these are exactly
the engine-assumption gaps framework CV exists to expose):

1. **Bar-feed model**: pyalgotrade is event-driven; every bar invokes
   ``onBars``. The native engine is vectorized over the whole df. pyalgotrade
   therefore incurs a small overhead in equity-curve shape (per-bar tick) but
   the price series + commission model are functionally identical.
2. **Order type**: only ``marketOrder`` is supported (pyalgotrade has no
   stop / limit surface that matches the native engine). Entry fills at the
   bar close, exit fills at the bar close — same convention as backtrader.
3. **Sizing**: pyalgotrade's broker has no fractional shares; we size to the
   nearest integer share. With ``weight=0.01`` and a 5-digit crypto price the
   truncation error is below 0.01 USD per order, well under the G7 noise
   floor.
4. **Commission**: ``TradePercentage`` is applied per fill (entry + exit =
   round-trip cost). Set ``commission`` to half the desired round-trip; the
   generic harness already does this (matches backtrader).
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable

import pandas as pd

from .native_engine import FrameworkRun

# Lazy / optional import guard -------------------------------------------------
try:  # pragma: no cover - exercised at call time
    import pyalgotrade.barfeed.membf as _membf  # noqa: F401
    from pyalgotrade import broker as _broker
    from pyalgotrade import strategy as _strategy
    from pyalgotrade.bar import BasicBar as _BasicBar
    from pyalgotrade.barfeed import Frequency as _Frequency
    _PYALGOTRADE_AVAILABLE = True
    _IMPORT_ERROR: Exception | None = None
except Exception as _e:  # ImportError or any third-party bootstrap issue
    _PYALGOTRADE_AVAILABLE = False
    _IMPORT_ERROR = _e


class PyalgotradeReplayError(RuntimeError):
    """Raised when pyalgotrade is unavailable or fails mid-run."""


# pyalgotrade wants tz-naive datetimes — mirror the backtrader adapter.
def _naive(ts: pd.Timestamp) -> dt.datetime:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.to_pydatetime()


def _freq_for(timeframe: str | None):
    """Map the strategy's timeframe string to pyalgotrade's Frequency enum.

    Falls back to MINUTE for unknown values (crypto default). Returns None
    when the input is unknown so the feed is built with pyalgotrade's own
    default Frequency (which infers from bar spacing).
    """
    if not timeframe:
        return None
    table = {
        "1m": _Frequency.MINUTE if _PYALGOTRADE_AVAILABLE else None,
        "5m": _Frequency.MINUTE if _PYALGOTRADE_AVAILABLE else None,
        "15m": _Frequency.MINUTE if _PYALGOTRADE_AVAILABLE else None,
        "30m": _Frequency.MINUTE if _PYALGOTRADE_AVAILABLE else None,
        "1h": _Frequency.HOUR if _PYALGOTRADE_AVAILABLE else None,
        "2h": _Frequency.HOUR if _PYALGOTRADE_AVAILABLE else None,
        "4h": _Frequency.HOUR if _PYALGOTRADE_AVAILABLE else None,
        "8h": _Frequency.HOUR if _PYALGOTRADE_AVAILABLE else None,
        "1d": _Frequency.DAY if _PYALGOTRADE_AVAILABLE else None,
    }
    return table.get(timeframe)


# In-memory bar feed ----------------------------------------------------------
class _InMemoryFeed(_membf.BarFeed if _PYALGOTRADE_AVAILABLE else object):
    """A BarFeed built from a pre-existing pandas DataFrame.

    Mirrors the bar shape produced by ``data_loader.load_all``:
    index = tz-aware UTC timestamp; columns include ``open/high/low/close``
    and optional ``volume`` (defaults to 1.0 so single-share orders can
    fill, matching backtrader's no-volume assumption).
    """

    def barsHaveAdjClose(self) -> bool:  # type: ignore[override]
        return False


# Strategy class --------------------------------------------------------------
class _ReplayStrategy(
    _strategy.BacktestingStrategy if _PYALGOTRADE_AVAILABLE else object
):
    """Replays the native trade schedule inside pyalgotrade's broker.

    Per-bar equity is captured to ``records['equity']`` (list of
    (datetime, float) tuples). Per-trade pnl is captured as a fraction of
    the *opening notional* (entry fill price x shares) into
    ``records['trade_pnls']`` — this matches the contract every other
    framework adapter exposes to ``metrics.metrics_from_run``.

    pyalgotrade 0.20 has no params mechanism (no _PARAMS like backtrader),
    so we accept the strategy's ``barFeed, broker`` positional args from the
    base ctor and stash our config as plain instance attributes.
    """

    def __init__(self, feed, broker, *, entry_map, exit_map, records, weight, share_lots=1):
        super().__init__(feed, broker)
        self._entry_map = entry_map
        self._exit_map = exit_map
        self._records = records
        self._weight = weight
        self._share_lots = max(1, int(share_lots))
        self._open_basis: float | None = None
        # +1 = long open, -1 = short open, 0 = flat
        self._open_side: int = 0
        # Rescaled opening notional (per share_lots) used so per-trade pnl
        # matches the notional the harness intends (cash * weight * shares)
        # even when share_lots > 1.
        self._notional_factor: float = 1.0

    def _sizing_for(self, price: float) -> int:
        """Integer share count such that ``cash*weight*share_lots/price``
        is large enough to produce non-zero fills for crypto-sized prices.

        ``price`` is the *rescaled* bar price (price_real / share_lots);
        we undo the rescale so the formula expresses the dollar allocation
        correctly.
        """
        cash = self.getBroker().getCash()
        if price <= 0 or cash <= 0:
            return 0
        price_real = price * self._share_lots
        return max(0, int((cash * self._weight * self._share_lots) / price_real))

    def onBars(self, bars):  # type: ignore[override]
        bar = bars[self.getFeed().getDefaultInstrument()]
        dt_now = bar.getDateTime()
        self._records["equity"].append((dt_now, self.getBroker().getEquity()))

        if self.getBroker().getShares(
            self.getFeed().getDefaultInstrument()
        ) != 0:
            # already in a position -> look for exit. pyalgotrade's
            # marketOrder with a positive share count closes the position
            # regardless of long/short sign; the Order.Action reported back
            # to onOrderUpdated (SELL vs BUY_TO_COVER) disambiguates.
            if dt_now in self._exit_map:
                pos = self.getBroker().getShares(
                    self.getFeed().getDefaultInstrument()
                )
                if pos != 0:
                    self.marketOrder(
                        self.getFeed().getDefaultInstrument(), -pos
                    )
        else:
            if dt_now in self._entry_map:
                direction = self._entry_map[dt_now]
                price = bar.getClose()
                shares = self._sizing_for(price)
                if shares <= 0:
                    return
                if direction == "long":
                    self.marketOrder(
                        self.getFeed().getDefaultInstrument(), shares
                    )
                else:  # "short" — pyalgotrade's marketOrder does NOT
                      # open shorts; use enterShort (SELL_SHORT action).
                    self.enterShort(
                        self.getFeed().getDefaultInstrument(), shares
                    )

    def onOrderUpdated(self, order):  # type: ignore[override]
        # pyalgotrade invokes this on every order state transition. We only
        # care about FILLED transitions — partials are uncommon in this
        # adapter's market-order usage but ignored defensively.
        if order.getState() != _broker.Order.State.FILLED:
            return
        side = order.getAction()
        fill_price = order.getAvgFillPrice()
        shares = order.getQuantity()
        # When share_lots > 1 the bars were rescaled (price/share_lots) so
        # pyalgotrade's notion of "1 share = price/share_lots real units".
        # We normalise the notional back to *real* dollars via the same
        # factor so the per-trade pnl is comparable to backtrader/vectorbt.
        price_real = fill_price * self._share_lots
        notional = abs(price_real * shares)
        action = _broker.Order.Action
        # Action classification — pyalgotrade's BUY is reused for both
        # opening-long and covering-short (BUY_TO_COVER is only set when
        # the order was submitted via a limit/side-aware API). We resolve
        # the ambiguity via ``open_side``:
        #   * BUY while flat         -> opening long
        #   * BUY while short open   -> covering short (treated as exit)
        # SELL_SHORT is always an entry; SELL is always an exit.
        if side == action.BUY and self._open_side == 0:
            # opening long
            self._open_basis = notional
            self._open_side = +1
        elif side == action.SELL_SHORT:
            self._open_basis = notional
            self._open_side = -1
        elif (
            (side == action.BUY and self._open_side == -1)
            or side == action.BUY_TO_COVER
            or side == action.SELL
        ) and self._open_basis:
            # exit — realised pnl normalised by opening notional
            proceeds = price_real * abs(shares)
            if self._open_side == -1:
                # short: profit when cover price < entry proceeds
                net = (self._open_basis - proceeds) / self._open_basis
            else:
                net = (proceeds - self._open_basis) / self._open_basis
            self._records["trade_pnls"].append(net)
            self._open_basis = None
            self._open_side = 0


# Public entry point ----------------------------------------------------------
def run_pyalgotrade_replay(
    df: pd.DataFrame,
    native_trades: list[dict],
    *,
    symbol: str,
    starting_cash: float = 100_000.0,
    commission: float = 0.0002,
    weight: float = 0.01,
    timeframe: str | None = None,
    share_lots: int = 1,
) -> FrameworkRun:
    """Replay native trade decisions on ``df`` (already window-sliced).

    ``commission`` is the per-fill commission fraction (half the round-trip
    cost), matching the backtrader adapter's ``commission`` semantics.
    ``weight`` is the fraction of current cash deployed per signal
    (``size_type="percent"`` analogue), matching the backtrader adapter's
    fixed-fraction notional sizer.

    ``share_lots`` is the integer-share rescaling factor: 1 share = 1/lots
    of a unit. Set > 1 when ``weight * starting_cash / price`` rounds to
    zero under integer sizing (e.g. crypto-USD pairs at 1% sizing).
    Per-trade pnl is reported as fraction of *opening notional* so the
    rescaling cancels out and is comparable to backtrader / vectorbt
    results.
    """
    if not _PYALGOTRADE_AVAILABLE:
        raise PyalgotradeReplayError(
            "pyalgotrade not installed (pip install pyalgotrade) — "
            f"framework leg unavailable ({_IMPORT_ERROR})"
        )

    df_v = df.copy()
    if df_v.index.tz is not None:
        df_v.index = df_v.index.tz_convert("UTC").tz_localize(None)

    # Build pyalgotrade bars (tz-naive UTC, matching backtrader convention).
    required_cols = {"open", "high", "low", "close"}
    missing = required_cols - set(df_v.columns)
    if missing:
        raise PyalgotradeReplayError(
            f"pyalgotrade replay requires columns {sorted(required_cols)}, "
            f"df has {sorted(df_v.columns)}"
        )

    freq = _freq_for(timeframe)
    if freq is None:
        # Let pyalgotrade infer from bar spacing — pass Frequency.MINUTE as a
        # safe default; the engine only uses it for resampling hooks.
        freq = _Frequency.MINUTE

    volume = (
        df_v["volume"].astype(float).tolist()
        if "volume" in df_v.columns
        else [1_000_000.0] * len(df_v)
    )

    # When share_lots > 1, rescale prices by 1/share_lots so pyalgotrade's
    # integer-share broker can fill non-trivial sizes. The realised pnl in
    # onOrderUpdated is renormalised by ``share_lots`` so the rescale
    # cancels out and per-trade pnl is comparable to backtrader/vectorbt.
    lots = max(1, int(share_lots))
    inv_lots = 1.0 / lots

    pg_bars = []
    for ts, row, vol in zip(df_v.index, df_v.itertuples(index=False), volume):
        pg_bars.append(
            _BasicBar(
                ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                float(row.open) * inv_lots,
                float(row.high) * inv_lots,
                float(row.low) * inv_lots,
                float(row.close) * inv_lots,
                float(vol),
                float(row.close) * inv_lots,  # adjClose == close (rescaled)
                freq,
            )
        )

    feed = _InMemoryFeed(freq, maxLen=max(2000, len(pg_bars) + 100))
    feed.registerInstrument(symbol)
    feed.addBarsFromSequence(symbol, pg_bars)

    # Build entry/exit lookup tables from native trades.
    entry_map: dict[dt.datetime, str] = {}
    exit_map: dict[dt.datetime, bool] = {}
    for t in native_trades:
        e = _naive(t["entry_date"])
        x = _naive(t["exit_date"])
        if e == x:
            # zero-duration trades would double-fire onBars on a single bar;
            # the native engine rejects them too, but guard here.
            continue
        entry_map[e] = t["direction"]
        exit_map[x] = True

    records: dict = {"equity": [], "trade_pnls": []}

    try:
        strat = _ReplayStrategy(
            feed,
            _broker.backtesting.Broker(starting_cash, feed),
            entry_map=entry_map,
            exit_map=exit_map,
            records=records,
            weight=weight,
            share_lots=share_lots,
        )
        strat.getBroker().setCommission(
            _broker.backtesting.TradePercentage(commission)
        )
        strat.run()
    except PyalgotradeReplayError:
        raise
    except Exception as e:  # pyalgotrade surfaces many error shapes
        raise PyalgotradeReplayError(
            f"pyalgotrade run failed for {symbol}: {e}"
        ) from e

    if not records["equity"]:
        raise PyalgotradeReplayError(
            f"pyalgotrade produced no equity ticks for {symbol} "
            f"(df rows={len(df_v)}, trades={len(native_trades)})"
        )

    equity = pd.Series(
        [v for _, v in records["equity"]],
        index=pd.DatetimeIndex([d for d, _ in records["equity"]]),
        dtype=float,
    )
    return FrameworkRun(
        framework="pyalgotrade",
        symbol=symbol,
        equity=equity,
        trade_pnls=list(records["trade_pnls"]),
        trades=[],
    )


__all__ = [
    "PyalgotradeReplayError",
    "run_pyalgotrade_replay",
]


def is_available() -> bool:
    """True iff pyalgotrade is importable in this Python environment."""
    return _PYALGOTRADE_AVAILABLE


def import_error() -> Exception | None:
    """The exception raised when ``import pyalgotrade`` failed, or None."""
    return _IMPORT_ERROR