# pyalgotrade framework-CV adapter

Status: added by SMA-35405 / framework hardening #38 (`pyalgotrade 0.20`).
The adapter plugs pyalgotrade's event-driven broker into the validation
generic-harness so pyalgotrade becomes a fifth framework leg alongside
backtrader, freqtrade, vectorbt (and the in-house native engine).

## Why

Framework CV exists to expose engine-assumption gaps — every framework
disagrees with the others in small, deterministic ways (next-bar fill
offset, commission model, fee granularity, re-investment cadence). pyalgotrade
contributes four divergences the existing four engines do not:

1. **Memory data model**: pyalgotrade ships an in-memory BarFeed and feeds
   it bar-by-bar to its dispatcher. Other engines either accept a vector
   (vectorbt) or feed a CSV (freqtrade via subprocess). This means the
   per-bar equity curve is strictly event-driven — no vectorised MTM
   shortcut.
2. **Order Action taxonomy**: pyalgotrade surfaces canonical BUY/SELL/
   SELL_SHORT/BUY_TO_COVER actions on every `Order`, so the covers/longs
   can be classified without position-state hacks. The other adapters
   (backtrader, vectorbt, freqtrade) flatten this to a single `pnl_pct`
   field on the trade dict.
3. **Integer-only share sizing**: pyalgotrade's `Broker` cannot accept
   fractional shares. For a `cash*0.01/price` sizer this is invisible, but
   for variants running larger weights the integer-truncation residual
   (≤ 1 USD/order at 5-digit crypto) becomes part of the divergence.
4. **Bar-Fixture handshake**: pyalgotrade cannot feed a DataFrame
   directly — bars must be constructed with `BasicBar(dt, o, h, l, c, v,
   adj, freq)` and pushed through `addBarsFromSequence(instrument, bars)`
   on a `membf.BarFeed` subclass. The adapter handles that conversion
   transparently.

## Contract

The adapter module lives at
[`validation/adapters/pyalgotrade_replay.py`](../validation/adapters/pyalgotrade_replay.py).
Public surface mirrors the other framework adapters:

```python
from validation.adapters import pyalgotrade_replay as pgr

res = pgr.run_pyalgotrade_replay(
    df,                  # ohlcv DataFrame (tz-aware UTC index)
    native_trades,       # [{"direction", "entry_date", "exit_date"}, ...]
    *,
    symbol="BTCUSDT",
    starting_cash=100_000.0,
    commission=0.0002,    # per-fill (matches backtrader semantics)
    weight=0.01,          # cash fraction per signal
    timeframe="1h",       # for Frequency enum selection
    share_lots=10000,     # integer-share rescale (see *Sizing* below)
)
# res.framework == "pyalgotrade"
# res.equity   is a tz-naive UTC pd.Series, one tick per input bar
# res.trade_pnls is list[float], one per closed round-trip
```

Missing-engine path: when `import pyalgotrade` raises `ImportError`,
calling the adapter raises `PyalgotradeReplayError` with a message that
includes the original error. `is_available()` and `import_error()` expose
the same gate so callers can branch without try/except ceremony.

## Sizing (integer-share rescale)

pyalgotrade's broker only accepts integer share counts. At the harness
default `weight=0.01` with cash=$100k and price=$40k BTC, the dollar
allocation rounds to **0** shares — the broker silently truncates and
the trade never fills. The adapter supports a `share_lots` parameter
that rescales the bar prices by `1/share_lots` so pyalgotrade sees
"shares that map to sub-units" while still operating with integers.

For BTC at $40k, `share_lots=10000` makes 1 "share" = $4 worth, so the
intended $1000 notional per signal = 250 shares = $1000 cost in the
broker's eyes. The realised pnl is renormalised back to real-dollar
terms in `onOrderUpdated`, so the rescale cancels out and per-trade
pnl is comparable to backtrader/vectorbt.

```python
# Without share_lots → 0 fills (integer truncation)
run_pyalgotrade_replay(df, trades, symbol="BTCUSDT", weight=0.01)
# res.trade_pnls == []

# With share_lots=10000 → fills, comparable pnl
run_pyalgotrade_replay(df, trades, symbol="BTCUSDT", weight=0.01,
                      share_lots=10000)
# res.trade_pnls == [-0.0026, 0.0501, ...]
```

