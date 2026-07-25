# Quant Strategy Module Inventory

**Generated:** 2026-07-25  
**Strategies scanned:** 38  
**Total Python modules:** 362  
**Root:** `/Users/mark/multica/quant-loop/strategies`  

## Overview

This inventory is the result of a recursive scan of `quant-loop/strategies/`,
including active strategies and the `_graveyard`. Each non-test Python file was
classified by filename and content heuristics into one of the following buckets:

| Category | Count | Description |
|----------|-------|-------------|
| entry | 68 | Strategy entry/run scripts |
| signal_generation | 26 | Indicators, signals, transforms |
| risk_sizing | 2 | Position sizing and risk controls |
| execution_cost | 1 | Cost and execution models |
| evaluation_metrics | 2 | Performance and evaluation metrics |
| data_utils | 171 | Data loading, universe, calendar, orchestration |
| anti_pattern | 92 | Code that should NOT be copied |

## Reusable Modules by Category

The following modules are candidates for migration into `quant-loop/_shared/`.
Migration priority is roughly: `risk_sizing` / `execution_cost` / `evaluation_metrics`
(high), `signal_generation` and `data_utils` (medium), `entry` (low/templates only).

### signal_generation

| Strategy | File | LOC | Reusable? | Description |
|----------|------|-----|-----------|-------------|
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vol_breakout_1m_15m_vpvr_confluence_u6_20260718/indicators.py` | 197 | yes | functions: _cfg, sqrt_bars_per_year, realized_vol, vol_median, vol_regime |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/build_signals.py` | 255 | yes | functions: _atr, _kama, _vpvr_poc, _kama_turn, build_signals |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/build_signals.py` | 259 | yes | functions: _atr, _vpvr_poc, _vpvr_value_area, _vol_ratio, _recent_failed_break |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/build_signals.py` | 107 | yes | functions: _atr, _vpvr_poc, _z_score, build_signals |
| _indicators | `_indicators/iter94_20260714.py` | 410 | yes | functions: _true_range, _wilder_smooth, _wilder_atr, adx, realized_vol_bps |
| _indicators | `_indicators/mtf_xs_pairs_base_20260718.py` | 855 | yes | classes: Trade; functions: aggregate_ohlcv, align_lower_to_upper, wilder_atr, pair_zscore, zscore_slope |
| _indicators | `_indicators/mtf_xs_runner_20260718.py` | 368 | yes | classes: PairMetrics; functions: _summarise_pair, _portfolio_metrics, _dummy_index, write_metrics, _window_split_bounds |
| _indicators | `_indicators/vpvr_levels.py` | 47 | yes | module-level code |
| funding_carry | `_graveyard/funding_carry/funding_carry_asym/build_signals.py` | 565 | yes | functions: _atr_from_close, _atr_ohlcv, _resolve_levels_at_bar, compute_signal, _vpvr_snapshot_levels |
| funding_carry | `_graveyard/funding_carry/funding_carry_asym/vpvr_hvn_persistence.py` | 454 | yes | classes: RespectResult; functions: load_4h_ohlcv, generate_hvns, _atr_at, score_zone_respect, run_for_symbol |
| impl_vpvr_multi_tf_funding | `impl_vpvr_multi_tf_funding/build_signals.py` | 498 | yes | functions: _atr, _vpvr_snapshot_levels, _shifted_snapshot_per_bar, build_signals_1m, build_signals_15m |
| impl_vpvr_multi_tf_funding | `impl_vpvr_multi_tf_funding/combine_signals.py` | 447 | yes | functions: _is_allowed, _align_to_1m, _vote_per_tf, _resolve_conflicts_and_cascade, _count_agree |
| loid_vpvr_confluence_20260717 | `loid_vpvr_confluence_20260717/build_signals.py` | 220 | yes | functions: _atr, _vpvr_snapshot_levels, build_signals |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/build_signals.py` | 123 | yes | functions: _atr, _vpvr_poc, _macro_proximity_bars, build_signals |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/build_signals.py` | 112 | yes | functions: _atr, _vpvr_poc, _z_score, build_signals |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/build_signals.py` | 100 | yes | functions: _atr, _vpvr_poc, build_signals |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/build_signals.py` | 138 | yes | functions: _atr, _vpvr_poc, build_signals |
| pairs_cointegration_1d_20260709 | `pairs_cointegration_1d_20260709/cointegration.py` | 382 | yes | classes: HedgeRatio, EGTestResult; functions: _to_1d_float, _align, ols_hedge_ratio, compute_spread, engle_granger_test |
| vol_breakout_2tf_vpvr_confluence_4h_20260712 | `vol_breakout_2tf_vpvr_confluence_4h_20260712/indicators.py` | 274 | yes | functions: load_config, realized_vol, vol_median, vol_regime, true_range |
| vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720 | `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/build_signals.py` | 535 | yes | functions: _atr, _ema, _vpvr_snapshot_levels, _shifted_snapshot_per_bar, _zscore |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/build_signals.py` | 153 | yes | functions: _atr, _align_to_1m, build_signals |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/funding_signal.py` | 123 | yes | functions: compute_funding_ema_signal |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/state_machine.py` | 337 | yes | classes: Trade; functions: _state_machine, run_backtest, compute_metrics |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/trend_filter.py` | 53 | yes | functions: build_trend_filter |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/vpvr_levels_band.py` | 145 | yes | functions: _vpvr_snapshot_band, build_vpvr_band |
| vpvr_tod_session_filter_15m_20260715 | `vpvr_tod_session_filter_15m_20260715/build_signals.py` | 89 | yes | functions: _atr, _vpvr_poc, build_signals |

### risk_sizing

| Strategy | File | LOC | Reusable? | Description |
|----------|------|-----|-----------|-------------|
| mtf_xs_pairs_1m_15m_2h_h3_20260718 | `mtf_xs_pairs_1m_15m_2h_h3_20260718/sizing_sweep.py` | 580 | yes | classes: VariantSpec; functions: sizing_baseline_atr, sizing_atr_multiplier, sizing_vol_target, sizing_regime_conditional, kelly_size |
| paper_trading | `_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/kill_criteria.py` | 116 | yes | classes: KillState, MetricsSnapshot; functions: evaluate |

### execution_cost

| Strategy | File | LOC | Reusable? | Description |
|----------|------|-----|-----------|-------------|
| paper_trading | `_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/fill_engine.py` | 687 | yes | classes: KlineBar, PaperAccount; functions: _parse_kline_msg, _ws_url, _load_strategy_history, _load_strategy_funding, _run_strategy_for_diff |

### evaluation_metrics

| Strategy | File | LOC | Reusable? | Description |
|----------|------|-----|-----------|-------------|
| pairs_cointegration_1d_20260709 | `pairs_cointegration_1d_20260709/optimize.py` | 409 | yes | functions: _sanitize, make_cfg, select_pairs_filtered, build_returns_series, evaluate_combo |
| vol_breakout_vpvr_val_fade_1h_5m_20260714 | `vol_breakout_vpvr_val_fade_1h_5m_20260714/scripts/b6_fwer.py` | 120 | yes | functions: load_returns, block_bootstrap, sharpe_like, one_sided_p_value, main |

### data_utils

| Strategy | File | LOC | Reusable? | Description |
|----------|------|-----|-----------|-------------|
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707/data_loader.py` | 146 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, load_symbol_1m, load_all |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707/run_backtest.py` | 220 | maybe | functions: _trade_rows, _bars_per_year, _strategy_metrics, main |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_049/data_loader.py` | 146 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, load_symbol_1m, load_all |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_049/run_backtest.py` | 220 | maybe | functions: _trade_rows, _bars_per_year, _strategy_metrics, main |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_049/run_cpcv.py` | 249 | yes | functions: _sanitize, _equity_returns, _purge, _embargo, _run_cpcv |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_050/data_loader.py` | 146 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, load_symbol_1m, load_all |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_050/run_backtest.py` | 220 | maybe | functions: _trade_rows, _bars_per_year, _strategy_metrics, main |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_050/run_cpcv.py` | 249 | yes | functions: _sanitize, _equity_returns, _purge, _embargo, _run_cpcv |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_099/data_loader.py` | 146 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, load_symbol_1m, load_all |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_099/run_backtest.py` | 220 | maybe | functions: _trade_rows, _bars_per_year, _strategy_metrics, main |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_099/run_cpcv.py` | 249 | yes | functions: _sanitize, _equity_returns, _purge, _embargo, _run_cpcv |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_100/data_loader.py` | 146 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, load_symbol_1m, load_all |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_100/run_backtest.py` | 220 | maybe | functions: _trade_rows, _bars_per_year, _strategy_metrics, main |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_100/run_cpcv.py` | 209 | yes | functions: _build_per_bar_returns, _sanitize, main |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vol_breakout_1m_15m_vpvr_confluence_u6_20260718/data_loader.py` | 121 | yes | functions: _normalize_ohlcv, _cache_path, load_symbol, _read_cfg, load_symbols |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vol_breakout_1m_15m_vpvr_confluence_u6_20260718/run_backtest.py` | 474 | maybe | functions: _daily_resampled_sharpe, _bootstrap_sharpe_ci, _envelope, _diagnostics, _write_equity_csv |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/data_loader.py` | 196 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, _resample, _to_pandas_freq |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/run_backtest.py` | 132 | maybe | functions: _trade_rows, main |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/data_loader.py` | 153 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, load_symbol, load_all |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/run_backtest.py` | 98 | maybe | functions: _trade_rows, main |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/data_loader.py` | 137 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, load_symbol, load_all |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/run_backtest.py` | 251 | maybe | functions: load_data, compute_metrics, _empty_metrics, write_trades_csv, write_equity_curve |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/run_backtest.py` | 112 | maybe | functions: _make_synthetic_data, _compute_metrics, main |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/data_loader.py` | 74 | yes | functions: _load_1m, _resample_5m, load_all |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/run_backtest.py` | 136 | maybe | functions: _summarise, _write_trades, _write_equity, main |
| _oos_rank_20260718 | `_oos_rank_20260718/oos_walk_forward.py` | 341 | yes | functions: bars_per_year, load_equity, oos_walk_forward, gates, main |
| donchian_breakout_atr_1d_20260709 | `donchian_breakout_atr_1d_20260709/data_loader.py` | 206 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, _resample_1d, load_symbol_1d |
| donchian_breakout_atr_1d_20260709 | `donchian_breakout_atr_1d_20260709/run_backtest.py` | 99 | maybe | functions: _trade_rows, main |
| funding_carry | `_graveyard/funding_carry/funding_carry/data_loader.py` | 149 | yes | functions: _load_ohlcv_1m, _load_funding, _merge_funding, load_symbol_1m |
| funding_carry | `_graveyard/funding_carry/funding_carry/sma34946/data_loader.py` | 110 | yes | functions: _load_ohlcv_1m, _load_funding, _merge_funding, load_symbol_1m |
| funding_carry | `_graveyard/funding_carry/funding_carry_asym/data_loader.py` | 132 | yes | functions: _load_ohlcv, _load_funding, load_symbol, load_all |
| funding_carry | `_graveyard/funding_carry/funding_carry_asym/run_backtest.py` | 366 | maybe | functions: _load_tf, _daily_resampled_sharpe, _compute_metrics, _tf_to_freq, _write_equity_csv |
| funding_carry | `_graveyard/funding_carry/funding_oscillator_mr/data_loader.py` | 129 | yes | functions: _load_ohlcv_1m, _load_funding, _merge_funding, load_symbol_1m |
| funding_carry | `_graveyard/funding_carry/sma_34925_btc_funding_delta/sensitivity_sweep.py` | 81 | yes | functions: main |
| impl_vpvr_multi_tf_funding | `impl_vpvr_multi_tf_funding/data_loader.py` | 155 | yes | functions: _load_ohlcv, _load_funding, _attach_funding, load_tf, load_all |
| impl_vpvr_multi_tf_funding | `impl_vpvr_multi_tf_funding/run_backtest.py` | 535 | maybe | functions: _daily_resampled_sharpe, _compute_metrics, _slice_window, _tf_freq, _run_fold |
| loid_vpvr_confluence_20260717 | `loid_vpvr_confluence_20260717/run_backtest.py` | 349 | maybe | functions: _tf_params, _load_tf, _daily_resampled_sharpe, _compute_metrics, _tf_to_freq |
| momentum_trend_btc_only_softer_stop_1h_20260712 | `momentum_trend_btc_only_softer_stop_1h_20260712/data_loader.py` | 210 | yes | classes: SourceManifest; functions: _sha256, _source_filename, _normalize_ohlcv, _read_source, build_source_manifest |
| momentum_trend_btc_only_softer_stop_1h_20260712 | `momentum_trend_btc_only_softer_stop_1h_20260712/run_backtest.py` | 172 | maybe | functions: _trade_rows, main |
| momentum_trend_btc_only_softer_stop_1h_20260712 | `momentum_trend_btc_only_softer_stop_1h_20260712/walk_forward.py` | 429 | yes | classes: WindowSlice; functions: build_schedule, slice_1h_4h, _per_symbol_equity_curve, _metrics, _run_slice |
| momentum_trend_multi_tf_atr_scaled_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_1h_20260712/data_loader.py` | 210 | yes | classes: SourceManifest; functions: _sha256, _source_filename, _normalize_ohlcv, _read_source, build_source_manifest |
| momentum_trend_multi_tf_atr_scaled_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_1h_20260712/run_backtest.py` | 172 | maybe | functions: _trade_rows, main |
| momentum_trend_multi_tf_atr_scaled_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_1h_20260712/walk_forward.py` | 429 | yes | classes: WindowSlice; functions: build_schedule, slice_1h_4h, _per_symbol_equity_curve, _metrics, _run_slice |
| momentum_trend_multi_tf_atr_scaled_v2_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_v2_1h_20260712/data_loader.py` | 210 | yes | classes: SourceManifest; functions: _sha256, _source_filename, _normalize_ohlcv, _read_source, build_source_manifest |
| momentum_trend_multi_tf_atr_scaled_v2_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_v2_1h_20260712/run_backtest.py` | 172 | maybe | functions: _trade_rows, main |
| momentum_trend_multi_tf_atr_scaled_v2_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_v2_1h_20260712/walk_forward.py` | 429 | yes | classes: WindowSlice; functions: build_schedule, slice_1h_4h, _per_symbol_equity_curve, _metrics, _run_slice |
| momentum_trend_multi_tf_atr_scaled_v3_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712/data_loader.py` | 210 | yes | classes: SourceManifest; functions: _sha256, _source_filename, _normalize_ohlcv, _read_source, build_source_manifest |
| momentum_trend_multi_tf_atr_scaled_v3_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712/run_backtest.py` | 172 | maybe | functions: _trade_rows, main |
| momentum_trend_multi_tf_atr_scaled_v3_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712/walk_forward.py` | 429 | yes | classes: WindowSlice; functions: build_schedule, slice_1h_4h, _per_symbol_equity_curve, _metrics, _run_slice |
| mtf_vpvr_edge_zscore_1m_15m_2h_20260718 | `mtf_vpvr_edge_zscore_1m_15m_2h_20260718/data_loader.py` | 43 | yes | functions: _load_1m, load_all, load_funding |
| mtf_vpvr_edge_zscore_1m_15m_2h_20260718 | `mtf_vpvr_edge_zscore_1m_15m_2h_20260718/run_backtest.py` | 128 | maybe | functions: main |
| mtf_xs_pairs_1m_15m_2h_h1_20260718 | `mtf_xs_pairs_1m_15m_2h_h1_20260718/data_loader.py` | 35 | yes | functions: _load_1m, load_all, load_funding |
| mtf_xs_pairs_1m_15m_2h_h1_20260718 | `mtf_xs_pairs_1m_15m_2h_h1_20260718/run_backtest.py` | 139 | maybe | functions: _write_trades_csv, _write_equity_csv, main |
| mtf_xs_pairs_1m_15m_2h_h1_20260718 | `mtf_xs_pairs_1m_15m_2h_h1_20260718/walk_forward.py` | 52 | yes | functions: main |
| mtf_xs_pairs_1m_15m_2h_h2_20260718 | `mtf_xs_pairs_1m_15m_2h_h2_20260718/data_loader.py` | 28 | yes | functions: _load_1m, load_all, load_funding |
| mtf_xs_pairs_1m_15m_2h_h2_20260718 | `mtf_xs_pairs_1m_15m_2h_h2_20260718/run_backtest.py` | 49 | maybe | functions: main |
| mtf_xs_pairs_1m_15m_2h_h2_20260718 | `mtf_xs_pairs_1m_15m_2h_h2_20260718/walk_forward.py` | 48 | yes | functions: main |
| mtf_xs_pairs_1m_15m_2h_h3_20260718 | `mtf_xs_pairs_1m_15m_2h_h3_20260718/data_loader.py` | 49 | yes | functions: _load_1m, _load_funding_one, load_all, load_funding |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/data_loader.py` | 35 | yes | functions: load_btcusdt_4h |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/macro_calendar.py` | 134 | yes | functions: high_impact_event_dates, is_high_impact_event_date |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/run_backtest.py` | 219 | maybe | functions: _ann_factor_for_tf, _compute_metrics, _write_summary, _write_trades_csv, _write_equity_csv |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/data_loader.py` | 32 | yes | functions: load_all |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/run_backtest.py` | 133 | maybe | functions: _summarise, _write_trades, _write_equity, main |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/run_backtest.py` | 260 | maybe | functions: _load_8h_ohlcv_with_pcr_proxy, _ann_factor_for_tf, _compute_metrics, _write_summary, _write_trades_csv |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/run_backtest.py` | 115 | maybe | functions: _make_synthetic_data, _compute_metrics, main |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/run_cpcv.py` | 182 | yes | functions: _build_strategy_returns, _sanitize, main |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/run_backtest.py` | 115 | maybe | functions: _make_synthetic_data, _compute_metrics, main |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/run_cpcv.py` | 192 | yes | functions: _build_strategy_returns, _sanitize, main |
| pairs_cointegration_1d_20260709 | `pairs_cointegration_1d_20260709/backtest.py` | 82 | yes | functions: load_config, _print_summary, main |
| pairs_cointegration_1d_20260709 | `pairs_cointegration_1d_20260709/data_loader.py` | 180 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, _resample_1d, load_symbol_1d |
| pairs_cointegration_1d_20260709 | `pairs_cointegration_1d_20260709/portfolio.py` | 384 | yes | classes: _RiskCfg, PairAllocation, _PairsDict; functions: apply_pair_constraints |
| pairs_cointegration_1d_20260709 | `pairs_cointegration_1d_20260709/run_backtest.py` | 520 | maybe | classes: PairCandidate, MultiPairResult; functions: select_pairs, rolling_eg_timeseries, find_coint_breaks, run_multi_pair_backtest, persist_results |
| pairs_cointegration_1d_20260709 | `pairs_cointegration_1d_20260709/walk_forward.py` | 77 | yes | functions: main |
| paper_trading | `_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/paper_runner.py` | 239 | yes | classes: KillState; functions: _load_config, _apply_cost, _load_live_bars, _evaluate_kill_criteria, _append_daily_metrics |
| trend_multi_tf_momentum_cascade_4h_1h_15m_20260714 | `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/data_loader.py` | 145 | yes | classes: SourceManifest; functions: _sha256, _normalize_ohlcv, _read_source, load_symbol_tf, load_symbol_multi |
| trend_multi_tf_momentum_cascade_4h_1h_15m_20260714 | `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/run_backtest.py` | 129 | maybe | functions: _trade_rows, main |
| trend_multi_tf_momentum_cascade_4h_1h_15m_20260714 | `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/walk_forward.py` | 259 | yes | classes: WindowSlice; functions: build_schedule, slice_frames, _run, _equity_curve, _metrics |
| trend_regime_gate_1d_adx_4h_1h_20260714 | `trend_regime_gate_1d_adx_4h_1h_20260714/data_loader.py` | 199 | yes | classes: SourceManifest; functions: _sha256, _normalize_ohlcv, _read_source, load_symbol_1h, load_symbol_4h |
| trend_regime_gate_1d_adx_4h_1h_20260714 | `trend_regime_gate_1d_adx_4h_1h_20260714/run_backtest.py` | 156 | maybe | functions: _trade_rows, main |
| trend_regime_gate_1d_adx_4h_1h_20260714 | `trend_regime_gate_1d_adx_4h_1h_20260714/walk_forward.py` | 308 | yes | classes: WindowSlice; functions: build_schedule, slice_frames, _run, _equity_curve, _metrics |
| vol_breakout_2tf_vpvr_confluence_4h_20260712 | `vol_breakout_2tf_vpvr_confluence_4h_20260712/data_loader.py` | 213 | yes | classes: SourceManifest; functions: _sha256, _source_filename, _normalize_ohlcv, _read_source, build_source_manifest |
| vol_breakout_2tf_vpvr_confluence_4h_20260712 | `vol_breakout_2tf_vpvr_confluence_4h_20260712/run_backtest.py` | 201 | maybe | functions: _trade_rows, _per_symbol_metrics, main |
| vol_breakout_vpvr_val_fade_1h_5m_20260714 | `vol_breakout_vpvr_val_fade_1h_5m_20260714/data_loader.py` | 371 | yes | classes: SourceManifest; functions: _sha256, _normalize_5m, _normalize_1h, _read_5m, _read_1h |
| vol_breakout_vpvr_val_fade_1h_5m_20260714 | `vol_breakout_vpvr_val_fade_1h_5m_20260714/run_backtest.py` | 216 | maybe | functions: _bars_per_year, _write_trades_csv, _write_equity_csv, _gate_row, main |
| vol_breakout_vpvr_val_fade_1h_5m_20260714 | `vol_breakout_vpvr_val_fade_1h_5m_20260714/scripts/b6_bootstrap.py` | 86 | yes | functions: load_returns, bootstrap_sharpe_like, percentile, main |
| vol_breakout_vpvr_val_fade_1h_5m_20260714 | `vol_breakout_vpvr_val_fade_1h_5m_20260714/walk_forward.py` | 154 | yes | functions: _max_drawdown, walk_forward |
| vpvr_carry_term_8h_20260711 | `vpvr_carry_term_8h_20260711/data_loader.py` | 140 | yes | classes: SourceManifest; functions: _load_1h, _load_funding_8h, _aggregate_8h, load_symbol, load_all |
| vpvr_carry_term_8h_20260711 | `vpvr_carry_term_8h_20260711/run_backtest.py` | 156 | maybe | functions: _summarise, _write_trades_csv, _write_equity_csv, main |
| vpvr_edge_zscore_15m_only_20260720 | `vpvr_edge_zscore_15m_only_20260720/run_backtest.py` | 487 | maybe | functions: _sanitize, _daily_resampled_sharpe, _compute_metrics, _slice_window, _run_walk_forward |
| vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720 | `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/data_loader.py` | 146 | yes | functions: _load_ohlcv, load_tf, load_all, default_symbols |
| vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720 | `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/run_backtest.py` | 620 | maybe | functions: _daily_resampled_sharpe, _compute_metrics, _slice_window, _tf_freq, _sanitize |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/data_loader.py` | 70 | yes | functions: _load_4h, _load_funding, load_symbol, load_all |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/run_backtest.py` | 187 | maybe | functions: _bars_per_year, _summarise, _write_trades_csv, _write_equity_csv, main |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/data_loader.py` | 85 | yes | functions: _load_4h, _load_funding, load_symbol, load_all, main |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/run_backtest.py` | 240 | maybe | functions: _bars_per_year, _summarise, _write_trades_csv, _write_equity_csv, main |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/walk_forward.py` | 212 | yes | functions: _bars_per_year, _annualised_sharpe, _bootstrap_ci, _build_folds, main |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/data_loader.py` | 111 | yes | functions: _load_ohlcv, _load_funding_events, load_tf, load_funding, load_all |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/run_backtest.py` | 358 | maybe | functions: _slice_window, _tf_freq, _bootstrap_ci_lower, _sanitize, _run_fold |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/run_cpcv.py` | 268 | yes | functions: _bar_to_periods_per_year, _build_strategy_returns, _strategy_factory, _sanitize, main |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_hvn_lvn_20260718/vpvr_funding_hvn_lvn_backtest.py` | 822 | yes | classes: Trade; functions: _load_ohlcv, _load_funding, _atr, _build_vpvr_table, _nearest_lvn_above |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_hvn_lvn_confluence_20260718/vpvr_funding_hvn_lvn_confluence_backtest.py` | 820 | yes | classes: Trade; functions: _load_ohlcv, _load_funding, _atr, _build_vpvr_table, _nearest_lvn_above |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/data_loader.py` | 54 | yes | functions: _load_15m, _load_funding, load_all |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/run_backtest.py` | 131 | maybe | functions: _summarise, _write_trades, _write_equity, main |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/data_loader.py` | 111 | yes | functions: _first_existing, _to_naive_utc, _load_1h, _load_funding, load_symbol |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/run_backtest.py` | 192 | maybe | functions: _bars_per_year, _summarise, _write_trades_csv, _write_equity_csv, main |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/data_loader.py` | 64 | yes | functions: _load_1h, _load_funding, load_symbol, load_all |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/run_backtest.py` | 182 | maybe | functions: _bars_per_year, _summarise, _write_trades_csv, _write_equity_csv, main |
| vpvr_micro_reversion_1h_funding_filter_20260710 | `vpvr_micro_reversion_1h_funding_filter_20260710/data_loader.py` | 143 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1h, load_symbol_1h, load_all |
| vpvr_micro_reversion_1h_funding_filter_20260710 | `vpvr_micro_reversion_1h_funding_filter_20260710/run_backtest.py` | 206 | maybe | functions: _trade_rows, _bars_per_year, _strategy_metrics, main |
| vpvr_regime_reversion_4h_vol_switch_20260710 | `vpvr_regime_reversion_4h_vol_switch_20260710/data_loader.py` | 179 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, _resample, _to_pandas_freq |
| vpvr_regime_reversion_4h_vol_switch_20260710 | `vpvr_regime_reversion_4h_vol_switch_20260710/run_backtest.py` | 125 | maybe | functions: _trade_rows, main |
| vpvr_reversion_15m_donchian_regime_20260709 | `vpvr_reversion_15m_donchian_regime_20260709/data_loader.py` | 158 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, _to_pandas_freq, _resample |
| vpvr_reversion_15m_donchian_regime_20260709 | `vpvr_reversion_15m_donchian_regime_20260709/run_backtest.py` | 98 | maybe | functions: _trade_rows, main |
| vpvr_reversion_5m_vwap_trail_20260709 | `vpvr_reversion_5m_vwap_trail_20260709/data_loader.py` | 183 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, _resample, _to_pandas_freq |
| vpvr_reversion_5m_vwap_trail_20260709 | `vpvr_reversion_5m_vwap_trail_20260709/run_backtest.py` | 127 | maybe | functions: _trade_rows, main |
| vpvr_tod_session_filter_15m_20260715 | `vpvr_tod_session_filter_15m_20260715/data_loader.py` | 83 | yes | functions: _synth_15m_from_30m, load_15m, load_pair_15m |
| vpvr_tod_session_filter_15m_20260715 | `vpvr_tod_session_filter_15m_20260715/run_backtest.py` | 243 | maybe | functions: _ann_factor_for_tf, _compute_metrics, _write_trades_csv, _write_equity_csv, main |
| vpvr_tod_session_filter_15m_20260715 | `vpvr_tod_session_filter_15m_20260715/tod_calendar.py` | 66 | yes | functions: session_for_timestamp, is_session_active, session_index_for_day, session_label, session_change_points |
| vpvr_volume_edge_3tf_v1_20260711 | `vpvr_volume_edge_3tf_v1_20260711/data_loader.py` | 96 | yes | functions: _normalize_ohlcv, _read_local, load_symbol_tf, load_symbol_multi, load_all |
| vpvr_volume_edge_3tf_v1_20260711 | `vpvr_volume_edge_3tf_v1_20260711/rebuild_summary.py` | 188 | yes | functions: _sanitize, _json_default, main |
| vpvr_volume_edge_3tf_v1_20260711 | `vpvr_volume_edge_3tf_v1_20260711/run_backtest.py` | 174 | maybe | functions: _trade_rows, _sanitize, _downsample_equity, main |
| vpvr_volume_edge_3tf_v1_20260711 | `vpvr_volume_edge_3tf_v1_20260711/walk_forward.py` | 259 | yes | classes: WindowSlice; functions: build_schedule, slice_frames, _run, _equity_curve, _metrics |
| vpvr_xs_basis_zscore_15m_funding_filter_20260712 | `vpvr_xs_basis_zscore_15m_funding_filter_20260712/data_loader.py` | 59 | yes | functions: _standardize, load_all, load_funding_series |
| vpvr_xs_basis_zscore_15m_funding_filter_20260712 | `vpvr_xs_basis_zscore_15m_funding_filter_20260712/run_backtest.py` | 166 | maybe | functions: _summarise_pair, _write_trades, _write_equity, main |
| vpvr_xs_basis_zscore_15m_funding_filter_20260712 | `vpvr_xs_basis_zscore_15m_funding_filter_20260712/walk_forward.py` | 279 | yes | classes: WindowResult; functions: _annualisation_factor, _summarise_trades, _dsr, build_windows, main |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/data_loader.py` | 52 | yes | functions: _load_1h, _load_4h, load_all |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/run_backtest.py` | 163 | maybe | functions: _summarise_pair, _write_trades, _write_equity, main |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/run_optimize_cpcv.py` | 566 | yes | functions: _build_variant_cfg, _bar_returns_for_variant, _strategy_fn_factory, _evaluate_variant, _decide_chosen |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/walk_forward.py` | 281 | yes | classes: WindowResult; functions: _summarise_trades, build_windows, main |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/build_deliverables.py` | 182 | yes | functions: main |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/cpcv_optimize.py` | 419 | yes | classes: FoldResult; functions: make_cfg, equity_curve_hash, annualisation_factor, sharpe_from_trades, dsr_zscore |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/data_loader.py` | 52 | yes | functions: _load_1h, _load_4h, load_all |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/run_backtest.py` | 160 | maybe | functions: _summarise_pair, _write_trades, _write_equity, main |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/walk_forward.py` | 387 | yes | classes: WindowResult; functions: _annualisation_factor, _summarise_trades, _slice_data, _window_metrics, _dsr |
| vpvr_xs_reversion_1d_momentum_filter_20260709 | `vpvr_xs_reversion_1d_momentum_filter_20260709/data_loader.py` | 151 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, _resample_1d, load_symbol |
| vpvr_xs_reversion_1d_momentum_filter_20260709 | `vpvr_xs_reversion_1d_momentum_filter_20260709/run_backtest.py` | 102 | maybe | functions: _trade_rows, main |
| vpvr_xs_smart_routing_15m_20260715 | `vpvr_xs_smart_routing_15m_20260715/data_loader.py` | 51 | yes | functions: _load_15m, load_symbol, load_all |
| vpvr_xs_smart_routing_15m_20260715 | `vpvr_xs_smart_routing_15m_20260715/run_backtest.py` | 182 | maybe | functions: _bars_per_year, _summarise, _write_trades_csv, _write_equity_csv, main |
| xs_momentum_rank_1d_20260709 | `xs_momentum_rank_1d_20260709/backtest.py` | 388 | yes | classes: RebalanceEvent, BacktestResult; functions: _cost_per_side, _prior_positions, _new_positions_from_target, _realized_pnl, _delta_turnover_cost |
| xs_momentum_rank_1d_20260709 | `xs_momentum_rank_1d_20260709/data_loader.py` | 225 | yes | classes: SourceManifest; functions: _sha256, build_source_manifest, _read_1m, _resample_1d, load_symbol_1d |
| xs_momentum_rank_1d_20260709 | `xs_momentum_rank_1d_20260709/portfolio.py` | 163 | yes | classes: TargetPosition, PortfolioTarget; functions: equal_weight_allocation, gross_exposure, enforce_gross_cap, daily_loss_breach, monthly_pause_active |
| xs_momentum_rank_1d_20260709 | `xs_momentum_rank_1d_20260709/run_backtest.py` | 113 | maybe | functions: _avg_weight_contribution, main |
| xs_momentum_rank_1d_20260709 | `xs_momentum_rank_1d_20260709/universe.py` | 130 | yes | classes: UniverseConfig; functions: load_universe_config, daily_usd_volume, trailing_bar_count, liquidity_filter, eligible_symbols_on |
| xs_momentum_rank_1d_20260709 | `xs_momentum_rank_1d_20260709/walk_forward.py` | 398 | yes | classes: WalkForwardSplit, WalkForwardReport; functions: walk_forward_splits, _empty_window_metrics, _window_metrics, _is_warmup_window, _to_naive_utc |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/data_loader.py` | 103 | yes | functions: _standardize_columns, _load_30m, _load_15m, _load_funding, load_all |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/run_backtest.py` | 160 | maybe | functions: _summarise_pair, _write_trades, _write_equity, main |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/walk_forward.py` | 375 | yes | classes: WindowResult; functions: _annualisation_factor, _summarise_trades, _slice_data, _window_metrics, _dsr |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/data_loader.py` | 63 | yes | functions: _standardize_columns, _load_30m, _load_funding, load_all, load_funding |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/run_backtest.py` | 160 | maybe | functions: _summarise_pair, _write_trades, _write_equity, main |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/walk_forward.py` | 375 | yes | classes: WindowResult; functions: _annualisation_factor, _summarise_trades, _slice_data, _window_metrics, _dsr |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/data_loader.py` | 63 | yes | functions: _standardize_columns, _load_30m, _load_funding, load_all, load_funding |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/run_backtest.py` | 160 | maybe | functions: _summarise_pair, _write_trades, _write_equity, main |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/walk_forward.py` | 375 | yes | classes: WindowResult; functions: _annualisation_factor, _summarise_trades, _slice_data, _window_metrics, _dsr |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/data_loader.py` | 103 | yes | functions: _standardize_columns, _load_30m, _load_15m, _load_funding, load_all |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/run_backtest.py` | 160 | maybe | functions: _summarise_pair, _write_trades, _write_equity, main |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/walk_forward.py` | 375 | yes | classes: WindowResult; functions: _annualisation_factor, _summarise_trades, _slice_data, _window_metrics, _dsr |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/data_loader.py` | 103 | yes | functions: _standardize_columns, _load_30m, _load_15m, _load_funding, load_all |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/run_backtest.py` | 160 | maybe | functions: _summarise_pair, _write_trades, _write_equity, main |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/walk_forward.py` | 375 | yes | classes: WindowResult; functions: _annualisation_factor, _summarise_trades, _slice_data, _window_metrics, _dsr |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/data_loader.py` | 103 | yes | functions: _standardize_columns, _load_30m, _load_15m, _load_funding, load_all |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/run_backtest.py` | 160 | maybe | functions: _summarise_pair, _write_trades, _write_equity, main |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/walk_forward.py` | 375 | yes | classes: WindowResult; functions: _annualisation_factor, _summarise_trades, _slice_data, _window_metrics, _dsr |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/data_loader.py` | 102 | yes | functions: _standardize_columns, _load_30m, _load_15m, _load_funding, load_all |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/run_backtest.py` | 160 | maybe | functions: _summarise_pair, _write_trades, _write_equity, main |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/walk_forward.py` | 375 | yes | classes: WindowResult; functions: _annualisation_factor, _summarise_trades, _slice_data, _window_metrics, _dsr |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/data_loader.py` | 63 | yes | functions: _standardize_columns, _load_30m, _load_funding, load_all, load_funding |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/run_backtest.py` | 160 | maybe | functions: _summarise_pair, _write_trades, _write_equity, main |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/walk_forward.py` | 375 | yes | classes: WindowResult; functions: _annualisation_factor, _summarise_trades, _slice_data, _window_metrics, _dsr |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/data_loader.py` | 63 | yes | functions: _standardize_columns, _load_30m, _load_funding, load_all, load_funding |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/run_backtest.py` | 160 | maybe | functions: _summarise_pair, _write_trades, _write_equity, main |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/walk_forward.py` | 375 | yes | classes: WindowResult; functions: _annualisation_factor, _summarise_trades, _slice_data, _window_metrics, _dsr |

### entry

| Strategy | File | LOC | Reusable? | Description |
|----------|------|-----|-----------|-------------|
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707/strategy.py` | 467 | maybe | strategy entry/run script |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_049/strategy.py` | 467 | maybe | strategy entry/run script |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_050/strategy.py` | 467 | maybe | strategy entry/run script |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_099/strategy.py` | 467 | maybe | strategy entry/run script |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_100/strategy.py` | 467 | maybe | strategy entry/run script |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vol_breakout_1m_15m_vpvr_confluence_u6_20260718/strategy.py` | 356 | maybe | strategy entry/run script |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/strategy.py` | 424 | maybe | strategy entry/run script |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/strategy.py` | 378 | maybe | strategy entry/run script |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/strategy.py` | 180 | maybe | strategy entry/run script |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/strategy.py` | 143 | maybe | strategy entry/run script |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/strategy.py` | 549 | maybe | strategy entry/run script |
| donchian_breakout_atr_1d_20260709 | `donchian_breakout_atr_1d_20260709/strategy.py` | 417 | maybe | strategy entry/run script |
| funding_carry | `_graveyard/funding_carry/funding_carry/run_u5.py` | 608 | maybe | strategy entry/run script |
| funding_carry | `_graveyard/funding_carry/funding_carry/sma34946/run_sma34946.py` | 384 | maybe | strategy entry/run script |
| funding_carry | `_graveyard/funding_carry/funding_carry/strategy.py` | 399 | maybe | strategy entry/run script |
| funding_carry | `_graveyard/funding_carry/funding_carry_asym/prototype.py` | 620 | maybe | strategy entry/run script |
| funding_carry | `_graveyard/funding_carry/funding_carry_asym/sma34928_low_threshold.py` | 320 | maybe | strategy entry/run script |
| funding_carry | `_graveyard/funding_carry/funding_carry_asym/strategy.py` | 211 | maybe | strategy entry/run script |
| funding_carry | `_graveyard/funding_carry/funding_oscillator_mr/run_u5noncarry.py` | 557 | maybe | strategy entry/run script |
| funding_carry | `_graveyard/funding_carry/funding_oscillator_mr/strategy.py` | 294 | maybe | strategy entry/run script |
| impl_vpvr_multi_tf_funding | `impl_vpvr_multi_tf_funding/strategy.py` | 405 | maybe | strategy entry/run script |
| loid_vpvr_confluence_20260717 | `loid_vpvr_confluence_20260717/strategy.py` | 228 | maybe | strategy entry/run script |
| momentum_trend_btc_only_softer_stop_1h_20260712 | `momentum_trend_btc_only_softer_stop_1h_20260712/strategy.py` | 531 | maybe | strategy entry/run script |
| momentum_trend_multi_tf_atr_scaled_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_1h_20260712/strategy.py` | 497 | maybe | strategy entry/run script |
| momentum_trend_multi_tf_atr_scaled_v2_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_v2_1h_20260712/strategy.py` | 539 | maybe | strategy entry/run script |
| momentum_trend_multi_tf_atr_scaled_v3_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712/strategy.py` | 497 | maybe | strategy entry/run script |
| mtf_vpvr_edge_zscore_1m_15m_2h_20260718 | `mtf_vpvr_edge_zscore_1m_15m_2h_20260718/strategy.py` | 651 | maybe | strategy entry/run script |
| mtf_xs_pairs_1m_15m_2h_h1_20260718 | `mtf_xs_pairs_1m_15m_2h_h1_20260718/strategy.py` | 32 | maybe | strategy entry/run script |
| mtf_xs_pairs_1m_15m_2h_h2_20260718 | `mtf_xs_pairs_1m_15m_2h_h2_20260718/strategy.py` | 31 | maybe | strategy entry/run script |
| mtf_xs_pairs_1m_15m_2h_h3_20260718 | `mtf_xs_pairs_1m_15m_2h_h3_20260718/strategy.py` | 32 | maybe | strategy entry/run script |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/strategy.py` | 151 | maybe | strategy entry/run script |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/strategy.py` | 306 | maybe | strategy entry/run script |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/strategy.py` | 147 | maybe | strategy entry/run script |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/strategy.py` | 152 | maybe | strategy entry/run script |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/strategy.py` | 156 | maybe | strategy entry/run script |
| pairs_cointegration_1d_20260709 | `pairs_cointegration_1d_20260709/strategy.py` | 533 | maybe | strategy entry/run script |
| trend_multi_tf_momentum_cascade_4h_1h_15m_20260714 | `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/strategy.py` | 334 | maybe | strategy entry/run script |
| trend_regime_gate_1d_adx_4h_1h_20260714 | `trend_regime_gate_1d_adx_4h_1h_20260714/strategy.py` | 404 | maybe | strategy entry/run script |
| vol_breakout_2tf_vpvr_confluence_4h_20260712 | `vol_breakout_2tf_vpvr_confluence_4h_20260712/strategy.py` | 539 | maybe | strategy entry/run script |
| vol_breakout_vpvr_val_fade_1h_5m_20260714 | `vol_breakout_vpvr_val_fade_1h_5m_20260714/strategy.py` | 420 | maybe | strategy entry/run script |
| vpvr_carry_term_8h_20260711 | `vpvr_carry_term_8h_20260711/strategy.py` | 303 | maybe | strategy entry/run script |
| vpvr_edge_zscore_15m_only_20260720 | `vpvr_edge_zscore_15m_only_20260720/strategy.py` | 318 | maybe | strategy entry/run script |
| vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720 | `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/strategy.py` | 373 | maybe | strategy entry/run script |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/strategy.py` | 225 | maybe | strategy entry/run script |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/strategy.py` | 319 | maybe | strategy entry/run script |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/strategy.py` | 359 | maybe | strategy entry/run script |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/strategy.py` | 317 | maybe | strategy entry/run script |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/strategy.py` | 305 | maybe | strategy entry/run script |
| vpvr_micro_reversion_1h_funding_filter_20260710 | `vpvr_micro_reversion_1h_funding_filter_20260710/strategy.py` | 531 | maybe | strategy entry/run script |
| vpvr_regime_reversion_4h_vol_switch_20260710 | `vpvr_regime_reversion_4h_vol_switch_20260710/strategy.py` | 432 | maybe | strategy entry/run script |
| vpvr_reversion_15m_donchian_regime_20260709 | `vpvr_reversion_15m_donchian_regime_20260709/strategy.py` | 356 | maybe | strategy entry/run script |
| vpvr_reversion_5m_vwap_trail_20260709 | `vpvr_reversion_5m_vwap_trail_20260709/strategy.py` | 409 | maybe | strategy entry/run script |
| vpvr_tod_session_filter_15m_20260715 | `vpvr_tod_session_filter_15m_20260715/strategy.py` | 148 | maybe | strategy entry/run script |
| vpvr_volume_edge_3tf_v1_20260711 | `vpvr_volume_edge_3tf_v1_20260711/strategy.py` | 443 | maybe | strategy entry/run script |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/strategy.py` | 400 | maybe | strategy entry/run script |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/strategy.py` | 350 | maybe | strategy entry/run script |
| vpvr_xs_reversion_1d_momentum_filter_20260709 | `vpvr_xs_reversion_1d_momentum_filter_20260709/strategy.py` | 310 | maybe | strategy entry/run script |
| vpvr_xs_smart_routing_15m_20260715 | `vpvr_xs_smart_routing_15m_20260715/strategy.py` | 325 | maybe | strategy entry/run script |
| xs_momentum_rank_1d_20260709 | `xs_momentum_rank_1d_20260709/strategy.py` | 163 | maybe | strategy entry/run script |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/strategy.py` | 371 | maybe | strategy entry/run script |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/strategy.py` | 376 | maybe | strategy entry/run script |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/strategy.py` | 375 | maybe | strategy entry/run script |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/strategy.py` | 370 | maybe | strategy entry/run script |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/strategy.py` | 371 | maybe | strategy entry/run script |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/strategy.py` | 370 | maybe | strategy entry/run script |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/strategy.py` | 372 | maybe | strategy entry/run script |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/strategy.py` | 376 | maybe | strategy entry/run script |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/strategy.py` | 376 | maybe | strategy entry/run script |

