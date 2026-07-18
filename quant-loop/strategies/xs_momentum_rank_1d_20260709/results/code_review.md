# Code Review: xs_momentum_rank_1d_20260709

> Independent review on the four axes requested in the task brief:
> correctness, security, code-quality, architecture.  Read-only pass,
> no source files modified.

## Verdict
**OVERALL: PASS-WITH-NITS**

## correctness (PASS)
- `compute_momentum_score` / `trailing_return`: math matches the spec
  formula `0.5 * r30 + 0.3 * r7 + 0.2 * r3`.  Weights are read from
  `config.json` and passed through to the function, so the score is
  driven by config and not duplicated as a magic number.
- `select_long_short` correctly shrinks K for tight universes.
  Traced manually for N=3 (the live `active_universe`):
  `top_k=3, bot_k=3, n=3` -> `actual_top=min(3, 3//2)=1`,
  `actual_bot=min(3, 3-1)=2`, skip the even-split branch because
  `1+2 == 3`.  Result: 1 long + 2 shorts, exactly matching SPEC.md
  and the `factor_exposure.json` long/short attribution (BTCUSDT is
  long ~4.4% and short ~5.6% of bars).
- NaN handling is consistent: 30d-NaN propagates through
  `momentum_score`; `rank_symbols_on` drops NaN scores; the panel
  warmup gate uses `first_valid_index().max()` so the first
  rebalance is the first date where ALL symbols have a score.
- Empty-universe / empty-ranking paths return `{}` or empty
  frames (covered by `test_select_long_short_empty_returns_empty`
  and the empty-rk branch in `select_long_short`).
- PnL sign convention is consistent: `_new_positions_from_target`
  emits a signed dollar exposure (negative for shorts), so
  `_realized_pnl` can do `pos * ret` uniformly.  `_delta_turnover_cost`
  charges `2 * sum(|delta|) * cost_per_side` (round-trip), as the
  spec describes.
- `daily_loss_flatten` correctly uses *today's* pnl against
  *prior* equity (prior_equity = pre-PnL equity on this bar, so
  ret = new_equity / prior_equity - 1).  The flatten zeroes
  positions and the next bar's `delta_cost` correctly pays the
  full close-out.

## security (PASS)
- No hardcoded secrets, API keys, tokens, or passwords anywhere
  in source or `config.json`.
- All file I/O is internal: `CONFIG_PATH = Path(__file__).parent /
  "config.json"` and `RESULTS_DIR = Path(__file__).parent /
  "results"`.  No untrusted path concatenation, no `os.path.join`
  with user input.
- `data_loader.py` raises `SystemExit` if `LIVE_TRADING=1` is set,
  which is a defensive guardrail matching the paper-trade claim
  in SPEC.md ("paper-trade only").
- `json.loads(...)` is the only untrusted-data entry point and is
  applied to a committed `config.json` (not user input).  Schema
  is implicitly enforced by key access (KeyError on missing keys,
  which is acceptable for a non-public config).
- SHA256 manifest in `data_loader.py` is read-only and detects
  upstream parquet drift; no write paths that could clobber
  upstream data.

## code-quality (NITS)
- `backtest.py:105` defines `_prior_positions` but it is never
  called.  Dead helper; safe to delete (or to keep with a TODO).
- `backtest.py:185` uses `json.loads(CONFIG_PATH.read_text())`
  but `backtest.py` does NOT `import json`.  It works at runtime
  only because `run_backtest.py` (and the test harness) import
  `json` first.  Calling `run_backtest()` directly from a
  notebook or REPL that hasn't pre-imported json will raise
  `NameError`.  Add `import json` to `backtest.py`.
- `backtest.py:34` imports `numpy as np` but never uses it
  (only `math.sqrt` is used).  Drop the import.
- `strategy.py:26` imports `List` from typing but never uses it.
- `universe.py:28` imports `Iterable` from typing but never uses
  it.
- `strategy.py:13-15` docstring references `min_history_bars=35`
  but the config key (`"min_history_bars": 35` in `config.json`)
  is never read.  The actual warmup is implemented in
  `backtest.py:224` via `panel.apply(lambda s:
  s.first_valid_index()).max()`, which is *equivalent* to
  requiring 30 bars (the 30d return lookback).  Functionality is
  preserved, but the config key is dead and the docstring is
  slightly misleading.  Either remove the field from config or
  wire it through.
- `backtest.py:203/205` apply `tz_localize("UTC")` to the user's
  `start` / `end` arguments unconditionally.  This fails with
  `TypeError: Already tz-aware` if a caller passes a tz-aware
  Timestamp (e.g. `pd.Timestamp("2025-01-01", tz="UTC")`).
  The default `run_backtest.py` path doesn't exercise this, but
  the public signature `run_backtest(..., start=..., end=...)`
  invites that misuse.  Guard with `if ts.tz is None`.
