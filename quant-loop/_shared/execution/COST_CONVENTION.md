# Cost Convention — quant-loop strategies

This document is the binding rule for how execution cost is expressed in any
strategy that runs under `quant-loop/strategies/`. Violations are flagged by
`_shared/execution/check_inline_costs.py` (CI gate). The scanner is read-only;
this document is what authors read before they touch a strategy.

## 1. Ratified standard (USDT-M perp)

USDT-M perp backtests must derive cost from `_shared.execution.cost_model`
against the `BINANCE_FUTURES` venue:

```python
from _shared.execution.cost_model import apply_cost, BINANCE_FUTURES

cost_usd = apply_cost(
    notional,
    adv_usd,
    venue=BINANCE_FUTURES,
    side="taker",
)
```

This call resolves to **22 bps round trip** (4 bps taker fee + 7 bps pure
slippage, both per side, ×2 for entry+exit), independent of notional and ADV.
The values are imported from `backtest/factor_backtester.py` and ratified
under SMA-34900 / SMA-34913. The single source of truth is:

```
backtest/factor_backtester.py:53-57
  SMA34900_FEE_BPS_PER_SIDE         = 4.0
  SMA34900_PURE_SLIPPAGE_BPS_PER_SIDE = 7.0
```

Any other expression of the same number in a strategy file is a drift
source and will be flagged.

## 2. Forbidden literals

Do not inline any of the following in strategy code:

| Literal                  | Meaning                     | Where it appears today |
|--------------------------|-----------------------------|------------------------|
| `0.0004` / `0.0008` / `0.0011` / `0.0016` / `0.0022` / `0.0024` | 4/8/11/16/22/24 bps as fraction | `setcommission(commission=0.0004)`, `TAKER_FEE = 0.0004`, etc. |
| `fee_bps = 5.0`, `FEE_RT_BPS = 22.0`, `slippage_bps_per_side = 7` | explicit assignment to a fee/slippage-named variable | `fee_bps = float(cfg.get(..., 5.0))`, `FEE_BPS_PER_FILL = 4.0` |
| Hand-rolled `(fee + slip) / 10_000` rounding tricks | rebuilding cost from a pair of bps instead of calling `apply_cost` | `cost = 2.0 * (fee_bps + slip_bps) / 10_000.0` |

The scanner's `COST_LITERAL_RE` matches the fractional bps literals with
negative lookarounds so unrelated values stay out:

* `0.00225` (22.5 bps — extra digit beyond 22) → not flagged
* `1.0004` (inside a larger coefficient) → not flagged
* `fee_bps = SMA34900_FEE_BPS_PER_SIDE` (pointing at the canonical source) → not flagged (self-reference guard)

The scanner's `ASSIGN_RE` matches explicit assignments to cost-named
variables with word-boundary anchors so common variable names stay out:

* `fee_bps = 5.0` → flagged
* `FEE_RT_BPS = 22.0` → flagged
* `coffee_bps = 5` (no word boundary before `fee_bps` inside `coffee_bps`) → not flagged

Fee-shock sensitivity experiments (e.g. 60 bps to test robustness) are
allowed only when expressed as **a multiplier on the canonical constant**
with a comment naming the source:

```python
shocked_round_trip_bps = 2.5 * 22.0   # 2.5x fee shock; canonical: factor_backtester.py:53-57
```

Bare `FEE_BPS = 60` without the multiplier pattern and provenance comment
will be flagged.

## 3. Spot strategies

Spot strategies may use `BINANCE_SPOT` (the legacy size-dependent
sqrt-impact path) only if the strategy SPEC declares venue = spot.
The cost model warns explicitly about this path:

> "the sqrt-impact parameters are *not* ratified against any empirical fill
> study, so small notionals produce near-zero slippage and understate real
> cost. Do NOT use the spot path for USDT-M perp backtests — use
> BINANCE_FUTURES so the cost matches `factor_backtester.CostModel`."
>
> — `_shared/execution/cost_model.py:20-23`

If your strategy SPEC says venue = spot, the `BINANCE_SPOT` declaration
in `cost_model.py:83` is the source of truth; do not re-implement the
sqrt formula inline.

## 4. Generic runners (`cost_bps_rt=...` parameter)

Generic runners (e.g. `_shared/run_backtest.py`) take a `cost_bps_rt`
parameter. **Passing an explicit numeric value** (e.g. `cost_bps_rt=22.0`)
is a drift source — the runner's default must be 22.0 (T13) so callers
can simply omit the parameter. Explicit passes must come with a comment
naming the multiplier pattern from §2.

## 5. Whitelist

The scanner does NOT flag these paths (verified 2026-07-25):

| Path                                                                                   | Why exempt |
|----------------------------------------------------------------------------------------|------------|
| `strategies/_graveyard/**`                                                              | Frozen archive; historical drift is the point of the directory. |
| `strategies/**/framework_adapter_*.py`                                                  | Adapter convergence workflow; different review chain, never smark. |
| `strategies/**/test_*.py`, `strategies/**/*_test.py`, any path with `tests/` segment    | Expected values are legal; tests are not run as backtests. |
| `backtest/factor_backtester.py`                                                         | The canonical constants source itself. |
| `_shared/execution/cost_model.py`                                                       | The canonical `apply_cost` source itself. |
| `validation/generic_harness.py`, `validation/oos_harness.py`                           | Adapter/harness workflow territory. |

Adding a new exemption class requires sign-off from smark and a
follow-up issue keyed against this convention.

## 6. Running the scanner

```bash
cd quant-loop/
python3 _shared/execution/check_inline_costs.py --report    # always exits 0; lists every violation
python3 _shared/execution/check_inline_costs.py --enforce  # exits 1 if any violation, else 0
```

Default root is `quant-loop/strategies/`. Override with `--root DIR` for
narrower sweeps (e.g. during migration cleanup).

## 7. Migration ownership

The scanner is **read-only**. Flagged literals must be migrated to
`apply_cost(venue=BINANCE_FUTURES)` by the dedicated migration tasks
(T7 framework_adapter rewrite, T8 strategy runner alignment, T14
strategy-by-strategy cleanup). Do not let the scanner rewrite strategies
on its own — every migration touches funding/fee math and needs human
review.