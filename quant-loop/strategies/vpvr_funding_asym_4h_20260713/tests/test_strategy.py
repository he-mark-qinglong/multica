"""Smoke tests for vpvr_funding_asym_4h_20260713 (V3_funding_asym, iter#92)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import VARIANT_KEY, _run_one_symbol, run_backtest


def _synth_df(n: int = 500, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2024-01-01", periods=n, freq="4h")
    px = 100 + np.cumsum(rng.normal(0, 0.5, n))
    df = pd.DataFrame({
        "open": px, "high": px + 0.5, "low": px - 0.5, "close": px,
        "volume": rng.uniform(100, 200, n),
        "quote_volume": rng.uniform(1e6, 2e6, n),
        "trades": rng.integers(100, 500, n),
        "taker_buy_base": rng.uniform(50, 100, n),
        "taker_buy_quote": rng.uniform(50000, 100000, n),
        "fundingRate": rng.normal(0, 0.0005, n),
    }, index=ts)
    df["fundingAnnBps"] = df["fundingRate"] * 3.0 * 365.0 * 10000.0
    df.index.name = "ts"
    return df


def test_variant_key_format():
    assert VARIANT_KEY == "vpvr_funding_asym_4h_20260713"


def test_run_one_symbol_returns_required_keys():
    df = _synth_df(500)
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["instruments"] = ["BTCUSDT"]
    cfg["starting_capital_per_symbol_usd"] = 100000.0
    res = _run_one_symbol(df, cfg)
    assert set(res.keys()) >= {
        "variant_key", "iteration", "symbol", "n_bars",
        "span_start", "span_end", "trades", "equity",
    }
    assert res["variant_key"] == VARIANT_KEY
    assert res["n_bars"] == 500
    assert res["symbol"] == "BTCUSDT"


def test_run_backtest_picks_first_symbol():
    df = _synth_df(500)
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["instruments"] = ["BTCUSDT"]
    res = run_backtest({"BTCUSDT": df}, cfg)
    assert res["symbol"] == "BTCUSDT"


def test_config_required_fields():
    cfg = json.loads((ROOT / "config.json").read_text())
    assert cfg["iteration"] == 92
    assert cfg["timeframe"] == "4h"
    assert set(cfg["instruments"]) >= {"BTCUSDT", "ETHUSDT"}
    p = cfg["params"]
    for k in ("asymmetric_take_profit_atr_k", "asymmetric_hard_stop_atr_k",
              "funding_annualized_basis_bps_threshold",
              "funding_carry_bps_per_bar"):
        assert k in p, k
    # Asymmetric TP:SL — TP must be > SL.
    assert p["asymmetric_take_profit_atr_k"] > p["asymmetric_hard_stop_atr_k"]
