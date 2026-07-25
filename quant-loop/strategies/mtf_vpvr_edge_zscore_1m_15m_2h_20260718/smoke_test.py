"""Smoke test for strategy — 30 days."""
import sys, time, json
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
from strategy import build_signals, run_backtest
import numpy as np

cfg = json.loads((_HERE / 'config.json').read_text())
data = data_loader.load_all(['BTCUSDT', 'ETHUSDT'])
# Use last 30 days for a meaningful smoke test (43,200 bars)
data_small = {sym: df.iloc[-43200:] for sym, df in data.items()}
print('Smoke test data spans:')
for s, df in data_small.items():
    print(f'  {s}: {len(df)} bars, {df.index[0]} -> {df.index[-1]}')

t0 = time.time()
res = run_backtest(data_small, cfg)
print(f'Result n_bars={res["portfolio"]["n_bars"]}, n_symbols={len(res["per_symbol"])}')
for ps in res['per_symbol']:
    print(f'  {ps["symbol"]}: n_trades={len(ps["trades"])}')
print(f'Time: {time.time()-t0:.2f}s')
print('OK')