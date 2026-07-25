"""Inspect trade details from last 30 days."""
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

cfg = json.loads((_HERE / 'config.json').read_text())
data = data_loader.load_all(['BTCUSDT'])
df = data['BTCUSDT'].iloc[-43200:]  # 30 days
res = run_backtest({'BTCUSDT': df}, cfg)
ps = res['per_symbol'][0]
print(f"n_trades: {len(ps['trades'])}")
trades = ps['trades']
# Aggregate stats
pnls = np.array([t['pnl_pct'] for t in trades])
print(f"pnls: min={pnls.min():.5f} max={pnls.max():.5f} mean={pnls.mean():.5f} median={np.median(pnls):.5f}")
print(f"win rate: {(pnls > 0).mean()*100:.1f}%")
print(f"avg win: {pnls[pnls > 0].mean() if (pnls > 0).any() else 0:.5f}")
print(f"avg loss: {pnls[pnls < 0].mean() if (pnls < 0).any() else 0:.5f}")
# Exit reasons
from collections import Counter
exit_reasons = Counter(t['exit_reason'] for t in trades)
print('exit reasons:', dict(exit_reasons))
print()
print('First 15 trades:')
for t in trades[:15]:
    print(f"  {t['direction']:5} entry={t['entry_price']:.2f} exit={t['exit_price']:.2f} "
          f"pnl={t['pnl_pct']*100:.3f}% held={t['bars_held']}m exit={t['exit_reason']} "
          f"z15={t['z15m_at_entry']:.2f} z2h={t['z2h_at_entry']:.2f}")