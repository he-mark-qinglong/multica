"""Sanity check: what is the sign of OFI→return predictive relationship?
If positive, our long-when-z>thr should win; if negative, short-when-z>thr wins.
Either way, the current implementation shows massive negative Sharpe.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
try:
    from _shared.paths import data_root
except ImportError:
    import sys
    from pathlib import Path
    _QL = str(Path(__file__).resolve().parents[2])
    if _QL not in sys.path:
        sys.path.insert(0, _QL)
    from _shared.paths import data_root
from ofi_sanity import load_bars  # type: ignore

bars = load_bars()
print(f'bars: {len(bars):,}')
print('correlation ofi vs future mid_ret (1-step lead):')
print('  corr(ofi_t, mid_ret_{t+1}):', bars['ofi'].corr(bars['mid_ret'].shift(-1)))
print('  corr(ofi_t, mid_ret_t):', bars['ofi'].corr(bars['mid_ret']))

# Standardized OFI z-score vs next mid_ret
for L in (60, 240, 1440):
    mu = bars['ofi'].rolling(L, min_periods=L//2).mean()
    sd = bars['ofi'].rolling(L, min_periods=L//2).std()
    z = (bars['ofi'] - mu) / sd
    z = z.dropna()
    fwd = bars['mid_ret'].shift(-1)
    df = pd.DataFrame({'z': z, 'fwd': fwd}).dropna()
    c = df['z'].corr(df['fwd'])
    print(f'corr(z_ofi[L={L}], mid_ret[t+1]): {c:.5f}')
    # Quantile bins
    df['qbin'] = pd.qcut(df['z'], 10, labels=False, duplicates='drop')
    grp = df.groupby('qbin')['fwd'].agg(['mean', 'count', 'std'])
    print(f'  per-decile mean fwd return:\n{grp.to_string()}\n')

# Also: signed correlation without z-score
print('Direct: corr(ofi_t, mid_ret_{t+1})')
print(f'  = {bars["ofi"].corr(bars["mid_ret"].shift(-1)):.5f}')

# Try simple lag-1 OFI change (not z-score)
diff_ofi = bars['ofi'].diff()
fwd = bars['mid_ret']
print(f'\nDiff-ofi predictive:')
for h in (1, 5, 30):
    print(f'  corr(diff_ofi_t, mid_ret_{{t+h}}): {diff_ofi.corr(bars["mid_ret"].shift(-h)):.5f}')
