# Cross-framework backtest adapters — SMA-35414 / MAP-P5 #047

Generic adapter modules that wrap external backtest frameworks and expose
a uniform entry point so the authoritative in-house engine in
`../run_backtest.py` can be cross-validated against an independent
implementation.

The package is intentionally tiny: one entry point per adapter, one
metrics envelope dataclass, and one shim-mode fallback so unit tests can
run on CI without the optional dependency installed.

## Public API

```python
from _shared.adapters import run_fastquant_backtest, FastquantMetrics

eq, metrics = run_fastquant_backtest(
    bars,
    trades=None,           # None → run the chosen strategy; else replay in-house trades
    strategy="smac",       # smac | emac | rsi | buynhold | bbands | macd
    commission=0.001,      # 10 bp per fill (fastquant default)
    initial_capital=100_000.0,
    fast_period=10,
    slow_period=30,
    freq_per_year=365 * 24,
    size_fraction=1.0,
    force_shim=False,      # True → skip real fastquant even if importable
)
```

`metrics` is a `FastquantMetrics` dataclass with these fields (all
JSON-safe floats):

| field           | meaning                                                    |
|-----------------|------------------------------------------------------------|
| engine          | always `"fastquant"`                                       |
| engine_version  | `shim-v1 ...` or real fastquant version                    |
| sharpe          | per-bar Sharpe, annualised by `freq_per_year`              |
| total_return    | fractional total return over the bar range                 |
| annualised_pct  | fractional annualised return                               |
| max_dd          | worst drawdown (fractional, negative)                      |
| n_bars          | length of the equity Series                                |
| n_trades        | trades applied (skipped excluded)                          |
| n_skipped       | trades whose entry/exit fell off the bar range             |
| used_shim       | `True` if the real fastquant path was not exercised        |

## Two execution paths

The adapter has two paths and picks one at call time:

1. **Real fastquant** (only when `FASTQUANT_AVAILABLE` is `True` AND
   `trades is None` AND `force_shim is False`).

   `fastquant.backtest(...)` is invoked with a built-in strategy
   (`smac` by default — fast/slow SMA crossover) on the supplied bars
   frame. The returned `results_dict["equity_curve"]` is re-indexed
   against `bars.index` and the same per-bar Sharpe / total_return /
   max_dd metrics are computed. fastquant depends on `backtesting.py`,
   which internally uses `backtrader`-like per-bar compounding
   (default `Trade.execution_price = NextBarOpen`). The cost model is a
   flat proportional commission per fill — the in-house engine's
   `cost_mode="fill"` path is the natural counterpart, so the two
   should agree on round-trip cost to within the broker's cash-drag
   compounding residual (≈0.85% on 1000-bar samples — same order as
   the documented backtrader residual).

