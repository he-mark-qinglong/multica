"""Inter-strategy shared capital pool (I11).

One pool of capital is shared by several strategies. Each strategy has a
*target weight*; its target equity is ``weight × total_pool_equity``.
Because strategies PnL independently, actual equity drifts from target.
When the largest absolute deviation (as a fraction of pool equity)
exceeds ``drift_threshold``, capital is transferred between strategies
to restore targets — the pool-internal equivalent of rebalancing.

Design:

  * :func:`compute_transfers` — pure core. Given actual equities and
    target weights, returns the minimal set of transfers that restores
    every strategy to its target (greedy debtor→creditor matching, so at
    most ``n-1`` transfers for ``n`` strategies).
  * :class:`CapitalPool` — thin stateful wrapper: applies transfers,
    appends every movement to an immutable audit ledger
    (:class:`TransferRecord`), and handles strategies joining
    (:meth:`CapitalPool.add_strategy`) / leaving
    (:meth:`CapitalPool.remove_strategy`) with capital redistribution.

Weights are validated to be non-negative and sum to 1 (within 1e-9).
Transfers never create negative equity: a debtor never transfers more
than it holds (guaranteed by construction — its excess over target is
what it pays out).

References:
  - Grinold & Kahn (2000), "Active Portfolio Management", Ch. 14 —
    capital allocation across strategy sleeves to target weights.
  - Thorp (2006), "The Kelly Criterion in Blackjack, Sports Betting,
    and the Stock Market" — fixed-fraction rebalancing back to target
    allocations as the growth-optimal maintenance rule.
"""
from __future__ import annotations

import time as _time
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "CapitalPool",
    "PoolConfig",
    "Transfer",
    "TransferRecord",
    "compute_transfers",
]

_W_SUM_TOL = 1e-9


@dataclass(frozen=True)
class PoolConfig:
    """Pool-level configuration."""

    drift_threshold: float = 0.05
    """Max tolerated |actual - target| / pool_equity before rebalancing."""


@dataclass(frozen=True)
class Transfer:
    """One planned capital movement between two strategies."""

    src: str
    dst: str
    amount: float


@dataclass(frozen=True)
class TransferRecord:
    """Audit-ledger entry for an applied transfer (or join/leave flow)."""

    ts: float
    src: str          # strategy name, or "EXTERNAL" for join/leave flows
    dst: str
    amount: float
    reason: str       # "rebalance" | "join" | "leave"
    pool_equity_after: float


def _validate_weights(weights: Mapping[str, float]) -> None:
    for name, w in weights.items():
        if w < 0:
            raise ValueError(f"negative target weight for {name!r}: {w}")
    if abs(sum(weights.values()) - 1.0) > _W_SUM_TOL:
        raise ValueError(f"target weights must sum to 1, got {sum(weights.values())}")


def compute_transfers(
    equities: Mapping[str, float],
    target_weights: Mapping[str, float],
) -> tuple[Transfer, ...]:
    """Minimal transfers restoring every strategy to its target equity.

    Pure. Debtors (above target) pay creditors (below target) in
    descending size order; total paid out equals total shortfall, so the
    plan is exact and uses at most ``n - 1`` transfers.
    """
    if set(equities) != set(target_weights):
        raise ValueError("equities and target_weights must cover the same strategies")
    _validate_weights(target_weights)
    total = sum(equities.values())
    deltas = {
        name: target_weights[name] * total - eq for name, eq in equities.items()
    }
    # Round dust below 1e-9 of pool to zero to avoid degenerate transfers.
    dust = max(total * 1e-9, 1e-12)
    creditors = sorted(
        ((d, n) for n, d in deltas.items() if d > dust), reverse=True
    )
    debtors = sorted(
        ((-d, n) for n, d in deltas.items() if d < -dust), reverse=True
    )
    transfers: list[Transfer] = []
    i = j = 0
    cred = list(creditors)  # (amount_needed, name)
    debt = list(debtors)    # (amount_available, name)
    while i < len(cred) and j < len(debt):
        amount = min(cred[i][0], debt[j][0])
        transfers.append(Transfer(src=debt[j][1], dst=cred[i][1], amount=amount))
        cred[i] = (cred[i][0] - amount, cred[i][1])
        debt[j] = (debt[j][0] - amount, debt[j][1])
        if cred[i][0] <= dust:
            i += 1
        if debt[j][0] <= dust:
            j += 1
    return tuple(transfers)


