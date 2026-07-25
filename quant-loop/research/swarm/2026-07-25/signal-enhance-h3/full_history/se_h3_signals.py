"""se-h3 signals: baseline H3 signals + favorable 15m z-slope column.

Read-only imports from the production base module; nothing here mutates
shared code. Key naming: the slope column is `z_slope_fav_4`, NEVER
`z_slope_15m` (that key triggers the base engine's ADVERSE H1 filter,
which is the exact opposite convention — see round2 card w4-s2).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# sys.path bootstrap (self-contained; do not rely on se_h3_common).
QL_ROOT = Path(__file__).resolve().parents[5]  # quant-loop root
_STRAT = QL_ROOT / "strategies"
for _p in (str(_STRAT), str(_STRAT / "_indicators")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mtf_xs_pairs_base_20260718 import (  # noqa: E402
    aggregate_ohlcv,
    align_lower_to_upper,
    build_h3_signals,
    zscore_slope,
)

SLOPE_LOOKBACK = 4          # locked by pre-registered SPEC
SLOPE_KEY = "z_slope_fav_4"  # never "z_slope_15m" (adverse-hook collision)

__all__ = ["build_se_h3_signals", "SLOPE_LOOKBACK", "SLOPE_KEY"]


def build_se_h3_signals(d1m: dict, cfg: dict, funding: dict) -> dict:
    """Baseline H3 signals (base L318-381) + favorable slope column.

    Mirrors run_experiments.enhance_signals L68-72 exactly:
    pair z (1m) -> aggregate to 15min -> zscore_slope(., 4) -> ffill to 1m.
    """
    sigs = build_h3_signals(d1m, cfg, funding)
    for pair in cfg["pairs"]:
        z = sigs[pair]["z"]
        z_15m = aggregate_ohlcv(z.rename("z").to_frame(), "15min")["z"]
        slope_15m = zscore_slope(z_15m, SLOPE_LOOKBACK).rename(SLOPE_KEY)
        # align onto the pair's own 1m index (== sigs[pair]["a"].index)
        sigs[pair][SLOPE_KEY] = align_lower_to_upper(sigs[pair]["a"], slope_15m)
    return sigs