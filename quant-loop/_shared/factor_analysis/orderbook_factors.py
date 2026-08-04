"""Order-book factor family — L2 snapshot factors for crypto perps.

Five factors over 5-level order-book snapshot frames, each registered in the
shared factor library (:mod:`_shared.strategy_kit.factor_library`) via the
``@_factor`` decorator, so configs can reference them by name and get
schema-validated parameter binding.

Input schema (matches the JSONL written by ``scripts/collect_okx_book_ws.py``):

    ts_ns                          int   exchange event time, ns
    bid_p1..bid_p5, bid_q1..bid_q5 float best-first bid ladder
    ask_p1..ask_p5, ask_q1..ask_q5 float best-first ask ladder

Conventions
-----------
- All factors are causal: the value at row ``t`` uses only rows ``<= t``
  (point-in-time snapshot factors; OFI additionally uses the previous row).
  The no-lookahead execution boundary — signal at bar close, position
  effective next bar — is owned by the strategy/backtest layer, as in
  :mod:`_shared.strategy_kit.factor_library`.
- ``direction``: ``+1`` = higher factor value predicts higher forward
  returns. All five factors here are +1 (bid-side pressure is bullish).
- Degenerate rows (empty ladder side, zero aggregate depth) produce NaN
  rather than a fabricated 0 — the caller decides how to treat gaps.

References
----------
- Cont, Stoikov & Talreja (2010) "The Price Impact of Order Book Events",
  JFE — order-flow imbalance as the driver of short-horizon price moves
  (``ofi_bars``).
- Cartea, Jaimungal & Penalva (2015), *Algorithmic and High-Frequency
  Trading*, CUP — microprice and book imbalance as short-horizon
  predictors (``microprice``, ``book_imbalance``).
- Kwon (2021) "Order book imbalance and its predictive power in equity
  markets" — imbalance/depth-shape predictors (``book_imbalance``,
  ``depth_slope``, ``wall_pressure``).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _shared.strategy_kit.factor_library import _factor
from _shared.strategy_kit.registry import ParamSpec

N_LEVELS = 5

_BID_P = tuple(f"bid_p{i}" for i in range(1, N_LEVELS + 1))
_BID_Q = tuple(f"bid_q{i}" for i in range(1, N_LEVELS + 1))
_ASK_P = tuple(f"ask_p{i}" for i in range(1, N_LEVELS + 1))
_ASK_Q = tuple(f"ask_q{i}" for i in range(1, N_LEVELS + 1))

_ALL_LADDER_COLS = _BID_P + _BID_Q + _ASK_P + _ASK_Q
_TOP_OF_BOOK_COLS = ("bid_p1", "bid_q1", "ask_p1", "ask_q1")

_BP = 1e4  # basis points per unit of relative price


# ---------------------------------------------------------------------------
# Shared helpers (pure)
# ---------------------------------------------------------------------------

def _mid(data: pd.DataFrame) -> pd.Series:
    """Mid price from the top of book; NaN where either side is empty."""
    bid = data["bid_p1"].astype(float).where(data["bid_p1"] > 0)
    ask = data["ask_p1"].astype(float).where(data["ask_p1"] > 0)
    return (bid + ask) / 2.0


def _depth_within_bp(data: pd.DataFrame, depth_bp: float) -> tuple[pd.Series, pd.Series]:
    """Aggregate bid/ask quantity within ``depth_bp`` of the mid.

    A level counts when it is populated (qty > 0, price > 0) and lies no
    further than ``depth_bp`` from the mid on its side. Returns
    ``(bid_depth, ask_depth)`` Series.
    """
    mid = _mid(data)
    bid_lim = mid * (1.0 - depth_bp / _BP)
    ask_lim = mid * (1.0 + depth_bp / _BP)
    bid_depth = pd.Series(0.0, index=data.index)
    ask_depth = pd.Series(0.0, index=data.index)
    for p, q in zip(_BID_P, _BID_Q):
        px, qty = data[p].astype(float), data[q].astype(float)
        bid_depth = bid_depth + qty.where((px > 0) & (qty > 0) & (px >= bid_lim), 0.0)
    for p, q in zip(_ASK_P, _ASK_Q):
        px, qty = data[p].astype(float), data[q].astype(float)
        ask_depth = ask_depth + qty.where((px > 0) & (qty > 0) & (px <= ask_lim), 0.0)
    return bid_depth, ask_depth


# ---------------------------------------------------------------------------
# 1. Book imbalance
# ---------------------------------------------------------------------------
@_factor(
    "book_imbalance", direction=+1,
    reference="Cont, Stoikov & Talreja (2010) JFE; Cartea, Jaimungal & "
              "Penalva (2015); Kwon (2021) order-book imbalance",
    required_columns=_ALL_LADDER_COLS,
    params={"depth_bp": ParamSpec("float", default=10.0, min=0.1)},
    description="(bid_depth - ask_depth)/(bid_depth + ask_depth) within depth_bp of mid",
)
def book_imbalance(data: pd.DataFrame, depth_bp: float = 10.0) -> pd.Series:
    """Depth imbalance within ``depth_bp`` of the mid, in [-1, 1].

    +1 = all depth on the bid side (buy pressure), -1 = all on the ask
    side. NaN when both sides are empty inside the band.
    """
    bid_depth, ask_depth = _depth_within_bp(data, depth_bp)
    total = bid_depth + ask_depth
    return ((bid_depth - ask_depth) / total.where(total > 0)).where(total > 0)


# ---------------------------------------------------------------------------
# 2. Microprice
# ---------------------------------------------------------------------------
@_factor(
    "microprice", direction=+1,
    reference="Cartea, Jaimungal & Penalva (2015) ch. 6 (microprice); "
              "Stoikov (2018) 'The micro-price: a high-frequency estimator'",
    required_columns=_TOP_OF_BOOK_COLS,
    params={},
    description="size-weighted microprice deviation from mid, in bp",
)
def microprice(data: pd.DataFrame) -> pd.Series:
    """Deviation of the size-weighted microprice from the mid, in bp.

    microprice = (bid_p1 * ask_q1 + ask_p1 * bid_q1) / (bid_q1 + ask_q1)

    The microprice tilts toward the *lighter* side of the book: a heavy bid
    stack pulls it above mid (bullish). Output is
    ``(microprice - mid) / mid * 1e4`` — signed bp deviation, direction +1.
    """
    bp, bq = data["bid_p1"].astype(float), data["bid_q1"].astype(float)
    ap, aq = data["ask_p1"].astype(float), data["ask_q1"].astype(float)
    qty = bq + aq
    mp = (bp * aq + ap * bq) / qty.where(qty > 0)
    mid = _mid(data)
    dev = (mp - mid) / mid * _BP
    return dev.where((qty > 0) & mid.notna())


# ---------------------------------------------------------------------------
# 3. Depth slope
# ---------------------------------------------------------------------------
@_factor(
    "depth_slope", direction=+1,
    reference="Kwon (2021) order-book shape; Cartea, Jaimungal & Penalva "
              "(2015) ch. 8 (book shape and price impact)",
    required_columns=_ALL_LADDER_COLS,
    params={"depth_bp": ParamSpec("float", default=50.0, min=0.1)},
    description="ask-side minus bid-side qty-weighted mean distance from mid (bp)",
)
def depth_slope(data: pd.DataFrame, depth_bp: float = 50.0) -> pd.Series:
    """Book-shape asymmetry: ask-side mean depth distance minus bid-side.

    For each side, the slope proxy is the quantity-weighted mean distance
    from mid (bp) of the levels inside ``depth_bp`` — a large value means
    that side's liquidity sits far from the touch (thin near the mid, steep
    wall further out). ``ask_dist - bid_dist`` > 0: bids are stacked closer
    to the mid than asks (tight bid support) -> bullish. NaN when either
    side is empty inside the band.
    """
    mid = _mid(data)

    def _side(prices: tuple[str, ...], qtys: tuple[str, ...],
              sign: float) -> tuple[pd.Series, pd.Series]:
        num = pd.Series(0.0, index=data.index)
        den = pd.Series(0.0, index=data.index)
        lim = mid * (1.0 + sign * depth_bp / _BP)
        for p, q in zip(prices, qtys):
            px, qty = data[p].astype(float), data[q].astype(float)
            dist = sign * (px - mid) / mid * _BP  # >= 0 on each side
            inside = (px > 0) & (qty > 0) & (
                (px >= lim) if sign < 0 else (px <= lim))
            num = num + (qty * dist).where(inside, 0.0)
            den = den + qty.where(inside, 0.0)
        return num, den

    bid_num, bid_den = _side(_BID_P, _BID_Q, sign=-1.0)
    ask_num, ask_den = _side(_ASK_P, _ASK_Q, sign=+1.0)
    bid_dist = bid_num / bid_den.where(bid_den > 0)
    ask_dist = ask_num / ask_den.where(ask_den > 0)
    return (ask_dist - bid_dist).where((bid_den > 0) & (ask_den > 0))


# ---------------------------------------------------------------------------
# 4. Wall pressure
# ---------------------------------------------------------------------------
@_factor(
    "wall_pressure", direction=+1,
    reference="Kwon (2021) large-order walls; Cartea, Jaimungal & Penalva "
              "(2015) (hidden liquidity / wall effects on short-term moves)",
    required_columns=_ALL_LADDER_COLS,
    params={"threshold_x": ParamSpec("float", default=3.0, min=0.0)},
    description="(bid_wall - ask_wall)/mean_level_qty; a wall is a level >= threshold_x * mean",
)
def wall_pressure(data: pd.DataFrame, threshold_x: float = 3.0) -> pd.Series:
    """Large-order wall asymmetry across the visible ladder.

    Mean level quantity ``m`` is taken over all populated levels of both
    sides. A *wall* is any level with qty >= ``threshold_x * m``; the wall
    strength per side is the size of its largest wall (0 when none).
    Output is ``(bid_wall - ask_wall) / m`` — positive when the dominant
    wall sits on the bid side (buy wall supports price). NaN when no level
    is populated at all.
    """
    all_qty = pd.concat(
        [data[c].astype(float) for c in _BID_Q + _ASK_Q], axis=1)
    populated = all_qty.where(all_qty > 0)
    mean_qty = populated.mean(axis=1)

    def _wall(qtys: tuple[str, ...]) -> pd.Series:
        side = pd.concat([data[c].astype(float) for c in qtys], axis=1)
        walls = side.where(side.ge(threshold_x * mean_qty, axis=0), 0.0)
        return walls.max(axis=1)

    bid_wall = _wall(_BID_Q)
    ask_wall = _wall(_ASK_Q)
    pressure = (bid_wall - ask_wall) / mean_qty.where(mean_qty > 0)
    return pressure.where(mean_qty.notna() & (mean_qty > 0))


# ---------------------------------------------------------------------------
# 5. Order-flow imbalance (bar level)
# ---------------------------------------------------------------------------
@_factor(
    "ofi_bars", direction=+1,
    reference="Cont, Stoikov & Talreja (2010) JFE 'The Price Impact of "
              "Order Book Events' (OFI at best bid/ask)",
    required_columns=_TOP_OF_BOOK_COLS,
    params={"window": ParamSpec("int", default=20, min=1)},
    description="rolling sum of event-level OFI at the best bid/ask over window snapshots",
)
def ofi_bars(data: pd.DataFrame, window: int = 20) -> pd.Series:
    """Cont-Stoikov-Talreja order-flow imbalance, rolled over ``window``.

    Per-snapshot event OFI at the best bid/ask (their eq. 3), comparing
    each snapshot to the previous one::

        e_bid = +q_b[n]            if p_b[n] >  p_b[n-1]   (bid improves)
                q_b[n] - q_b[n-1]  if p_b[n] == p_b[n-1]   (size change)
                -q_b[n-1]          if p_b[n] <  p_b[n-1]   (bid retreats)
        e_ask = +q_a[n]            if p_a[n] <  p_a[n-1]   (ask improves)
                q_a[n] - q_a[n-1]  if p_a[n] == p_a[n-1]
                -q_a[n-1]          if p_a[n] >  p_a[n-1]   (ask retreats)
        ofi   = e_bid - e_ask

    Positive OFI = buy-side events dominate (aggressive bids / retreating
    asks) -> upward price impact. Output is the rolling sum over the last
    ``window`` snapshots; the first row is NaN (no previous snapshot).
    """
    bp, bq = data["bid_p1"].astype(float), data["bid_q1"].astype(float)
    ap, aq = data["ask_p1"].astype(float), data["ask_q1"].astype(float)

    def _events(px: pd.Series, qty: pd.Series, improve: pd.Series,
                retreat: pd.Series) -> pd.Series:
        same = ~(improve | retreat)
        return (qty.diff().where(same, 0.0)
                + qty.where(improve, 0.0)
                - qty.shift(1).where(retreat, 0.0))

    e_bid = _events(bp, bq, improve=bp > bp.shift(1), retreat=bp < bp.shift(1))
    e_ask = _events(ap, aq, improve=ap < ap.shift(1), retreat=ap > ap.shift(1))
    ofi = e_bid - e_ask
    ofi.iloc[0] = np.nan  # first snapshot has no predecessor
    out = ofi.rolling(window, min_periods=1).sum()
    out[ofi.isna()] = np.nan  # rolling sum would silently zero NaN events
    return out
