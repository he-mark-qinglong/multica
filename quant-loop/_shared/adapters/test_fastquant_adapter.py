"""Unit tests for the fastquant adapter (SMA-35404 / MAP-P5 #037).

The shim path is exercised here — it's deterministic, has no third-party
dependency, and *is* the contract a real fastquant run must honour. The
real-fq path is exercised only by integration tests (out of scope here;
the shim's residuals vs backtesting.py are documented in the adapter
README).

Coverage targets:
  * Adapter import surface (constants, dataclass, public entry point).
  * Pure-Python shim correctness on known inputs:
      - flat-close: zero price return + zero cost = flat equity.
      - up-close: per-bar compounding at bar return.
      - single trade: cost basis matches ``2 * commission * size``.
      - same-bar round-trip: full RT commission only (no held-bar return).
      - missing bars: trade silently skipped, counter incremented.
  * Signal generators: SMAC and EMAC produce expected entries/exits.
  * Trade conversion: signal mask -> trade list shape matches expectations.
  * Validation hooks: ``to_framework_cv`` produces validator-shaped dict.
  * Public API guardrails: bad inputs raise ``ValueError``.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import pytest

# Make ``_shared.adapters`` importable when this file is run directly
# (e.g. ``python -m pytest _shared/adapters/test_fastquant_adapter.py``).
_SHARED_DIR = Path(__file__).resolve().parents[1]
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from adapters.fastquant_adapter import (  # noqa: E402
    FASTQUANT_AVAILABLE,
    FASTQUANT_DEFAULT_COMMISSION,
    FASTQUANT_DEFAULT_FAST_PERIOD,
    FASTQUANT_DEFAULT_SLOW_PERIOD,
    FASTQUANT_DEFAULT_STRATEGY,
    FASTQUANT_SUPPORTED_STRATEGIES,
    DEFAULT_FREQ_PER_YEAR,
    FastquantMetrics,
    _buynhold_signals,
    _compute_metrics,
    _emac_signals,
    _shim_replay,
    _signals_to_trades,
    _smac_signals,
    run_fastquant_backtest,
    to_framework_cv,
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
    """``from _shared.adapters import run_fastquant_backtest`` works."""
    from adapters import run_fastquant_backtest as rfb
    assert rfb is run_fastquant_backtest


def test_public_constants_well_typed():
    assert FASTQUANT_DEFAULT_COMMISSION > 0
    assert isinstance(FASTQUANT_DEFAULT_COMMISSION, float)
    assert isinstance(FASTQUANT_DEFAULT_FAST_PERIOD, int)
    assert isinstance(FASTQUANT_DEFAULT_SLOW_PERIOD, int)
    assert FASTQUANT_DEFAULT_STRATEGY == "smac"
    assert "smac" in FASTQUANT_SUPPORTED_STRATEGIES
    assert "buynhold" in FASTQUANT_SUPPORTED_STRATEGIES
    assert isinstance(FASTQUANT_AVAILABLE, bool)
    assert DEFAULT_FREQ_PER_YEAR > 0


def test_metrics_dataclass_serialises():
    m = FastquantMetrics(
        engine="fastquant", engine_version="shim-v1", sharpe=1.0,
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
# Shim correctness — pure-Python emulation of fastquant's broker model.
# ---------------------------------------------------------------------------


def test_shim_flat_close_preserves_equity_when_no_trades():
    """Flat close + no trades = equity stays at initial_capital."""
    bars = _flat_bars(50)
    eq, metrics = run_fastquant_backtest(
        bars, trades=None,
        initial_capital=100_000.0, commission=0.001,
    )
    assert metrics.n_trades == 0
    np.testing.assert_allclose(eq.to_numpy(), 100_000.0, atol=1e-6)


def test_shim_buynhold_on_rising_close_grows_equity():
    """buynhold on a linearly rising close should grow equity.

    With the corrected shim:
      - entry fill at bar 1's open, price_ret[1] captured at bar 1.
      - middle bars 2..49 capture pure price_ret (no commission).
      - exit fill would land at bar 50 (out of range for 50-bar window),
        so no exit commission is charged — position is still open.
    Per-bar return = 0.01 (1/100), 48 held bars (bars 1..49), one
    entry commission (0.001) at bar 1.
    Total return ≈ (1 + 0.01 - 0.001)(1.01)^47 - 1 ≈ (1.009)(1.01)^47 - 1
        ≈ 1.009 * 1.604 - 1 ≈ 0.619.
    """
    bars = _up_bars(50, start=100.0, step=1.0)
    eq, metrics = run_fastquant_backtest(
        bars, trades=None, strategy="buynhold",
        initial_capital=100_000.0, commission=0.001,
        freq_per_year=DEFAULT_FREQ_PER_YEAR,
    )
    assert metrics.n_trades == 1
    # 49 close-to-close returns (101/100 .. 149/148), 1 entry commission
    # at bar 1; geometric mean of close-to-close = (149/100)^(1/49) ≈ 1.00830
    # per bar. 49 held bars × ~0.83% ≈ 49.7% gross - 1 bp commission ≈ 48.85%.
    assert 0.45 < metrics.total_return < 0.55
    assert metrics.used_shim is True


def test_shim_inhouse_trade_replay_known_cost():
    """A 1-bar held trade with known commission produces a predictable PnL.

    Setup: flat close at 100.0, one long trade ei=10, xi=11 with
    size=1.0, commission=0.001. Held window is bar 11 (one bar);
    entry fill lands at bar 11's open (price_ret[11] = 0), exit fill
    lands at bar 12's open (commission only, no price return).
    Per-bar return at bar 11 = 0 - commission = -0.001; per-bar return
    at bar 12 = -commission = -0.001. Total return over 50 bars
    ≈ (1 - 0.001)(1 - 0.001) - 1 ≈ -0.001999.
    Equity at bar 11 = 100_000 * (1 - 0.001) = 99_900.
    Equity at bar 12 = 99_900 * (1 - 0.001) = 99_800.
    """
    from dataclasses import dataclass

    @dataclass
    class _T:
        entry_ts: pd.Timestamp
        exit_ts: pd.Timestamp
        direction: str = "long"
        size_fraction: float = 1.0

    bars = _flat_bars(50)
    trades = [_T(entry_ts=bars.index[10], exit_ts=bars.index[11])]
    eq, metrics = run_fastquant_backtest(
        bars, trades=trades,
        initial_capital=100_000.0, commission=0.001,
    )
    assert metrics.n_trades == 1
    assert metrics.n_skipped == 0
    # Equity at bar 11 = 100_000 * (1 - 0.001) = 99_900 (entry only).
    np.testing.assert_allclose(eq.iloc[11], 99_900.0, atol=1e-6)
    # Equity at bar 12 = 99_900 * (1 - 0.001) = 99_800.1 (exit commission
    # + per-bar compounding residual — the exact value is 99_800.1, not
    # 99_800, because bar 12's return is applied to bar 11's already-
    # reduced equity).
    np.testing.assert_allclose(eq.iloc[12], 99_800.1, atol=1e-6)
    # Total return = 99_800.1 / 100_000 - 1 = -0.001999 (compounded).
    np.testing.assert_allclose(metrics.total_return, -0.001999, atol=1e-9)
    # Max DD is the single trade drawdown (recovered at bar 12).
    assert metrics.max_dd < 0


def test_shim_long_trade_on_up_close_grows_equity():
    """Long held over rising bars, full commission applied."""
    from dataclasses import dataclass

    @dataclass
    class _T:
        entry_ts: pd.Timestamp
        exit_ts: pd.Timestamp
        direction: str = "long"
        size_fraction: float = 1.0

    # Linear ramp from 100 to 110 over 11 bars (10 held bars).
    bars = _make_bars([100.0 + i for i in range(11)])
    trades = [_T(entry_ts=bars.index[0], exit_ts=bars.index[10])]
    eq, metrics = run_fastquant_backtest(
        bars, trades=trades,
        initial_capital=100_000.0, commission=0.001,
        freq_per_year=DEFAULT_FREQ_PER_YEAR,
    )
    assert metrics.n_trades == 1
    # Gross return over the held window (bars 1..10):
    #   close[10] / close[0] - 1 = 110/100 - 1 = 0.10
    # Commission deducted once at entry bar (0.0005) and exit bar (0.0005)
    # of position notional — per-bar compounding rounds it slightly.
    # Net total return should be roughly 0.10 - 0.001 = 0.099, with
    # compounding rounding on the order of commission*10 = 0.01.
    assert metrics.total_return > 0.08
    assert metrics.total_return < 0.105


def test_shim_short_trade_on_up_close_loses_money():
    """Short direction inverts the sign of price return."""
    from dataclasses import dataclass

    @dataclass
    class _T:
        entry_ts: pd.Timestamp
        exit_ts: pd.Timestamp
        direction: str
        size_fraction: float = 1.0

    bars = _make_bars([100.0 + i for i in range(11)])
    trades = [_T(entry_ts=bars.index[0], exit_ts=bars.index[10],
                  direction="short")]
    eq, metrics = run_fastquant_backtest(
        bars, trades=trades,
        initial_capital=100_000.0, commission=0.001,
        freq_per_year=DEFAULT_FREQ_PER_YEAR,
    )
    # Short over rising close -> net loss.
    assert metrics.total_return < -0.08
    assert metrics.max_dd < 0


def test_shim_skips_trades_with_missing_bars():
    """Trades whose entry or exit falls outside bars.index are skipped."""
    from dataclasses import dataclass

    @dataclass
    class _T:
        entry_ts: pd.Timestamp
        exit_ts: pd.Timestamp
        direction: str = "long"
        size_fraction: float = 1.0

    bars = _flat_bars(20)
    # One good trade, one bad (exit before entry), one off-bar.
    good = _T(entry_ts=bars.index[5], exit_ts=bars.index[10])
    bad_window = _T(entry_ts=bars.index[10], exit_ts=bars.index[5])
    off_bar = _T(entry_ts=bars.index[15],
                 exit_ts=pd.Timestamp("2099-01-01", tz="UTC"))
    eq, metrics = run_fastquant_backtest(
        bars, trades=[good, bad_window, off_bar],
        initial_capital=100_000.0, commission=0.001,
    )
    assert metrics.n_trades == 1
    assert metrics.n_skipped == 2


def test_shim_same_bar_roundtrip_charges_full_commission_only():
    """A 1-bar trade (xi == ei+1) charges exactly 2 * commission total."""
    from dataclasses import dataclass

    @dataclass
    class _T:
        entry_ts: pd.Timestamp
        exit_ts: pd.Timestamp
        direction: str = "long"
        size_fraction: float = 1.0

    # Flat close at 100 — no price contribution. ei=10, xi=11 => entry
    # fill at bar 11, exit fill at bar 12. fastquant charges one
    # commission per fill, so the round-trip costs exactly 2 * commission,
    # NOT 3 * commission (that was the pre-fix overcharging bug).
    bars = _flat_bars(50)
    trades = [_T(entry_ts=bars.index[10], exit_ts=bars.index[11])]
    eq, metrics = run_fastquant_backtest(
        bars, trades=trades,
        initial_capital=100_000.0, commission=0.001,
    )
    # Compounded: 100_000 * (1 - 0.001)(1 - 0.001) - 1 = -0.001999.
    # The "exactly -0.002" form would only hold under additive returns;
    # under per-bar compounding the residual is one bp.
    np.testing.assert_allclose(metrics.total_return, -0.001999, atol=1e-9)


# ---------------------------------------------------------------------------
# Signal generators — SMAC / EMAC / buynhold produce expected schedules.
# ---------------------------------------------------------------------------


def test_smac_signals_produce_entry_after_golden_cross():
    """SMAC on a price series that crosses up should produce an entry mask."""
    # Construct a series with a clear uptrend after a flat warmup.
    closes = [100.0] * 30 + [100.0 + i for i in range(20)]
    in_market, _ = _smac_signals(np.asarray(closes), fast_period=5,
                                 slow_period=20)
    # Should be flat for the first ~30 bars (no signal), then enter.
    assert in_market[5] == 0   # still in warmup
    assert in_market.sum() > 0  # at least one entry fires


def test_buynhold_signals_in_market_from_bar_zero():
    closes = [100.0] * 10
    in_market, _ = _buynhold_signals(np.asarray(closes))
    assert in_market[0] == 1
    assert in_market.sum() == 10


def test_signals_to_trades_emits_one_trade_per_run():
    closes = [100.0] * 5 + [110.0] * 5  # up then flat
    idx = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    in_market, _ = _buynhold_signals(np.asarray(closes))
    trades = _signals_to_trades(idx, in_market, size_fraction=1.0)
    assert len(trades) == 1
    ei_ts, xi_ts, direction, size = trades[0]
    assert direction == "long"
    assert size == 1.0
    assert ei_ts == idx[0]
    assert xi_ts == idx[-1]


# ---------------------------------------------------------------------------
# to_framework_cv — validator hook shape.
# ---------------------------------------------------------------------------


def test_to_framework_cv_returns_validator_shape():
    m = FastquantMetrics(
        engine="fastquant", engine_version="shim-v1", sharpe=1.23,
        total_return=0.42, annualised_pct=0.18, max_dd=-0.07,
        n_bars=100, n_trades=4, n_skipped=1, used_shim=True,
    )
    cv = to_framework_cv(m)
    for k in ("engine", "engine_version", "sharpe", "total_return",
              "annualised_pct", "max_dd", "n_bars", "n_trades",
              "used_shim"):
        assert k in cv
    assert cv["engine"] == "fastquant"
    assert cv["sharpe"] == 1.23
    assert cv["total_return"] == 0.42


def test_to_framework_cv_compatible_with_validator():
    """Round-trip through the validator without raising."""
    from validators.framework_cv_validator import validate_framework_cv

    inhouse = {"sharpe": 0.8, "ann_return": 0.12}
    m = FastquantMetrics(
        engine="fastquant", engine_version="shim-v1", sharpe=0.78,
        total_return=0.115, annualised_pct=0.115, max_dd=-0.06,
        n_bars=100, n_trades=3, n_skipped=0, used_shim=True,
    )
    cv_record = {"framework": to_framework_cv(m)}
    # Should NOT raise — divergence is well under all rule thresholds.
    validate_framework_cv(inhouse, cv_record, strategy_name="smoke")


# ---------------------------------------------------------------------------
# Public API guardrails — bad inputs raise ``ValueError``.
# ---------------------------------------------------------------------------


def test_initial_capital_must_be_positive():
    bars = _flat_bars(10)
    with pytest.raises(ValueError, match="initial_capital"):
        run_fastquant_backtest(bars, trades=None, initial_capital=0.0)


def test_bars_must_have_close_column():
    bars = pd.DataFrame({"open": [100.0] * 10},
                        index=pd.date_range("2024-01-01", periods=10,
                                            freq="1h", tz="UTC"))
    with pytest.raises(ValueError, match="close"):
        run_fastquant_backtest(bars, trades=None)


def test_negative_commission_rejected():
    bars = _flat_bars(10)
    with pytest.raises(ValueError, match="commission"):
        run_fastquant_backtest(bars, trades=None, commission=-0.001)


def test_unknown_strategy_rejected():
    bars = _flat_bars(10)
    with pytest.raises(ValueError, match="strategy"):
        run_fastquant_backtest(bars, trades=None, strategy="not-a-strategy")


def test_force_shim_skips_real_fq_path():
    """force_shim=True should mark used_shim=True regardless of FASTQUANT_AVAILABLE."""
    bars = _flat_bars(20)
    _, metrics = run_fastquant_backtest(
        bars, trades=None, strategy="buynhold",
        force_shim=True,
    )
    assert metrics.used_shim is True


# ---------------------------------------------------------------------------
# Metrics helpers — internal sanity checks.
# ---------------------------------------------------------------------------


def test_compute_metrics_constant_series_is_zero_sharpe():
    """A constant equity series has zero per-bar std -> sharpe = 0."""
    eq = np.full(100, 100_000.0)
    sharpe, total_return, _ann, max_dd = _compute_metrics(eq)
    assert sharpe == 0.0
    assert total_return == 0.0
    assert max_dd == 0.0


def test_compute_metrics_monotone_growth():
    """A monotonically growing equity gives positive sharpe and zero max_dd."""
    eq = np.linspace(100_000.0, 110_000.0, 100)
    sharpe, total_return, _ann, max_dd = _compute_metrics(eq)
    assert sharpe > 0
    assert abs(total_return - 0.10) < 1e-9
    assert max_dd == 0.0


def test_compute_metrics_drawdown_negative():
    """An equity dip followed by recovery records a negative max_dd."""
    eq = np.array([100.0, 110.0, 90.0, 100.0])
    sharpe, total_return, _ann, max_dd = _compute_metrics(eq)
    # max DD occurs at bar 2: 90 / 110 - 1 = -0.1818...
    assert abs(max_dd - (-0.1818181818181818)) < 1e-9


def test_shim_replay_returns_initial_capital_length():
    """Shim output array length matches the bars frame."""
    bars = _flat_bars(50)
    sched: List = []
    eq, n_fills, n_skipped = _shim_replay(
        bars["close"].to_numpy(), bars.index, sched,
        initial_capital=100_000.0, commission=0.001,
    )
    assert len(eq) == 50
    assert n_fills == 0
    assert n_skipped == 0


# ---------------------------------------------------------------------------
# Smoke test — full adapter round-trip on synthetic data.
# ---------------------------------------------------------------------------


def test_smac_synthetic_round_trip_has_finite_metrics():
    """End-to-end smoke: synthetic data -> SMAC -> all metrics finite."""
    rng = np.random.default_rng(42)
    closes = 100.0 * np.cumprod(1.0 + rng.normal(0, 0.01, 200))
    bars = _make_bars(closes.tolist())
    eq, metrics = run_fastquant_backtest(
        bars, trades=None, strategy="smac",
        initial_capital=100_000.0, commission=0.001,
        fast_period=5, slow_period=20,
        freq_per_year=DEFAULT_FREQ_PER_YEAR,
    )
    for field in ("sharpe", "total_return", "annualised_pct", "max_dd"):
        v = getattr(metrics, field)
        assert math.isfinite(v), f"{field}={v} is not finite"
    assert metrics.used_shim is True  # no fastquant on CI
    assert metrics.engine == "fastquant"
    assert metrics.engine_version.startswith("shim")


# ---------------------------------------------------------------------------
# Cross-framework A/B — fastquant shim vs in-house per-bar engine on a
# known schedule. The shim and the in-house engine use DIFFERENT cost
# conventions (fastquant: 1 commission per fill on notional; in-house
# cost_mode="fill": half-commission at entry + half at exit on size, not
# notional) so we calibrate each side independently to its native
# convention and verify they AGREE to within the documented per-bar
# compounding residual. SMA-35404 evidence.
# ---------------------------------------------------------------------------


def _run_inhouse(bars, trades, initial_capital, cost_bps_rt):
    """Drive the authoritative in-house engine with cost_mode='fill'."""
    # Imported lazily so a missing run_backtest doesn't kill collection.
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
    RT), commission=0.001 on both sides. The trades are discrete windows
    — equity is FLAT (no per-bar return) outside each trade window, so
    the realistic final-equity range compounds only the held windows:

      bar 10-11: 111/110 - 1 = +0.909% gross - 0.2% cost ≈ +0.73%
      bar 30-35: 135/130 - 1 = +3.846% gross - 0.2% cost ≈ +3.65%
      bar 50-99: 199/150 - 1 = +32.667% gross - 0.2% cost ≈ +32.47%

    Final equity ≈ 100_000 * 1.0073 * 1.0365 * 1.3247 ≈ 138_300.

    The two engines use DIFFERENT cost conventions (fastquant: 1
    commission per fill on notional; in-house cost_mode='fill': half-
    commission at entry + half at exit on size, not notional) so we
    calibrate each side independently and verify they agree to within
    the documented compounding residual. SMA-35404 evidence.
    """
    from dataclasses import dataclass

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
    commission = 0.001

    # fastquant shim (target).
    eq_shim, m_shim = run_fastquant_backtest(
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
    # SMA-35404 cross-framework A/B evidence.
    gap = abs(shim_final - inhouse_final) / initial_capital
    assert gap < 0.02, (
        f"shim vs inhouse divergence {gap*100:.2f}% exceeds 2% threshold; "
        f"shim={shim_final:.2f} inhouse={inhouse_final:.2f}"
    )
    # Both should report finite, same-sign total_return.
    assert m_shim.total_return > 0.30
    assert m_shim.n_trades == 3
    assert m_shim.n_skipped == 0