class CapitalPool:
    """Stateful shared pool: equities, target weights, audit ledger."""

    def __init__(
        self,
        target_weights: Mapping[str, float],
        equities: Mapping[str, float] | None = None,
        config: PoolConfig = PoolConfig(),
    ):
        _validate_weights(target_weights)
        if equities is None:
            if not target_weights:
                raise ValueError("empty pool needs explicit equities")
            # Convention: caller seeds via `equities`; without one we start
            # each strategy at its weight × 1 unit of capital.
            equities = {n: w for n, w in target_weights.items()}
        if set(equities) != set(target_weights):
            raise ValueError("equities and target_weights must cover the same strategies")
        if any(e < 0 for e in equities.values()):
            raise ValueError("equities must be non-negative")
        self._config = config
        self._targets: dict[str, float] = dict(target_weights)
        self._equities: dict[str, float] = dict(equities)
        self._ledger: list[TransferRecord] = []

    # ---------------------------------------------------------------- views
    @property
    def config(self) -> PoolConfig:
        return self._config

    @property
    def target_weights(self) -> dict[str, float]:
        return dict(self._targets)

    @property
    def equities(self) -> dict[str, float]:
        return dict(self._equities)

    @property
    def total_equity(self) -> float:
        return sum(self._equities.values())

    @property
    def ledger(self) -> tuple[TransferRecord, ...]:
        return tuple(self._ledger)

    def target_equity(self, name: str) -> float:
        return self._targets[name] * self.total_equity

    def drift(self, name: str) -> float:
        """(actual - target) / pool equity for one strategy."""
        total = self.total_equity
        if total <= 0:
            return 0.0
        return (self._equities[name] - self.target_equity(name)) / total

    def max_abs_drift(self) -> float:
        if not self._equities:
            return 0.0
        return max(abs(self.drift(n)) for n in self._equities)

    # ------------------------------------------------------------- mutators
    def apply_pnl(self, name: str, pnl: float) -> None:
        """Credit/debit a strategy's equity (no transfer, no ledger entry)."""
        if name not in self._equities:
            raise KeyError(f"unknown strategy {name!r}")
        new_eq = self._equities[name] + pnl
        if new_eq < 0:
            raise ValueError(f"PnL would take {name!r} equity negative")
        self._equities[name] = new_eq

    def rebalance(self, force: bool = False) -> tuple[TransferRecord, ...]:
        """Transfer capital back to targets if drift exceeds the threshold.

        Returns the ledger entries created (empty when drift is below
        ``config.drift_threshold`` and ``force`` is False).
        """
        if not force and self.max_abs_drift() <= self._config.drift_threshold:
            return ()
        records = []
        for t in compute_transfers(self._equities, self._targets):
            self._equities[t.src] -= t.amount
            self._equities[t.dst] += t.amount
            records.append(
                TransferRecord(
                    ts=_time.time(),
                    src=t.src,
                    dst=t.dst,
                    amount=t.amount,
                    reason="rebalance",
                    pool_equity_after=self.total_equity,
                )
            )
        self._ledger.extend(records)
        return tuple(records)

    def add_strategy(
        self,
        name: str,
        target_weight: float,
        initial_equity: float = 0.0,
        rescale: bool = True,
    ) -> tuple[TransferRecord, ...]:
        """Add a strategy to the pool.

        ``initial_equity`` external capital is injected (ledger reason
        ``join``). When ``rescale`` is True, existing target weights are
        scaled down by ``(1 - target_weight)`` so the new weight vector
        still sums to 1; the pool is then rebalanced to the new targets.
        """
        if name in self._targets:
            raise ValueError(f"strategy {name!r} already in pool")
        if not 0.0 <= target_weight <= 1.0:
            raise ValueError(f"target_weight out of range: {target_weight}")
        if initial_equity < 0:
            raise ValueError("initial_equity must be non-negative")
        if rescale:
            for n in self._targets:
                self._targets[n] *= 1.0 - target_weight
        self._targets[name] = target_weight
        _validate_weights(self._targets)
        self._equities[name] = initial_equity
        records = []
        if initial_equity > 0:
            records.append(
                TransferRecord(
                    ts=_time.time(),
                    src="EXTERNAL",
                    dst=name,
                    amount=initial_equity,
                    reason="join",
                    pool_equity_after=self.total_equity,
                )
            )
        self._ledger.extend(records)
        return tuple(records) + self.rebalance(force=True)

    def remove_strategy(self, name: str) -> tuple[TransferRecord, ...]:
        """Remove a strategy; its equity leaves the pool via EXTERNAL.

        Remaining target weights are renormalised to sum to 1 and the
        pool is rebalanced to the new targets.
        """
        if name not in self._targets:
            raise KeyError(f"unknown strategy {name!r}")
        freed = self._equities.pop(name)
        w = self._targets.pop(name)
        records = []
        if freed > 0:
            records.append(
                TransferRecord(
                    ts=_time.time(),
                    src=name,
                    dst="EXTERNAL",
                    amount=freed,
                    reason="leave",
                    pool_equity_after=self.total_equity,
                )
            )
        if self._targets:
            if w >= 1.0 - _W_SUM_TOL:
                # Sole member removed; nothing to redistribute to.
                self._targets = {}
            else:
                scale = 1.0 / (1.0 - w)
                for n in self._targets:
                    self._targets[n] *= scale
        self._ledger.extend(records)
        if not self._targets:
            return tuple(records)
        return tuple(records) + self.rebalance(force=True)
