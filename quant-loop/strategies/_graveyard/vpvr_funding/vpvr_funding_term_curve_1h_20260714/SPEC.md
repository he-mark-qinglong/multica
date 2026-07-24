# V1_funding_term_curve — VPVR + funding-term curve steepness (iter#97)

**Campaign:** SMA-33616 (2026-07-14)
**Branch:** feat/vpvr-variant-sweep-iter97+-20260714
**Iteration:** 97
**Universe:** BTCUSDT + ETHUSDT, 1h TF
**Axis:** funding-term curve steepness z-spread + VPVR POC directional filter

## Signal

```
funding_1h(t)       = current 1h funding (8h ffill to 1h bar frequency)
funding_8h_roll(t)  = rolling 8-bar (1h×8 = 8h) mean of funding_1h
funding_8h_std(t)   = rolling 8-bar std of funding_1h

z_spread(t) = (funding_1h(t) - funding_8h_roll(t)) / funding_8h_std(t)
```

## Entry

| z_spread | POC condition | Direction |
|---|---|---|
| z_spread ≥ +2.0 | close > POC | SHORT (longs paying extreme) |
| z_spread ≤ -2.0 | close < POC | LONG (shorts paying extreme) |

POC computed via rolling VPVR (window 120 bars, 24 bins), shifted 1 bar
to prevent look-ahead.

## Exit

- ATR trailing stop k = 2.5 (ratchets only on favorable side)
- Time-stop = 24 bars (1h × 24 = 24h = 1 trading day)
- Hard stop = 4 × ATR

## Funding carry

While in position, funding carry is debited per bar:
- long pays funding if rate > 0, receives if < 0
- short receives funding if rate > 0, pays if < 0

## Cycle-46 lessons applied

1. **Use funding-term curve, not raw delta** — z-spread over 8h smooths
   out single-bar noise that ruined iter#90-93 results.
2. **VPVR as directional filter only**, not TP/SL modifier — preserves
   the carry signal without asymmetric cost trap.
3. **Time-stop 24h** — funding unwind typically resolves within 1 day;
   longer holds lose to adverse selection.

## Tag rule

- Sharpe ≥ 1.0 → [PROFITABLE]
- Sharpe < 1.0 → [NOT-PROFITABLE]

## Public API

`from strategy import VARIANT_KEY, run_backtest`
