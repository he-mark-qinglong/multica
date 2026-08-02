"""Long-only cross-sectional momentum research on 1h spot (7 symbols).

Research questions (see REPORT.md):
1. Does long-only XS momentum survive at longer horizons than the decayed
   15m variant? Grid: lookback {24,48,72,168}h x hold {8,24,72}h.
2. Does an ER(24h) > 0.4 trend-regime filter revive the decayed signal?
3. 7-symbol universe vs the original 3 (BTC/ETH/SOL).
4. Return correlation with hedged_grid_v1 (active when ER < 0.3) — the two
   are natural complements (trend vs chop).

Pure-function core: every computation takes data in, returns data out.
Only `load_closes` / `load_grid_equity` / `main` touch the filesystem.

Conventions:
- Entry/exit at bar close, non-overlapping trades (step = hold).
- Fee: 10 bp taker per side (spot), i.e. 20 bp round trip, matches
  hedged_grid_v1's spot_fee_bp.
- t-stat is per-trade: mean / std * sqrt(n), no annualization.
- ER = Kaufman efficiency ratio, |P_t - P_{t-n}| / sum|P_i - P_{i-1}|.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/mark/multica/quant-loop")
OUT_DIR = Path(__file__).resolve().parent

SYMBOLS_7 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
             "AVAXUSDT", "LINKUSDT", "DOGEUSDT"]
SYMBOLS_3 = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

LOOKBACKS = [24, 48, 72, 168]
HOLDS = [8, 24, 72]
FEE_BP = 10.0          # per side
ER_WINDOW = 24         # bars (1h bars -> 24h)
ER_THRESHOLD = 0.4


# ---------------------------------------------------------------------------
# Data loading (the only I/O besides main)
# ---------------------------------------------------------------------------

def load_closes(symbols: list[str], root: Path = ROOT) -> pd.DataFrame:
    """Hourly close prices, UTC datetime index, one column per symbol."""
    cols = {}
    for sym in symbols:
        df = pd.read_parquet(root / "data" / "spot" / f"{sym}_1h.parquet")
        idx = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        cols[sym] = pd.Series(df["close"].to_numpy(), index=idx, name=sym)
    closes = pd.DataFrame(cols).sort_index()
    closes = closes[~closes.index.duplicated(keep="last")]
    return closes


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------

def efficiency_ratio(close: pd.Series, period: int) -> pd.Series:
    """Kaufman ER: |net move| / sum of |1-bar moves| over `period` bars."""
    net = (close - close.shift(period)).abs()
    path = close.diff().abs().rolling(period).sum()
    return net / path.replace(0.0, np.nan)


def momentum_trades(closes: pd.DataFrame, lookback: int, hold: int,
                    fee_bp: float = FEE_BP, top_n: int = 1,
                    er_window: int = ER_WINDOW,
                    btc_symbol: str = "BTCUSDT") -> pd.DataFrame:
    """Non-overlapping long-only XS-momentum trades.

    At each slot t (spaced `hold` bars apart), rank symbols by return over
    the past `lookback` bars, go long the top `top_n` (equal weight), exit
    at t + hold. Returns one row per slot with gross/net return and the ER
    of the picked basket and of BTC at entry (for regime filtering).
    """
    px = closes.dropna(axis=0, how="any")
    if len(px) < lookback + hold + 1:
        return pd.DataFrame(columns=["entry_time", "year", "symbols",
                                     "gross_ret", "net_ret",
                                     "er_pick", "er_btc"])

    er = px.apply(lambda c: efficiency_ratio(c, er_window))
    er_btc = er[btc_symbol] if btc_symbol in er.columns else er.iloc[:, 0]

    fee = fee_bp / 1e4
    rows = []
    n = len(px)
    i = lookback
    while i + hold < n:
        now = px.iloc[i]
        past = px.iloc[i - lookback]
        fwd = px.iloc[i + hold]
        lb_ret = now / past - 1.0
        picks = lb_ret.sort_values(ascending=False).head(top_n)
        gross = float((fwd[picks.index] / now[picks.index] - 1.0).mean())
        t = px.index[i]
        rows.append({
            "entry_time": t,
            "year": t.year,
            "symbols": ",".join(picks.index),
            "gross_ret": gross,
            "net_ret": gross - 2.0 * fee,
            "er_pick": float(er.loc[t, picks.index].mean()),
            "er_btc": float(er_btc.loc[t]),
        })
        i += hold
    return pd.DataFrame(rows)


def trade_stats(trades: pd.DataFrame, ret_col: str = "net_ret") -> dict:
    """Summary stats for a set of trades."""
    r = trades[ret_col].dropna()
    n = len(r)
    if n == 0:
        return {"n": 0, "mean_bp": np.nan, "t": np.nan,
                "winrate": np.nan, "pf": np.nan}
    std = r.std(ddof=1)
    t = float(r.mean() / std * np.sqrt(n)) if n > 1 and std > 0 else np.nan
    gains = r[r > 0].sum()
    losses = -r[r < 0].sum()
    return {
        "n": n,
        "mean_bp": float(r.mean() * 1e4),
        "t": t,
        "winrate": float((r > 0).mean()),
        "pf": float(gains / losses) if losses > 0 else np.inf,
    }


def stats_by_year(trades: pd.DataFrame, ret_col: str = "net_ret"
                  ) -> pd.DataFrame:
    """Per-year stats table (index = year)."""
    if trades.empty:
        return pd.DataFrame()
    rows = {int(y): trade_stats(g, ret_col)
            for y, g in trades.groupby("year")}
    out = pd.DataFrame(rows).T
    out.index.name = "year"
    return out


def strategy_bar_returns(closes: pd.DataFrame, trades: pd.DataFrame,
                         hold: int, fee_bp: float = FEE_BP) -> pd.Series:
    """Hourly strategy return stream implied by the trade list.

    While a trade is open the portfolio holds its equal-weight basket; fees
    hit the entry and exit bars. Trades must come from `momentum_trades`
    run with the same `hold`. Trades are back-to-back by construction, but
    any gap is handled as 0 (flat)."""
    px = closes.dropna(axis=0, how="any")
    bar_ret = px.pct_change().fillna(0.0)
    strat = pd.Series(0.0, index=px.index)
    fee = fee_bp / 1e4
    for tr in trades.itertuples():
        syms = tr.symbols.split(",")
        i0 = px.index.get_loc(tr.entry_time)
        i1 = min(i0 + hold, len(px) - 1)
        seg = bar_ret[syms].iloc[i0 + 1: i1 + 1].mean(axis=1)
        strat.iloc[i0 + 1: i1 + 1] += seg.to_numpy()
        strat.iloc[i0 + 1] -= fee   # entry fee on first held bar
        strat.iloc[i1] -= fee       # exit fee on last held bar
    return strat


def daily_returns(bar_ret: pd.Series) -> pd.Series:
    """Compound an hourly return stream to daily (UTC)."""
    eq = (1.0 + bar_ret).cumprod()
    daily_eq = eq.resample("1D").last().dropna()
    return daily_eq.pct_change().dropna()


def equity_from_bar_returns(bar_ret: pd.Series) -> pd.Series:
    return (1.0 + bar_ret).cumprod()


# ---------------------------------------------------------------------------
# hedged_grid_v1 equity (for Q4 correlation)
# ---------------------------------------------------------------------------

def load_grid_equity(root: Path = ROOT) -> pd.Series:
    """Equal-weight combo *cumulative return* of hedged_grid_v1, hourly.

    NB: this is equity/capital − 1 (zero-based, crosses zero), NOT an
    equity level — add 1 before pct_change/resampling into returns."""
    strat_dir = root / "strategies" / "hedged_grid_v1_20260802"
    spec = importlib.util.spec_from_file_location(
        "hedged_grid_strategy", strat_dir / "strategy.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hedged_grid_strategy"] = mod
    spec.loader.exec_module(mod)

    cfg = mod.GridConfig.from_json(strat_dir / "config.json")
    curves = {}
    for sym_cfg in cfg.symbols:
        bars, funding = mod.load_symbol_data(sym_cfg.symbol, root)
        res = mod.run_symbol(bars, funding, sym_cfg, cfg.initial_capital)
        curves[sym_cfg.symbol] = res["equity"] / cfg.initial_capital - 1.0
    eq_df = pd.DataFrame(curves).ffill().dropna()
    combo = eq_df.mean(axis=1)
    idx = pd.to_datetime(combo.index, unit="ms", utc=True) \
        if not isinstance(combo.index, pd.DatetimeIndex) else combo.index
    combo.index = idx
    return combo


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_grid(closes: pd.DataFrame, lookback: int, hold: int,
             er_filter: str | None = None) -> dict:
    """One (lookback, hold) cell: overall + by-year, optionally ER-filtered.

    er_filter: None | "pick" (ER of picked basket > threshold) |
               "btc" (ER of BTC > threshold).
    """
    trades = momentum_trades(closes, lookback, hold)
    if er_filter == "pick":
        trades = trades[trades["er_pick"] > ER_THRESHOLD]
    elif er_filter == "btc":
        trades = trades[trades["er_btc"] > ER_THRESHOLD]
    by_year = stats_by_year(trades)
    return {
        "overall": trade_stats(trades),
        "by_year": by_year.reset_index().to_dict("records"),
        "n_trades": len(trades),
    }


def correlation_with_grid(closes: pd.DataFrame, lookback: int, hold: int,
                          grid_equity: pd.Series) -> dict:
    """Daily-return correlation between the momentum strategy and the grid."""
    trades = momentum_trades(closes, lookback, hold)
    mom_hourly = strategy_bar_returns(closes, trades, hold)
    mom_daily = daily_returns(mom_hourly)

    # grid_equity is a *cumulative return* series (equity/capital − 1, see
    # hedged_grid_v1 run_backtest); convert back to an equity level before
    # differencing — pct_change of a zero-crossing series is garbage.
    grid_daily_eq = (1.0 + grid_equity).resample("1D").last().dropna()
    grid_daily = grid_daily_eq.pct_change().dropna()

    both = pd.concat([mom_daily.rename("mom"),
                      grid_daily.rename("grid")], axis=1).dropna()
    corr = float(both["mom"].corr(both["grid"])) if len(both) > 10 else np.nan

    # equal-weight 50/50 combo, daily rebalanced
    combo_daily = 0.5 * both["mom"] + 0.5 * both["grid"]
    def _metrics(d: pd.Series) -> dict:
        if len(d) < 2:
            return {}
        eq = (1 + d).cumprod()
        days = (d.index[-1] - d.index[0]).days
        ann = (eq.iloc[-1] ** (365.0 / max(days, 1))) - 1.0
        dd = (eq / eq.cummax() - 1.0).min()
        sharpe = d.mean() / d.std() * np.sqrt(365.0) if d.std() > 0 else np.nan
        return {"ann_ret": float(ann), "max_dd": float(dd),
                "sharpe": float(sharpe)}
    return {
        "n_days": len(both),
        "corr": corr,
        "mom": _metrics(both["mom"]),
        "grid": _metrics(both["grid"]),
        "combo_50_50": _metrics(combo_daily),
    }


def main() -> dict:
    out: dict = {"data": {}, "q1_full_grid": {}, "q2_er_filter": {},
                 "q3_universe": {}, "q4_grid_corr": {}}

    closes7 = load_closes(SYMBOLS_7)
    closes3 = closes7[SYMBOLS_3].dropna()
    closes7 = closes7.dropna()
    out["data"] = {
        "symbols_7": SYMBOLS_7,
        "start": str(closes7.index[0]), "end": str(closes7.index[-1]),
        "n_bars": len(closes7),
    }

    # Q1: full parameter grid, 7 symbols
    for lb in LOOKBACKS:
        for h in HOLDS:
            out["q1_full_grid"][f"lb{lb}_h{h}"] = run_grid(closes7, lb, h)

    # Q2: ER filter (both flavors) on the same grid
    for lb in LOOKBACKS:
        for h in HOLDS:
            key = f"lb{lb}_h{h}"
            out["q2_er_filter"][key] = {
                "all": out["q1_full_grid"][key]["overall"],
                "er_pick": run_grid(closes7, lb, h, "pick"),
                "er_btc": run_grid(closes7, lb, h, "btc"),
            }

    # Q3: 3-symbol universe vs 7
    for lb in LOOKBACKS:
        for h in HOLDS:
            key = f"lb{lb}_h{h}"
            out["q3_universe"][key] = {
                "u3": run_grid(closes3, lb, h),
                "u7": out["q1_full_grid"][key],
            }

    # Q4: correlation with hedged_grid_v1 for a representative config
    # (longest lookback x daily hold is the canonical "slow momentum" cell;
    # also report the full-sample best-net-t cell).
    grid_eq = load_grid_equity()
    out["q4_grid_corr"]["lb168_h24"] = correlation_with_grid(
        closes7, 168, 24, grid_eq)
    best_key, best_t = None, -np.inf
    for key, cell in out["q1_full_grid"].items():
        t = cell["overall"]["t"]
        if t is not None and np.isfinite(t) and t > best_t:
            best_t, best_key = t, key
    if best_key is not None:
        lb = int(best_key.split("_")[0][2:])
        h = int(best_key.split("_")[1][1:])
        out["q4_grid_corr"][f"best_{best_key}"] = correlation_with_grid(
            closes7, lb, h, grid_eq)
    out["best_full_sample_cell"] = {"key": best_key, "t": best_t}

    (OUT_DIR / "results.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    res = main()
    print(json.dumps(res["best_full_sample_cell"], indent=2))
    print(f"results written to {OUT_DIR / 'results.json'}")
