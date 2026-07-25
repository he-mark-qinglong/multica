"""Direct sanity check: replicate C-K-S OFI signal at minimal parameters, no cost.
Compare to expected sign (correlation +0.2 with next-bar return).
"""
import sys, numpy as np, pandas as pd
from pathlib import Path
try:
    from _shared.paths import data_root
except ImportError:
    import sys
    from pathlib import Path
    _QL = str(Path(__file__).resolve().parents[2])
    if _QL not in sys.path:
        sys.path.insert(0, _QL)
    from _shared.paths import data_root
from ofi_sanity import load_bars, z_ofi

bars = load_bars()
print(f'bars: {len(bars):,}')

# What if we ALWAYS-LONG?
pos = pd.Series(1, index=bars.index, dtype=np.int8).shift(1).fillna(0).astype(np.int8)
gross = pos * bars['mid_ret']
print(f'ALWAYS LONG gross Sharpe: {gross.mean()/gross.std()*np.sqrt(525600):.3f}')

# What if we always-short?
pos = pd.Series(-1, index=bars.index, dtype=np.int8).shift(1).fillna(0).astype(np.int8)
gross = pos * bars['mid_ret']
print(f'ALWAYS SHORT gross Sharpe: {gross.mean()/gross.std()*np.sqrt(525600):.3f}')

# OFI sign strategy: z > 0 → long, z < 0 → short
for L in (60, 240, 1440):
    z = z_ofi(bars, L)

    # Strategy A: long if z>0, short if z<0
    pos_a = pd.Series(0, index=z.index, dtype=np.int8)
    pos_a[z > 0] = 1
    pos_a[z < 0] = -1
    pos_a = pos_a.shift(1).fillna(0).astype(np.int8)
    gross_a = pos_a * bars['mid_ret']
    sh_a = gross_a.dropna().mean() / gross_a.dropna().std() * np.sqrt(525600)
    print(f'L={L}: A) z>0→L, z<0→S   Sharpe={sh_a:.3f}  mean_ret={gross_a.mean():.2e}  std={gross_a.std():.2e}')

    # Strategy B: long if z>0, else flat
    pos_b = pd.Series(0, index=z.index, dtype=np.int8)
    pos_b[z > 0] = 1
    pos_b = pos_b.shift(1).fillna(0).astype(np.int8)
    gross_b = pos_b * bars['mid_ret']
    sh_b = gross_b.dropna().mean() / gross_b.dropna().std() * np.sqrt(525600)
    print(f'L={L}: B) z>0→L, else=0 Sharpe={sh_b:.3f}')

    # Sanity at thr levels:
    for thr in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
        pos_c = pd.Series(0, index=z.index, dtype=np.int8)
        pos_c[z > thr] = 1
        pos_c[z < -thr] = -1
        pos_c = pos_c.shift(1).fillna(0).astype(np.int8)
        gross_c = pos_c * bars['mid_ret']
        r = gross_c.dropna()
        if r.std() > 0:
            sh_c = r.mean() / r.std() * np.sqrt(525600)
        else:
            sh_c = 0
        # Also compute mean|return| per entry
        n_entries = (pos_c.diff().abs() > 0).sum()
        print(f'L={L}: C) thr=±{thr}  Sharpe={sh_c:.3f}  n_entries={n_entries}')
