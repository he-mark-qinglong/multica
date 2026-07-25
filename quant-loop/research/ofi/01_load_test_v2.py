"""Fast OFI 1m bucket build from BTCUSDT aggTrades.
Strategy: use pyarrow compute to bucket by minute, no Python apply loops.
"""
import sys
import time
import glob
import os
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

try:
    from _shared.paths import data_root
except ImportError:
    import sys
    from pathlib import Path
    _QL = str(Path(__file__).resolve().parents[2])
    if _QL not in sys.path:
        sys.path.insert(0, _QL)
    from _shared.paths import data_root

ROOT = str(data_root() / 'trades' / 'BTCUSDT_aggtrades.parquet')
MONTHS = ['2026-04', '2026-05', '2026-06']  # canonical: full 3 calendar months

def open_table(year_month: str) -> pa.Table:
    y, m = year_month.split('-')
    p = f'{ROOT}/year={y}/month={int(m)}/data.parquet'
    return pq.read_table(p, columns=['ts', 'price', 'qty', 'is_buyer_maker'])


def bucket_one_month(t: pa.Table) -> pd.DataFrame:
    """Bucket a month of aggTrades into 1m bars: buy_vol, sell_vol, mid (vwap)."""
    qty = t['qty']
    ibm = t['is_buyer_maker']
    # signed_qty = +qty if buyer is taker (is_buyer_maker=False), else -qty
    is_taker_buy = pc.invert(ibm)
    zero = pa.array(np.zeros(len(qty), dtype=np.float64))
    buy_qty = pc.if_else(is_taker_buy, qty, zero)
    sell_qty = pc.if_else(is_taker_buy, zero, qty)
    t2 = t.append_column('buy_qty', buy_qty).append_column('sell_qty', sell_qty)

    # Group by 1-min bucket. Use ts to derive bucket, then groupby.
    # Easiest path: convert to pandas, use resample.
    df = t2.to_pandas()
    df['ts'] = pd.to_datetime(df['ts'], utc=True)
    df = df.set_index('ts').sort_index()
    out = pd.DataFrame()
    out['buy_vol'] = df['buy_qty'].resample('1min').sum()
    out['sell_vol'] = df['sell_qty'].resample('1min').sum()
    # vwap proxy = mean of trade prices within bar (BTC is liquid; fine)
    out['vwap'] = df['price'].resample('1min').mean()
    out['n_trades'] = df['buy_qty'].resample('1min').count()
    return out


def main():
    parts = []
    for ym in MONTHS:
        t0 = time.time()
        t = open_table(ym)
        print(f'[{ym}] read rows={t.num_rows:,} in {time.time()-t0:.1f}s')
        t1 = time.time()
        b = bucket_one_month(t)
        print(f'[{ym}] bucketed into 1m bars={len(b):,} in {time.time()-t1:.1f}s')
        parts.append(b)
    bars = pd.concat(parts).sort_index()
    print(f'\nTOTAL bars: {len(bars):,}')
    print(f'range: {bars.index.min()} → {bars.index.max()}')
    print(bars.describe())
    bars.to_parquet(Path(__file__).resolve().parent / 'btc_1m_3mo.parquet')
    print('saved → btc_1m_3mo.parquet')


if __name__ == '__main__':
    main()
