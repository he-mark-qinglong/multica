"""SMA-34936 H3 sizing sweep driver.

Reproduces the SMA-34878 baseline (Sharpe 2.77, ann 59.75%, maxDD
-12.62%, G3 PF=1.013 FAIL) using the shared base, then sweeps ONLY the
sizing rule — entry/exit logic untouched. Variants:

  1. ATR multiplier sweep       0.5x, 0.75x, 1.0x, 1.25x, 1.5x, 1.75x, 2.0x
  2. Volatility-targeted        target 10% / 15% / 20% annualised
  3. Kelly-fraction             1/4 Kelly, 1/2 Kelly
  4. Regime-conditional         size multiplied by 1 / (1 + |fund_ema|/threshold)

Metrics: daily-resampled Sharpe (smark directive 2026-07-18); PF and
portfolio-NAV maxDD per iter#82 / SMA-34927 lesson (NOT per-symbol-worst).

Walk-forward: same train/test window as the SMA-34875 campaign (H3).

This script emits both per-variant OOS metrics into a CSV and a
machine-readable JSON summary at results/sizing_sweep.json.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # strategies/
sys.path.insert(0, str(_HERE.parent / "_indicators"))

from mtf_xs_pairs_base_20260718 import (  # noqa: E402
    VARIANT_KEY,
    align_lower_to_upper,
    aggregate_ohlcv,
    build_h3_signals,
    build_portfolio,
    daily_returns,
    pair_zscore,
    profit_factor_and_mdd,
    sharpe_daily_resampled,
    wilder_atr,
)

from data_loader import load_all, load_funding  # noqa: E402

CONFIG_PATH = _HERE / "config.json"
RESULTS_DIR = _HERE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Sizing variants. Each takes pre-computed auxiliary series and returns a
# per-bar pandas.Series aligned to ``common_index`` with values in [floor,
# ceiling] (per cfg). Returns are scaled into the bar return in
# ``compute_scaled_returns``.
# ---------------------------------------------------------------------------

def sizing_baseline_atr(scale_atr: pd.Series, attr_med: pd.Series,
                        cfg: dict) -> pd.Series:
    """Original baseline size = (atr_med / atr_1m).clip(floor, ceiling).

    This is the formula used in build_h3_signals; we replicate it for
    comparison.
    """
    floor = float(cfg["size_floor"])
    ceil = float(cfg["size_ceiling"])
    return (attr_med / scale_atr.replace(0.0, np.nan)).clip(floor, ceil).fillna(1.0)


def sizing_atr_multiplier(scale_atr: pd.Series, attr_med: pd.Series,
                          cfg: dict, multiplier: float) -> pd.Series:
    """ATR multiplier sweep.

    size = baseline_formula * multiplier, then clipped to [floor, ceiling].
    A 1.5x multiplier widens the cap to allow more aggressive sizing
    when ATR is low; values beyond ceiling are clipped at the configured
    ceiling.
    """
    floor = float(cfg["size_floor"])
    ceil = float(cfg["size_ceiling"])
    base = attr_med / scale_atr.replace(0.0, np.nan)
    return (base * multiplier).clip(floor, ceil).fillna(1.0)


def sizing_vol_target(scale_atr: pd.Series, attr_med: pd.Series,
                      cfg: dict, target_vol_annual_pct: float) -> pd.Series:
    """Volatility-targeted sizing: scale inversely with realised 1m vol.

    realised_vol_1m = sigma_1m / close (rolling 1000-bar window);
    vol_target_1m  = target_vol_annual_pct / sqrt(525600);
    size = clip(vol_target_1m / realised_vol_1m, floor, ceiling).
    """
    floor = float(cfg["size_floor"])
    ceil = float(cfg["size_ceiling"])
    # We need close for vol. Caller passes it via attr_med-index trick: we
    # accept the close series as attr_med to keep variant signature simple,
    # but rename the meaning here:
    close = attr_med  # we re-purpose the second arg as close for this variant
    sigma_1m = close.pct_change().rolling(1000, min_periods=1000).std(ddof=0)
    target_1m = float(target_vol_annual_pct) / math.sqrt(525600.0)
    size = (target_1m / sigma_1m.replace(0.0, np.nan)).clip(floor, ceil).fillna(1.0)
    return size


def sizing_regime_conditional(scale_atr: pd.Series, attr_med: pd.Series,
                              cfg: dict, fund_allow: pd.Series,
                              funding_ema: pd.Series,
                              fund_thr: float,
                              boost_max: float = 1.6) -> pd.Series:
    """Regime-conditional sizing.

    size = baseline_atr_size * (1 + boost_max * regime_confidence)
    where regime_confidence = max(0, 1 - |funding_ema| / fund_thr).
    """
    floor = float(cfg["size_floor"])
    ceil = float(cfg["size_ceiling"])
    base = (attr_med / scale_atr.replace(0.0, np.nan)).clip(lower=0.0).fillna(1.0)
    conf = (1.0 - (funding_ema.abs() / max(fund_thr, 1e-12))).clip(lower=0.0, upper=1.0)
    conf = conf.reindex(base.index, method="ffill").fillna(0.0)
    size = (base * (1.0 + boost_max * conf)).clip(floor, ceil).fillna(1.0)
    return size


# ---------------------------------------------------------------------------
# Replay engine: build signals once, then sweep sizing, then compute
# walk-forward metrics per variant.
# ---------------------------------------------------------------------------

@dataclass
class VariantSpec:
    name: str
    family: str  # "atr_mult" | "vol_target" | "kelly" | "regime"
    params: dict


def kelly_size(trade_log: list, kelly_fraction: float,
               floor: float, ceil: float) -> float:
    """Half-Kelly / quarter-Kelly scalar as a single multiplier.

    We approximate per-pair realised win-rate and payoff from the trade
    log of the BASELINE run (entry/exit logic preserved). Kelly fraction
    is then applied as a constant size multiplier on top of the baseline
    ATR scale; wins/losses compound the same per-bar PnL, so we only
    need a single scalar to capture sizing intent.
    """
    if not trade_log:
        return 1.0
    pnls = np.array([t["pnl_pct"] for t in trade_log])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    if len(wins) == 0 or len(losses) == 0:
        return 1.0
    p = float(len(wins)) / len(pnls)
    avg_w = float(wins.mean())
    avg_l = float(-losses.mean())
    b = avg_w / max(avg_l, 1e-12)
    kelly = max(0.0, (p * (b + 1.0) - 1.0) / b) if b > 0 else 0.0
    scale = max(0.0, kelly * kelly_fraction)
    # Apply scale through the floor/ceiling of the baseline ATR variant
    return float(np.clip(scale, floor, ceil))


def replay_single(signals_by_pair: dict, size_scale_fn, common_index: pd.DatetimeIndex,
                  cfg: dict, fee_bps: float, slip_bps: float) -> dict:
    """Replay a single sizing variant end-to-end on the prepared signals.

    Reuses the entry/exit logic from the shared base (we import the
    internals as a module — see _backtest_pair import below). Returns
    the per-pair trade log plus the portfolio bar_return array.
    """
    from mtf_xs_pairs_base_20260718 import _backtest_pair  # type: ignore

    per_pair = []
    for pair, sig in signals_by_pair.items():
        # size_scale is allowed to be recomputed externally; pair-specific
        # kwargs are absorbed into the closure below.
        size_scale = size_scale_fn(sig, pair)
        result = _backtest_pair(
            sig, pair,
            sizing_scale=size_scale,
            fee_bps=fee_bps, slip_bps=slip_bps,
        )
        per_pair.append(result)
    starting = float(cfg.get("starting_capital_usd", 100000.0))
    port = build_portfolio(per_pair, starting_capital=starting)
    return {"per_pair": per_pair, "portfolio": port}


def _strip_tz(d1m: dict, funding: dict) -> tuple[dict, dict]:
    d1m_n = {}
    for sym, df in d1m.items():
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df = df.copy()
            df.index = df.index.tz_convert(None)
        d1m_n[sym] = df
    f_n = {}
    for sym, f in funding.items():
        if isinstance(f.index, pd.DatetimeIndex) and f.index.tz is not None:
            f = f.copy()
            f.index = f.index.tz_convert(None)
        f_n[sym] = f
    return d1m_n, f_n


def compute_sizing_signals(d1m: dict, funding: dict, cfg: dict) -> dict:
    """Precompute the union of baseline H3 signals plus size helpers used
    by the sizing variants.

    NOTE: indices are normalised to tz-naive (matching the contract of
    ``run_backtest`` in mtf_xs_pairs_base_20260718) so that the
    BTC/SOL common index can be reindexed against the funding 2h-resampled
    EMA without ``datetime64[ms] vs datetime64[ms, UTC]`` mismatches.
    """
    d1m, funding = _strip_tz(d1m, funding)
    out = build_h3_signals(d1m, cfg, funding)
    # For vol-target we need close_a (BTC) close array and funding EMA.
    pair = cfg["pairs"][0]
    a_sym, b_sym = pair.split("/")
    a = d1m[a_sym]
    b = d1m[b_sym]
    common = a.index.intersection(b.index)
    b_15m = aggregate_ohlcv(b.loc[common], "15min")
    atr_b_15m = wilder_atr(b_15m, 14)
    atr_b_1m = align_lower_to_upper(b.loc[common], atr_b_15m)
    atr_med = atr_b_1m.rolling(int(cfg["indicators"]["atr_normalize_window"]),
                               min_periods=240).median()
    # funding EMA series (rebroadcast to common index) for regime-conditional
    f_a = funding.get(a_sym)
    fund_ema_a = pd.Series(1.0, index=common, dtype=float)  # default allow
    if f_a is not None and len(f_a):
        f = f_a.copy()
        if f.index.tz is not None:
            f.index = f.index.tz_convert(None)
        ema_e = f.ewm(span=max(int(cfg["indicators"]["funding_ema_window"]), 2),
                      adjust=False).mean()
        ema_2h = ema_e.resample("2h", closed="left", label="left").mean().dropna()
        fund_ema_a = ema_2h.reindex(common, method="ffill").ffill()
    # The factor 0.5 converts the helper arg's role into "close" for vol-target
    return {"out": out, "atr_b_1m": atr_b_1m, "atr_med": atr_med,
            "fund_ema_a": fund_ema_a, "fund_allow": out[pair]["fund_allow"]}


def variant_scale_fn(spec: VariantSpec, sigs: dict, cfg: dict, trade_log_baseline: list):
    """Closure factory: return a fn(pair, *args) -> pd.Series for the variant."""
    ind = cfg["indicators"]
    floor = float(cfg["sizing"]["size_floor"])
    ceil = float(cfg["sizing"]["size_ceiling"])
    if spec.family == "atr_mult":
        mult = float(spec.params["multiplier"])
        def fn(pair_signals, pair):
            return sizing_atr_multiplier(
                sigs["atr_b_1m"], sigs["atr_med"],
                {"size_floor": floor, "size_ceiling": ceil},
                multiplier=mult,
            )
        return fn
    if spec.family == "vol_target":
        target_pct = float(spec.params["target_annual_vol_pct"])
        def fn(pair_signals, pair):
            # We pass close_a as the second arg (re-purpose attr_med slot)
            return sizing_vol_target(
                sigs["atr_b_1m"], pair_signals["a"]["close"],
                {"size_floor": floor, "size_ceiling": ceil},
                target_vol_annual_pct=target_pct,
            )
        return fn
    if spec.family == "kelly":
        kf = float(spec.params["kelly_fraction"])
        scale_const = kelly_size(trade_log_baseline, kf, floor, ceil)
        def fn(pair_signals, pair):
            base = sizing_baseline_atr(
                sigs["atr_b_1m"], sigs["atr_med"],
                {"size_floor": floor, "size_ceiling": ceil},
            )
            return (base * scale_const).clip(floor, ceil).fillna(1.0)
        return fn
    if spec.family == "regime":
        boost_max = float(spec.params.get("boost_max", 1.6))
        fund_thr = float(ind["funding_filter_threshold"])
        funding_ema = sigs["fund_ema_a"]
        def fn(pair_signals, pair):
            return sizing_regime_conditional(
                sigs["atr_b_1m"], sigs["atr_med"],
                {"size_floor": floor, "size_ceiling": ceil},
                fund_allow=sigs["fund_allow"],
                funding_ema=funding_ema,
                fund_thr=fund_thr,
                boost_max=boost_max,
            )
        return fn
    raise ValueError("unknown family: " + spec.family)


def metrics_for_window(port_bar_return: np.ndarray, idx: pd.DatetimeIndex,
                       starting: float, gates: dict) -> dict:
    """Per-window portfolio metrics (uses portfolio-NAV maxDD per the
    iter#82 / SMA-34927 lesson)."""
    sr = sharpe_daily_resampled(port_bar_return, idx)
    pfdd = profit_factor_and_mdd(port_bar_return, starting)
    return {
        "sharpe_daily_resampled": sr["sharpe_daily_resampled"],
        "annualized_return_daily": sr["annualized_return_daily"],
        "max_drawdown_pct": pfdd["max_drawdown_pct"],
        "profit_factor": pfdd["profit_factor"],
        "n_days": sr["n_days"],
    }


def walk_forward_variants(d1m: dict, funding: dict, cfg: dict, variants: list) -> dict:
    """Run walk-forward for every variant; return dict[variant_name] = wfo_dict.

    Per-window signals (z, fund_allow, atr, etc.) are computed ONCE per
    window and reused across all sizing variants — sizing only changes
    the ``size_scale`` Series, so the entry/exit positions and trade
    counts are identical across variants; only per-bar PnL magnitude
    changes.
    """
    wf = cfg["walk_forward"]
    train = int(wf["train_bars_1m"])
    test = int(wf["test_bars_1m"])
    step = int(wf["step_bars_1m"])
    gates = cfg.get("hard_gates", {})
    starting = float(cfg.get("starting_capital_usd", 100000.0))

    n_bars = min(len(df) for df in d1m.values())
    first_index = next(iter(d1m.values())).index

    # windows = anchored expanding-train, advancing by step bars
    test_start = train
    windows = []
    while test_start + test <= n_bars:
        windows.append((0, test_start, test_start, test_start + test))
        test_start += step

    if len(windows) < int(wf.get("min_windows", 3)):
        raise SystemExit("insufficient windows: " + str(windows))

    # Precompute sizing signals once across full data (ATR / funding EMA
    # are bar-static under no shift; they will be sliced per window below
    # by virtue of the per-window data slice).
    sigs_full = compute_sizing_signals(d1m, funding, cfg)

    floor_ceil = {"size_floor": float(cfg["sizing"]["size_floor"]),
                  "size_ceiling": float(cfg["sizing"]["size_ceiling"])}

    def _baseline_size(pair_signals, pair):
        return sizing_baseline_atr(
            sigs_full["atr_b_1m"], sigs_full["atr_med"], floor_ceil,
        )

    from mtf_xs_pairs_base_20260718 import _backtest_pair  # type: ignore

    fee_bps = float(cfg.get("fees_bps_per_side", 1.0))
    slip_bps = float(cfg.get("slippage_bps_per_side", 1.0))

    # Baseline full-data trade log used by Kelly win-rate / payoff
    baseline_full_per_pair = []
    for pair, sig in sigs_full["out"].items():
        size_scale = _baseline_size(sig, pair)
        baseline_full_per_pair.append(_backtest_pair(
            sig, pair, sizing_scale=size_scale,
            fee_bps=fee_bps, slip_bps=slip_bps,
        ))
    baseline_full_trades = []
    for pp in baseline_full_per_pair:
        baseline_full_trades.extend(pp["trades"])

    # ------------------------------------------------------------------
    # Per-window: compute signals once, then run every variant on top.
    # ------------------------------------------------------------------
    per_window_results: list[dict] = []  # per_window_results[i] = {variant_name: per_pair}
    for i, (tr_s, tr_e, te_s, te_e) in enumerate(windows):
        d_win = {sym: df.iloc[te_s:te_e] for sym, df in d1m.items()}
        funding_win = {}
        for sym, f in funding.items():
            fs = f.copy()
            if fs.index.tz is not None:
                fs.index = fs.index.tz_convert(None)
            start_ts = first_index[te_s]
            end_ts = first_index[te_e - 1]
            if start_ts.tz is not None:
                start_ts = start_ts.tz_convert(None)
            if end_ts.tz is not None:
                end_ts = end_ts.tz_convert(None)
            funding_win[sym] = fs[(fs.index >= start_ts) & (fs.index <= end_ts)]
        sigs_win = compute_sizing_signals(d_win, funding_win, cfg)
        per_variant_per_pair = {}
        per_variant_trade_count = {}
        for spec in variants:
            scale_fn = variant_scale_fn(spec, sigs_win, cfg, baseline_full_trades)
            per_pair = []
            for pair, sig in sigs_win["out"].items():
                size_scale = scale_fn(sig, pair)
                per_pair.append(_backtest_pair(
                    sig, pair, sizing_scale=size_scale,
                    fee_bps=fee_bps, slip_bps=slip_bps,
                ))
            per_variant_per_pair[spec.name] = per_pair
            per_variant_trade_count[spec.name] = sum(len(p["trades"]) for p in per_pair)
        per_window_results.append({
            "test_bars": [int(te_s), int(te_e)],
            "test_start_iso": str(first_index[te_s]),
            "test_end_iso": str(first_index[te_e - 1]),
            "n_test_bars": int(te_e - te_s),
            "results": per_variant_per_pair,
            "trade_count": per_variant_trade_count,
        })

    # ------------------------------------------------------------------
    # Aggregate per-variant OOS metrics across windows.
    # ------------------------------------------------------------------
    out = {}
    for spec in variants:
        per_window_metrics = []
        for w in per_window_results:
            per_pair = w["results"][spec.name]
            port = build_portfolio(per_pair, starting_capital=starting)
            if port["n_bars"] > 0:
                idx_win = first_index[w["test_bars"][0]: w["test_bars"][1]]
                m = metrics_for_window(port["bar_return"], idx_win, starting, gates)
            else:
                m = {"sharpe_daily_resampled": 0.0, "annualized_return_daily": 0.0,
                     "max_drawdown_pct": 0.0, "profit_factor": 0.0, "n_days": 0}
            per_window_metrics.append({
                "window_id": len(per_window_metrics),
                "test_bars": w["test_bars"],
                "test_start_iso": w["test_start_iso"],
                "test_end_iso": w["test_end_iso"],
                "n_test_bars": w["n_test_bars"],
                "portfolio": m,
                "n_trades_total": w["trade_count"][spec.name],
            })
        sharpes = np.array([w["portfolio"]["sharpe_daily_resampled"] for w in per_window_metrics])
        rets = np.array([w["portfolio"]["annualized_return_daily"] for w in per_window_metrics])
        mdds = np.array([w["portfolio"]["max_drawdown_pct"] for w in per_window_metrics])
        pfs = np.array([w["portfolio"]["profit_factor"] for w in per_window_metrics])
        rng = np.random.default_rng(int(gates.get("bootstrap_seed", 42)))
        n_resamples = int(gates.get("bootstrap_resamples", 10000))
        if len(sharpes) >= 2:
            means = np.empty(n_resamples)
            for k in range(n_resamples):
                idx = rng.integers(0, len(sharpes), size=len(sharpes))
                means[k] = sharpes[idx].mean()
            boot_lo = float(np.percentile(means, 2.5))
            boot_hi = float(np.percentile(means, 97.5))
        else:
            boot_lo, boot_hi = 0.0, 0.0
        mean_sharpe = float(np.mean(sharpes))
        mean_ret = float(np.mean(rets))
        worst_mdd = float(np.min(mdds))
        mean_pf = float(np.mean(np.where(np.isfinite(pfs), pfs, 0.0)))
        g_sharpe = float(gates.get("oos_sharpe_min", 1.0))
        g_ann = float(gates.get("oos_annualized_min", 0.15))
        g_pf = float(gates.get("profit_factor_min", 1.5))
        g_mdd = float(gates.get("max_drawdown_max_abs_pct", 25.0))
        g_boot = float(gates.get("bootstrap_ci_lower_min", 0.5))
        passed = (mean_sharpe >= g_sharpe) and (mean_ret >= g_ann) \
                 and (mean_pf >= g_pf) and (abs(worst_mdd) <= g_mdd) \
                 and (boot_lo >= g_boot)
        out[spec.name] = {
            "family": spec.family,
            "params": spec.params,
            "n_windows": len(per_window_metrics),
            "oos_sharpe_mean_daily_resampled": mean_sharpe,
            "oos_annualized_mean_daily": mean_ret,
            "oos_max_drawdown_worst_pct": worst_mdd,
            "oos_profit_factor_portfolio": mean_pf,
            "bootstrap_ci_lower": boot_lo,
            "bootstrap_ci_upper": boot_hi,
            "per_window": per_window_metrics,
            "gates": {"sharpe": g_sharpe, "ann": g_ann, "pf": g_pf,
                      "max_abs_mdd_pct": g_mdd, "boot_lo": g_boot},
            "passed": bool(passed),
            "tag": ("PROFITABLE" if passed else "NOT-PROFITABLE"),
        }
    return out


def build_variant_list(cfg: dict) -> list:
    out = []
    for m in [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]:
        out.append(VariantSpec(
            name=f"atr_mult_{m:.2f}".replace(".", "_"),
            family="atr_mult",
            params={"multiplier": m},
        ))
    for t in [10.0, 15.0, 20.0]:
        out.append(VariantSpec(
            name=f"vol_target_{int(t)}pct",
            family="vol_target",
            params={"target_annual_vol_pct": t},
        ))
    for kf in [0.25, 0.5]:
        out.append(VariantSpec(
            name=f"kelly_{int(kf*100)}pct",
            family="kelly",
            params={"kelly_fraction": kf},
        ))
    for bm in [1.6, 2.0]:
        out.append(VariantSpec(
            name=f"regime_boost_{bm:.1f}".replace(".", "_"),
            family="regime",
            params={"boost_max": bm},
        ))
    return out


def main():
    cfg = json.loads(CONFIG_PATH.read_text())
    syms = list(cfg["instruments"])
    print("Loading 1m + funding data for", syms)
    d1m = load_all(syms)
    funding = load_funding(syms)
    for s, df in d1m.items():
        print(" ", s, "rows=", len(df), "span=", df.index[0], "->", df.index[-1])
    for s, f in funding.items():
        print(" ", s, "funding_rows=", len(f), "span=", f.index[0], "->", f.index[-1])

    variants = build_variant_list(cfg)
    print(f"Running {len(variants)} sizing variants over walk-forward windows …")
    results = walk_forward_variants(d1m, funding, cfg, variants)

    # Persist results
    rows = []
    for name, r in results.items():
        rows.append({
            "variant": name,
            "family": r["family"],
            "params": json.dumps(r["params"]),
            "n_windows": r["n_windows"],
            "oos_sharpe_mean": r["oos_sharpe_mean_daily_resampled"],
            "oos_ann_mean": r["oos_annualized_mean_daily"],
            "oos_max_dd_worst": r["oos_max_drawdown_worst_pct"],
            "oos_pf_portfolio": r["oos_profit_factor_portfolio"],
            "boot_ci_lower": r["bootstrap_ci_lower"],
            "boot_ci_upper": r["bootstrap_ci_upper"],
            "passed": r["passed"],
            "tag": r["tag"],
        })
    df = pd.DataFrame(rows).sort_values("oos_pf_portfolio", ascending=False)
    csv_path = RESULTS_DIR / "sizing_sweep.csv"
    df.to_csv(csv_path, index=False)
    print("\n=== sizing sweep (sorted by portfolio PF) ===")
    print(df.to_string(index=False))

    summary = {
        "strategy": cfg["strategy"],
        "hypothesis": cfg["hypothesis"],
        "campaign": cfg.get("campaign"),
        "n_variants": len(variants),
        "gates_passed": [r for r in results.values() if r["passed"]],
        "results": results,
    }
    json_path = RESULTS_DIR / "sizing_sweep.json"
    json_path.write_text(json.dumps(_jsonable(summary), indent=2, default=float))
    print("\nsweep CSV  :", csv_path)
    print("sweep JSON :", json_path)


def _jsonable(obj):
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items() if k != "per_window"}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    return obj


if __name__ == "__main__":
    main()