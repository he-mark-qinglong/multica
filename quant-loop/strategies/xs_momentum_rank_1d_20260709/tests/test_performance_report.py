"""Tests for performance-report artifacts.

These tests verify only the side-effect files produced by the
performance-analyst run:
- ``results/factor_exposure.json`` has a ``"v2"`` key with valid
  numeric values for every required field.
- ``results/performance_report.md`` exists and contains at least one
  ``## `` markdown header.

The existing 26 tests (strategy, universe, portfolio, backtest) are
unaffected. These tests do not modify any artifacts.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

STRATEGY_DIR = Path(__file__).resolve().parent.parent
FACTOR_PATH = STRATEGY_DIR / "results" / "factor_exposure.json"
REPORT_PATH = STRATEGY_DIR / "results" / "performance_report.md"

REQUIRED_V2_KEYS = [
    "avg_daily_return",
    "annualized_volatility",
    "long_leg_pnl_contribution",
    "short_leg_pnl_contribution",
    "per_symbol_net_contribution",
    "weight_hhi_avg",
    "implied_avg_holding_period_days",
]

REQUIRED_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def test_factor_exposure_has_v2_key_with_valid_numeric_values():
    assert FACTOR_PATH.exists(), f"missing {FACTOR_PATH}"
    payload = json.loads(FACTOR_PATH.read_text())
    assert "v2" in payload, "factor_exposure.json missing 'v2' key"
    v2 = payload["v2"]
    assert isinstance(v2, dict), "'v2' must be a dict"

    # Required top-level scalar numeric fields (per_symbol_net_contribution
    # is a dict-of-numerics per the documented schema, validated separately).
    SCALAR_KEYS = [k for k in REQUIRED_V2_KEYS if k != "per_symbol_net_contribution"]
    for key in SCALAR_KEYS:
        assert key in v2, f"v2 missing required key: {key}"
        assert _is_number(v2[key]), (
            f"v2[{key}] must be a numeric value, got {type(v2[key]).__name__}: {v2[key]!r}"
        )

    # Per-symbol dict must contain all three active-universe symbols with numeric values
    per_sym = v2["per_symbol_net_contribution"]
    assert isinstance(per_sym, dict), "per_symbol_net_contribution must be a dict"
    for sym in REQUIRED_SYMBOLS:
        assert sym in per_sym, f"per_symbol_net_contribution missing {sym}"
        assert _is_number(per_sym[sym]), (
            f"per_symbol_net_contribution[{sym}] must be numeric, got {type(per_sym[sym]).__name__}"
        )

    # Existing keys must still be preserved (no destructive overwrite)
    assert "long" in payload, "existing 'long' key missing after v2 extension"
    assert "short" in payload, "existing 'short' key missing after v2 extension"

    # Sanity bounds (these are loose; they only catch obviously-wrong values)
    assert -0.05 < v2["avg_daily_return"] < 0.05, (
        f"avg_daily_return {v2['avg_daily_return']} outside plausible daily band"
    )
    assert 0.0 <= v2["annualized_volatility"] < 2.0, (
        f"annualized_volatility {v2['annualized_volatility']} outside plausible band"
    )
    assert 0.0 <= v2["weight_hhi_avg"] <= 1.0, (
        f"weight_hhi_avg {v2['weight_hhi_avg']} must lie in [0, 1]"
    )
    assert v2["implied_avg_holding_period_days"] > 0, (
        f"implied_avg_holding_period_days must be positive, got {v2['implied_avg_holding_period_days']}"
    )


def test_performance_report_md_exists_with_markdown_headers():
    assert REPORT_PATH.exists(), f"missing {REPORT_PATH}"
    content = REPORT_PATH.read_text()
    assert len(content) >= 30, "performance_report.md looks empty / too short"

    # Must contain at least one "## " markdown header
    headers = re.findall(r"^##\s+\S", content, flags=re.MULTILINE)
    assert headers, "performance_report.md contains no '## ' markdown headers"

    # Sanity: report must mention core concepts
    lowered = content.lower()
    assert "sharpe" in lowered or "headline" in lowered, (
        "performance_report.md does not reference sharpe or headline metrics"
    )