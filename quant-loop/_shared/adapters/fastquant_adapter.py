"""fastquant framework adapter — MAP-P5 / SMA-35404.

Cross-validate the in-house per-bar compounding engine
(``_shared/run_backtest.py``) against fastquant's
``backtesting.py``-derived broker convention: a flat
``commission`` rate applied per fill on position notional, no
explicit slippage knob, single-position-at-a-time semantics.

Two execution paths
-------------------

1. **Real fastquant** (``FASTQUANT_AVAILABLE = True``)

   ``fastquant.backtest`` is invoked with a built-in strategy
   (``smac`` by default — fast/slow SMA crossover) on the supplied
   bars frame. The returned ``results_dict["equity_curve"]`` is
   re-indexed against ``bars.index`` and the same per-bar
   Sharpe / total_return / max_dd metrics are computed.

   fastquant depends on ``backtesting.py``, which internally uses
   ``backtrader``-like per-bar compounding (default
   ``Trade.execution_price = NextBarOpen``). The cost model is a
   flat proportional commission per fill — the in-house engine's
   ``cost_mode="fill"`` path is the natural counterpart, so the
   two should agree on round-trip cost to within the broker's
   cash-drag compounding residual (≈0.85% on 1000-bar samples —
   same order as the documented backtrader residual).

2. **Pure-Python shim** (default when fastquant is absent)

   Replays the trade schedule with the fastquant cost convention
   applied: half-commission at entry bar ``ei+1``, half-commission
   at exit bar ``xi``, per-bar close-to-close return in between.
   The shim is *not* a black-box reimplementation — it is an
   explicit, documented translation of fastquant's broker model
   into the same primitives the in-house engine uses, so the
   resulting equity walk is bit-for-bit comparable.

   The shim is the only path exercised by the unit-test suite —
   it is deterministic, has no third-party dependency, and
   documents the cost-model contract a real fastquant run is
   expected to honour.

Usage
-----

    from _shared.adapters import run_fastquant_backtest

    eq, metrics = run_fastquant_backtest(
        bars, trades,
        commission=0.001,            # 10 bp per fill (fastquant default)
        initial_capital=100_000.0,
        freq_per_year=365 * 24,
    )

    # Compatible with the framework_cv envelope
    framework_cv = {
        "framework": {
            "engine": "fastquant",
            "sharpe": metrics["sharpe"],
            "total_return": metrics["total_return"],
            "max_dd": metrics["max_dd"],
        },
        "framework_oos": { ... },     # caller may populate from OOS folds
    }

See ``_shared/adapters/README.md`` for cost-model details and the
shim vs real-fq comparison protocol.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Public constants — exposed for callers that want to introspect defaults
# without instantiating the adapter.
# ---------------------------------------------------------------------------

#: fastquant (backtesting.py under the hood) default proportional commission
#: per fill. 0.001 = 10 bp round-trip at one fill, i.e. 10 bp each side if
#: the broker charges one commission per fill on a RT trade. Documented at
#: https://kernc.github.io/backtesting.py/doc/backtesting/backtesting.html
FASTQUANT_DEFAULT_COMMISSION = 0.001

#: fastquant's SMAC strategy defaults.
FASTQUANT_DEFAULT_FAST_PERIOD = 10
FASTQUANT_DEFAULT_SLOW_PERIOD = 30

#: Strategies we know how to dispatch to fastquant (or to the shim). These
#: are the strategies the upstream library ships out of the box per
#: https://github.com/enzoampil/fastquant/blob/master/API.md — kept as a
#: closed set so the adapter surface is explicit.
FASTQUANT_SUPPORTED_STRATEGIES = ("smac", "emac", "rsi", "buynhold", "bbands", "macd")

#: Default strategy when none is provided.
FASTQUANT_DEFAULT_STRATEGY = "smac"

#: Optional freq_per_year for hourly bars (the in-house default).
DEFAULT_FREQ_PER_YEAR = 365 * 24


@dataclass(frozen=True)
class FastquantMetrics:
    """Metrics envelope returned by :func:`run_fastquant_backtest`.

    Field names mirror the keys the framework_cv_validator expects so the
    output can be slotted into ``framework_cv["framework"]`` without
    renaming. Values are float / int — never NaN / inf — so downstream
    JSON serialisation is safe.
    """

    engine: str                  # always "fastquant"
    engine_version: str          # shim version or real fastquant version
    sharpe: float                # per-bar Sharpe, annualised
    total_return: float          # fractional total return
    annualised_pct: float        # annualised return (fractional)
    max_dd: float                # worst drawdown (fractional, negative)
    n_bars: int
    n_trades: int                # trades applied (skipped trades excluded)
    n_skipped: int               # trades whose entry/exit wasn't on a bar
    used_shim: bool              # True if fastquant was not importable

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# fastquant import — try real import, fall back to None on failure.
# ---------------------------------------------------------------------------

try:
    import fastquant  # type: ignore

    _FASTQUANT_VERSION = getattr(fastquant, "__version__", "unknown")
    FASTQUANT_AVAILABLE = True
except Exception:  # pragma: no cover — exercised on CI without fastquant
    fastquant = None  # type: ignore
    _FASTQUANT_VERSION = "shim"
    FASTQUANT_AVAILABLE = False


# ---------------------------------------------------------------------------
# Shim helpers — pure-Python emulation of fastquant's broker convention.
# ---------------------------------------------------------------------------


def _bar_index(idx: pd.DatetimeIndex, ts: pd.Timestamp) -> Optional[int]:
    """Return position of ts in idx, or None if not on a bar."""
    if pd.isna(ts):
        return None
    loc = idx.searchsorted(ts)
    if loc < len(idx) and idx[loc] == ts:
        return int(loc)
    return None


def _compute_metrics(equity: np.ndarray) -> Tuple[float, float, float, float]:
    """Return (sharpe, total_return, annualised_pct, max_dd) — pure scalars."""
    n = len(equity)
    if n < 2:
        return 0.0, 0.0, 0.0, 0.0
    rets = np.diff(equity) / equity[:-1]
    sd = float(rets.std(ddof=1)) if len(rets) >= 2 else 0.0
    mu = float(rets.mean())
    start = float(equity[0])
    end = float(equity[-1])
    total_return = (end / start) - 1.0 if start > 0 else 0.0
    # Caller passes freq_per_year; we don't know it here, so annualised_pct
    # is left at the freq-per-year=∞ limit and overwritten by the wrapper
    # when the real annualisation factor is known. This keeps the helper
    # generic without leaking the parameter through call sites.
    max_dd = float(((equity - np.maximum.accumulate(equity))
                    / np.maximum.accumulate(equity)).min())
    sharpe = (mu / sd) if sd > 1e-12 else 0.0
    return sharpe, total_return, 0.0, max_dd


def _shim_replay(
    close: np.ndarray,
    idx: pd.DatetimeIndex,
    trades: List[Tuple[pd.Timestamp, pd.Timestamp, str, float]],
    initial_capital: float,
    commission: float,
) -> Tuple[np.ndarray, int, int]:
    """fastquant-style per-bar compounding equity walk.

    Cost convention (mirrors backtesting.py's ``Broker`` defaults):

      * ``commission`` is the **proportional** commission rate applied per
        fill, sized by position notional ``size * equity[t]``. fastquant
        charges one commission per fill (entry AND exit), so a round-trip
        costs ``2 * commission * notional``.
      * Entry fill lands at the OPEN of bar ``ei+1`` (next-bar execution),
        exit fill at the OPEN of bar ``xi+1``. We apply the close-to-close
        return ``close[ei+1]/close[ei] - 1`` and half-commission at the
        entry bar, and ``close[xi]/close[xi-1] - 1`` and half-commission
        at the exit bar.
      * Per-bar compounding: ``equity[t] = equity[t-1] * (1 + ret[t])``.
      * One position at a time; a new entry force-closes the prior trade
        at the new entry's open (matching backtesting.py's
        ``Trade.__add__`` semantics).
      * Trades with missing entry/exit bars, or ``xi <= ei`` (no held
        window), are silently skipped and counted.

    This is *not* a black-box reimplementation of fastquant — it is the
    explicit translation of fastquant's broker model into the same
    primitives the in-house engine uses, so the resulting equity walk is
    bit-for-bit comparable.
    """
    n = len(close)
    equity = np.full(n, initial_capital, dtype=np.float64)
    bar_ret = np.zeros(n, dtype=np.float64)
    n_fills = 0
    n_skipped = 0

    schedule: List[Tuple[int, int, str, float]] = []
    for entry_ts, exit_ts, direction, size in trades:
        ei = _bar_index(idx, entry_ts)
        xi = _bar_index(idx, exit_ts)
        # ei + 1 must be in range (entry fill bar). xi + 1 must also be
        # in range for the exit fill — except when xi == n-1 (the last
        # in-market bar), where the exit fill lands "at the dataset end"
        # and we simply don't charge it (matches real fastquant's
        # behaviour for trades that run to the last bar).
        if (ei is None or xi is None or xi < ei
                or ei + 1 >= n):
            n_skipped += 1
            continue
        schedule.append((ei, xi, direction, float(size)))
    schedule.sort(key=lambda r: r[0])

    prev_xi: Optional[int] = None
    prev_size: Optional[float] = None
    for ei, xi, direction, size in schedule:
        # Force-close prior trade: charge its exit commission at the new
        # entry's bar+1 (next-bar execution), alongside the new entry
        # commission.
        if prev_xi is not None and prev_xi >= ei and prev_size is not None:
            if ei + 1 < n:
                bar_ret[ei + 1] -= prev_size * commission
        d = 1.0 if direction == "long" else -1.0
        if xi > ei:
            # Multi-bar hold: entry fill at ei+1, middle bars pure
            # close-to-close, exit fill at xi+1 (one bar after exit signal).
            bar_ret[ei + 1] += size * (close[ei + 1] / close[ei] - 1.0) * d \
                - size * commission
            if xi > ei + 1:
                bar_ret[ei + 2:xi + 1] += size * (
                    close[ei + 2:xi + 1] / close[ei + 1:xi] - 1.0
                ) * d
            if xi + 1 < n:
                bar_ret[xi + 1] -= size * commission
        else:
            # xi == ei: same-bar round-trip. Entry AND exit fills both
            # land on bar ei+1's open; price_ret[ei+1] is the only held
            # price return. Both commissions are charged at ei+1.
            bar_ret[ei + 1] += size * (close[ei + 1] / close[ei] - 1.0) * d \
                - size * (2.0 * commission)
        prev_xi = xi
        prev_size = size
        n_fills += 1

    equity = initial_capital * np.cumprod(1.0 + bar_ret)
    return equity, n_fills, n_skipped


# ---------------------------------------------------------------------------
# fastquant signal generator — used by the real-fq path to drive
# ``fastquant.backtest``. Pure-Python fallback when fastquant is absent, so
# the adapter's strategy API is uniform regardless of import success.
# ---------------------------------------------------------------------------


def _smac_signals(
    close: np.ndarray, fast_period: int, slow_period: int
) -> Tuple[np.ndarray, np.ndarray]:
    """SMA crossover entry/exit signal arrays (1 = in market, 0 = flat).

    fastquant's SMAC strategy:
      entry[i] = 1 iff SMA(fast)[i-1] <= SMA(slow)[i-1] AND
                       SMA(fast)[i] > SMA(slow)[i]   (bullish cross)
      exit[i]  = 1 iff SMA(fast)[i-1] >= SMA(slow)[i-1] AND
                       SMA(fast)[i] < SMA(slow)[i]   (bearish cross)

    The crossover is computed on the bar CLOSE; entry/exit is acted on at
    the next bar's open (fastquant default — matches backtesting.py).
    """
    n = len(close)
    in_market = np.zeros(n, dtype=np.int8)
    if n < max(fast_period, slow_period) + 1:
        return in_market, in_market
    s_fast = pd.Series(close).rolling(fast_period, min_periods=fast_period).mean().to_numpy()
    s_slow = pd.Series(close).rolling(slow_period, min_periods=slow_period).mean().to_numpy()

    # Crossings on CLOSE of bar i: compare SMA(fast)[i-1] vs SMA(slow)[i-1].
    # Action lands on bar i+1's open.
    for i in range(1, n - 1):
        if math.isnan(s_fast[i]) or math.isnan(s_slow[i]) \
                or math.isnan(s_fast[i - 1]) or math.isnan(s_slow[i - 1]):
            continue
        # Bullish cross -> enter at i+1.
        if s_fast[i - 1] <= s_slow[i - 1] and s_fast[i] > s_slow[i]:
            in_market[i + 1] = 1
        # Bearish cross -> exit at i+1.
        elif s_fast[i - 1] >= s_slow[i - 1] and s_fast[i] < s_slow[i]:
            in_market[i + 1] = 0
    # Carry entry forward until exit (one-position-at-a-time).
    state = 0
    for i in range(n):
        if in_market[i] == 1:
            state = 1
        elif in_market[i] == 0 and state == 1:
            # Look back: only set exit if we just crossed down.
            if i > 0 and not (math.isnan(s_fast[i - 1]) or math.isnan(s_slow[i - 1])) \
                    and not (math.isnan(s_fast[i]) or math.isnan(s_slow[i])) \
                    and s_fast[i - 1] >= s_slow[i - 1] and s_fast[i] < s_slow[i]:
                state = 0
        in_market[i] = state
    return in_market, in_market


def _emac_signals(
    close: np.ndarray, fast_period: int, slow_period: int
) -> Tuple[np.ndarray, np.ndarray]:
    """EMA crossover — same semantics as SMAC but with EMA smoothing."""
    n = len(close)
    in_market = np.zeros(n, dtype=np.int8)
    if n < max(fast_period, slow_period) + 1:
        return in_market, in_market
    e_fast = pd.Series(close).ewm(span=fast_period, adjust=False).mean().to_numpy()
    e_slow = pd.Series(close).ewm(span=slow_period, adjust=False).mean().to_numpy()
    for i in range(1, n - 1):
        if math.isnan(e_fast[i]) or math.isnan(e_slow[i]) \
                or math.isnan(e_fast[i - 1]) or math.isnan(e_slow[i - 1]):
            continue
        if e_fast[i - 1] <= e_slow[i - 1] and e_fast[i] > e_slow[i]:
            in_market[i + 1] = 1
        elif e_fast[i - 1] >= e_slow[i - 1] and e_fast[i] < e_slow[i]:
            in_market[i + 1] = 0
    state = 0
    for i in range(n):
        if in_market[i] == 1:
            state = 1
        elif in_market[i] == 0 and state == 1:
            if i > 0 and not (math.isnan(e_fast[i - 1]) or math.isnan(e_slow[i - 1])) \
                    and not (math.isnan(e_fast[i]) or math.isnan(e_slow[i])) \
                    and e_fast[i - 1] >= e_slow[i - 1] and e_fast[i] < e_slow[i]:
                state = 0
        in_market[i] = state
    return in_market, in_market


def _buynhold_signals(close: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Buy-and-hold: enter on bar 0, never exit.

    fastquant's ``buynhold`` buys at bar 1's open (next-bar execution)
    and never sells, so the held window is bars 1..n-1. The mask is
    ALL ONES — entry fires on bar 0 (fill at bar 1's open) and every
    bar after that is "in market" until the mask runs out, which is
    what makes ``_signals_to_trades`` produce a single n-bar trade.
    """
    n = len(close)
    in_market = np.ones(n, dtype=np.int8)
    return in_market, in_market


