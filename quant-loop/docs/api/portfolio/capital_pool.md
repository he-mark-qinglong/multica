# `_shared.portfolio.capital_pool`

Source: `_shared/portfolio/capital_pool.py`

Inter-strategy shared capital pool (I11).

## class `CapitalPool(target_weights: 'Mapping[str, float]', equities: 'Mapping[str, float] | None' = None, config: 'PoolConfig' = PoolConfig(drift_threshold=0.05))`

Stateful shared pool: equities, target weights, audit ledger.

### `add_strategy(self, name: 'str', target_weight: 'float', initial_equity: 'float' = 0.0, rescale: 'bool' = True) -> 'tuple[TransferRecord, ...]'`

Add a strategy to the pool.

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `target_weight` | 'float' | — |
| `initial_equity` | 'float' | 0.0 |
| `rescale` | 'bool' | True |

### `apply_pnl(self, name: 'str', pnl: 'float') -> 'None'`

Credit/debit a strategy's equity (no transfer, no ledger entry).

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |
| `pnl` | 'float' | — |

### `drift(self, name: 'str') -> 'float'`

(actual - target) / pool equity for one strategy.

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |

### `max_abs_drift(self) -> 'float'`

### `rebalance(self, force: 'bool' = False) -> 'tuple[TransferRecord, ...]'`

Transfer capital back to targets if drift exceeds the threshold.

| Parameter | Type | Default |
|---|---|---|
| `force` | 'bool' | False |

### `remove_strategy(self, name: 'str') -> 'tuple[TransferRecord, ...]'`

Remove a strategy; its equity leaves the pool via EXTERNAL.

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |

### `target_equity(self, name: 'str') -> 'float'`

| Parameter | Type | Default |
|---|---|---|
| `name` | 'str' | — |

## class `PoolConfig(drift_threshold: 'float' = 0.05) -> None`

Pool-level configuration.

## class `Transfer(src: 'str', dst: 'str', amount: 'float') -> None`

One planned capital movement between two strategies.

## class `TransferRecord(ts: 'float', src: 'str', dst: 'str', amount: 'float', reason: 'str', pool_equity_after: 'float') -> None`

Audit-ledger entry for an applied transfer (or join/leave flow).

## `compute_transfers(equities: 'Mapping[str, float]', target_weights: 'Mapping[str, float]') -> 'tuple[Transfer, ...]'`

Minimal transfers restoring every strategy to its target equity.

| Parameter | Type | Default |
|---|---|---|
| `equities` | 'Mapping[str, float]' | — |
| `target_weights` | 'Mapping[str, float]' | — |
