"""Top-level live-trading execution runner (P7-EXEC-027 wire-up).

This module is the canonical wire-up point for components under
``~/multica/quant-loop/execution/``. It owns one :class:`LiveExecutionRunner`
that runs on top of the slippage_sqrt calculator from
``slippage_sqrt_p7exec_027/`` (P7-EXEC-027 — the component this issue ships).

The wider wire-up (anomaly detector, attribution backend, scorer, etc.)
lives on the ``agent/multica-code/sma-35145-per-bar-compounding`` branch;
copying it onto this branch would drag in seven sibling components that
are not in scope for P7-EXEC-027. This module keeps the integration
scope to the slippage_sqrt calculator only — the broader sibling
wire-up is reproducible from the canonical branch by re-running the
full integration smoke once it lands.

For P7-EXEC-027 the runner exposes:

* :meth:`LiveExecutionRunner.ingest_slippage_sqrt_request` — feed one
  fully-formed :class:`SlippageSqrtRequest` into the calculator; the
  journal is fsynced before the estimate returns.
* :meth:`LiveExecutionRunner.ingest_fill` — placeholder fan-out
  surface. The slippage_sqrt component is **not** wired through this
  path: the kernel needs ``daily_volume`` and ``volatility_per_s``,
  which a plain ``FillRecord`` does not carry. The method returns an
  empty dict so callers that fan out across multiple components still
  see the slippage_sqrt key absent rather than fabricated.
* :meth:`LiveExecutionRunner.slippage_sqrt_stats` — snapshot of the
  cost-model counters.
* :meth:`LiveExecutionRunner.stats` — global snapshot.
* :meth:`LiveExecutionRunner.shutdown` — close the journal cleanly.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Mapping

from slippage_sqrt_p7exec_027 import (
    SlippageSqrtEstimate,
    SlippageSqrtRequest,
)
from slippage_sqrt_p7exec_027.runner import (
    ExecutionRunner as SlippageSqrtRunner,
)


class LiveExecutionRunner:
    """Owns the slippage_sqrt cost-model calculator for one live session.

    Parameters
    ----------
    journal_dir:
        Directory in which the slippage_sqrt WAL lives. A
        ``slippage_sqrt_journal/`` sub-directory is created under it.
    """

    def __init__(self, journal_dir: Path) -> None:
        self._journal_dir = Path(journal_dir)
        self._slippage_sqrt_journal_dir = (
            self._journal_dir / "slippage_sqrt_journal"
        )
        self.slippage_sqrt_runner = SlippageSqrtRunner(
            journal_dir=self._slippage_sqrt_journal_dir,
        )
        self._closed = False
        self._lock = threading.Lock()

    def ingest_slippage_sqrt_request(
        self, req: SlippageSqrtRequest
    ) -> SlippageSqrtEstimate:
        """Feed one Almgren-sqrt request into the calculator.

        The journal is fsynced before the estimate returns. Callers
        that have only ``FillRecord`` data should look at the per-fill
        attribution breakdown instead — this calculator needs the full
        market context.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("LiveExecutionRunner is shut down")
            return self.slippage_sqrt_runner.estimate(req)

    def ingest_fill(self, fill) -> dict:
        """Placeholder fan-out for plain ``FillRecord`` inputs.

        Returns an empty dict. The slippage_sqrt component is
        intentionally NOT wired into this path because the Almgren
        kernel requires ``daily_volume`` and ``volatility_per_s``,
        which a plain ``FillRecord`` does not carry — fabricating
        defaults would silently corrupt the impact estimate. Callers
        that have the full market context should call
        :meth:`ingest_slippage_sqrt_request` directly.
        """
        # ``fill`` is intentionally typed loosely (annotation ``object``)
        # so this method can be called from sibling-component paths
        # that define their own FillRecord dataclass; we never read it.
        with self._lock:
            if self._closed:
                raise RuntimeError("LiveExecutionRunner is shut down")
        return {}

    def slippage_sqrt_stats(self) -> Mapping[str, Any]:
        """Snapshot of the slippage_sqrt cost model's running counters."""
        with self._lock:
            if self._closed:
                return {}
            return {"slippage_sqrt": self.slippage_sqrt_runner.stats()}

    def stats(self) -> Mapping[str, Mapping[str, Any]]:
        """Global snapshot — currently just the slippage_sqrt component."""
        with self._lock:
            if self._closed:
                return {}
            return {"slippage_sqrt": self.slippage_sqrt_runner.stats()}

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.slippage_sqrt_runner.shutdown()

    # ----------------------------------------------------------------- ctxmgr
    def __enter__(self) -> "LiveExecutionRunner":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()