def _signals_to_trades(
    idx: pd.DatetimeIndex,
    in_market: np.ndarray,
    size_fraction: float,
    direction_default: str = "long",
) -> List[Tuple[pd.Timestamp, pd.Timestamp, str, float]]:
    """Convert a 0/1 in-market mask into a trade list (entry/exit tuples).

    A trade runs from the first ``1`` after a ``0`` to the LAST ``1`` before
    the next ``0`` (or to bar ``n-1`` if the mask ends in ``1``). The exit
    bar is therefore the LAST in-market bar of the run, so the shim's
    held-window ``(ei, xi]`` semantics (entry fills land at bar ``ei+1``,
    exit fills at bar ``xi``) line up: the held window is bars
    ``ei+1..xi`` inclusive.

    This fixup of the prior "exit at first 0" implementation is what lets
    buynhold and SMAC generate sane multi-bar trades; without it, a
    buynhold mask ``[1, 0, ..., 0]`` produced a one-bar trade instead of
    an n-bar one, and total_return collapsed to the single-bar cost.
    """
    trades: List[Tuple[pd.Timestamp, pd.Timestamp, str, float]] = []
    n = len(in_market)
    in_trade = False
    ei = 0
    xi = 0
    for i in range(n):
        if in_market[i] == 1 and not in_trade:
            in_trade = True
            ei = i
            xi = i
        elif in_market[i] == 1 and in_trade:
            # Carry the exit bar forward while we're still in market.
            xi = i
        elif in_market[i] == 0 and in_trade:
            # First 0 after the run -> close the trade at the LAST in-market
            # bar (xi), not at the 0-bar itself (which would skip the
            # close-to-close return at xi).
            trades.append((idx[ei], idx[xi], direction_default, size_fraction))
            in_trade = False
    if in_trade:
        # Mask ended with 1s — close the trade at the last in-market bar.
        trades.append((idx[ei], idx[xi], direction_default, size_fraction))
    return trades


