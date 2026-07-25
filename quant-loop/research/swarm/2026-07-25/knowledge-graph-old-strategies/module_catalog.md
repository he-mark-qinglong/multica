# Old-strategy reusable-module catalog

Generated from `strategies` (100 strategy directories).

## 1. Per-strategy summary

| Strategy | Status | Entry files | Uses _shared | Cost model | Gate logic | Signal modules | Risk modules | Exec modules | Cost modules | Eval modules |
|----------|--------|-------------|--------------|------------|------------|----------------|--------------|--------------|--------------|--------------|
| `donchian_breakout_atr_1d_20260709` | ACTIVE | 2 | False | none_visible | none | strategy, test_strategy | — | strategy, test_strategy | — | test_strategy |
| `impl_vpvr_multi_tf_funding` | ACTIVE | 2 | False | none_visible | manual | build_signals, combine_signals, data_loader, test_build_signals, test_combine_signals | — | strategy | — | data_loader, run_backtest, test_build_signals |
| `large_order_iceberg_tape_20260718` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `loid_iceberg_v4_1m_20260720` | ACTIVE | 7 | True | _shared | _shared | iceberg_detector, test_backtest, test_iceberg_detector | test_backtest | backtest, run_param_scan_fast | test_backtest | iceberg_detector, run_first_btc_90d, run_first_btc_90d_chunked, run_param_scan, run_param_scan_fast, run_param_scan_minimal, run_threshold_scan, test_backtest, test_iceberg_detector |
| `loid_vpvr_confluence_20260717` | ACTIVE | 3 | False | none_visible | none | build_signals | — | strategy | — | run_backtest, run_g1g7_backtest |
| `momentum_intraday_fast_15m_btc_20260712` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | data_loader |
| `momentum_trend_btc_only_softer_stop_1h_20260712` | ACTIVE | 2 | False | none_visible | none | framework_adapter_backtrader, strategy, test_strategy | strategy, test_strategy | strategy, test_strategy | — | data_loader, walk_forward |
| `momentum_trend_multi_tf_atr_scaled_1h_20260712` | ACTIVE | 2 | False | none_visible | none | strategy, test_strategy | strategy, test_strategy | strategy, test_strategy | — | data_loader, walk_forward |
| `momentum_trend_multi_tf_atr_scaled_v2_1h_20260712` | ACTIVE | 2 | False | none_visible | none | strategy, test_strategy | strategy, test_strategy | strategy, test_strategy | — | data_loader, walk_forward |
| `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712` | ACTIVE | 2 | False | none_visible | none | framework_adapter_backtrader, strategy, test_strategy | strategy, test_strategy | strategy, test_strategy | — | data_loader, framework_adapter_backtrader, walk_forward |
| `mtf_h2_vpvr_edge_1m_15m_2h_20260718` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `mtf_vpvr_edge_zscore_1m_15m_2h_20260718` | ACTIVE | 2 | False | none_visible | none | data_loader, strategy | strategy | strategy | — | strategy |
| `mtf_xs_pairs_1m_15m_2h_h1_20260718` | ACTIVE | 2 | False | none_visible | none | data_loader, test_h1_strategy_20260718 | — | test_h1_strategy_20260718 | — | — |
| `mtf_xs_pairs_1m_15m_2h_h2_20260718` | ACTIVE | 2 | False | none_visible | none | data_loader | — | — | — | — |
| `mtf_xs_pairs_1m_15m_2h_h3_20260718` | ACTIVE | 1 | False | none_visible | manual | data_loader, sizing_sweep | sizing_sweep | — | framework_validate | framework_validate, sizing_sweep |
| `mtf_xs_pairs_1m_15m_2h_h4_20260718` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `pairs_cointegration_1d_20260709` | ACTIVE | 3 | True | none_visible | none | cointegration, framework_adapter_freqtrade, optimize, run_backtest, strategy, test_backtest, test_cointegration_rolling, test_strategy | framework_adapter_freqtrade, portfolio, test_backtest | run_backtest, strategy, test_backtest, test_strategy | cointegration, test_cointegration_ols | cointegration, framework_adapter_freqtrade, optimize, strategy, test_backtest, test_cointegration_ols |
| `reports` | ACTIVE | 0 | False | none_visible | manual | _build_correlation, test_correlation_matrix | — | — | — | _build_correlation, test_correlation_matrix |
| `trend_funding_drift_4h_1h_20260714` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714` | ACTIVE | 2 | False | none_visible | none | strategy | — | strategy | strategy | data_loader, test_backtest_outputs, walk_forward |
| `trend_regime_gate_1d_adx_4h_1h_20260714` | ACTIVE | 2 | False | none_visible | none | strategy, test_strategy | test_strategy | strategy, test_strategy | strategy | data_loader, walk_forward |
| `vol_breakout_2tf_vpvr_confluence_4h_20260712` | ACTIVE | 2 | False | none_visible | none | indicators, test_basic | strategy, test_basic, walk_forward | strategy | strategy | data_loader, run_backtest, strategy, test_basic, walk_forward |
| `vol_breakout_vpvr_regime_blend_4h_20260714` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | ACTIVE | 2 | False | none_visible | manual | data_loader | framework_adapter_backtrader, framework_adapter_freqtrade, strategy, walk_forward | strategy | — | data_loader, framework_adapter_backtrader, framework_adapter_freqtrade, b6_bootstrap, b6_fwer, strategy, test_b3_artifacts, walk_forward |
| `vpvr_carry_term_8h_20260711` | ACTIVE | 2 | False | none_visible | manual | data_loader, framework_adapter_freqtrade, strategy, test_strategy | test_framework_adapter_backtrader | strategy, test_strategy | — | framework_adapter_backtrader, framework_adapter_vectorbt, test_framework_adapter_backtrader, test_strategy |
| `vpvr_edge_zscore_15m_only_20260720` | ACTIVE | 3 | False | hardcoded_or_local | manual | — | — | strategy | — | run_backtest |
| `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720` | ACTIVE | 3 | False | hardcoded_or_local | manual | build_signals, test_signals | — | strategy, test_signals | test_signals | data_loader, run_backtest, test_signals |
| `vpvr_inverse_reversion_4h_funding_filter_20260712` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | framework_adapter_freqtrade |
| `vpvr_liquidation_heatmap_15m_20260714` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_micro_reversion_1h_funding_filter_20260710` | ACTIVE | 2 | False | none_visible | none | strategy, test_strategy | — | strategy, test_strategy | — | run_backtest, test_strategy |
| `vpvr_mtf_consensus_v2_4h_20260714` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_oi_divergence_4h_20260713` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_regime_blend_4h_20260714` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_regime_reversion_4h_vol_switch_20260710` | ACTIVE | 2 | False | none_visible | manual | strategy, test_strategy | — | strategy, test_strategy | — | — |
| `vpvr_reversal_check_20260717` | ACTIVE | 1 | False | none_visible | none | run_cross_check | — | — | — | — |
| `vpvr_reversion_15m_donchian_regime_20260709` | ACTIVE | 2 | False | none_visible | none | strategy, test_strategy | — | strategy | — | — |
| `vpvr_reversion_15m_vrp_filter_20260713` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_reversion_4h_stablecoin_netflow_20260713` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_reversion_5m_vwap_trail_20260709` | ACTIVE | 2 | False | none_visible | none | strategy, test_strategy | — | strategy | — | framework_adapter_vectorbt |
| `vpvr_tod_session_filter_15m_20260715` | ACTIVE | 2 | False | none_visible | none | build_signals, test_signals | — | strategy, test_signals | — | run_backtest |
| `vpvr_volume_edge_3tf_v1_20260711` | ACTIVE | 2 | False | none_visible | none | strategy | — | strategy | strategy | data_loader, test_backtest_outputs, walk_forward |
| `vpvr_xs_basis_15m_cross_exchange_20260713` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | ACTIVE | 3 | True | _shared | manual | data_loader, framework_adapter_backtrader, framework_adapter_freqtrade, strategy, test_v72_basis_zscore | — | strategy, test_v72_basis_zscore | strategy | framework_adapter_backtrader, framework_adapter_freqtrade, run_cpcv, strategy, test_v72_basis_zscore |
| `vpvr_xs_corr_breakdown_4h_20260714` | ACTIVE | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_xs_reversion_1d_momentum_filter_20260709` | ACTIVE | 2 | False | none_visible | none | strategy, test_strategy | strategy | strategy, test_strategy | — | test_strategy |
| `vpvr_xs_smart_routing_15m_20260715` | ACTIVE | 2 | False | none_visible | none | framework_adapter_backtrader, strategy, test_v3_basic | — | strategy, test_v3_basic | — | framework_adapter_backtrader, framework_adapter_freqtrade |
| `xs_momentum_rank_1d_20260709` | ACTIVE | 3 | False | none_visible | manual | strategy, test_portfolio, test_strategy, test_universe, universe | backtest, portfolio, test_performance_report, test_portfolio | backtest, test_backtest, test_strategy | backtest | strategy, test_strategy, test_walk_forward, walk_forward |
| `bb_reversion_rsi_1m_20260707` | GRAVEYARD | 2 | False | none_visible | none | strategy, test_strategy | — | strategy, test_strategy | — | run_backtest, test_strategy |
| `bb_reversion_rsi_1m_20260707_p3opt_049` | GRAVEYARD | 3 | True | none_visible | none | strategy, test_strategy | — | strategy, test_strategy | — | run_backtest, run_cpcv, test_strategy |
| `bb_reversion_rsi_1m_20260707_p3opt_050` | GRAVEYARD | 3 | True | none_visible | none | strategy | — | strategy | — | run_backtest, run_cpcv |
| `bb_reversion_rsi_1m_20260707_p3opt_099` | GRAVEYARD | 3 | True | none_visible | none | strategy, test_strategy | — | strategy, test_strategy | — | run_backtest, run_cpcv, test_strategy |
| `bb_reversion_rsi_1m_20260707_p3opt_100` | GRAVEYARD | 3 | False | none_visible | none | strategy, test_strategy | — | strategy, test_strategy | — | run_backtest, test_strategy |
| `vol_breakout_1m_15m_vpvr_confluence_u6_20260718` | GRAVEYARD | 2 | False | none_visible | none | indicators | strategy | strategy | strategy | data_loader, run_backtest, strategy |
| `vpvr_iceberg_fade_5m_20260711` | GRAVEYARD | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_iceberg_fade_v2_5m_20260711` | GRAVEYARD | 0 | False | none_visible | none | framework_adapter_backtrader | framework_adapter_backtrader | — | — | framework_adapter_backtrader |
| `vpvr_microstructure_5m_volume_delta_20260710` | GRAVEYARD | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_mtf_reversion_5m_consensus_20260710` | GRAVEYARD | 2 | False | none_visible | manual | strategy, test_strategy | — | strategy, test_strategy | — | framework_adapter_freqtrade |
| `vpvr_obi_micro_v2_1m_20260714` | GRAVEYARD | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_reversion_1m_kama_reversal_20260709` | GRAVEYARD | 2 | False | none_visible | none | build_signals, strategy, test_signals, test_strategy | — | strategy | — | — |
| `vpvr_reversion_1m_volume_profile_break_20260709` | GRAVEYARD | 2 | False | none_visible | manual | build_signals, framework_adapter_backtrader, test_signals, test_strategy | — | strategy, test_signals | — | framework_adapter_backtrader, framework_adapter_freqtrade, run_backtest |
| `vpvr_sentiment_attention_1m_20260716` | GRAVEYARD | 2 | False | none_visible | none | build_signals, test_signals | — | strategy | — | framework_adapter_freqtrade, run_backtest |
| `vpvr_xs_leadlag_5m_20260711` | GRAVEYARD | 2 | False | none_visible | none | strategy, test_indicators | — | strategy | — | framework_adapter_backtrader, framework_adapter_freqtrade |
| `funding_carry` | GRAVEYARD | 5 | False | none_visible | manual | data_loader, run_u5_multiwindow, strategy | run_u5, run_sma34946 | strategy | — | data_loader, framework_adapter_backtrader, framework_adapter_freqtrade, run_u5, run_sma34946, strategy |
| `funding_carry_asym` | GRAVEYARD | 2 | False | none_visible | manual | build_signals, data_loader, prototype, test_build_signals, vpvr_hvn_persistence | — | prototype, strategy | — | build_signals, data_loader, framework_adapter_vectorbt, prototype, run_backtest, sma34928_low_threshold, sweep_top_combo, test_build_signals, vpvr_hvn_persistence |
| `funding_oscillator_mr` | GRAVEYARD | 2 | False | none_visible | manual | data_loader, strategy | run_u5noncarry | strategy | — | data_loader, run_u5noncarry |
| `sma_34925_btc_funding_delta` | GRAVEYARD | 0 | False | none_visible | manual | backtest_backtrader | backtest_inhouse | — | backtest_backtrader | backtest_backtrader, backtest_inhouse, backtest_vectorbt |
| `vpvr_macro_calendar_4h_20260715` | GRAVEYARD | 2 | False | none_visible | none | build_signals, framework_adapter_vectorbt, test_signals | — | strategy, test_signals | macro_calendar | framework_adapter_backtrader, framework_adapter_freqtrade, framework_adapter_vectorbt, run_backtest |
| `vpvr_onchain_proxy_1h_20260711` | GRAVEYARD | 2 | False | none_visible | none | strategy, test_indicators | — | strategy | — | — |
| `vpvr_options_gamma_1d_20260711` | GRAVEYARD | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_options_iv_skew_1d_20260713` | GRAVEYARD | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_options_iv_termstructure_4h_20260715` | GRAVEYARD | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | GRAVEYARD | 2 | False | none_visible | none | build_signals, test_signals | — | strategy | — | framework_adapter_freqtrade, run_backtest |
| `vpvr_stable_depeg_regime_4h_20260716` | GRAVEYARD | 3 | True | none_visible | none | build_signals, framework_adapter_backtrader, test_signals | — | strategy | — | framework_adapter_backtrader, framework_adapter_freqtrade, run_backtest |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | GRAVEYARD | 3 | True | none_visible | none | build_signals, framework_adapter_backtrader, test_signals | — | strategy | — | framework_adapter_backtrader, framework_adapter_freqtrade, run_backtest |
| `paper_trading_mtf_xs_pairs_eth_sol_20260719` | GRAVEYARD | 0 | True | _shared | manual | fill_engine | — | fill_engine | paper_runner | fill_engine, kill_criteria, paper_runner |
| `vpvr_funding_asym_4h_20260713` | GRAVEYARD | 2 | False | none_visible | none | data_loader, strategy | — | strategy, test_strategy | — | framework_adapter_vectorbt |
| `vpvr_funding_aware_v1_20260711` | GRAVEYARD | 2 | False | none_visible | none | data_loader, strategy, test_strategy | — | strategy, test_strategy | — | audit_maxdd, framework_adapter_vectorbt, strategy, test_strategy, walk_forward |
| `vpvr_funding_carry_asym_v2_20260718` | GRAVEYARD | 2 | True | _shared | _shared | build_signals, data_loader, funding_signal, test_funding_signal, test_state_machine, trend_filter, vpvr_levels_band | — | state_machine | — | data_loader, funding_signal, run_backtest, state_machine, test_state_machine, test_vpvr_band |
| `vpvr_funding_delta_1h_20260711` | GRAVEYARD | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_funding_delta_1h_asym_20260711` | GRAVEYARD | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_funding_delta_1h_mtf_20260711` | GRAVEYARD | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_funding_delta_1h_pair_20260711` | GRAVEYARD | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_funding_hvn_lvn_20260718` | GRAVEYARD | 1 | False | none_visible | none | vpvr_funding_hvn_lvn_backtest | — | vpvr_funding_hvn_lvn_backtest | — | vpvr_funding_hvn_lvn_backtest |
| `vpvr_funding_hvn_lvn_confluence_20260718` | GRAVEYARD | 1 | False | none_visible | none | framework_adapter_backtrader, vpvr_funding_hvn_lvn_confluence_backtest | — | vpvr_funding_hvn_lvn_confluence_backtest | — | framework_adapter_backtrader, vpvr_funding_hvn_lvn_confluence_backtest |
| `vpvr_funding_regime_15m_20260711` | GRAVEYARD | 2 | False | none_visible | none | data_loader, strategy, test_indicators | — | strategy | — | — |
| `vpvr_funding_reset_window_1h_20260715` | GRAVEYARD | 2 | False | none_visible | none | data_loader, strategy, test_v3_basic | — | strategy, test_v3_basic | — | — |
| `vpvr_funding_term_curve_1h_20260714` | GRAVEYARD | 2 | False | none_visible | none | data_loader, strategy, test_strategy | — | strategy | strategy, test_strategy | framework_adapter_freqtrade, framework_adapter_vectorbt |
| `cointegration_pairs_vpvr_poc_4h_20260714` | GRAVEYARD | 0 | False | none_visible | none | — | — | — | — | framework_adapter_vectorbt |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | GRAVEYARD | 3 | True | none_visible | none | framework_adapter_freqtrade, strategy, test_v3_pair_zscore | framework_adapter_freqtrade | strategy, test_v3_pair_zscore | strategy | framework_adapter_backtrader, framework_adapter_freqtrade, run_optimize_cpcv, strategy, test_v3_pair_zscore |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | GRAVEYARD | 2 | False | none_visible | manual | cpcv_optimize, strategy, test_v3_pair_zscore | — | strategy, test_v3_pair_zscore | — | cpcv_optimize, framework_adapter_freqtrade, strategy, test_v3_pair_zscore, test_walk_forward, walk_forward |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | GRAVEYARD | 2 | False | none_visible | manual | data_loader, strategy, test_v3_pair_zscore | — | strategy, test_v3_pair_zscore | — | framework_adapter_backtrader, framework_adapter_freqtrade, strategy, test_v3_pair_zscore, test_walk_forward, walk_forward |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | GRAVEYARD | 2 | False | none_visible | none | data_loader, framework_adapter_backtrader, strategy | — | strategy | — | framework_adapter_backtrader, framework_adapter_freqtrade, strategy, walk_forward |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | GRAVEYARD | 2 | False | none_visible | none | data_loader, framework_adapter_backtrader, strategy | — | strategy | — | framework_adapter_backtrader, framework_adapter_freqtrade, strategy, walk_forward |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | GRAVEYARD | 2 | False | none_visible | manual | data_loader, strategy, test_v3_pair_zscore | — | strategy, test_v3_pair_zscore | — | framework_adapter_backtrader, framework_adapter_freqtrade, strategy, test_v3_pair_zscore, test_walk_forward, walk_forward |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_backtrader_20260717` | GRAVEYARD | 0 | False | none_visible | none | — | — | — | — | — |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | GRAVEYARD | 2 | False | none_visible | manual | data_loader, strategy, test_v3_pair_zscore | — | strategy, test_v3_pair_zscore | — | framework_adapter_backtrader, framework_adapter_freqtrade, strategy, test_v3_pair_zscore, test_walk_forward, walk_forward |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | GRAVEYARD | 2 | False | none_visible | manual | data_loader, strategy, test_v3_pair_zscore | — | strategy, test_v3_pair_zscore | — | framework_adapter_backtrader, framework_adapter_freqtrade, strategy, test_v3_pair_zscore, test_walk_forward, walk_forward |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | GRAVEYARD | 2 | False | none_visible | manual | data_loader, strategy, test_v3_pair_zscore | framework_adapter_backtrader, framework_adapter_freqtrade | strategy, test_v3_pair_zscore | — | framework_adapter_backtrader, framework_adapter_freqtrade, strategy, test_v3_pair_zscore, test_walk_forward, walk_forward |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | GRAVEYARD | 2 | False | none_visible | none | data_loader, strategy | framework_adapter_backtrader, framework_adapter_freqtrade | strategy | — | framework_adapter_backtrader, framework_adapter_freqtrade, strategy, walk_forward |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | GRAVEYARD | 2 | False | none_visible | none | data_loader, strategy | framework_adapter_backtrader, framework_adapter_freqtrade | strategy | — | framework_adapter_backtrader, framework_adapter_freqtrade, strategy, walk_forward |

