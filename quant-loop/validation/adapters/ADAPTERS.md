# Framework adapters

Each module in this directory is a *framework replay adapter* — a
self-contained implementation of one external (or, in ouraq's case,
in-house) engine that re-executes the trade schedule emitted by the
native engine so the cross-framework CV harness can compare fills
under different conventions.

The shared contract is the one-line entry point:

```python
run_<name>_replay(
    df: pd.DataFrame,
    native_trades: list[dict],
    *,
    symbol: str,
    starting_cash: float = 100_000.0,
    **kwargs,
) -> FrameworkRun
```

`FrameworkRun` is defined in [`native_engine.py`](./native_engine.py).
Each adapter imports it from there; do **not** re-define it locally.

## Catalogue

| `name`       | module                                  | fill rule                     | sizing model                                     | engine required?  |
|--------------|-----------------------------------------|-------------------------------|--------------------------------------------------|-------------------|
| `native`     | `native_engine.NativeEngineAdapter`     | bar-close (signal-bar)        | `size_fraction` per trade (from variant config)  | no (always runs)  |
| `backtrader` | `backtrader_replay.run_backtrader_replay` | next-bar-open (`+1` lag)    | fixed-fraction of **starting cash**              | backtrader        |
| `freqtrade`  | `freqtrade_replay.run_freqtrade_replay`   | next-candle open via CLI    | per-trade `stake` over `max_open_trades`         | freqtrade CLI     |
| `vectorbt`   | `vectorbt_replay.run_vectorbt_replay`     | `from_signals` portfolio    | fraction of **current cash** (`size_type=percent`)| vectorbt (numba)  |
| `ouraq`      | `ouraq_replay.run_ouraq_replay`           | bar-close (no lag)         | vol-targeted fraction of **current cash**        | no (pure numpy)   |

When an adapter's external engine is not installed the adapter raises
its own `*ReplayError` (`VectorbtReplayError`, `FreqtradeReplayError`,
`OuraqReplayError`, …) and the generic harness records the leg under
`report["framework_skips"]` instead of crashing.

## Adding a new adapter

1. Create `validation/adapters/<name>_replay.py` exposing
   `run_<name>_replay(df, native_trades, *, symbol, starting_cash=..., **kwargs) -> FrameworkRun`.
2. Define a `<Name>ReplayError(RuntimeError)` and raise it for any
   recoverable input failure (missing columns, off-bar timestamps,
   non-finite sizing parameters).
3. Lazy-import the external engine so a missing dependency raises the
   adapter's own error rather than an opaque `ImportError`.
4. Add a unit test module `validation/test_<name>_replay.py` covering:
   - the happy path (returns `FrameworkRun`, equity is a dense
     `pd.Series`, trade pnls populated for a non-empty schedule),
   - every input validation branch (parameterised where reasonable),
   - one piece of behaviour unique to the adapter (the vectorbt test
     asserts the missing-engine path; the freqtrade test asserts the
     generated strategy is importable; the ouraq test asserts the
     vol-targeting scaler scales inversely with realised vol).
5. Add a row to the catalogue table above.
6. Wire the new leg into `generic_harness._run_framework_leg` *and*
   `generic_harness.FRAMEWORKS` so the `unknown frameworks` validator
   recognises it.

## Why a fifth leg?

The four existing legs cover three dimensions: native + 3 external
backtest engines. They agree on entry/exit timestamps but disagree on
fill price (bar close vs. next-bar open vs. next-candle open) and on
sizing (fixed-fraction of starting cash vs. fixed-fraction of current
cash). They do **not** cover the risk-model dimension: every existing
leg sizes every trade identically regardless of how volatile the
market was when the entry signal fired. Ouraq is a minimal, dependency-
free probe of that dimension — it is the always-runnable fifth leg
that gives the harness at least one non-native framework available on
hosts where backtrader/freqtrade/vectorbt cannot be installed.