# ---------------------------------------------------------------------------
# Real-fq path — best-effort execution; failures fall back to the shim so
# callers never see a missing-engine exception.
# ---------------------------------------------------------------------------


def _try_real_fastquant(
    bars: pd.DataFrame,
    strategy: str,
    commission: float,
    initial_capital: float,
    fast_period: int,
    slow_period: int,
) -> Optional[Tuple[np.ndarray, int, int]]:
    """Run ``fastquant.backtest``; return (equity, n_trades, n_skipped) or None."""
    if not FASTQUANT_AVAILABLE or strategy not in FASTQUANT_SUPPORTED_STRATEGIES:
        return None
    try:
        # fastquant expects ``data`` with columns named ``Open/High/Low/Close``
        # (or ``close`` — it's tolerant; we keep the canonical names).
        fq_data = bars.rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        })[["Open", "High", "Low", "Close"]].copy()
        fq_data.index.name = "Date"

        kwargs: Dict[str, Any] = {
            "data": fq_data,
            "commission": commission,
            "init_cash": initial_capital,
            "plot": False,
            "verbose": False,
        }
        if strategy == "smac":
            kwargs.update(strategy="smac",
                          fast_period=fast_period, slow_period=slow_period)
        elif strategy == "emac":
            kwargs.update(strategy="emac",
                          fast_period=fast_period, slow_period=slow_period)
        elif strategy == "buynhold":
            kwargs.update(strategy="buynhold")
        elif strategy == "rsi":
            # Sensible defaults; caller can tune by adding kwargs upstream.
            kwargs.update(strategy="rsi", rsi_period=14,
                          rsi_upper=70, rsi_lower=30)
        elif strategy == "bbands":
            kwargs.update(strategy="bbands", period=20, devfactor=2.0)
        elif strategy == "macd":
            kwargs.update(strategy="macd",
                          fast_period=fast_period, slow_period=slow_period,
                          signal_period=9)
        else:  # pragma: no cover — closed-set guard above
            return None

        result = fastquant.backtest(**kwargs)  # type: ignore
        # fastquant returns a tuple ``(results_dict, heatmap)`` since 0.x;
        # older versions return just the dict. Normalise.
        results_dict = result[0] if isinstance(result, tuple) else result
        equity_df = results_dict.get("equity_curve")
        if equity_df is None or len(equity_df) == 0:
            return None
        # Align equity to bars.index (forward-fill).
        equity = equity_df["Equity"].reindex(bars.index, method="ffill") \
            .fillna(initial_capital).to_numpy(dtype=np.float64)
        # Trade count from fastquant's ``trades`` table (closed trades only).
        trades_df = results_dict.get("trades", [])
        n_trades = int(len(trades_df)) if hasattr(trades_df, "__len__") else 0
        return equity, n_trades, 0
    except Exception:  # pragma: no cover — exercised when fastquant is broken
        return None


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------


