"""Strategy lifecycle state machine (I16).

Every strategy moves through a fixed funnel::

    REGISTERED -> BACKTESTING -> PAPER -> LIVE -> DEGRADED -> RETIRED

(:data:`LEGAL_TRANSITIONS` is the full table; DEGRADED can recover to
LIVE, and any non-terminal state can RETIRE.) A transition is applied
only when (a) it is in the legal table and (b) its
:class:`TransitionRule` condition — a pluggable
``Callable[[StrategyMetrics], bool]`` — passes. The default rule for
PAPER -> LIVE requires paper-period Sharpe > 1 and max drawdown better
than -25%; all other transitions are unconditional by default. Rules
are overridable per manager, so a desk can tighten promotion criteria
without touching the state machine.

Every *attempted* transition — accepted or rejected — is appended as
one JSON line to the audit log: the audit trail is the point (a silent
refusal is not enforceable process). Public data objects are frozen;
only the manager mutates.

References:
  - Grinold & Kahn (2000), "Active Portfolio Management", Ch. 20
    (performance measurement gates between research and production).
  - Aronson (2006), "Evidence-Based Technical Analysis", Ch. 6
    (out-of-sample validation before committing capital — the
    BACKTESTING -> PAPER -> LIVE funnel).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple


class LifecycleState(str, Enum):
    REGISTERED = "registered"
    BACKTESTING = "backtesting"
    PAPER = "paper"
    LIVE = "live"
    DEGRADED = "degraded"
    RETIRED = "retired"


LEGAL_TRANSITIONS: Mapping[LifecycleState, frozenset] = {
    LifecycleState.REGISTERED: frozenset(
        {LifecycleState.BACKTESTING, LifecycleState.RETIRED}
    ),
    LifecycleState.BACKTESTING: frozenset(
        {LifecycleState.PAPER, LifecycleState.RETIRED}
    ),
    LifecycleState.PAPER: frozenset(
        {LifecycleState.LIVE, LifecycleState.RETIRED}
    ),
    LifecycleState.LIVE: frozenset(
        {LifecycleState.DEGRADED, LifecycleState.RETIRED}
    ),
    LifecycleState.DEGRADED: frozenset(
        {LifecycleState.LIVE, LifecycleState.RETIRED}
    ),
    LifecycleState.RETIRED: frozenset(),
}


@dataclass(frozen=True)
class StrategyMetrics:
    """Evidence bundle a transition rule evaluates.

    ``max_drawdown`` is a negative fraction (e.g. ``-0.20`` = -20%).
    ``None`` means "not measured" — conditions decide whether that
    passes (the defaults require actual numbers).
    """

    sharpe: float | None = None
    max_drawdown: float | None = None


@dataclass(frozen=True)
class TransitionRule:
    """Gate on one transition. ``condition=None`` = unconditional."""

    to_state: LifecycleState
    condition: Optional[Callable[[StrategyMetrics], bool]] = None
    description: str = ""


def _paper_to_live(m: StrategyMetrics) -> bool:
    return (
        m.sharpe is not None
        and m.sharpe > 1.0
        and m.max_drawdown is not None
        and m.max_drawdown > -0.25
    )


DEFAULT_RULES: Mapping[Tuple[LifecycleState, LifecycleState], TransitionRule] = {
    (LifecycleState.PAPER, LifecycleState.LIVE): TransitionRule(
        to_state=LifecycleState.LIVE,
        condition=_paper_to_live,
        description="paper Sharpe > 1 and max drawdown > -25%",
    ),
}


@dataclass(frozen=True)
class TransitionRecord:
    """Audit record of one attempted transition."""

    ts: float                        # unix epoch seconds
    strategy_id: str
    from_state: str
    to_state: str
    accepted: bool
    reason: str                      # "" when accepted


class LifecycleManager:
    """Tracks strategy states and enforces the transition table.

    Parameters
    ----------
    audit_path : path to a jsonl file; every attempted transition is
        appended. ``None`` keeps records in memory only.
    rules : extra/override rules keyed ``(from_state, to_state)``;
        merged over :data:`DEFAULT_RULES`.
    """

    def __init__(
        self,
        audit_path: str | Path | None = None,
        rules: Mapping[Tuple[LifecycleState, LifecycleState], TransitionRule]
        | None = None,
    ):
        self._rules: Dict[Tuple[LifecycleState, LifecycleState], TransitionRule] = (
            dict(DEFAULT_RULES)
        )
        if rules:
            self._rules.update(rules)
        self._states: Dict[str, LifecycleState] = {}
        self._records: List[TransitionRecord] = []
        self._audit_path = Path(audit_path) if audit_path is not None else None
        if self._audit_path is not None:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def records(self) -> List[TransitionRecord]:
        return list(self._records)

    def state(self, strategy_id: str) -> LifecycleState:
        if strategy_id not in self._states:
            raise KeyError(f"unknown strategy: {strategy_id!r}")
        return self._states[strategy_id]

    def register(self, strategy_id: str) -> None:
        """Enter a strategy into the funnel at REGISTERED."""
        if strategy_id in self._states:
            raise ValueError(f"strategy already registered: {strategy_id!r}")
        self._states[strategy_id] = LifecycleState.REGISTERED

    def transition(
        self,
        strategy_id: str,
        to_state: LifecycleState,
        metrics: StrategyMetrics = StrategyMetrics(),
        ts: float | None = None,
    ) -> TransitionRecord:
        """Attempt ``strategy_id`` -> ``to_state``.

        The attempt is always recorded (in memory and, if configured, in
        the jsonl audit log); the state changes only when the transition
        is legal and its rule condition passes.
        """
        if strategy_id not in self._states:
            raise KeyError(f"unknown strategy: {strategy_id!r}")
        from_state = self._states[strategy_id]
        ts = time.time() if ts is None else float(ts)

        accepted, reason = self._evaluate(from_state, to_state, metrics)
        record = TransitionRecord(
            ts=ts,
            strategy_id=strategy_id,
            from_state=from_state.value,
            to_state=to_state.value,
            accepted=accepted,
            reason=reason,
        )
        self._records.append(record)
        self._write_audit(record)
        if accepted:
            self._states[strategy_id] = to_state
        return record

    def _evaluate(
        self,
        from_state: LifecycleState,
        to_state: LifecycleState,
        metrics: StrategyMetrics,
    ) -> Tuple[bool, str]:
        """Legal-table check, then rule condition. Pure."""
        if to_state not in LEGAL_TRANSITIONS[from_state]:
            return False, (
                f"illegal transition: {from_state.value} -> {to_state.value}"
            )
        rule = self._rules.get((from_state, to_state))
        if rule is not None and rule.condition is not None:
            if not rule.condition(metrics):
                return False, (
                    f"condition failed: {rule.description or 'custom rule'}"
                )
        return True, ""

    def _write_audit(self, record: TransitionRecord) -> None:
        if self._audit_path is None:
            return
        line = json.dumps(asdict(record), sort_keys=True, default=str)
        with self._audit_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
