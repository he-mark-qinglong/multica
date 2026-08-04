# multifactor_stacked_v1 — Validation Report

- Validation: temporal-stability (fixed params), CPCV {'n_groups': 6, 'k_test': 2, 'purge_bars': 50, 'embargo_bars': 20}
- DSR: 1.242 on best variant **kama_only** (n_trials=4) → PASS

| Variant | Sharpe | MaxDD | Calmar | Total Ret | CPCV mean OOS Sharpe | CPCV worst fold | all folds > 0 |
|---|---|---|---|---|---|---|---|
| kama_only | 1.254 | -34.38% | 1.33 | 819.4% | 1.112 | -0.561 | NO |
| kama_imb | 0.000 | 0.00% | 0.00 | 0.0% | 0.000 | 0.000 | NO |
| kama_session | 0.296 | -27.65% | 0.12 | 20.7% | 0.378 | -0.514 | NO |
| stacked4 | 0.734 | -11.04% | 0.45 | 32.8% | 0.774 | -0.069 | NO |

## Interpretation

- `kama_only` is the validated baseline (kama_mtf_btc_4h_1d params).
- Aux factors are AND-gates on top of the KAMA veto: they can only *reduce* exposure. The study asks whether that reduction is compensated by higher risk-adjusted returns (Sharpe/Calmar/MaxDD).
- CPCV here is temporal-stability (fixed parameters), not true OOS refit — the strategy_fn intentionally ignores data_train.
- Factor 2 (book imbalance) uses the taker-flow kline proxy; real books5 history from scripts/collect_okx_book_ws.py is still accumulating.