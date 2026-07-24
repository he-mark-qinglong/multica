"""Diagnose: how often do signal conditions trigger?"""
import sys, json
from pathlib import Path
_HERE = Path('/home/smark/multica/quant-loop.worktrees/mtf-1m-15m-2h/quant-loop/strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718')
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / '_indicators'))
sys.path.insert(0, '/home/smark/multica/quant-loop')

import numpy as np
import pandas as pd
import data_loader
from strategy import build_signals, _compute_hvn_lvn_series, ema, rolling_vpvr_levels, aggregate_ohlcv

cfg = json.loads((_HERE / 'config.json').read_text())
ind = cfg['indicators']
data = data_loader.load_all(['BTCUSDT'])
df = data['BTCUSDT'].iloc[-43200:]
print(f'BTC slice: {len(df)} bars, {df.index[0]} -> {df.index[-1]}')

df_15m = aggregate_ohlcv(df, '15min')
df_2h = aggregate_ohlcv(df, '2h')
print(f'15m bars: {len(df_15m)}, 2h bars: {len(df_2h)}')

# Z-score distributions
from strategy import zscore, rolling_slope
z15 = zscore(df_15m['close'], int(ind['zscore_lookback_bars_15m'])).dropna()
z2h = zscore(df_2h['close'], int(ind['zscore_lookback_bars_2h'])).dropna()
print(f'z15m (100-bar): mean={z15.mean():.3f} std={z15.std():.3f} min={z15.min():.2f} max={z15.max():.2f}')
print(f'  |z|>2.0 count: {(z15.abs()>2.0).sum()} / {len(z15)} = {(z15.abs()>2.0).mean()*100:.1f}%')
print(f'  |z|>1.5 count: {(z15.abs()>1.5).sum()} / {len(z15)} = {(z15.abs()>1.5).mean()*100:.1f}%')
print(f'  |z|>1.0 count: {(z15.abs()>1.0).sum()} / {len(z15)} = {(z15.abs()>1.0).mean()*100:.1f}%')
print(f'z2h (60-bar): mean={z2h.mean():.3f} std={z2h.std():.3f} min={z2h.min():.2f} max={z2h.max():.2f}')

# POC slope
prof_15m = rolling_vpvr_levels(df_15m['close'], df_15m['volume'], int(ind['vpvr_window_bars_15m']), int(ind['vpvr_n_bins']))
poc_slope = rolling_slope(prof_15m['poc'], int(ind['poc_slope_lookback'])).dropna()
print(f'POC slope 15m: mean={poc_slope.mean():.4f} std={poc_slope.std():.4f}')
print(f'  POC slope>0 count: {(poc_slope>0).sum()} / {len(poc_slope)} = {(poc_slope>0).mean()*100:.1f}%')

# EMA slope 2h
ema2h = ema(df_2h['close'], int(ind['ema_period_2h']))
ema_slope_2h = rolling_slope(ema2h, int(ind['ema_slope_lookback'])).dropna()
print(f'EMA(20) slope 2h: mean={ema_slope_2h.mean():.4f} std={ema_slope_2h.std():.4f}')

# ATR 15m
from strategy import wilder_atr
atr_15m = wilder_atr(df_15m, int(ind['atr_period'])).dropna()
print(f'ATR 15m: mean={atr_15m.mean():.2f} (BTC price ~$108K so ATR ~0.05% of price)')

# Test the joint condition
df_1m = df
idx_15m = df_15m.index
idx_2h = df_2h.index

# Count 1m bars where (1) z15<=-2 OR z15>=+2, (2) |z2h|>=1 same side
z15_1m = z15.reindex(df_1m.index, method='ffill')
z2h_1m = z2h.reindex(df_1m.index, method='ffill')
poc_slope_1m = poc_slope.reindex(df_1m.index, method='ffill')
ema_slope_1m = ema_slope_2h.reindex(df_1m.index, method='ffill')

cond_long = (z15_1m <= -2.0) & (z2h_1m <= -1.0) & (poc_slope_1m > 0)
cond_short = (z15_1m >= +2.0) & (z2h_1m >= +1.0) & (poc_slope_1m < 0)
cond_long_simple = (z15_1m <= -2.0).fillna(False)
cond_short_simple = (z15_1m >= +2.0).fillna(False)
print(f'1m bars where z15m<=-2 (raw, no other filter): {cond_long_simple.sum():,}')
print(f'1m bars where long condition (z15<=-2, z2h<=-1, poc_slope>0): {cond_long.fillna(False).sum():,}')
print(f'1m bars where short condition (z15>=+2, z2h>=+1, poc_slope<0): {cond_short.fillna(False).sum():,}')