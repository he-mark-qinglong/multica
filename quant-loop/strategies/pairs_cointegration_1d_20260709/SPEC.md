# Pairs Cointegration 1D — Strategy Spec

> **Status**: B1 (cointegration + OLS hedge ratio). B2 will fill in signal, portfolio,
> and backtest logic on top of this scaffold. The directory layout is already in
> place so B2 can land cleanly.

## Goal

Market-neutral stat-arb on liquid crypto pairs. For each cointegrated pair, trade
the spread `log(A) - alpha - beta * log(B)` as a mean-reverting series.

## Universe

`BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, ADAUSDT, AVAXUSDT` (6 high-liquidity coins,
tradeable on Binance perps).

## Pair selection (rolling 90d, recomputed weekly)

For each pair `(A, B)` in the universe:

1. Fit OLS `log(A) = alpha + beta * log(B)` on the trailing 90 daily closes.
2. Run Engle-Granger 2-step cointegration test on the OLS residuals:
   - `adfuller` on the residuals with `regression="c"`, `maxlag=1`.
   - Keep pairs with `p_value < 0.05`.
3. Compute the OLS `beta` (hedge ratio) and store `(alpha, beta, p_value, n_obs)`.

Sort by `p_value` ascending; keep the top-3 most-cointegrated pairs.

## Signal (B2 owns the implementation; spec here for context)

Daily bar-close recompute:

- Recompute the rolling hedge ratio `(alpha, beta)` on a 90d window.
- Compute spread `s_t = log(A_t) - alpha - beta * log(B_t)`.
- Compute z-score on a 30d rolling window of the spread: `z = (s - mean) / std`.
- **Entry**:
  - `z >  2.0` → SHORT the spread (short A, long `beta` x B).
  - `z < -2.0` → LONG the spread (long A, short `beta` x B).
- **Exit**:
  - `|z| < 0.5` → flatten.

## Position sizing (B2)

- Each pair leg = 5% of account equity, market-neutral.
- Max 3 active pairs simultaneously.
- Single-pair max gross = 20%.

## Risk controls (B2 + backtest)

- **Cointegration break**: daily change in spread > 4σ → force-close pair.
- **Pair-level monthly max loss**: -3% → pause pair 30 calendar days.
- **Portfolio monthly max loss**: -5% → flatten all pairs 30 calendar days.

## Rebalance schedule

- Z-score: every daily bar close.
- Hedge ratio `(alpha, beta)`: weekly (rolling 90d OLS).
- Pair selection: weekly.

## Deliverables

| File | Owner | Status |
|------|-------|--------|
| `cointegration.py` | B1 (this agent) | ✅ complete + 20 tests |
| `strategy.py` | B2 | scaffold provided; B2 fills signal/backtest |
| `portfolio.py` | B2 | scaffold provided |
| `backtest.py` | B3 | scaffold provided |
| `tests/test_cointegration.py` | B1 (this agent) | ✅ 20/20 passing |
| `tests/test_strategy.py` | B2 | pending |
| `SPEC.md` | this file | ✅ |
| `config.json` | this file | ✅ |
| `results/` | B4 (perf analyst) | pending |

## B1 hand-off contract

B2 can assume:

- `from cointegration import ols_hedge_ratio, engle_granger_test, rolling_hedge_ratio, rolling_zscore, compute_spread, half_life`
- All public functions take `ArrayLike = np.ndarray | pd.Series`; returns are
  dataclasses (`HedgeRatio`, `EGTestResult`) or pandas DataFrames.
- Rolling hedge ratio index preserves the input Series index.
- `engle_granger_test` accepts `regression="c" | "n" | "nc"` (with `"nc"` aliased
  to `"n"` for backward compatibility) and `maxlag` (default 1).
- All functions are pure (no globals, no I/O).

## Verification (B1)

```
$ cd /home/smark/multica/quant-loop/strategies/pairs_cointegration_1d_20260709
$ python3 -m pytest tests/test_cointegration.py -v
============================== 20 passed in 0.87s ==============================
```