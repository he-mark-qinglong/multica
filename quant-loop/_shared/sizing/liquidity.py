"""Multi-Cap Liquidity Sizing (MCLS) — per SPEC liquidity_sizing_v1_20260726/SPEC.md.

Provides a per-bar liquidity-aware sizing primitive that bounds each order's
notional by the tightest available liquidity constraint on the book at signal
time, and **composes with** (does NOT replace) the regime-adaptive
``_shared/sizing/vol_target.py`` layer.

Per-bar size multiplier:

    m_t = min(
        cap_adv,        # (a) ADV-fraction cap
        cap_depth,      # (b) top-of-book depth cap
        cap_part,       # (c) bar-volume participation cap
        cap_impact,     # (d) impact-vs-edge shrink
        cap_vpin,       # (e) VPIN-aware shrink
        cap_base*vol_w, # (f) compose with vol_target multiplier
    )

Each cap closes a distinct failure mode (see SPEC §4):

- ``cap_adv``   — "sized more than 24h ADV justifies" (load-bearing: SMA-34955
                 killed the fixed-pct axis; MCLS opens the depth-axis instead)
- ``cap_depth`` — "order bigger than displayed book" (Albers et al. 2025)
- ``cap_part``  — "participation rate signals" (Kyle 1985)
- ``cap_impact`` — "projected impact eats edge" (T01/T04 cost-cap)
- ``cap_vpin``  — "informed flow is asymmetric on this bar"
                  (Easley, López de Prado, O'Hara 2012)

When ALL active liquidity caps fall below ``k_floor``, MCLS returns ``0.0``
— kill-switch handoff to the risk flatten layer (V5 acceptance gate).

When the L2 depth snapshot is stale (age > ``l2_stale_seconds``),
``cap_depth`` is excluded from the intersection (per SPEC V6, "fall back to
cap_adv-only") and the kill-switch check drops ``cap_depth`` too.

References:
- SPEC §12 for the full citation list
- Torre & Ferraris (1997) — square-root market impact (used in cap_impact)
- Easley, López de Prado, O'Hara (2012) — VPIN
- Lee-Ready (1991) — bulk-volume classifier
- Albers et al. (2025) — Albers dilemma (top-of-book depth)
- Kyle (1985) — informed-trading probability background

This module is OPT-IN per ``_shared/sizing/README.md`` — strategies adopt
MCLS by calling ``MCLS(...).size_multiplier(...)`` on each bar. No
auto-wiring. Composes with the existing ``vol_target.apply_vol_target``
layer; the two layers are independent and each can be enabled or disabled
without affecting the other.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MCLSParams:
    """MCLS tuning constants. Defaults calibrated to the ratified
    SMA-34900/SMA-34913 22bp RT futures cost-cap on BTC/ETH/SOL
    (SPEC §3.1).
    """
    k_adv: float = 0.02                  # max 2% of 24h ADV per fill
    k_depth: float = 0.10                # max 10% of top-5 combined depth
    k_part: float = 0.05                 # max 5% of 1h bar volume
    k_impact: float = 0.5                # impact / edge shrink threshold
    k_vpin: float = 0.6                  # informed-trading shrink threshold
    floor: float = 0.0                   # minimum returned multiplier
    cap: float = 1.5                     # maximum returned multiplier (1.5x base)
    k_floor: float = 0.05                # flatten trigger (V5) when all active
                                        # liquidity caps fall below this
    l2_stale_seconds: float = 60.0       # L2 staleness → cap_depth excluded
                                        # from intersection (V6 fallback)
    impact_alpha_bp: float = 10.0        # square-root impact coefficient (bp)
    cap_base: float = 1.0                # base scalar for vol_target composition


@dataclass(frozen=True)
class LiquiditySnapshot:
    """Per-bar liquidity inputs (SPEC §5). All USD denominated.

    Attributes:
        timestamp: bar close time.
        adv_24h_usd: 24h rolling average daily volume in USD. Real
            aggTrades source per execution-microstructure SKILL.
        depth_top5_usd: top-5 bid + ask depth in USD, 60s median of L2
            order-book snapshots. Source: Binance USDⓈ-M WS depth stream
            ``<symbol>@depth20@100ms``.
        depth_age_seconds: age of the L2 snapshot in seconds. When
            ``> l2_stale_seconds``, the stale-L2 fallback fires
            (cap_depth excluded).
        vol_1h_usd: rolling 1h bar volume in USD, aggTrades-aggregated.
            kline proxy REJECTED a priori (T01 lesson).
        vpin: bulk-volume VPIN ∈ [0, 1]. Bulk-classifier on aggTrades
            (Lee-Ready 1991).
        expected_edge_bp: strategy edge estimate in basis points. Pass
            ``+inf`` to skip ``cap_impact`` (no shrink); ``<= 0`` is
            treated as "no edge estimate" and also skips the cap.
    """
    timestamp: pd.Timestamp
    adv_24h_usd: float
    depth_top5_usd: float
    depth_age_seconds: float
    vol_1h_usd: float
    vpin: float
    expected_edge_bp: float


class MCLS:
    """Multi-Cap Liquidity Sizing — intersection of 5 caps + compose with
    vol_target (SPEC §5 public API).
    """

    def __init__(self, params: Optional[MCLSParams] = None):
        self.p: MCLSParams = params if params is not None else MCLSParams()

    # ----- public API ----------------------------------------------------------

    def size_multiplier(
        self,
        snap: LiquiditySnapshot,
        base_size_usd: float,
        vol_target_weight: float = 1.0,
    ) -> float:
        """Per-bar multiplier in [floor, cap]; 0.0 if every active liquidity
        cap is below ``k_floor`` (V5 SPEC §6 kill-switch handoff).

        Args:
            snap: liquidity snapshot at this bar (see ``LiquiditySnapshot``).
            base_size_usd: notional the strategy wants this bar (USD).
                Must be > 0.
            vol_target_weight: output of ``vol_target.py`` for this bar
                (default 1.0 = no vol targeting applied separately).
                Composes as ``cap_base * vol_target_weight`` inside the
                intersection (SPEC §3.2).

        Returns:
            float multiplier m_t in [floor, cap]; ``0.0`` triggers the
            flatten/kill-switch handoff when **all** active liquidity caps
            (i.e. ``max(active_liq) < k_floor``) are below the floor —
            per SPEC §6 V5, the book must be too thin on every axis to
            hold any notional. A single cap above floor is enough to
            suppress the kill (the strategy can still deploy up to the
            tightest cap's limit on that bar).
        """
        if base_size_usd <= 0:
            raise ValueError(
                f"base_size_usd must be > 0 (got {base_size_usd}); "
                "MCLS scales an existing notional, it does not create one."
            )

        cap_adv = self._cap_adv(snap.adv_24h_usd, base_size_usd)
        cap_depth = self._cap_depth(
            snap.depth_top5_usd, base_size_usd, snap.depth_age_seconds,
        )
        cap_part = self._cap_part(snap.vol_1h_usd, base_size_usd)
        cap_impact = self._cap_impact(
            snap.adv_24h_usd, base_size_usd, snap.expected_edge_bp,
        )
        cap_vpin = self._cap_vpin(snap.vpin)
        cap_base_vol = self.p.cap_base * float(vol_target_weight)

        # V6 stale-L2 fallback: when the L2 snapshot is older than the
        # configured staleness threshold, ``cap_depth`` is excluded from
        # the intersection (per SPEC: "MCLS falls back to cap_adv-only").
        l2_stale = snap.depth_age_seconds > self.p.l2_stale_seconds

        # Active liquidity caps (the 5 caps used by the kill-switch check).
        # ``cap_base_vol`` is the regime scalar and is NOT part of the
        # kill-switch condition; it only feeds the final min().
        if l2_stale:
            active_liq = (cap_adv, cap_part, cap_impact, cap_vpin)
            all_caps = (cap_adv, cap_part, cap_impact, cap_vpin, cap_base_vol)
        else:
            active_liq = (cap_adv, cap_depth, cap_part, cap_impact, cap_vpin)
            all_caps = (cap_adv, cap_depth, cap_part, cap_impact, cap_vpin, cap_base_vol)

        # V5 kill-switch handoff (SPEC §6).
        # Kill fires ONLY when *every* active liquidity cap is below k_floor
        # ("the book is too thin on every axis"), NOT when the tightest cap is
        # below floor. Concretely: kill iff max(active_liq) < k_floor, equivalent
        # to "all caps below floor simultaneously". This matches §6 V5 verbatim
        # ("all 5 caps < k_floor simultaneously") and the test T15 expectation
        # that kill does NOT fire when one cap (e.g. cap_adv) is above floor.
        # Note: this is the conservative direction — using `min(...) < k_floor`
        # would over-fire (kill whenever *any* cap is tight), which is not the
        # SPEC contract.
        if max(active_liq) < self.p.k_floor:
            return 0.0

        m_t = min(all_caps)
        # Clip to [floor, cap]. NaN/inf in inputs propagate and are caught
        # by np.clip (NaN→NaN; np.nan_to_num is NOT applied to preserve the
        # fail-loud behaviour for upstream data bugs).
        return float(np.clip(m_t, self.p.floor, self.p.cap))

    def cap_breakdown(
        self,
        snap: LiquiditySnapshot,
        base_size_usd: float,
    ) -> dict:
        """Diagnostic: return each cap value individually.

        Does NOT affect ``size_multiplier``. Useful for logging,
        kill-switch trigger logic, and the V2.* sub-gate audits.
        """
        if base_size_usd <= 0:
            raise ValueError(
                f"base_size_usd must be > 0 (got {base_size_usd})"
            )

        cap_adv = self._cap_adv(snap.adv_24h_usd, base_size_usd)
        cap_depth = self._cap_depth(
            snap.depth_top5_usd, base_size_usd, snap.depth_age_seconds,
        )
        cap_part = self._cap_part(snap.vol_1h_usd, base_size_usd)
        cap_impact = self._cap_impact(
            snap.adv_24h_usd, base_size_usd, snap.expected_edge_bp,
        )
        cap_vpin = self._cap_vpin(snap.vpin)
        l2_stale = snap.depth_age_seconds > self.p.l2_stale_seconds

        return {
            "cap_adv": cap_adv,
            "cap_depth": cap_depth,
            "cap_part": cap_part,
            "cap_impact": cap_impact,
            "cap_vpin": cap_vpin,
            "cap_base": self.p.cap_base,
            "l2_stale": l2_stale,
        }

    # ----- internal: per-cap computation (SPEC §4) -----------------------------

    def _cap_adv(self, adv_24h_usd: float, base_size_usd: float) -> float:
        """cap_adv = k_adv * ADV_24h / target_dollar_notional (SPEC §4.1).

        Missing data (adv <= 0) returns +inf → no ADV-side constraint.
        The remaining caps still constrain.
        """
        if adv_24h_usd <= 0:
            return float("inf")
        return self.p.k_adv * adv_24h_usd / base_size_usd

    def _cap_depth(
        self,
        depth_top5_usd: float,
        base_size_usd: float,
        depth_age_seconds: float,
    ) -> float:
        """cap_depth = k_depth * depth_topN / target_dollar_notional (SPEC §4.2).

        Stale L2 (age > l2_stale_seconds) is signalled by the caller
        (size_multiplier excludes ``cap_depth`` from the intersection
        under V6). The numeric value of cap_depth when stale is +inf so
        it would not artificially shrink anything if accidentally
        included.
        """
        if depth_age_seconds > self.p.l2_stale_seconds:
            return float("inf")
        if depth_top5_usd <= 0:
            return float("inf")
        return self.p.k_depth * depth_top5_usd / base_size_usd

    def _cap_part(self, vol_1h_usd: float, base_size_usd: float) -> float:
        """cap_part = k_part * vol_1h / target_dollar_notional (SPEC §4.3)."""
        if vol_1h_usd <= 0:
            return float("inf")
        return self.p.k_part * vol_1h_usd / base_size_usd

    def _cap_impact(
        self,
        adv_24h_usd: float,
        base_size_usd: float,
        expected_edge_bp: float,
    ) -> float:
        """Square-root impact vs expected edge (SPEC §4.4).

        expected_impact_bp = alpha * sqrt(order_qty / ADV_24h)
        if expected_impact_bp > k_impact * expected_edge_bp:
            cap_impact = k_impact * expected_edge_bp / expected_impact_bp
        else:
            cap_impact = 1.0
        """
        # Strategies without an edge estimate pass +inf (or 0/neg) → no shrink.
        if (not np.isfinite(expected_edge_bp)) or expected_edge_bp <= 0:
            return 1.0
        if adv_24h_usd <= 0 or base_size_usd <= 0:
            return 1.0

        participation = base_size_usd / adv_24h_usd
        expected_impact_bp = self.p.impact_alpha_bp * (participation ** 0.5)
        threshold = self.p.k_impact * expected_edge_bp
        if expected_impact_bp > threshold and expected_impact_bp > 0:
            return threshold / expected_impact_bp
        return 1.0

    def _cap_vpin(self, vpin: float) -> float:
        """VPIN-aware shrink (SPEC §4.5).

        if VPIN > k_vpin:
            cap_vpin = (1 - VPIN) / (1 - k_vpin)
        else:
            cap_vpin = 1.0
        """
        if vpin > self.p.k_vpin:
            denom = 1.0 - self.p.k_vpin
            if denom <= 0:
                # k_vpin misconfiguration (>=1.0); never shrink to be safe.
                return 1.0
            return (1.0 - vpin) / denom
        return 1.0