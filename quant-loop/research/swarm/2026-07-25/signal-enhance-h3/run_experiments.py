"""signal-enhance-h3 experiments.

Diagnose why the H3 BTC/SOL pair signal loses money at the trade level
(mean net -7.8 bps, win rate 27%) and test verifiable entry/exit/filter
improvements.

All code lives in the swarm research directory; the production strategy and
shared modules are only imported read-only.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# Make shared strategy modules importable without touching them.
HERE = Path(__file__).resolve().parent
STRATEGIES = HERE.parents[3] / "strategies"
SHARED = HERE.parents[3] / "_shared"
sys.path.insert(0, str(STRATEGIES))
sys.path.insert(0, str(STRATEGIES / "_indicators"))
sys.path.insert(0, str(SHARED / "validation"))

import data_loader_patch as dlp  # noqa: E402
from mtf_xs_pairs_base_20260718 import (  # noqa: E402
    aggregate_ohlcv,
    align_lower_to_upper,
    build_h3_signals,
    pair_zscore,
    sharpe_daily_resampled,
    wilder_atr,
    zscore_slope,
)
from compute_metrics import compute_metrics  # noqa: E402

OUT = HERE
OUT.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = STRATEGIES / "mtf_xs_pairs_1m_15m_2h_h3_20260718" / "config.json"

FREQ_PER_YEAR_1M = 365 * 24 * 60


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def enhance_signals(d1m: dict[str, pd.DataFrame], cfg: dict, funding: dict[str, pd.Series]) -> dict:
    """Build H3 signals plus extra diagnostics needed for variants."""
    # Baseline H3 signal builder (mutates copies internally, safe).
    sigs = build_h3_signals(d1m, cfg, funding)

    ind = cfg["indicators"]
    pair = cfg["pairs"][0]
    a_sym, b_sym = pair.split("/")
    a = d1m[a_sym]
    b = d1m[b_sym]
    common = a.index.intersection(b.index)
    a = a.loc[common]
    b = b.loc[common]

    z = sigs[pair]["z"]

    # 15m z-slope (same machinery as H1) — used for entry confirmation.
    z_15m = aggregate_ohlcv(z.rename("z").to_frame(), "15min")["z"]
    z_slope_4 = align_lower_to_upper(a, zscore_slope(z_15m, 4).rename("z_slope_4"))
    z_slope_8 = align_lower_to_upper(a, zscore_slope(z_15m, 8).rename("z_slope_8"))

    # 1m spread return for candle confirmation and vol filter.
    a_ret = a["close"].pct_change().rename("a_ret")
    b_ret = b["close"].pct_change().rename("b_ret")
    spread_ret = (a_ret - b_ret).rename("spread_ret")

    # Funding differential (2h EMA of a minus b) for differential filter.
    fund_ema_a = _fund_ema_2h(a, funding.get(a_sym), int(ind["funding_ema_window"]))
    fund_ema_b = _fund_ema_2h(b, funding.get(b_sym), int(ind["funding_ema_window"]))
    fund_diff = (fund_ema_a - fund_ema_b).rename("fund_diff")

    sigs[pair].update({
        "z_slope_4": z_slope_4,
        "z_slope_8": z_slope_8,
        "spread_ret": spread_ret,
        "fund_diff": fund_diff,
    })
    return sigs


def _fund_ema_2h(df: pd.DataFrame, f: Optional[pd.Series], span: int) -> pd.Series:
    """Return 2h-forward-filled funding EMA aligned to df.index."""
    if f is None or len(f) == 0:
        return pd.Series(0.0, index=df.index, name="fund_ema")
    f = f.copy()
    if f.index.tz is not None:
        f.index = f.index.tz_convert(None)
    ema_e = f.ewm(span=max(span, 2), adjust=False).mean()
    ema_2h = ema_e.resample("2h", closed="left", label="left").mean().dropna()
    return ema_2h.reindex(df.index, method="ffill").fillna(0.0)


# Backtest one variant using the pre-computed (enhanced) signals.
# ---------------------------------------------------------------------------

def _cost_rt(cfg: dict) -> float:
    fee = float(cfg.get("fees_bps_per_side", 1.0))
    slip = float(cfg.get("slippage_bps_per_side", 1.0))
    return 2.0 * 2.0 * (fee + slip) / 10_000.0


def backtest_variant(signals: dict, cfg: dict, params: Dict[str, Any]) -> dict:
    """Run one backtest variant.

    Parameters
    ----------
    params keys:
        - z_entry, z_exit, max_hold, regime_break: overrides
        - slope_filter: {"lookback": 4|8, "sign": "favorable"|"adverse"|None}
        - adverse_stop_z: float or None
        - candle_confirm: bool
        - funding_diff_filter: bool
    """
    pair = cfg["pairs"][0]
    sig = signals[pair]
    a = sig["a"]
    b = sig["b"]
    common = a.index
    n = len(common)

    z = sig["z"]
    fund_allow = sig["fund_allow"]
    size_scale = sig.get("size_scale")

    z_entry = float(params.get("z_entry", sig["params"]["z_entry"]))
    z_exit = float(params.get("z_exit", sig["params"]["z_exit"]))
    regime_break = float(params.get("regime_break", sig["params"].get("regime_break", 3.0)))
    max_hold = int(params.get("max_hold", sig["params"]["max_hold"]))

    slope_filter = params.get("slope_filter")
    adverse_stop_z = params.get("adverse_stop_z")
    candle_confirm = bool(params.get("candle_confirm", False))
    funding_diff_filter = bool(params.get("funding_diff_filter", False))

    if slope_filter:
        lb = slope_filter.get("lookback", 4)
        slope = sig.get(f"z_slope_{lb}")
    else:
        slope = None

    spread_ret = sig.get("spread_ret")
    fund_diff = sig.get("fund_diff")

    fee_bps = float(cfg.get("fees_bps_per_side", 1.0))
    slip_bps = float(cfg.get("slippage_bps_per_side", 1.0))
    cost_rt = 2.0 * 2.0 * (fee_bps + slip_bps) / 10_000.0

    pnl_per_bar = np.zeros(n)
    trades: List[dict] = []
    pos = 0
    bars_held = 0
    entry_idx = None
    entry_a = entry_b = entry_z = None

    for i in range(1, n):
        zi = float(z.iat[i]) if np.isfinite(z.iat[i]) else None

        if pos == 0 and zi is not None:
            direction = 0
            if zi <= -z_entry:
                direction = +1
            elif zi >= +z_entry:
                direction = -1

            allow = True
            if int(fund_allow.iat[i]) == 0:
                allow = False

            if allow and direction != 0 and slope is not None:
                sl = float(slope.iat[i]) if np.isfinite(slope.iat[i]) else None
                sign = slope_filter.get("sign", "favorable")
                if sign == "favorable":
                    # long_a_short_b expects z rising from a negative extreme
                    if direction == +1 and (sl is None or sl <= 0):
                        allow = False
                    if direction == -1 and (sl is None or sl >= 0):
                        allow = False
                elif sign == "adverse":
                    # H1 convention: enter while z still running into the extreme
                    if direction == +1 and (sl is None or sl >= 0):
                        allow = False
                    if direction == -1 and (sl is None or sl <= 0):
                        allow = False

            if allow and candle_confirm and spread_ret is not None:
                sr = float(spread_ret.iat[i]) if np.isfinite(spread_ret.iat[i]) else 0.0
                # Direction of the signal candle should agree with the trade.
                if direction == +1 and sr <= 0:
                    allow = False
                if direction == -1 and sr >= 0:
                    allow = False

            if allow and funding_diff_filter and fund_diff is not None:
                fd = float(fund_diff.iat[i]) if np.isfinite(fund_diff.iat[i]) else 0.0
                # Long a / short b is cheaper when a funding < b funding (fd < 0).
                if direction == +1 and fd >= 0:
                    allow = False
                if direction == -1 and fd <= 0:
                    allow = False

            if allow and direction != 0:
                pos = direction
                entry_idx = i
                entry_a = float(a["close"].iat[i])
                entry_b = float(b["close"].iat[i])
                entry_z = zi
                bars_held = 1
        elif pos != 0:
            bars_held += 1
            a_ret_i = float(a["close"].iat[i]) / float(a["close"].iat[i - 1]) - 1.0
            b_ret_i = float(b["close"].iat[i]) / float(b["close"].iat[i - 1]) - 1.0
            scale = float(size_scale.iat[i]) if size_scale is not None and np.isfinite(size_scale.iat[i]) else 1.0
            pnl_per_bar[i] = pos * (a_ret_i - b_ret_i) / 2.0 * scale

            exit_reason = None
            if abs(zi) <= z_exit:
                exit_reason = "z_mean_revert"
            if exit_reason is None and ((pos == +1 and zi <= -regime_break) or (pos == -1 and zi >= +regime_break)):
                exit_reason = "regime_break"
            if exit_reason is None and adverse_stop_z is not None:
                if pos == +1 and zi <= entry_z - adverse_stop_z:
                    exit_reason = "adverse_stop"
                if pos == -1 and zi >= entry_z + adverse_stop_z:
                    exit_reason = "adverse_stop"
            if exit_reason is None and bars_held >= max_hold:
                exit_reason = "max_holding"

            if exit_reason:
                exit_a = float(a["close"].iat[i])
                exit_b = float(b["close"].iat[i])
                if pos == +1:
                    gross = (exit_a / entry_a - 1.0) - (exit_b / entry_b - 1.0)
                else:
                    gross = -(exit_a / entry_a - 1.0) + (exit_b / entry_b - 1.0)
                net = gross - cost_rt
                trades.append({
                    "direction": "long_a_short_b" if pos == +1 else "short_a_long_b",
                    "entry_ts": common[i],
                    "exit_ts": common[i],
                    "entry_price_a": entry_a,
                    "entry_price_b": entry_b,
                    "exit_price_a": exit_a,
                    "exit_price_b": exit_b,
                    "gross_pct": gross,
                    "net_pct": net,
                    "bars_held": bars_held,
                    "z_at_entry": entry_z,
                    "z_at_exit": zi,
                    "exit_reason": exit_reason,
                })
                pos = 0
                bars_held = 0
                entry_idx = entry_a = entry_b = entry_z = None

    equity = np.empty(n)
    equity[0] = float(cfg.get("starting_capital_usd", 100_000.0))
    for i in range(1, n):
        equity[i] = equity[i - 1] * (1.0 + pnl_per_bar[i])

    return {
        "pair": pair,
        "trades": trades,
        "bar_return": pnl_per_bar,
        "n_bars": n,
        "equity": equity,
        "index": common,
    }


def build_portfolio(per_pair: List[dict], starting_capital: float = 100_000.0) -> dict:
    n_bars = min(p["n_bars"] for p in per_pair) if per_pair else 0
    if n_bars == 0:
        return {"equity": np.zeros(0), "bar_return": np.zeros(0), "n_bars": 0}
    returns = np.mean([p["bar_return"][:n_bars] for p in per_pair], axis=0)
    equity = np.empty(n_bars)
    equity[0] = starting_capital
    for i in range(1, n_bars):
        equity[i] = equity[i - 1] * (1.0 + returns[i])
    return {"equity": equity, "bar_return": returns, "n_bars": n_bars}


def metrics_from_result(res: dict, cfg: dict) -> dict:
    idx = res["index"]
    equity = pd.Series(res["equity"], index=idx)
    n_trades = len(res["trades"])
    # 9-key bar-based metrics (for gates), with per-trade win rate.
    trade_pnls = [t["net_pct"] for t in res["trades"]]
    m = compute_metrics(equity, n_trades, freq_per_year=FREQ_PER_YEAR_1M, trade_pnls=trade_pnls)
    # Daily-resampled Sharpe (campaign standard).
    sr = sharpe_daily_resampled(res["bar_return"], idx)
    m["sharpe_daily_resampled"] = sr["sharpe_daily_resampled"]
    m["annualized_return_daily"] = sr["annualized_return_daily"]
    m["n_days"] = sr["n_days"]
    # Trade-level stats.
    if trade_pnls:
        m["mean_net_pct"] = float(np.mean(trade_pnls))
        m["mean_gross_pct"] = float(np.mean([t["gross_pct"] for t in res["trades"]]))
        m["win_rate"] = float(np.mean(np.array(trade_pnls) > 0))
        m["median_bars_held"] = float(np.median([t["bars_held"] for t in res["trades"]]))
    else:
        m.update({"mean_net_pct": 0.0, "mean_gross_pct": 0.0, "win_rate": 0.0, "median_bars_held": 0.0})
    return m


def run_all():
    cfg = load_config()
    print("Loading data ...")
    d1m_raw = dlp.load_all()
    funding_raw = dlp.load_funding()
    d1m, funding = dlp.slice_by_date(d1m_raw, funding_raw, start="2022-01-01", end="2026-07-10")
    print("BTC bars:", len(d1m["BTCUSDT"]), "SOL bars:", len(d1m["SOLUSDT"]))

    print("Computing signals once ...")
    signals = enhance_signals(d1m, cfg, funding)

    variants = [
        {"name": "baseline", "params": {}},
        {"name": "slope_fav_4", "params": {"slope_filter": {"lookback": 4, "sign": "favorable"}}},
        {"name": "slope_fav_8", "params": {"slope_filter": {"lookback": 8, "sign": "favorable"}}},
        {"name": "slope_adv_4", "params": {"slope_filter": {"lookback": 4, "sign": "adverse"}}},
        {"name": "adverse_stop_0_5", "params": {"adverse_stop_z": 0.5, "regime_break": 9.0}},
        {"name": "adverse_stop_0_7", "params": {"adverse_stop_z": 0.7, "regime_break": 9.0}},
        {"name": "adverse_stop_1_0", "params": {"adverse_stop_z": 1.0, "regime_break": 9.0}},
        {"name": "slope_fav_4_stop_0_7", "params": {"slope_filter": {"lookback": 4, "sign": "favorable"}, "adverse_stop_z": 0.7, "regime_break": 9.0}},
        {"name": "candle_confirm", "params": {"candle_confirm": True}},
        {"name": "funding_diff", "params": {"funding_diff_filter": True}},
    ]

    results = []
    for v in variants:
        print("Running", v["name"], "...")
        res = backtest_variant(signals, cfg, v["params"])
        port = build_portfolio([res], starting_capital=float(cfg.get("starting_capital_usd", 100_000.0)))
        port_idx = res["index"][:port["n_bars"]]
        # Package portfolio result to reuse metric helper.
        port_res = {
            "pair": res["pair"],
            "trades": res["trades"],
            "bar_return": port["bar_return"],
            "n_bars": port["n_bars"],
            "equity": port["equity"],
            "index": port_idx,
        }
        m = metrics_from_result(port_res, cfg)
        m["variant"] = v["name"]
        m["n_trades"] = len(res["trades"])
        results.append((v["name"], m, port_res))
        print(f"  trades={m['n_trades']} net_mean={m['mean_net_pct']*1e4:.2f}bps "
              f"win={m['win_rate']:.2%} sharpe_d={m['sharpe_daily_resampled']:.3f} "
              f"ann={m['annualized_return_daily']:.2%} mdd={m['max_drawdown_pct']:.2%} pf={m['profit_factor']:.3f}")

    # Save summary table.
    rows = []
    for name, m, _ in results:
        rows.append({
            "variant": name,
            "n_trades": m["n_trades"],
            "mean_net_bps": round(m["mean_net_pct"] * 1e4, 3),
            "mean_gross_bps": round(m["mean_gross_pct"] * 1e4, 3),
            "win_rate": round(m["win_rate"], 4),
            "sharpe_daily_resampled": round(m["sharpe_daily_resampled"], 4),
            "annualized_return_daily": round(m["annualized_return_daily"], 4),
            "max_drawdown_pct": round(m["max_drawdown_pct"], 4),
            "profit_factor": round(m["profit_factor"], 4),
            "calmar": round(m["calmar"], 4),
            "sortino": round(m["sortino"], 4),
        })
    df = pd.DataFrame(rows)
    csv_path = OUT / "variant_metrics.csv"
    df.to_csv(csv_path, index=False)
    print("\nSaved:", csv_path)

    # Persist per-variant metrics + trades.
    for name, m, res in results:
        (OUT / f"metrics_{name}.json").write_text(json.dumps(m, indent=2, default=float))
        if res["trades"]:
            pd.DataFrame(res["trades"]).to_csv(OUT / f"trades_{name}.csv", index=False)
        # Save daily equity for plotting.
        eq = pd.Series(res["equity"], index=res["index"])
        daily_eq = eq.resample("1D").last().dropna()
        daily_eq.reset_index().rename(columns={"index": "timestamp", 0: "equity"}).to_csv(
            OUT / f"equity_{name}_1d.csv", index=False
        )

    # Pick best variant by daily-resampled Sharpe (must have >=100 trades).
    best = max((r for r in results if r[1]["n_trades"] >= 100), key=lambda x: x[1]["sharpe_daily_resampled"])
    print("\nBest variant:", best[0])
    (OUT / "best_variant.txt").write_text(best[0])
    return results


if __name__ == "__main__":
    run_all()
