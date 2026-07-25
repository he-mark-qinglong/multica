# H1/H3 Baseline — FIXED Fee-Shock Replay (SMA-36566)

Per-trade cost basis corrected from buggy 0.005 to fixed **1.0** (= full pair pct, matches trade log `pnl_pct` and engine `cost = 2*2*(fee+slip)/10000`).

## Per-trade stats (sizing-independent break-even evidence)

| Hyp | n_trades | mean gross | std gross | win_rate | mean net @8bps |
|-----|---------:|-----------:|----------:|---------:|---------------:|
| H1   | 14221 | 1.18 bps | 45.94 bps | 28.0% | -6.82 bps |
| H2   | 5301 | 0.38 bps | 56.61 bps | 38.3% | -7.62 bps |
| H3   | 46238 | 0.52 bps | 49.20 bps | 32.7% | -7.48 bps |
| H4   | 550 | 9.42 bps | 52.30 bps | 51.1% | 1.42 bps |

## Fee-shock Sharpe (FIXED per_trade_fraction=1.0)

| Hyp | Gross Sharpe | 4 bps RT | 24 bps RT | 60 bps RT | Verdict |
|-----|-------------:|---------:|----------:|----------:|---------|
| H1   | +1.517 | -7.522 | -19.541 | -21.610 | DEAD @24bps |
| H2   | +0.215 | -4.319 | -13.115 | -15.188 | DEAD @24bps |
| H3   | +2.300 | -15.557 | -43.041 | -47.953 | DEAD @24bps |
| H4   | +1.967 | -7.601 | -7.732 | -7.744 | DEAD @24bps |