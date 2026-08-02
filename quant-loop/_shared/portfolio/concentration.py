"""Theme/sector concentration limiter (I14).

Adds a fifth dimension on top of the four hard caps in
``_shared/portfolio/exposure.py`` (total / per-symbol / per-direction /
leverage): notional aggregated per *theme* — a coarse bucket such as
``major`` (BTC, ETH), ``L1`` (SOL, AVAX) or ``meme`` (DOGE). A book can
pass all four exposure caps and still be one narrative (e.g. 80% L1
beta); theme caps catch that.

Core logic is the pure function :func:`check_concentration`;
:class:`ConcentrationLimiter` is the thin stateful wrapper tracking the
book and logging every rejection — same shape as ``ExposureLimiter`` so
the two compose in a pre-trade check chain.

Symbols missing from the ``themes`` mapping fall into
:data:`DEFAULT_THEME` (``"unmapped"``) so an unmapped listing is
visible in the audit trail instead of silently uncapped.

References:
  - Grinold & Kahn (2000), "Active Portfolio Management", Ch. 3
    (risk decomposition along common factors — here themes are the
    trader-declared factor buckets).
  - López de Prado (2018), "Advances in Financial Machine Learning",
    Ch. 10 (position limits under concurrent bets sharing a factor).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Tuple

from _shared.portfolio.exposure import Position

DEFAULT_THEME = "unmapped"


@dataclass(frozen=True)
class ConcentrationLimits:
    """Per-theme notional caps. ``None`` default cap = unlimited."""

    theme_caps: Mapping[str, float] = field(default_factory=dict)
    default_cap: float | None = None   # cap for themes absent from theme_caps


@dataclass(frozen=True)
class ConcentrationRejection:
    """Audit record of a position change rejected on theme grounds."""

    symbol: str
    theme: str
    qty: float
    price: float
    reason: str


def theme_exposure(
    positions: Mapping[str, Position],
    themes: Mapping[str, str],
) -> Dict[str, float]:
    """Aggregate absolute notional per theme. Pure."""
    out: Dict[str, float] = {}
    for pos in positions.values():
        theme = themes.get(pos.symbol, DEFAULT_THEME)
        out[theme] = out.get(theme, 0.0) + pos.notional
    return out


def check_concentration(
    positions: Mapping[str, Position],
    new: Position,
    themes: Mapping[str, str],
    limits: ConcentrationLimits,
) -> Tuple[bool, str]:
    """Would replacing ``positions[new.symbol]`` with ``new`` keep the
    new position's theme within its cap? Pure — does not mutate.

    Only the theme of ``new`` is re-checked: an already-over-cap theme
    elsewhere in the book does not block unrelated trades, and closing
    a position (``qty == 0``) is always allowed. Returns
    ``(allowed, reason)``; ``reason`` is "" when allowed.
    """
    if new.qty == 0.0:
        return True, ""

    book = dict(positions)
    book[new.symbol] = new
    theme = themes.get(new.symbol, DEFAULT_THEME)
    agg = theme_exposure(book, themes).get(theme, 0.0)

    cap = limits.theme_caps.get(theme, limits.default_cap)
    if cap is not None and agg > cap:
        return False, (
            f"theme cap: {theme} notional {agg:.2f} > {cap:.2f} "
            f"(new: {new.symbol} {new.notional:.2f})"
        )
    return True, ""


class ConcentrationLimiter:
    """Stateful book tracker enforcing :class:`ConcentrationLimits`.

    Usage::

        lim = ConcentrationLimiter(
            ConcentrationLimits(theme_caps={"meme": 5_000.0}),
            themes={"BTC": "major", "ETH": "major", "DOGE": "meme"},
        )
        ok, reason = lim.check(Position("DOGE", 60.0, 100.0))
        if ok:
            lim.apply(Position("DOGE", 60.0, 100.0))
    """

    def __init__(
        self,
        limits: ConcentrationLimits,
        themes: Mapping[str, str],
    ):
        self.limits = limits
        self.themes = dict(themes)
        self._positions: Dict[str, Position] = {}
        self._rejections: List[ConcentrationRejection] = []

    @property
    def positions(self) -> Dict[str, Position]:
        return dict(self._positions)

    @property
    def rejections(self) -> List[ConcentrationRejection]:
        return list(self._rejections)

    def check(self, new: Position) -> Tuple[bool, str]:
        """Check and log. Rejections are appended to ``self.rejections``."""
        allowed, reason = check_concentration(
            self._positions, new, self.themes, self.limits
        )
        if not allowed:
            self._rejections.append(
                ConcentrationRejection(
                    new.symbol,
                    self.themes.get(new.symbol, DEFAULT_THEME),
                    new.qty,
                    new.price,
                    reason,
                )
            )
        return allowed, reason

    def apply(self, new: Position) -> None:
        """Update the book. Call only after ``check`` returned True."""
        if new.qty == 0.0:
            self._positions.pop(new.symbol, None)
        else:
            self._positions[new.symbol] = new

    def theme_notional(self) -> Dict[str, float]:
        """Current absolute notional per theme."""
        return theme_exposure(self._positions, self.themes)
