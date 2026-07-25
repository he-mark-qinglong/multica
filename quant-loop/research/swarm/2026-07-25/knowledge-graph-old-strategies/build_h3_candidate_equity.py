#!/usr/bin/env python3
"""Generate 2024 daily equity curves for the H3 baseline and the slope+fav+stop candidate.

Uses the existing signal-enhance-h3 harness but swaps the data loader for a
duckdb-based reader so it runs in environments without pyarrow/fastparquet.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
QH = HERE.parent / "signal-enhance-h3"
if not QH.exists():
    raise FileNotFoundError(f"Expected signal-enhance-h3 harness at {QH}")

sys.path.insert(0, str(QH))

from run_experiments import (  # noqa: E402
    backtest_variant,
    build_portfolio,
    enhance_signals,
    load_config,
    metrics_from_result,
)

ROOT = Path("/Users/mark/multica/quant-loop")
PERP_1M_DIR = ROOT / "data" / "perp_1m"
BTC_FUNDING = ROOT / "funding_analysis" / "BTCUSDT_funding.parquet"
SOL_FUNDING = ROOT / "strategies" / "_graveyard" / "xs_pairs_30m" / "vpvr_xs_pairs_30m_funding_filter_20260712" / "data" / "SOLUSDT__funding.parquet"


def _read_1m(symbol: str) -> pd.DataFrame:
    p = PERP_1M_DIR / f"{symbol}_1m.parquet"
    con = duckdb.connect()
    cols = ["open_time", "open", "high", "low", "close", "volume"]
    df = con.execute(f"SELECT {', '.join(cols)} FROM read_parquet('{p}')").fetchdf()
    con.close()
    idx = pd.DatetimeIndex(pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)).tz_convert(None)
    df.index = idx
    df.index.name = "openTime"
    return df.sort_index()[cols[1:]]  # keep OHLCV, drop open_time column


def _read_funding_btc() -> pd.Series:
    con = duckdb.connect()
    df = con.execute(f"SELECT fundingTime, fundingRate FROM read_parquet('{BTC_FUNDING}')").fetchdf()
    con.close()
    idx = pd.DatetimeIndex(pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms", utc=True)).tz_convert(None)
    s = pd.Series(df["fundingRate"].astype(float).to_numpy(), index=idx, name="fundingRate")
    return s.sort_index()


def _read_funding_sol() -> pd.Series:
    con = duckdb.connect()
    df = con.execute(f"SELECT ts, fundingRate FROM read_parquet('{SOL_FUNDING}')").fetchdf()
    con.close()
    idx = pd.DatetimeIndex(pd.to_datetime(df["ts"], utc=True)).tz_convert(None)
    s = pd.Series(df["fundingRate"].astype(float).to_numpy(), index=idx, name="fundingRate")
    return s.sort_index()


def _slice_by_date(d1m: dict[str, pd.DataFrame], funding: dict[str, pd.Series],
                   start: str | None = "2022-01-01", end: str | None = None):
    d1m_s = {}
    for sym, df in d1m.items():
        mask = pd.Series(True, index=df.index)
        if start:
            mask &= df.index >= pd.Timestamp(start)
        if end:
            mask &= df.index <= pd.Timestamp(end)
        d1m_s[sym] = df.loc[mask].copy()
    fund_s = {}
    for sym, s in funding.items():
        mask = pd.Series(True, index=s.index)
        if start:
            mask &= s.index >= pd.Timestamp(start)
        if end:
            mask &= s.index <= pd.Timestamp(end)
        fund_s[sym] = s.loc[mask].copy()
    return d1m_s, fund_s


def _daily_equity_from_bars(bar_idx: pd.DatetimeIndex, equity: list[float]) -> pd.Series:
    s = pd.Series(equity, index=bar_idx[:len(equity)], name="equity")
    return s.resample("D").last().dropna()


def main() -> None:
    cfg = load_config()
    d1m_raw = {"BTCUSDT": _read_1m("BTCUSDT"), "SOLUSDT": _read_1m("SOLUSDT")}
    funding_raw = {"BTCUSDT": _read_funding_btc(), "SOLUSDT": _read_funding_sol()}
    d1m, funding = _slice_by_date(d1m_raw, funding_raw, start="2024-01-01", end="2024-12-31")
    print("2024 bars BTC:", len(d1m["BTCUSDT"]), "SOL:", len(d1m["SOLUSDT"]))

    signals = enhance_signals(d1m, cfg, funding)

    variants = [
        ("h3_baseline_2024", {}),
        ("h3_slope_fav_4_stop_0_7_2024", {
            "slope_filter": {"lookback": 4, "sign": "favorable"},
            "adverse_stop_z": 0.7,
            "regime_break": 9.0,
        }),
    ]

    out = {}
    for name, params in variants:
        res = backtest_variant(signals, cfg, params)
        port = build_portfolio([res], starting_capital=float(cfg.get("starting_capital_usd", 100_000.0)))
        bar_idx = res["index"][:port["n_bars"]]
        port_res = {
            "pair": res["pair"],
            "trades": res["trades"],
            "bar_return": port["bar_return"],
            "n_bars": port["n_bars"],
            "equity": port["equity"],
            "index": bar_idx,
        }
        m = metrics_from_result(port_res, cfg)
        daily = _daily_equity_from_bars(bar_idx, port["equity"])
        csv_path = HERE / f"equity_{name}.csv"
        daily.to_csv(csv_path, header=True)
        out[name] = {
            "csv": str(csv_path.relative_to(HERE)),
            "n_trades": int(m["n_trades"]),
            "sharpe_daily_resampled": float(m["sharpe_daily_resampled"]),
            "annualized_return_daily": float(m["annualized_return_daily"]),
            "max_drawdown_pct": float(m["max_drawdown_pct"]),
            "profit_factor": float(m["profit_factor"]),
        }
        print(name, out[name])

    (HERE / "candidate_metrics_2024.json").write_text(json.dumps(out, indent=2, default=float))
    print("Saved candidate equity + metrics.")


if __name__ == "__main__":
    main()
