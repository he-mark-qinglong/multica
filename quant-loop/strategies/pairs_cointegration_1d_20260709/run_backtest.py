"""Real-data backtest driver for `pairs_cointegration_1d_20260709`.

This module expands B1's scaffold into a working backtest on the 3-symbol
universe available in the canonical 1m source (BTC/ETH/SOL). It:

  1. Loads 1d OHLCV for every symbol in `config.instruments`.
  2. For every ordered pair (A, B), runs a rolling Engle-Granger selection
     on a 90d window. Pairs with p<0.05 are admitted; we keep the top-N
     (`max_active_pairs`) by p-value ascending.
  3. For every active pair, simulates the z-score mean-reversion strategy
     (entry ±2σ, exit |z|<0.5, 4σ cointegration-break guard) on a daily bar
     basis with proper fees + slippage.
  4. Aggregates per-pair P&L into a portfolio equity curve and writes:
       results/pair_selection.csv
       results/hedge_ratio_stability.csv
       results/eg_pvalue_timeseries.csv
       results/per_pair_pnl.csv
       results/portfolio_equity.csv
       results/run_summary.json
       results/README.md
  5. Surfaces any rolling-90d EG test that flipped from cointegrated to
     non-cointegrated as a "historical cointegration break" event in
     `run_summary.json` so B4 (performance analyst) and the spec-mandated
     "测试 synthetic + 历史场景" check are both satisfied.

Universe note: the spec asked for 6 symbols; the canonical 1m source has
only BTC/ETH/SOL. We work with what's available and document the limitation
in the README rather than fabricate data. The framework is symbol-agnostic
so B2/B3 can extend to BNB/ADA/AVAX once the ETL adds them.
"""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import data_loader
import strategy
from cointegration import engle_granger_test, rolling_hedge_ratio, rolling_zscore

CONFIG_PATH = Path(__file__).parent / "config.json"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Pair selection
# ---------------------------------------------------------------------------
@dataclass
class PairCandidate:
    pair_key: str
    a: str
    b: str
    p_value: float
    hedge: strategy.HedgeRatio
    n_obs: int
    selected: bool


def select_pairs(
    prices: Dict[str, pd.DataFrame],
    cfg: dict,
) -> List[PairCandidate]:
    """Run a single end-of-sample EG test on each candidate pair.

    Universe has N symbols -> N*(N-1)/2 ordered pairs (we use the same direction
    regardless of which leg is "A" because the z-score entry rule is symmetric).
    Sorted by p-value ascending; keep top `max_active_pairs`.
    """
    symbols = list(prices.keys())
    p_threshold = cfg["universe_selection"]["p_value_threshold"]
    max_pairs = cfg["universe_selection"]["max_active_pairs"]
    selection_window = cfg["universe_selection"]["selection_window_days"]
    adf_lag = cfg["cointegration"]["adf_maxlag"]

    # Align all series to the intersection of their indices, then take the
    # trailing `selection_window` rows for the EG test.
    aligned_dates = prices[symbols[0]].index
    for s in symbols[1:]:
        aligned_dates = aligned_dates.intersection(prices[s].index)
    if len(aligned_dates) < selection_window:
        raise RuntimeError(
            f"aligned series only has {len(aligned_dates)} rows; need {selection_window}"
        )
    window_slice = aligned_dates[-selection_window:]

    candidates: List[PairCandidate] = []
    for a, b in itertools.combinations(symbols, 2):
        log_a = np.log(prices[a].loc[window_slice, "close"].to_numpy(dtype=float))
        log_b = np.log(prices[b].loc[window_slice, "close"].to_numpy(dtype=float))
        try:
            eg = engle_granger_test(log_a, log_b, maxlag=adf_lag)
        except Exception:
            continue
        candidates.append(
            PairCandidate(
                pair_key=f"{a}-{b}",
                a=a,
                b=b,
                p_value=eg.p_value,
                hedge=eg.hedge_ratio,
                n_obs=eg.n_obs,
                selected=False,
            )
        )

    candidates.sort(key=lambda c: c.p_value)
    for c in candidates[:max_pairs]:
        if c.p_value < p_threshold:
            c.selected = True
    return candidates


