"""Hard-cap position-size rule between vol-target sizing and order routing.

Implements SMA-35558 / SMA-36645 (Risk Mgmt #90): per-position, per-symbol,
and per-strategy NAV caps, with a linear drawdown-scaled multiplier and three
breach actions (``block`` / ``trim`` / ``alert``).

This module sits ABOVE ``_shared/sizing/vol_target.py`` (which produces a
soft, vol-normalised *requested* notional) and BELOW the order router. It is
deliberately a pure function — no I/O, no DB, no logging, no global state — so
it can run inside a pre-trade filter without side effects.

Conventions
-----------
- Long and short notionals in the same symbol are aggregated **gross**
  (no netting) — netting is a separate spec.
- Caps are expressed as fractions of NAV (e.g. ``0.05`` == 5% NAV).
- The DD-scaled multiplier collapses caps linearly from ``1.0`` at
  ``DD = dd_scale_trigger`` down to ``dd_scale_floor`` at
  ``DD = 2 * dd_scale_trigger``. Below the trigger it is exactly ``1.0``.
- "Effective cap" = ``max_pct_nav * dd_mult`` for that axis.
- A request is **allowed** if and only if it satisfies the per-position,
  per-symbol, **and** per-strategy effective caps simultaneously.

References
----------
- quant-researcher SPEC, SMA-35558 (parent), 2026-07-26.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Literal, Optional, Sequence, Union

Side = Literal["long", "short"]
BreachAction = Literal["block", "trim", "alert"]
BreachKind = Literal["none", "position", "symbol", "strategy"]


# ---------------------------------------------------------------------------
# Dataclasses — the public I/O contract from the SPEC.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    """A position currently held in the portfolio, in absolute USD notional.

    ``notional_usd`` is the absolute (signed) notional: positive for longs,
    negative for shorts. Sum of ``abs(notional_usd)`` across positions of
    the same symbol gives the gross per-symbol exposure; per-strategy
    aggregation is the same idea across symbols of the same ``strategy_id``.
    """
    strategy_id: str
    symbol: str
    notional_usd: float
    side: Side

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id:
            raise ValueError("strategy_id must be a non-empty string")
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(self.notional_usd, (int, float)):
            raise ValueError("notional_usd must be numeric")
        if not isinstance(self.side, str) or self.side not in ("long", "short"):
            raise ValueError("side must be 'long' or 'short'")


@dataclass(frozen=True)
class PositionRequest:
    """A proposed order BEFORE it hits the router."""
    strategy_id: str
    symbol: str
    side: Side
    requested_notional_usd: float
    ts: object  # pd.Timestamp | datetime | str; opaque here

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id:
            raise ValueError("strategy_id must be a non-empty string")
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError("symbol must be a non-empty string")
        if self.side not in ("long", "short"):
            raise ValueError("side must be 'long' or 'short'")
        try:
            nv = float(self.requested_notional_usd)
        except (TypeError, ValueError) as e:
            raise ValueError(f"requested_notional_usd must be numeric: {e}")
        if nv < 0.0:
            raise ValueError(
                "requested_notional_usd must be >= 0 (use side='short' for shorts)"
            )
        object.__setattr__(self, "requested_notional_usd", nv)


@dataclass(frozen=True)
class PortfolioState:
    """Snapshot of portfolio NAV and live exposure."""
    nav_usd: float
    positions: Sequence[Position] = field(default_factory=tuple)
    drawdown_pct: float = 0.0  # 0.10 == 10% drawdown
    regime: str = "unknown"

    def __post_init__(self) -> None:
        try:
            nav = float(self.nav_usd)
        except (TypeError, ValueError) as e:
            raise ValueError(f"nav_usd must be numeric: {e}")
        if nav <= 0.0:
            raise ValueError(f"nav_usd must be > 0, got {nav}")
        object.__setattr__(self, "nav_usd", nav)
        try:
            dd = float(self.drawdown_pct)
        except (TypeError, ValueError) as e:
            raise ValueError(f"drawdown_pct must be numeric: {e}")
        if dd < 0.0:
            raise ValueError(f"drawdown_pct must be >= 0, got {dd}")
        object.__setattr__(self, "drawdown_pct", dd)
        # positions must be a sequence of Position (or compatible)
        for p in self.positions:
            if not isinstance(p, Position):
                raise TypeError(
                    f"positions must be Position instances, got {type(p).__name__}"
                )


@dataclass(frozen=True)
class MaxSizeConfig:
    """Configuration for the hard-cap rule. Defaults match the SPEC."""
    per_position_max_pct_nav: float = 0.05
    per_symbol_max_pct_nav: float = 0.15
    per_strategy_max_pct_nav: float = 0.40
    dd_scale_trigger: float = 0.10
    dd_scale_floor: float = 0.50
    breach_action: BreachAction = "block"

    def __post_init__(self) -> None:
        for name in (
            "per_position_max_pct_nav",
            "per_symbol_max_pct_nav",
            "per_strategy_max_pct_nav",
            "dd_scale_trigger",
            "dd_scale_floor",
        ):
            v = getattr(self, name)
            if not isinstance(v, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if v < 0.0:
                raise ValueError(f"{name} must be >= 0, got {v}")
        if self.per_position_max_pct_nav > 1.0:
            raise ValueError("per_position_max_pct_nav cannot exceed 100% NAV")
        if self.per_symbol_max_pct_nav > 1.0:
            raise ValueError("per_symbol_max_pct_nav cannot exceed 100% NAV")
        if self.per_strategy_max_pct_nav > 1.0:
            raise ValueError("per_strategy_max_pct_nav cannot exceed 100% NAV")
        if not (0.0 < self.dd_scale_floor <= 1.0):
            raise ValueError(
                f"dd_scale_floor must be in (0, 1], got {self.dd_scale_floor}"
            )
        if self.dd_scale_trigger < 0.0:
            raise ValueError("dd_scale_trigger must be >= 0")
        if self.breach_action not in ("block", "trim", "alert"):
            raise ValueError(
                f"breach_action must be block|trim|alert, got {self.breach_action!r}"
            )


@dataclass(frozen=True)
class MaxSizeDecision:
    """Result of the pre-trade cap check."""
    allow: bool
    capped_notional_usd: float
    breach_kind: BreachKind
    reason: str
    dd_mult: float = 1.0
    effective_per_position_pct: float = 0.0
    effective_per_symbol_pct: float = 0.0
    effective_per_strategy_pct: float = 0.0


# ---------------------------------------------------------------------------
# Core: DD-scaled cap multiplier.
# ---------------------------------------------------------------------------


def dd_scaled_multiplier(drawdown_pct: float, trigger: float, floor: float) -> float:
    """Linear interpolation of the cap multiplier with drawdown.

    Below the trigger, returns 1.0. Above ``2 * trigger`` it is clamped to
    ``floor``. In between it linearly interpolates so that ``dd == 2*trigger``
    yields exactly ``floor`` (matching the SPEC's "DD=20% -> 0.5x" anchor at
    trigger=0.10).

    >>> dd_scaled_multiplier(0.0, 0.10, 0.50)
    1.0
    >>> dd_scaled_multiplier(0.10, 0.10, 0.50)
    1.0
    >>> dd_scaled_multiplier(0.15, 0.10, 0.50)
    0.75
    >>> dd_scaled_multiplier(0.20, 0.10, 0.50)
    0.5
    >>> dd_scaled_multiplier(0.50, 0.10, 0.50)
    0.5
    """
    if trigger <= 0:
        # No scaling region defined — behave as the floor at any drawdown.
        return floor
    if drawdown_pct <= trigger:
        return 1.0
    # Linear interpolation: at 2*trigger -> floor
    denom = trigger  # distance from trigger to the anchor (2*trigger - trigger)
    excess = drawdown_pct - trigger
    # When drawdown_pct >= 2*trigger the result would go below floor;
    # clamp so floor is the floor of the function (not just the endpoint).
    raw = 1.0 - (1.0 - floor) * (excess / denom)
    if raw < floor:
        return floor
    if raw > 1.0:
        return 1.0
    return raw


# ---------------------------------------------------------------------------
# Aggregation helpers — gross notional across positions.
# ---------------------------------------------------------------------------


def _gross_symbol_notional(
    positions: Iterable[Position], symbol: str
) -> float:
    """Sum of |notional_usd| across positions in ``symbol`` (gross, no netting)."""
    total = 0.0
    for p in positions:
        if p.symbol == symbol:
            total += abs(p.notional_usd)
    return total


def _gross_strategy_notional(
    positions: Iterable[Position], strategy_id: str
) -> float:
    """Sum of |notional_usd| across positions of ``strategy_id`` (gross)."""
    total = 0.0
    for p in positions:
        if p.strategy_id == strategy_id:
            total += abs(p.notional_usd)
    return total


# ---------------------------------------------------------------------------
# Main pure function.
# ---------------------------------------------------------------------------


def evaluate_max_position_size(
    request: PositionRequest,
    portfolio: PortfolioState,
    config: Optional[MaxSizeConfig] = None,
) -> MaxSizeDecision:
    """Apply the hard-cap rule to one ``request`` against the live ``portfolio``.

    The function is pure: identical inputs always produce an identical
    decision. No I/O, no DB, no logging, no mutation of ``request``,
    ``portfolio``, or ``config``.

    Logic
    -----
    1. ``dd_mult = dd_scaled_multiplier(drawdown_pct, trigger, floor)``.
    2. Compute effective caps for the three axes (per-position,
       per-symbol, per-strategy) by multiplying each ``config`` cap by
       ``dd_mult``.
    3. Compute proposed per-axis notional exposures AFTER the request:
       per-position = ``requested_notional_usd``; per-symbol =
       ``current_symbol_gross + requested_notional_usd`` (gross,
       ignoring side — netting is a separate spec); per-strategy =
       ``current_strategy_gross + requested_notional_usd``.
    4. For each axis, compute the notional that fits within the effective
       cap (``current_gross`` excludes the request; remaining =
       ``effective_cap_usd - current_gross``).
    5. Pick the tightest of the three remaining-notionals as the cap on
       this request. ``allow`` / ``breach_kind`` depend on whether the
       requested_notional fits. ``capped_notional_usd`` is the allowed
       notional given ``config.breach_action``:
       - ``block``: 0.0 if any cap is breached
       - ``trim``:  the tightest remaining notional (clamped to >=0)
       - ``alert``: the requested notional (caller is informed only)
    """
    cfg = config or MaxSizeConfig()

    dd_mult = dd_scaled_multiplier(
        portfolio.drawdown_pct, cfg.dd_scale_trigger, cfg.dd_scale_floor
    )

    eff_pos_pct = cfg.per_position_max_pct_nav * dd_mult
    eff_sym_pct = cfg.per_symbol_max_pct_nav * dd_mult
    eff_strat_pct = cfg.per_strategy_max_pct_nav * dd_mult

    eff_pos_usd = eff_pos_pct * portfolio.nav_usd
    eff_sym_usd = eff_sym_pct * portfolio.nav_usd
    eff_strat_usd = eff_strat_pct * portfolio.nav_usd

    requested = float(request.requested_notional_usd)

    # Per-position: the request itself cannot exceed the per-position cap.
    pos_remaining = eff_pos_usd  # request stands alone

    # Per-symbol: current gross in this symbol + the new notional.
    cur_sym = _gross_symbol_notional(portfolio.positions, request.symbol)
    sym_remaining = max(eff_sym_usd - cur_sym, 0.0)

    # Per-strategy: current gross in this strategy + the new notional.
    cur_strat = _gross_strategy_notional(portfolio.positions, request.strategy_id)
    strat_remaining = max(eff_strat_usd - cur_strat, 0.0)

    # The tightest cap governs.
    tightest = min(pos_remaining, sym_remaining, strat_remaining)

    fits = requested <= tightest

    if fits and requested == 0.0:
        # Zero-sized request is trivially allowed.
        reason = (
            f"requested_notional=0 fits every cap "
            f"(nav={portfolio.nav_usd:.2f}, dd_mult={dd_mult:.3f})"
        )
        return MaxSizeDecision(
            allow=True,
            capped_notional_usd=0.0,
            breach_kind="none",
            reason=reason,
            dd_mult=dd_mult,
            effective_per_position_pct=eff_pos_pct,
            effective_per_symbol_pct=eff_sym_pct,
            effective_per_strategy_pct=eff_strat_pct,
        )

    if fits:
        # Determine which axis would have been tightest had we gone larger.
        # This is informational — for a fitting request we say "none".
        reason = (
            f"request fits effective caps "
            f"(pos={eff_pos_pct:.4f}, sym={eff_sym_pct:.4f}, "
            f"strat={eff_strat_pct:.4f}; nav={portfolio.nav_usd:.2f}, "
            f"dd_mult={dd_mult:.3f}, tightest_room={tightest:.2f})"
        )
        return MaxSizeDecision(
            allow=True,
            capped_notional_usd=requested,
            breach_kind="none",
            reason=reason,
            dd_mult=dd_mult,
            effective_per_position_pct=eff_pos_pct,
            effective_per_symbol_pct=eff_sym_pct,
            effective_per_strategy_pct=eff_strat_pct,
        )

    # Breach path: pick the binding axis (the one with the smallest room).
    # Equal-room ties: prefer per-position > per-symbol > per-strategy, matching
    # the SPEC's per-position-is-the-tightest-axis order.
    rooms = [
        ("position", pos_remaining),
        ("symbol", sym_remaining),
        ("strategy", strat_remaining),
    ]
    # Smallest room = binding axis. On tie, the spec order above breaks it
    # because list sort is stable.
    binding = min(rooms, key=lambda r: r[1])

    if cfg.breach_action == "block":
        return MaxSizeDecision(
            allow=False,
            capped_notional_usd=0.0,
            breach_kind=binding[0],
            reason=(
                f"BLOCK by {binding[0]} cap "
                f"(requested={requested:.2f} > remaining={binding[1]:.2f}; "
                f"effective caps: "
                f"pos={eff_pos_pct:.4f}, sym={eff_sym_pct:.4f}, "
                f"strat={eff_strat_pct:.4f}; nav={portfolio.nav_usd:.2f}, "
                f"dd_mult={dd_mult:.3f})"
            ),
            dd_mult=dd_mult,
            effective_per_position_pct=eff_pos_pct,
            effective_per_symbol_pct=eff_sym_pct,
            effective_per_strategy_pct=eff_strat_pct,
        )

    if cfg.breach_action == "trim":
        capped = max(tightest, 0.0)
        return MaxSizeDecision(
            allow=capped > 0.0,
            capped_notional_usd=capped,
            breach_kind=binding[0],
            reason=(
                f"TRIM by {binding[0]} cap "
                f"(requested={requested:.2f}, capped={capped:.2f}; "
                f"effective caps: "
                f"pos={eff_pos_pct:.4f}, sym={eff_sym_pct:.4f}, "
                f"strat={eff_strat_pct:.4f}; nav={portfolio.nav_usd:.2f}, "
                f"dd_mult={dd_mult:.3f})"
            ),
            dd_mult=dd_mult,
            effective_per_position_pct=eff_pos_pct,
            effective_per_symbol_pct=eff_sym_pct,
            effective_per_strategy_pct=eff_strat_pct,
        )

    # breach_action == "alert" — caller is informed but allowed through.
    return MaxSizeDecision(
        allow=True,
        capped_notional_usd=requested,
        breach_kind=binding[0],
        reason=(
            f"ALERT: would breach {binding[0]} cap "
            f"(requested={requested:.2f} > remaining={binding[1]:.2f}; "
            f"effective caps: "
            f"pos={eff_pos_pct:.4f}, sym={eff_sym_pct:.4f}, "
            f"strat={eff_strat_pct:.4f}; nav={portfolio.nav_usd:.2f}, "
            f"dd_mult={dd_mult:.3f})"
        ),
        dd_mult=dd_mult,
        effective_per_position_pct=eff_pos_pct,
        effective_per_symbol_pct=eff_sym_pct,
        effective_per_strategy_pct=eff_strat_pct,
    )


__all__: List[str] = [
    "Side",
    "BreachAction",
    "BreachKind",
    "Position",
    "PositionRequest",
    "PortfolioState",
    "MaxSizeConfig",
    "MaxSizeDecision",
    "dd_scaled_multiplier",
    "evaluate_max_position_size",
]
