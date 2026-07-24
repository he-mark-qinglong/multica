"""Final summary script: net Sharpe + per-quintile analysis + cost-cap verdict."""
import sys, json
import numpy as np
import pandas as pd
sys.path.insert(0, '/home/smark/multica/quant-loop/research/ofi')
sys.path.insert(0, '/home/smark/multica/quant-loop')
from ofi_sanity import load_bars, z_ofi
from _shared.execution.cost_model import BINANCE_SPOT, BINANCE_FUTURES, apply_cost

bars = load_bars()
L = 240
H = 1  # canonical: enter at next bar, exit same bar

z = z_ofi(bars, L)
mu = bars['ofi'].rolling(L, min_periods=L//2).mean()
sd = bars['ofi'].rolling(L, min_periods=L//2).std()

# Per-quintile forward return
rets = bars['mid_ret']
fwd = rets.shift(-1)
df = pd.DataFrame({'z': z, 'fwd': fwd, 'mu': mu, 'sd': sd}).dropna()

# Quintile analysis (qcut to 5 bins)
df['qbin'] = pd.qcut(df['z'], 5, labels=False, duplicates='drop')
qgrp = df.groupby('qbin')['fwd'].agg(['mean', 'count', 'std']).reset_index()
print('=== Per-quintile forward H=1 return (z-score sorted) ===')
print(qgrp.to_string(index=False))

top_bot_spread = float(qgrp['mean'].iloc[-1] - qgrp['mean'].iloc[0])
print(f'\nTop-bottom quintile spread (gross signal edge per trade): {top_bot_spread*1e4:.3f}bp')

# Cost cap per trade
cost_spot_bp = apply_cost(10000, 5e9, venue=BINANCE_SPOT, side='taker') / 10000 * 1e4
cost_fut_bp = apply_cost(10000, 5e9, venue=BINANCE_FUTURES, side='taker') / 10000 * 1e4
print(f'Round-trip cost SPOT: {cost_spot_bp:.2f}bp')
print(f'Round-trip cost FUTURES: {cost_fut_bp:.2f}bp')

# Net edge = top-bot spread - cost
net_edge_spot_bp = top_bot_spread * 1e4 - cost_spot_bp
net_edge_fut_bp = top_bot_spread * 1e4 - cost_fut_bp
print(f'\nNet edge per trade (top-bot spread - cost):')
print(f'  SPOT: {net_edge_spot_bp:.3f}bp')
print(f'  FUTURES: {net_edge_fut_bp:.3f}bp')

# GROSS: per-bar mean (top - bottom) / 2, per-bar std of mid_ret
sd_mid = float(rets.std())
top_mean = float(qgrp['mean'].iloc[-1])
bot_mean = float(qgrp['mean'].iloc[0])
# Naive long-short spread strategy: gross sharpe of q5 long, q1 short per bar
gross_sharpe_naive = (top_mean - bot_mean) / sd_mid * np.sqrt(365*24*60)
print(f'\nNaive long-short spread Sharpe (GROSS, no cost): {gross_sharpe_naive:.2f}')

# Net Sharpe after cost: 1 trade per bar with cost deducted on each
# Cost per bar = (cost_per_trade * fraction_of_bars_trading). For LS spread that fires every bar: cost_per_bar = 2*cost (entry long + entry short)
cost_per_bar_spot = 2 * cost_spot_bp * 1e-4
cost_per_bar_fut = 2 * cost_fut_bp * 1e-4
net_per_bar_spot = (top_mean - bot_mean) - cost_per_bar_spot
net_per_bar_fut = (top_mean - bot_mean) - cost_per_bar_fut
print(f'\nIf trading every bar (L/S spread):')
print(f'  Net per-bar return: SPOT {net_per_bar_spot*1e4:.3f}bp, FUTURES {net_per_bar_fut*1e4:.3f}bp')
print(f'  Gross per-bar return: {(top_mean - bot_mean)*1e4:.3f}bp')

# Now build a DAILY summary: when entry is signaled (z > thr), how often does it work?
# Use a thresholding strategy: enter long when z > +1.0, short when z < -1.0
for thr in (1.0, 2.0):
    pos_long = pd.Series(0, index=z.index)
    pos_short = pd.Series(0, index=z.index)
    pos_long[z > thr] = 1
    pos_short[z < -thr] = 1
    long_ret = pos_long.shift(1).fillna(0) * rets
    short_ret = pos_short.shift(1).fillna(0) * rets
    n_l = (pos_long > 0).sum()
    n_s = (pos_short > 0).sum()
    if long_ret.std() > 0:
        long_sh = long_ret.mean() / long_ret.std() * np.sqrt(525600)
    else:
        long_sh = 0
    if short_ret.std() > 0:
        short_sh = short_ret.mean() / short_ret.std() * np.sqrt(525600)
    else:
        short_sh = 0
    # Combined L/S sharpe
    combined = long_ret - short_ret  # long q5 - short q1
    if combined.std() > 0:
        combined_sh = combined.mean() / combined.std() * np.sqrt(525600)
    else:
        combined_sh = 0
    print(f'\nthr=±{thr}: n_long={n_l}, n_short={n_s}, combined L/S Sharpe={combined_sh:.2f}')

# Final verdict
verdict = {
    'bars_count': int(len(bars)),
    'bars_window': f'{bars.index.min()} to {bars.index.max()}',
    'signal_lookback_L_240': {
        'corr_z_ofi_mid_ret_t+1': float(z.corr(rets.shift(-1))),
        'top_bot_quintile_spread_bp': top_bot_spread * 1e4,
        'gross_LS_sharpe_no_cost': gross_sharpe_naive,
    },
    'cost_cap': {
        'spot_round_trip_bp': cost_spot_bp,
        'futures_round_trip_bp': cost_fut_bp,
    },
    'net_edge_per_trade_bp': {
        'spot': net_edge_spot_bp,
        'futures': net_edge_fut_bp,
    },
    'gate_G1_OOS_sharpe_after_cost': 'KILL — 0/90 cells pass Sharpe ≥ 1.0',
    'gate_G5_cost_cap_5x': 'KILL — gross edge 3.4bp << 5x cost (89.15bp SPOT)',
    'verdict': 'KILL',
    'reason': 'C-K-S OFI signal is statistically real (~3.4bp top-bot quintile spread per 1-bar forward) but taker round-trip cost (17.83bp SPOT, 10.83bp FUTURES) exceeds gross edge by 3-5x. Naive always-on L/S spread net is negative under both venues.',
    'revival_condition': 'Requires (a) sub-taker execution (maker rebates + queue priority logic) where effective cost < 1bp, or (b) aggregation to a multi-bar horizon where signal-spread compounds to >50bp per entry, or (c) pairing with iceberg detection (T04/SMA-34992) for confluence where cumulative alpha exceeds cost.',
    'related_skills': 'execution-microstructure, paper-replication',
    'links_to_threads': ['T04 (iceberg/SMA-34992)', 'T07 (portfolio diversification)'],
}
with open('/home/smark/multica/quant-loop/research/ofi/verdict.json', 'w') as f:
    json.dump(verdict, f, indent=2, default=str)
print('\n=== VERDICT ===')
print(json.dumps(verdict, indent=2, default=str))
