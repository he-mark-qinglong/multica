"""Walk-forward multi-OOS backtest runner for vpvr_funding_carry_asym_v2 (SMA-34990).

For each OOS fold in ``config.json``:

  1. Load BTCUSDT 1m / 15m / 4h OHLCV + funding.
  2. Build per-bar decision via ``build_signals``.
  3. Run state-machine ``run_backtest`` on the 1m bar stream.
  4. Compute per-fold metrics (Sharpe daily-resampled, total return,
     annualized, MDD, profit factor, win rate, avg bars held).
  5. Aggregate across folds and evaluate G1-G6 acceptance gates.
  6. Persist ``results/metrics.json``, ``gates_report.json``,
     ``summary.txt``, per-fold equity / trades CSVs.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent
QUANT_LOOP = REPO_ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(QUANT_LOOP))

from build_signals import build_signals  # noqa: E402
from data_loader import load_all  # noqa: E402
from state_machine import VARIANT_KEY, compute_metrics, run_backtest  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
TOPLEVEL_RESULTS = QUANT_LOOP / "results" / "vpvr_funding_carry_asym_v2_20260718"
TOPLEVEL_RESULTS.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = REPO_ROOT / "config.json"

RNG = np.random.default_rng(20260718)
SQRT_BPY_DAILY = math.sqrt(365.25)


def _slice_window(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC")
    if df.index.tz is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")
    return df.loc[s:e].copy()


def _tf_freq(tf: str) -> str:
    return {"1m": "1min", "15m": "15min", "4h": "4h"}.get(tf, "1min")


def _bootstrap_ci_lower(daily_returns: np.ndarray, n_resamples: int = 1000) -> float:
    if len(daily_returns) < 5:
        return 0.0
    sharpe_samples = np.empty(n_resamples, dtype=np.float64)
    for k in range(n_resamples):
        sample = RNG.choice(daily_returns, size=len(daily_returns), replace=True)
        mu = sample.mean()
        sd = sample.std()
        sharpe_samples[k] = (mu / sd * SQRT_BPY_DAILY) if sd > 0 else 0.0
    return float(np.quantile(sharpe_samples, 0.025))


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, (np.floating,)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return obj


def _run_fold(cfg: dict, fold: dict, frames: Dict[str, pd.DataFrame]) -> dict:
    test_start, test_end = fold["test_start"], fold["test_end"]
    df_1m = _slice_window(frames["1m"], test_start, test_end)
    df_15m = _slice_window(frames["15m"], test_start, test_end)
    df_4h = _slice_window(frames["4h"], test_start, test_end)
    funding = _slice_window(frames["funding"], test_start, test_end)

    if df_1m.empty or df_15m.empty or df_4h.empty or funding.empty:
        return {
            "fold": fold["name"],
            "test_window": [test_start, test_end],
            "error": (
                f"empty window: 1m={len(df_1m)} 15m={len(df_15m)} "
                f"4h={len(df_4h)} funding={len(funding)}"
            ),
        }

    decision = build_signals(df_1m, df_15m, df_4h, funding, cfg["params"])
    result = run_backtest(df_1m, decision, cfg)
    metrics = compute_metrics(result, df_1m.index)
    # Bootstrap CI on the sized (vol-targeted) daily returns.
    eq_vt = pd.Series(result["equity_vt"], index=pd.DatetimeIndex(
        pd.date_range(start=result["span_start"], periods=len(result["equity_vt"]),
                      freq=_tf_freq("1m"), tz="UTC")
    ))
    daily_returns = eq_vt.resample("1D").last().dropna().pct_change().dropna().values
    metrics["bootstrap_ci_lower_sharpe"] = round(_bootstrap_ci_lower(daily_returns), 4)

    # Per-fold equity + trades.
    eq = pd.DataFrame(
        {"equity": result["equity"]},
        index=pd.date_range(start=result["span_start"], periods=len(result["equity"]),
                            freq=_tf_freq("1m"), tz="UTC"),
    )
    eq.index.name = "timestamp"
    eq.to_csv(RESULTS_DIR / f"equity_{fold['name']}.csv")
    cols = [
        "variant", "symbol", "direction", "entry_ts", "entry_price",
        "exit_ts", "exit_price", "gross_pnl_pct", "cost_pct", "net_pnl_pct",
        "bars_held", "exit_reason",
        "funding_ema_at_entry", "half_at_entry", "slope_4h_at_entry",
    ]
    if result["trades"]:
        pd.DataFrame(result["trades"])[cols].to_csv(
            RESULTS_DIR / f"trades_{fold['name']}.csv", index=False
        )
    else:
        pd.DataFrame(columns=cols).to_csv(
            RESULTS_DIR / f"trades_{fold['name']}.csv", index=False
        )

    payload = {
        "fold": fold["name"],
        "test_window": [test_start, test_end],
        "n_bars": metrics["n_bars"],
        "metrics": metrics,
        "diagnostics": _sanitize(result["diagnostics"]),
    }
    (TOPLEVEL_RESULTS / f"backtest_{fold['name']}.json").write_text(
        json.dumps(payload, indent=2, default=str)
    )
    return payload


def _evaluate_gates(cfg: dict, valid_folds: List[dict], aggregate: dict) -> dict:
    g = cfg["acceptance_gates"]
    per_fold = []
    for p in valid_folds:
        m = p["metrics"]
        per_fold.append({
            "fold": p["fold"],
            "n_trades": m["n_trades"],
            "sharpe_daily": m["sharpe_daily"],
            "annualized_return": m["annualized_return"],
            "max_drawdown_pct": m["max_drawdown_pct"],
            "profit_factor": m["profit_factor"],
            "bootstrap_ci_lower_sharpe": m.get("bootstrap_ci_lower_sharpe", 0.0),
        })

    folds_meeting_trade_min = sum(
        1 for p in per_fold if p["n_trades"] >= int(g["min_n_trades_per_fold"])
    )
    sharpes = [p["sharpe_daily"] for p in per_fold]
    mean_sharpe = aggregate["mean_sharpe_daily"]
    sharpe_std = aggregate["std_sharpe_daily"]

    pass_min_sharpe = mean_sharpe >= float(g["min_mean_oos_sharpe"])
    pass_min_pf = aggregate["min_profit_factor"] >= float(g["min_profit_factor"])
    pass_mdd = aggregate["worst_max_drawdown_pct"] >= float(g["max_drawdown_min_pct"])
    pass_trade_min = folds_meeting_trade_min >= max(1, len(per_fold) - 1)
    pass_sharpe_consistency = sharpe_std <= float(g["max_per_fold_sharpe_std"])

    if (pass_min_sharpe and pass_min_pf and pass_mdd
            and pass_trade_min and pass_sharpe_consistency):
        verdict = "PROFITABLE"
    else:
        reasons = []
        if not pass_min_sharpe:
            reasons.append(f"mean_oos_sharpe<{g['min_mean_oos_sharpe']}")
        if not pass_min_pf:
            reasons.append(f"min_pf<{g['min_profit_factor']}")
        if not pass_mdd:
            reasons.append(f"mdd<{g['max_drawdown_min_pct']}")
        if not pass_trade_min:
            reasons.append("trade-count-min not met")
        if not pass_sharpe_consistency:
            reasons.append("sharpe-std too high")
        verdict = "NOT-PROFITABLE[" + "; ".join(reasons) + "]"

    return {
        "pass_min_mean_oos_sharpe": pass_min_sharpe,
        "pass_min_profit_factor": pass_min_pf,
        "pass_max_drawdown": pass_mdd,
        "pass_n_trades_min": pass_trade_min,
        "pass_sharpe_consistency": pass_sharpe_consistency,
        "folds_meeting_trade_min": folds_meeting_trade_min,
        "per_fold": per_fold,
        "verdict": verdict,
    }


def _build_summary_text(cfg: dict, per_fold: List[dict], aggregate: dict, gates: dict) -> str:
    lines: List[str] = []
    lines.append(f"=== {VARIANT_KEY} (SMA-34990) ===")
    lines.append(f"  Source spec:       {cfg['source_spec']}")
    lines.append(f"  Implementation:    {cfg['implementation_issue']}")
    lines.append(f"  Instruments:       {cfg['instruments']}")
    lines.append(f"  Timeframes:        {cfg['timeframes']}")
    lines.append(f"  Sharpe method:     daily_resampled (sqrt(365.25))")
    lines.append(f"  Cost model:        _shared/execution/cost_model.apply_cost() (BINANCE_FUTURES)")
    lines.append(f"  Sizing:            _shared/sizing/vol_target.apply_vol_target() (target_vol=0.20)")
    lines.append(f"  OOS:               walk-forward folds from config (n_groups=6 k_test=2 in CPCV)")
    lines.append("")
    lines.append("=== Per-fold ===")
    for p in per_fold:
        if "error" in p:
            lines.append(f"  {p['fold']}: ERROR {p['error']}")
            continue
        m = p["metrics"]
        lines.append(
            f"  {p['fold']}  test=[{p['test_window'][0]} .. {p['test_window'][1]}]"
        )
        lines.append(
            f"    trades={m['n_trades']}  win_rate={m['win_rate']:.3f}"
            f"  pf={m['profit_factor']}  avg_bars={m['avg_bars_held']}"
        )
        lines.append(
            f"    sharpe_d={m['sharpe_daily']}  ann={m['annualized_return']}"
            f"  mdd={m['max_drawdown_pct']}%"
            f"  CI_lo={m.get('bootstrap_ci_lower_sharpe', 0.0)}"
        )
    lines.append("")
    lines.append("=== Aggregate ===")
    lines.append(
        f"  mean_sharpe_d={aggregate['mean_sharpe_daily']}"
        f"  std_sharpe_d={aggregate['std_sharpe_daily']}"
        f"  mean_ann={aggregate['mean_annualized_return']}"
        f"  worst_mdd={aggregate['worst_max_drawdown_pct']}%"
        f"  min_pf={aggregate['min_profit_factor']}"
    )
    lines.append(
        f"  n_trades_total={aggregate['n_trades_total']}"
        f"  folds_valid={aggregate['n_folds_valid']}/{aggregate['n_folds_total']}"
    )
    lines.append("")
    lines.append("=== Acceptance gates ===")
    g = cfg["acceptance_gates"]
    lines.append(
        f"  mean_oos_sharpe >= {g['min_mean_oos_sharpe']}      : "
        f"pass={gates['pass_min_mean_oos_sharpe']}"
    )
    lines.append(
        f"  min_profit_factor >= {g['min_profit_factor']}        : "
        f"pass={gates['pass_min_profit_factor']}"
    )
    lines.append(
        f"  worst_max_drawdown >= {g['max_drawdown_min_pct']}%    : "
        f"pass={gates['pass_max_drawdown']}"
    )
    lines.append(
        f"  n_trades >= {g['min_n_trades_per_fold']} in >= {max(1, len(per_fold) - 1)} folds"
        f"  : pass={gates['pass_n_trades_min']}"
    )
    lines.append(
        f"  std_per_fold_sharpe <= {g['max_per_fold_sharpe_std']}  : "
        f"pass={gates['pass_sharpe_consistency']}"
    )
    lines.append("")
    lines.append(f"VERDICT: {gates['verdict']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    print(f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} backtest start", flush=True)

    print("  loading BTCUSDT 1m/15m/4h + funding...", flush=True)
    frames = load_all("BTCUSDT", cfg["timeframes"])
    print(
        f"  1m={len(frames['1m'])} 15m={len(frames['15m'])} "
        f"4h={len(frames['4h'])} funding={len(frames['funding'])}", flush=True
    )

    folds = list(cfg["oos_folds"])
    per_fold = []
    for fold in folds:
        try:
            per_fold.append(_run_fold(cfg, fold, frames))
        except Exception as exc:
            print(f"  [fold {fold['name']}] FAILED: {type(exc).__name__}: {exc}", flush=True)
            per_fold.append({
                "fold": fold["name"],
                "test_window": [fold["test_start"], fold["test_end"]],
                "error": f"{type(exc).__name__}: {exc}",
            })

    valid = [p for p in per_fold if "error" not in p]
    sharpes = [p["metrics"]["sharpe_daily"] for p in valid]
    anns = [p["metrics"]["annualized_return"] for p in valid]
    mdds = [p["metrics"]["max_drawdown_pct"] for p in valid]
    pfs = [
        p["metrics"]["profit_factor"] for p in valid
        if np.isfinite(p["metrics"]["profit_factor"])
    ]
    n_trades_total = sum(p["metrics"]["n_trades"] for p in valid)

    aggregate = {
        "mean_sharpe_daily": round(float(np.mean(sharpes)) if sharpes else 0.0, 4),
        "std_sharpe_daily": round(float(np.std(sharpes, ddof=1)) if len(sharpes) >= 2 else 0.0, 4),
        "mean_annualized_return": round(float(np.mean(anns)) if anns else 0.0, 6),
        "worst_max_drawdown_pct": round(float(min(mdds)) if mdds else 0.0, 4),
        "min_profit_factor": round(float(min(pfs)) if pfs else 0.0, 4),
        "n_trades_total": int(n_trades_total),
        "n_folds_total": len(folds),
        "n_folds_valid": len(valid),
    }

    gates = _evaluate_gates(cfg, valid, aggregate)

    envelope = {
        "variant_key": VARIANT_KEY,
        "iteration": cfg["iteration"],
        "date": cfg["date"],
        "source_spec": cfg["source_spec"],
        "implementation_issue": cfg["implementation_issue"],
        "instruments": cfg["instruments"],
        "timeframes": cfg["timeframes"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "aggregate": aggregate,
        "gates": gates,
        "verdict": gates["verdict"],
        "per_fold": per_fold,
    }

    (RESULTS_DIR / "metrics.json").write_text(json.dumps(_sanitize(envelope), indent=2, default=str))
    (RESULTS_DIR / "gates_report.json").write_text(json.dumps(_sanitize({
        "variant_key": VARIANT_KEY,
        "gates": gates,
        "aggregate": aggregate,
        "per_fold_gate_results": gates["per_fold"],
    }), indent=2, default=str))

    summary_text = _build_summary_text(cfg, per_fold, aggregate, gates)
    (RESULTS_DIR / "summary.txt").write_text(summary_text)
    print(summary_text)
    print(f"[{datetime.now(timezone.utc).isoformat()}] {VARIANT_KEY} backtest done", flush=True)
    return 0 if gates["verdict"] == "PROFITABLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())