## 2. Reusable-module candidates

Functions / classes that look generic enough to move into `_shared/`.

| Strategy | File | Symbol | Category | Move to _shared? | Note |
|----------|------|--------|----------|------------------|------|

## 3. Cautionary / strategy-specific modules (do NOT move)

| Strategy | File | Symbol | Why |
|----------|------|--------|-----|
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/data_loader.py` | `SourceManifest` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/data_loader.py` | `_sha256` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/data_loader.py` | `build_source_manifest` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/data_loader.py` | `_read_1m` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/data_loader.py` | `_resample_1d` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/data_loader.py` | `load_symbol_1d` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/data_loader.py` | `load_all` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/data_loader.py` | `main` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/run_backtest.py` | `_trade_rows` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/run_backtest.py` | `main` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | `true_range` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | `wilder_atr` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | `wilder_adx` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | `donchian_upper` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | `donchian_lower` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | `annotate` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | `Trade` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | `BacktestResult` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | `_exit_on_bar` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | `run_backtest` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | `_summarize` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | `baseline_hold` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_data_loader.py` | `test_1d_cache_has_expected_schema` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_data_loader.py` | `test_1d_close_matches_last_1m_close` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_data_loader.py` | `test_manifest_matches_actual_source_sha256` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_data_loader.py` | `test_all_config_instruments_loadable` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `_cfg` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `_build_fixture` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `test_true_range_first_bar_equals_high_minus_low` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `test_wilder_atr_matches_expected_shape` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `test_wilder_adx_emits_values_after_seed` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `test_donchian_upper_no_lookahead` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `test_donchian_lower_symmetric` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `test_annotate_emits_expected_columns` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `test_long_entry_fires_in_trending_fixture` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `test_run_backtest_produces_trade_and_equity_curve` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `test_baseline_hold_matches_pnl_math` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `test_run_backtest_records_exit_reason` | hardcoded params / one-off |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/tests/test_strategy.py` | `test_run_backtest_handles_flat_input` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/build_signals.py` | `_atr` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/build_signals.py` | `_vpvr_snapshot_levels` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/build_signals.py` | `_shifted_snapshot_per_bar` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/build_signals.py` | `build_signals_1m` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/build_signals.py` | `build_signals_15m` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/build_signals.py` | `build_signals_4h` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/build_signals.py` | `build_signals` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/combine_signals.py` | `_is_allowed` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/combine_signals.py` | `_align_to_1m` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/combine_signals.py` | `_vote_per_tf` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/combine_signals.py` | `_resolve_conflicts_and_cascade` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/combine_signals.py` | `_count_agree` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/combine_signals.py` | `combine_signals` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/data_loader.py` | `_load_ohlcv` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/data_loader.py` | `_load_funding` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/data_loader.py` | `_attach_funding` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/data_loader.py` | `load_tf` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/data_loader.py` | `load_all` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/run_backtest.py` | `_daily_resampled_sharpe` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/run_backtest.py` | `_compute_metrics` | hardcoded params / one-off |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/run_backtest.py` | `_slice_window` | hardcoded params / one-off |
