"""Public dataclasses and constants for slippage_sqrt_p7exec_027.

The hot-path kernel consumes ``SlippageSqrtRequest`` and emits
``SlippageSqrtEstimate``. Both are frozen dataclasses so the
Almgren kernel receives immutable inputs and produces immutable
outputs.

See ``INTERFACE_CONTRACT.md`` for the per-field validation rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


# Kernel version is exposed as a module-level constant so callers can
# pin to it without importing the kernel module (avoids triggering any
# journal-construction side effects at import time).
KERNEL_VERSION = "0.1.0"

# --- Defaults (overridable at SlippageSqrtRequest construction) ------
DEFAULT_SECONDS_PER_DAY: float = 86400.0
DEFAULT_ARRIVAL_HORIZON_S: float = 1.0
DEFAULT_K_FACTOR: float = 1.0
DEFAULT_CHECKPOINT_EVERY: int = 100

# --- Verdict thresholds for temporary_impact_bps -> verdict -----------
# Adverse-positive bps ranges. The range boundaries are inclusive on
# the lower bound and exclusive on the upper bound, except the
# extreme bucket which is inclusive on the lower bound.
VERDICT_THRESHOLD_MINIMAL: float = 5.0
VERDICT_THRESHOLD_LOW: float = 15.0
VERDICT_THRESHOLD_MODERATE: float = 50.0
VERDICT_THRESHOLD_HIGH: float = 200.0

# --- Persistence filenames ------------------------------------------
JOURNAL_FILENAME: str = "journal.jsonl"
CHECKPOINT_FILENAME: str = "state.json"

# --- Verdict vocabulary ---------------------------------------------
VERDICT_MINIMAL: str = "minimal"
VERDICT_LOW: str = "low"
VERDICT_MODERATE: str = "moderate"
VERDICT_HIGH: str = "high"
VERDICT_EXTREME: str = "extreme"

VERDICTS_ALL = (
    VERDICT_MINIMAL,
    VERDICT_LOW,
    VERDICT_MODERATE,
    VERDICT_HIGH,
    VERDICT_EXTREME,
)


# --- Public dataclasses ---------------------------------------------


@dataclass(frozen=True)
class SlippageSqrtRequest:
    """One fill's request payload for the Almgren sqrt cost model.

    Validated by ``__post_init__``; invalid inputs raise
    ``InvalidRequestError`` (a ``ValueError``). Validation is strict
    on its own; the kernel never silently coerces.

    See ``SPEC.md`` §5 and ``INTERFACE_CONTRACT.md`` §1 for the
    per-field validation rules.
    """

    fill_id: str
    strategy_id: str
    symbol: str
    venue: str
    side: str
    qty: float
    daily_volume: float
    volatility_per_s: float
    mid_price: float = 0.0
    arrival_horizon_s: float = DEFAULT_ARRIVAL_HORIZON_S
    seconds_per_day: float = DEFAULT_SECONDS_PER_DAY
    k_factor: float = DEFAULT_K_FACTOR
    fee_bps: float = 0.0

    def __post_init__(self) -> None:
        # Lazy import to avoid circular dependency at module load time.
        from .exceptions import InvalidRequestError

        if not _is_nonempty_str(self.fill_id):
            raise InvalidRequestError(
                f"fill_id must be a non-empty string, got {self.fill_id!r}"
            )
        if not _is_nonempty_str(self.strategy_id):
            raise InvalidRequestError(
                f"strategy_id must be a non-empty string, got {self.strategy_id!r}"
            )
        if not _is_nonempty_str(self.symbol):
            raise InvalidRequestError(
                f"symbol must be a non-empty string, got {self.symbol!r}"
            )
        if not _is_nonempty_str(self.venue):
            raise InvalidRequestError(
                f"venue must be a non-empty string, got {self.venue!r}"
            )
        if self.side not in ("buy", "sell"):
            raise InvalidRequestError(
                f"side must be one of ('buy','sell'), got {self.side!r}"
            )
        if not _is_positive_number(self.qty):
            raise InvalidRequestError(
                f"qty must be > 0, got {self.qty!r}"
            )
        if not _is_nonnegative_number(self.mid_price):
            raise InvalidRequestError(
                f"mid_price must be >= 0, got {self.mid_price!r}"
            )
        if not _is_positive_number(self.daily_volume):
            raise InvalidRequestError(
                f"daily_volume must be > 0, got {self.daily_volume!r}"
            )
        if not _is_nonnegative_number(self.volatility_per_s):
            raise InvalidRequestError(
                f"volatility_per_s must be >= 0, got {self.volatility_per_s!r}"
            )
        if not _is_positive_number(self.arrival_horizon_s):
            raise InvalidRequestError(
                f"arrival_horizon_s must be > 0, got {self.arrival_horizon_s!r}"
            )
        if not _is_positive_number(self.seconds_per_day):
            raise InvalidRequestError(
                f"seconds_per_day must be > 0, got {self.seconds_per_day!r}"
            )
        if not _is_positive_number(self.k_factor):
            raise InvalidRequestError(
                f"k_factor must be > 0, got {self.k_factor!r}"
            )
        if not _is_nonnegative_number(self.fee_bps):
            raise InvalidRequestError(
                f"fee_bps must be >= 0, got {self.fee_bps!r}"
            )

    def to_payload(self) -> Dict[str, Any]:
        """Serialise to a plain dict for the WAL row.

        Returned as ``dict`` (not the frozen dataclass) so callers
        may mutate it for storage purposes. Used by the kernel when
        writing the journal row.
        """
        return {
            "fill_id": self.fill_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "venue": self.venue,
            "side": self.side,
            "qty": float(self.qty),
            "mid_price": float(self.mid_price),
            "daily_volume": float(self.daily_volume),
            "volatility_per_s": float(self.volatility_per_s),
            "arrival_horizon_s": float(self.arrival_horizon_s),
            "seconds_per_day": float(self.seconds_per_day),
            "k_factor": float(self.k_factor),
            "fee_bps": float(self.fee_bps),
        }


@dataclass(frozen=True)
class SlippageSqrtEstimate:
    """The kernel's per-fill output.

    All ``*_bps`` values are non-negative (adverse-positive). The
    ``verdict`` is one of the ``VERDICT_*`` constants and is keyed
    off ``temporary_impact_bps`` only — ``fee_bps`` is reported but
    does not influence verdict classification.
    """

    fill_id: str
    strategy_id: str
    symbol: str
    venue: str
    side: str
    qty: float
    mid_price: float
    daily_volume: float
    arrival_horizon_s: float
    seconds_per_day: float
    k_factor: float
    volatility_per_s: float
    v_per_s: float
    participation: float
    temporary_impact_bps: float
    fee_bps: float
    total_slippage_bps: float
    verdict: str
    decided_at_ms: int
    kernel_version: str


# --- Validation helpers (module-private) ---------------------------


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_positive_number(value: object) -> bool:
    # bool is a subclass of int; reject explicitly.
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float)) and value > 0


def _is_nonnegative_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float)) and value >= 0