"""slippage_sqrt_p7exec_027 — Almgren (2003) square-root cost model.

A small, pure-Python component that:

* ingests a stream of execution fills as ``SlippageSqrtRequest``,
* computes the temporary market impact via the Almgren factorisation
  ``impact_bps = k * sigma * sqrt(Q / (V_per_s * T)) * 10000``,
* persists every estimate to a write-ahead journal,
* exposes a per-verdict classification (minimal / low / moderate /
  high / extreme) and aggregate counters per symbol and globally,
* can rehydrate from the journal on restart without losing
  estimates.
"""

from .exceptions import (
    InvalidRequestError,
    SlippageSqrtError,
    SlippageSqrtHalt,
    SlippageSqrtJournalReplayRequired,
    SlippageSqrtJournalWriteError,
)
from .journal import Checkpoint, EstimateJournalRow, SlippageSqrtJournal, SymbolAggregate, now_ms
from .kernel import (
    SlippageSqrtCalculator,
    compute_slippage_sqrt,
)
from .models import (
    DEFAULT_ARRIVAL_HORIZON_S,
    DEFAULT_CHECKPOINT_EVERY,
    DEFAULT_K_FACTOR,
    DEFAULT_SECONDS_PER_DAY,
    KERNEL_VERSION,
    VERDICTS_ALL,
    VERDICT_EXTREME,
    VERDICT_HIGH,
    VERDICT_LOW,
    VERDICT_MINIMAL,
    VERDICT_MODERATE,
    VERDICT_THRESHOLD_HIGH,
    VERDICT_THRESHOLD_LOW,
    VERDICT_THRESHOLD_MINIMAL,
    VERDICT_THRESHOLD_MODERATE,
    SlippageSqrtEstimate,
    SlippageSqrtRequest,
)
from .runner import ExecutionRunner, build_runner

__all__ = [
    # Version + defaults
    "KERNEL_VERSION",
    "DEFAULT_CHECKPOINT_EVERY",
    "DEFAULT_SECONDS_PER_DAY",
    "DEFAULT_ARRIVAL_HORIZON_S",
    "DEFAULT_K_FACTOR",
    # Verdict constants
    "VERDICT_MINIMAL",
    "VERDICT_LOW",
    "VERDICT_MODERATE",
    "VERDICT_HIGH",
    "VERDICT_EXTREME",
    "VERDICTS_ALL",
    # Verdict thresholds
    "VERDICT_THRESHOLD_MINIMAL",
    "VERDICT_THRESHOLD_LOW",
    "VERDICT_THRESHOLD_MODERATE",
    "VERDICT_THRESHOLD_HIGH",
    # Dataclasses
    "SlippageSqrtRequest",
    "SlippageSqrtEstimate",
    # Exceptions
    "SlippageSqrtError",
    "InvalidRequestError",
    "SlippageSqrtJournalWriteError",
    "SlippageSqrtJournalReplayRequired",
    "SlippageSqrtHalt",
    # Journal primitives
    "Checkpoint",
    "EstimateJournalRow",
    "SymbolAggregate",
    "SlippageSqrtJournal",
    "now_ms",
    # Kernel
    "compute_slippage_sqrt",
    "SlippageSqrtCalculator",
    # Runtime
    "ExecutionRunner",
    "build_runner",
]

__version__ = KERNEL_VERSION