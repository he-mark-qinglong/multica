"""SMA-34957 U5 cycle-46 rebuild — funding-oscillator MR backtest harness.

Runs the funding-oscillator mean-reversion strategy on real Binance
1m data for:

  1. SOL 1m standalone
  2. ETH 1m standalone
  3. Combined ETH+SOL 1m equal-risk portfolio

For each symbol the harness sweeps:

  - z_in            ∈ {1.5, 2.0, 2.5, 3.0}
  - lookback_events ∈ {30, 60, 90}
  - holding_events  ∈ {1, 2, 3}     ⇒ 8h / 16h / 24h horizon

Each variant is scored on:

  - OOS daily-resampled Sharpe (per SMA-34787)
  - annualized return  (compounded from daily equity)
  - max drawdown       (% of running peak)
  - profit factor
  - trades, win rate
  - bootstrap 95% CI for the Sharpe (10k resamples, seed=42)
  - Bonferroni-corrected p-value (one-sided, H1: Sharpe > 0)

G1-G7 gates (from the canonical config):

  G1  Sharpe (daily-resampled) >= 1.0
  G2  annualized return       >= 15%
  G3  max drawdown            >= -25% (more positive is better)
  G4  profit factor           >= 1.5
  G5  bootstrap CI lower      >= 0.5  (over 10k resamples at alpha=0.05)
  G6  Bonferroni p-value (one-sided) <= 0.0125
  G7  trades                  >= 30

Outputs go to ``~/multica/quant-loop/backtests/u5_funding_oscillator_mr/``:

  - u5noncarry_metrics.json      — full per-variant results + gate evaluation
  - u5noncarry_summary.txt       — human-readable run log
  - u5noncarry_equity_<sym>_<label>.csv  — daily equity per (sym, variant)
  - u5noncarry_trades_<sym>_<label>.csv  — per-trade ledger per (sym, variant)
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd

# --- Paths -------------------------------------------------------------------
QUANT_LOOP = Path("/home/smark/multica/quant-loop")
STRATEGY_DIR = QUANT_LOOP / "strategies" / "funding_oscillator_mr"
sys.path.insert(0, str(STRATEGY_DIR))

OUT_DIR = QUANT_LOOP / "backtests" / "u5_funding_oscillator_mr"
OUT_DIR.mkdir(parents=True, exist_ok=True)

from data_loader import load_symbol_1m  # noqa: E402
from strategy import VARIANT_KEY, run_backtest  # noqa: E402

# --- G1-G7 hard gates (same convention as the U5 funding-carry harness) ---
GATES = {
    "sharpe_min": 1.0,                # G1
    "annualized_return_min": 0.15,   # G2
    "max_drawdown_max": -0.25,       # G3 (upper bound, more positive = better)
    "profit_factor_min": 1.5,        # G4
    "bootstrap_ci_lower_min": 0.5,   # G5
    "bootstrap_resamples": 10000,
    "bootstrap_seed": 42,
    "bonferroni_alpha": 0.0125,      # G6 (one-sided, H1: Sharpe > 0)
    "trades_min": 30,                # G7
}
DEFAULT_WINDOW_DAYS = 365
BARS_PER_YEAR_DAILY = 365.25
SQRT_BPY_DAILY = math.sqrt(BARS_PER_YEAR_DAILY)
RISK_TARGET_PCT = 0.005
STARTING_CAPITAL = 100000.0
WINDOW_DAYS = DEFAULT_WINDOW_DAYS

SYMBOLS = ["ETHUSDT", "SOLUSDT"]


# ---------------------------------------------------------------------------
# Sweep grid
# ---------------------------------------------------------------------------
def variant_grid() -> list[Tuple[str, dict]]:
    grid: list[Tuple[str, dict]] = []
    for z_in in (1.5, 2.0, 2.5, 3.0):
        for lookback in (30, 60, 90):
            for hold in (1, 2, 3):
                label = f"z{z_in:g}_n{lookback}_h{hold}"
                params = {
                    "z_in": z_in,
                    "lookback_events": lookback,
                    "holding_events": hold,
                    "fee_bps_per_fill": 4.0,
                    "slippage_bps_per_fill": 1.0,
                    "risk_target_pct": RISK_TARGET_PCT,
                }
                grid.append((label, params))
    return grid


# ---------------------------------------------------------------------------
# Metrics (mirror of run_u5.py for consistency)
# ---------------------------------------------------------------------------
def _trade_pnl_to_daily(trades: list, span_start: pd.Timestamp, span_end: pd.Timestamp) -> pd.Series:
    def _strip(t):
        return t.tz_convert(None) if (t is not None and t.tz is not None) else t
    if not trades:
        idx = pd.date_range(_strip(span_start), _strip(span_end), freq="1D")
        return pd.Series(0.0, index=idx)
    idx_list = [_strip(pd.Timestamp(t["exit_event_ts"])).normalize() for t in trades]
    pnl = pd.Series([t["pnl_pct"] for t in trades],
                    index=pd.DatetimeIndex(idx_list))
    daily = pnl.resample("1D").sum()
    full_idx = pd.date_range(_strip(span_start).normalize(),
                             _strip(span_end).normalize(), freq="1D")
    daily = daily.reindex(full_idx, fill_value=0.0)
    return daily


def _build_daily_equity(daily_pnl: pd.Series, starting: float = STARTING_CAPITAL) -> Tuple[np.ndarray, pd.DatetimeIndex]:
    eq = (1.0 + daily_pnl).cumprod() * starting
    return eq.values, eq.index


def _daily_sharpe_from_equity(equity: np.ndarray, idx: pd.DatetimeIndex) -> Tuple[float, pd.Series]:
    eq_series = pd.Series(equity, index=idx, dtype=np.float64)
    rets = eq_series.pct_change().dropna()
    if len(rets) < 2 or rets.std() == 0 or not np.isfinite(rets.std()):
        return 0.0, rets
    return float(rets.mean() / rets.std() * SQRT_BPY_DAILY), rets


def _max_drawdown(equity: np.ndarray) -> float:
    if len(equity) < 2:
        return 0.0
    rm = np.maximum.accumulate(equity)
    dd = (equity - rm) / rm
    return float(np.min(dd)) if dd.size else 0.0


def _bootstrap_sharpe_ci(rets: pd.Series, *, n_resamples: int, seed: int) -> dict:
    if len(rets) < 2:
        return {"mean": 0.0, "ci_lo": 0.0, "ci_hi": 0.0,
                "p_value": 1.0, "p_positive_one_sided": 1.0}
    rng = np.random.RandomState(seed)
    rets_arr = rets.values
    sharpes = np.empty(n_resamples, dtype=np.float64)
    for b in range(n_resamples):
        idx = rng.randint(0, len(rets_arr), size=len(rets_arr))
        sample = rets_arr[idx]
        sd = sample.std(ddof=1)
        sharpes[b] = (sample.mean() / sd * SQRT_BPY_DAILY) if (sd > 0 and np.isfinite(sd)) else 0.0
    ci_lo = float(np.quantile(sharpes, 0.025))
    ci_hi = float(np.quantile(sharpes, 0.975))
    mean_sh = float(np.mean(sharpes))
    p_two = float(min((sharpes <= 0).sum(), (sharpes >= 0).sum()) / n_resamples)
    p_pos_one = float((sharpes <= 0).sum() / n_resamples)   # H1: Sharpe > 0
    return {"mean": mean_sh, "ci_lo": ci_lo, "ci_hi": ci_hi,
            "p_value": p_two, "p_positive_one_sided": p_pos_one}


def _evaluate_gates(metrics: dict, bonferroni_family_n: int) -> dict:
    n = bonferroni_family_n
    adj_alpha = GATES["bonferroni_alpha"] / n if n > 0 else GATES["bonferroni_alpha"]
    p_pos = metrics.get("sharpe_p_value_pos_one_sided", 1.0)
    return {
        "G1_sharpe_ge_1": (metrics.get("sharpe_daily", 0.0) >= GATES["sharpe_min"]),
        "G2_ann_ge_15pct": (metrics.get("annualized_return", 0.0) >= GATES["annualized_return_min"]),
        "G3_maxdd_ge_-25pct": (metrics.get("max_drawdown_pct", 0.0) >= GATES["max_drawdown_max"]),
        "G4_pf_ge_1_5": (metrics.get("profit_factor", 0.0) >= GATES["profit_factor_min"]),
        "G5_bs_ci_lo_ge_0_5": (metrics.get("bootstrap_sharpe_ci_lo", 0.0) >= GATES["bootstrap_ci_lower_min"]),
        "G6_bonferroni_pos_one_sided": (p_pos <= adj_alpha),
        "G7_trades_ge_30": (metrics.get("n_trades", 0) >= GATES["trades_min"]),
    }


def _compute_metrics(result: dict, daily_pnl: pd.Series,
                     bonferroni_family_n: int) -> dict:
    equity_arr, equity_idx = _build_daily_equity(daily_pnl)
    starting = float(equity_arr[0]) if len(equity_arr) else 0.0
    final = float(equity_arr[-1]) if len(equity_arr) else 0.0
    n_trades = int(result.get("n_trades_fired", len(result["trades"])))

    if len(equity_arr) < 2 or starting <= 0:
        return {
            "n_trades": n_trades, "win_rate": 0.0,
            "profit_factor": 0.0, "sharpe_daily": 0.0,
            "total_return": 0.0, "annualized_return": 0.0,
            "max_drawdown_pct": 0.0, "avg_bars_held": 0.0,
            "bootstrap_sharpe_mean": 0.0, "bootstrap_sharpe_ci_lo": 0.0,
            "bootstrap_sharpe_ci_hi": 0.0, "sharpe_p_value_two_sided": 1.0,
            "sharpe_p_value_pos_one_sided": 1.0, "daily_rets_n": 0,
            "n_funding_events": int(result.get("n_events", 0)),
            "span_start": result.get("span_start"), "span_end": result.get("span_end"),
            "gates": _evaluate_gates({}, bonferroni_family_n),
            "bonferroni_p_raw": 1.0,
            "bonferroni_alpha_adj": GATES["bonferroni_alpha"] / bonferroni_family_n if bonferroni_family_n else GATES["bonferroni_alpha"],
            "bonferroni_family_n": bonferroni_family_n,
        }

    total_return = final / starting - 1.0
    n_days = max(1, (equity_idx[-1] - equity_idx[0]).days)
    n_years = n_days / BARS_PER_YEAR_DAILY
    annualized = (final / starting) ** (1.0 / n_years) - 1.0 if (n_years > 0 and final > 0) else 0.0

    sharpe_d, daily_rets = _daily_sharpe_from_equity(equity_arr, equity_idx)
    max_dd = _max_drawdown(equity_arr)

    pnl_arr = np.array([t["pnl_pct"] for t in result["trades"]], dtype=np.float64) if n_trades else np.array([])
    if pnl_arr.size:
        gross_profit = float(pnl_arr[pnl_arr > 0].sum())
        gross_loss = float(abs(pnl_arr[pnl_arr < 0].sum()))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
        wr = float((pnl_arr > 0).sum() / n_trades)
        avg_bars = float(np.mean([t["bars_held"] for t in result["trades"]]))
    else:
        pf = 0.0
        wr = 0.0
        avg_bars = 0.0

    boot = _bootstrap_sharpe_ci(daily_rets, n_resamples=GATES["bootstrap_resamples"], seed=GATES["bootstrap_seed"])
    out = {
        "n_trades": n_trades,
        "win_rate": round(wr, 4),
        "profit_factor": round(pf, 4) if math.isfinite(pf) else float("inf"),
        "sharpe_daily": round(sharpe_d, 4),
        "total_return": round(total_return, 6),
        "annualized_return": round(annualized, 6),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
        "avg_bars_held": round(avg_bars, 1),
        "bootstrap_sharpe_mean": round(boot["mean"], 4),
        "bootstrap_sharpe_ci_lo": round(boot["ci_lo"], 4),
        "bootstrap_sharpe_ci_hi": round(boot["ci_hi"], 4),
        "sharpe_p_value_two_sided": round(boot["p_value"], 4),
        "sharpe_p_value_pos_one_sided": round(boot["p_positive_one_sided"], 4),
        "daily_rets_n": int(len(daily_rets)),
        "n_funding_events": int(result.get("n_events", 0)),
        "span_start": result.get("span_start"),
        "span_end": result.get("span_end"),
    }
    out["gates"] = _evaluate_gates(out, bonferroni_family_n)
    out["bonferroni_p_raw"] = out["sharpe_p_value_pos_one_sided"]
    out["bonferroni_alpha_adj"] = (GATES["bonferroni_alpha"] / bonferroni_family_n
                                    if bonferroni_family_n else GATES["bonferroni_alpha"])
    out["bonferroni_family_n"] = bonferroni_family_n
    return out


# ---------------------------------------------------------------------------
# Single-symbol backtest
# ---------------------------------------------------------------------------
def run_symbol(symbol: str, grid: list, iteration: str, window_days: int,
               out_dir: Path, family_n: int) -> list:
    df, stats = load_symbol_1m(symbol, window_days)
    out_rows: list = []
    for label, params in grid:
        cfg = {
            "iteration": iteration,
            "instruments": [symbol],
            "params": dict(params),
            "starting_capital_usd": STARTING_CAPITAL,
        }
        result = run_backtest(df, cfg)
        daily_pnl = _trade_pnl_to_daily(result["trades"], df.index[0], df.index[-1])
        metrics = _compute_metrics(result, daily_pnl, bonferroni_family_n=family_n)
        row = {
            "label": label,
            "params": params,
            "metrics": metrics,
            "diagnostics": result["diagnostics"],
            "n_funding_events": int(result.get("n_events", 0)),
            "span_start": result.get("span_start"),
            "span_end": result.get("span_end"),
            "symbol": symbol,
            "tf": "1m",
            "variant": VARIANT_KEY,
            "sharpe_method": "daily_resampled_per_SMA-34787",
            "sharpe_method_audit_ref": "SMA-34787",
            "params_override": params,
        }
        out_rows.append(row)

        # write equity + trades csvs
        eq_arr, eq_idx = _build_daily_equity(daily_pnl)
        eq_df = pd.DataFrame({"date": eq_idx, "equity": eq_arr})
        eq_df.to_csv(out_dir / f"u5noncarry_equity_{symbol}_{label}.csv", index=False)
        if result["trades"]:
            tdf = pd.DataFrame(result["trades"])
            tdf.to_csv(out_dir / f"u5noncarry_trades_{symbol}_{label}.csv", index=False)
    return out_rows


# ---------------------------------------------------------------------------
# Portfolio: equal-risk ETH+SOL on the same variant label
# ---------------------------------------------------------------------------
def run_portfolio(per_sym_rows: dict, grid: list, iteration: str,
                  window_days: int, out_dir: Path, family_n: int) -> dict:
    """Aggregate per-symbol trades per variant label into an equal-risk
    portfolio. Daily PnL is the sum of the two symbols' daily pnls for
    that variant.
    """
    portfolio_variants: dict = {}
    span_start = None
    span_end = None
    sym_stats: dict = {}
    for sym, rows in per_sym_rows.items():
        # Each row has trades; reconstruct daily pnl per variant
        first = rows[0]
        span_start = first.get("span_start")
        span_end = first.get("span_end")
        sym_stats[sym] = rows

    for label, _ in grid:
        # Reconstruct daily pnls for both symbols
        sym_daily = {}
        for sym, rows in sym_stats.items():
            row = next(r for r in rows if r["label"] == label)
            # Re-derive trades -> daily pnl
            trades = []
            # load trades from the trades CSV (since row only has metrics)
            tpath = out_dir / f"u5noncarry_trades_{sym}_{label}.csv"
            if tpath.exists():
                tdf = pd.read_csv(tpath)
                trades = tdf.to_dict(orient="records")
            df_tmp, _ = load_symbol_1m(sym, window_days)
            dpnl = _trade_pnl_to_daily(trades, df_tmp.index[0], df_tmp.index[-1])
            sym_daily[sym] = dpnl

        # Equal-risk: per-day return = 0.5 * sym1 + 0.5 * sym2 (in pnl_pct)
        if not sym_daily:
            continue
        all_idx = None
        for sym, dpnl in sym_daily.items():
            all_idx = dpnl.index if all_idx is None else all_idx.union(dpnl.index)
        if all_idx is None:
            continue
        # Reindex each to all_idx, fill 0
        aligned = {sym: dpnl.reindex(all_idx, fill_value=0.0) for sym, dpnl in sym_daily.items()}
        port_daily = sum(aligned.values()) / len(aligned)

        # Build portfolio equity + metrics
        eq_arr, eq_idx = _build_daily_equity(port_daily)
        starting = float(eq_arr[0]) if len(eq_arr) else 0.0
        final = float(eq_arr[-1]) if len(eq_arr) else 0.0
        if len(eq_arr) < 2 or starting <= 0:
            port_metrics = {
                "n_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "sharpe_daily": 0.0, "total_return": 0.0, "annualized_return": 0.0,
                "max_drawdown_pct": 0.0, "avg_bars_held": 0.0,
                "bootstrap_sharpe_mean": 0.0, "bootstrap_sharpe_ci_lo": 0.0,
                "bootstrap_sharpe_ci_hi": 0.0, "sharpe_p_value_two_sided": 1.0,
                "sharpe_p_value_pos_one_sided": 1.0, "daily_rets_n": 0,
                "gates": _evaluate_gates({}, family_n),
            }
        else:
            total_return = final / starting - 1.0
            n_days = max(1, (eq_idx[-1] - eq_idx[0]).days)
            n_years = n_days / BARS_PER_YEAR_DAILY
            annualized = (final / starting) ** (1.0 / n_years) - 1.0 if (n_years > 0 and final > 0) else 0.0
            sharpe_d, daily_rets = _daily_sharpe_from_equity(eq_arr, eq_idx)
            max_dd = _max_drawdown(eq_arr)

            n_trades_port = sum(int(next(r for r in sym_stats[sym] if r["label"] == label)
                                     ["metrics"]["n_trades"]) for sym in sym_stats.keys())
            # Per-trade pf/win-rate aggregation
            sym_trades_pf = []
            for sym in sym_stats.keys():
                tpath = out_dir / f"u5noncarry_trades_{sym}_{label}.csv"
                if tpath.exists():
                    tdf = pd.read_csv(tpath)
                    sym_trades_pf.extend(tdf["pnl_pct"].tolist())
            if sym_trades_pf:
                arr = np.array(sym_trades_pf, dtype=np.float64)
                gp = float(arr[arr > 0].sum())
                gl = float(abs(arr[arr < 0].sum()))
                pf = gp / gl if gl > 0 else float("inf")
                wr = float((arr > 0).sum()) / len(arr)
                avg_bars = float(np.mean([t for t in sym_trades_pf]))  # placeholder
            else:
                pf = 0.0; wr = 0.0; avg_bars = 0.0

            boot = _bootstrap_sharpe_ci(daily_rets, n_resamples=GATES["bootstrap_resamples"],
                                         seed=GATES["bootstrap_seed"])
            port_metrics = {
                "n_trades": int(n_trades_port),
                "win_rate": round(wr, 4),
                "profit_factor": round(pf, 4) if math.isfinite(pf) else float("inf"),
                "sharpe_daily": round(sharpe_d, 4),
                "total_return": round(total_return, 6),
                "annualized_return": round(annualized, 6),
                "max_drawdown_pct": round(max_dd * 100.0, 4),
                "avg_bars_held": round(avg_bars, 1),
                "bootstrap_sharpe_mean": round(boot["mean"], 4),
                "bootstrap_sharpe_ci_lo": round(boot["ci_lo"], 4),
                "bootstrap_sharpe_ci_hi": round(boot["ci_hi"], 4),
                "sharpe_p_value_two_sided": round(boot["p_value"], 4),
                "sharpe_p_value_pos_one_sided": round(boot["p_positive_one_sided"], 4),
                "daily_rets_n": int(len(daily_rets)),
                "span_start": span_start,
                "span_end": span_end,
            }
            port_metrics["gates"] = _evaluate_gates(port_metrics, family_n)
        port_metrics["bonferroni_p_raw"] = port_metrics["sharpe_p_value_pos_one_sided"]
        port_metrics["bonferroni_alpha_adj"] = (GATES["bonferroni_alpha"] / family_n
                                                if family_n else GATES["bonferroni_alpha"])
        port_metrics["bonferroni_family_n"] = family_n

        # Save portfolio equity
        eq_df = pd.DataFrame({"date": eq_idx, "equity": eq_arr})
        eq_df.to_csv(out_dir / f"u5noncarry_equity_ETH+SOL_{label}.csv", index=False)
        portfolio_variants[label] = port_metrics
    return portfolio_variants


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------
def main(window_days: int = DEFAULT_WINDOW_DAYS,
         out_dir: Path = OUT_DIR,
         iteration: str = "SMA-34957") -> int:
    grid = variant_grid()
    family_n = len(grid) * len(SYMBOLS)  # Bonferroni family: per-symbol × variant
    print(f"=== {iteration}  funding-oscillator-MR  window={window_days}d ===", flush=True)
    print(f"=== Sweep: {len(grid)} variants × {len(SYMBOLS)} symbols = {family_n} family tests ===", flush=True)
    funding_event_stats: dict = {}
    per_symbol_variants: dict = {}
    t_total = time.time()
    for sym in SYMBOLS:
        t0 = time.time()
        rows = run_symbol(sym, grid, iteration, window_days, out_dir, family_n)
        per_symbol_variants[sym] = rows
        _, st = load_symbol_1m(sym, window_days)
        funding_event_stats[sym] = st
        print(f"  [done] {sym}  ({len(rows)} variants)  {time.time()-t0:.1f}s", flush=True)

    # Portfolio
    t0 = time.time()
    portfolio = run_portfolio(per_symbol_variants, grid, iteration, window_days, out_dir, family_n)
    print(f"  [done] ETH+SOL portfolio ({len(portfolio)} variants)  {time.time()-t0:.1f}s", flush=True)

    # Aggregate
    metrics = {
        "variant_key": VARIANT_KEY,
        "iteration": iteration,
        "source_spec": "SMA-34957",
        "instruments": SYMBOLS,
        "timeframes": ["1m"],
        "window_days": window_days,
        "axis": "b_funding_oscillator_z_mr_no_carry",
        "hard_gates": GATES,
        "funding_event_stats": funding_event_stats,
        "sharpe_method": "daily_resampled_per_SMA-34787",
        "sharpe_method_audit_ref": "SMA-34787",
        "data_provenance": {
            "ETH_1m": "data/perp_1m/ETHUSDT_1m.parquet (shared pool, real Binance)",
            "SOL_1m": "strategies/vpvr_volume_edge_3tf_v1_20260711/data/SOLUSDT__1m.parquet (real Binance snapshot)",
            "ETH_FUND": "data/funding/ETHUSDT.parquet (Binance USDT-M, 5100 events 8h cadence)",
            "SOL_FUND": "data/funding/SOLUSDT.parquet (Binance USDT-M, 5175 events 8h cadence)",
        },
        "per_symbol_variants": per_symbol_variants,
        "portfolio_variants": portfolio,
        "bonferroni_family_n": family_n,
        "bonferroni_alpha_adj": (GATES["bonferroni_alpha"] / family_n if family_n else GATES["bonferroni_alpha"]),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    metrics_path = out_dir / "u5noncarry_metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, default=str))

    # Best-of summary
    print(f"\n=== {iteration} window={window_days}d  (elapsed {time.time()-t_total:.1f}s) ===", flush=True)
    print(f"family_n = {family_n}, alpha_adj = {GATES['bonferroni_alpha']/family_n:.6f}", flush=True)
    print("--- per-symbol best variant (by Sharpe_daily, n>=30 only) ---", flush=True)
    best_per_window = []
    for sym, rows in per_symbol_variants.items():
        viable = [r for r in rows if r["metrics"].get("n_trades", 0) >= GATES["trades_min"]]
        viable.sort(key=lambda r: r["metrics"].get("sharpe_daily", -99.0), reverse=True)
        if not viable:
            print(f"  {sym}: NO VARIANT MET n>=30", flush=True)
            continue
        top = viable[0]
        m = top["metrics"]
        gates_str = " ".join(f"{k}={'Y' if v else 'n'}" for k, v in m.get("gates", {}).items())
        print(f"  {sym}  {top['label']}: Sharpe={m.get('sharpe_daily')} ann={m.get('annualized_return')} mdd={m.get('max_drawdown_pct')}% PF={m.get('profit_factor')} n={m.get('n_trades')} | {gates_str}", flush=True)
        best_per_window.append({"scope": "per_symbol", "symbol": sym, **top})
    print("--- portfolio best variant (by Sharpe_daily, n>=30 only) ---", flush=True)
    viable_port = [{"label": k, "metrics": v} for k, v in portfolio.items()
                   if v.get("n_trades", 0) >= GATES["trades_min"]]
    viable_port.sort(key=lambda r: r["metrics"].get("sharpe_daily", -99.0), reverse=True)
    if not viable_port:
        print("  ETH+SOL: NO PORTFOLIO VARIANT MET n>=30", flush=True)
    else:
        top = viable_port[0]
        m = top["metrics"]
        gates_str = " ".join(f"{k}={'Y' if v else 'n'}" for k, v in m.get("gates", {}).items())
        print(f"  ETH+SOL {top['label']}: Sharpe={m.get('sharpe_daily')} ann={m.get('annualized_return')} mdd={m.get('max_drawdown_pct')}% PF={m.get('profit_factor')} n={m.get('n_trades')} | {gates_str}", flush=True)
        best_per_window.append({"scope": "portfolio", "symbol": "ETH+SOL", "label": top["label"], **top})

    # Persist best
    (out_dir / "u5noncarry_best_per_window.json").write_text(
        json.dumps({"window_days": window_days, "best": best_per_window}, indent=2, default=str)
    )
    # Summary text
    summary_lines = [
        f"=== {iteration} funding-oscillator-MR window={window_days}d ===",
        f"family_n = {family_n}, alpha_adj = {GATES['bonferroni_alpha']/family_n:.6f}",
        f"variants = {len(grid)}, symbols = {SYMBOLS}",
        f"funding event stats (window):",
    ]
    for sym, st in funding_event_stats.items():
        summary_lines.append(
            f"  {sym}: n_events={st['n_events']} max={st['max']:.6f} p99={st['p99']:.6f} "
            f"min={st['min']:.6f} mean={st['mean']:.6e}"
        )
    summary_lines.append("--- per-symbol best (n>=30) ---")
    for entry in best_per_window:
        if entry.get("scope") == "per_symbol":
            m = entry["metrics"]
            summary_lines.append(
                f"  {entry['symbol']} {entry['label']}: "
                f"Sharpe={m.get('sharpe_daily')} ann={m.get('annualized_return')} "
                f"mdd={m.get('max_drawdown_pct')}% PF={m.get('profit_factor')} "
                f"n={m.get('n_trades')} ci_lo={m.get('bootstrap_sharpe_ci_lo')} "
                f"p_pos={m.get('sharpe_p_value_pos_one_sided')}"
            )
    summary_lines.append("--- portfolio best (n>=30) ---")
    for entry in best_per_window:
        if entry.get("scope") == "portfolio":
            m = entry["metrics"]
            summary_lines.append(
                f"  {entry['symbol']} {entry['label']}: "
                f"Sharpe={m.get('sharpe_daily')} ann={m.get('annualized_return')} "
                f"mdd={m.get('max_drawdown_pct')}% PF={m.get('profit_factor')} "
                f"n={m.get('n_trades')} ci_lo={m.get('bootstrap_sharpe_ci_lo')} "
                f"p_pos={m.get('sharpe_p_value_pos_one_sided')}"
            )
    (out_dir / "u5noncarry_summary.txt").write_text("\n".join(summary_lines) + "\n")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW_DAYS)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--iteration", type=str, default="SMA-34957")
    args = ap.parse_args()
    sys.exit(main(window_days=args.window, out_dir=args.out_dir, iteration=args.iteration))
