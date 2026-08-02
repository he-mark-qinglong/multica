# `_shared.sizing.liquidity`

Source: `_shared/sizing/liquidity.py`

Multi-Cap Liquidity Sizing (MCLS) — per SPEC liquidity_sizing_v1_20260726/SPEC.md.

## class `LiquiditySnapshot(timestamp: 'pd.Timestamp', adv_24h_usd: 'float', depth_top5_usd: 'float', depth_age_seconds: 'float', vol_1h_usd: 'float', vpin: 'float', expected_edge_bp: 'float') -> None`

Per-bar liquidity inputs (SPEC §5). All USD denominated.

## class `MCLS(params: 'Optional[MCLSParams]' = None)`

Multi-Cap Liquidity Sizing — intersection of 5 caps + compose with vol_target (SPEC §5 public API).

### `cap_breakdown(self, snap: 'LiquiditySnapshot', base_size_usd: 'float') -> 'dict'`

Diagnostic: return each cap value individually.

| Parameter | Type | Default |
|---|---|---|
| `snap` | 'LiquiditySnapshot' | — |
| `base_size_usd` | 'float' | — |

### `size_multiplier(self, snap: 'LiquiditySnapshot', base_size_usd: 'float', vol_target_weight: 'float' = 1.0) -> 'float'`

Per-bar multiplier in [floor, cap]; 0.0 if every active liquidity cap is below ``k_floor`` (V5 SPEC §6 kill-switch handoff).

| Parameter | Type | Default |
|---|---|---|
| `snap` | 'LiquiditySnapshot' | — |
| `base_size_usd` | 'float' | — |
| `vol_target_weight` | 'float' | 1.0 |

## class `MCLSParams(k_adv: 'float' = 0.02, k_depth: 'float' = 0.1, k_part: 'float' = 0.05, k_impact: 'float' = 0.5, k_vpin: 'float' = 0.6, floor: 'float' = 0.0, cap: 'float' = 1.5, k_floor: 'float' = 0.05, l2_stale_seconds: 'float' = 60.0, impact_alpha_bp: 'float' = 10.0, cap_base: 'float' = 1.0) -> None`

MCLS tuning constants. Defaults calibrated to the ratified SMA-34900/SMA-34913 22bp RT futures cost-cap on BTC/ETH/SOL (SPEC §3.1).
