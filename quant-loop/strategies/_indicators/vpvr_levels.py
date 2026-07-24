"""Compatibility shim — canonical implementation moved to ``_shared.indicators.vpvr``.

Phase D of PLAN_20260724_hf_strategy_optimization: indicators live under
``_shared/indicators/``; this module re-exports the full public API so
existing ``from vpvr_levels import ...`` / ``from _indicators.vpvr_levels
import ...`` callers keep working unchanged. Do not add new code here.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make ``_shared`` importable even when only ``strategies/_indicators``
# is on sys.path (legacy sys.path.insert pattern used by older scripts).
_ROOT = Path(__file__).resolve().parents[2]  # quant-loop/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from _shared.indicators.vpvr import (  # noqa: E402,F401
    DEFAULT_HVN_QUANTILE,
    DEFAULT_LVN_QUANTILE,
    DEFAULT_NUM_HVN,
    DEFAULT_NUM_LVN,
    DEFAULT_PRICE_BINS,
    DEFAULT_VALUE_AREA_FRACTION,
    VolumeProfile,
    build_volume_profile,
    compute_vpvr_levels,
    find_hvn_lvn,
    find_poc,
    find_value_area,
)

__all__ = [
    "DEFAULT_VALUE_AREA_FRACTION",
    "DEFAULT_NUM_HVN",
    "DEFAULT_NUM_LVN",
    "DEFAULT_HVN_QUANTILE",
    "DEFAULT_LVN_QUANTILE",
    "DEFAULT_PRICE_BINS",
    "VolumeProfile",
    "build_volume_profile",
    "find_poc",
    "find_value_area",
    "find_hvn_lvn",
    "compute_vpvr_levels",
]
