"""Framework adapters for the OOS validation harness.

Each adapter exposes a single public entry point with a uniform signature::

    run_<name>_replay(
        df: pd.DataFrame,
        native_trades: list[dict],
        *,
        symbol: str,
        starting_cash: float = 100_000.0,
        **kwargs,
    ) -> FrameworkRun

so the generic harness can swap one replay leg for another without code
changes. See :mod:`validation.adapters.native_engine` for the
``FrameworkRun`` dataclass and the normalised trade-dict shape the
adapters consume.

Adapter catalogue
-----------------

================  ======================================  ======================================
name              module                                  fill + sizing model
================  ======================================  ======================================
``native``        :mod:`.native_engine`                   bar-close fill, fixed notional
``backtrader``    :mod:`.backtrader_replay`               next-bar-open fill, fixed fraction of starting cash
``freqtrade``     :mod:`.freqtrade_replay`                next-candle open fill via freqtrade CLI
``vectorbt``      :mod:`.vectorbt_replay`                 signal-based portfolio, fraction of current cash
``ouraq``         :mod:`.ouraq_replay`                    bar-close fill, vol-targeted fraction of current cash
================  ======================================  ======================================
"""
from .native_engine import FrameworkRun, NativeEngineAdapter, UnsupportedVariantError

__all__ = [
    "FrameworkRun",
    "NativeEngineAdapter",
    "UnsupportedVariantError",
]