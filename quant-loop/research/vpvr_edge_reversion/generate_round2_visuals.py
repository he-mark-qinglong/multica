"""Generate mandatory visualization bundle for VPVR round-2 (SMA-36661).

This is a retroactive application of the visualization mandate to validate the
methodology before it becomes the default for all strategies.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

QUANT_LOOP = Path("/Users/mark/multica/quant-loop")
sys.path.insert(0, str(QUANT_LOOP))
sys.path.insert(0, str(QUANT_LOOP / "_shared"))

from visualization import StrategyVisualizer

DATA_DIR = QUANT_LOOP / "data/perp_1m"
OUT_DIR = QUANT_LOOP / "research/vpvr_edge_reversion/figures_round2"
COST_RT_BP = 2.8


def load_ohlc(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol}_1m.parquet"
    df = pd.read_parquet(path, columns=["open_time", "open", "high", "low", "close", "quote_volume"])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time").sort_index()
    return df.loc[start:end].copy()


def firsttouch_to_trades(df: pd.DataFrame) -> pd.DataFrame:
    """Convert round-2 firsttouch parquet rows into StrategyVisualizer trades format."""
    df = df.copy()
    df["entry_time"] = pd.to_datetime(df["window_end"], utc=True)
    df["exit_time"] = pd.to_datetime(df["fill_time"], utc=True)
    df["side"] = df["direction"]
    df["entry_price"] = df["entry_price"].astype(float)
    # Approximate exit price from markout bps (pre-cost) — sufficient for visual.
    df["exit_price"] = df["entry_price"] * (1 + df["scenario_b_markout_bps"] / 10000.0)
    df["pnl_bps"] = df["scenario_b_markout_net_bps"].astype(float)
    df["size"] = 1.0
    return df[["entry_time", "exit_time", "side", "entry_price", "exit_price", "pnl_bps", "size"]].copy()


def build_equity(trades: pd.DataFrame, freq: str = "1h") -> pd.DataFrame:
    """Build NAV curve from net trade returns."""
    trades = trades.sort_values("exit_time").copy()
    trades["ret"] = 1 + trades["pnl_bps"] / 10000.0
    # Aggregate multiple trades closing at the same timestamp.
    daily_ret = trades.groupby("exit_time")["ret"].prod()
    nav = daily_ret.cumprod()
    # Resample to regular index for plotting.
    idx = pd.date_range(start=nav.index.min(), end=nav.index.max(), freq=freq, tz="UTC")
    nav = nav.reindex(idx, method="ffill").fillna(1.0)
    return pd.DataFrame({"nav": nav}, index=idx)


def generate_for(symbol: str, horizon: str) -> None:
    parquet_path = QUANT_LOOP / f"research/vpvr_edge_reversion/round2_firsttouch_{symbol}_{horizon}.parquet"
    if not parquet_path.exists():
        print(f"skip missing {parquet_path}")
        return

    df = pd.read_parquet(parquet_path)
    # Drop setups that never filled (NaT exit_time).
    df = df[df["fill_time"].notna()].copy()
    trades = firsttouch_to_trades(df)

    # Long-only / short-only equity curves are built from filtered trades.
    equity = build_equity(trades)

    start = trades["entry_time"].min() - pd.Timedelta(hours=4)
    end = trades["exit_time"].max() + pd.Timedelta(hours=4)
    ohlc = load_ohlc(symbol, start, end)

    out_dir = OUT_DIR / f"{symbol}_{horizon}"
    out_dir.mkdir(parents=True, exist_ok=True)

    viz = StrategyVisualizer(
        ohlc_df=ohlc,
        trades_df=trades,
        equity_df=equity,
        output_dir=out_dir,
        symbol=symbol,
        cost_bps_rt=COST_RT_BP,
    )
    paths = viz.generate_all(max_trade_samples=150)
    print(f"{symbol} {horizon}: generated {len(paths)} files in {out_dir}")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        for horizon in ("4h", "1d"):
            generate_for(symbol, horizon)


if __name__ == "__main__":
    main()