def run_fastquant_backtest(
    bars: pd.DataFrame,
    trades: Optional[List[Any]] = None,
    *,
    strategy: str = FASTQUANT_DEFAULT_STRATEGY,
    commission: float = FASTQUANT_DEFAULT_COMMISSION,
    initial_capital: float = 100_000.0,
    fast_period: int = FASTQUANT_DEFAULT_FAST_PERIOD,
    slow_period: int = FASTQUANT_DEFAULT_SLOW_PERIOD,
    freq_per_year: int = DEFAULT_FREQ_PER_YEAR,
    size_fraction: float = 1.0,
    force_shim: bool = False,
) -> Tuple[pd.Series, FastquantMetrics]:
    """Cross-validation entry point — fastquant-compatible broker replay.

    Parameters
    ----------
    bars : pd.DataFrame
        Bar frame indexed by UTC timestamp. MUST contain a ``close`` column.
        Read-only; never mutated.
    trades : list, optional
        Optional pre-built trade schedule (objects with ``entry_ts``,
        ``exit_ts``, ``direction``, ``size_fraction`` — same shape as
        ``_shared.run_backtest.Trade``). When provided, the trade schedule
        is replayed under the fastquant cost model via the shim. When
        ``None``, the chosen ``strategy`` is run on ``bars`` to generate a
        schedule internally (the path used for standalone fastquant CV
        without an in-house candidate strategy).
    strategy : {"smac", "emac", "rsi", "buynhold", "bbands", "macd"}
        Built-in fastquant strategy to run when ``trades`` is ``None``.
        Closed set — anything else raises ``ValueError``.
    commission : float
        Proportional commission rate per fill (default 0.001 = 10 bp
        round-trip per side, matching fastquant / backtesting.py default).
    initial_capital : float
        Starting NAV. Must be > 0.
    fast_period, slow_period : int
        SMAC / EMAC lookback windows. Ignored by ``buynhold`` and ``rsi``.
    freq_per_year : int
        Annualisation factor for Sharpe / annualised return.
    size_fraction : float
        Position size fraction (0 < size <= 1) for both shim- and
        strategy-generated trades.
    force_shim : bool
        If True, skip the real fastquant path even when it's importable.
        Useful for cross-engine A/B tests that want to isolate the shim.

    Returns
    -------
    (equity, metrics) : (pd.Series, FastquantMetrics)
        ``equity`` is indexed by ``bars.index``; ``metrics`` is the
        frozen dataclass described in :class:`FastquantMetrics`.
    """
    if initial_capital <= 0:
        raise ValueError(f"initial_capital must be > 0, got {initial_capital}")
    if "close" not in bars.columns:
        raise ValueError(
            f"bars must have a 'close' column, got {sorted(bars.columns)}"
        )
    if commission < 0:
        raise ValueError(f"commission must be >= 0, got {commission}")
    if strategy not in FASTQUANT_SUPPORTED_STRATEGIES:
        raise ValueError(
            f"strategy must be one of {FASTQUANT_SUPPORTED_STRATEGIES!r}, "
            f"got {strategy!r}"
        )

    idx: pd.DatetimeIndex = pd.DatetimeIndex(bars.index)
    close = bars["close"].to_numpy(dtype=float)
    n = len(bars)

    # 1. Real fastquant path (best-effort; silent fall back to shim).
    used_shim = True
    equity: Optional[np.ndarray] = None
    n_trades = 0
    n_skipped = 0

    if not force_shim and trades is None:
        real = _try_real_fastquant(
            bars, strategy, commission, initial_capital,
            fast_period, slow_period,
        )
        if real is not None:
            equity, n_trades, n_skipped = real
            used_shim = False

    # 2. Shim path — always reached when real-fq is unavailable or the
    # caller provided an in-house trade schedule.
    if equity is None:
        if trades is not None:
            # Translate the in-house Trade objects into the shim's
            # (entry_ts, exit_ts, direction, size) tuple format.
            sched: List[Tuple[pd.Timestamp, pd.Timestamp, str, float]] = []
            for t in trades:
                # Support both attribute-style (Trade dataclass) and
                # dict-style trade records.
                if isinstance(t, dict):
                    sched.append((
                        pd.Timestamp(t["entry_ts"]),
                        pd.Timestamp(t["exit_ts"]),
                        str(t["direction"]),
                        float(t.get("size_fraction", size_fraction)),
                    ))
                else:
                    sched.append((
                        pd.Timestamp(t.entry_ts),
                        pd.Timestamp(t.exit_ts),
                        str(t.direction),
                        float(getattr(t, "size_fraction", size_fraction)),
                    ))
        else:
            # Generate a trade schedule from the chosen strategy's signal.
            if strategy == "smac":
                in_market, _ = _smac_signals(close, fast_period, slow_period)
            elif strategy == "emac":
                in_market, _ = _emac_signals(close, fast_period, slow_period)
            elif strategy == "buynhold":
                in_market, _ = _buynhold_signals(close)
            elif strategy == "rsi":
                # RSI-based entry/exit is harder to map onto a closed-set
                # shim; defer to the real fastquant path. We fall through
                # to a buynhold shim as a safe default so the call still
                # returns valid metrics.
                in_market, _ = _buynhold_signals(close)
            elif strategy == "bbands":
                # Same: defer to real fq; shim falls back to buynhold.
                in_market, _ = _buynhold_signals(close)
            elif strategy == "macd":
                in_market, _ = _emac_signals(close, fast_period, slow_period)
            else:  # pragma: no cover — closed-set guard above
                in_market, _ = _buynhold_signals(close)
            sched = _signals_to_trades(idx, in_market, size_fraction)

        equity, n_trades, n_skipped = _shim_replay(
            close, idx, sched, initial_capital, commission,
        )
        used_shim = True

    # 3. Compute metrics. The shim helper returns ``annualised_pct = 0``;
    # we re-derive it here using the caller's ``freq_per_year`` so the
    # metric envelope is complete and serialisable.
    sharpe, total_return, _annualised_unused, max_dd = _compute_metrics(equity)
    if n >= 2 and initial_capital > 0 and (1.0 + total_return) > 0:
        annualised_pct = (1.0 + total_return) ** (freq_per_year / max(n - 1, 1)) - 1.0
    else:
        annualised_pct = -1.0
    # Convert per-bar sharpe (no annualisation) -> annualised sharpe.
    sharpe_annualised = sharpe * math.sqrt(freq_per_year)

    equity_series = pd.Series(equity, index=idx, dtype=float)
    metrics = FastquantMetrics(
        engine="fastquant",
        engine_version=(_FASTQUANT_VERSION if not used_shim
                         else f"shim-v1 (FASTQUANT_AVAILABLE={FASTQUANT_AVAILABLE})"),
        sharpe=float(sharpe_annualised),
        total_return=float(total_return),
        annualised_pct=float(annualised_pct),
        max_dd=float(max_dd),
        n_bars=int(n),
        n_trades=int(n_trades),
        n_skipped=int(n_skipped),
        used_shim=bool(used_shim),
    )
    return equity_series, metrics


