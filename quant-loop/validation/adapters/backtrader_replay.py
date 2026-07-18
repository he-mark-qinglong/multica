"""Backtrader cross-validation adapter.

Re-executes the native engine's trade decisions inside the backtrader
event-driven engine. Entry/exit *timestamps and direction* come from the
native trades; *fills are re-priced by backtrader* at the next bar's open
with percent commission — the framework does not trust native fill prices.
A one-bar fill offset vs the native engine (which fills at signal-bar close)
is intentional: it is exactly the engine-assumption difference framework CV
exists to expose.
"""
from __future__ import annotations

import pandas as pd

from .native_engine import FrameworkRun

# pandas >= 2.0 removed Series.iteritems; backtrader 1.9.78 still references it
# in some code paths. Harmless shim when unused.
if not hasattr(pd.Series, "iteritems"):
    pd.Series.iteritems = pd.Series.items  # type: ignore[attr-defined]

import backtrader as bt  # noqa: E402


class _NotionalSizer(bt.Sizer):
    """Fixed-fraction-of-cash notional sizing (fractions allowed)."""

    params = (("weight", 0.01),)

    def _getsizing(self, comminfo, cash, data, isbuy):
        return (cash * self.p.weight) / data.close[0]


class _ReplayStrategy(bt.Strategy):
    params = (
        ("entry_map", {}),   # naive datetime -> "long" | "short"
        ("exit_map", {}),    # naive datetime -> True
        ("records", None),
    )

    def __init__(self):
        self._open_basis: float | None = None

    def next(self):
        dt = self.data.datetime.datetime(0)
        self.p.records["equity"].append((dt, self.broker.getvalue()))
        if self.position:
            if dt in self.p.exit_map:
                self.close()
        elif dt in self.p.entry_map:
            if self.p.entry_map[dt] == "long":
                self.buy()
            else:
                self.sell()

    def notify_order(self, order):
        # remember the notional of the position-opening fill; trade.size is 0
        # by the time notify_trade reports the closed trade
        if order.status == order.Completed and self._open_basis is None:
            self._open_basis = abs(order.executed.size * order.executed.price)

    def notify_trade(self, trade):
        if not trade.isclosed:
            return
        basis, self._open_basis = self._open_basis, None
        if basis:
            self.p.records["trade_pnls"].append(trade.pnlcomm / basis)


def run_backtrader_replay(
    df: pd.DataFrame,
    native_trades: list[dict],
    *,
    symbol: str,
    starting_cash: float = 100_000.0,
    commission: float = 0.0002,
    weight: float = 0.01,
) -> FrameworkRun:
    """Replay native trade decisions on `df` (already window-sliced)."""
    df_bt = df.copy()
    # backtrader wants tz-naive datetimes
    if df_bt.index.tz is not None:
        df_bt.index = df_bt.index.tz_convert("UTC").tz_localize(None)

    entry_map = {}
    exit_map = {}
    for t in native_trades:
        e = pd.Timestamp(t["entry_date"])
        x = pd.Timestamp(t["exit_date"])
        if e.tzinfo is not None:
            e = e.tz_convert("UTC").tz_localize(None)
        if x.tzinfo is not None:
            x = x.tz_convert("UTC").tz_localize(None)
        entry_map[e.to_pydatetime()] = t["direction"]
        exit_map[x.to_pydatetime()] = True

    records: dict = {"equity": [], "trade_pnls": []}
    cerebro = bt.Cerebro(stdstats=False)
    data = bt.feeds.PandasData(dataname=df_bt)
    cerebro.adddata(data)
    cerebro.addstrategy(_ReplayStrategy, entry_map=entry_map, exit_map=exit_map, records=records)
    cerebro.broker.setcash(starting_cash)
    cerebro.broker.setcommission(commission=commission)
    cerebro.addsizer(_NotionalSizer, weight=weight)
    cerebro.run()

    equity = pd.Series(
        [v for _, v in records["equity"]],
        index=pd.DatetimeIndex([d for d, _ in records["equity"]]),
    )
    return FrameworkRun(
        framework="backtrader",
        symbol=symbol,
        equity=equity,
        trade_pnls=list(records["trade_pnls"]),
        trades=[],
    )
