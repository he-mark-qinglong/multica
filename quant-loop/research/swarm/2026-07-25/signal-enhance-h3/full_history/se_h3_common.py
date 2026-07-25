"""Common plumbing for the signal-enhance-h3 full-history validation.

Single import point for the authoritative pipeline: everything data- or
config-related comes from ../H3-variants-h1h2h4/run_btcsol_variants_fixed.py
(bit-identical to the H3 baseline methodology) so no code is copied and
cannot drift. Read-only with respect to production/shared code.
"""
from __future__ import annotations

import sys
from pathlib import Path

FH_DIR = Path(__file__).resolve().parent                       # full_history/
VARIANTS_DIR = FH_DIR.parent.parent / "H3-variants-h1h2h4"     # sibling swarm dir

for p in (str(VARIANTS_DIR),):
    if p not in sys.path:
        sys.path.insert(0, p)

from run_btcsol_variants_fixed import (  # noqa: E402
    align_and_clip,        # L112
    fee_shock_metrics,     # L313
    load_config,           # L135
    load_funding,          # L101
    load_perp_1m,          # L90
    portfolio_metrics,     # L220
)

SYMBOLS = ("BTCUSDT", "SOLUSDT")

# Locked enhancement parameters (pre-registered, see SPEC_signal_enhance_h3_fullhist.md).
SE_H3_SLOPE_LOOKBACK = 4
SE_H3_SLOPE_SIGN = "favorable"
SE_H3_ADVERSE_STOP_Z = 0.7
SE_H3_REGIME_BREAK = 9.0  # effectively disables the wide regime_break stop


def load_aligned_data():
    """Authoritative BTC+SOL 1m klines + funding, aligned & clipped (baseline method).

    Returns (d1m, funding, common_idx): dicts keyed by symbol plus the
    common tz-naive DatetimeIndex. Expected: 2448219 bars,
    2021-11-20 16:01 -> 2026-07-17 19:39.
    """
    d1m = {s: load_perp_1m(s) for s in SYMBOLS}
    funding = {s: load_funding(s) for s in SYMBOLS}
    d1m, funding = align_and_clip(d1m, funding)
    return d1m, funding, d1m["BTCUSDT"].index


def load_se_h3_config() -> dict:
    """H3 config via the authoritative loader + locked se-h3 overrides."""
    cfg = load_config("H3")  # already forces BTC+SOL + 1bps/1bps cost model
    cfg["exit"]["regime_break_threshold"] = SE_H3_REGIME_BREAK
    cfg["indicators"]["regime_break_threshold"] = SE_H3_REGIME_BREAK
    cfg["se_h3"] = {
        "slope_lookback": SE_H3_SLOPE_LOOKBACK,
        "slope_sign": SE_H3_SLOPE_SIGN,
        "adverse_stop_z": SE_H3_ADVERSE_STOP_Z,
        "regime_break": SE_H3_REGIME_BREAK,
    }
    return cfg
