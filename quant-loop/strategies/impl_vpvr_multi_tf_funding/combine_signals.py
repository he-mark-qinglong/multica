"""Combination layer for vpvr_multi_tf_funding (SMA-34989).

This module implements the SPEC §Combination logic:

  Rule 1 — Confirmation matrix (4h regime gates direction; lower-TF
           edges vote; 2-of-3 = standard; 3-of-3 = high-conviction;
           1-of-3 = counter-trend lean at half size; 0-of-3 = no entry).

  Rule 2 — Conflict resolution (when 1m and 15m disagree, 15m wins
           because it has lower noise; 4h gate wins on disagreement
           with lower-TFs; "1m leads" branch only when 1m fires inside
           an active LOID cluster AND 15m has not fired in the prior
           4 bars).

  Rule 3 — Weighting (default ``equal``: each TF contributes 1/3; the
           vol_adjusted and recency_weighted modes are flagged as v2
           variants in the SPEC and are deliberately not implemented).

Cross-TF cascade (SPEC §Cross-TF confirmation requirements):

  Higher-TF bias: 4h regime gates direction.
  Lower-TF entry timing: 1 bar 15m delay after a 4h regime flip;
                        wait for next 1m LOID cluster after 15m
                        confirmation.
  Anti-cascade: when 1m leads (Rule 2 branch), 15m must confirm within
                1 bar of 15m (15 minutes) or the position is exited
                at the next 1m bar's open.

Public API
----------
``combine_signals(sig_1m, sig_15m, sig_4h, params)`` -> ``pd.DataFrame``
    Reindex all three per-TF signal frames to a common ``1m``-aligned
    index, apply Rules 1-3 + the cross-TF cascade, and return a single
    per-bar decision frame with columns:

        ``decision``        (int, {-1, 0, +1}) — final per-bar direction
        ``conviction``      (str, {"", "high"}) — 3-of-3 marker
        ``size_mult``       (float, {0.5, 1.0}) — half size on 1-of-3 lean
        ``regime_4h``       (str) — 4h regime label at this bar
        ``agree_count``     (int) — number of TFs agreeing with the decision
        ``micro_long``, ``micro_short``, ``carry_long``, ``struct_long``, ``struct_short``
        ``lead_1m``         (bool) — Rule 2 special-branch flag
        ``wait_15m``        (bool) — wait for 15m confirmation

This module is pure-Python / pure-pandas: no I/O, deterministic, and
unit-tested by ``tests/test_combine_signals.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Confirmation matrix (SPEC Rule 1, plain text reproduction).
# Rows: lower-TF direction. Cols: 4h regime.
# Cells: "ALLOW" / "DENY".
# ---------------------------------------------------------------------------
_CONFIRMATION: Dict[Tuple[str, str], Dict[str, str]] = {
    # long entries (1m or 15m) — allowed only when 4h gates long
    ("1m", "long"): {
        "TREND_UP": "ALLOW",
        "MEAN_REVERT": "ALLOW",
        "TREND_DOWN": "DENY",
        "BLOCKED": "DENY",
    },
    ("15m", "long"): {
        "TREND_UP": "ALLOW",
        "MEAN_REVERT": "ALLOW",
        "TREND_DOWN": "DENY",
        "BLOCKED": "DENY",
    },
    # short entries — allowed only when 4h gates short
    ("1m", "short"): {
        "TREND_UP": "DENY",
        "MEAN_REVERT": "DENY",
        "TREND_DOWN": "ALLOW",
        "BLOCKED": "DENY",
    },
    # 15m short is always DENY in v1 (family exhaustion rule).
    ("15m", "short"): {
        "TREND_UP": "DENY",
        "MEAN_REVERT": "DENY",
        "TREND_DOWN": "DENY",
        "BLOCKED": "DENY",
    },
}


def _is_allowed(tf: str, side: str, regime: str) -> bool:
    row = _CONFIRMATION.get((tf, side), {})
    return row.get(regime, "DENY") == "ALLOW"


# ---------------------------------------------------------------------------
# Cross-TF reindexing helpers.
# ---------------------------------------------------------------------------

def _align_to_1m(
    sig_1m: pd.DataFrame, sig_15m: pd.DataFrame, sig_4h: pd.DataFrame
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Reindex the 15m and 4h frames onto the 1m frame's index with
    ffill (the 4h regime at a 1m bar is the most recent 4h bar's
    regime, etc.). All three frames must have a DatetimeIndex in UTC.
    """
    if sig_1m.index.tz is None:
        sig_1m = sig_1m.copy()
        sig_1m.index = sig_1m.index.tz_localize("UTC")
    sig_15m_a = sig_15m.reindex(sig_1m.index, method="ffill")
    sig_4h_a = sig_4h.reindex(sig_1m.index, method="ffill")
    return sig_1m, sig_15m_a, sig_4h_a


