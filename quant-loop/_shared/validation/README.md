# validation / CPCV

Combinatorial Purged Cross-Validation (CPCV) per López de Prado,
*Advances in Financial Machine Learning* Ch.7. Splits the series into `N`
contiguous groups, holds out every `C(N,K)` combination as test, refits the
strategy on the remaining `N-K` groups, and aggregates OOS Sharpe across all
paths. Purge + embargo bars guard train/test boundaries against label leakage.

## Why the old `oos_walk_forward.py` is broken

It computes equity on the **full** sample, then slices the last 40% as "OOS".
Parameters chosen on the full sample contaminate the OOS window — the SPEC's
"no parameters are fit per fold" is the *opposite* of what WF should test.
CPCV forces a real refit on train-only per fold.

## Usage (opt-in — strategies must call explicitly)

```python
from _shared.validation.cpcv import cpcv
res = cpcv(data, strategy_fn, n_groups=6, k_test=2,
           purge_bars=100, embargo_bars=50)
print(res.mean_oos_sharpe, res.oos_sharpe_ci95)
```

`strategy_fn(data_train, data_full)` must refit on `data_train` only and emit
a per-bar returns `Series` indexed over `data_full`; the harness slices test.

## Deflated Sharpe Ratio

`deflated_sharpe(observed, n_trials, sample_len, skew, kurt)` replaces the
bogus G7 "Bonferroni α=0.0125" multiple-testing claim with the
Bailey & López de Prado (2014) correction. If `observed > deflated`, the edge
survives `n_trials` backtests at 95%.

## Status

**NOT auto-wired.** Opt-in library. Does not touch `oos_walk_forward.py`, any
existing strategy, autopilot, cron, or daemon.
