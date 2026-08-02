"""rate_limiter — Binance-style weight rate limiter (E18).

Venue-facing request pacing for REST endpoints that report usage
via ``X-MBX-USED-WEIGHT-<interval>`` response headers (Binance
spot ``/api`` and USD-M futures ``/fapi`` semantics).

The limiter owns:

* **header parsing** (:func:`parse_used_weight_headers`,
  :func:`parse_order_count_headers`, :func:`parse_retry_after_s`)
  — pure functions turning a response-header mapping into typed
  values.  Header names are matched case-insensitively;
  ``Retry-After`` is parsed as integer seconds (the only form
  Binance emits).
* **token buckets, one per endpoint** — each endpoint
  (:class:`RateLimiter` key, e.g. ``"POST /fapi/v1/order"``)
  tracks its own used-weight within a fixed 1-minute window.
  Local acquires increment the estimate; every response carrying
  a ``USED-WEIGHT`` header *syncs* the bucket to the venue's
  reported value (the venue's number is the truth — local drift
  from retried / parallel requests is corrected on each
  response).
* **soft-cap throttling** — when the projected usage after an
  acquire reaches ``soft_cap_fraction`` (default 0.8) of the
  window limit, the acquire is still allowed but the returned
  :class:`RateLimitDecision` carries ``throttled=True`` and a
  ``wait_s`` that grows linearly from 0 at the soft cap to
  ``throttle_max_delay_s`` at the hard cap — callers insert that
  delay before the next request, bleeding pressure off *before*
  the venue starts rejecting.
* **punitive backoff on 429 / 418** — a 429 response parks the
  endpoint in a cooldown (``Retry-After`` when present, else
  ``penalty_429_s``); a 418 (IP ban) parks it for the ban
  duration (``Retry-After`` when present, else
  ``penalty_418_s``).  While parked, every acquire is refused
  with the remaining wait.

Design constraints
------------------
* Time is explicit (``now_s`` monotonic seconds) — no wall-clock
  reads, fully deterministic in tests.
* No I/O: the limiter only *decides*; the caller performs the
  request and feeds the response back via
  :meth:`RateLimiter.record_response`.
* Core logic is pure: header parsers are module-level pure
  functions; the policy is a frozen dataclass.  Only the bucket
  bookkeeping is mutable state, isolated in :class:`RateLimiter`.

References
----------
- Binance Spot API docs — "LIMITS": ``X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)``,
  ``X-MBX-ORDER-COUNT-...``, HTTP 429 (rate limit) / 418 (IP ban)
  semantics, ``Retry-After`` header.
- Binance USD-M Futures docs — identical header scheme with
  per-endpoint weights.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

# ---------------------------------------------------------------------------
# Pure header parsers
# ---------------------------------------------------------------------------

_USED_WEIGHT_PREFIX = "x-mbx-used-weight-"
_ORDER_COUNT_PREFIX = "x-mbx-order-count-"
_RETRY_AFTER = "retry-after"


def _parse_interval_headers(
    headers: Mapping[str, Any],
    prefix: str,
) -> Dict[str, int]:
    """Parse ``<prefix><intervalNum><intervalLetter>`` headers.

    Returns ``{interval: value}`` with the interval upper-cased,
    e.g. ``{"1M": 1200, "10S": 40}``.  Non-integer values and
    unrelated headers are ignored.  Pure.
    """
    out: Dict[str, int] = {}
    if not headers:
        return out
    for key, value in headers.items():
        k = str(key).strip().lower()
        if not k.startswith(prefix):
            continue
        interval = k[len(prefix):].upper()
        if not interval:
            continue
        try:
            out[interval] = int(str(value).strip())
        except (TypeError, ValueError):
            continue
    return out


def parse_used_weight_headers(
    headers: Mapping[str, Any],
) -> Dict[str, int]:
    """Parse ``X-MBX-USED-WEIGHT-*`` headers → ``{"1M": 1200}``.
    Pure."""
    return _parse_interval_headers(headers, _USED_WEIGHT_PREFIX)


def parse_order_count_headers(
    headers: Mapping[str, Any],
) -> Dict[str, int]:
    """Parse ``X-MBX-ORDER-COUNT-*`` headers → ``{"1M": 12}``.
    Pure."""
    return _parse_interval_headers(headers, _ORDER_COUNT_PREFIX)


def parse_retry_after_s(headers: Mapping[str, Any]) -> Optional[float]:
    """Parse the ``Retry-After`` header as seconds.  Returns
    ``None`` when absent or unparsable (HTTP-date form is not
    emitted by Binance and is deliberately unsupported).  Pure."""
    if not headers:
        return None
    for key, value in headers.items():
        if str(key).strip().lower() != _RETRY_AFTER:
            continue
        try:
            seconds = float(str(value).strip())
        except (TypeError, ValueError):
            return None
        if not math.isfinite(seconds) or seconds < 0:
            return None
        return seconds
    return None


def interval_to_seconds(interval: str) -> Optional[int]:
    """Convert a Binance interval label (``"10S"`` / ``"1M"`` /
    ``"5M"`` / ``"1H"`` / ``"1D"``) to seconds.  ``None`` for
    unknown labels.  Pure."""
    s = str(interval or "").strip().upper()
    if len(s) < 2:
        return None
    unit = s[-1]
    try:
        n = int(s[:-1])
    except ValueError:
        return None
    factor = {"S": 1, "M": 60, "H": 3600, "D": 86400}.get(unit)
    if factor is None or n <= 0:
        return None
    return n * factor


# ---------------------------------------------------------------------------
# Policy + decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitPolicy:
    """Declarative configuration for one endpoint bucket.

    ``weight_limit``         hard cap of used weight per window.
    ``window_s``             window length (default 60s — the
                             Binance ``1M`` interval).
    ``soft_cap_fraction``    fraction of the limit at which
                             throttling starts (default 0.8).
    ``throttle_max_delay_s`` delay returned when projected usage
                             hits the hard cap.
    ``penalty_429_s``        cooldown applied to a 429 response
                             with no ``Retry-After``.
    ``penalty_418_s``        ban duration applied to a 418
                             response with no ``Retry-After``.
    """

    weight_limit: int = 1200
    window_s: float = 60.0
    soft_cap_fraction: float = 0.8
    throttle_max_delay_s: float = 5.0
    penalty_429_s: float = 10.0
    penalty_418_s: float = 120.0

    def __post_init__(self) -> None:
        if self.weight_limit <= 0:
            raise ValueError("weight_limit must be positive")
        if self.window_s <= 0:
            raise ValueError("window_s must be positive")
        if not (0.0 < self.soft_cap_fraction < 1.0):
            raise ValueError("soft_cap_fraction must be in (0, 1)")
        if self.throttle_max_delay_s < 0:
            raise ValueError("throttle_max_delay_s must be >= 0")
        if self.penalty_429_s <= 0 or self.penalty_418_s <= 0:
            raise ValueError("penalties must be positive")


DEFAULT_RATE_LIMIT_POLICY = RateLimitPolicy()


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of one :meth:`RateLimiter.acquire` call.

    ``allowed``      the request may be sent now.
    ``wait_s``       recommended delay before sending / retrying
                     (throttle delay when ``throttled``, remaining
                     park time when refused).
    ``throttled``    allowed, but the soft cap was crossed — the
                     caller should insert ``wait_s`` before the
                     *next* request.
    ``reason``       ``ok`` | ``throttled_soft_cap`` |
                     ``hard_cap`` | ``cooldown_429`` | ``banned_418``.
    ``used_after``   projected used weight in the window after the
                     acquire (current usage when refused).
    """

    allowed: bool
    wait_s: float
    throttled: bool
    reason: str
    used_after: int


