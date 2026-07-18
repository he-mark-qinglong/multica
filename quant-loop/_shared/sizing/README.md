# vol_target — Volatility-Targeted Position Sizing

Scales per-bar position size by inverse realized vol so a strategy targets a
constant ~15% annualized vol (configurable). Calm regimes → size up (capped at
3x base), volatile regimes → size down (floored at 0.1x base).

## Why
- **Vol-targeting is a different axis from sizing magnitude.** U9 (SMA-34955)
  showed that scaling overall risk (e.g. 0.5% → 1%) cannot lift a PF<1.5 wall.
  Vol-targeting is *regime-adaptive*: it normalizes risk across regimes rather
  than uniformly amplifying it. Net effect is usually Sharpe-neutral-to-positive
  and drawdown reduction.
- Replaces the fixed `risk_target_pct = 0.005` pattern used in 17 sites.

## Usage (1-line on any existing equity curve)
```python
from _shared.sizing.vol_target import apply_vol_target
equity_vt = apply_vol_target(equity)   # equity is a pd.Series from backtest
```
Lower-level: `vol_target_weights(returns)` → per-bar multiplier in [floor, cap].

## References
- Moreira & Muir (2017), "Volatility-Managed Portfolios", *Journal of Finance*
- Harvey, Hoyle, Rattray, Sargaison, Taylor, Van Hemert (2019), Man-AHL
  "The Best of Strategies for the Worst of Times: Portfolio Protections"

**Note:** opt-in library. No auto-wiring into strategies — each strategy chooses
when to adopt by calling `apply_vol_target` on its equity curve.