## Anti-Patterns

These files contain duplicated framework adapters, hardcoded paths, one-off
diagnostic scripts, or other patterns that should NOT be copied into `_shared/`.

| Strategy | File | LOC | Reason |
|----------|------|-----|--------|
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_iceberg_fade_v2_5m_20260711/framework_adapter_backtrader.py` | 206 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/framework_adapter_freqtrade.py` | 290 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/framework_adapter_backtrader.py` | 362 | framework adapter duplicated per strategy; framework-specific backtrader coupling |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/framework_adapter_freqtrade.py` | 264 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/framework_adapter_backtrader.py` | 420 | framework adapter duplicated per strategy; framework-specific backtrader coupling |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/framework_adapter_freqtrade.py` | 318 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/framework_adapter_backtrader.py` | 470 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| 1m_klines_reversal | `_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/framework_adapter_freqtrade.py` | 501 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| funding_carry | `_graveyard/funding_carry/funding_carry/framework_adapter_backtrader.py` | 336 | framework adapter duplicated per strategy; framework-specific backtrader coupling |
| funding_carry | `_graveyard/funding_carry/funding_carry/framework_adapter_freqtrade.py` | 403 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| funding_carry | `_graveyard/funding_carry/funding_carry/run_framework_cv_multiwindow.py` | 129 | framework-specific backtrader coupling; framework-specific freqtrade coupling |
| funding_carry | `_graveyard/funding_carry/funding_carry/run_u5_multiwindow.py` | 256 | framework-specific backtrader coupling; framework-specific freqtrade coupling |
| funding_carry | `_graveyard/funding_carry/funding_carry_asym/framework_adapter_vectorbt.py` | 217 | framework adapter duplicated per strategy; framework-specific vectorbt coupling |
| funding_carry | `_graveyard/funding_carry/funding_carry_asym/sweep_top_combo.py` | 367 | framework-specific freqtrade coupling |
| funding_carry | `_graveyard/funding_carry/sma_34925_btc_funding_delta/backtest_backtrader.py` | 255 | framework-specific backtrader coupling |
| funding_carry | `_graveyard/funding_carry/sma_34925_btc_funding_delta/backtest_inhouse.py` | 363 | framework-specific freqtrade coupling |
| funding_carry | `_graveyard/funding_carry/sma_34925_btc_funding_delta/backtest_vectorbt.py` | 218 | framework-specific vectorbt coupling |
| loid_vpvr_confluence_20260717 | `loid_vpvr_confluence_20260717/run_g1g7_backtest.py` | 407 | framework-specific vectorbt coupling; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| momentum_trend_btc_only_softer_stop_1h_20260712 | `momentum_trend_btc_only_softer_stop_1h_20260712/framework_adapter_backtrader.py` | 445 | framework adapter duplicated per strategy; framework-specific backtrader coupling |
| momentum_trend_multi_tf_atr_scaled_v3_1h_20260712 | `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712/framework_adapter_backtrader.py` | 310 | framework adapter duplicated per strategy; framework-specific backtrader coupling |
| mtf_vpvr_edge_zscore_1m_15m_2h_20260718 | `mtf_vpvr_edge_zscore_1m_15m_2h_20260718/diagnose.py` | 68 | one-off diagnostic/script not reusable |
| mtf_vpvr_edge_zscore_1m_15m_2h_20260718 | `mtf_vpvr_edge_zscore_1m_15m_2h_20260718/inspect_full.py` | 45 | one-off diagnostic/script not reusable |
| mtf_vpvr_edge_zscore_1m_15m_2h_20260718 | `mtf_vpvr_edge_zscore_1m_15m_2h_20260718/inspect_trades.py` | 36 | one-off diagnostic/script not reusable |
| mtf_vpvr_edge_zscore_1m_15m_2h_20260718 | `mtf_vpvr_edge_zscore_1m_15m_2h_20260718/smoke_test.py` | 28 | one-off diagnostic/script not reusable |
| mtf_xs_pairs_1m_15m_2h_h3_20260718 | `mtf_xs_pairs_1m_15m_2h_h3_20260718/framework_validate.py` | 227 | framework-specific backtrader coupling; framework-specific freqtrade coupling |
| mtf_xs_pairs_1m_15m_2h_h3_20260718 | `mtf_xs_pairs_1m_15m_2h_h3_20260718/write_winner_trades.py` | 122 | framework-specific backtrader coupling; framework-specific freqtrade coupling |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/framework_adapter_backtrader.py` | 310 | framework adapter duplicated per strategy; framework-specific backtrader coupling |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/framework_adapter_freqtrade.py` | 348 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/framework_adapter_vectorbt.py` | 492 | framework adapter duplicated per strategy; framework-specific vectorbt coupling; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/framework_adapter_backtrader.py` | 370 | framework adapter duplicated per strategy; framework-specific backtrader coupling |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/framework_adapter_vectorbt.py` | 373 | framework adapter duplicated per strategy; framework-specific vectorbt coupling |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/framework_adapter_backtrader.py` | 417 | framework adapter duplicated per strategy; framework-specific backtrader coupling |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/framework_adapter_freqtrade.py` | 329 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/framework_adapter_backtrader.py` | 521 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/framework_adapter_freqtrade.py` | 347 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/framework_adapter_backtrader.py` | 521 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| options_macro_sentiment | `_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/framework_adapter_freqtrade.py` | 347 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| pairs_cointegration_1d_20260709 | `pairs_cointegration_1d_20260709/framework_adapter_freqtrade.py` | 340 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| vol_breakout_2tf_vpvr_confluence_4h_20260712 | `vol_breakout_2tf_vpvr_confluence_4h_20260712/walk_forward.py` | 523 | framework-specific backtrader coupling; framework-specific freqtrade coupling; inline metrics duplicated from _shared/evaluation |
| vol_breakout_vpvr_val_fade_1h_5m_20260714 | `vol_breakout_vpvr_val_fade_1h_5m_20260714/framework_adapter_backtrader.py` | 154 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vol_breakout_vpvr_val_fade_1h_5m_20260714 | `vol_breakout_vpvr_val_fade_1h_5m_20260714/framework_adapter_freqtrade.py` | 204 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| vol_breakout_vpvr_val_fade_1h_5m_20260714 | `vol_breakout_vpvr_val_fade_1h_5m_20260714/scripts/b6_aggregate.py` | 94 | framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_carry_term_8h_20260711 | `vpvr_carry_term_8h_20260711/framework_adapter_backtrader.py` | 370 | framework adapter duplicated per strategy; framework-specific backtrader coupling |
| vpvr_carry_term_8h_20260711 | `vpvr_carry_term_8h_20260711/framework_adapter_freqtrade.py` | 399 | framework adapter duplicated per strategy; framework-specific vectorbt coupling; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_carry_term_8h_20260711 | `vpvr_carry_term_8h_20260711/framework_adapter_vectorbt.py` | 449 | framework adapter duplicated per strategy; framework-specific vectorbt coupling |
| vpvr_edge_zscore_15m_only_20260720 | `vpvr_edge_zscore_15m_only_20260720/smoke_backtest.py` | 116 | one-off diagnostic/script not reusable |
| vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720 | `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/smoke_backtest.py` | 137 | one-off diagnostic/script not reusable |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/framework_adapter_backtrader.py` | 245 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/framework_adapter_freqtrade.py` | 230 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/framework_adapter_vectorbt.py` | 350 | framework adapter duplicated per strategy; framework-specific vectorbt coupling; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/audit_maxdd.py` | 219 | framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/framework_adapter_backtrader.py` | 263 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/framework_adapter_freqtrade.py` | 243 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/framework_adapter_vectorbt.py` | 357 | framework adapter duplicated per strategy; framework-specific vectorbt coupling; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_hvn_lvn_confluence_20260718/framework_adapter_backtrader.py` | 377 | framework adapter duplicated per strategy; framework-specific backtrader coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/framework_adapter_backtrader.py` | 293 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/framework_adapter_vectorbt.py` | 294 | framework adapter duplicated per strategy; framework-specific vectorbt coupling; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/framework_adapter_backtrader.py` | 219 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/framework_adapter_freqtrade.py` | 196 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/framework_adapter_vectorbt.py` | 234 | framework adapter duplicated per strategy; framework-specific vectorbt coupling; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/framework_adapter_backtrader.py` | 312 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/framework_adapter_freqtrade.py` | 300 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| vpvr_funding | `_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/framework_adapter_vectorbt.py` | 403 | framework adapter duplicated per strategy; framework-specific vectorbt coupling; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_reversion_5m_vwap_trail_20260709 | `vpvr_reversion_5m_vwap_trail_20260709/framework_adapter_vectorbt.py` | 292 | framework adapter duplicated per strategy; framework-specific vectorbt coupling |
| vpvr_xs_basis_zscore_15m_funding_filter_20260712 | `vpvr_xs_basis_zscore_15m_funding_filter_20260712/framework_adapter_backtrader.py` | 399 | framework adapter duplicated per strategy; framework-specific backtrader coupling |
| vpvr_xs_basis_zscore_15m_funding_filter_20260712 | `vpvr_xs_basis_zscore_15m_funding_filter_20260712/framework_adapter_freqtrade.py` | 367 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| vpvr_xs_basis_zscore_15m_funding_filter_20260712 | `vpvr_xs_basis_zscore_15m_funding_filter_20260712/run_cpcv.py` | 728 | framework-specific freqtrade coupling |
| vpvr_xs_basis_zscore_15m_funding_filter_20260712 | `vpvr_xs_basis_zscore_15m_funding_filter_20260712/strategy.py` | 422 | framework-specific freqtrade coupling |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/cointegration_pairs_vpvr_poc_4h_20260714/framework_adapter_vectorbt.py` | 279 | framework adapter duplicated per strategy; framework-specific vectorbt coupling; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/framework_adapter_backtrader.py` | 468 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/framework_adapter_freqtrade.py` | 332 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| vpvr_xs_pairs_4h | `_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/framework_adapter_freqtrade.py` | 479 | framework adapter duplicated per strategy; framework-specific vectorbt coupling; framework-specific freqtrade coupling |
| vpvr_xs_smart_routing_15m_20260715 | `vpvr_xs_smart_routing_15m_20260715/framework_adapter_backtrader.py` | 362 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| vpvr_xs_smart_routing_15m_20260715 | `vpvr_xs_smart_routing_15m_20260715/framework_adapter_freqtrade.py` | 299 | framework adapter duplicated per strategy; framework-specific vectorbt coupling; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/framework_adapter_backtrader.py` | 550 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/framework_adapter_freqtrade.py` | 559 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/framework_adapter_backtrader.py` | 653 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/framework_adapter_freqtrade.py` | 531 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/framework_adapter_backtrader.py` | 643 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/framework_adapter_freqtrade.py` | 525 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/framework_adapter_backtrader.py` | 704 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/framework_adapter_freqtrade.py` | 556 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/framework_adapter_backtrader.py` | 707 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/framework_adapter_freqtrade.py` | 565 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/framework_adapter_backtrader.py` | 707 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/framework_adapter_freqtrade.py` | 557 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/framework_adapter_backtrader.py` | 756 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/framework_adapter_freqtrade.py` | 613 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/framework_adapter_backtrader.py` | 674 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/framework_adapter_freqtrade.py` | 606 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/framework_adapter_backtrader.py` | 743 | framework adapter duplicated per strategy; framework-specific backtrader coupling; framework-specific freqtrade coupling |
| xs_pairs_30m | `_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/framework_adapter_freqtrade.py` | 632 | framework adapter duplicated per strategy; framework-specific freqtrade coupling |

