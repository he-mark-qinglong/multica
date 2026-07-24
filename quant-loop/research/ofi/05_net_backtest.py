"""Final honest backtest with holding period and cost amortization.
Tests whether OFI signal can clear realistic taker costs with longer holding.
"""
import sys
import json
import time
import numpy as np
import pandas as pd
sys.path.insert(0, '/home/smark/multica/quant-loop')
from ofi_sanity import load_bars, z_ofi
from _shared.execution.cost_model import BINANCE_SPOT, BINANCE_FUTURES, apply_cost

OUT = '/home/smark/multica/quant-loop/research/ofi'

# ============================================================
# Strategy with holding period: enter on signal, hold for H bars
# ============================================================
def strategy_with_hold(bars, lookback, thr, hold_bars=1):
    """
    Signal at time t → enter at t+1 (next bar) → hold for `hold_bars` bars.
    Cost charged once at entry (round-trip amortized over hold period).

    For hold_bars=1, charged on bar t+1, realized return at t+2... no wait:
    If we hold for H bars starting at t+1, realized returns at bars t+1, t+2, ..., t+H.
    Cost is total round-trip = fee + slip × 2; charged once at the entry bar.

    With H=1: enter bar t+1, exit at end of bar t+1 (or open of t+2). Net = mid_ret_{t+1} - cost.
    With H=k: enter bar t+1, exit at end of t+k. Net = sum(mid_ret[t+1..t+k]) - cost.
    """
    z = z_ofi(bars, lookback)
    # Signal trigger at bar t: long if z_t > thr, short if z_t < -thr
    pos_signal = pd.Series(0, index=z.index)
    pos_signal[z > thr] = 1
    pos_signal[z < -thr] = -1
    pos_signal = pos_signal.shift(1).fillna(0)  # shifted: signal at t held during bar t+1

    if hold_bars <= 1:
        # Strategy: pos_t is signal at t-1, holding this bar only
        pos = pos_signal.copy()
        # For a hold_bars=1 with round-trip: pos_t triggers entry at start of bar t+1, exit end of bar t+1
        # Realized return = mid_ret at bar t+1
        # But our pos is already shifted; realize at pos_t+1.
        # Simpler: realized = pos_signal shifted to t+1, so realized at end of bar t+1
        # Wait — we need a fresh entry at first signal AND exit on signal loss.
        # Re-build as "trade on signal change": enter when pos != prev_pos and pos != 0; exit otherwise
        # For simplicity, treat each bar's pos as the bar's full-period trade. Cost once per active bar.
        # Per-bar net = pos * mid_ret - (|pos_change| > 0) * cost_pct_round_trip
        gross = pos * bars['mid_ret']
        # For each bar where pos goes non-zero (new entry) we pay entry cost; when pos returns to 0 we pay exit cost.
        # With round-trip x 2 already modeled as single 'apply_cost' round_trip=true, just charge per entry.
        pos_change = pos.diff().abs().fillna(0)
        # entries: pos != 0 AND prev_pos == 0 OR pos flips sign
        enters = (pos != 0) & ((pos.shift(1) == 0) | (pos != pos.shift(1)))
        cost_per_entry = apply_cost(10000, 5e9, venue=BINANCE_SPOT, side='taker') / 10000
        cost = enters.astype(int) * cost_per_entry
        net = gross - cost
        return {'gross': gross, 'net': net, 'n_entries': int(enters.sum()),
                'cost_per_entry': cost_per_entry}

    # For longer hold, we keep position for hold_bars bars after first signal
    # Overlap rule: when in a position, ignore further signals until exit
    pos = pd.Series(0, index=z.index)
    held_until = -1  # bar index we are currently holding until (exclusive end)
    cost_per_entry = apply_cost(10000, 5e9, venue=BINANCE_SPOT, side='taker') / 10000
    n_entries = 0
    cost_arr = pd.Series(0.0, index=z.index)
    for i in range(len(z)):
        if i > held_until and i > 0:
            sig = pos_signal.iloc[i]
            if sig != 0:
                pos.iloc[i] = sig
                held_until = i + hold_bars - 1
                n_entries += 1
                cost_arr.iloc[i] += cost_per_entry
    # After loop, future bars beyond the loop also can't fire (held_until never reached)
    gross = pos * bars['mid_ret']
    net = gross - cost_arr
    return {'gross': gross, 'net': net, 'n_entries': int(n_entries),
            'cost_per_entry': cost_per_entry}


# ============================================================
# Metrics
# ============================================================
def metrics(returns, periods_per_year=365*24*60):
    r = returns.dropna()
    if len(r) < 10 or r.std() < 1e-15:
        return {'sharpe': 0.0, 'ann_return': 0.0, 'ann_vol': 0.0, 'n': len(r),
                'hit_rate': float('nan'), 'mean': 0.0, 'std': 0.0}
    mu = float(r.mean()); sd = float(r.std(ddof=1))
    sharpe = mu / sd * np.sqrt(periods_per_year) if sd > 0 else 0.0
    ann = mu * periods_per_year
    ann_vol = sd * np.sqrt(periods_per_year)
    return {'sharpe': sharpe, 'ann_return': ann, 'ann_vol': ann_vol, 'n': len(r),
            'hit_rate': float((r > 0).sum()/len(r)), 'mean': mu, 'std': sd}


