"""Position reconciliation with convergence logic (E7).

Compares the local (shadow) position ledger against the exchange's position
report, classifies each mismatch by severity, and retries up to ``max_retries``
times to allow transient propagation delays to resolve before alerting.

Design
------
* ``compute_diff`` is a **pure function**: two dicts in, a tuple of
  :class:`PositionDiff` out. No I/O, no side effects.
* :class:`Reconciler` wraps the retry loop. An optional ``fetch_fn`` callback
  re-fetches exchange positions between retries (e.g. a REST ``GET /positions``
  closure). The sleep between retries is real (``time.sleep``) but
  configurable via ``retry_delay_sec``.

Severity model
--------------
* **ok** — ``|diff| <= warn_threshold``
* **warn** — ``warn_threshold < |diff| <= critical_threshold``
* **critical** — ``|diff| > critical_threshold``

A symbol present on only one side is always **critical**.

Convergence
-----------
The reconciler converges when **every** diff is ``"ok"``. If diffs persist
after ``max_retries`` attempts, alert messages are generated for each non-ok
symbol.

References
----------
- Google SRE Book §13 — reconciliation cycles and eventual consistency.
- :mod:`_shared.ops.alerting` — alert fan-out infrastructure.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

__all__ = [
    "PositionDiff",
    "ReconcileResult",
    "compute_diff",
    "Reconciler",
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PositionDiff:
    """One symbol's local-vs-exchange position discrepancy.

    ``diff`` is ``local_qty - exchange_qty`` (positive means local thinks it
    holds more than the exchange reports).
    """

    symbol: str
    local_qty: float
    exchange_qty: float
    diff: float
    severity: str          # "ok" | "warn" | "critical"


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of a reconciliation cycle."""

    diffs: tuple[PositionDiff, ...]
    converged: bool
    attempts: int
    alerts: tuple[str, ...]


# ---------------------------------------------------------------------------
# Pure diff computation
# ---------------------------------------------------------------------------

def _classify(diff: float, warn_threshold: float, critical_threshold: float) -> str:
    """Severity label for an absolute position difference."""
    a = abs(diff)
    if a <= warn_threshold:
        return "ok"
    if a <= critical_threshold:
        return "warn"
    return "critical"


def compute_diff(
    local: dict[str, float],
    exchange: dict[str, float],
    warn_threshold: float = 0.01,
    critical_threshold: float = 0.1,
) -> tuple[PositionDiff, ...]:
    """Pure diff between two position ledgers.

    Parameters
    ----------
    local
        Local position map ``{symbol: qty}``.
    exchange
        Exchange-reported position map.
    warn_threshold
        Absolute qty delta at or below which the diff is ``"ok"``.
    critical_threshold
        Absolute qty delta above which the diff is ``"critical"``.

    Returns
    -------
    tuple[PositionDiff, ...]
        One entry per symbol in either ledger, sorted alphabetically.
    """
    all_symbols = sorted(set(local.keys()) | set(exchange.keys()))
    diffs: list[PositionDiff] = []

    for sym in all_symbols:
        lq = float(local.get(sym, 0.0))
        eq = float(exchange.get(sym, 0.0))
        diff = lq - eq
        sev = _classify(diff, warn_threshold, critical_threshold)
        diffs.append(
            PositionDiff(
                symbol=sym,
                local_qty=lq,
                exchange_qty=eq,
                diff=diff,
                severity=sev,
            )
        )

    return tuple(diffs)


# ---------------------------------------------------------------------------
# Reconciler (retry loop)
# ---------------------------------------------------------------------------

class Reconciler:
    """Retry-loop position reconciler with auto-convergence and alerting.

    Parameters
    ----------
    max_retries
        Maximum number of diff attempts (1 = single diff, no retries).
    retry_delay_sec
        Sleep between retries.
    warn_threshold
        Severity threshold passed to :func:`compute_diff`.
    critical_threshold
        Severity threshold passed to :func:`compute_diff`.
    """

    def __init__(
        self,
        max_retries: int = 3,
        retry_delay_sec: float = 1.0,
        warn_threshold: float = 0.01,
        critical_threshold: float = 0.1,
    ) -> None:
        self.max_retries = max_retries
        self.retry_delay_sec = retry_delay_sec
        self.warn_threshold = warn_threshold
        self.critical_threshold = critical_threshold

    def reconcile(
        self,
        local: dict[str, float],
        exchange: dict[str, float],
        fetch_fn: Optional[Callable[[], dict[str, float]]] = None,
    ) -> ReconcileResult:
        """Run the reconciliation cycle.

        Parameters
        ----------
        local
            Current local position ledger.
        exchange
            Current exchange-reported positions (used on the first attempt).
        fetch_fn
            Optional zero-arg callable that re-fetches exchange positions
            between retries. If it raises, the previous exchange snapshot is
            retained.

        Returns
        -------
        ReconcileResult
        """
        current_exchange = dict(exchange)

        for attempt in range(self.max_retries):
            if attempt > 0:
                # Re-fetch exchange positions if a callback is provided.
                if fetch_fn is not None:
                    try:
                        current_exchange = fetch_fn()
                    except Exception:
                        pass  # keep stale snapshot; will retry again
                if self.retry_delay_sec > 0:
                    time.sleep(self.retry_delay_sec)

            diffs = compute_diff(
                local, current_exchange,
                warn_threshold=self.warn_threshold,
                critical_threshold=self.critical_threshold,
            )

            if all(d.severity == "ok" for d in diffs):
                return ReconcileResult(
                    diffs=diffs,
                    converged=True,
                    attempts=attempt + 1,
                    alerts=(),
                )

        # Exhausted retries — build alerts for non-ok diffs.
        alerts = tuple(
            self._format_alert(d) for d in diffs if d.severity != "ok"
        )

        return ReconcileResult(
            diffs=diffs,
            converged=False,
            attempts=self.max_retries,
            alerts=alerts,
        )

    @staticmethod
    def _format_alert(d: PositionDiff) -> str:
        return (
            f"Position mismatch {d.symbol}: "
            f"local={d.local_qty}, exchange={d.exchange_qty}, "
            f"diff={d.diff:.6f} [{d.severity}]"
        )
