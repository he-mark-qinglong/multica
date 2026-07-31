"""Maker pilot v1 — strategy entry point.

Loads config, delegates to ``_shared.market_making.maker_simulator``,
and returns trades + metrics compatible with the existing gate system.

Usage:
    python run_backtest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import pyarrow.dataset as ds

from _shared.market_making.maker_simulator import (
    MakerSimConfig,
    simulate_market_making,
)

CONFIG_PATH = Path(__file__).parent / "config.json"
DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "trades" / "BTCUSDT_aggtrades.parquet"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def build_sim_config(cfg: dict) -> MakerSimConfig:
    """Flatten nested config.json into a flat MakerSimConfig."""
    fv = cfg.get("fair_value", {})
    rp = cfg.get("reservation_price", {})
    q = cfg.get("quoting", {})
    inv = cfg.get("inventory", {})
    adv = cfg.get("adverse_selection", {})
    ex = cfg.get("exit", {})
    cost = cfg.get("cost_model", {})
    dw = cfg.get("data_window", {})

    return MakerSimConfig(
        symbol=cfg.get("symbol", "BTCUSDT"),
        vwap_lookback=fv.get("vwap_lookback", 20),
        vpvr_bars=fv.get("vpvr_bars", 200),
        spread_estimate_ticks=fv.get("spread_estimate_ticks", 2),
        gamma=rp.get("gamma", 0.1),
        sigma_window_seconds=rp.get("sigma_window_seconds", 60),
        horizon_seconds=rp.get("horizon_seconds", 300.0),
        base_spread_bp=q.get("base_spread_bp", 2.0),
        size_usd=q.get("size_usd", 1000.0),
        tick_size=q.get("tick_size", 0.01),
        inventory_skew_factor=q.get("inventory_skew_factor", 1.0),
        vol_spread_coeff=q.get("vol_spread_coeff", 0.5),
        max_inventory_usd=inv.get("max_inventory_usd", 5000.0),
        fill_penalty_bp=adv.get("fill_penalty_bp", 1.0),
        penalty_decay_per_second=adv.get("penalty_decay_per_second", 0.5),
        sweep_threshold=adv.get("sweep_threshold", 3),
        sweep_cooldown_seconds=adv.get("sweep_cooldown_seconds", 5.0),
        max_penalty_bp=adv.get("max_penalty_bp", 10.0),
        expected_sweep_cost_bp=adv.get("expected_sweep_cost_bp", 1.74),
        max_hold_seconds=ex.get("max_hold_seconds", 300.0),
        tp_bp=ex.get("tp_bp", 5.0),
        sl_bp=ex.get("sl_bp", 10.0),
        maker_fee_bp=cost.get("maker_fee_bp", 2.0),
        taker_fee_bp=cost.get("taker_fee_bp", 5.0),
        start_ts=dw.get("start_ts", "2026-04-19"),
        end_ts=dw.get("end_ts", "2026-04-22"),
    )


def load_aggtrades(config: MakerSimConfig) -> pd.DataFrame:
    """Load aggTrades parquet for the configured symbol."""
    symbol = config.symbol
    path = DATA_PATH.parent / f"{symbol}_aggtrades.parquet"
    if not path.exists():
        # Fallback to BTCUSDT
        path = DATA_PATH
    d = ds.dataset(str(path), format="parquet", partitioning="hive")
    start = pd.Timestamp(config.start_ts, tz="UTC")
    end = pd.Timestamp(config.end_ts, tz="UTC")
    flt = (ds.field("ts") >= start) & (ds.field("ts") < end)
    tbl = d.to_table(filter=flt, columns=["ts", "price", "qty", "is_buyer_maker"])
    df = tbl.to_pandas()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.sort_values("ts").reset_index(drop=True)


def run() -> dict:
    """Full pipeline: load data → simulate → return metrics."""
    cfg = load_config()
    sim_config = build_sim_config(cfg)
    aggtrades = load_aggtrades(sim_config)

    trades, metrics = simulate_market_making(aggtrades, sim_config)

    # Attach trades for downstream gate evaluation
    metrics["trades"] = trades
    metrics["config"] = cfg
    return metrics


if __name__ == "__main__":
    results = run()
    trades = results.pop("trades", [])
    cfg = results.pop("config", {})

    print(f"=== Maker Pilot v1 — {results.get('n_trades', 0)} round-trips ===")
    print(f"Symbol:          {cfg.get('symbol', 'BTCUSDT')}")
    print(f"Trades in:       {results.get('n_trades_in', 0):,}")
    print(f"Quotes gen:      {results.get('quotes_generated', 0):,}")
    print(f"Quotes filled:   {results.get('quotes_filled', 0):,}")
    print(f"Fill rate:       {results.get('fill_rate', 0):.2%}")
    print(f"Maker ratio:     {results.get('maker_ratio', 0):.2%}")
    print(f"---")
    print(f"Sharpe (daily):  {results.get('sharpe_daily', 0):.2f}")
    print(f"Ann return:      {results.get('annualized_return', 0):.2%}")
    print(f"Max DD:          {results.get('max_drawdown_pct', 0):.2%}")
    print(f"Profit factor:   {results.get('profit_factor', 0):.2f}")
    print(f"Avg pnl (bp):    {results.get('avg_pnl_bp', 0):.2f}")
    print(f"Win rate:        {results.get('win_rate', 0):.2%}")
    print(f"Exit reasons:    {results.get('exit_reasons', {})}")
    print(f"Elapsed:         {results.get('elapsed_seconds', 0):.1f}s")