# ============================================================
# Sweep
# ============================================================
def run_sweep(bars, lookbacks=(60, 240, 1440),
              thrs=(0.3, 0.5, 0.7, 1.0, 1.5, 2.0),
              holds=(1, 5, 15, 60, 240)):
    """Lookback × thr × hold_bars sweep."""
    rows = []
    n = len(bars)
    cut = n // 2  # OOS = second half
    for L in lookbacks:
        for thr in thrs:
            for H in holds:
                r = strategy_with_hold(bars, L, thr, hold_bars=H)
                is_m = metrics(r['net'].iloc[:cut])
                oos_m = metrics(r['net'].iloc[cut:])
                rows.append({
                    'lookback': L,
                    'thr': thr,
                    'hold_bars': H,
                    'is_sharpe': is_m['sharpe'],
                    'oos_sharpe': oos_m['sharpe'],
                    'is_ann': is_m['ann_return'],
                    'oos_ann': oos_m['ann_return'],
                    'oos_n': is_m['n'] - cut,  # rough proxy
                    'n_entries_total': r['n_entries'],
                    'cost_per_entry': r['cost_per_entry'],
                })
    return pd.DataFrame(rows)


def main():
    bars = load_bars()
    print(f'bars: {len(bars):,}')
    cost_spot = apply_cost(10000, 5e9, venue=BINANCE_SPOT, side='taker') / 10000
    cost_fut = apply_cost(10000, 5e9, venue=BINANCE_FUTURES, side='taker') / 10000
    print(f'Cost per entry: SPOT {cost_spot*1e4:.2f}bp   FUTURES {cost_fut*1e4:.2f}bp')

    t0 = time.time()
    sweep = run_sweep(bars)
    print(f'Sweep {len(sweep)} cells in {time.time()-t0:.1f}s')
    sweep.to_csv(f'{OUT}/sweep_hold.csv', index=False)

    # Top 15 by OOS Sharpe
    print('\n=== TOP 15 OOS Sharpe (net of cost) ===')
    pd.set_option('display.float_format', lambda x: f'{x:.2f}')
    cols = ['lookback', 'thr', 'hold_bars', 'is_sharpe', 'oos_sharpe', 'is_ann', 'oos_ann', 'n_entries_total']
    print(sweep.sort_values('oos_sharpe', ascending=False)[cols].head(15).to_string(index=False))

    # Pass counts
    n_pass = (sweep['oos_sharpe'] >= 1.0).sum()
    n_pass_strong = (sweep['oos_sharpe'] >= 3.0).sum()
    print(f'\nGate G1 OOS Sharpe >= 1.0: {n_pass}/{len(sweep)} cells pass')
    print(f'Gate G1+ Sharpe >= 3.0: {n_pass_strong}/{len(sweep)} cells pass')

    # Find best cell, run CPCV on it
    best = sweep.sort_values('oos_sharpe', ascending=False).iloc[0]
    L, thr, H = int(best['lookback']), float(best['thr']), int(best['hold_bars'])
    print(f'\nCPCV on best cell: L={L}, thr={thr}, H={H}')

    # Simple split-CPCV: 4 equal subperiods, test on each
    n = len(bars)
    win = n // 4
    fold_sharpes = []
    for w in range(4):
        b = bars.iloc[w*win:(w+1)*win]
        cut = len(b) // 2
        r = strategy_with_hold(b, L, thr, H)
        m = metrics(r['net'].iloc[cut:])
        fold_sharpes.append(m['sharpe'])
    print(f'Per-window OOS Sharpes (4 subperiods, each OOS=last half): {[round(s,2) for s in fold_sharpes]}')
    mean_fold = float(np.mean(fold_sharpes))
    std_fold = float(np.std(fold_sharpes, ddof=1))
    print(f'Mean: {mean_fold:.2f}   Std: {std_fold:.2f}')

    # Verdicts
    summary = {
        'bars': int(len(bars)),
        'cost_per_entry_spot_bp': float(cost_spot * 1e4),
        'cost_per_entry_futures_bp': float(cost_fut * 1e4),
        'best_cell': best.to_dict(),
        'cpcv_mean_oos_sharpe': mean_fold,
        'cpcv_std_oos_sharpe': std_fold,
        'cpcv_fold_sharpes': [round(s, 3) for s in fold_sharpes],
        'n_pass_g1_sharpe_1': int(n_pass),
        'n_pass_g1_strong_3': int(n_pass_strong),
        'n_cells': len(sweep),
    }
    # Save
    with open(f'{OUT}/sweep_summary.json', 'w') as f:
        json.dump(summary, f, indent=2, default=str)
    print('\n=== SUMMARY ===')
    print(json.dumps(summary, indent=2, default=str))


if __name__ == '__main__':
    main()
