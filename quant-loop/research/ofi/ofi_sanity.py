"""Shared OFI helpers."""
from pathlib import Path
import pandas as pd

OUT = Path(__file__).resolve().parent
BARS_PATH = OUT / 'btc_1m_3mo.parquet'


def load_bars() -> pd.DataFrame:
    bars = pd.read_parquet(BARS_PATH)
    bars['ofi'] = bars['buy_vol'] - bars['sell_vol']
    bars['mid_ret'] = bars['vwap'].pct_change()
    bars = bars.dropna()
    bars = bars[bars['n_trades'] >= 10].copy()
    return bars


def z_ofi(bars: pd.DataFrame, lookback: int) -> pd.Series:
    ofi = bars['ofi']
    mu = ofi.rolling(lookback, min_periods=lookback // 2).mean()
    sd = ofi.rolling(lookback, min_periods=lookback // 2).std()
    z = (ofi - mu) / sd.replace(0, float('nan'))
    return z