- Magic numbers `30` (lookback in `strategy.py:56`,
  `portfolio.py:155`), `7` (lookback_days in `universe.py`),
  `365` and `365.25` (annualization in `backtest.py:324/330`)
  appear inline.  All match the SPEC; acceptable but a small
  constants block would aid future tweaks.
- No mutable shared state in the per-bar loop beyond
  local-variable rebinding (positions / last_closes / equity).
  Good.
- `run_backtest` is 192 lines.  Below the 200-line guidance,
  but the for-loop is doing 7 numbered steps (PnL, flatten,
  pause, ranking, allocation, sizing, advance).  An
  `extract-method` pass on steps 1, 2, 4 would shorten it
  materially; not blocking.

## architecture (PASS)
- Dependency direction is clean and inward:
  `strategy.py`, `universe.py`, `portfolio.py`, `data_loader.py`
  are leaf modules (no internal imports).
  `backtest.py` depends on `portfolio` / `strategy` / `universe`
  (orchestrator).
  `run_backtest.py` depends on `backtest` / `data_loader` /
  `universe` (entry point).
  No circular imports, no back-deps from leaves.
- Layering matches SPEC.md's "Code surface" diagram: signal
  (`strategy.py`) and portfolio (`portfolio.py`) are separated;
  the engine (`backtest.py`) composes them; the CLI
  (`run_backtest.py`) is the composition root.
- `data_loader.py` is decoupled from `backtest.py`; the loader
  writes cached 1d parquets to `data/` and the backtest reads
  them, with SHA256 manifest as the bridge.  This is the
  battle-tested ETL pattern and a good choice vs. re-reading
  1m parquets every backtest run.
- `PortfolioTarget` / `TargetPosition` / `RebalanceEvent` /
  `BacktestResult` are well-scoped dataclasses; the engine
  returns a structured `BacktestResult` rather than a tuple,
  which aids both `summary_dict()` and the CSV writers.
- No custom framework reinvention: standard `pandas`, `numpy`,
  `dataclasses`, `json`, `pathlib`, `hashlib`.  No external
  deps beyond what's already used elsewhere in the repo.

## pytest
26 passed in 0.55s

```
$ PYTHONPATH=. python3 -m pytest tests/ -q
..........................                                               [100%]
26 passed in 0.55s
```

Test mix: 8 strategy, 9 universe, 9 portfolio, 2 E2E backtest --
matches SPEC.md's coverage table.

## nits (non-blocking)
- Drop dead code: `_prior_positions` (backtest.py:105), unused
  `numpy as np` (backtest.py:34), unused `List` (strategy.py:26),
  unused `Iterable` (universe.py:28).
- Add `import json` to `backtest.py` (currently relies on
  `run_backtest.py` to import json first).
- Either wire `min_history_bars` through or drop it from
  `config.json` and the strategy.py docstring.
- `start`/`end` filters in `backtest.py:203/205` will crash on
  tz-aware Timestamps; guard with `if ts.tz is None` before
  `tz_localize`.
- Optional `extract-method` on the per-bar loop in
  `run_backtest` to keep the engine under 150 lines.
- Magic lookback constants (`30`, `7`, `365`, `365.25`) could
  be named (`_DAILY_LOOKBACK_30D`, `_ANNUALIZATION_DAYS`) but
  the inline values are SPEC-driven and readable.

Files reviewed:
- `/home/smark/multica/quant-loop/strategies/xs_momentum_rank_1d_20260709/SPEC.md`
- `/home/smark/multica/quant-loop/strategies/xs_momentum_rank_1d_20260709/strategy.py`
- `/home/smark/multica/quant-loop/strategies/xs_momentum_rank_1d_20260709/universe.py`
- `/home/smark/multica/quant-loop/strategies/xs_momentum_rank_1d_20260709/portfolio.py`
- `/home/smark/multica/quant-loop/strategies/xs_momentum_rank_1d_20260709/backtest.py`
- `/home/smark/multica/quant-loop/strategies/xs_momentum_rank_1d_20260709/data_loader.py`
- `/home/smark/multica/quant-loop/strategies/xs_momentum_rank_1d_20260709/run_backtest.py`
- `/home/smark/multica/quant-loop/strategies/xs_momentum_rank_1d_20260709/config.json`
- `/home/smark/multica/quant-loop/strategies/xs_momentum_rank_1d_20260709/tests/test_*.py` (4 files)
- `/home/smark/multica/quant-loop/strategies/xs_momentum_rank_1d_20260709/results/{summary,metrics,factor_exposure}.json`
