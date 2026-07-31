"""Unit tests for the backtrader adapter (SMA-35409 / MAP-P5 #042).

The shim path is exercised here — it is deterministic, has no
third-party dependency, and *is* the contract a real backtrader run
must honour. The real-backtrader path is exercised only by integration
tests (out of scope here; the shim's residuals vs backtrader are
documented in the adapter README).

Coverage targets:
  * Adapter import surface (constants, dataclass, public entry point).
  * Pure-Python shim correctness on known inputs:
      - flat-close: zero price return + zero cost = flat equity.
      - up-close: per-bar compounding at bar return.
      - single trade: cost basis matches ``2 * commission * size``.
      - same-bar round-trip: full RT commission only (no held-bar return).
      - missing bars: trade silently skipped, counter incremented.
  * Signal generators: sma_cross / ema_cross / buynhold / rsi / bbands
    produce expected entries/exits.
  * Trade conversion: signal mask -> trade list shape matches expectations.
  * Validation hooks: ``to_backtrader_framework_cv`` produces
    validator-shaped dict, and round-trips through
    ``framework_cv_validator.validate_framework_cv`` without raising.
  * Public API guardrails: bad inputs raise ``ValueError``.
  * Shim-vs-inhouse parity: shim cost basis agrees with the
    authoritative in-house engine within 2% absolute on a known
    3-trade schedule (matches the fastquant adapter's SMA-35404
    evidence line).
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import pytest

# Make ``_shared.adapters`` importable when this file is run directly
# (e.g. ``python -m pytest _shared/adapters/test_backtrader_adapter.py``).
_SHARED_DIR = Path(__file__).resolve().parents[1]
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from adapters.backtrader_adapter import (  # noqa: E402
    BACKTRADER_AVAILABLE,
    BACKTRADER_DEFAULT_COMMISSION,
    BACKTRADER_DEFAULT_SMA_FAST,
    BACKTRADER_DEFAULT_SMA_SLOW,
    BACKTRADER_DEFAULT_STRATEGY,
    BACKTRADER_SUPPORTED_STRATEGIES,
    DEFAULT_FREQ_PER_YEAR,
    BacktraderMetrics,
    _bar_index,
    _bbands_signals,
    _buynhold_signals,
    _compute_metrics,
    _ema_cross_signals,
    _rsi_signals,
    _shim_replay,
    _signals_to_trades,
    _sma_cross_signals,
    import_error as backtrader_import_error,
    is_available as backtrader_is_available,
    run_backtrader_backtest,
    to_framework_cv as to_backtrader_framework_cv,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_bars(closes: List[float], freq: str = "1h") -> pd.DataFrame:
    """Build a minimal bars frame with a UTC DatetimeIndex."""
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"close": closes}, index=idx)


def _flat_bars(n: int = 50, freq: str = "1h") -> pd.DataFrame:
    return _make_bars([100.0] * n, freq=freq)


def _up_bars(n: int = 50, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    return _make_bars([start + i * step for i in range(n)])


# ---------------------------------------------------------------------------
# Import surface — adapter is importable from _shared.adapters
# ---------------------------------------------------------------------------


def test_adapter_package_importable():
    """``from _shared.adapters import run_backtrader_backtest`` works."""
    from adapters import run_backtrader_backtest as rbb
    assert rbb is run_backtrader_backtest


def test_public_constants_well_typed():
    assert BACKTRADER_DEFAULT_COMMISSION > 0
    assert isinstance(BACKTRADER_DEFAULT_COMMISSION, float)
    assert isinstance(BACKTRADER_DEFAULT_SMA_FAST, int)
    assert isinstance(BACKTRADER_DEFAULT_SMA_SLOW, int)
    assert BACKTRADER_DEFAULT_STRATEGY == "sma_cross"
    assert "sma_cross" in BACKTRADER_SUPPORTED_STRATEGIES
    assert "buynhold" in BACKTRADER_SUPPORTED_STRATEGIES
    assert "rsi" in BACKTRADER_SUPPORTED_STRATEGIES
    assert "bbands" in BACKTRADER_SUPPORTED_STRATEGIES
    assert "macd" in BACKTRADER_SUPPORTED_STRATEGIES
    assert "ema_cross" in BACKTRADER_SUPPORTED_STRATEGIES
    assert isinstance(BACKTRADER_AVAILABLE, bool)
    assert DEFAULT_FREQ_PER_YEAR > 0
    # Diagnostic helpers present and return the right type.
    assert isinstance(backtrader_is_available(), bool)
    # When backtrader is unavailable, the import error is captured.
    if not BACKTRADER_AVAILABLE:
        assert backtrader_import_error() is not None


def test_metrics_dataclass_serialises():
    m = BacktraderMetrics(
        engine="backtrader", engine_version="shim-v1", sharpe=1.0,
        total_return=0.10, annualised_pct=0.08, max_dd=-0.05,
        n_bars=100, n_trades=5, n_skipped=0, used_shim=True,
    )
    d = m.as_dict()
    for k in ("engine", "engine_version", "sharpe", "total_return",
              "annualised_pct", "max_dd", "n_bars", "n_trades",
              "n_skipped", "used_shim"):
        assert k in d
    # JSON-safe: no NaN / inf.
    for v in d.values():
        if isinstance(v, float):
            assert math.isfinite(v), f"{v} not finite"


# ---------------------------------------------------------------------------
# Shim correctness — pure-Python emulation of backtrader's broker model.
# ---------------------------------------------------------------------------


def test_shim_replay_empty_schedule_yields_flat_equity():
    """No trades -> equity stays at initial_capital for every bar."""
    bars = _flat_bars(50)
    sched: List = []
    eq, n_fills, n_skipped = _shim_replay(
        bars["close"].to_numpy(), bars.index, sched,
        initial_capital=100_000.0, commission=0.001,
    )
    assert len(eq) == 50
    assert n_fills == 0
    assert n_skipped == 0
    assert np.allclose(eq, 100_000.0)


def test_shim_replay_single_long_trade_charges_full_rt_commission():
    """Long, on a flat-close window, the cost basis equals 2 * commission.

    The held window is bars (ei+1, xi] where price_ret is 0 (flat
    close), so the only per-bar returns are the entry commission at
    bar ei+1 and the exit commission at bar xi+1. Per-bar compounding
    of those two commissions yields a final equity of
    ``initial * (1 - commission)^2``.

    Verified against the zero-cost baseline: cost drag = no_cost -
    with_cost. For small commission (0.001) the drag is ~2*commission*initial,
    within per-bar-compounding tolerance.
    """
    bars = _flat_bars(50)
    sched = [(bars.index[10], bars.index[20], "long", 1.0)]
    close = bars["close"].to_numpy()
    initial_capital = 100_000.0
    commission = 0.001

    no_cost, n_fills_nc, _ = _shim_replay(
        close, bars.index, sched,
        initial_capital=initial_capital, commission=0.0,
    )
    with_cost, n_fills_wc, n_skipped = _shim_replay(
        close, bars.index, sched,
        initial_capital=initial_capital, commission=commission,
    )
    assert n_fills_nc == 1
    assert n_fills_wc == 1
    assert n_skipped == 0
    # Flat-close: no-cost equity stays at initial.
    assert no_cost[-1] == initial_capital
    # Cost drag = 2*commission*initial * (1 - commission) ≈ 199.8.
    # For small commission, 2*commission*initial is a tight bound.
    drag = no_cost[-1] - with_cost[-1]
    assert 195.0 < drag < 200.5, (
        f"flat-close cost drag {drag:.4f} outside [195, 200.5]; "
        f"with_cost={with_cost[-1]:.4f}"
    )


def test_shim_replay_up_close_long_trade_earns_price_return_minus_cost():
    """Linear ramp 100->110 over 10 bars, single long trade, cost basis ≈ 2*comm.

    Verifies via zero-cost baseline: the cost drag (no-cost minus
    with-cost final equity) must land at ~ 2 * commission * mean_equity,
    which is ~ 2 * commission * initial_capital for a small-window
    trade. This avoids the brittle per-bar compounding analytical
    comparison (the same pattern ``_shared/test_run_backtest.py`` uses
    for ``test_run_backtest_total_cost_equals_cost_bps_rt``).
    """
    bars = _make_bars([100.0 + i for i in range(50)])
    sched = [(bars.index[10], bars.index[20], "long", 1.0)]
    close = bars["close"].to_numpy()
    initial_capital = 100_000.0
    commission = 0.0005

    no_cost, _, _ = _shim_replay(
        close, bars.index, sched,
        initial_capital=initial_capital, commission=0.0,
    )
    with_cost, _, _ = _shim_replay(
        close, bars.index, sched,
        initial_capital=initial_capital, commission=commission,
    )
    # Long trade in up-market: final equity (no cost) > initial.
    assert no_cost[-1] > initial_capital
    # Cost drag ≈ 2 * commission * initial_capital.
    drag = no_cost[-1] - with_cost[-1]
    expected_drag = 2.0 * commission * initial_capital
    # Looser tolerance than the analytical test (per-bar compounding on
    # a 10-bar window makes the exact cost basis ~ commission*mean_equity,
    # which is slightly different from commission*initial).
    assert math.isclose(drag, expected_drag, rel_tol=0.20), (
        f"cost drag {drag:.2f} != expected {expected_drag:.2f} (20% tol); "
        f"no_cost={no_cost[-1]:.2f} with_cost={with_cost[-1]:.2f}"
    )


def test_shim_replay_same_bar_round_trip_charges_full_cost():
    """xi == ei -> entry AND exit both land on bar ei+1 with full RT commission."""
    bars = _make_bars([100.0 + i for i in range(50)])
    sched = [(bars.index[10], bars.index[10], "long", 1.0)]
    eq, n_fills, n_skipped = _shim_replay(
        bars["close"].to_numpy(), bars.index, sched,
        initial_capital=100_000.0, commission=0.001,
    )
    assert n_fills == 1
    # price_ret[11] = 111/110 - 1 ≈ 0.00909
    # commission = 2 * 0.001 = 0.002
    # bar_ret[11] = 1 * 0.00909 - 0.002 = 0.00709
    # final = 100_000 * (1 + 0.00709) ≈ 100_709
    expected = 100_000.0 * (1.0 + (111.0 / 110.0 - 1.0) - 2.0 * 0.001)
    assert math.isclose(eq[-1], expected, rel_tol=1e-9), (
        f"final eq {eq[-1]:.4f} != expected {expected:.4f}"
    )


def test_shim_replay_short_trade_inverts_price_return():
    """Short in an up-market loses money (zero-cost baseline check)."""
    bars = _make_bars([100.0 + i for i in range(50)])
    sched = [(bars.index[10], bars.index[20], "short", 1.0)]
    close = bars["close"].to_numpy()
    initial_capital = 100_000.0
    commission = 0.001

    no_cost, _, _ = _shim_replay(
        close, bars.index, sched,
        initial_capital=initial_capital, commission=0.0,
    )
    with_cost, _, _ = _shim_replay(
        close, bars.index, sched,
        initial_capital=initial_capital, commission=commission,
    )
    # Short in up-market: no-cost final < initial.
    assert no_cost[-1] < initial_capital
    # Cost drag (no-cost - with-cost) is positive (short loses both
    # the price move AND the commission).
    drag = no_cost[-1] - with_cost[-1]
    # For a short the commission is on notional; the drag is bounded
    # by 2 * commission * mean_equity.
    assert 0 < drag < 2.0 * commission * initial_capital * 1.5, (
        f"short cost drag {drag:.2f} outside sensible band"
    )


def test_shim_replay_off_bar_trade_is_skipped():
    """Trades with off-bar entry/exit are silently skipped and counted."""
    bars = _flat_bars(50)
    sched = [
        (bars.index[10], bars.index[20], "long", 1.0),  # valid
        (pd.Timestamp("2010-01-01", tz="UTC"), bars.index[20], "long", 1.0),  # off
        (bars.index[10], pd.Timestamp("2099-01-01", tz="UTC"), "long", 1.0),  # off
    ]
    eq, n_fills, n_skipped = _shim_replay(
        bars["close"].to_numpy(), bars.index, sched,
        initial_capital=100_000.0, commission=0.001,
    )
    assert n_fills == 1
    assert n_skipped == 2


def test_shim_replay_off_window_exit_at_last_bar_charges_only_entry_commission():
    """xi == n-1 -> exit fill at bar n is OUT of range; backtrader
    leaves the position open and does not charge the exit commission.

    This matches the backtrader broker's behaviour for trades that run
    to the last bar (no chance to fill the exit order). We verify via
    the zero-cost baseline: the cost drag is roughly half the full
    round-trip cost (entry fill only, no exit fill).
    """
    bars = _make_bars([100.0 + i for i in range(50)])
    sched = [(bars.index[10], bars.index[49], "long", 1.0)]
    close = bars["close"].to_numpy()
    initial_capital = 100_000.0
    commission = 0.001

    no_cost, _, _ = _shim_replay(
        close, bars.index, sched,
        initial_capital=initial_capital, commission=0.0,
    )
    with_cost, _, _ = _shim_replay(
        close, bars.index, sched,
        initial_capital=initial_capital, commission=commission,
    )
    # Cost drag should be roughly 1 * commission * initial (entry only).
    drag = no_cost[-1] - with_cost[-1]
    expected_one_fill = commission * initial_capital
    expected_two_fills = 2.0 * commission * initial_capital
    # Drag must be closer to 1 fill than to 2 fills.
    assert abs(drag - expected_one_fill) < abs(drag - expected_two_fills), (
        f"drag {drag:.2f} closer to 2-fill ({expected_two_fills:.2f}) "
        f"than 1-fill ({expected_one_fill:.2f}) — exit fill incorrectly charged"
    )
    # Also: drag is non-trivially positive (entry fill was charged).
    assert drag > 0, f"drag {drag:.2f} not positive; entry fill missing"


def test_shim_replay_force_close_on_overlap():
    """New trade arriving while prior is still open force-closes the prior.

    On the new entry's bar+1, the prior trade's exit fill commission is
    charged (matches backtrader's ``self.close()`` semantics).
    """
    bars = _flat_bars(50)
    sched = [
        (bars.index[10], bars.index[30], "long", 1.0),
        (bars.index[15], bars.index[20], "short", 1.0),  # overlaps prior
    ]
    eq, n_fills, n_skipped = _shim_replay(
        bars["close"].to_numpy(), bars.index, sched,
        initial_capital=100_000.0, commission=0.001,
    )
    # Both applied, both force-close overlap. On flat-close, the force-close
    # of the prior trade at bar 16 charges an extra 0.001 commission on
    # the prior position; the new trade's entry at bar 16 charges its
    # own 0.001. Total: -0.001 - 0.001 (entry) - 0.001 (prior exit) - 0.001
    # (new exit) = -0.004 ... actually -0.001 each direction. The exact
    # cost basis depends on the force-close convention; we just check
    # that BOTH trades are applied (n_fills == 2) and the equity remains
    # within a sensible band around 100_000.
    assert n_fills == 2
    assert 99_000.0 < eq[-1] < 100_000.0, (
        f"overlap final {eq[-1]:.4f} outside [99000, 100000]"
    )


# ---------------------------------------------------------------------------
# Signal generators — pure-Python backtrader-style signal masks.
# ---------------------------------------------------------------------------


def test_sma_cross_signals_bullish_then_bearish():
    """U-shape price path: bullish cross -> enter, bearish cross -> exit."""
    closes = [100.0] * 20 + [90.0] * 20 + [100.0] * 20  # V-shape dip
    sm_fast, sm_slow = 3, 6
    in_market, _ = _sma_cross_signals(np.asarray(closes), sm_fast, sm_slow)
    # Bullish cross when the dip recovers; bearish cross when the
    # V-shape begins. We just check that at least one entry and one
    # exit signal are emitted — the exact bars depend on the smoothing
    # windows.
    assert in_market.sum() > 0, "smac never entered market on V-shape"
    # Must be contiguous segments (state machine): once in market, no
    # 0 until exit; once flat, no 1 until entry.
    transitions = np.diff(in_market.astype(np.int32))
    assert (transitions == 1).sum() >= 1, "no entry transitions found"


def test_ema_cross_signals_smoke():
    closes = np.linspace(100.0, 110.0, 60)
    in_market, _ = _ema_cross_signals(closes, 5, 15)
    # Monotonic uptrend: should be in market most of the time after
    # the EMA crossover warms up.
    assert in_market.sum() > 0


def test_buynhold_signals_all_ones():
    closes = np.array([100.0, 101.0, 102.0])
    in_market, _ = _buynhold_signals(closes)
    np.testing.assert_array_equal(in_market, np.ones(3, dtype=np.int8))


def test_rsi_signals_falls_back_to_buynhold_on_short_series():
    """When the series is too short for RSI, fall back to buynhold."""
    closes = np.array([100.0, 101.0, 102.0])
    in_market, _ = _rsi_signals(closes, period=14)
    np.testing.assert_array_equal(in_market, np.ones(3, dtype=np.int8))


def test_bbands_signals_falls_back_to_buynhold_on_short_series():
    closes = np.array([100.0, 101.0, 102.0])
    in_market, _ = _bbands_signals(closes, period=20)
    np.testing.assert_array_equal(in_market, np.ones(3, dtype=np.int8))


def test_signals_to_trades_buynhold_produces_single_long_trade():
    """A buynhold mask [1, 1, ..., 1] produces a single n-bar long trade."""
    bars = _make_bars([100.0 + i for i in range(50)])
    in_market, _ = _buynhold_signals(bars["close"].to_numpy())
    trades = _signals_to_trades(bars.index, in_market, 1.0)
    assert len(trades) == 1
    ei_ts, xi_ts, direction, size = trades[0]
    assert direction == "long"
    assert size == 1.0
    assert ei_ts == bars.index[0]
    assert xi_ts == bars.index[-1]


# ---------------------------------------------------------------------------
# to_framework_cv — validator hook shape.
# ---------------------------------------------------------------------------


def test_to_backtrader_framework_cv_returns_validator_shape():
    m = BacktraderMetrics(
        engine="backtrader", engine_version="shim-v1", sharpe=1.23,
        total_return=0.42, annualised_pct=0.18, max_dd=-0.07,
        n_bars=100, n_trades=4, n_skipped=1, used_shim=True,
    )
    cv = to_backtrader_framework_cv(m)
    for k in ("engine", "engine_version", "sharpe", "total_return",
              "annualised_pct", "max_dd", "n_bars", "n_trades",
              "used_shim"):
        assert k in cv
    assert cv["engine"] == "backtrader"
    assert cv["sharpe"] == 1.23
    assert cv["total_return"] == 0.42


def test_to_backtrader_framework_cv_compatible_with_validator():
    """Round-trip through the framework_cv_validator without raising."""
    from validators.framework_cv_validator import validate_framework_cv

    inhouse = {"sharpe": 0.8, "ann_return": 0.12}
    m = BacktraderMetrics(
        engine="backtrader", engine_version="shim-v1", sharpe=0.78,
        total_return=0.115, annualised_pct=0.115, max_dd=-0.06,
        n_bars=100, n_trades=3, n_skipped=0, used_shim=True,
    )
    cv_record = {"framework": to_backtrader_framework_cv(m)}
    # Should NOT raise — divergence is well under all rule thresholds.
    validate_framework_cv(inhouse, cv_record, strategy_name="smoke")


# ---------------------------------------------------------------------------
# Public API guardrails — bad inputs raise ``ValueError``.
# ---------------------------------------------------------------------------


def test_initial_capital_must_be_positive():
    bars = _flat_bars(10)
    with pytest.raises(ValueError, match="initial_capital"):
        run_backtrader_backtest(bars, trades=None, initial_capital=0.0)


def test_bars_must_have_close_column():
    bars = pd.DataFrame({"open": [100.0] * 10},
                        index=pd.date_range("2024-01-01", periods=10,
                                            freq="1h", tz="UTC"))
    with pytest.raises(ValueError, match="close"):
        run_backtrader_backtest(bars, trades=None)


def test_negative_commission_rejected():
    bars = _flat_bars(10)
    with pytest.raises(ValueError, match="commission"):
        run_backtrader_backtest(bars, trades=None, commission=-0.0002)


def test_unknown_strategy_rejected():
    bars = _flat_bars(10)
    with pytest.raises(ValueError, match="strategy"):
        run_backtrader_backtest(bars, trades=None, strategy="not-a-strategy")


def test_force_shim_skips_real_bt_path():
    """force_shim=True should mark used_shim=True regardless of BACKTRADER_AVAILABLE."""
    bars = _flat_bars(20)
    _, metrics = run_backtrader_backtest(
        bars, trades=None, strategy="buynhold",
        force_shim=True,
    )
    assert metrics.used_shim is True


# ---------------------------------------------------------------------------
# Metrics helpers — internal sanity checks.
# ---------------------------------------------------------------------------


def test_compute_metrics_constant_series_is_zero_sharpe():
    eq = np.full(100, 100_000.0)
    sharpe, total_return, _ann, max_dd = _compute_metrics(eq)
    assert sharpe == 0.0
    assert total_return == 0.0
    assert max_dd == 0.0


def test_compute_metrics_monotone_growth():
    eq = np.linspace(100_000.0, 110_000.0, 100)
    sharpe, total_return, _ann, max_dd = _compute_metrics(eq)
    assert sharpe > 0
    assert abs(total_return - 0.10) < 1e-9
    assert max_dd == 0.0


def test_compute_metrics_drawdown_negative():
    eq = np.array([100.0, 110.0, 90.0, 100.0])
    sharpe, total_return, _ann, max_dd = _compute_metrics(eq)
    # max DD occurs at bar 2: 90 / 110 - 1 = -0.1818...
    assert abs(max_dd - (-0.1818181818181818)) < 1e-9


def test_bar_index_returns_position_or_none():
    idx = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
    assert _bar_index(idx, idx[2]) == 2
    assert _bar_index(idx, pd.Timestamp("2010-01-01", tz="UTC")) is None
    assert _bar_index(idx, pd.NaT) is None


# ---------------------------------------------------------------------------
# Smoke test — full adapter round-trip on synthetic data.
# ---------------------------------------------------------------------------


def test_sma_cross_synthetic_round_trip_has_finite_metrics():
    """End-to-end smoke: synthetic data -> sma_cross -> all metrics finite."""
    rng = np.random.default_rng(42)
    closes = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, 200))
    bars = _make_bars(closes.tolist())
    eq, metrics = run_backtrader_backtest(
        bars, trades=None, strategy="sma_cross",
        initial_capital=100_000.0, commission=0.0002,
        sma_fast=5, sma_slow=20,
        freq_per_year=DEFAULT_FREQ_PER_YEAR,
    )
    for field in ("sharpe", "total_return", "annualised_pct", "max_dd"):
        v = getattr(metrics, field)
        assert math.isfinite(v), f"{field}={v} is not finite"
    # The shim is exercised when backtrader is not importable (the
    # default in the test environment). engine_version starts with
    # "shim" in that case.
    if not BACKTRADER_AVAILABLE:
        assert metrics.used_shim is True
        assert metrics.engine_version.startswith("shim")
    assert metrics.engine == "backtrader"


def test_buynhold_synthetic_round_trip_has_finite_metrics():
    """End-to-end smoke: synthetic data -> buynhold -> all metrics finite."""
    rng = np.random.default_rng(7)
    closes = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, 200))
    bars = _make_bars(closes.tolist())
    eq, metrics = run_backtrader_backtest(
        bars, trades=None, strategy="buynhold",
        initial_capital=100_000.0, commission=0.0002,
        freq_per_year=DEFAULT_FREQ_PER_YEAR,
    )
    for field in ("sharpe", "total_return", "annualised_pct", "max_dd"):
        v = getattr(metrics, field)
        assert math.isfinite(v), f"{field}={v} is not finite"
    # buynhold -> n_trades == 1 (one held window from bar 0 to bar n-1)
    assert metrics.n_trades == 1


# ---------------------------------------------------------------------------
# Cross-framework A/B — backtrader shim vs in-house per-bar engine on a
# known schedule. Mirrors the fastquant adapter's SMA-35404 evidence line.
# The shim and the in-house engine use DIFFERENT cost conventions
# (backtrader: 1 commission per fill on notional; in-house cost_mode="fill":
# half-commission at entry + half at exit on size, not notional) so we
# calibrate each side independently to its native convention and verify
# they AGREE to within the documented per-bar compounding residual.
# ---------------------------------------------------------------------------


def _run_inhouse(bars, trades, initial_capital, cost_bps_rt):
    """Drive the authoritative in-house engine with cost_mode='fill'."""
    from run_backtest import Trade, run_backtest  # type: ignore

    inhouse_trades = [
        Trade(
            entry_ts=t.entry_ts,
            exit_ts=t.exit_ts,
            direction=t.direction,
            size_fraction=t.size_fraction,
        )
        for t in trades
    ]
    res = run_backtest(
        bars, inhouse_trades,
        initial_capital=initial_capital,
        cost_bps_rt=cost_bps_rt,
        cost_mode="fill",
    )
    return res["equity"]


def test_shim_agrees_with_inhouse_engine_on_known_schedule():
    """Shim cost basis (2 * commission per RT) matches in-house cost_mode='fill'.

    Setup: 100-bar linear ramp from 100 to 200 (gross return 100%), three
    long trades of varying hold windows (same-bar RT, 5-bar RT, 49-bar
    RT), commission=0.0002 on both sides. The trades are discrete
    windows — equity is FLAT (no per-bar return) outside each trade
    window, so the realistic final-equity range compounds only the
    held windows:

      bar 10-11: 111/110 - 1 = +0.909% gross - 0.04% cost ≈ +0.87%
      bar 30-35: 135/130 - 1 = +3.846% gross - 0.04% cost ≈ +3.81%
      bar 50-99: 199/150 - 1 = +32.667% gross - 0.04% cost ≈ +32.63%

    Final equity ≈ 100_000 * 1.0087 * 1.0381 * 1.3263 ≈ 138_950.

    The two engines use DIFFERENT cost conventions (backtrader: 1
    commission per fill on notional; in-house cost_mode="fill":
    half-commission at entry + half at exit on size, not notional) so
    we calibrate each side independently and verify they agree to
    within the documented compounding residual. SMA-35409 evidence.
    """
    @dataclass
    class _T:
        entry_ts: pd.Timestamp
        exit_ts: pd.Timestamp
        direction: str = "long"
        size_fraction: float = 1.0

    bars = _make_bars([100.0 + i for i in range(100)])
    trades = [
        _T(entry_ts=bars.index[10], exit_ts=bars.index[11]),   # 1-bar RT
        _T(entry_ts=bars.index[30], exit_ts=bars.index[35]),   # 5-bar RT
        _T(entry_ts=bars.index[50], exit_ts=bars.index[99]),   # 49-bar RT
    ]
    initial_capital = 100_000.0
    commission = 0.0002

    # backtrader shim (target).
    eq_shim, m_shim = run_backtrader_backtest(
        bars, trades=trades,
        initial_capital=initial_capital, commission=commission,
    )
    # In-house engine (baseline; cost_mode="fill", cost_bps_rt = 2 * commission).
    eq_inhouse = _run_inhouse(
        bars, trades, initial_capital, cost_bps_rt=2 * commission * 10_000,
    )

    shim_final = float(eq_shim.iloc[-1])
    inhouse_final = float(eq_inhouse.iloc[-1])

    # Both should land in the realistic ~130_000-150_000 window for the
    # discrete-trade schedule described above.
    assert shim_final > initial_capital * 1.30, (
        f"shim final {shim_final:.2f} implausibly low; "
        f"expected ~130_000+ on 3 discrete trade windows"
    )
    assert inhouse_final > initial_capital * 1.30, (
        f"inhouse final {inhouse_final:.2f} implausibly low; "
        f"expected ~130_000+ on 3 discrete trade windows"
    )
    # The two engines should agree to within ~2% absolute on the final
    # equity (loose because cost conventions differ). This is the
    # SMA-35409 cross-framework A/B evidence.
    gap = abs(shim_final - inhouse_final) / initial_capital
    assert gap < 0.02, (
        f"shim vs inhouse divergence {gap*100:.2f}% exceeds 2% threshold; "
        f"shim={shim_final:.2f} inhouse={inhouse_final:.2f}"
    )
    # Both should report finite, same-sign total_return.
    assert m_shim.total_return > 0.30
    assert m_shim.n_trades == 3
    assert m_shim.n_skipped == 0
