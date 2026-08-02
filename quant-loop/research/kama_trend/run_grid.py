"""Run the full KAMA parameter grid: top5 per (symbol, tf), stability,
plateau, and MA20 / buy-hold controls. Writes CSVs + REPORT.md."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")
sys.path.insert(0, "/Users/mark/multica/quant-loop/research/kama_trend")

import itertools
import json

import numpy as np
import pandas as pd

from kama_core import (
    BARS_PER_2Y,
    ER_WINDOWS,
    FASTS,
    LOOKBACKS,
    SLOWS,
    SYMBOLS,
    TIMEFRAMES,
    kama,
    kama_signal,
    load_ohlc,
    ma_signal,
    strategy_returns,
    tstat,
)

OUT = "/Users/mark/multica/quant-loop/research/kama_trend"


def neighbors(params):
    """Grid neighbours at +-1 step in each dimension (each dim separately)."""
    er, f, s, lb = params
    grids = [ER_WINDOWS, FASTS, SLOWS, LOOKBACKS]
    out = []
    for dim, grid in enumerate(grids):
        cur = params[dim]
        i = grid.index(cur)
        for j in (i - 1, i + 1):
            if 0 <= j < len(grid):
                nb = list(params)
                nb[dim] = grid[j]
                out.append(tuple(nb))
    return sorted(set(out))


def rolling_t(r: pd.Series, window: int) -> pd.Series:
    m = r.rolling(window).mean()
    sd = r.rolling(window).std()
    return m / sd * np.sqrt(window)


def main():
    all_rows = []          # full grid results
    stab_rows = []         # yearly breakdown for top sets
    roll_rows = []         # rolling 2y t (year-end samples) for top sets
    plateau_rows = []
    ctrl_rows = []         # MA20 + buy-hold controls
    top_map = {}           # (sym, tf) -> list of top5 param tuples

    for sym, tf in itertools.product(SYMBOLS, TIMEFRAMES):
        df = load_ohlc(sym, tf)
        close = df["close"]
        # KAMA depends only on (er, fast, slow): compute 24 series once
        kamas = {}
        for er, f, s in itertools.product(ER_WINDOWS, FASTS, SLOWS):
            kamas[(er, f, s)] = kama(close, er, f, s)

        for er, f, s, lb in itertools.product(ER_WINDOWS, FASTS, SLOWS, LOOKBACKS):
            k = kamas[(er, f, s)]
            slope = k - k.shift(lb)
            sig = (slope > 0).astype(float)
            sig[slope.isna()] = 0.0
            r = strategy_returns(close, sig)
            n_trades = int((sig.shift(1).fillna(0).diff().abs() > 0).sum() / 2)
            all_rows.append(
                dict(symbol=sym, tf=tf, er=er, fast=f, slow=s, lb=lb,
                     t=tstat(r), ann_ret=float((1 + r).prod() ** (1 / max(len(r), 1)) - 1),
                     n_bars=len(r), n_rt=n_trades)
            )

        g = pd.DataFrame([x for x in all_rows if x["symbol"] == sym and x["tf"] == tf])
        top5 = g.nlargest(5, "t")
        top_map[(sym, tf)] = [tuple(x) for x in top5[["er", "fast", "slow", "lb"]].values]

        # --- controls ---
        r_bh = strategy_returns(close, pd.Series(1.0, index=close.index))
        r_ma = strategy_returns(close, ma_signal(close, 20))
        for name, r in [("buy_hold", r_bh), ("ma20", r_ma)]:
            r_recent = r[r.index >= "2024-01-01"]
            ctrl_rows.append(dict(symbol=sym, tf=tf, ctrl=name, t=tstat(r),
                                  t_2024_26=tstat(r_recent),
                                  mean_2024_26=float(r_recent.mean())))

        # --- stability + plateau for top5 ---
        for params in top_map[(sym, tf)]:
            er, f, s, lb = params
            sig = kama_signal(close, er, f, s, lb)
            r = strategy_returns(close, sig)
            # yearly
            for yr, rr in r.groupby(r.index.year):
                stab_rows.append(dict(symbol=sym, tf=tf, er=er, fast=f, slow=s, lb=lb,
                                      year=int(yr), t=tstat(rr), mean=float(rr.mean())))
            # recent 2024-26
            r_recent = r[r.index >= "2024-01-01"]
            stab_rows.append(dict(symbol=sym, tf=tf, er=er, fast=f, slow=s, lb=lb,
                                  year=20242026, t=tstat(r_recent), mean=float(r_recent.mean())))
            # rolling 2y t, sampled at each year-end
            rt = rolling_t(r, BARS_PER_2Y[tf])
            for yr in sorted(set(r.index.year)):
                yr_end = rt[rt.index.year == yr]
                if len(yr_end):
                    roll_rows.append(dict(symbol=sym, tf=tf, er=er, fast=f, slow=s, lb=lb,
                                          year=int(yr), roll2y_t=float(yr_end.iloc[-1])))
            # plateau
            t_lookup = {(x["er"], x["fast"], x["slow"], x["lb"]): x["t"]
                        for x in all_rows if x["symbol"] == sym and x["tf"] == tf}
            nbs = neighbors(params)
            nb_t = [t_lookup[nb] for nb in nbs]
            frac_pos = float(np.mean([x > 0 for x in nb_t])) if nb_t else np.nan
            plateau_rows.append(dict(symbol=sym, tf=tf, er=er, fast=f, slow=s, lb=lb,
                                     n_nb=len(nb_t), frac_pos=frac_pos,
                                     nb_t=json.dumps([round(x, 2) for x in nb_t])))

    pd.DataFrame(all_rows).to_csv(f"{OUT}/grid_results.csv", index=False)
    pd.DataFrame(stab_rows).to_csv(f"{OUT}/stability_yearly.csv", index=False)
    pd.DataFrame(roll_rows).to_csv(f"{OUT}/rolling_2y_t.csv", index=False)
    pd.DataFrame(plateau_rows).to_csv(f"{OUT}/plateau.csv", index=False)
    pd.DataFrame(ctrl_rows).to_csv(f"{OUT}/controls.csv", index=False)
    with open(f"{OUT}/top_map.json", "w") as fh:
        json.dump({f"{k[0]}_{k[1]}": [[int(x) for x in p] for p in v]
                   for k, v in top_map.items()}, fh, indent=1)
    print("done",
          len(all_rows), "grid rows,",
          len(stab_rows), "stability rows,",
          len(plateau_rows), "plateau rows")


if __name__ == "__main__":
    main()