@dataclass
class _Bucket:
    """Mutable per-endpoint state (internal)."""

    used_weight: int = 0
    window_start_s: Optional[float] = None
    cooldown_until_s: Optional[float] = None   # 429
    banned_until_s: Optional[float] = None     # 418


# ---------------------------------------------------------------------------
# Limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Binance-style weight rate limiter with per-endpoint buckets.

    Usage::

        limiter = RateLimiter(policies={"POST /fapi/v1/order": policy})
        decision = limiter.acquire("POST /fapi/v1/order", weight=1,
                                   now_s=clock())
        if decision.allowed:
            response = send(...)
            limiter.record_response(
                "POST /fapi/v1/order",
                status=response.status,
                headers=response.headers,
                now_s=clock(),
            )
    """

    def __init__(
        self,
        *,
        policies: Optional[Mapping[str, RateLimitPolicy]] = None,
        default_policy: RateLimitPolicy = DEFAULT_RATE_LIMIT_POLICY,
    ) -> None:
        self._default_policy = default_policy
        self._policies: Dict[str, RateLimitPolicy] = dict(
            policies or {})
        self._buckets: Dict[str, _Bucket] = {}

    # -- internals ---------------------------------------------------------

    def _policy_for(self, endpoint: str) -> RateLimitPolicy:
        return self._policies.get(endpoint, self._default_policy)

    def _bucket_for(self, endpoint: str) -> _Bucket:
        bucket = self._buckets.get(endpoint)
        if bucket is None:
            bucket = _Bucket()
            self._buckets[endpoint] = bucket
        return bucket

    @staticmethod
    def _roll_window(bucket: _Bucket, policy: RateLimitPolicy,
                     now_s: float) -> None:
        """Reset the fixed window when it has elapsed."""
        if bucket.window_start_s is None:
            bucket.window_start_s = float(now_s)
            return
        if float(now_s) - bucket.window_start_s >= policy.window_s:
            bucket.used_weight = 0
            bucket.window_start_s = float(now_s)

    # -- public API ----------------------------------------------------------

    def acquire(
        self,
        endpoint: str,
        *,
        weight: int = 1,
        now_s: float,
    ) -> RateLimitDecision:
        """Decide whether a request of ``weight`` may be sent to
        ``endpoint`` at ``now_s``.

        Parked endpoints (429 cooldown / 418 ban) are refused with
        the remaining park time.  A projected overflow of the hard
        cap is refused with the window-reset wait.  At or above the
        soft cap the acquire is allowed but throttled — ``wait_s``
        grows linearly from 0 (at the soft cap) to
        ``throttle_max_delay_s`` (at the hard cap).
        """
        if weight <= 0:
            raise ValueError(f"weight must be positive, got {weight!r}")
        policy = self._policy_for(endpoint)
        bucket = self._bucket_for(endpoint)
        now_s = float(now_s)

        if bucket.banned_until_s is not None and now_s < bucket.banned_until_s:
            return RateLimitDecision(
                allowed=False,
                wait_s=bucket.banned_until_s - now_s,
                throttled=False,
                reason="banned_418",
                used_after=bucket.used_weight,
            )
        if (bucket.cooldown_until_s is not None
                and now_s < bucket.cooldown_until_s):
            return RateLimitDecision(
                allowed=False,
                wait_s=bucket.cooldown_until_s - now_s,
                throttled=False,
                reason="cooldown_429",
                used_after=bucket.used_weight,
            )

        self._roll_window(bucket, policy, now_s)
        projected = bucket.used_weight + int(weight)
        limit = policy.weight_limit
        if projected > limit:
            # Refuse until the current window elapses.
            window_start = (
                bucket.window_start_s
                if bucket.window_start_s is not None else now_s
            )
            elapsed = now_s - window_start
            wait = max(0.0, policy.window_s - elapsed)
            return RateLimitDecision(
                allowed=False,
                wait_s=wait,
                throttled=False,
                reason="hard_cap",
                used_after=bucket.used_weight,
            )

        bucket.used_weight = projected
        soft = policy.soft_cap_fraction * limit
        if projected >= soft:
            # Linear ramp: 0 at the soft cap → max delay at the cap.
            span = max(1e-9, limit - soft)
            frac = min(1.0, (projected - soft) / span)
            return RateLimitDecision(
                allowed=True,
                wait_s=policy.throttle_max_delay_s * frac,
                throttled=True,
                reason="throttled_soft_cap",
                used_after=projected,
            )
        return RateLimitDecision(
            allowed=True,
            wait_s=0.0,
            throttled=False,
            reason="ok",
            used_after=projected,
        )

    def record_response(
        self,
        endpoint: str,
        *,
        status: int,
        headers: Optional[Mapping[str, Any]] = None,
        now_s: float,
    ) -> None:
        """Feed one HTTP response back into the bucket.

        * ``X-MBX-USED-WEIGHT-<interval>`` whose interval matches
          the policy window syncs the bucket's used weight to the
          venue-reported value (the venue's number is the truth)
          and re-anchors the window at ``now_s``.
        * HTTP 429 parks the endpoint for ``Retry-After`` seconds
          (fallback ``policy.penalty_429_s``).
        * HTTP 418 parks it for the ban duration (fallback
          ``policy.penalty_418_s``).
        """
        policy = self._policy_for(endpoint)
        bucket = self._bucket_for(endpoint)
        now_s = float(now_s)
        headers = headers or {}

        weights = parse_used_weight_headers(headers)
        for interval, used in weights.items():
            if interval_to_seconds(interval) == int(policy.window_s):
                self._roll_window(bucket, policy, now_s)
                # Never let a stale header lower the count below
                # what we already accounted in this window.
                bucket.used_weight = max(bucket.used_weight,
                                         int(used))
                bucket.window_start_s = now_s
                break

        retry_after = parse_retry_after_s(headers)
        if int(status) == 418:
            bucket.banned_until_s = now_s + (
                retry_after if retry_after is not None
                else policy.penalty_418_s
            )
        elif int(status) == 429:
            bucket.cooldown_until_s = now_s + (
                retry_after if retry_after is not None
                else policy.penalty_429_s
            )

    # -- observability -----------------------------------------------------

    def snapshot(self, endpoint: str, *, now_s: float) -> Dict[str, Any]:
        """Read-only view of one endpoint's bucket."""
        policy = self._policy_for(endpoint)
        bucket = self._bucket_for(endpoint)
        self._roll_window(bucket, policy, float(now_s))
        return {
            "endpoint": endpoint,
            "used_weight": bucket.used_weight,
            "weight_limit": policy.weight_limit,
            "soft_cap": int(policy.soft_cap_fraction
                            * policy.weight_limit),
            "cooldown_remaining_s": max(
                0.0, (bucket.cooldown_until_s or 0.0) - float(now_s),
            ),
            "banned_remaining_s": max(
                0.0, (bucket.banned_until_s or 0.0) - float(now_s),
            ),
        }

    def snapshot_all(self, *, now_s: float) -> Dict[str, Dict[str, Any]]:
        return {
            ep: self.snapshot(ep, now_s=now_s)
            for ep in sorted(self._buckets)
        }


__all__ = [
    "DEFAULT_RATE_LIMIT_POLICY",
    "RateLimitDecision",
    "RateLimiter",
    "RateLimitPolicy",
    "interval_to_seconds",
    "parse_order_count_headers",
    "parse_retry_after_s",
    "parse_used_weight_headers",
]
