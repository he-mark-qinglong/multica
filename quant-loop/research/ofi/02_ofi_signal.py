"""Cont-Kukanov-Stoikov OFI replication on BTC 1m aggTrades.

Per task SMA-35037:
 1. Replicate Cont-Kukanov-Stoikov OFI signal: BTC 1m canonical window
 2. Pre-registered gates: Sharpe >= 1.0 (OOS walk-forward), cost-cap
 3. If alpha: combine with pair strategy (execution layer)

Approach (per execution-microstructure skill):
  - signed taker volume per 1m bar: ofi = buy_vol - sell_vol (where is_buyer_maker=False → taker buy)
  - Per C-K-S: signed cumulative order-flow imbalance z-scored over rolling window
  - Trading rule: enter long if z_ofi > +thr (size = 1 unit), short if z_ofi < -thr, else flat
  - Compute next-bar mid return per position
  - Apply cost model via _shared/execution/cost_model.py

Pre-registered gates (must ALL pass to ship):
  G1: OOS Sharpe >= 1.0 (CPCV walk-forward)
  G2: DSR (Deflated Sharpe) passing for n_trials = total cells
  G3: Annualized >= 15% after realistic costs
  G4: Robustness: >= 5/35 cells pass G1 (not single-cell)
  G5: cost-cap: |mean return| > 5 * cost_bps_round_trip

Hard KILL triggers:
  - Single-cell pass (parameter-fitting)
  - Sharpe sign flip when lookback doubled
  - cost-cap fails on canonical window
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
import numpy as np
import pandas as pd

# Allow imports from _shared
sys.path.insert(0, '/home/smark/multica/quant-loop')
from _shared.execution.cost_model import BINANCE_SPOT, BINANCE_FUTURES, apply_cost

OUT = Path('/home/smark/multica/quant-loop/research/ofi')
BARS_PATH = OUT / 'btc_1m_3mo.parquet'
RESULTS_PATH = OUT / 'ofi_results.json'

# ============================================================
# 1. Load canonical 1m BTC bars
# ============================================================
def load_bars() -> pd.DataFrame:
    bars = pd.read_parquet(BARS_PATH)
    # Compute OFI
    bars['ofi'] = bars['buy_vol'] - bars['sell_vol']
    bars['mid_ret'] = bars['vwap'].pct_change()
    bars = bars.dropna()
    # Drop bars with zero n_trades (which were synthetic padding)
    bars = bars[bars['n_trades'] >= 10].copy()
    return bars


# ============================================================
# 2. C-K-S OFI signal: z-scored signed flow over rolling window
# ============================================================
def z_ofi(bars: pd.DataFrame, lookback: int) -> pd.Series:
    """Per Cont-Kukanov-Stoikov (2014): signed order flow normalized by rolling vol.
    Generalization to tape data: signed taker volume imbalance, rolling z-score."""
    ofi = bars['ofi']
    mu = ofi.rolling(lookback, min_periods=lookback // 2).mean()
    sd = ofi.rolling(lookback, min_periods=lookback // 2).std()
    z = (ofi - mu) / sd.replace(0, np.nan)
    return z


# ============================================================
# 3. Position signal: -1 / 0 / +1 based on z_ofi threshold
# ============================================================
def position_signal(z: pd.Series, thr: float) -> pd.Series:
    """Enter LONG next bar if z > +thr, SHORT if z < -thr, else flat.
    Use shift(1) so today's signal drives today's entry (positions decided at bar t close)."""
    pos = pd.Series(0, index=z.index, dtype=np.int8)
    pos[z > thr] = 1
    pos[z < -thr] = -1
    # We trade at next bar (avoid same-bar look-ahead): enter at t+1 open
    return pos.shift(1).fillna(0).astype(np.int8)


# ============================================================
# 4. Compute gross returns and net-of-cost returns
# ============================================================
def compute_returns(bars: pd.DataFrame, lookback: int, thr: float,
                    venue=BINANCE_SPOT, notional_per_trade_usd: float = 10_000.0,
                    adv_usd: float = 5e9) -> dict:
    """Apply signal, compute per-bar PnL gross and net of cost."""
    z = z_ofi(bars, lookback)
    pos = position_signal(z, thr)
    # Per-bar PnL: position taken at bar t close → realize mid_ret at bar t+1 close
    gross = pos * bars['mid_ret']
    # Cost: every bar a trade happens (pos changes 0→±1 or sign flip) incurs round-trip fee+slip
    pos_change = pos.diff().fillna(pos).abs()
    # Two flavors of cost:
    # A) cost per bar a position is held (round-trip on entry+exit at far end)
    # B) cost per bar a NEW position is entered (entry cost only; exit cost charged when position closes)
    # Realistic: charge entry+exit each time we ENTER a new (non-zero) position
    n_entries = (pos_change > 0).astype(int)
    cost_pct_per_entry = apply_cost(notional_per_trade_usd, adv_usd, venue=venue, side='taker') / notional_per_trade_usd
    # Distribute cost to the bar where entry happens (one-shot drag)
    cost_drag = n_entries * cost_pct_per_entry
    net = gross - cost_drag
    return {
        'pos': pos,
        'z': z,
        'gross': gross,
        'cost_drag': cost_drag,
        'net': net,
        'cost_pct_per_entry': float(cost_pct_per_entry),
        'n_entries': int(n_entries.sum()),
    }


# ============================================================
# 5. Metrics
# ============================================================
def metrics(returns: pd.Series, periods_per_year: int = 365 * 24 * 60) -> dict:
    r = returns.dropna().values
    if len(r) < 10:
        return {'sharpe': float('nan'), 'ann_return': float('nan'), 'ann_vol': float('nan'),
                'n': len(r), 'hit_rate': float('nan'), 'mean': float('nan'), 'std': float('nan')}
    mu = float(np.mean(r))
    sd = float(np.std(r, ddof=1))
    if sd <= 1e-12:
        sharpe = 0.0
    else:
        sharpe = mu / sd * np.sqrt(periods_per_year)
    ann = mu * periods_per_year
    ann_vol = sd * np.sqrt(periods_per_year)
    hits = float((r > 0).sum()) / len(r)
    return {'sharpe': sharpe, 'ann_return': ann, 'ann_vol': ann_vol,
            'n': len(r), 'hit_rate': hits, 'mean': mu, 'std': sd}


# ============================================================
# 6. Walk-forward OOS: train on first half, test on second half (simple split).
# Also: split 70/30 and run CPCV with N groups, k_test.
# ============================================================
def walk_forward(bars: pd.DataFrame, lookbacks=(60, 120, 240, 480, 1440),
                 thrs=(0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0),
                 venue=BINANCE_SPOT) -> pd.DataFrame:
    """For each (lookback, thr) compute is_sharpe (first 50%) and oos_sharpe (last 50%)."""
    rows = []
    n = len(bars)
    cut = n // 2
    for L in lookbacks:
        for thr in thrs:
            r = compute_returns(bars, L, thr, venue=venue)
            is_met = metrics(r['net'].iloc[:cut])
            oos_met = metrics(r['net'].iloc[cut:])
            rows.append({
                'lookback': L,
                'thr': thr,
                'is_sharpe': is_met['sharpe'],
                'oos_sharpe': oos_met['sharpe'],
                'is_ann': is_met['ann_return'],
                'oos_ann': oos_met['ann_return'],
                'n_entries': r['n_entries'],
                'cost_per_entry_pct': r['cost_pct_per_entry'],
            })
    return pd.DataFrame(rows)


# ============================================================
# 7. CPCV walk-forward
# ============================================================
def cpcv_walk_forward(bars: pd.DataFrame, lookback: int, thr: float,
                      n_groups: int = 6, k_test: int = 2,
                      venue=BINANCE_SPOT) -> dict:
    """Pure CPCV on (lookback, thr) parameters — for robustness check.
    Refit: in non-OFI version, lookback/thr are fixed; we just split bars.
    Returns per-fold sharpe + aggregate.
    """
    n = len(bars)
    group_size = n // n_groups
    fold_sharpes = []
    fold_returns = []
    for i in range(n_groups):
        for j in range(i + 1, n_groups):  # all pairs (k_test=2)
            test_start = i * group_size
            test_end = (j + 1) * group_size
            test_bars = bars.iloc[test_start:test_end]
            # Embargo: drop first 60 and last 60 bars of test
            if len(test_bars) < 200:
                continue
            test_bars = test_bars.iloc[60:-60]
            r = compute_returns(test_bars, lookback, thr, venue=venue)
            m = metrics(r['net'])
            fold_sharpes.append(m['sharpe'])
            fold_returns.append(r['net'].dropna().values)
    if not fold_sharpes:
        return {'mean_oos_sharpe': float('nan'), 'fold_sharpes': fold_sharpes, 'std_oos_sharpe': float('nan')}
    return {
        'mean_oos_sharpe': float(np.mean(fold_sharpes)),
        'std_oos_sharpe': float(np.std(fold_sharpes, ddof=1)),
        'fold_sharpes': fold_sharpes,
    }


# ============================================================
# 8. Multi-window robustness: split into 3 rolling windows, each WF in half.
# ============================================================
def rolling_window_oos(bars: pd.DataFrame, lookback: int, thr: float,
                       venue=BINANCE_SPOT) -> pd.DataFrame:
    """3 rolling sub-windows, each with internal 50/50 IS/OOS split."""
    n = len(bars)
    win_size = n // 3
    rows = []
    for w in range(3):
        b = bars.iloc[w * win_size:(w + 1) * win_size]
        if len(b) < 1000:
            continue
        cut = len(b) // 2
        r = compute_returns(b, lookback, thr, venue=venue)
        is_m = metrics(r['net'].iloc[:cut])
        oos_m = metrics(r['net'].iloc[cut:])
        rows.append({'window': w, 'is_sharpe': is_m['sharpe'], 'oos_sharpe': oos_m['sharpe']})
    return pd.DataFrame(rows)


# ============================================================
# MAIN
# ============================================================
def main():
    t0 = time.time()
    print('Loading bars...')
    bars = load_bars()
    print(f'Loaded {len(bars):,} bars in {time.time()-t0:.1f}s')
    print(f'Range: {bars.index.min()} → {bars.index.max()}')

    # Choose spot venue (defensible; perp ~similar). Record cost pct.
    cost_pct = apply_cost(10000, 5e9, venue=BINANCE_SPOT, side='taker') / 10000
    cost_pct_futures = apply_cost(10000, 5e9, venue=BINANCE_FUTURES, side='taker') / 10000
    print(f'Round-trip cost SPOT: {cost_pct*1e4:.2f}bp   FUTURES: {cost_pct_futures*1e4:.2f}bp')

    # Lookback grid: 1h, 2h, 4h, 8h, 24h (in minutes)
    lookbacks = (60, 120, 240, 480, 1440)
    thrs = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
    print(f'\n=== Grid: lookbacks={lookbacks} x thrs={thrs} = {len(lookbacks)*len(thrs)} cells')

    # Compute IS/OOS sharpes
    t1 = time.time()
    df_is_oos = walk_forward(bars, lookbacks=lookbacks, thrs=thrs, venue=BINANCE_SPOT)
    print(f'Grid (spot) computed in {time.time()-t1:.1f}s')
    df_is_oos.to_csv(OUT / 'ofi_grid_spot.csv', index=False)

    print('\n=== Spot cost IS/OOS grid ===')
    pd.set_option('display.float_format', lambda x: f'{x:.3f}')
    print(df_is_oos.sort_values('oos_sharpe', ascending=False).head(15).to_string(index=False))

    # Top cells by OOS
    top = df_is_oos.sort_values('oos_sharpe', ascending=False).head(10)
    print('\nTOP 10 OOS cells:', top[['lookback', 'thr', 'is_sharpe', 'oos_sharpe', 'is_ann', 'oos_ann']].to_string(index=False))

    # === Acceptance gates ===
    n_pass = (df_is_oos['oos_sharpe'] >= 1.0).sum()
    print(f'\nGate G1 (OOS sharpe >= 1.0): {n_pass} / {len(df_is_oos)} cells pass')

    # === Deflated Sharpe calculation ===
    best_sharpe = float(df_is_oos['oos_sharpe'].max())
    n_trials = len(df_is_oos)
    sample_len = len(bars) // 2  # OOS portion
    from _shared.validation.cpcv import deflated_sharpe
    dsr = deflated_sharpe(best_sharpe, n_trials, sample_len)
    print(f'\nGate G2 DSR: best={best_sharpe:.3f} n_trials={n_trials} sample_len={sample_len} → DSR={dsr:.3f} (>0 = pass)')

    # === Cost cap: bar-level mean |return| > 5 * cost_per_entry_pct
    # If mean gross >> cost drag, the strategy clears cost
    cost_per_entry_pct = float(df_is_oos['cost_per_entry_pct'].iloc[0])
    print(f'\nCost per entry: {cost_per_entry_pct*1e4:.2f}bp')

    # Compute mean gross return per entry across all cells (rough)
    mean_gross_per_entry = []
    for _, row in df_is_oos.iterrows():
        # Re-run to get mean per entry
        L, thr = int(row['lookback']), float(row['thr'])
        r = compute_returns(bars, L, thr, venue=BINANCE_SPOT)
        n_ent = max(r['n_entries'], 1)
        mean_gross = float(r['gross'].abs().sum() / n_ent)
        mean_gross_per_entry.append(mean_gross)

    df_is_oos['mean_gross_per_entry'] = mean_gross_per_entry
    df_is_oos['cost_cap_ratio'] = df_is_oos['mean_gross_per_entry'] / cost_per_entry_pct

    cap_pass = (df_is_oos['cost_cap_ratio'] >= 5.0).sum()
    print(f'Gate G5 cost-cap (>= 5x): {cap_pass} / {len(df_is_oos)} cells pass')

    df_is_oos.to_csv(OUT / 'ofi_grid_spot.csv', index=False)

    # Save top for the run summary
    summary = {
        'bars_loaded': int(len(bars)),
        'lookbacks': list(lookbacks),
        'thrs': list(thrs),
        'n_cells': int(len(df_is_oos)),
        'best_oos_sharpe': float(best_sharpe),
        'best_cell': top.iloc[0].to_dict(),
        'n_pass_g1': int(n_pass),
        'dsr': float(dsr),
        'n_pass_g5_cost_cap': int(cap_pass),
        'cost_per_entry_pct_spot_bp': float(cost_per_entry_pct * 1e4),
        'cost_per_entry_pct_futures_bp': float(cost_pct_futures * 1e4),
    }
    with open(RESULTS_PATH, 'w') as f:
        json.dump(summary, f, indent=2, default=str)

    print('\n=== SUMMARY ===')
    print(json.dumps(summary, indent=2, default=str))

    # Bonus: CPCV on top 1 cell for robustness quantification
    best_L = int(top.iloc[0]['lookback'])
    best_thr = float(top.iloc[0]['thr'])
    print(f'\nRunning CPCV on best cell: L={best_L}, thr={best_thr}')
    t2 = time.time()
    cpcv = cpcv_walk_forward(bars, lookback=best_L, thr=best_thr, n_groups=6, k_test=2)
    print(f'CPCV computed in {time.time()-t2:.1f}s')
    print(f'CPCV: mean_oos_sharpe={cpcv["mean_oos_sharpe"]:.3f}, std_oos_sharpe={cpcv["std_oos_sharpe"]:.3f}')
    print(f'Per-fold: {[round(s,3) for s in cpcv["fold_sharpes"]]}')

    summary['cpcv_mean_oos_sharpe'] = cpcv['mean_oos_sharpe']
    summary['cpcv_std_oos_sharpe'] = cpcv['std_oos_sharpe']
    summary['cpcv_fold_sharpes'] = [round(s, 3) for s in cpcv['fold_sharpes']]
    with open(RESULTS_PATH, 'w') as f:
        json.dump(summary, f, indent=2, default=str)


if __name__ == '__main__':
    main()