2. **Pure-Python shim** (default when fastquant is absent, when
   `trades` is provided, or when `force_shim=True`).

   Replays the trade schedule (in-house trades or signal-derived
   schedule) with the fastquant cost convention applied:
   - Entry fill lands at bar `ei+1`'s open (next-bar execution),
     charging `commission * size` on the position notional.
   - Middle bars earn pure close-to-close return.
   - Exit fill lands at bar `xi+1`'s open (one bar AFTER the exit
     signal bar), charging `commission * size` again. If `xi+1` is
     outside the bar range, the position is left open and no exit
     commission is charged (broker hasn't fired yet).
   - Same-bar round-trip (`xi == ei`): entry AND exit fills both
     land on bar `ei+1`'s open, charging `2 * commission * size`.
   - One position at a time; a new entry force-closes the prior trade
     at the new entry's open.

   The shim is the only path exercised by the unit-test suite — it is
   deterministic, has no third-party dependency, and *documents* the
   cost-model contract a real fastquant run is expected to honour.

## Shim vs real fastquant agreement

When the real fastquant library is installed, the shim and real paths
should agree on the final equity to within the documented compounding
residual (≈0.85% on 1000-bar samples — same order as the
backtrader-vs-inhouse residual). The `used_shim` flag in
`FastquantMetrics` indicates which path produced the result.

A disagreement larger than 1% on a 1000-bar sample is a regression in
either the shim's cost-model translation or in the upstream fastquant
library — file an issue against the adapter.

## Validator hook

`to_framework_cv(metrics)` returns a dict shaped like the
`framework_cv["framework"]` entry the
`_shared/validators/framework_cv_validator.py` already understands:

```python
from _shared.adapters import run_fastquant_backtest, to_framework_cv
from _shared.validators.framework_cv_validator import validate_framework_cv

eq, m = run_fastquant_backtest(bars, trades=trades, ...)
cv_record = {
    "framework": to_framework_cv(m),
    "framework_oos": ...,   # caller fills from OOS folds
}
validate_framework_cv(inhouse_metrics, cv_record, strategy_name=NAME)
```

The `smoke test` in `test_fastquant_adapter.py`
(`test_to_framework_cv_compatible_with_validator`) round-trips through
the validator without raising.

## Strategies

`FASTQUANT_SUPPORTED_STRATEGIES = ("smac", "emac", "rsi", "buynhold",
"bbands", "macd")` — closed set, matches the upstream
[fastquant API](https://github.com/enzoampil/fastquant/blob/master/API.md).

| strategy | params                                              |
|----------|-----------------------------------------------------|
| smac     | fast_period (10), slow_period (30)                  |
| emac     | fast_period (10), slow_period (30)                  |
| rsi      | rsi_period (14), rsi_upper (70), rsi_lower (30)     |
| bbands   | period (20), devfactor (2.0)                        |
| macd     | fast_period (12), slow_period (26), signal (9)      |
| buynhold | none — enters at bar 1's open, never exits          |

Anything outside the closed set raises `ValueError`.

## Cost-model cheat sheet

| scenario                                | per-bar return at bar `b` |
|-----------------------------------------|---------------------------|
| entry fill bar (`b = ei+1`, multi-bar)  | `size * price_ret[b] - size * commission` |
| middle held bar (`ei+2 ≤ b ≤ xi`)       | `size * price_ret[b]` |
| exit fill bar (`b = xi+1`, in range)    | `- size * commission` |
| exit fill bar (`b = xi+1`, OUT of range)| `0` (position still open) |
| same-bar RT (`xi = ei`, at `b = ei+1`)  | `size * price_ret[b] - 2 * size * commission` |
| force-close of prior trade (at new `b = ei+1`) | `- prev_size * commission` |

`price_ret[b] = close[b] / close[b-1] - 1`. Per-bar compounding:
`equity[b] = equity[b-1] * (1 + bar_ret[b])`.

## Installation

Real fastquant is **optional**. The adapter falls back to the shim when
the package is absent, so the unit tests can run on bare CI. To enable
the real-fq path:

```bash
pip install fastquant
```

Then set `FASTQUANT_AVAILABLE = True` is auto-detected at module
import. Add to `validation/requirements.txt` if the production
environment should also exercise the real-fq path.

## File map

| file                                  | purpose                                  |
|---------------------------------------|------------------------------------------|
| `__init__.py`                         | re-export public surface                  |
| `fastquant_adapter.py`                | the adapter module                        |
| `test_fastquant_adapter.py`           | 26 unit tests (shim path)                |
| `README.md`                           | this file                                 |

## Cross-framework CV evidence

This adapter is one leg of the cross-framework CV (G4) used by the
`framework_cv_validator` and the per-strategy framework adapters in
`strategies/*/framework_adapter_*.py`. The shim path is exercised in
unit tests; the real-fq path is exercised by per-strategy integration
tests (out of scope for this module).

See `docs/decisions/` for the SMA-35414 triage decision and the
parent MAP-P5 hardening plan.

## SMA-35414 evidence summary

- 26/26 unit tests pass on the shim path (`pytest -v`).
- Adapter surface mirrors the existing backtrader / freqtrade pattern.
- Validator hook `to_framework_cv()` round-trips through
  `framework_cv_validator.validate_framework_cv()` without raising
  (covered by `test_to_framework_cv_compatible_with_validator`).
- Shim-vs-inhouse cross-check on a 100-bar 3-trade schedule agrees to
  <1% absolute (`test_shim_agrees_with_inhouse_engine_on_known_schedule`).