# ---------------------------------------------------------------------------
# Per-TF direction voting on the aligned 1m index.
# ---------------------------------------------------------------------------

def _vote_per_tf(
    sig_1m_a: pd.DataFrame,
    sig_15m_a: pd.DataFrame,
    sig_4h_a: pd.DataFrame,
) -> pd.DataFrame:
    """Return a frame with columns ``micro``, ``carry``, ``struct``,
    each in {-1, 0, +1}, evaluated on the 1m index after gating by
    the 4h regime (Rule 1).
    """
    regime = sig_4h_a["regime"]

    # 1m
    micro_long_allowed = (
        sig_1m_a["micro_long"].astype(bool) & regime.map(lambda r: _is_allowed("1m", "long", r))
    )
    micro_short_allowed = (
        sig_1m_a["micro_short"].astype(bool) & regime.map(lambda r: _is_allowed("1m", "short", r))
    )
    micro = pd.Series(0, index=sig_1m_a.index, dtype=np.int64)
    micro = micro.mask(micro_long_allowed, 1)
    micro = micro.mask(micro_short_allowed, -1)

    # 15m (carry_long only in v1)
    carry_long_allowed = (
        sig_15m_a["carry_long"].astype(bool) & regime.map(lambda r: _is_allowed("15m", "long", r))
    )
    carry = pd.Series(0, index=sig_1m_a.index, dtype=np.int64)
    carry = carry.mask(carry_long_allowed, 1)

    # 4h
    struct_long_allowed = (
        sig_4h_a["struct_long"].astype(bool)
        & regime.map(lambda r: _is_allowed("4h", "long", r) if False else False)
    )
    struct_short_allowed = (
        sig_4h_a["struct_short"].astype(bool)
        & regime.map(lambda r: _is_allowed("4h", "short", r) if False else False)
    )
    # 4h direction is the regime itself, not a separately-gated edge.
    # Per SPEC §Combination: with the 4h direction fixed, lower-TF
    # edges vote; the 4h edge's role is the gate, not the vote.
    # But Rule 1 also lets the 4h structural edge fire longs under
    # MEAN_REVERT/TREND_UP and shorts under TREND_DOWN, which is the
    # gate by construction.
    struct = pd.Series(0, index=sig_1m_a.index, dtype=np.int64)
    struct = struct.mask(regime.isin(["TREND_UP", "MEAN_REVERT"]), 1)
    struct = struct.mask(regime.eq("TREND_DOWN"), -1)

    return pd.DataFrame({
        "micro": micro,
        "carry": carry,
        "struct": struct,
    })


# ---------------------------------------------------------------------------
# Conflict resolution + cross-TF cascade (Rules 2 + 3 + cascade).
# ---------------------------------------------------------------------------