`share_lots=1` (the default) is correct for adapters that produce
meaningful integer fills under typical harness weights — e.g. stocks at
$100/share with 1% sizing already give 10 shares. Set `share_lots >= 1`
when targeting crypto-USD pairs.

## Integration

* `validation/generic_harness.py`: `FRAMEWORKS = ("native", "backtrader",
  "freqtrade", "vectorbt", "pyalgotrade")`. The `_run_framework_leg`
  dispatcher has a new branch; the per-window loop now also runs
  pyalgotrade when it is in the requested framework set and silently
  records a `framework_skips` entry if pyalgotrade is missing.
* `validation/oos_harness.py`: same wiring for the legacy variant
  harness; CLI `--frameworks` help text updated.
* `validation/gates.py`: `evaluate_gates` gains an optional
  `window_pyalgotrade=None` kwarg. When non-empty, pyalgotrade contributes
  to **G5** (worst-of-framework-mean-OOS-Sharpe) just like the other legs.
* `validation/requirements.txt`: pyalgotrade is NOT pulled in by default
  (see *Install* below). The adapter is correct-by-default and skips
  cleanly when the engine is missing.

## Install (optional)

```bash
uv pip install pyalgotrade     # legacy 0.20, pure-Python
# or:
python -m pip install pyalgotrade
```

pyalgotrade 0.20 is the only supported version (older releases pre-date
the `BUY_TO_COVER` action; newer releases renamed the package to
`pyalgotrade_ng`).

## Cost model

`TradePercentage(commission)` is applied **per fill** (entry + exit = the
round trip). The generic harness already halves the configured
`cost_bps_rt` before passing `commission` to the adapter (matching
backtrader), so:

```
inhouse_round_trip    = 2 × (1bp fee + 1bp slip) = 4bp   ← cost_bps_rt default
backtrader / vectorbt commission           = 2bp           ← half rt
pyalgotrade       commission           = 2bp           ← per fill, mirrors backtrader
```

This makes pyalgotrade cost-equivalent to backtrader at the same
configured cost, which is what gate G5's "worst framework" check expects
when comparing Sharpe means across frameworks.

## Acceptance metrics

For any variant with N>=30 native trades over at least one OOS window,
the pyalgotrade leg should:

| Metric | Target | Why |
|---|---|---|
| `n_trades / native.n_trades` | 1.0 ± 0 | pyalgotrade re-executes the native schedule; no fills may be dropped or duplicated. |
| `|sharpe_pyalgotrade - sharpe_native| / |sharpe_native|` | < 50% | G5's acceptance bar. Larger divergence triggers W5 auto-archive. |
| `commission_residual_per_trade` | ≈ 4bp (-1bp vs native) | same as backtrader leg. |
| `equity_row_count` | exactly n_bars in input | one MTM tick per bar. |

When pyalgotrade is missing, `framework_skips["pyalgotrade"]` records the
exception and G5 evaluates on the remaining framework legs.

## Tests

`validation/test_pyalgotrade_replay.py` covers:

* availability / missing-engine error shape
* empty-trade, single-long, single-short, long+short combos
* zero-duration trade is dropped before submission
* missing required column raises clean error
* `FRAMEWORKS` tuple contains `"pyalgotrade"`
* `evaluate_gates` signature accepts `window_pyalgotrade`
* dispatch via the generic-harness `_run_framework_leg` runs end-to-end

Tests skip cleanly when pyalgotrade is not installed:

```bash
python -m pytest validation/test_pyalgotrade_replay.py -v
# 10 passed, 3 skipped  (the 3 skips are conditional on missing deps)
```

## Known limitations

* **Market orders only.** pyalgotrade supports limit / stop / stop-limit,
  but the native engine emits timing-only decisions; matching limit/stop
  semantics across engines is a separate workstream (and an explicit
  reason pyalgotrade's `BUY_TO_COVER` Action is surfaced — so future
  limit-order support can hang off the same hook).
* **Integer shares only.** pyalgotrade's `Broker` will not book a
  fractional-share market order; the `share_lots` parameter (default 1)
  rescales bar prices so integer fills correspond to sub-unit positions
  for crypto-USD pairs. The realised pnl is renormalised by the same
  factor and is comparable to backtrader/vectorbt outputs.
* **One-bar fill offset.** pyalgotrade matches orders at the next bar's
  open (the same convention backtrader uses) — this is the engine
  assumption difference framework CV exists to expose and is therefore
  *not* normalised away.