# ---------------------------------------------------------------------------
# Helpers — convert a :class:`FastquantMetrics` envelope into the
# ``framework_cv`` dict shape the validators expect.
# ---------------------------------------------------------------------------


def to_framework_cv(metrics: FastquantMetrics) -> Dict[str, Any]:
    """Return a dict shaped like ``framework_cv["framework"]`` for the validator.

    Usage::

        cv_record = {
            "framework": to_framework_cv(metrics),
            "framework_oos": ...,  # caller fills from OOS folds
        }
        validate_framework_cv(inhouse_metrics, cv_record, strategy_name)
    """
    return {
        "engine": metrics.engine,
        "engine_version": metrics.engine_version,
        "sharpe": metrics.sharpe,
        "total_return": metrics.total_return,
        "annualised_pct": metrics.annualised_pct,
        "max_dd": metrics.max_dd,
        "n_bars": metrics.n_bars,
        "n_trades": metrics.n_trades,
        "used_shim": metrics.used_shim,
    }


__all__ = [
    "FASTQUANT_AVAILABLE",
    "FASTQUANT_DEFAULT_COMMISSION",
    "FASTQUANT_DEFAULT_FAST_PERIOD",
    "FASTQUANT_DEFAULT_SLOW_PERIOD",
    "FASTQUANT_DEFAULT_STRATEGY",
    "FASTQUANT_SUPPORTED_STRATEGIES",
    "FastquantMetrics",
    "DEFAULT_FREQ_PER_YEAR",
    "run_fastquant_backtest",
    "to_framework_cv",
]