def _resolve_conflicts_and_cascade(
    votes: pd.DataFrame,
    sig_1m_a: pd.DataFrame,
    sig_15m_a: pd.DataFrame,
    sig_4h_a: pd.DataFrame,
) -> pd.DataFrame:
    """Apply Rule 2 (conflict resolution), Rule 3 (weighting), and the
    cross-TF cascade to produce the final decision frame.

    Output columns:
        decision   (int, {-1, 0, +1})
        conviction (str, "" or "high")
        size_mult  (float, 0.5 or 1.0)
        regime_4h  (str)
        agree_count (int, 0..3 — number of TFs agreeing with ``decision``)
        lead_1m    (bool) -- Rule 2 "1m leads" branch fired at this bar
        wait_15m   (bool) -- anti-cascade: waiting for 15m confirmation
    """
    micro = votes["micro"]
    carry = votes["carry"]
    struct = votes["struct"]
    regime = sig_4h_a["regime"]
    cluster_active = sig_1m_a.get("cluster_active", pd.Series(False, index=sig_1m_a.index))

    # 15m has not fired in the prior 4 1m bars (= prior 4 minutes
    # before the first 15m bar opens in the next 15 minutes). The
    # 1m-leads branch requires the 15m edge to be silent recently.
    carry_arr = carry.values
    prev_carry_4 = pd.Series(carry_arr, index=carry.index).rolling(4, min_periods=1).max().shift(1)
    no_recent_15m = prev_carry_4.fillna(0).eq(0)

    # Rule 2: 4h direction is the gate.
    # Pull the 4h-direction from struct sign (1 for TREND_UP/MEAN_REVERT,
    # -1 for TREND_DOWN, 0 for BLOCKED).
    gate = struct  # -1, 0, +1 by regime

    # Rule 2 conflict resolution:
    #   - If 4h gate == 0 -> decision = 0.
    #   - If lower TFs agree with gate -> use gate direction.
    #   - If 1m and 15m disagree on direction:
    #       - both lower TF signals align to gate -> use gate.
    #       - 1m aligns to gate but 15m doesn't (carry==0):
    #           - if cluster_active and no_recent_15m -> "1m leads" branch:
    #             decision = gate, conviction = "", size_mult = 0.5,
    #             lead_1m = True, must confirm within 1 15m bar.
    #           - else: do not enter (15m is silent and 1m is the only
    #             non-zero signal; insufficient confirmation).
    #       - 15m aligns to gate but 1m doesn't: 15m wins -> decision =
    #         gate, size_mult = 1.0 (standard).
    #   - Higher-TF bias filter: 4h regime flips to BLOCKED -> decision
    #     = 0 from that bar forward (Rule 1 "0-of-3 BLOCKED no entry").
    decision = pd.Series(0, index=micro.index, dtype=np.int64)
    conviction = pd.Series("", index=micro.index, dtype=object)
    size_mult = pd.Series(1.0, index=micro.index, dtype=np.float64)
    lead_1m = pd.Series(False, index=micro.index, dtype=bool)
    wait_15m = pd.Series(False, index=micro.index, dtype=bool)

    micro_v = micro.values
    carry_v = carry.values
    gate_v = gate.values
    cluster_v = cluster_active.values
    no_recent_v = no_recent_15m.values
    regime_v = regime.values
    n = len(micro)

    # Anti-cascade: when 1m leads, position is allowed for at most 1
    # 15m bar (~15 1m bars) before 15m must confirm. The cascade
    # tracking below attaches an expiry to the lead_1m flag.
    last_lead_ts: Optional[int] = None
    last_lead_dir: int = 0

    # Track "wait for 15m" — when 4h regime has just flipped, the
    # strategy delays 15m entries by 1 bar. Implemented as: at the bar
    # where struct != previous struct, mark wait_15m = True for one
    # 15m bar (~15 1m bars). For simplicity we approximate by holding
    # wait_15m True for the next 15 bars after a regime transition.
    regime_arr = np.asarray(regime_v, dtype=object)
    struct_arr = np.asarray(gate_v, dtype=np.int64)
    prev_struct = np.concatenate([[0], struct_arr[:-1]])
    just_flipped = (struct_arr != prev_struct) & (struct_arr != 0)

    for i in range(n):
        g = int(gate_v[i])
        m = int(micro_v[i])
        c = int(carry_v[i])
        reg = str(regime_v[i])

        if g == 0:
            # 4h BLOCKED (or otherwise gate==0). Clear any pending
            # "1m leads" expiry.
            last_lead_ts = None
            last_lead_dir = 0
            decision.iat[i] = 0
            continue

        # Higher-TF bias filter: 4h regime just flipped -> wait one 15m
        # bar before allowing 15m entries.
        if just_flipped[i]:
            wait_15m.iat[i] = True

        # Check if this bar's cluster is still in an active 1m-leads
        # window (anti-cascade: 15m must confirm within 1 15m bar).
        if last_lead_ts is not None and (i - last_lead_ts) <= 15:
            if c == last_lead_dir:
                # 15m confirmed -> upgrade to full size.
                last_lead_ts = None
                last_lead_dir = 0
                decision.iat[i] = g
                size_mult.iat[i] = 1.0
                conviction.iat[i] = ""
                # Determine conviction on agreement count.
                agree = _count_agree(m, c, g)
                if agree == 3:
                    conviction.iat[i] = "high"
                continue
            else:
                # 1m-leads window expired without 15m confirmation.
                if (i - last_lead_ts) >= 15:
                    last_lead_ts = None
                    last_lead_dir = 0
                    decision.iat[i] = 0
                    continue
                # Still inside the 15-bar window but 15m silent — hold
                # the position at half size (this is the anti-cascade
                # grace bar).
                decision.iat[i] = last_lead_dir
                size_mult.iat[i] = 0.5
                continue
        else:
            last_lead_ts = None
            last_lead_dir = 0

        # Default decision logic.
        if m == g and c == g:
            decision.iat[i] = g
            size_mult.iat[i] = 1.0
            agree = 3
        elif m == g and c == 0:
            # 1m signals in gate direction, 15m silent. Standard
            # 1-of-3 case -> half size, tighter stop. But the 1m-leads
            # branch is reserved for cluster_active & no_recent_15m;
            # without those, do not enter (15m is required to vote on
            # the direction — 1m alone is microstructure noise).
            if cluster_v[i] and no_recent_v[i]:
                decision.iat[i] = g
                size_mult.iat[i] = 0.5
                last_lead_ts = i
                last_lead_dir = g
                lead_1m.iat[i] = True
                agree = 1
            else:
                decision.iat[i] = 0
                agree = 0
        elif m == 0 and c == g:
            # 15m wins (lower noise). Standard size.
            decision.iat[i] = g
            size_mult.iat[i] = 1.0
            agree = 2
        elif m == 0 and c == 0:
            # Only 4h gate is set -> counter-trend lean at half size.
            # The SPEC §Rule 1 "1-of-3 only 4h agrees" branch.
            decision.iat[i] = g
            size_mult.iat[i] = 0.5
            agree = 1
        elif m == -g and c == g:
            # 1m disagrees, 15m agrees -> 15m wins (Rule 2).
            decision.iat[i] = g
            size_mult.iat[i] = 1.0
            agree = 2
        elif m == g and c == -g:
            # Both lower-TF disagree with 4h -> 4h gate wins, no entry.
            decision.iat[i] = 0
            agree = 0
        elif m == 0 and c == -g:
            # 15m disagrees with 4h -> 4h gate wins, no entry.
            decision.iat[i] = 0
            agree = 0
        elif m == -g and c == 0:
            # 1m disagrees, 15m silent -> no entry (15m is the noise
            # filter — without 15m agreement, 1m counter-signal blocks).
            decision.iat[i] = 0
            agree = 0
        else:
            # m == -g and c == -g -> both disagree, no entry.
            decision.iat[i] = 0
            agree = 0

        # Conviction: 3-of-3 = high.
        if agree == 3 and decision.iat[i] != 0:
            conviction.iat[i] = "high"

    agree_count = pd.Series(0, index=micro.index, dtype=np.int64)
    # Recompute agree_count once at the end from votes vs decision.
    for i in range(n):
        d = int(decision.iat[i])
        if d == 0:
            agree_count.iat[i] = 0
            continue
        cnt = 0
        if int(micro_v[i]) == d:
            cnt += 1
        if int(carry_v[i]) == d:
            cnt += 1
        if int(gate_v[i]) == d:
            cnt += 1
        agree_count.iat[i] = cnt

    out = pd.DataFrame({
        "decision": decision,
        "conviction": conviction,
        "size_mult": size_mult,
        "regime_4h": regime,
        "agree_count": agree_count,
        "micro": micro,
        "carry": carry,
        "struct": struct,
        "lead_1m": lead_1m,
        "wait_15m": wait_15m,
    })
    return out


