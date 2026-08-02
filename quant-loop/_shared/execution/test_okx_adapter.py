"""Tests for _shared/execution/okx_adapter.py (E8)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import pytest

from _shared.execution.cost_model import Venue
from _shared.execution.okx_adapter import OKXAdapter, OKXConfig, okx_venue


# ---------------------------------------------------------------------------
# OKXConfig
# ---------------------------------------------------------------------------

def test_okx_config_defaults():
    """Default config should match OKX VIP1 fees (maker 2bp, taker 5bp)."""
    cfg = OKXConfig()
    assert cfg.maker_fee_bps == 2.0
    assert cfg.taker_fee_bps == 5.0


def test_okx_config_is_frozen():
    """OKXConfig should be immutable."""
    cfg = OKXConfig()
    with pytest.raises(Exception):
        cfg.maker_fee_bps = 0.0


def test_okx_config_custom():
    """Custom config values should be accepted."""
    cfg = OKXConfig(maker_fee_bps=0.0, taker_fee_bps=3.0)
    assert cfg.maker_fee_bps == 0.0
    assert cfg.taker_fee_bps == 3.0


# ---------------------------------------------------------------------------
# okx_venue
# ---------------------------------------------------------------------------

def test_okx_venue_returns_venue():
    """okx_venue should return a cost_model.Venue."""
    v = okx_venue()
    assert isinstance(v, Venue)
    assert v.name == "okx_swap"


def test_okx_venue_default_fees():
    """Default venue should have VIP1 fees."""
    v = okx_venue()
    assert v.taker_fee_bps == 5.0
    assert v.maker_fee_bps == 2.0
    assert v.has_bnb_discount is False
    assert v.fixed_pure_slippage_bps is None


def test_okx_venue_custom_config():
    """Custom config should propagate to venue."""
    cfg = OKXConfig(maker_fee_bps=1.0, taker_fee_bps=4.0)
    v = okx_venue(cfg)
    assert v.maker_fee_bps == 1.0
    assert v.taker_fee_bps == 4.0


# ---------------------------------------------------------------------------
# OKXAdapter
# ---------------------------------------------------------------------------

def test_okx_adapter_default_config():
    """Default adapter uses VIP1 config."""
    adapter = OKXAdapter()
    assert adapter.config.maker_fee_bps == 2.0
    assert adapter.config.taker_fee_bps == 5.0


def test_okx_adapter_venue_property():
    """Adapter.venue should return a Venue."""
    adapter = OKXAdapter()
    v = adapter.venue
    assert isinstance(v, Venue)
    assert v.name == "okx_swap"


def test_okx_adapter_fee_bps_maker():
    """Maker fee should be 2bp."""
    adapter = OKXAdapter()
    assert adapter.fee_bps("maker") == 2.0


def test_okx_adapter_fee_bps_taker():
    """Taker fee should be 5bp."""
    adapter = OKXAdapter()
    assert adapter.fee_bps("taker") == 5.0


def test_okx_adapter_fee_bps_invalid_side():
    """Invalid side should raise ValueError."""
    adapter = OKXAdapter()
    with pytest.raises(ValueError, match="maker"):
        adapter.fee_bps("invalid")


def test_okx_adapter_round_trip_fee_taker():
    """Round-trip taker fee = 2 × 5bp = 10bp."""
    adapter = OKXAdapter()
    assert adapter.round_trip_fee_bps("taker") == 10.0


def test_okx_adapter_round_trip_fee_maker():
    """Round-trip maker fee = 2 × 2bp = 4bp."""
    adapter = OKXAdapter()
    assert adapter.round_trip_fee_bps("maker") == 4.0


def test_okx_adapter_round_trip_cost_with_slippage():
    """RT cost should include fees + 2 × slippage."""
    adapter = OKXAdapter()
    # taker: 10bp fee + 2 × 3bp slip = 16bp
    assert adapter.round_trip_cost_bps("taker", slippage_bps=3.0) == 16.0


def test_okx_adapter_round_trip_cost_no_slippage():
    """RT cost with zero slippage = round-trip fee only."""
    adapter = OKXAdapter()
    assert adapter.round_trip_cost_bps("taker", slippage_bps=0.0) == 10.0


def test_okx_adapter_custom_config_propagates():
    """Custom config should propagate through adapter."""
    cfg = OKXConfig(maker_fee_bps=0.0, taker_fee_bps=3.0)
    adapter = OKXAdapter(cfg)
    assert adapter.fee_bps("maker") == 0.0
    assert adapter.fee_bps("taker") == 3.0
    assert adapter.round_trip_fee_bps("taker") == 6.0


def test_okx_adapter_describe():
    """describe() should return a non-empty string."""
    adapter = OKXAdapter()
    desc = adapter.describe()
    assert isinstance(desc, str)
    assert "OKX" in desc
    assert "maker=2" in desc
    assert "taker=5" in desc


def test_okx_venue_matches_cost_model_okx_perp():
    """okx_venue should match the OKX_PERP constant in cost_model."""
    from _shared.execution.cost_model import OKX_PERP
    v = okx_venue()
    assert v.name == OKX_PERP.name
    assert v.taker_fee_bps == OKX_PERP.taker_fee_bps
    assert v.maker_fee_bps == OKX_PERP.maker_fee_bps
