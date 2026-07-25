"""Inspect trade details from full BTC range."""
import sys, json
from pathlib import Path
try:
    from _shared.paths import quant_loop_root
except ImportError:  # bare-script mode
    _QL = str(Path(__file__).resolve().parents[2])
    if _QL not in sys.path:
        sys.path.insert(0, _QL)
    from _shared.paths import quant_loop_root
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / '_indicators'))
sys.path.insert(0, str(quant_loop_root()))

import data_loader
from strategy import run_backtest
import numpy as np
from collections import Counter

cfg = json.loads((_HERE / 'config.json').read_text())
data = data_loader.load_all(['BTCUSDT'])
res = run_backtest(data, cfg)
ps = res['per_symbol'][0]
print(f"n_trades: {len(ps['trades'])}")
trades = ps['trades']
pnls = np.array([t['pnl_pct'] for t in trades])
print(f"pnls: min={pnls.min():.5f} max={pnls.max():.5f} mean={pnls.mean():.5f} median={np.median(pnls):.5f}")
print(f"win rate: {(pnls > 0).mean()*100:.1f}%")
print(f"avg win: {pnls[pnls > 0].mean() if (pnls > 0).any() else 0:.5f}")
print(f"avg loss: {pnls[pnls < 0].mean() if (pnls < 0).any() else 0:.5f}")
exit_reasons = Counter(t['exit_reason'] for t in trades)
print('exit reasons:', dict(exit_reasons))
directions = Counter(t['direction'] for t in trades)
print('directions:', dict(directions))
print('PnL by exit reason:')
for reason, count in exit_reasons.items():
    sub = pnls[np.array([t['exit_reason']==reason for t in trades])]
    print(f"  {reason}: count={count}, mean={sub.mean():.5f}, sum={sub.sum():.4f}")
# time-of-year distribution
print()
print('Trades by year:')
from collections import defaultdict
year_pnl = defaultdict(list)
for t in trades:
    yr = pd.Timestamp(t['entry_ts']).year if False else t['entry_ts'][:4]
    year_pnl[yr].append(t['pnl_pct'])
import pandas as pd
for yr in sorted(year_pnl.keys()):
    arr = np.array(year_pnl[yr])
    print(f"  {yr}: n={len(arr)}, sum={arr.sum():.4f}, mean={arr.mean():.5f}")