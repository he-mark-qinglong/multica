"""High-level orchestrator for slippage_sqrt_p7exec_027.

The runner owns one ``SlippageSqrtCalculator`` and exposes:

* ``estimate(req) -> SlippageSqrtEstimate`` — feed one
  ``SlippageSqrtRequest`` from the upstream fill source.
* ``stats()`` — return aggregate counters (global + per-symbol +
  per-verdict).
* ``shutdown()`` — flush a final checkpoint and close the journal.

The runner is intentionally minimal: it does not know about specific
venues, exchanges, or message buses. Higher-level orchestrators (e.g.
the live trading harness, the paper-trading harness) construct a
runner and call ``estimate`` from whatever the upstream fill source
is.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Mapping

from .kernel import SlippageSqrtCalculator
from .models import SlippageSqrtEstimate, SlippageSqrtRequest


class ExecutionRunner:
    """Top-level wrapper that owns the calculator and tally counters."""

    def __init__(
        self,
        journal_dir: Path,
        **calculator_kwargs,
    ) -> None:
        """Construct a runner.

        All keyword arguments beyond ``journal_dir`` are forwarded to
        ``SlippageSqrtCalculator.__init__``. See
        ``SlippageSqrtCalculator.__init__.__doc__`` for the full list.
        """
        self._calculator = SlippageSqrtCalculator(
            journal_dir=Path(journal_dir), **calculator_kwargs
        )
        self._lock = threading.Lock()  # protects counters only; calculator has its own
        self._closed = False

    def estimate(self, req: SlippageSqrtRequest) -> SlippageSqrtEstimate:
        """Feed one request into the calculator; return the estimate."""
        if self._closed:
            raise RuntimeError("ExecutionRunner is shut down")
        return self._calculator.estimate(req)

    def stats(self) -> Mapping[str, object]:
        """Return aggregate counters (global + per-symbol + per-verdict)."""
        with self._lock:
            if self._closed:
                return {}
            return self._calculator.stats()

    def stats_for(self, symbol: str) -> Mapping[str, object]:
        """Return per-symbol counters."""
        with self._lock:
            if self._closed:
                return {}
            return self._calculator.stats_for(symbol)

    def cumulative_impact_bps_for(self, symbol: str) -> float:
        """Return cumulative temporary_impact_bps for ``symbol``."""
        with self._lock:
            if self._closed:
                return 0.0
            return self._calculator.cumulative_impact_bps_for(symbol)

    def known_symbols(self) -> List[str]:
        """Sorted list of symbols the calculator has seen."""
        with self._lock:
            if self._closed:
                return []
            return self._calculator.known_symbols()

    def calculator(self) -> SlippageSqrtCalculator:
        """Return the underlying calculator for advanced queries."""
        return self._calculator

    def kernel_version(self) -> str:
        """Effective kernel version (semver)."""
        return self._calculator.kernel_version()

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._calculator.close()

    # ----------------------------------------------------------------- ctxmgr
    def __enter__(self) -> "ExecutionRunner":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()


def build_runner(journal_dir: Path, **calculator_kwargs) -> ExecutionRunner:
    """Convenience factory — wraps ``ExecutionRunner`` construction."""
    return ExecutionRunner(journal_dir=journal_dir, **calculator_kwargs)