# `_shared.indicators.vpvr`

Source: `_shared/indicators/vpvr.py`

Volume Profile Visible Range (VPVR) level detector.

## class `VolumeProfile(price_centers: 'np.ndarray', volume: 'np.ndarray', bin_width: 'float', poc_price: 'float', vah_price: 'float', val_price: 'float', hvn_zones: 'List[Tuple[float, float, float]]', lvn_zones: 'List[Tuple[float, float, float]]', total_volume: 'float', value_area_fraction: 'float') -> None`

A volume profile result.

## `build_volume_profile(high: 'pd.Series', low: 'pd.Series', volume: 'pd.Series', num_bins: 'int' = 200) -> 'Tuple[np.ndarray, np.ndarray, float]'`

Compute the volume profile by distributing each bar's volume uniformly across the price bins it spans.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `volume` | 'pd.Series' | — |
| `num_bins` | 'int' | 200 |

## `compute_vpvr_levels(high: 'pd.Series', low: 'pd.Series', volume: 'pd.Series', num_bins: 'int' = 200, value_area_fraction: 'float' = 0.7, hvn_quantile: 'float' = 0.85, lvn_quantile: 'float' = 0.15, num_hvn: 'int' = 5, num_lvn: 'int' = 5) -> 'VolumeProfile'`

Run the full pipeline and return a :class:`VolumeProfile`.

| Parameter | Type | Default |
|---|---|---|
| `high` | 'pd.Series' | — |
| `low` | 'pd.Series' | — |
| `volume` | 'pd.Series' | — |
| `num_bins` | 'int' | 200 |
| `value_area_fraction` | 'float' | 0.7 |
| `hvn_quantile` | 'float' | 0.85 |
| `lvn_quantile` | 'float' | 0.15 |
| `num_hvn` | 'int' | 5 |
| `num_lvn` | 'int' | 5 |

## `find_hvn_lvn(price_centers: 'np.ndarray', profile: 'np.ndarray', bin_width: 'float', hvn_quantile: 'float' = 0.85, lvn_quantile: 'float' = 0.15, num_hvn: 'int' = 5, num_lvn: 'int' = 5) -> 'Tuple[List[Tuple[float, float, float]], List[Tuple[float, float, float]]]'`

Identify High and Low Volume Nodes.

| Parameter | Type | Default |
|---|---|---|
| `price_centers` | 'np.ndarray' | — |
| `profile` | 'np.ndarray' | — |
| `bin_width` | 'float' | — |
| `hvn_quantile` | 'float' | 0.85 |
| `lvn_quantile` | 'float' | 0.15 |
| `num_hvn` | 'int' | 5 |
| `num_lvn` | 'int' | 5 |

## `find_poc(price_centers: 'np.ndarray', profile: 'np.ndarray') -> 'float'`

Point of Control = bin center with the highest volume.

| Parameter | Type | Default |
|---|---|---|
| `price_centers` | 'np.ndarray' | — |
| `profile` | 'np.ndarray' | — |

## `find_value_area(price_centers: 'np.ndarray', profile: 'np.ndarray', bin_width: 'float', poc_index: 'int', value_area_fraction: 'float' = 0.7) -> 'Tuple[float, float]'`

Value Area High / Low.

| Parameter | Type | Default |
|---|---|---|
| `price_centers` | 'np.ndarray' | — |
| `profile` | 'np.ndarray' | — |
| `bin_width` | 'float' | — |
| `poc_index` | 'int' | — |
| `value_area_fraction` | 'float' | 0.7 |
