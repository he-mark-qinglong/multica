# regime — BTC Regime Gate (shared)

Canonical classifier for "what regime are we in". Consolidates the ad-hoc
regime logic now sprinkled across 5+ strategies (`vpvr_regime_blend_4h`,
`trend_regime_gate_1d_adx_4h_1h`, `multi_pair_basket_regime_filter_4h`,
`vpvr_funding_regime_15m`, `vpvr_reversion_4h_vol_regime`) so every strategy
reads from the same labels.

## Three orthogonal dimensions
- **trend** (`TrendRegime`): `bull` / `bear` / `range` — EMA cross, ADX-gated
- **vol** (`VolRegime`): `calm` / `normal` / `volatile` — ATR percentile (100-bar)
- **funding** (`FundingRegime`): `neutral` / `long_favor` / `short_favor` / `extreme`

## Usage
```python
from _shared.regime.btc_gate import regime_snapshot, regime_series
snap = regime_snapshot(ohlcv_4h, funding_8h=funding_series)
if snap.trend != "range" and snap.vol != "volatile":
    ...  # gate entries: only trade trending, non-volatile regimes
rs = regime_series(ohlcv_4h, funding_8h=funding_series)  # every bar, for backtests
mask = (rs["trend"] != "range") & (rs["vol"] != "volatile")
```

## Status
Opt-in library. **No existing strategy is modified** — each adopts when ready.
Tests: `python3 _shared/regime/test_btc_gate.py` (20/20).
