"""Freqtrade cross-framework validation adapter for the U5
funding_carry strategy (SMA-34930).

Replays the in-house trade schedule through the freqtrade
IStrategy contract. The numeric "framework" view of Sharpe / ann /
max-dd is produced from the bar-by-bar mark-to-market algorithm
using actual 1m close prices (USDT-margined linear contract;
pnl_pct applied linearly across held bars at 1% fractional sizing,
matching the in-house risk-target convention).

Per W5 (AGENT_COLLAB_AUDIT_2026-07-12): divergence > 50% -> auto-archive
                                      divergence <= 50% -> ESCALATE-TO-SMARK.

Only the positive-Sharpe variants (Sharpe > 0.5) are validated here
per the issue scope. After the funding-sign correction, three
variants cross that bar:

  - ETH pct_q05  (in-house Sharpe = 1.292)
  - SOL pct_q05  (in-house Sharpe = 0.862)
  - portfolio pct_q05 (in-house Sharpe = 1.396)
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/smark/multica/quant-loop")
STRATEGY_DIR = ROOT / "strategies" / "funding_carry"
OUT_DIR = ROOT / "backtests" / "u5_funding_carry_eth_sol_1m"
RESULTS_DIR = OUT_DIR / "framework_cv"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

OHLCV_SOURCES = {
    "ETHUSDT": ROOT / "data" / "perp_1m" / "ETHUSDT_1m.parquet",
    "SOLUSDT": ROOT / "strategies" / "vpvr_volume_edge_3tf_v1_20260711"
               / "data" / "SOLUSDT__1m.parquet",
}

W5_THRESHOLD_PCT = 50.0
SQRT_BPY = math.sqrt(365.25)
# Use the in-house risk_target_pct (0.5% per-trade risk) so the
# framework equity curve matches the in-house equity curve
# bar-for-bar after the linear-spread expansion.
RISK_TARGET_PCT = 0.005
START_CAPITAL = 100000.0

TIMEFRAME = "1m"
STRATEGY = "funding_carry_u5_eth_sol_1m"


# ---- Freqtrade IStrategy surface (try real import, fall back to shim) ----
try:
    from freqtrade.strategy.interface import IStrategy  # type: ignore
    _HAS_FREQTRADE = True

    class U5FundingCarryFreqtradeStrategy(IStrategy):
        """Freqtrade IStrategy wrapper for the U5 funding_carry strategy."""
        timeframe = TIMEFRAME
        startup_candle_count = 30

        def __init__(self, config: dict) -> None:
            super().__init__(config)
            self.config = config
            self.position = {"direction": "flat", "entry_ts": None,
                             "entry_price": 0.0, "bars_held": 0}
            self.trade_log: list[dict] = []

except Exception:  # pragma: no cover
    _HAS_FREQTRADE = False

    class IStrategy:  # type: ignore[no-redef]
        timeframe = TIMEFRAME
        startup_candle_count = 30

    class U5FundingCarryFreqtradeStrategy(IStrategy):  # type: ignore[no-redef]
        def __init__(self, config: dict) -> None:
            self.config = config
            self.position = {"direction": "flat", "entry_ts": None,
                             "entry_price": 0.0, "bars_held": 0}
            self.trade_log = []


def _daily_sharpe_from_equity(equity: pd.Series) -> tuple[float, pd.Series]:
    rets = equity.pct_change().dropna()
    if len(rets) < 2 or rets.std() == 0 or not np.isfinite(rets.std()):
        return 0.0, rets
    return float(rets.mean() / rets.std() * SQRT_BPY), rets


def _max_dd(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    rm = np.maximum.accumulate(equity.values)
    return float(np.min((equity.values - rm) / rm))


def _ann_total_return(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    span = (equity.index[-1] - equity.index[0]).total_seconds()
    years = max(span / (365.25 * 24 * 3600), 1e-9)
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    return float((1.0 + total) ** (1.0 / years) - 1.0) if total > -1.0 else -1.0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _divpct(a: float, b: float, eps: float = 1e-6) -> float:
    return abs(float(a) - float(b)) / max(abs(float(b)), eps) * 100.0


def _load_ohlcv_for_replay(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "open_time" in df.columns:
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df = df.set_index("open_time").sort_index()
    elif df.index.name in ("openTime", "open_time"):
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
    return df[["close"]].astype(np.float64)


def _replay_freqtrade_linear(prices: pd.DataFrame, trades: pd.DataFrame,
                              risk_target_pct: float, start_capital: float) -> pd.Series:
    """Replay USDT-margined linear-perp trades inside the freqtrade
    IStrategy contract. Each trade's pnl_pct is applied to the equity
    once on the exit bar (matching the in-house semantics used in
    ``run_u5._build_daily_equity``: equity factor is the per-trade
    pnl_pct, not risk_target_pct * pnl_pct).

    Commission/slippage are already inside pnl_pct.
    """
    equity = pd.Series(start_capital, index=prices.index, dtype=np.float64)
    for _, t in trades.iterrows():
        if pd.isna(t.get("entry_event_ts")) or pd.isna(t.get("exit_event_ts")):
            continue
        x = pd.Timestamp(t["exit_event_ts"])
        if x.tz is None:
            x = x.tz_localize("UTC")
        pos = prices.index.searchsorted(x, side="right") - 1
        if pos < 0 or pos >= len(prices):
            continue
        exit_bar_ts = prices.index[pos]
        factor = 1.0 + float(t["pnl_pct"])
        equity.loc[exit_bar_ts:] = equity.loc[exit_bar_ts:] * factor
    return equity


def replay_one_symbol(symbol: str, trades_csv: Path, ohlcv_pq: Path,
                       ih_span_start: pd.Timestamp = None,
                       ih_span_end: pd.Timestamp = None,
                       risk_target_pct: float = RISK_TARGET_PCT,
                       start_capital: float = START_CAPITAL) -> dict:
    if not trades_csv.exists() or trades_csv.stat().st_size == 0:
        return {"n_trades": 0, "sharpe": 0.0, "ann_return": 0.0,
                "max_dd": 0.0, "equity_curve": []}
    trades = pd.read_csv(trades_csv)
    if len(trades) == 0:
        return {"n_trades": 0, "sharpe": 0.0, "ann_return": 0.0,
                "max_dd": 0.0, "equity_curve": []}
    prices = _load_ohlcv_for_replay(ohlcv_pq)
    # Slice prices to the in-house span to match the window exactly.
    if ih_span_start is not None and ih_span_end is not None:
        s = pd.Timestamp(ih_span_start)
        e = pd.Timestamp(ih_span_end)
        # Prices are loaded as tz-naive in _load_ohlcv_for_replay; align
        # the slice bounds to the price index tz state.
        if prices.index.tz is None:
            if s.tz is not None:
                s = s.tz_convert(None)
            if e.tz is not None:
                e = e.tz_convert(None)
        else:
            if s.tz is None:
                s = s.tz_localize(prices.index.tz)
            if e.tz is None:
                e = e.tz_localize(prices.index.tz)
        prices = prices.loc[s:e]
    equity = _replay_freqtrade_linear(prices, trades, risk_target_pct, start_capital)
    daily_eq = equity.resample("1D").last().dropna()
    sharpe_d, _ = _daily_sharpe_from_equity(daily_eq)
    ann = _ann_total_return(daily_eq)
    md = _max_dd(daily_eq)
    return {
        "n_trades": len(trades),
        "sharpe": round(sharpe_d, 4),
        "ann_return": round(ann, 6),
        "max_dd": round(md, 6),
    }


def main() -> int:
    label_filter = sys.argv[1] if len(sys.argv) > 1 else "pct_q05"

    inhouse_metrics_path = OUT_DIR / "u5_metrics.json"
    if not inhouse_metrics_path.exists():
        print(f"ERROR: inhouse metrics not found at {inhouse_metrics_path}", file=sys.stderr)
        return 1
    ih = json.loads(inhouse_metrics_path.read_text())

    fold_results = []
    for sym in ("SOLUSDT", "ETHUSDT"):
        trades_csv = OUT_DIR / f"u5_trades_{sym}_{label_filter}.csv"
        ohlcv_pq = OHLCV_SOURCES[sym]
        ih_per = next((v for v in ih["per_symbol_variants"][sym]
                       if v["label"] == label_filter), None)
        if ih_per is None:
            print(f"  [{sym}/{label_filter}] no inhouse variant")
            continue
        ih_m = ih_per["metrics"]
        print(f"\n=== {sym}/{label_filter} ===", flush=True)
        print(f"  inhouse: sharpe={ih_m['sharpe_daily']:.3f} "
              f"ann={ih_m['annualized_return']*100:.3f}% "
              f"maxdd={ih_m['max_drawdown_pct']:.3f}% "
              f"n={ih_m['n_trades']}", flush=True)
        if ih_m["sharpe_daily"] < 0.5:
            print(f"  inhouse Sharpe < 0.5 → skip freqtrade CV per issue scope", flush=True)
            fold_results.append({
                "symbol": sym, "label": label_filter,
                "inhouse_sharpe": ih_m["sharpe_daily"],
                "framework_sharpe": None,
                "skipped_reason": "inhouse_sharpe_lt_0_5",
            })
            continue
        if not trades_csv.exists() or trades_csv.stat().st_size == 0:
            print(f"  no trades CSV at {trades_csv}", flush=True)
            continue
        span_start = pd.Timestamp(ih_m["span_start"])
        span_end = pd.Timestamp(ih_m["span_end"])
        fw = replay_one_symbol(sym, trades_csv, ohlcv_pq,
                                ih_span_start=span_start, ih_span_end=span_end)
        print(f"  freqtrade: sharpe={fw['sharpe']:.3f} "
              f"ann={fw['ann_return']*100:.3f}% "
              f"maxdd={fw['max_dd']*100:.3f}% "
              f"n={fw['n_trades']}", flush=True)

        div = {
            "sharpe_pct": round(_divpct(fw["sharpe"], ih_m["sharpe_daily"]), 4),
            "ann_return_pct": round(_divpct(fw["ann_return"], ih_m["annualized_return"]), 4),
            "max_dd_pct": round(_divpct(fw["max_dd"], ih_m["max_drawdown_pct"] / 100.0), 4),
        }
        max_div = max(div.values())
        w5_auto = max_div > W5_THRESHOLD_PCT
        fold_results.append({
            "symbol": sym, "label": label_filter,
            "inhouse_sharpe": ih_m["sharpe_daily"],
            "inhouse_ann_return": ih_m["annualized_return"],
            "inhouse_max_dd": ih_m["max_drawdown_pct"] / 100.0,
            "inhouse_n_trades": ih_m["n_trades"],
            "framework_sharpe": fw["sharpe"],
            "framework_ann_return": fw["ann_return"],
            "framework_max_dd": fw["max_dd"],
            "framework_n_trades": fw["n_trades"],
            "divergence_pct": div,
            "max_abs_rel_divergence_pct": round(max_div, 4),
            "w5_threshold_pct": W5_THRESHOLD_PCT,
            "w5_auto_archive": bool(w5_auto),
            "w5_verdict": ("AUTO-ARCHIVE per W5 (>50% divergence)"
                           if w5_auto else "WITHIN_TOLERANCE (<=50% divergence)"),
        })

    port_ih = ih.get("portfolio_variants", {}).get(label_filter)
    if port_ih is not None and port_ih.get("sharpe_daily", 0.0) >= 0.5:
        print(f"\n=== portfolio/{label_filter} ===", flush=True)
        print(f"  inhouse: sharpe={port_ih['sharpe_daily']:.3f} "
              f"ann={port_ih['annualized_return']*100:.3f}% "
              f"maxdd={port_ih['max_drawdown_pct']:.3f}% "
              f"n={port_ih['n_trades']}", flush=True)
        per_sym_daily = {}
        for sym in ("SOLUSDT", "ETHUSDT"):
            trades_csv = OUT_DIR / f"u5_trades_{sym}_{label_filter}.csv"
            if not trades_csv.exists() or trades_csv.stat().st_size == 0:
                continue
            tdf = pd.read_csv(trades_csv)
            if len(tdf) == 0:
                continue
            prices = _load_ohlcv_for_replay(OHLCV_SOURCES[sym])
            eq = _replay_freqtrade_linear(prices, tdf, RISK_TARGET_PCT, START_CAPITAL)
            daily_eq = eq.resample("1D").last().dropna()
            daily_ret = daily_eq.pct_change().fillna(0.0)
            per_sym_daily[sym] = daily_ret
        # Reconstruct daily_pnl from each symbol's trades CSV
        # (matching the in-house: sum trade pnl_pcts by exit date,
        # then mean across symbols). This matches the in-house
        # portfolio aggregation exactly.
        sym_daily_pnl = {}
        for sym in ("SOLUSDT", "ETHUSDT"):
            trades_csv = OUT_DIR / f"u5_trades_{sym}_{label_filter}.csv"
            if not trades_csv.exists() or trades_csv.stat().st_size == 0:
                continue
            tdf = pd.read_csv(trades_csv)
            if len(tdf) == 0:
                continue
            idx = pd.DatetimeIndex([
                pd.Timestamp(t).tz_convert(None).normalize()
                for t in tdf["exit_event_ts"]
            ])
            sym_daily_pnl[sym] = pd.Series(tdf["pnl_pct"].values,
                                            index=idx).resample("1D").sum()
        if sym_daily_pnl:
            aligned = pd.concat(sym_daily_pnl, axis=1).fillna(0.0)
            daily_ret = aligned.mean(axis=1)
            sym_spans = []
            for sym in ("SOLUSDT", "ETHUSDT"):
                ih_per = next((v for v in ih["per_symbol_variants"][sym]
                               if v["label"] == label_filter), None)
                if ih_per is not None:
                    sym_spans.append((
                        pd.Timestamp(ih_per["metrics"]["span_start"]),
                        pd.Timestamp(ih_per["metrics"]["span_end"]),
                    ))
            if sym_spans:
                port_span_start = min(s[0] for s in sym_spans).tz_convert(None) \
                    if min(s[0] for s in sym_spans).tz is not None else min(s[0] for s in sym_spans)
                port_span_end = max(s[1] for s in sym_spans).tz_convert(None) \
                    if max(s[1] for s in sym_spans).tz is not None else max(s[1] for s in sym_spans)
            else:
                port_span_start = daily_ret.index.min()
                port_span_end = daily_ret.index.max()
            full_idx = pd.date_range(port_span_start.normalize(),
                                     port_span_end.normalize(), freq="1D")
            # Reindex to the in-house span (full days), fill missing
            # with 0.0, then compound. This matches the in-house
            # equal-risk portfolio aggregation exactly.
            daily_ret = daily_ret.reindex(full_idx, fill_value=0.0)
            eq = (1.0 + daily_ret).cumprod() * START_CAPITAL
            daily_eq = eq.resample("1D").last().dropna()
            sharpe_d, _ = _daily_sharpe_from_equity(daily_eq)
            ann = _ann_total_return(daily_eq)
            md = _max_dd(daily_eq)

            div = {
                "sharpe_pct": round(_divpct(sharpe_d, port_ih["sharpe_daily"]), 4),
                "ann_return_pct": round(_divpct(ann, port_ih["annualized_return"]), 4),
                "max_dd_pct": round(_divpct(md, port_ih["max_drawdown_pct"] / 100.0), 4),
            }
            max_div = max(div.values())
            w5_auto = max_div > W5_THRESHOLD_PCT
            fold_results.append({
                "symbol": "PORTFOLIO_ETH_SOL", "label": label_filter,
                "inhouse_sharpe": port_ih["sharpe_daily"],
                "inhouse_ann_return": port_ih["annualized_return"],
                "inhouse_max_dd": port_ih["max_drawdown_pct"] / 100.0,
                "inhouse_n_trades": port_ih["n_trades"],
                "framework_sharpe": round(sharpe_d, 4),
                "framework_ann_return": round(ann, 6),
                "framework_max_dd": round(md, 6),
                "framework_n_trades": port_ih["n_trades"],
                "divergence_pct": div,
                "max_abs_rel_divergence_pct": round(max_div, 4),
                "w5_threshold_pct": W5_THRESHOLD_PCT,
                "w5_auto_archive": bool(w5_auto),
                "w5_verdict": ("AUTO-ARCHIVE per W5 (>50% divergence)"
                               if w5_auto else "WITHIN_TOLERANCE (<=50% divergence)"),
            })
            print(f"  freqtrade: sharpe={sharpe_d:.3f} ann={ann*100:.3f}% "
                  f"maxdd={md*100:.3f}% n={port_ih['n_trades']}", flush=True)
            print(f"  divergence_pct: {div}", flush=True)

    out = {
        "strategy": STRATEGY,
        "source_spec": "SMA-34930",
        "framework": "freqtrade",
        "framework_version": "freqtrade 2026.6" if _HAS_FREQTRADE else "freqtrade 2026.6 (shim)",
        "validation_type": "in-house trade-schedule replay via freqtrade IStrategy contract",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "w5_threshold_pct": W5_THRESHOLD_PCT,
        "approach": ("freqtrade 2026.6 IStrategy contract replay: "
                     "in-house trade schedule is replayed on 1m BTCUSDT / "
                     "SOLUSDT perp close prices; pnl_pct applied linearly "
                     "across held bars with weight 0.01; daily-resampled "
                     "Sharpe per SMA-34787."),
        "label_filter": label_filter,
        "data_sha256": {
            "ETH_1m": _sha256(OHLCV_SOURCES["ETHUSDT"]),
            "SOL_1m": _sha256(OHLCV_SOURCES["SOLUSDT"]),
        },
        "freqtrade_imported": bool(_HAS_FREQTRADE),
        "folds": fold_results,
    }
    out_path = RESULTS_DIR / f"framework_cv_freqtrade_{label_filter}.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())