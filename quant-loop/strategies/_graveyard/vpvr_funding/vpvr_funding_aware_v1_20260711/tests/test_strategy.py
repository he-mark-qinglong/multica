"""Unit tests for vpvr_funding_aware_v1_20260711 (V8 Rule A rev2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_loader import load_symbol  # noqa: E402
from strategy import (  # noqa: E402
    VARIANT_KEY,
    CarryLedger,
    Trade,
    build_signal,
    funding_sum_24h,
    funding_vol_bps,
    run_backtest,
    wilder_atr,
)


def _toy_4h(n: int = 400, seed: int = 7, funding_pattern: str = "neg_then_pos",
            flat_prices: bool = False) -> pd.DataFrame:
    """Build a synthetic 4h OHLCV+funding frame for invariant checks.

    `funding_pattern`:
      - "always_neg": every funding_bps value is -2.0
      - "neg_then_pos": first half negative, second half positive (funding reversal)
      - "all_pos": every funding_bps value is +2.0
    `flat_prices`: if True, close path is flat (no returns) so the only exit is
    funding-driven (used for the funding-reversal test).
    """
    rng = np.random.default_rng(seed)
    if flat_prices:
        close = np.full(n, 100.0)
    else:
        rets = rng.normal(0.0005, 0.012, size=n)
        close = 100.0 * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0.001, 0.0005, size=n)))
    low = close * (1 - np.abs(rng.normal(0.001, 0.0005, size=n)))
    open_ = close * (1 + rng.normal(0.0, 0.0005, size=n))
    volume = np.abs(rng.normal(1_000, 200, size=n))

    if funding_pattern == "always_neg":
        fund_bps = np.full(n, -2.0)
    elif funding_pattern == "neg_then_pos":
        fund_bps = np.concatenate([np.full(n // 2, -2.0), np.full(n - n // 2, +6.0)])
    elif funding_pattern == "all_pos":
        fund_bps = np.full(n, +2.0)
    else:  # noqa
        fund_bps = np.zeros(n)
    fund_rate = fund_bps / 10000.0

    idx = pd.date_range("2024-01-01", periods=n, freq="4h")
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "fundingRate": fund_rate, "funding_bps": fund_bps,
    }, index=idx)


def _load_cfg() -> dict:
    return json.loads((ROOT / "config.json").read_text())


def test_strategy_key_constant():
    assert VARIANT_KEY == "vpvr_funding_aware_v1_20260711"


def test_wilder_atr_warmup():
    df = _toy_4h(120, funding_pattern="always_neg")
    atr = wilder_atr(df, 14).to_numpy()
    assert np.all(np.isfinite(atr[14:]))
    assert np.all(atr[14:] > 0)


def test_funding_sum_24h_and_vol_shape():
    df = _toy_4h(120, funding_pattern="always_neg")
    s = funding_sum_24h(df, 6).to_numpy()
    v = funding_vol_bps(df, 42).to_numpy()
    # Warmup: 6 for sum, 42 for vol.
    assert np.all(np.isfinite(s[6:]))
    assert np.all(np.isfinite(v[42:]))


def test_build_signal_long_only_when_funding_neg_and_vol_ok():
    df = _toy_4h(400, funding_pattern="always_neg")
    cfg = _load_cfg()
    sig = build_signal(df, cfg).to_numpy()
    # Funding is always -2 bps, vol is bounded; signal should be all 1s after warmup
    # (modulo min-gap bars_between_trades which just throttles entries).
    assert (sig[60:] >= 0).all()
    assert sig.sum() > 0


def test_build_signal_blocked_when_funding_positive():
    df = _toy_4h(400, funding_pattern="all_pos")
    cfg = _load_cfg()
    sig = build_signal(df, cfg).to_numpy()
    # Funding always +2 bps → funding_sum_24h > 0 → no entries.
    assert sig.sum() == 0


def test_carry_ledger_long_with_negative_funding_pays_positive_carry():
    ledger = CarryLedger()
    ledger.apply_event(funding_rate=-0.0002, units=10.0, mark=100.0, notional=10 * 100.0)
    # long position pays in when funding is negative (shorts pay longs)
    assert ledger.cum_carry_pct > 0.0


def test_carry_ledger_long_with_positive_funding_pays_negative_carry():
    ledger = CarryLedger()
    ledger.apply_event(funding_rate=+0.0002, units=10.0, mark=100.0, notional=10 * 100.0)
    assert ledger.cum_carry_pct < 0.0


def test_run_backtest_always_neg_funding_generates_long_trades():
    df = _toy_4h(800, funding_pattern="always_neg")
    cfg = _load_cfg()
    cfg["_symbol"] = "TEST"
    res = run_backtest(df, cfg)
    assert res["n_bars"] == 800
    assert len(res["trades"]) > 0
    t = res["trades"][0]
    assert isinstance(t, Trade)
    assert t.direction == "long"
    assert t.entry_ts < t.exit_ts
    assert t.funding_sum_24h_bps_at_entry < 0.0


def test_run_backtest_funding_reversal_triggers_exit():
    """Funding pattern: neg then pos → entries happen early, exits via funding_reversal later.

    Flat price path means hard_stop / time_stop don't fire; the only exit
    is the funding reversal once the second-half funding_bps>=+5.
    """
    df = _toy_4h(800, funding_pattern="neg_then_pos", flat_prices=True)
    cfg = _load_cfg()
    cfg["_symbol"] = "TEST"
    res = run_backtest(df, cfg)
    assert len(res["trades"]) > 0
    reasons = [t.exit_reason for t in res["trades"]]
    assert "funding_reversal" in reasons


def test_run_backtest_end_to_end_real_parquet():
    p = ROOT / "data" / "BTCUSDT__4h.parquet"
    if not p.is_file():
        pytest.skip("4h parquet cache not built yet")
    df = load_symbol("BTCUSDT")
    assert "funding_bps" in df.columns and "fundingRate" in df.columns
    cfg = _load_cfg()
    cfg["_symbol"] = "BTCUSDT"
    res = run_backtest(df, cfg)
    assert res["n_bars"] == len(df)
    assert "trades" in res and "equity" in res
