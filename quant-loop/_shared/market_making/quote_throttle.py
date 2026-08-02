"""Quote throttle — exchange rate-limit guard for market-making loops.

Prevents a quoting loop from tripping venue order-rate limits by enforcing
three independent constraints before every quote / amend:

  1. **Minimum interval** — hard floor between consecutive quote updates.
  2. **Per-minute amend budget** — rolling 60 s cap on quote updates
     (mirrors Binance Futures' order-count-per-interval limit).
  3. **Burst circuit breaker** — after N updates inside a short burst
     window the state machine latches into COOLDOWN for M seconds,
     guaranteeing the venue sees a quiet period before quoting resumes.

The state machine is a pair of pure functions over immutable state:
``should_quote(now, state, params) -> ThrottleDecision`` both answers the
boolean question and returns the *next* state (quote recorded, cooldown
latched if the burst limit was hit). Nothing is mutated in place.

References
----------
  - Binance Futures API docs, "LIMITS" — order-count rate limits per
    interval; repeated violations escalate to 418/429 responses and IP
    bans, so the client must self-throttle below the venue ceiling.
  - Cartea, Jaimungal & Penalva (2015), *Algorithmic and High-Frequency
    Trading*, ch. 10 — exchange-imposed message-rate constraints as a
    first-class input to quoting policy.
"""
from __future__ import annotations

from dataclasses import dataclass

READY = "READY"
COOLDOWN = "COOLDOWN"

# Rolling window for the per-minute budget, in seconds.
_MINUTE_WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class ThrottleParams:
    """Tunables for the throttle state machine."""

    min_interval_seconds: float = 0.5   # hard floor between consecutive quotes
    max_amends_per_minute: int = 40     # rolling 60 s quote-update budget
    burst_limit: int = 8                # quotes allowed inside the burst window
    burst_window_seconds: float = 2.0   # window for burst counting
    cooldown_seconds: float = 5.0       # forced pause after a burst breach


@dataclass(frozen=True)
class ThrottleState:
    """Immutable throttle state.

    ``quote_times`` holds recent quote timestamps (epoch seconds), pruned to
    the trailing 60 s on every call. ``cooldown_until`` is the epoch second
    at which COOLDOWN expires (0.0 when in READY).
    """

    phase: str = READY
    quote_times: tuple[float, ...] = ()
    cooldown_until: float = 0.0


@dataclass(frozen=True)
class ThrottleDecision:
    """Result of one ``should_quote`` call.

    ``allowed`` answers the boolean question. ``reason`` is one of
    ``"ok"`` / ``"cooldown"`` / ``"min_interval"`` / ``"minute_cap"``.
    ``state`` is the next state — with the quote recorded (and COOLDOWN
    possibly latched) when allowed, otherwise the input state with only
    lazy expiry/pruning applied.
    """

    allowed: bool
    reason: str
    state: ThrottleState


def initial_state() -> ThrottleState:
    """Fresh state: READY, no history."""
    return ThrottleState()


def _prune(times: tuple[float, ...], now: float) -> tuple[float, ...]:
    """Keep only timestamps inside the trailing 60 s window."""
    cutoff = now - _MINUTE_WINDOW_SECONDS
    return tuple(t for t in times if t > cutoff)


def should_quote(
    now: float,
    state: ThrottleState,
    params: ThrottleParams,
) -> ThrottleDecision:
    """Decide whether a quote update may be sent at ``now`` (epoch seconds).

    Gate order: cooldown → min interval → minute cap → burst latch. The
    burst latch is checked *after* recording the quote: the quote that
    reaches ``burst_limit`` inside the burst window is still sent, but the
    state transitions to COOLDOWN until ``now + cooldown_seconds``.
    """
    times = _prune(state.quote_times, now)
    phase = state.phase
    cooldown_until = state.cooldown_until

    # Lazy expiry: a stale COOLDOWN flips back to READY on the next call.
    if phase == COOLDOWN and now >= cooldown_until:
        phase = READY
        cooldown_until = 0.0

    base = ThrottleState(phase=phase, quote_times=times,
                         cooldown_until=cooldown_until)

    if phase == COOLDOWN:
        return ThrottleDecision(False, "cooldown", base)
    if times and now - times[-1] < params.min_interval_seconds:
        return ThrottleDecision(False, "min_interval", base)
    if len(times) >= params.max_amends_per_minute:
        return ThrottleDecision(False, "minute_cap", base)

    new_times = times + (float(now),)
    burst_count = sum(
        1 for t in new_times if t > now - params.burst_window_seconds
    )
    if burst_count >= params.burst_limit:
        next_state = ThrottleState(
            phase=COOLDOWN,
            quote_times=new_times,
            cooldown_until=now + params.cooldown_seconds,
        )
        return ThrottleDecision(True, "ok", next_state)

    return ThrottleDecision(
        True, "ok",
        ThrottleState(phase=READY, quote_times=new_times, cooldown_until=0.0),
    )


__all__ = [
    "COOLDOWN",
    "READY",
    "ThrottleDecision",
    "ThrottleParams",
    "ThrottleState",
    "initial_state",
    "should_quote",
]
