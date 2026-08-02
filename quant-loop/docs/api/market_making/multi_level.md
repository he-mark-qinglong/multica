# `_shared.market_making.multi_level`

Source: `_shared/market_making/multi_level.py`

Multi-level quoting — post orders at multiple price tiers.

## class `MultiLevelParams(tiers: 'tuple[TierConfig, ...]' = (TierConfig(spread_multiplier=1.0, size_fraction=0.5), TierConfig(spread_multiplier=2.0, size_fraction=0.3), TierConfig(spread_multiplier=4.0, size_fraction=0.2))) -> None`

Multi-level quoting configuration.

## class `TierConfig(spread_multiplier: 'float', size_fraction: 'float') -> None`

Configuration for one quote tier.

## class `TierQuote(tier: 'int', bid_price: 'float', ask_price: 'float', bid_size: 'float', ask_size: 'float') -> None`

A quote at one tier level.

## `generate_multi_level_quotes(reservation_price: 'float', base_half_spread: 'float', total_size_usd: 'float', inventory_skew_offset: 'float', tick_size: 'float', params: 'MultiLevelParams' = MultiLevelParams(tiers=(TierConfig(spread_multiplier=1.0, size_fraction=0.5), TierConfig(spread_multiplier=2.0, size_fraction=0.3), TierConfig(spread_multiplier=4.0, size_fraction=0.2))), timestamp: 'pd.Timestamp | None' = None) -> 'list[TierQuote]'`

Generate a ladder of quotes at multiple price tiers.

| Parameter | Type | Default |
|---|---|---|
| `reservation_price` | 'float' | — |
| `base_half_spread` | 'float' | — |
| `total_size_usd` | 'float' | — |
| `inventory_skew_offset` | 'float' | — |
| `tick_size` | 'float' | — |
| `params` | 'MultiLevelParams' | MultiLevelParams(tiers=(TierConfig(spread_multiplier=1.0, size_fraction=0.5), TierConfig(spread_multiplier=2.0, size_fraction=0.3), TierConfig(spread_multiplier=4.0, size_fraction=0.2))) |
| `timestamp` | 'pd.Timestamp | None' | None |