## Recommended Migration to `_shared/`

1. **Consolidate indicators/signals** into `_shared/indicators/` — the existing
   `_shared/indicators/` already contains some base modules; strategy-specific
   `build_signals.py`, `indicators.py`, and `vpvr_levels.py` files should be
   refactored into generic primitives there.

2. **Unify sizing/risk** into `_shared/sizing/` — most strategies implement
   vol-target or fixed-fraction sizing inline. Extract common helpers (e.g.
   `volatility_target_size`, `kelly_fraction`) into `_shared/sizing/`.

3. **Centralize cost models** into `_shared/execution/` — any strategy with
   `execution.py`, `cost.py`, or inline fee logic should use shared cost models.

4. **Move metrics to `_shared/validation/`** — many strategies redefine Sharpe,
   max drawdown, etc. `_shared/validation/` already exists and should become the
   single source of truth.

5. **Data/orchestration helpers** — `data_loader.py`, `universe.py`, calendar
   modules, and `run_backtest.py` runners should migrate to `_shared/data/` or
   `_shared/templates/` after stripping strategy-specific columns/symbols.

6. **Delete/copy-blocker list** — the anti-pattern files (framework adapters,
   smoke/diagnose scripts) should be left in-place as historical artifacts but
   never copied into shared infrastructure.

## Methodology

The scan used a rule-based classifier with the following priority:
1. Filename keyword matching (`strategy.py` -> entry, `indicators.py` -> signal, etc.)
2. Content keyword matching on top-level function/class names
3. Anti-pattern detection (hardcoded paths, framework adapters, diagnostic scripts)
4. Manual review fallback marked as `unknown` or `maybe`

The scanner script itself is preserved at
`knowledge_graph_scanner.py` for reproducibility.
