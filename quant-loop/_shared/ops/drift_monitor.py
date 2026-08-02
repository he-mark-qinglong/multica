"""Live-vs-backtest drift monitor (H19).

Compares the live fill stream against the backtest-expected fill stream on
three axes: fill-price deviation (bp), fill-rate deviation, and cumulative
PnL deviation (bp of notional). Breaches surface as structured alerts so the
operator can kill or demote a strategy whose live behaviour has decoupled
from its backtest.

References:
- Bailey & López de Prado (2014), "The Deflated Sharpe Ratio" — backtest
  overfitting is why live/backtest divergence must be monitored, not assumed.
- Gama et al. (2014), "A Survey on Concept Drift Adaptation", ACM Computing
  Surveys — taxonomy of drift detection (we use fixed-threshold monitoring,
  the simplest member, appropriate for kill-switch gating).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

from _shared.ops.alerting import Alert, AlertLevel


@dataclass(frozen=True)
class Fill:
    """One fill observation (live or backtest-expected)."""

    price: float
    qty: float
    pnl: float = 0.0        # realised pnl attributable to this fill (quote ccy)


@dataclass(frozen=True)
class DriftThresholds:
    """Breach levels; any one breach makes the report not ok."""

    max_price_dev_bp: float = 5.0
    max_fill_rate_dev: float = 0.20      # |live - expected| / expected
    max_pnl_dev_bp: float = 50.0         # of expected notional
    min_fills: int = 1                   # need at least this many pairs


@dataclass(frozen=True)
class DriftReport:
    """Immutable drift measurement between matched fill streams."""

    n_live: int
    n_expected: int
    price_dev_bp: float        # qty-weighted mean signed live-vs-expected dev
    fill_rate_dev: float       # (n_live - n_expected) / n_expected
    pnl_dev_bp: float          # (live pnl - expected pnl) / expected notional
    breaches: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.breaches


# --- Pure metric functions ----------------------------------------------------
def fill_price_deviation_bp(
    live: Sequence[Fill],
    expected: Sequence[Fill],
) -> float:
    """Qty-weighted mean signed deviation of live vs expected fill prices (bp).

    Pairs are aligned by index over the first ``min(len)`` fills. Positive
    means live fills are systematically worse-priced than the backtest assumed
    (for buys) — the sign convention is raw (live - expected) / expected.
    """
    n = min(len(live), len(expected))
    if n == 0:
        return 0.0
    num = 0.0
    den = 0.0
    for lf, ef in zip(live[:n], expected[:n]):
        if ef.price == 0:
            continue
        w = abs(ef.qty)
        num += w * (lf.price - ef.price) / ef.price
        den += w
    if den == 0:
        return 0.0
    return num / den * 1e4


def fill_rate_deviation(n_live: int, n_expected: int) -> float:
    """Relative shortfall/surplus of live fills vs expected. 0 = on par."""
    if n_expected <= 0:
        return 0.0 if n_live == 0 else float("inf")
    return (n_live - n_expected) / n_expected


def cumulative_pnl_deviation_bp(
    live: Sequence[Fill],
    expected: Sequence[Fill],
) -> float:
    """(sum live pnl - sum expected pnl) as bp of expected traded notional."""
    notional = sum(abs(f.price * f.qty) for f in expected)
    if notional == 0:
        return 0.0
    live_pnl = sum(f.pnl for f in live)
    exp_pnl = sum(f.pnl for f in expected)
    return (live_pnl - exp_pnl) / notional * 1e4


# --- Report + alerts ----------------------------------------------------------
def compute_drift(
    live: Sequence[Fill],
    expected: Sequence[Fill],
    thresholds: DriftThresholds = DriftThresholds(),
) -> DriftReport:
    """Compute the full drift report and evaluate all thresholds. Pure."""
    n_live, n_exp = len(live), len(expected)
    price_bp = fill_price_deviation_bp(live, expected)
    rate_dev = fill_rate_deviation(n_live, n_exp)
    pnl_bp = cumulative_pnl_deviation_bp(live, expected)

    breaches = []
    if min(n_live, n_exp) >= thresholds.min_fills:
        if abs(price_bp) > thresholds.max_price_dev_bp:
            breaches.append(
                f"price_dev_bp {price_bp:+.2f} exceeds ±{thresholds.max_price_dev_bp:.2f}"
            )
        if abs(rate_dev) > thresholds.max_fill_rate_dev:
            breaches.append(
                f"fill_rate_dev {rate_dev:+.3f} exceeds ±{thresholds.max_fill_rate_dev:.3f}"
            )
        if abs(pnl_bp) > thresholds.max_pnl_dev_bp:
            breaches.append(
                f"pnl_dev_bp {pnl_bp:+.2f} exceeds ±{thresholds.max_pnl_dev_bp:.2f}"
            )
    return DriftReport(
        n_live=n_live,
        n_expected=n_exp,
        price_dev_bp=price_bp,
        fill_rate_dev=rate_dev,
        pnl_dev_bp=pnl_bp,
        breaches=tuple(breaches),
    )


def drift_alert(
    report: DriftReport,
    strategy: str = "strategy",
    now: Optional[float] = None,
) -> Optional[Alert]:
    """CRITICAL alert when the report breaches any threshold; else None."""
    if report.ok:
        return None
    return Alert(
        ts=time.time() if now is None else float(now),
        level=AlertLevel.CRITICAL.value,
        rule="live_backtest_drift",
        message=f"{strategy} live/backtest drift: {'; '.join(report.breaches)}",
        context={
            "strategy": strategy,
            "n_live": report.n_live,
            "n_expected": report.n_expected,
            "price_dev_bp": report.price_dev_bp,
            "fill_rate_dev": report.fill_rate_dev,
            "pnl_dev_bp": report.pnl_dev_bp,
            "breaches": list(report.breaches),
        },
    )
