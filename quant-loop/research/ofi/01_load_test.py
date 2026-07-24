"""Quick load test: read 1 month of BTC aggTrades, bucket into 1m, sanity check.
Sanity check our pipeline before doing the full 3-month run.
"""
import sys
import time
import pyarrow.parquet as pq
import pandas as pd

ROOT = '/home/smark/multica/quant-loop/data/trades/BTCUSDT_aggtrades.parquet/year=2026/month=4'

t0 = time.time()
pf = pq.ParquetFile(f'{ROOT}/data.parquet')
print(f'Loaded file in {time.time()-t0:.1f}s, num_rows={pf.metadata.num_rows}')

t0 = time.time()
df = pf.read(columns=['ts', 'price', 'qty', 'is_buyer_maker']).to_pandas()
print(f'Read cols + to_pandas in {time.time()-t0:.1f}s, shape={df.shape}, mem MB={df.memory_usage(deep=True).sum()/1e6:.1f}')

t0 = time.time()
# Ensure UTC
df['ts'] = pd.to_datetime(df['ts'], utc=True)
df = df.set_index('ts').sort_index()
print(f'Set index + sorted in {time.time()-t0:.1f}s')

# Sanity: print head/tail
print('range:', df.index.min(), '→', df.index.max())
print('head:')
print(df.head(3))
print('tail:')
print(df.tail(3))

# Compute 1m buckets: signed buy vol - sell vol
t0 = time.time()
df['signed_qty'] = df.apply(
    lambda r: r['qty'] if not r['is_buyer_maker'] else -r['qty'],
    axis=1
)
print(f'Signed qty compute in {time.time()-t0:.1f}s')

t0 = time.time()
b = df.resample('1min').agg(
    buy_vol=('qty', lambda x: x[(df.loc[x.index, 'is_buyer_maker'] == False)].sum()),
)
print('resample head:')
# Actually let's do it differently with agg over both buy/sell volumes
buy_vol = df.loc[df['is_buyer_maker'] == False, 'qty'].resample('1min').sum()
sell_vol = df.loc[df['is_buyer_maker'] == True, 'qty'].resample('1min').sum()
print(f'Buy/sell sum in {time.time()-t0:.1f}s')

t0 = time.time()
mid = df['price'].resample('1min').mean()
print(f'Mid (price mean) in {time.time()-t0:.1f}s')

bars = pd.DataFrame({
    'buy_vol': buy_vol,
    'sell_vol': sell_vol,
    'mid': mid
}).fillna(0)
bars['ofi'] = bars['buy_vol'] - bars['sell_vol']
bars['mid_ret'] = bars['mid'].pct_change()

print(f'\n1m bars: {len(bars)} from {bars.index.min()} → {bars.index.max()}')
print(bars.describe())
print('\nFirst non-zero ofi:')
print(bars[bars['ofi'] != 0].head(3))
print('\nMid return stats:')
print(bars['mid_ret'].describe())
print(f'Non-zero mid_ret: {(bars["mid_ret"] != 0).sum()}/{len(bars)}')