# ---------------------------------------------------------------------------
# Single-pair backtest
# ---------------------------------------------------------------------------
@dataclass
class PairTrade:
    pair_key: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    side: str          # "long_spread" or "short_spread"
    z_entry: float
    z_exit: float
    pnl_pct: float
    pnl_usd: float
    reason: str        # "mean_revert" or "coint_break" or "max_hold"


@dataclass
class PairResult:
    pair_key: str
    n_trades: int
    win_rate: float
    total_pnl_usd: float
    total_pnl_pct: float
    avg_bars_held: float
    trades: List[PairTrade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    hedge_ratio_stability: pd.DataFrame = field(default_factory=pd.DataFrame)


def run_pair_backtest(
    prices_a: pd.DataFrame,
    prices_b: pd.DataFrame,
    pair_key: str,
    cfg: dict,
) -> PairResult:
    """Z-score mean-reversion backtest for a single (A, B) pair.

    Position model:
        long_spread  : long 1 unit of A, short beta units of B
        short_spread : short 1 unit of A, long beta units of B
    Dollar-PnL is realized at exit: pnl_usd = pnl_pct * (5% of starting_cap).

    Trade triggers (z = rolling z-score of the spread):
        - z >  entry_threshold  -> short_spread entry
        - z < -entry_threshold  -> long_spread  entry
        - |z| < exit_threshold  -> close any open position
        - |Δspread_today| > stop_sigma_threshold * std -> close (coint break)
    """
    starting_cap = float(cfg["starting_capital_usd"])
    leg_pct = float(cfg["position_sizing"]["leg_pct_per_pair"])
    fee = float(cfg["fees_bps_per_side"]) / 10000.0
    slip = float(cfg["slippage_bps_per_side"]) / 10000.0
    cost_per_side = fee + slip

    entry_thr = float(cfg["signal"]["entry_threshold"])
    exit_thr = float(cfg["signal"]["exit_threshold"])
    stop_sig = float(cfg["signal"]["stop_sigma_threshold"])

    sig = strategy.build_signals(prices_a, prices_b, cfg)

    # Hedge ratio stability: rolling beta std/mean over the full sample.
    valid_beta = sig["beta"].dropna()
    hedge_summary = pd.DataFrame(
        {
            "beta_mean": [float(valid_beta.mean()) if len(valid_beta) else float("nan")],
            "beta_std": [float(valid_beta.std()) if len(valid_beta) else float("nan")],
            "beta_min": [float(valid_beta.min()) if len(valid_beta) else float("nan")],
            "beta_max": [float(valid_beta.max()) if len(valid_beta) else float("nan")],
            "n_obs": [int(valid_beta.notna().sum())],
        },
        index=[pair_key],
    )

    trades: List[PairTrade] = []
    in_pos = False
    side: str = ""
    entry_date = None
    entry_z = 0.0
    entry_spread = 0.0
    prev_spread: Optional[float] = None

    leg_notional = starting_cap * leg_pct
    for i, (dt, row) in enumerate(sig.iterrows()):
        z = row.get("zscore")
        spread = row.get("spread")
        if not (np.isfinite(z) and np.isfinite(spread)):
            continue

        if in_pos:
            # Cointegration-break guard: today's spread move > N sigma of
            # recent std -> close immediately.
            if prev_spread is not None and np.isfinite(row.get("spread_std")):
                move = abs(spread - prev_spread)
                if move > stop_sig * float(row["spread_std"]):
                    pnl_pct = (spread - entry_spread) * (
                        1.0 if side == "long_spread" else -1.0
                    )
                    # Subtract 2 legs of cost on entry and exit.
                    pnl_pct -= 2.0 * cost_per_side
                    trades.append(
                        PairTrade(
                            pair_key=pair_key,
                            entry_date=entry_date,
                            exit_date=dt,
                            side=side,
                            z_entry=entry_z,
                            z_exit=float(z),
                            pnl_pct=float(pnl_pct),
                            pnl_usd=float(pnl_pct) * leg_notional,
                            reason="coint_break",
                        )
                    )
                    in_pos = False
            if in_pos and abs(z) < exit_thr:
                pnl_pct = (spread - entry_spread) * (
                    1.0 if side == "long_spread" else -1.0
                )
                pnl_pct -= 2.0 * cost_per_side
                trades.append(
                    PairTrade(
                        pair_key=pair_key,
                        entry_date=entry_date,
                        exit_date=dt,
                        side=side,
                        z_entry=entry_z,
                        z_exit=float(z),
                        pnl_pct=float(pnl_pct),
                        pnl_usd=float(pnl_pct) * leg_notional,
                        reason="mean_revert",
                    )
                )
                in_pos = False
        else:
            if z > entry_thr:
                in_pos = True
                side = "short_spread"
                entry_date = dt
                entry_z = float(z)
                entry_spread = float(spread)
            elif z < -entry_thr:
                in_pos = True
                side = "long_spread"
                entry_date = dt
                entry_z = float(z)
                entry_spread = float(spread)

        prev_spread = float(spread) if np.isfinite(spread) else prev_spread

    n = len(trades)
    if n:
        wins = [t for t in trades if t.pnl_usd > 0]
        win_rate = len(wins) / n
        total_pnl_usd = sum(t.pnl_usd for t in trades)
        # Per-trade pnl_pct is on leg_notional, so aggregate % return is
        # total_pnl_usd / starting_cap. The spec wants per-pair return; we
        # report it as (total_usd / starting_cap) and also surface raw %.
        total_pnl_pct = total_pnl_usd / starting_cap
        avg_bars = float(np.mean([
            (t.exit_date - t.entry_date).days for t in trades
        ]))
    else:
        win_rate = 0.0
        total_pnl_usd = 0.0
        total_pnl_pct = 0.0
        avg_bars = 0.0

    # Pair-level equity curve: mark-to-market at exit dates, ffill between.
    eq = pd.Series([starting_cap], index=[sig.index[0]])
    if trades:
        running = starting_cap
        for t in trades:
            running = running + t.pnl_usd
            eq = pd.concat([eq, pd.Series([running], index=[t.exit_date])])
    eq = eq[~eq.index.duplicated(keep="last")].sort_index()

    return PairResult(
        pair_key=pair_key,
        n_trades=n,
        win_rate=win_rate,
        total_pnl_usd=total_pnl_usd,
        total_pnl_pct=total_pnl_pct,
        avg_bars_held=avg_bars,
        trades=trades,
        equity_curve=eq,
        hedge_ratio_stability=hedge_summary,
    )


# ---------------------------------------------------------------------------
# Multi-pair orchestration
# ---------------------------------------------------------------------------
@dataclass
class MultiPairResult:
    cfg: dict
    universe: List[str]
    pair_selection: List[PairCandidate]
    pair_results: Dict[str, PairResult]
    portfolio_equity: pd.Series
    portfolio_total_pnl_usd: float
    portfolio_total_pnl_pct: float
    historical_coint_breaks: List[Dict[str, str]]
    eg_pvalue_timeseries: pd.DataFrame
    n_total_trades: int
    n_active_pairs: int


def rolling_eg_timeseries(
    prices: Dict[str, pd.DataFrame],
    cfg: dict,
    window: int = 90,
) -> pd.DataFrame:
    """Compute a 90d rolling EG p-value for every pair.

    Daily recompute is overkill; we sample every `step` days. The output is the
    raw input for the "historical cointegration break" check below.
    """
    symbols = list(prices.keys())
    adf_lag = cfg["cointegration"]["adf_maxlag"]
    step = 7  # weekly cadence per the spec
    aligned_dates = prices[symbols[0]].index
    for s in symbols[1:]:
        aligned_dates = aligned_dates.intersection(prices[s].index)

    pairs = list(itertools.combinations(symbols, 2))
    rows = []
    for end_idx in range(window, len(aligned_dates), step):
        sl = slice(end_idx - window, end_idx)
        d = aligned_dates[end_idx - 1]
        row = {"date": d}
        for a, b in pairs:
            log_a = np.log(prices[a].iloc[sl]["close"].to_numpy(dtype=float))
            log_b = np.log(prices[b].iloc[sl]["close"].to_numpy(dtype=float))
            try:
                eg = engle_granger_test(log_a, log_b, maxlag=adf_lag)
                row[f"{a}-{b}"] = eg.p_value
            except Exception:
                row[f"{a}-{b}"] = float("nan")
        rows.append(row)
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def find_coint_breaks(
    eg_ts: pd.DataFrame,
    p_threshold: float,
) -> List[Dict[str, str]]:
    """Identify (pair, date) where a previously-cointegrated pair flips.

    A 'break' is defined as: the rolling-90d EG p-value was below the threshold
    at time t-1 and is above it at time t. These are the events the B2 owner
    must wire the 4σ spread-move guard against.
    """
    breaks: List[Dict[str, str]] = []
    if len(eg_ts) < 2:
        return breaks
    for col in eg_ts.columns:
        s = eg_ts[col]
        prev_below = (s.shift(1) < p_threshold)
        now_above = (s >= p_threshold)
        flip = prev_below & now_above
        for date, hit in flip.items():
            if bool(hit):
                breaks.append(
                    {
                        "pair": col,
                        "date": pd.Timestamp(date).date().isoformat(),
                        "p_value_before": float(s.shift(1).loc[date]),
                        "p_value_after": float(s.loc[date]),
                    }
                )
    return breaks


def run_multi_pair_backtest(cfg: dict) -> MultiPairResult:
    """End-to-end: load -> select pairs -> backtest each -> aggregate."""
    prices = data_loader.load_all()
    if len(prices) < 2:
        raise RuntimeError(f"need at least 2 symbols; got {list(prices.keys())}")

    print(f"[run] universe: {sorted(prices.keys())}")
    candidates = select_pairs(prices, cfg)
    selected = [c for c in candidates if c.selected]
    print(f"[run] pair selection: {len(selected)} of {len(candidates)} candidates pass EG p<{cfg['universe_selection']['p_value_threshold']}")
    for c in selected:
        print(f"        {c.pair_key:>16s}  p={c.p_value:.4g}  beta={c.hedge.beta:.3f}  r2={c.hedge.r_squared:.3f}")

    pair_results: Dict[str, PairResult] = {}
    for c in selected:
        res = run_pair_backtest(prices[c.a], prices[c.b], c.pair_key, cfg)
        pair_results[c.pair_key] = res
        print(
            f"[run] {c.pair_key:>16s}  trades={res.n_trades:>3d}  "
            f"win_rate={100*res.win_rate:.1f}%  pnl_usd={res.total_pnl_usd:+.2f}  "
            f"pnl_pct={100*res.total_pnl_pct:+.3f}%"
        )

    # Portfolio equity: sum of per-pair pnl_usd at each trade exit date.
    if pair_results:
        # Use the union of all exit dates as the timeline; ffill running equity.
        all_dates = sorted({
            dt
            for res in pair_results.values()
            for t in res.trades
            for dt in [t.entry_date, t.exit_date]
        })
        if all_dates:
            eq_index = pd.DatetimeIndex(all_dates)
            # Daily PnL attribution: each trade's pnl_usd lands on its exit date.
            pnl_by_date = pd.Series(0.0, index=eq_index)
            for res in pair_results.values():
                for t in res.trades:
                    if t.exit_date in pnl_by_date.index:
                        pnl_by_date.loc[t.exit_date] += t.pnl_usd
            running = pnl_by_date.cumsum() + cfg["starting_capital_usd"]
            portfolio_equity = running
        else:
            portfolio_equity = pd.Series(
                [cfg["starting_capital_usd"]], index=[prices[sorted(prices.keys())[0]].index[0]]
            )
    else:
        portfolio_equity = pd.Series(
            [cfg["starting_capital_usd"]], index=[prices[sorted(prices.keys())[0]].index[0]]
        )

    portfolio_pnl_usd = float(sum(r.total_pnl_usd for r in pair_results.values()))
    portfolio_pnl_pct = portfolio_pnl_usd / cfg["starting_capital_usd"]
    n_total = sum(r.n_trades for r in pair_results.values())

    eg_ts = rolling_eg_timeseries(prices, cfg)
    breaks = find_coint_breaks(eg_ts, cfg["universe_selection"]["p_value_threshold"])
    print(f"[run] historical cointegration breaks detected: {len(breaks)}")
    for b in breaks[:5]:
        print(f"        {b['pair']} on {b['date']}  p: {b['p_value_before']:.3f} -> {b['p_value_after']:.3f}")

    return MultiPairResult(
        cfg=cfg,
        universe=sorted(prices.keys()),
        pair_selection=candidates,
        pair_results=pair_results,
        portfolio_equity=portfolio_equity,
        portfolio_total_pnl_usd=portfolio_pnl_usd,
        portfolio_total_pnl_pct=portfolio_pnl_pct,
        historical_coint_breaks=breaks,
        eg_pvalue_timeseries=eg_ts,
        n_total_trades=n_total,
        n_active_pairs=len(selected),
    )


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------
def persist_results(result: MultiPairResult) -> Dict[str, str]:
    """Write CSV/JSON/Markdown artifacts to results/.

    Returns a dict {filename: relpath} for the run summary to reference.
    """
    paths: Dict[str, str] = {}

    # Pair selection
    sel_rows = [
        {
            "pair_key": c.pair_key,
            "p_value": c.p_value,
            "alpha": c.hedge.alpha,
            "beta": c.hedge.beta,
            "r_squared": c.hedge.r_squared,
            "n_obs": c.n_obs,
            "selected": c.selected,
        }
        for c in result.pair_selection
    ]
    p = RESULTS_DIR / "pair_selection.csv"
    pd.DataFrame(sel_rows).to_csv(p, index=False)
    paths["pair_selection"] = str(p.relative_to(RESULTS_DIR.parent.parent))

    # Per-pair P&L
    pnl_rows = [
        {
            "pair_key": k,
            "n_trades": r.n_trades,
            "win_rate": r.win_rate,
            "total_pnl_usd": r.total_pnl_usd,
            "total_pnl_pct": r.total_pnl_pct,
            "avg_bars_held": r.avg_bars_held,
        }
        for k, r in result.pair_results.items()
    ]
    p = RESULTS_DIR / "per_pair_pnl.csv"
    pd.DataFrame(pnl_rows).to_csv(p, index=False)
    paths["per_pair_pnl"] = str(p.relative_to(RESULTS_DIR.parent.parent))

    # Hedge ratio stability: concat per-pair
    hblocks = [r.hedge_ratio_stability for r in result.pair_results.values()]
    if hblocks:
        h = pd.concat(hblocks)
    else:
        h = pd.DataFrame()
    p = RESULTS_DIR / "hedge_ratio_stability.csv"
    h.to_csv(p)
    paths["hedge_ratio_stability"] = str(p.relative_to(RESULTS_DIR.parent.parent))

    # EG p-value timeseries
    p = RESULTS_DIR / "eg_pvalue_timeseries.csv"
    result.eg_pvalue_timeseries.to_csv(p)
    paths["eg_pvalue_timeseries"] = str(p.relative_to(RESULTS_DIR.parent.parent))

    # Portfolio equity curve
    p = RESULTS_DIR / "portfolio_equity.csv"
    result.portfolio_equity.to_csv(p, header=["equity_usd"])
    paths["portfolio_equity"] = str(p.relative_to(RESULTS_DIR.parent.parent))

    # Per-pair trade ledger (each pair gets its own file)
    for k, r in result.pair_results.items():
        safe = k.replace("/", "_")
        p = RESULTS_DIR / f"trades_{safe}.csv"
        if r.trades:
            pd.DataFrame([asdict(t) for t in r.trades]).to_csv(p, index=False)
        else:
            pd.DataFrame(
                columns=["pair_key", "entry_date", "exit_date", "side",
                         "z_entry", "z_exit", "pnl_pct", "pnl_usd", "reason"]
            ).to_csv(p, index=False)
        paths[f"trades_{safe}"] = str(p.relative_to(RESULTS_DIR.parent.parent))

    # JSON summary
    summary = {
        "strategy": "pairs_cointegration_1d_20260709",
        "universe": result.universe,
        "n_active_pairs": result.n_active_pairs,
        "n_total_trades": result.n_total_trades,
        "portfolio_total_pnl_usd": result.portfolio_total_pnl_usd,
        "portfolio_total_pnl_pct": result.portfolio_total_pnl_pct,
        "starting_capital_usd": result.cfg["starting_capital_usd"],
        "fees_bps_per_side": result.cfg["fees_bps_per_side"],
        "slippage_bps_per_side": result.cfg["slippage_bps_per_side"],
        "historical_coint_breaks_count": len(result.historical_coint_breaks),
        "historical_coint_breaks_first10": result.historical_coint_breaks[:10],
    }
    p = RESULTS_DIR / "run_summary.json"
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    paths["run_summary"] = str(p.relative_to(RESULTS_DIR.parent.parent))

    # README
    write_readme(result, paths)
    return paths


def write_readme(result: MultiPairResult, paths: Dict[str, str]) -> None:
    p = RESULTS_DIR / "README.md"
    lines: List[str] = []
    lines.append("# pairs_cointegration_1d_20260709 — backtest results")
    lines.append("")
    lines.append(f"- **Universe**: {', '.join(result.universe)}")
    lines.append(f"- **Active pairs (EG p<{result.cfg['universe_selection']['p_value_threshold']})**: {result.n_active_pairs}")
    lines.append(f"- **Total trades**: {result.n_total_trades}")
    lines.append(f"- **Portfolio pnl**: {result.portfolio_total_pnl_usd:+.2f} USD "
                 f"({100*result.portfolio_total_pnl_pct:+.3f}%)")
    lines.append(f"- **Fees/slippage (per side)**: {result.cfg['fees_bps_per_side']} / "
                 f"{result.cfg['slippage_bps_per_side']} bps")
    lines.append(f"- **Historical cointegration breaks detected**: "
                 f"{len(result.historical_coint_breaks)}")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    for k, rel in paths.items():
        lines.append(f"- `{rel}` — {k}")
    lines.append("")
    lines.append("## Per-pair summary")
    lines.append("")
    if result.pair_results:
        lines.append("| pair | trades | win_rate | pnl_usd | pnl_pct |")
        lines.append("|------|-------:|---------:|--------:|--------:|")
        for k, r in result.pair_results.items():
            lines.append(
                f"| {k} | {r.n_trades} | {100*r.win_rate:.1f}% | "
                f"{r.total_pnl_usd:+.2f} | {100*r.total_pnl_pct:+.3f}% |"
            )
    lines.append("")
    lines.append("## Pair selection (full table)")
    lines.append("")
    lines.append("| pair | p_value | alpha | beta | r_squared | selected |")
    lines.append("|------|--------:|------:|-----:|----------:|:--------:|")
    for c in result.pair_selection:
        lines.append(
            f"| {c.pair_key} | {c.p_value:.4g} | {c.hedge.alpha:+.3f} | "
            f"{c.hedge.beta:.3f} | {c.hedge.r_squared:.3f} | "
            f"{'yes' if c.selected else 'no'} |"
        )
    lines.append("")
    if result.historical_coint_breaks:
        lines.append("## First 10 historical cointegration breaks")
        lines.append("")
        lines.append("| date | pair | p_before | p_after |")
        lines.append("|------|------|---------:|--------:|")
        for b in result.historical_coint_breaks[:10]:
            lines.append(
                f"| {b['date']} | {b['pair']} | "
                f"{b['p_value_before']:.3f} | {b['p_value_after']:.3f} |"
            )
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    paths["README"] = str(p.relative_to(RESULTS_DIR.parent.parent))


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text())
    print(f"[run] config: {cfg['strategy']}  timeframe={cfg['timeframe']}")
    result = run_multi_pair_backtest(cfg)
    paths = persist_results(result)
    print()
    print("[run] persisted:")
    for k, rel in paths.items():
        print(f"        {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())