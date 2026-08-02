"""Automated strategy discovery engine.

Generates candidate strategies systematically across symbols, timeframes,
and signal types — tests each through the existing gate system — and
outputs a ranked list of survivors. This replaces manual one-by-one
testing with automated search at scale.

Pipeline:
  1. ENUMERATE: generate parameter grid (symbol × signal × params × timeframe)
  2. EXECUTE: run each candidate through the backtester
  3. GATE: feed results through G1-G7 + T1 falsification
  4. RANK: sort survivors by Deflated Sharpe (multiple-testing corrected)
  5. EVOLVE: genetic mutation/crossover on top survivors (next generation)

This is the "strategy factory" — the machine that finds strategies.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Candidate definition
# ---------------------------------------------------------------------------

@dataclass
class StrategyCandidate:
    """One strategy configuration to be tested."""

    id: str
    symbol: str
    signal_type: str           # 'funding_carry' | 'momentum' | 'mean_revert' | 'market_making'
    timeframe: str             # 'funding' | '1m' | '15m' | '1h'
    params: dict               # strategy-specific parameters
    generation: int = 0        # 0 = initial grid, 1+ = evolved


@dataclass
class CandidateResult:
    """Result of testing one candidate."""

    candidate: StrategyCandidate
    n_trades: int
    avg_pnl_bp: float
    win_rate: float
    profit_factor: float
    sharpe: float
    max_drawdown_bp: float
    deflated_sharpe: float
    passed_gates: bool
    failed_gate_names: list[str]
    pnl_history_bp: list[float]
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# Signal implementations (pluggable)
# ---------------------------------------------------------------------------

def signal_funding_carry(fund_data: pd.DataFrame, params: dict,
                         bars_1m: pd.DataFrame | None = None) -> list[dict]:
    """Funding carry signal: counter-funding position at each funding event.

    CORRECT MODEL (Model B) — two invariants that the naive model violates:

    1. **Funding is paid only at settlement, to whoever holds at that moment.**
       If the position is stopped out before the next settlement, funding
       collected = 0. The naive model always credits funding → overstates PnL.

    2. **Stop-loss is checked on intraday 1m high/low, exit AT the SL level.**
       The naive model caps the *end-of-period* price move at SL → lookahead
       bias: a price that dips past SL then recovers is scored as -sl_bp when
       a real stop would have exited at -sl_bp... AND the naive model scores
       the recovered price. Both directions of error inflate results.

    Parameters
    ----------
    fund_data : pd.DataFrame
        Columns: ts, fundingRate, markPrice.
    params : dict
        threshold_bp, sl_bp (0 = no stop), rt_fee_bp.
    bars_1m : pd.DataFrame, optional
        1m OHLCV bars with columns open_time(ms), high, low.
        Required when sl_bp > 0; without it, SL is ignored (documented).

    Returns
    -------
    list of trade dicts: {ts, direction, pnl_bp, exit_reason}
    """
    threshold = params.get('threshold_bp', 2) / 10000
    sl_bp = params.get('sl_bp', 0)  # 0 = no stop loss
    rt_fee_bp = params.get('rt_fee_bp', 7)

    # Prepare intraday bar arrays for SL checking
    bar_ts = None
    bar_high = None
    bar_low = None
    if bars_1m is not None and len(bars_1m) > 0:
        bar_ts = pd.to_datetime(bars_1m['open_time'], unit='ms', utc=True).values
        bar_high = bars_1m['high'].values
        bar_low = bars_1m['low'].values

    trades = []
    for i in range(len(fund_data) - 1):
        fr = fund_data.iloc[i].get('fundingRate', 0)
        if abs(fr) < threshold:
            continue
        cur = fund_data.iloc[i].get('markPrice', None)
        nxt = fund_data.iloc[i + 1].get('markPrice', None)
        if pd.isna(cur) or pd.isna(nxt) or cur <= 0:
            continue

        direction = -1 if fr > 0 else 1
        t0 = fund_data.iloc[i]['ts']
        t1 = fund_data.iloc[i + 1]['ts']

        # ---- intraday SL check ----
        sl_hit = False
        if sl_bp > 0 and bar_ts is not None:
            lo_idx = bar_ts.searchsorted(np.datetime64(t0.tz_convert(None)))
            hi_idx = bar_ts.searchsorted(np.datetime64(t1.tz_convert(None)))
            if hi_idx > lo_idx:
                if direction == -1:  # short: SL if high crosses entry*(1+sl)
                    stop_price = cur * (1 + sl_bp / 10000)
                    if (bar_high[lo_idx:hi_idx] >= stop_price).any():
                        sl_hit = True
                else:  # long: SL if low crosses entry*(1-sl)
                    stop_price = cur * (1 - sl_bp / 10000)
                    if (bar_low[lo_idx:hi_idx] <= stop_price).any():
                        sl_hit = True

        if sl_hit:
            # Stopped out BEFORE settlement → NO funding collected
            funding_pnl_bp = 0.0
            price_pnl_bp = -sl_bp
            exit_reason = 'sl'
        else:
            funding_pnl_bp = abs(fr) * 10000
            price_pnl_bp = direction * (nxt - cur) / cur * 10000
            exit_reason = 'settle'

        net_bp = funding_pnl_bp + price_pnl_bp - rt_fee_bp
        trades.append({
            'ts': t0,
            'direction': 'short' if fr > 0 else 'long',
            'pnl_bp': net_bp,
            'funding_bp': funding_pnl_bp,
            'price_bp': price_pnl_bp,
            'exit_reason': exit_reason,
        })
    return trades


def signal_momentum(prices: np.ndarray, timestamps: pd.Series, params: dict) -> list[dict]:
    """Momentum signal: enter in direction of recent return."""
    lookback = params.get('lookback', 50)
    min_move_bp = params.get('min_move_bp', 2)
    tp_bp = params.get('tp_bp', 5)
    sl_bp = params.get('sl_bp', 10)
    hold_bars = params.get('hold_bars', 60)
    rt_fee_bp = params.get('rt_fee_bp', 7)
    step = params.get('step', 10)

    trades = []
    open_pos = None
    n = len(prices)

    for i in range(lookback, n, step):
        px = prices[i]
        if open_pos:
            d, ep, entry_i = open_pos
            pnl_bp = ((px - ep) / ep * 10000) if d == 'long' else ((ep - px) / ep * 10000)
            net = pnl_bp - rt_fee_bp
            if net >= tp_bp or net <= -sl_bp or (i - entry_i) >= hold_bars:
                trades.append({'ts': timestamps.iloc[entry_i], 'direction': d, 'pnl_bp': net})
                open_pos = None
        if open_pos:
            continue

        ret_bp = (px - prices[i - lookback]) / prices[i - lookback] * 10000
        if abs(ret_bp) < min_move_bp:
            continue
        d = 'long' if ret_bp > 0 else 'short'
        open_pos = (d, px, i)

    if open_pos:
        d, ep, _ = open_pos
        px = prices[-1]
        pnl_bp = ((px - ep) / ep * 10000) if d == 'long' else ((ep - px) / ep * 10000)
        trades.append({'ts': timestamps.iloc[open_pos[2]], 'direction': d, 'pnl_bp': pnl_bp - rt_fee_bp})

    return trades


def signal_mean_revert(prices: np.ndarray, qtys: np.ndarray, timestamps: pd.Series, params: dict) -> list[dict]:
    """VWAP mean reversion signal."""
    window = params.get('vwap_window', 100)
    dev_bp = params.get('deviation_bp', 5)
    tp_bp = params.get('tp_bp', 3)
    sl_bp = params.get('sl_bp', 8)
    hold_bars = params.get('hold_bars', 120)
    rt_fee_bp = params.get('rt_fee_bp', 7)
    step = params.get('step', 10)

    trades = []
    open_pos = None
    n = len(prices)

    for i in range(window, n, step):
        px = prices[i]
        w = qtys[i - window:i]
        p = prices[i - window:i]
        total_w = w.sum()
        if total_w <= 0:
            continue
        vwap = (p * w).sum() / total_w
        actual_dev = (px - vwap) / vwap * 10000

        if open_pos:
            d, ep, entry_i = open_pos
            pnl_bp = ((px - ep) / ep * 10000) if d == 'long' else ((ep - px) / ep * 10000)
            net = pnl_bp - rt_fee_bp
            if net >= tp_bp or net <= -sl_bp or (i - entry_i) >= hold_bars:
                trades.append({'ts': timestamps.iloc[entry_i], 'direction': d, 'pnl_bp': net})
                open_pos = None
        if open_pos:
            continue

        if actual_dev < -dev_bp:
            open_pos = ('long', px, i)
        elif actual_dev > dev_bp:
            open_pos = ('short', px, i)

    if open_pos:
        d, ep, _ = open_pos
        px = prices[-1]
        pnl_bp = ((px - ep) / ep * 10000) if d == 'long' else ((ep - px) / ep * 10000)
        trades.append({'ts': timestamps.iloc[open_pos[2]], 'direction': d, 'pnl_bp': pnl_bp - rt_fee_bp})

    return trades


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------

def evaluate_candidate(pnl_history_bp: list[float], n_trials: int = 1) -> dict:
    """Evaluate a PnL history through simplified gate checks.

    Returns dict with all gate metrics + pass/fail.
    """
    arr = np.array(pnl_history_bp, dtype=float)
    n = len(arr)

    if n < 2:
        return {
            'n_trades': n, 'avg_pnl_bp': 0, 'win_rate': 0, 'profit_factor': 0,
            'sharpe': 0, 'max_drawdown_bp': 0, 'deflated_sharpe': 0,
            'passed': False, 'failed_gates': ['T1_min_trades'],
        }

    avg = float(np.mean(arr))
    std = float(np.std(arr, ddof=1)) if n > 1 else 0
    sharpe = avg / std * np.sqrt(max(1, n)) if std > 0 else 0
    win_rate = float(np.mean(arr > 0))

    gains = arr[arr > 0].sum()
    losses = abs(arr[arr < 0].sum())
    pf = float(gains / losses) if losses > 0 else 999.0

    cum = np.cumsum(arr)
    running_max = np.maximum.accumulate(cum)
    max_dd = float((cum - running_max).min())

    # Deflated Sharpe (simplified Bailey-LdP)
    try:
        from scipy.stats import norm
        skew = float(np.mean(((arr - avg) / std) ** 3)) if std > 0 else 0
        kurt = float(np.mean(((arr - avg) / std) ** 4)) - 3.0 if std > 0 else 0
        sharpe_var = (1 + 0.5 * sharpe ** 2 - skew * sharpe + (kurt / 4) * sharpe ** 2) / n
        expected_max = (np.sqrt(2 * np.log(n_trials)) if n_trials > 1 else 0)
        dsr = (sharpe * np.sqrt(n) - expected_max) / np.sqrt(n * sharpe_var) if sharpe_var > 0 else 0
    except Exception:
        dsr = 0.0

    # Gate checks
    failed = []
    if n < 10: failed.append('T1_min_trades')
    if sharpe < 1.0: failed.append('G1_sharpe')
    if avg < 0: failed.append('G0_positive_edge')
    if pf < 1.0: failed.append('G4_profit_factor')
    if max_dd < -2500: failed.append('G3_max_drawdown')  # -25%
    if dsr < 0: failed.append('G7_deflated_sharpe')

    return {
        'n_trades': n, 'avg_pnl_bp': avg, 'win_rate': win_rate,
        'profit_factor': pf, 'sharpe': sharpe,
        'max_drawdown_bp': max_dd, 'deflated_sharpe': dsr,
        'passed': len(failed) == 0, 'failed_gates': failed,
    }


# ---------------------------------------------------------------------------
# Sweep engine
# ---------------------------------------------------------------------------

@dataclass
class SweepConfig:
    """What to search over."""

    symbols: list[str] = field(default_factory=lambda: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'])
    signal_types: list[str] = field(default_factory=lambda: [
        'funding_carry', 'momentum', 'mean_revert',
    ])

    # Parameter grids
    funding_grid: dict = field(default_factory=lambda: {
        'threshold_bp': [1, 2, 3, 4, 5, 8],
        'sl_bp': [0, 100, 200, 500],
        'rt_fee_bp': [7],
    })
    momentum_grid: dict = field(default_factory=lambda: {
        'lookback': [20, 50, 100],
        'min_move_bp': [2, 5, 10],
        'tp_bp': [3, 5, 8],
        'sl_bp': [6, 10, 15],
        'hold_bars': [30, 60, 120],
        'step': [10],
        'rt_fee_bp': [7],
    })
    mean_revert_grid: dict = field(default_factory=lambda: {
        'vwap_window': [50, 100, 200],
        'deviation_bp': [3, 5, 8, 12],
        'tp_bp': [2, 3, 5],
        'sl_bp': [4, 8, 12],
        'hold_bars': [30, 60, 120],
        'step': [10],
        'rt_fee_bp': [7],
    })


def enumerate_candidates(config: SweepConfig) -> list[StrategyCandidate]:
    """Generate all candidates from the parameter grid."""
    candidates = []
    cid = 0

    for symbol in config.symbols:
        for signal_type in config.signal_types:
            if signal_type == 'funding_carry':
                grid = config.funding_grid
                timeframe = 'funding'
            elif signal_type == 'momentum':
                grid = config.momentum_grid
                timeframe = '1m'
            elif signal_type == 'mean_revert':
                grid = config.mean_revert_grid
                timeframe = '1m'
            else:
                continue

            keys = list(grid.keys())
            for combo in itertools.product(*[grid[k] for k in keys]):
                params = dict(zip(keys, combo))
                candidates.append(StrategyCandidate(
                    id=f"C{cid:05d}",
                    symbol=symbol,
                    signal_type=signal_type,
                    timeframe=timeframe,
                    params=params,
                ))
                cid += 1

    return candidates


def run_sweep(
    candidates: list[StrategyCandidate],
    data_loader: Callable[[str, str], Any],
    n_trials: int = 0,
    verbose: bool = True,
) -> list[CandidateResult]:
    """Execute all candidates and return ranked results.

    Parameters
    ----------
    candidates : list of StrategyCandidate
    data_loader : callable(symbol, timeframe) -> data
        For 'funding': returns pd.DataFrame with fundingRate, markPrice, ts
        For '1m': returns (prices_array, qtys_array, timestamps_series)
    n_trials : int
        Total number of candidates (for Deflated Sharpe correction).
    verbose : bool
        Print progress.
    """
    if n_trials == 0:
        n_trials = len(candidates)

    results: list[CandidateResult] = []
    survivors: list[CandidateResult] = []
    t0 = time.time()

    for i, cand in enumerate(candidates):
        t_start = time.time()

        try:
            data = data_loader(cand.symbol, cand.timeframe)

            if cand.signal_type == 'funding_carry':
                # data_loader must return (fund_df, bars_1m_df_or_None) for funding
                if isinstance(data, tuple):
                    fund_df, bars_1m = data
                else:
                    fund_df, bars_1m = data, None
                trades = signal_funding_carry(fund_df, cand.params, bars_1m=bars_1m)
            elif cand.signal_type == 'momentum':
                prices, qtys, timestamps = data
                trades = signal_momentum(prices, timestamps, cand.params)
            elif cand.signal_type == 'mean_revert':
                prices, qtys, timestamps = data
                trades = signal_mean_revert(prices, qtys, timestamps, cand.params)
            else:
                continue

            pnl_bp = [t['pnl_bp'] for t in trades]
            eval_result = evaluate_candidate(pnl_bp, n_trials=n_trials)

            result = CandidateResult(
                candidate=cand,
                n_trades=eval_result['n_trades'],
                avg_pnl_bp=eval_result['avg_pnl_bp'],
                win_rate=eval_result['win_rate'],
                profit_factor=eval_result['profit_factor'],
                sharpe=eval_result['sharpe'],
                max_drawdown_bp=eval_result['max_drawdown_bp'],
                deflated_sharpe=eval_result['deflated_sharpe'],
                passed_gates=eval_result['passed'],
                failed_gate_names=eval_result['failed_gates'],
                pnl_history_bp=pnl_bp,
                elapsed_seconds=time.time() - t_start,
            )
            results.append(result)

            if result.passed_gates:
                survivors.append(result)

            if verbose and (i + 1) % 50 == 0:
                elapsed = time.time() - t0
                print(f"  [{i+1}/{len(candidates)}] {elapsed:.0f}s — "
                      f"survivors: {len(survivors)} — "
                      f"current: {cand.symbol} {cand.signal_type} "
                      f"avg={result.avg_pnl_bp:.1f}bp PF={result.profit_factor:.2f}")

        except Exception as e:
            if verbose:
                print(f"  [{i+1}] ERROR {cand.id}: {e}")

    # Rank by Deflated Sharpe (multiple-testing corrected)
    results.sort(key=lambda r: r.deflated_sharpe, reverse=True)

    return results


def summarize_results(results: list[CandidateResult]) -> dict:
    """Summarize sweep results."""
    total = len(results)
    passed = [r for r in results if r.passed_gates]
    profitable = [r for r in results if r.avg_pnl_bp > 0]

    by_signal = {}
    for r in results:
        key = r.candidate.signal_type
        if key not in by_signal:
            by_signal[key] = {'total': 0, 'profitable': 0, 'passed': 0, 'best_pf': 0, 'best_avg': -999}
        by_signal[key]['total'] += 1
        if r.avg_pnl_bp > 0:
            by_signal[key]['profitable'] += 1
        if r.passed_gates:
            by_signal[key]['passed'] += 1
        by_signal[key]['best_pf'] = max(by_signal[key]['best_pf'], r.profit_factor)
        by_signal[key]['best_avg'] = max(by_signal[key]['best_avg'], r.avg_pnl_bp)

    return {
        'total_tested': total,
        'passed_gates': len(passed),
        'profitable': len(profitable),
        'pass_rate': len(passed) / max(1, total),
        'by_signal_type': by_signal,
        'top_10': [(r.candidate.id, r.candidate.symbol, r.candidate.signal_type,
                     r.avg_pnl_bp, r.profit_factor, r.sharpe, r.deflated_sharpe,
                     r.passed_gates)
                    for r in results[:10]],
    }
