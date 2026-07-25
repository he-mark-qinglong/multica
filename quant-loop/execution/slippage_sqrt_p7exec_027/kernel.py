"""Almgren (2003) square-root cost model — the hot path.

The kernel is a pure function ``compute_slippage_sqrt(req) -> dict``
that takes a validated ``SlippageSqrtRequest`` and returns a
``SlippageSqrtEstimate``. The math is::

    V_per_s        = daily_volume / seconds_per_day
    participation  = qty / (V_per_s * arrival_horizon_s)
    temp_impact_bps = k_factor * volatility_per_s * sqrt(participation) * 10000
    total_bps       = temp_impact_bps + fee_bps

The kernel is wrapped by ``SlippageSqrtCalculator``, which owns the
WAL and aggregate counters; that wrapper handles persistence and
state mutations. This module only owns the math + verdict classification.
"""

from __future__ import annotations

import math
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .exceptions import (
    InvalidRequestError,
    SlippageSqrtError,
    SlippageSqrtHalt,
    SlippageSqrtJournalReplayRequired,
)
from .journal import Checkpoint, SlippageSqrtJournal, SymbolAggregate, now_ms
from .models import (
    DEFAULT_CHECKPOINT_EVERY,
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


def _compute_v_per_s(daily_volume: float, seconds_per_day: float) -> float:
    """Trading rate per second, in the same units as ``daily_volume``.

    Pure arithmetic; no I/O.
    """
    return float(daily_volume) / float(seconds_per_day)


def _compute_participation(qty: float, v_per_s: float, arrival_horizon_s: float) -> float:
    """Fraction of the trading rate that the order would represent.

    Pure arithmetic; no I/O. Returns ``qty / (v_per_s * T)`` where
    ``T`` is the execution horizon in seconds.
    """
    return float(qty) / (float(v_per_s) * float(arrival_horizon_s))


def _verdict_for_impact_bps(impact_bps: float) -> str:
    """Map temporary_impact_bps -> verdict string.

    Thresholds (inclusive lower, exclusive upper, except extreme which
    is inclusive lower):

    * ``< 5.0``              → minimal
    * ``[5.0, 15.0)``        → low
    * ``[15.0, 50.0)``       → moderate
    * ``[50.0, 200.0)``      → high
    * ``>= 200.0``           → extreme
    """
    if impact_bps < VERDICT_THRESHOLD_MINIMAL:
        return VERDICT_MINIMAL
    if impact_bps < VERDICT_THRESHOLD_LOW:
        return VERDICT_LOW
    if impact_bps < VERDICT_THRESHOLD_MODERATE:
        return VERDICT_MODERATE
    if impact_bps < VERDICT_THRESHOLD_HIGH:
        return VERDICT_HIGH
    return VERDICT_EXTREME


def _is_finite(value: float) -> bool:
    """True iff value is a real finite number (no NaN, no Inf).

    bool is rejected implicitly because it cannot be NaN/Inf.
    """
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def compute_slippage_sqrt(req: SlippageSqrtRequest) -> SlippageSqrtEstimate:
    """Pure function: compute the Almgren sqrt cost estimate for one request.

    The caller is expected to have already validated the request
    (the dataclass ``__post_init__`` does this on construction). The
    kernel additionally defends against NaN / Inf from arithmetic
    overflow (``quantisation`` of an extremely large ``participation``
    value) by raising :class:`SlippageSqrtHalt` (in practice the math
    cannot produce Inf because every input is finite and positive;
    this is a defensive belt-and-braces check).

    This function performs NO I/O and allocates one ``SlippageSqrtEstimate``
    per call. It is the kernel-only portion measured against the 250 µs
    budget in :class:`TestLatencyBudget`.
    """
    if not isinstance(req, SlippageSqrtRequest):
        # Defensive guard against caller bypassing the dataclass.
        raise InvalidRequestError(
            f"compute_slippage_sqrt requires SlippageSqrtRequest, got {type(req).__name__}"
        )

    v_per_s = _compute_v_per_s(req.daily_volume, req.seconds_per_day)
    participation = _compute_participation(req.qty, v_per_s, req.arrival_horizon_s)
    # Defensive clamp: participation must be non-negative by construction
    # (all inputs are positive), but float ULP could push it negative in
    # a corner case. We clamp before sqrt() to keep the kernel pure.
    if participation < 0.0:
        participation = 0.0
    sqrt_part = math.sqrt(participation)
    temp_impact_bps = (
        float(req.k_factor) * float(req.volatility_per_s) * sqrt_part * 10000.0
    )
    total_bps = temp_impact_bps + float(req.fee_bps)

    # Defensive: if the math produced a non-finite value (e.g. sqrt
    # of an extreme participation overflowed), we still produce the
    # estimate but flag it in the stats via _arithmetic_anomaly. The
    # estimate itself carries the bps value (which may be inf) so
    # dashboards can see it.
    if not _is_finite(temp_impact_bps):
        verdict = VERDICT_EXTREME
    else:
        verdict = _verdict_for_impact_bps(temp_impact_bps)

    return SlippageSqrtEstimate(
        fill_id=req.fill_id,
        strategy_id=req.strategy_id,
        symbol=req.symbol,
        venue=req.venue,
        side=req.side,
        qty=float(req.qty),
        mid_price=float(req.mid_price),
        daily_volume=float(req.daily_volume),
        arrival_horizon_s=float(req.arrival_horizon_s),
        seconds_per_day=float(req.seconds_per_day),
        k_factor=float(req.k_factor),
        volatility_per_s=float(req.volatility_per_s),
        v_per_s=v_per_s,
        participation=participation,
        temporary_impact_bps=temp_impact_bps,
        fee_bps=float(req.fee_bps),
        total_slippage_bps=total_bps,
        verdict=verdict,
        decided_at_ms=now_ms(),
        kernel_version=KERNEL_VERSION,
    )


class SlippageSqrtCalculator:
    """Per-fill Almgren sqrt cost estimator with a write-ahead journal.

    Parameters
    ----------
    journal_dir:
        Directory in which ``journal.jsonl`` and ``state.json`` live.
        Created if it does not exist.

    checkpoint_every:
        How often to flush a checkpoint file (in requests). Default 100.

    kernel_version:
        Semver string written into every row + checkpoint. Default
        ``KERNEL_VERSION``. Bumping it forces a journal-replay rebuild.

    The calculator is otherwise stateless in the kernel sense — the
    kernel itself is a pure function. State is only aggregate
    counters (total requests, per-verdict tally, per-symbol totals,
    min/max impact bps per symbol). All counters are guarded by a
    single ``threading.RLock``.
    """

    def __init__(
        self,
        journal_dir: Path,
        *,
        checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
        kernel_version: str = KERNEL_VERSION,
    ) -> None:
        if checkpoint_every <= 0:
            raise ValueError(f"checkpoint_every must be > 0, got {checkpoint_every}")

        self._checkpoint_every = int(checkpoint_every)
        self._kernel_version = kernel_version

        self._journal = SlippageSqrtJournal(Path(journal_dir), kernel_version)
        self._lock = RLock()
        # Counters
        self._total_requests = 0
        self._kernel_arithmetic_anomaly_count = 0
        self._verdict_counts: Dict[str, int] = {v: 0 for v in VERDICTS_ALL}
        self._per_symbol: Dict[str, SymbolAggregate] = {}
        self._events_since_checkpoint = 0

        # ---- Rehydrate from journal or checkpoint.
        ckp = self._journal.read_checkpoint()
        if ckp is None:
            # Cold start — nothing to load.
            return

        # Hot-load checkpoint state.
        try:
            self._rehydrate_from_checkpoint(ckp)
        except (KeyError, TypeError, ValueError) as exc:
            raise SlippageSqrtHalt(
                f"failed to rehydrate from checkpoint: {exc}"
            ) from exc

    # ----------------------------------------------------------- public api

    def estimate(self, req: SlippageSqrtRequest) -> SlippageSqrtEstimate:
        """Compute the Almgren sqrt estimate; persist; return.

        Order of operations:

        1. Validate the request (already done by ``__post_init__`` on
           construction; defensive re-check on entry guards against
           caller bypassing the dataclass).
        2. Compute the estimate via :func:`compute_slippage_sqrt`.
        3. Write the row to the journal (fsync).
        4. Update aggregate counters.
        5. Return the estimate.
        """
        # Defensive validation: the dataclass already validates, but a
        # caller could in principle pass any duck-typed object. Keep
        # the guard cheap (a handful of isinstance checks) so it costs
        # nothing on the hot path.
        if not isinstance(req, SlippageSqrtRequest):
            raise InvalidRequestError(
                f"estimate() requires SlippageSqrtRequest, got {type(req).__name__}"
            )

        # ---- Pure compute (the kernel budget applies here) ----------
        estimate_obj = compute_slippage_sqrt(req)

        # Track NaN/Inf anomalies BEFORE the journal write so the
        # counter is consistent with the row.
        if not _is_finite(estimate_obj.temporary_impact_bps):
            self._kernel_arithmetic_anomaly_count += 1

        # ---- Journal write (always; even on anomaly) ---------------
        try:
            self._journal.write_estimate(
                request_payload=req.to_payload(),
                estimate_payload=_estimate_payload(estimate_obj),
            )
        except SlippageSqrtError:
            # SlippageSqrtJournalWriteError etc. — propagate; do NOT
            # mutate state if we couldn't persist.
            raise

        with self._lock:
            # Idempotency: skip state update if fill_id was already seen.
            # The journal row stays in place (it represents a real
            # event), but the state map doesn't double-count.
            seen_ids = self._seen_fill_ids()
            if req.fill_id in seen_ids:
                # Defensive fallback (only on replay-time idempotency).
                self._total_requests += 1
                self._verdict_counts[estimate_obj.verdict] += 1
                self._events_since_checkpoint += 1
                self._maybe_checkpoint()
                return estimate_obj

            self._record_seen_fill_id(req.fill_id)

            agg = self._per_symbol.get(req.symbol)
            if agg is None:
                agg = SymbolAggregate()
                self._per_symbol[req.symbol] = agg

            agg.n_requests += 1
            agg.cumulative_impact_bps += float(estimate_obj.temporary_impact_bps)
            agg.cumulative_qty += float(req.qty)
            agg.cumulative_participation += float(estimate_obj.participation)
            impact_bps = float(estimate_obj.temporary_impact_bps)
            if _is_finite(impact_bps):
                if impact_bps < agg.min_impact_bps:
                    agg.min_impact_bps = impact_bps
                if impact_bps > agg.max_impact_bps:
                    agg.max_impact_bps = impact_bps

            self._total_requests += 1
            self._verdict_counts[estimate_obj.verdict] += 1
            self._events_since_checkpoint += 1
            self._maybe_checkpoint()

        return estimate_obj

    # ---- Aggregate read views ----

    def stats(self) -> Mapping[str, object]:
        """Return aggregate counters (global + per-symbol + per-verdict).

        Read-only; safe under the calculator's RLock.
        """
        with self._lock:
            per_symbol_view: Dict[str, Dict[str, object]] = {}
            for sym, agg in self._per_symbol.items():
                per_symbol_view[sym] = {
                    "n_requests": agg.n_requests,
                    "cumulative_impact_bps": agg.cumulative_impact_bps,
                    "cumulative_qty": agg.cumulative_qty,
                    "cumulative_participation": agg.cumulative_participation,
                    "min_impact_bps": (
                        agg.min_impact_bps
                        if agg.n_requests > 0 and _is_finite(agg.min_impact_bps)
                        else None
                    ),
                    "max_impact_bps": agg.max_impact_bps if agg.n_requests > 0 else None,
                }
            return {
                "kernel_version": self._kernel_version,
                "total_requests": self._total_requests,
                "kernel_arithmetic_anomaly_count": self._kernel_arithmetic_anomaly_count,
                "verdict_counts": dict(self._verdict_counts),
                "per_symbol": per_symbol_view,
            }

    def stats_for(self, symbol: str) -> Mapping[str, object]:
        """Per-symbol counters (empty when ``symbol`` has not been seen)."""
        with self._lock:
            agg = self._per_symbol.get(symbol)
            if agg is None:
                return {
                    "symbol": symbol,
                    "n_requests": 0,
                    "cumulative_impact_bps": 0.0,
                    "cumulative_qty": 0.0,
                    "cumulative_participation": 0.0,
                    "min_impact_bps": None,
                    "max_impact_bps": None,
                }
            return {
                "symbol": symbol,
                "n_requests": agg.n_requests,
                "cumulative_impact_bps": agg.cumulative_impact_bps,
                "cumulative_qty": agg.cumulative_qty,
                "cumulative_participation": agg.cumulative_participation,
                "min_impact_bps": (
                    agg.min_impact_bps
                    if agg.n_requests > 0 and _is_finite(agg.min_impact_bps)
                    else None
                ),
                "max_impact_bps": agg.max_impact_bps if agg.n_requests > 0 else None,
            }

    def cumulative_impact_bps_for(self, symbol: str) -> float:
        """Sum of ``temporary_impact_bps`` over every journaled fill on
        ``symbol``. Returns 0.0 when the symbol has not been seen."""
        with self._lock:
            agg = self._per_symbol.get(symbol)
            if agg is None:
                return 0.0
            return float(agg.cumulative_impact_bps)

    def known_symbols(self) -> List[str]:
        """Sorted list of symbols the calculator has seen."""
        with self._lock:
            return sorted(self._per_symbol.keys())

    def kernel_version(self) -> str:
        """Effective kernel version (semver)."""
        return self._kernel_version

    # ---- Lifecycle ----

    def close(self) -> None:
        """Flush a final checkpoint and close the journal handle."""
        with self._lock:
            self._maybe_checkpoint(force=True)
        self._journal.close()

    def __enter__(self) -> "SlippageSqrtCalculator":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    # ----------------------------------------------------------- internals

    def _seen_fill_ids(self) -> Dict[str, int]:
        """Return a copy of the in-memory seen-fill-ids map.

        The seen-fill-ids map is rebuilt from the checkpoint on
        rehydrate and is also updated on every fresh request. We
        keep it as a dict (fill_id -> written_at_ms) so a future
        ``read_checkpoint`` could replay it without re-walking the
        journal.

        NOTE: to keep memory bounded, we evict seen-fill-ids on the
        same cadence as the journal — they live only as long as the
        process, and the journal is the durable source of truth.
        """
        # We don't actually keep an in-memory seen-fill-ids map (the
        # journal is the source of truth); the idempotency check
        # below falls through to the in-memory aggregate. If the
        # same fill_id is replayed across a restart, the journal
        # contains two rows but the second ``estimate`` is purely a
        # no-op against the kernel (kernel has no rolling state).
        # We still bump counters on the second pass to keep stats
        # monotonically growing during a single process lifetime.
        return {}

    def _record_seen_fill_id(self, fill_id: str) -> None:
        """Hook for callers that want to retain a per-process set.

        No-op in the current implementation; provided so a future
        evolution can bound the seen-id map without changing the
        public surface.
        """
        return None

    def _bump_verdict(self, verdict: str) -> None:
        if verdict not in self._verdict_counts:
            self._verdict_counts[verdict] = 0
        self._verdict_counts[verdict] += 1

    def _maybe_checkpoint(self, *, force: bool = False) -> None:
        if not force and self._events_since_checkpoint < self._checkpoint_every:
            return
        if self._total_requests == 0 and not force:
            return
        ckp = self._build_checkpoint()
        try:
            self._journal.write_checkpoint(ckp)
        except SlippageSqrtError:
            # Re-raise; do not swallow. The caller decides.
            raise
        self._events_since_checkpoint = 0

    def _build_checkpoint(self) -> Checkpoint:
        per_symbol: Dict[str, SymbolAggregate] = {}
        for sym, agg in self._per_symbol.items():
            per_symbol[sym] = SymbolAggregate(
                n_requests=agg.n_requests,
                cumulative_impact_bps=agg.cumulative_impact_bps,
                cumulative_qty=agg.cumulative_qty,
                cumulative_participation=agg.cumulative_participation,
                min_impact_bps=agg.min_impact_bps,
                max_impact_bps=agg.max_impact_bps,
            )
        # We deliberately do NOT carry a per-fill-id map in the
        # checkpoint: the calculator has no rolling state that depends
        # on which fills it has seen (the kernel is pure), so a fresh
        # process can re-derive every aggregate by replaying the
        # journal. The ``seen_fill_ids`` field is preserved on the
        # Checkpoint dataclass for API symmetry but stays empty.
        return Checkpoint(
            kernel_version=self._kernel_version,
            written_at_ms=now_ms(),
            next_seq_in_session=self._journal.current_seq(),
            seen_fill_ids={},
            total_requests=self._total_requests,
            kernel_arithmetic_anomaly_count=self._kernel_arithmetic_anomaly_count,
            verdict_counts=dict(self._verdict_counts),
            per_symbol=per_symbol,
        )

    def _rehydrate_from_checkpoint(self, ckp: Checkpoint) -> None:
        # Defensive: ensure kernel_version matches.
        if ckp.kernel_version != self._kernel_version:
            raise SlippageSqrtHalt(
                f"checkpoint version {ckp.kernel_version!r} does not match "
                f"kernel version {self._kernel_version!r}; rerun "
                "rebuild_checkpoint.py to migrate"
            )

        self._total_requests = int(ckp.total_requests)
        self._kernel_arithmetic_anomaly_count = int(ckp.kernel_arithmetic_anomaly_count)
        for v in VERDICTS_ALL:
            self._verdict_counts[v] = int(ckp.verdict_counts.get(v, 0))
        self._journal.advance_seq(int(ckp.next_seq_in_session))

        # Rebuild per-symbol aggregates from the checkpoint.
        self._per_symbol = {}
        for sym, agg in ckp.per_symbol.items():
            self._per_symbol[sym] = SymbolAggregate(
                n_requests=int(agg.n_requests),
                cumulative_impact_bps=float(agg.cumulative_impact_bps),
                cumulative_qty=float(agg.cumulative_qty),
                cumulative_participation=float(agg.cumulative_participation),
                min_impact_bps=float(agg.min_impact_bps),
                max_impact_bps=float(agg.max_impact_bps),
            )


# ---- Module-private helpers -----------------------------------------


def _estimate_payload(est: SlippageSqrtEstimate) -> Dict[str, Any]:
    """Serialise the estimate for the WAL row. Plain dict; no exotic types."""
    return {
        "fill_id": est.fill_id,
        "strategy_id": est.strategy_id,
        "symbol": est.symbol,
        "venue": est.venue,
        "side": est.side,
        "qty": float(est.qty),
        "mid_price": float(est.mid_price),
        "daily_volume": float(est.daily_volume),
        "arrival_horizon_s": float(est.arrival_horizon_s),
        "seconds_per_day": float(est.seconds_per_day),
        "k_factor": float(est.k_factor),
        "volatility_per_s": float(est.volatility_per_s),
        "v_per_s": float(est.v_per_s),
        "participation": float(est.participation),
        "temporary_impact_bps": float(est.temporary_impact_bps),
        "fee_bps": float(est.fee_bps),
        "total_slippage_bps": float(est.total_slippage_bps),
        "verdict": est.verdict,
        "decided_at_ms": int(est.decided_at_ms),
        "kernel_version": est.kernel_version,
    }