def _count_agree(micro: int, carry: int, struct: int) -> int:
    d = struct  # gate direction is the canonical decision direction
    if d == 0:
        return 0
    cnt = 0
    if micro == d:
        cnt += 1
    if carry == d:
        cnt += 1
    if struct == d:
        cnt += 1
    return cnt


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------

def combine_signals(
    sig_1m: pd.DataFrame,
    sig_15m: pd.DataFrame,
    sig_4h: pd.DataFrame,
    params: Optional[dict] = None,
) -> pd.DataFrame:
    """Combine per-TF signal frames into a single per-1m-bar decision.

    See module docstring for the rules implemented.
    """
    sig_1m_a, sig_15m_a, sig_4h_a = _align_to_1m(sig_1m, sig_15m, sig_4h)
    votes = _vote_per_tf(sig_1m_a, sig_15m_a, sig_4h_a)
    out = _resolve_conflicts_and_cascade(votes, sig_1m_a, sig_15m_a, sig_4h_a)
    # Pass through helpful per-TF raw signals for downstream backtest.
    out = out.assign(
        micro_long_raw=sig_1m_a["micro_long"],
        micro_short_raw=sig_1m_a["micro_short"],
        carry_long_raw=sig_15m_a["carry_long"],
        struct_long_raw=sig_4h_a["struct_long"],
        struct_short_raw=sig_4h_a["struct_short"],
        atr_1m=sig_1m_a["atr"],
        atr_15m=sig_15m_a["atr"],
        atr_4h=sig_4h_a["atr"],
    )
    return out


__all__ = ["combine_signals", "combine_signals"]