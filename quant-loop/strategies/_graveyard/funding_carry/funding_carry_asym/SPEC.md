# funding_carry_asym — funding > 0.03% at VPVR support = long

SMA-34793 prototype. Pure signal-coding task; no parameter sweep
expected here (the optimizer pass owns that).

## Goal

Combine a **funding-rate asymmetry condition** (carries > 3 bps per
8h event on the long side, i.e. `funding > 0.0003`) with a **VPVR
support level** (an HVN absorption zone that the price is at or
within an ATR-multiple distance of) into a binary long/flat signal.

The shape of the signal is the prototype: every bar is either
**+1** (long) or **0** (flat). No short side, no exits — those are
the responsibility of the strategy-level backtest harness.

## Data

| field | source | path |
|---|---|---|
| OHLCV 4h BTC | binance_usdm | `live_data/BTCUSDT_4h.parquet` (9912 bars) |
| Funding 8h   | Binance USDT-M perpetual fetcher (SMA-34789) | `data/funding/BTCUSDT.parquet` (5100 events) |
| VPVR levels  | VPVR level detector (SMA-34790) | produced by `_indicators/vpvr_levels.compute_vpvr_levels` |

Funding is reindexed onto the 4h bar index with `ffill` (paid 3×/day,
so within an 8h funding interval the most recent paid funding rate
applies). The funding column at bar `t` is the funding rate paid at
the most recent funding event strictly before bar `t`'s open time.

## Inputs

```python
def compute_signal(
    close: pd.Series,                  # per-bar close, indexed by ts
    funding: pd.Series,                # same index as `close`; funding per 8h event
    levels: List[VpvrLevel],           # SMA-34790 output, computed on a prior window only
    *,
    funding_threshold: float = 0.0003, # 0.03% per 8h event = 3 bps
    support_kind: str = "HVN",         # "HVN" or "LVN"; default = HVN (absorption support)
    proximity_atr: float = 1.0,        # max distance in ATR multiples
    atr: Optional[pd.Series] = None,   # pre-computed ATR; if None, computed from `close`
    atr_period: int = 14,              # used only when `atr` is None
) -> pd.DataFrame:
    ...
```

When the caller supplies `levels` that were computed on the entire
window `0..t` the strategy layer is responsible for shifting those
levels (`shift(1)` on the snapshot) so the level used at bar `t`
reflects data strictly before `t`. The `build_signals` wrapper
enforces this for callers that hand it raw OHLCV.

## Entry condition (long, per bar)

```
long_signal[t] = 1  iff
    funding[t] > funding_threshold
    and there exists at least one level in `levels`
         whose kind == support_kind
         and |close[t] - level.price_center| <= proximity_atr * atr[t]
otherwise 0
```

`price_center` is used instead of `[price_low, price_high]` for
distance computation because the asymmetry test from the spec
("price is at/within a configured distance of a VPVR support level")
is naturally a point-distance test against the node center; callers
that want a band test can pass `price_low`/`price_high` via VpvrLevel
overrides. The 1.0 ATR default is consistent with the prototype
`vpvr_funding_asym_4h_20260713` `poc_atr_buffer`.

## Outputs

The function returns a DataFrame indexed by `close.index` with:

| column | dtype | meaning |
|---|---|---|
| `signal` | int (-1/0/+1) | +1 = long, 0 = flat |
| `funding` | float | the funding rate used at the bar (post-ffill, post-shift) |
| `funding_above_threshold` | bool | raw funding gate |
| `support_level_price` | float | center of the matching support level, NaN if no match |
| `support_level_kind` | str | "HVN" or "LVN" or "" |
| `support_distance_atr` | float | distance to the nearest matching level, in ATR multiples |
| `near_support` | bool | price within `proximity_atr` of any support level |
| `atr` | float | the ATR used |

## No-look-ahead

`compute_signal` itself is pure: it reads `close[t]`, `funding[t]`,
`levels` (a single set computed externally on a prior window), and
emits a per-bar signal. The `build_signals` wrapper that ships
alongside is the place where the no-look-ahead enforcement lives:

1. `atr` is computed as the rolling-mean TR with `close.shift(1)` so
   today's range cannot leak into today's ATR (cycle-46 convention
   from `vpvr_funding_asym_4h_20260713` and others).
2. VPVR levels are computed on a per-bar rolling window of `window`
   bars **strictly preceding** bar `t` (the rolling VPVR includes
   bar `t`'s own contribution to volume at the level it produces,
   so when we want the level **used** at bar `t` we recompute with
   the window ending at bar `t - 1` and `shift(1)`).
3. Funding at bar `t` is the rate paid at the most recent funding
   event strictly before bar `t`'s index — i.e. the funding series
   is `ffill` of the funding events shifted by one.

## Universe

This prototype is BTC-only (`BTCUSDT`) on the 4h timeframe. ETH/SOL
follow the same pattern but the carry threshold's economic meaning
differs (ETH p99 funding ≈ 4.3 bps vs BTC 3.9 bps — cycle-46 stats),
so multi-symbol expansion is out of scope for the prototype.

## Verdict

This is a prototype. It does **not** declare profitability; the
backtest harness owns that decision. The done criteria from
SMA-34793 are:

  - [ ] Function compiles and runs.
  - [ ] Unit-tested with at minimum: funding-just-above-threshold,
        funding-just-below-threshold, price-at-VPVR-support,
        price-far-from-support.
  - [ ] Minimal end-to-end smoke backtest runs on the last 30 days
        of BTCUSDT 1m / 15m / 4h (just to prove the wiring, no
        tuning).
