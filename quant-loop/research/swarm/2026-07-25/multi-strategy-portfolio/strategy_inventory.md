# Old-strategy module inventory

Generated from `strategies` (active + graveyard).

## Strategy overview

| Strategy | Status | Family | TF | Entry points | Modules | Portable | Cautionary |
|----------|--------|--------|----|--------------|---------|----------|------------|
| `donchian_breakout_atr_1d_20260709` | ACTIVE | donchian_breakout | 1d | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; strategy.py; __init__.py; test_data_loader.py; test_strategy.py | 2 | 4 |
| `impl_vpvr_multi_tf_funding` | ACTIVE | impl_vpvr | ? | run_backtest.py; strategy.py | build_signals.py; combine_signals.py; data_loader.py; run_backtest.py; strategy.py; __init__.py; test_build_signals.py; test_combine_signals.py | 5 | 3 |
| `large_order_iceberg_tape_20260718` | ACTIVE | large_order | ? | — | — | 0 | 0 |
| `loid_iceberg_v4_1m_20260720` | ACTIVE | loid_iceberg | 1m | backtest.py | analyze_param_scan.py; backtest.py; iceberg_detector.py; run_first_btc_90d.py; run_first_btc_90d_chunked.py; run_param_scan.py; run_param_scan_fast.py; run_param_scan_minimal.py; run_threshold_scan.py; save_composite.py; test_backtest.py; test_iceberg_detector.py | 4 | 8 |
| `loid_vpvr_confluence_20260717` | ACTIVE | loid_vpvr | ? | run_backtest.py; strategy.py | build_signals.py; run_backtest.py; run_g1g7_backtest.py; strategy.py | 1 | 3 |
| `momentum_intraday_fast_15m_btc_20260712` | ACTIVE | momentum_intraday | 15m | — | data_loader.py | 0 | 1 |
| `momentum_trend_btc_only_softer_stop_1h_20260712` | ACTIVE | momentum_trend | 1h | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; run_backtest.py; strategy.py; __init__.py; test_strategy.py; walk_forward.py | 2 | 5 |
| `momentum_trend_multi_tf_atr_scaled_1h_20260712` | ACTIVE | momentum_trend | 1h | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; strategy.py; __init__.py; test_strategy.py; walk_forward.py | 2 | 4 |
| `momentum_trend_multi_tf_atr_scaled_v2_1h_20260712` | ACTIVE | momentum_trend | 1h | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; strategy.py; __init__.py; test_strategy.py; walk_forward.py | 2 | 4 |
| `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712` | ACTIVE | momentum_trend | 1h | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; run_backtest.py; strategy.py; __init__.py; test_strategy.py; walk_forward.py | 2 | 5 |
| `mtf_h2_vpvr_edge_1m_15m_2h_20260718` | ACTIVE | mtf_h2 | 1m | — | — | 0 | 0 |
| `mtf_vpvr_edge_zscore_1m_15m_2h_20260718` | ACTIVE | mtf_vpvr | 1m | run_backtest.py; smoke_test.py; strategy.py | data_loader.py; diagnose.py; inspect_full.py; inspect_trades.py; run_backtest.py; smoke_test.py; strategy.py | 0 | 7 |
| `mtf_xs_pairs_1m_15m_2h_h1_20260718` | ACTIVE | mtf_xs | 1m | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; strategy.py; test_h1_strategy_20260718.py; walk_forward.py | 1 | 4 |
| `mtf_xs_pairs_1m_15m_2h_h2_20260718` | ACTIVE | mtf_xs | 1m | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; strategy.py; walk_forward.py | 1 | 3 |
| `mtf_xs_pairs_1m_15m_2h_h3_20260718` | ACTIVE | mtf_xs | 1m | strategy.py | data_loader.py; framework_validate.py; sizing_sweep.py; strategy.py; write_winner_trades.py | 1 | 4 |
| `mtf_xs_pairs_1m_15m_2h_h4_20260718` | ACTIVE | mtf_xs | 1m | — | — | 0 | 0 |
| `pairs_cointegration_1d_20260709` | ACTIVE | pairs_cointegration | 1d | backtest.py; run_backtest.py; strategy.py | backtest.py; cointegration.py; conftest.py; data_loader.py; framework_adapter_freqtrade.py; optimize.py; portfolio.py; run_backtest.py; strategy.py; __init__.py; _synthetic.py; test_backtest.py; test_cointegration_eg.py; test_cointegration_ols.py; test_cointegration_rolling.py; test_data_loader.py; test_portfolio.py; test_strategy.py; walk_forward.py | 7 | 12 |
| `trend_funding_drift_4h_1h_20260714` | ACTIVE | trend_funding | 1h | — | — | 0 | 0 |
| `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714` | ACTIVE | trend_multi | 15m | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; strategy.py; test_backtest_outputs.py; walk_forward.py | 2 | 3 |
| `trend_regime_gate_1d_adx_4h_1h_20260714` | ACTIVE | trend_regime | 1h | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; strategy.py; __init__.py; test_strategy.py; walk_forward.py | 2 | 4 |
| `vol_breakout_2tf_vpvr_confluence_4h_20260712` | ACTIVE | vol_breakout | 4h | run_backtest.py; strategy.py | data_loader.py; indicators.py; run_backtest.py; strategy.py; __init__.py; test_basic.py; walk_forward.py | 2 | 5 |
| `vol_breakout_vpvr_regime_blend_4h_20260714` | ACTIVE | vol_breakout | 4h | — | — | 0 | 0 |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | ACTIVE | vol_breakout | 5m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; b6_aggregate.py; b6_bootstrap.py; b6_fwer.py; strategy.py; test_b3_artifacts.py; walk_forward.py | 1 | 9 |
| `vpvr_carry_term_8h_20260711` | ACTIVE | vpvr_carry_term | 8h | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; framework_adapter_vectorbt.py; run_backtest.py; strategy.py; test_framework_adapter_backtrader.py; test_strategy.py | 0 | 8 |
| `vpvr_edge_zscore_15m_only_20260720` | ACTIVE | vpvr_edge_zscore | 15m | run_backtest.py; smoke_backtest.py; strategy.py | run_backtest.py; smoke_backtest.py; strategy.py | 0 | 3 |
| `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720` | ACTIVE | vpvr_edge_zscore | 1m | run_backtest.py; smoke_backtest.py; strategy.py | build_signals.py; data_loader.py; run_backtest.py; smoke_backtest.py; strategy.py; __init__.py; test_signals.py | 1 | 6 |
| `vpvr_inverse_reversion_4h_funding_filter_20260712` | ACTIVE | vpvr_inverse_reversion | 4h | — | framework_adapter_freqtrade.py | 0 | 1 |
| `vpvr_liquidation_heatmap_15m_20260714` | ACTIVE | vpvr_liquidation_heatmap | 15m | — | — | 0 | 0 |
| `vpvr_micro_reversion_1h_funding_filter_20260710` | ACTIVE | vpvr_micro_reversion | 1h | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; strategy.py; __init__.py; test_data_loader.py; test_strategy.py | 2 | 4 |
| `vpvr_mtf_consensus_v2_4h_20260714` | ACTIVE | vpvr_mtf_consensus | 4h | — | — | 0 | 0 |
| `vpvr_oi_divergence_4h_20260713` | ACTIVE | vpvr_oi_divergence | 4h | — | — | 0 | 0 |
| `vpvr_regime_blend_4h_20260714` | ACTIVE | vpvr_regime_blend | 4h | — | — | 0 | 0 |
| `vpvr_regime_reversion_4h_vol_switch_20260710` | ACTIVE | vpvr_regime_reversion | 4h | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; strategy.py; test_strategy.py | 0 | 4 |
| `vpvr_reversal_check_20260717` | ACTIVE | vpvr_reversal_check | ? | — | run_cross_check.py | 0 | 1 |
| `vpvr_reversion_15m_donchian_regime_20260709` | ACTIVE | vpvr_reversion_15m | 15m | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; strategy.py; test_data_loader.py; test_strategy.py | 1 | 4 |
| `vpvr_reversion_15m_vrp_filter_20260713` | ACTIVE | vpvr_reversion_15m | 15m | — | — | 0 | 0 |
| `vpvr_reversion_4h_stablecoin_netflow_20260713` | ACTIVE | vpvr_reversion_4h | 4h | — | — | 0 | 0 |
| `vpvr_reversion_5m_vwap_trail_20260709` | ACTIVE | vpvr_reversion_5m | 5m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_vectorbt.py; run_backtest.py; strategy.py; test_data_loader.py; test_strategy.py | 1 | 5 |
| `vpvr_tod_session_filter_15m_20260715` | ACTIVE | vpvr_tod_session | 15m | run_backtest.py; strategy.py | build_signals.py; data_loader.py; run_backtest.py; strategy.py; test_signals.py; tod_calendar.py | 2 | 4 |
| `vpvr_volume_edge_3tf_v1_20260711` | ACTIVE | vpvr_volume_edge | 1m | run_backtest.py; strategy.py | data_loader.py; rebuild_summary.py; run_backtest.py; strategy.py; test_backtest_outputs.py; walk_forward.py | 3 | 3 |
| `vpvr_xs_basis_15m_cross_exchange_20260713` | ACTIVE | vpvr_xs_basis | 15m | — | — | 0 | 0 |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | ACTIVE | vpvr_xs_basis | 15m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; run_cpcv.py; strategy.py; test_v72_basis_zscore.py; walk_forward.py | 1 | 7 |
| `vpvr_xs_corr_breakdown_4h_20260714` | ACTIVE | vpvr_xs_corr | 4h | — | — | 0 | 0 |
| `vpvr_xs_reversion_1d_momentum_filter_20260709` | ACTIVE | vpvr_xs_reversion | 1d | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; strategy.py; test_data_loader.py; test_strategy.py | 1 | 4 |
| `vpvr_xs_smart_routing_15m_20260715` | ACTIVE | vpvr_xs_smart | 15m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; test_v3_basic.py | 0 | 6 |
| `xs_momentum_rank_1d_20260709` | ACTIVE | xs_momentum | 1d | backtest.py; run_backtest.py; strategy.py | backtest.py; data_loader.py; portfolio.py; run_backtest.py; strategy.py; __init__.py; test_backtest.py; test_performance_report.py; test_portfolio.py; test_strategy.py; test_universe.py; test_walk_forward.py; universe.py; walk_forward.py | 7 | 7 |
| `bb_reversion_rsi_1m_20260707` | GRAVEYARD | bb_reversion | 1m | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; strategy.py; __init__.py; test_data_loader.py; test_strategy.py | 2 | 4 |
| `bb_reversion_rsi_1m_20260707_p3opt_049` | GRAVEYARD | bb_reversion | 1m | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; run_cpcv.py; strategy.py; __init__.py; test_data_loader.py; test_strategy.py | 2 | 5 |
| `bb_reversion_rsi_1m_20260707_p3opt_050` | GRAVEYARD | bb_reversion | 1m | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; run_cpcv.py; strategy.py | 0 | 4 |
| `bb_reversion_rsi_1m_20260707_p3opt_099` | GRAVEYARD | bb_reversion | 1m | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; run_cpcv.py; strategy.py; __init__.py; test_data_loader.py; test_strategy.py | 2 | 5 |
| `bb_reversion_rsi_1m_20260707_p3opt_100` | GRAVEYARD | bb_reversion | 1m | run_backtest.py; strategy.py | data_loader.py; run_backtest.py; run_cpcv.py; strategy.py; __init__.py; test_data_loader.py; test_strategy.py | 2 | 5 |
| `vol_breakout_1m_15m_vpvr_confluence_u6_20260718` | GRAVEYARD | vol_breakout | tf_dependent | run_backtest.py; strategy.py | data_loader.py; indicators.py; run_backtest.py; strategy.py | 1 | 3 |
| `vpvr_iceberg_fade_5m_20260711` | GRAVEYARD | vpvr_iceberg_fade | 5m | — | — | 0 | 0 |
| `vpvr_iceberg_fade_v2_5m_20260711` | GRAVEYARD | vpvr_iceberg_fade | 5m | — | framework_adapter_backtrader.py | 0 | 1 |
| `vpvr_microstructure_5m_volume_delta_20260710` | GRAVEYARD | vpvr_microstructure_5m | 5m | — | — | 0 | 0 |
| `vpvr_mtf_reversion_5m_consensus_20260710` | GRAVEYARD | vpvr_mtf_reversion | 5m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; test_strategy.py | 0 | 5 |
| `vpvr_obi_micro_v2_1m_20260714` | GRAVEYARD | vpvr_obi_micro | 1m | — | — | 0 | 0 |
| `vpvr_reversion_1m_kama_reversal_20260709` | GRAVEYARD | vpvr_reversion_1m | 1m | run_backtest.py; strategy.py | build_signals.py; data_loader.py; run_backtest.py; strategy.py; test_data_loader.py; test_signals.py; test_strategy.py | 2 | 5 |
| `vpvr_reversion_1m_volume_profile_break_20260709` | GRAVEYARD | vpvr_reversion_1m | 1m | run_backtest.py; strategy.py | build_signals.py; data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; test_data_loader.py; test_signals.py; test_strategy.py | 2 | 7 |
| `vpvr_sentiment_attention_1m_20260716` | GRAVEYARD | vpvr_sentiment_attention | 1m | run_backtest.py; strategy.py | build_signals.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; test_signals.py | 2 | 4 |
| `vpvr_xs_leadlag_5m_20260711` | GRAVEYARD | vpvr_xs_leadlag | 5m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; __init__.py; test_indicators.py | 2 | 5 |
| `funding_carry` | GRAVEYARD | funding_carry | ? | strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_framework_cv_multiwindow.py; run_u5.py; run_u5_multiwindow.py; data_loader.py; run_sma34946.py; strategy.py | 1 | 8 |
| `funding_carry_asym` | GRAVEYARD | funding_carry | ? | run_backtest.py; strategy.py | __init__.py; build_signals.py; data_loader.py; framework_adapter_vectorbt.py; prototype.py; run_backtest.py; sma34928_low_threshold.py; strategy.py; sweep_top_combo.py; __init__.py; test_build_signals.py; vpvr_hvn_persistence.py | 2 | 10 |
| `funding_oscillator_mr` | GRAVEYARD | funding_oscillator | ? | strategy.py | data_loader.py; run_u5noncarry.py; strategy.py | 0 | 3 |
| `sma_34925_btc_funding_delta` | GRAVEYARD | sma | ? | — | backtest_backtrader.py; backtest_inhouse.py; backtest_vectorbt.py; sensitivity_sweep.py | 0 | 4 |
| `vpvr_macro_calendar_4h_20260715` | GRAVEYARD | vpvr_macro_calendar | 4h | run_backtest.py; strategy.py | build_signals.py; data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; framework_adapter_vectorbt.py; macro_calendar.py; run_backtest.py; strategy.py; test_signals.py | 1 | 8 |
| `vpvr_onchain_proxy_1h_20260711` | GRAVEYARD | vpvr_onchain_proxy | 1h | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_vectorbt.py; run_backtest.py; strategy.py; __init__.py; test_indicators.py | 2 | 5 |
| `vpvr_options_gamma_1d_20260711` | GRAVEYARD | vpvr_options_gamma | 1d | — | — | 0 | 0 |
| `vpvr_options_iv_skew_1d_20260713` | GRAVEYARD | vpvr_options_iv | 1d | — | — | 0 | 0 |
| `vpvr_options_iv_termstructure_4h_20260715` | GRAVEYARD | vpvr_options_iv | 4h | — | — | 0 | 0 |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | GRAVEYARD | vpvr_options_putcall | 8h | run_backtest.py; strategy.py | build_signals.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; __init__.py; test_signals.py | 3 | 4 |
| `vpvr_stable_depeg_regime_4h_20260716` | GRAVEYARD | vpvr_stable_depeg | 4h | run_backtest.py; strategy.py | build_signals.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; run_cpcv.py; strategy.py; test_signals.py | 2 | 5 |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | GRAVEYARD | vpvr_stable_depeg | 4h | run_backtest.py; strategy.py | build_signals.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; run_cpcv.py; strategy.py; test_signals.py | 2 | 5 |
| `paper_trading_mtf_xs_pairs_eth_sol_20260719` | GRAVEYARD | paper | 30m | — | fill_engine.py; kill_criteria.py; paper_runner.py | 1 | 2 |
| `vpvr_funding_asym_4h_20260713` | GRAVEYARD | vpvr_funding_asym | 4h | run_backtest.py; strategy.py | __init__.py; data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; framework_adapter_vectorbt.py; run_backtest.py; strategy.py; test_strategy.py | 1 | 7 |
| `vpvr_funding_aware_v1_20260711` | GRAVEYARD | vpvr_funding_aware | 4h | run_backtest.py; strategy.py | audit_maxdd.py; data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; framework_adapter_vectorbt.py; run_backtest.py; strategy.py; test_strategy.py; walk_forward.py | 1 | 8 |
| `vpvr_funding_carry_asym_v2_20260718` | GRAVEYARD | vpvr_funding_carry | ? | run_backtest.py | build_signals.py; data_loader.py; funding_signal.py; run_backtest.py; run_cpcv.py; state_machine.py; __init__.py; test_funding_signal.py; test_state_machine.py; test_vpvr_band.py; trend_filter.py; vpvr_levels_band.py | 6 | 6 |
| `vpvr_funding_delta_1h_20260711` | GRAVEYARD | vpvr_funding_delta | 1h | — | — | 0 | 0 |
| `vpvr_funding_delta_1h_asym_20260711` | GRAVEYARD | vpvr_funding_delta | 1h | — | — | 0 | 0 |
| `vpvr_funding_delta_1h_mtf_20260711` | GRAVEYARD | vpvr_funding_delta | 1h | — | — | 0 | 0 |
| `vpvr_funding_delta_1h_pair_20260711` | GRAVEYARD | vpvr_funding_delta | 1h | — | — | 0 | 0 |
| `vpvr_funding_hvn_lvn_20260718` | GRAVEYARD | vpvr_funding_hvn | ? | — | vpvr_funding_hvn_lvn_backtest.py | 0 | 1 |
| `vpvr_funding_hvn_lvn_confluence_20260718` | GRAVEYARD | vpvr_funding_hvn | ? | — | framework_adapter_backtrader.py; vpvr_funding_hvn_lvn_confluence_backtest.py | 0 | 2 |
| `vpvr_funding_regime_15m_20260711` | GRAVEYARD | vpvr_funding_regime | 15m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_vectorbt.py; run_backtest.py; strategy.py; __init__.py; test_indicators.py | 2 | 5 |
| `vpvr_funding_reset_window_1h_20260715` | GRAVEYARD | vpvr_funding_reset | 1h | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; framework_adapter_vectorbt.py; run_backtest.py; strategy.py; __init__.py; test_v3_basic.py | 1 | 7 |
| `vpvr_funding_term_curve_1h_20260714` | GRAVEYARD | vpvr_funding_term | 1h | run_backtest.py; strategy.py | __init__.py; data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; framework_adapter_vectorbt.py; run_backtest.py; strategy.py; __init__.py; test_strategy.py | 2 | 7 |
| `cointegration_pairs_vpvr_poc_4h_20260714` | GRAVEYARD | cointegration | 4h | — | framework_adapter_vectorbt.py | 0 | 1 |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | GRAVEYARD | vpvr_xs_pairs | 4h | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; run_optimize_cpcv.py; strategy.py; __init__.py; test_v3_pair_zscore.py; walk_forward.py | 3 | 6 |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | GRAVEYARD | vpvr_xs_pairs | 4h | run_backtest.py; strategy.py | build_deliverables.py; cpcv_optimize.py; data_loader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; test_v3_pair_zscore.py; test_walk_forward.py; walk_forward.py | 1 | 8 |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | GRAVEYARD | vpvr_xs_pairs | 30m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; test_v3_pair_zscore.py; test_walk_forward.py; walk_forward.py | 1 | 7 |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | GRAVEYARD | vpvr_xs_pairs | 30m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; walk_forward.py | 0 | 6 |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | GRAVEYARD | vpvr_xs_pairs | 30m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; walk_forward.py | 0 | 6 |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | GRAVEYARD | vpvr_xs_pairs | 30m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; test_v3_pair_zscore.py; test_walk_forward.py; walk_forward.py | 1 | 7 |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_backtrader_20260717` | GRAVEYARD | vpvr_xs_pairs | 30m | — | — | 0 | 0 |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | GRAVEYARD | vpvr_xs_pairs | 30m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; test_v3_pair_zscore.py; test_walk_forward.py; walk_forward.py | 1 | 7 |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | GRAVEYARD | vpvr_xs_pairs | 30m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; test_v3_pair_zscore.py; test_walk_forward.py; walk_forward.py | 1 | 7 |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | GRAVEYARD | vpvr_xs_pairs | 30m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; test_v3_pair_zscore.py; test_walk_forward.py; walk_forward.py | 1 | 7 |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | GRAVEYARD | vpvr_xs_pairs | 30m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; walk_forward.py | 0 | 6 |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | GRAVEYARD | vpvr_xs_pairs | 30m | run_backtest.py; strategy.py | data_loader.py; framework_adapter_backtrader.py; framework_adapter_freqtrade.py; run_backtest.py; strategy.py; walk_forward.py | 0 | 6 |

## Reusable module catalog

### Signal Generation

| Strategy | Module | Movability | Reason |
|----------|--------|------------|--------|
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/build_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/combine_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/tests/test_build_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/tests/test_combine_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `loid_iceberg_v4_1m_20260720` | `strategies/loid_iceberg_v4_1m_20260720/iceberg_detector.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `loid_iceberg_v4_1m_20260720` | `strategies/loid_iceberg_v4_1m_20260720/tests/test_iceberg_detector.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `loid_vpvr_confluence_20260717` | `strategies/loid_vpvr_confluence_20260717/build_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `loid_vpvr_confluence_20260717` | `strategies/loid_vpvr_confluence_20260717/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `loid_vpvr_confluence_20260717` | `strategies/loid_vpvr_confluence_20260717/run_g1g7_backtest.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `loid_vpvr_confluence_20260717` | `strategies/loid_vpvr_confluence_20260717/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `mtf_vpvr_edge_zscore_1m_15m_2h_20260718` | `strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `mtf_vpvr_edge_zscore_1m_15m_2h_20260718` | `strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/diagnose.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `mtf_vpvr_edge_zscore_1m_15m_2h_20260718` | `strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/inspect_full.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `mtf_vpvr_edge_zscore_1m_15m_2h_20260718` | `strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/inspect_trades.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `mtf_vpvr_edge_zscore_1m_15m_2h_20260718` | `strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `mtf_vpvr_edge_zscore_1m_15m_2h_20260718` | `strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/smoke_test.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `mtf_vpvr_edge_zscore_1m_15m_2h_20260718` | `strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `mtf_xs_pairs_1m_15m_2h_h1_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h1_20260718/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `mtf_xs_pairs_1m_15m_2h_h1_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h1_20260718/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `mtf_xs_pairs_1m_15m_2h_h1_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h1_20260718/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `mtf_xs_pairs_1m_15m_2h_h2_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h2_20260718/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `mtf_xs_pairs_1m_15m_2h_h2_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h2_20260718/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `mtf_xs_pairs_1m_15m_2h_h2_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h2_20260718/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `mtf_xs_pairs_1m_15m_2h_h3_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `mtf_xs_pairs_1m_15m_2h_h3_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/sizing_sweep.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `mtf_xs_pairs_1m_15m_2h_h3_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/write_winner_trades.py` | cautionary | contains hardcoded ticker symbols |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/backtest.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/cointegration.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/tests/test_cointegration_eg.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/tests/test_cointegration_ols.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/tests/test_cointegration_rolling.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vol_breakout_2tf_vpvr_confluence_4h_20260712` | `strategies/vol_breakout_2tf_vpvr_confluence_4h_20260712/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vol_breakout_2tf_vpvr_confluence_4h_20260712` | `strategies/vol_breakout_2tf_vpvr_confluence_4h_20260712/indicators.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vol_breakout_2tf_vpvr_confluence_4h_20260712` | `strategies/vol_breakout_2tf_vpvr_confluence_4h_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vol_breakout_2tf_vpvr_confluence_4h_20260712` | `strategies/vol_breakout_2tf_vpvr_confluence_4h_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vol_breakout_2tf_vpvr_confluence_4h_20260712` | `strategies/vol_breakout_2tf_vpvr_confluence_4h_20260712/tests/test_basic.py` | cautionary | contains hardcoded ticker symbols |
| `vol_breakout_2tf_vpvr_confluence_4h_20260712` | `strategies/vol_breakout_2tf_vpvr_confluence_4h_20260712/walk_forward.py` | cautionary | references strategy-local paths |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/scripts/b6_aggregate.py` | cautionary | references strategy-local paths |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/scripts/b6_bootstrap.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/scripts/b6_fwer.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/tests/test_b3_artifacts.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_carry_term_8h_20260711` | `strategies/vpvr_carry_term_8h_20260711/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_carry_term_8h_20260711` | `strategies/vpvr_carry_term_8h_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_carry_term_8h_20260711` | `strategies/vpvr_carry_term_8h_20260711/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_carry_term_8h_20260711` | `strategies/vpvr_carry_term_8h_20260711/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_carry_term_8h_20260711` | `strategies/vpvr_carry_term_8h_20260711/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_carry_term_8h_20260711` | `strategies/vpvr_carry_term_8h_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_carry_term_8h_20260711` | `strategies/vpvr_carry_term_8h_20260711/tests/test_strategy.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_edge_zscore_15m_only_20260720` | `strategies/vpvr_edge_zscore_15m_only_20260720/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_edge_zscore_15m_only_20260720` | `strategies/vpvr_edge_zscore_15m_only_20260720/smoke_backtest.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_edge_zscore_15m_only_20260720` | `strategies/vpvr_edge_zscore_15m_only_20260720/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720` | `strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/build_signals.py` | cautionary | references strategy-local paths |
| `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720` | `strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720` | `strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720` | `strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/smoke_backtest.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720` | `strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720` | `strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/tests/test_signals.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_inverse_reversion_4h_funding_filter_20260712` | `strategies/vpvr_inverse_reversion_4h_funding_filter_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_micro_reversion_1h_funding_filter_20260710` | `strategies/vpvr_micro_reversion_1h_funding_filter_20260710/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_micro_reversion_1h_funding_filter_20260710` | `strategies/vpvr_micro_reversion_1h_funding_filter_20260710/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_micro_reversion_1h_funding_filter_20260710` | `strategies/vpvr_micro_reversion_1h_funding_filter_20260710/tests/test_strategy.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_regime_reversion_4h_vol_switch_20260710` | `strategies/vpvr_regime_reversion_4h_vol_switch_20260710/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_regime_reversion_4h_vol_switch_20260710` | `strategies/vpvr_regime_reversion_4h_vol_switch_20260710/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_regime_reversion_4h_vol_switch_20260710` | `strategies/vpvr_regime_reversion_4h_vol_switch_20260710/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_regime_reversion_4h_vol_switch_20260710` | `strategies/vpvr_regime_reversion_4h_vol_switch_20260710/tests/test_strategy.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_reversal_check_20260717` | `strategies/vpvr_reversal_check_20260717/run_cross_check.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_15m_donchian_regime_20260709` | `strategies/vpvr_reversion_15m_donchian_regime_20260709/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_reversion_15m_donchian_regime_20260709` | `strategies/vpvr_reversion_15m_donchian_regime_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_reversion_15m_donchian_regime_20260709` | `strategies/vpvr_reversion_15m_donchian_regime_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_15m_donchian_regime_20260709` | `strategies/vpvr_reversion_15m_donchian_regime_20260709/tests/test_strategy.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_reversion_5m_vwap_trail_20260709` | `strategies/vpvr_reversion_5m_vwap_trail_20260709/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_reversion_5m_vwap_trail_20260709` | `strategies/vpvr_reversion_5m_vwap_trail_20260709/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_reversion_5m_vwap_trail_20260709` | `strategies/vpvr_reversion_5m_vwap_trail_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_reversion_5m_vwap_trail_20260709` | `strategies/vpvr_reversion_5m_vwap_trail_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_5m_vwap_trail_20260709` | `strategies/vpvr_reversion_5m_vwap_trail_20260709/tests/test_strategy.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_tod_session_filter_15m_20260715` | `strategies/vpvr_tod_session_filter_15m_20260715/build_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_tod_session_filter_15m_20260715` | `strategies/vpvr_tod_session_filter_15m_20260715/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_tod_session_filter_15m_20260715` | `strategies/vpvr_tod_session_filter_15m_20260715/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_tod_session_filter_15m_20260715` | `strategies/vpvr_tod_session_filter_15m_20260715/tests/test_signals.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_tod_session_filter_15m_20260715` | `strategies/vpvr_tod_session_filter_15m_20260715/tod_calendar.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_volume_edge_3tf_v1_20260711` | `strategies/vpvr_volume_edge_3tf_v1_20260711/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_volume_edge_3tf_v1_20260711` | `strategies/vpvr_volume_edge_3tf_v1_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_volume_edge_3tf_v1_20260711` | `strategies/vpvr_volume_edge_3tf_v1_20260711/tests/test_backtest_outputs.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_volume_edge_3tf_v1_20260711` | `strategies/vpvr_volume_edge_3tf_v1_20260711/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/run_cpcv.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/tests/test_v72_basis_zscore.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_reversion_1d_momentum_filter_20260709` | `strategies/vpvr_xs_reversion_1d_momentum_filter_20260709/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_reversion_1d_momentum_filter_20260709` | `strategies/vpvr_xs_reversion_1d_momentum_filter_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_reversion_1d_momentum_filter_20260709` | `strategies/vpvr_xs_reversion_1d_momentum_filter_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_reversion_1d_momentum_filter_20260709` | `strategies/vpvr_xs_reversion_1d_momentum_filter_20260709/tests/test_strategy.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_smart_routing_15m_20260715` | `strategies/vpvr_xs_smart_routing_15m_20260715/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_smart_routing_15m_20260715` | `strategies/vpvr_xs_smart_routing_15m_20260715/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_smart_routing_15m_20260715` | `strategies/vpvr_xs_smart_routing_15m_20260715/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_smart_routing_15m_20260715` | `strategies/vpvr_xs_smart_routing_15m_20260715/tests/test_v3_basic.py` | cautionary | contains hardcoded ticker symbols |
| `xs_momentum_rank_1d_20260709` | `strategies/xs_momentum_rank_1d_20260709/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `xs_momentum_rank_1d_20260709` | `strategies/xs_momentum_rank_1d_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `bb_reversion_rsi_1m_20260707` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `bb_reversion_rsi_1m_20260707_p3opt_049` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_049/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `bb_reversion_rsi_1m_20260707_p3opt_050` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_050/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `bb_reversion_rsi_1m_20260707_p3opt_099` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_099/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `bb_reversion_rsi_1m_20260707_p3opt_100` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_100/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vol_breakout_1m_15m_vpvr_confluence_u6_20260718` | `strategies/_graveyard/1m_klines_reversal/vol_breakout_1m_15m_vpvr_confluence_u6_20260718/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vol_breakout_1m_15m_vpvr_confluence_u6_20260718` | `strategies/_graveyard/1m_klines_reversal/vol_breakout_1m_15m_vpvr_confluence_u6_20260718/indicators.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vol_breakout_1m_15m_vpvr_confluence_u6_20260718` | `strategies/_graveyard/1m_klines_reversal/vol_breakout_1m_15m_vpvr_confluence_u6_20260718/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vol_breakout_1m_15m_vpvr_confluence_u6_20260718` | `strategies/_graveyard/1m_klines_reversal/vol_breakout_1m_15m_vpvr_confluence_u6_20260718/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_iceberg_fade_v2_5m_20260711` | `strategies/_graveyard/1m_klines_reversal/vpvr_iceberg_fade_v2_5m_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_mtf_reversion_5m_consensus_20260710` | `strategies/_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_mtf_reversion_5m_consensus_20260710` | `strategies/_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_mtf_reversion_5m_consensus_20260710` | `strategies/_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_mtf_reversion_5m_consensus_20260710` | `strategies/_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_mtf_reversion_5m_consensus_20260710` | `strategies/_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/tests/test_strategy.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_reversion_1m_kama_reversal_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/build_signals.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_reversion_1m_kama_reversal_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_reversion_1m_kama_reversal_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_reversion_1m_kama_reversal_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_1m_kama_reversal_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/tests/test_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_reversion_1m_kama_reversal_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/tests/test_strategy.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_reversion_1m_volume_profile_break_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/build_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_reversion_1m_volume_profile_break_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_reversion_1m_volume_profile_break_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_reversion_1m_volume_profile_break_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_reversion_1m_volume_profile_break_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_reversion_1m_volume_profile_break_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_1m_volume_profile_break_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/tests/test_signals.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_reversion_1m_volume_profile_break_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/tests/test_strategy.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_sentiment_attention_1m_20260716` | `strategies/_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/build_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_sentiment_attention_1m_20260716` | `strategies/_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_sentiment_attention_1m_20260716` | `strategies/_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_sentiment_attention_1m_20260716` | `strategies/_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_sentiment_attention_1m_20260716` | `strategies/_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_sentiment_attention_1m_20260716` | `strategies/_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/tests/test_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_leadlag_5m_20260711` | `strategies/_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_leadlag_5m_20260711` | `strategies/_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_leadlag_5m_20260711` | `strategies/_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_leadlag_5m_20260711` | `strategies/_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_leadlag_5m_20260711` | `strategies/_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/tests/test_indicators.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/run_framework_cv_multiwindow.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/run_u5.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/run_u5_multiwindow.py` | cautionary | contains hardcoded ticker symbols |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/sma34946/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/sma34946/run_sma34946.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/build_signals.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/prototype.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/sma34928_low_threshold.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/sweep_top_combo.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/tests/test_build_signals.py` | cautionary | references strategy-local paths |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/vpvr_hvn_persistence.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_oscillator_mr` | `strategies/_graveyard/funding_carry/funding_oscillator_mr/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `funding_oscillator_mr` | `strategies/_graveyard/funding_carry/funding_oscillator_mr/run_u5noncarry.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_oscillator_mr` | `strategies/_graveyard/funding_carry/funding_oscillator_mr/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `sma_34925_btc_funding_delta` | `strategies/_graveyard/funding_carry/sma_34925_btc_funding_delta/backtest_backtrader.py` | cautionary | contains hardcoded ticker symbols |
| `sma_34925_btc_funding_delta` | `strategies/_graveyard/funding_carry/sma_34925_btc_funding_delta/backtest_inhouse.py` | cautionary | contains hardcoded ticker symbols |
| `sma_34925_btc_funding_delta` | `strategies/_graveyard/funding_carry/sma_34925_btc_funding_delta/backtest_vectorbt.py` | cautionary | contains hardcoded ticker symbols |
| `sma_34925_btc_funding_delta` | `strategies/_graveyard/funding_carry/sma_34925_btc_funding_delta/sensitivity_sweep.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/build_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/tests/test_signals.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_onchain_proxy_1h_20260711` | `strategies/_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_onchain_proxy_1h_20260711` | `strategies/_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_onchain_proxy_1h_20260711` | `strategies/_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_onchain_proxy_1h_20260711` | `strategies/_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_onchain_proxy_1h_20260711` | `strategies/_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/tests/test_indicators.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/build_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/tests/test_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/build_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/run_cpcv.py` | cautionary | references strategy-local paths |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/tests/test_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/build_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/run_cpcv.py` | cautionary | references strategy-local paths |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/tests/test_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `paper_trading_mtf_xs_pairs_eth_sol_20260719` | `strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/fill_engine.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/tests/test_strategy.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/audit_maxdd.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/tests/test_strategy.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/build_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/funding_signal.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/run_cpcv.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/state_machine.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/tests/test_funding_signal.py` | cautionary | references strategy-local paths |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/tests/test_state_machine.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/tests/test_vpvr_band.py` | cautionary | references strategy-local paths |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/trend_filter.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/vpvr_levels_band.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_funding_hvn_lvn_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_hvn_lvn_20260718/vpvr_funding_hvn_lvn_backtest.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_funding_hvn_lvn_confluence_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_hvn_lvn_confluence_20260718/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_hvn_lvn_confluence_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_hvn_lvn_confluence_20260718/vpvr_funding_hvn_lvn_confluence_backtest.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_funding_regime_15m_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_funding_regime_15m_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_regime_15m_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_regime_15m_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_regime_15m_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_funding_regime_15m_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/tests/test_indicators.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/tests/test_v3_basic.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/tests/test_strategy.py` | cautionary | contains hardcoded ticker symbols |
| `cointegration_pairs_vpvr_poc_4h_20260714` | `strategies/_graveyard/vpvr_xs_pairs_4h/cointegration_pairs_vpvr_poc_4h_20260714/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/run_optimize_cpcv.py` | cautionary | references strategy-local paths |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/tests/test_v3_pair_zscore.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/build_deliverables.py` | cautionary | references strategy-local paths |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/cpcv_optimize.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/tests/test_v3_pair_zscore.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/walk_forward.py` | cautionary | references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/tests/test_v3_pair_zscore.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/tests/test_v3_pair_zscore.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/tests/test_v3_pair_zscore.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/tests/test_v3_pair_zscore.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/tests/test_v3_pair_zscore.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |

### Risk Sizing

| Strategy | Module | Movability | Reason |
|----------|--------|------------|--------|
| `mtf_xs_pairs_1m_15m_2h_h3_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/sizing_sweep.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/portfolio.py` | cautionary | contains hardcoded ticker symbols |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/tests/test_portfolio.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `xs_momentum_rank_1d_20260709` | `strategies/xs_momentum_rank_1d_20260709/portfolio.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `xs_momentum_rank_1d_20260709` | `strategies/xs_momentum_rank_1d_20260709/tests/test_portfolio.py` | portable | generic helper; no hardcoded symbols/strategy paths |

### Execution

| Strategy | Module | Movability | Reason |
|----------|--------|------------|--------|
| `paper_trading_mtf_xs_pairs_eth_sol_20260719` | `strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/fill_engine.py` | portable | generic helper; no hardcoded symbols/strategy paths |

### Cost

| Strategy | Module | Movability | Reason |
|----------|--------|------------|--------|
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `loid_iceberg_v4_1m_20260720` | `strategies/loid_iceberg_v4_1m_20260720/backtest.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `loid_iceberg_v4_1m_20260720` | `strategies/loid_iceberg_v4_1m_20260720/tests/test_backtest.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `loid_vpvr_confluence_20260717` | `strategies/loid_vpvr_confluence_20260717/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `momentum_trend_btc_only_softer_stop_1h_20260712` | `strategies/momentum_trend_btc_only_softer_stop_1h_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `momentum_trend_btc_only_softer_stop_1h_20260712` | `strategies/momentum_trend_btc_only_softer_stop_1h_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `momentum_trend_multi_tf_atr_scaled_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_1h_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `momentum_trend_multi_tf_atr_scaled_v2_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_v2_1h_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_v3_1h_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `mtf_vpvr_edge_zscore_1m_15m_2h_20260718` | `strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `mtf_xs_pairs_1m_15m_2h_h1_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h1_20260718/tests/test_h1_strategy_20260718.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `mtf_xs_pairs_1m_15m_2h_h3_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/framework_validate.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `mtf_xs_pairs_1m_15m_2h_h3_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/sizing_sweep.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `mtf_xs_pairs_1m_15m_2h_h3_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/write_winner_trades.py` | cautionary | contains hardcoded ticker symbols |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/optimize.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/tests/test_backtest.py` | cautionary | references strategy-local paths |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/tests/test_data_loader.py` | cautionary | typically hardcodes symbols/paths for the strategy |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/tests/test_portfolio.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/tests/test_strategy.py` | cautionary | contains hardcoded ticker symbols |
| `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714` | `strategies/trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `trend_regime_gate_1d_adx_4h_1h_20260714` | `strategies/trend_regime_gate_1d_adx_4h_1h_20260714/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vol_breakout_2tf_vpvr_confluence_4h_20260712` | `strategies/vol_breakout_2tf_vpvr_confluence_4h_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vol_breakout_2tf_vpvr_confluence_4h_20260712` | `strategies/vol_breakout_2tf_vpvr_confluence_4h_20260712/tests/test_basic.py` | cautionary | contains hardcoded ticker symbols |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_carry_term_8h_20260711` | `strategies/vpvr_carry_term_8h_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_carry_term_8h_20260711` | `strategies/vpvr_carry_term_8h_20260711/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_carry_term_8h_20260711` | `strategies/vpvr_carry_term_8h_20260711/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_carry_term_8h_20260711` | `strategies/vpvr_carry_term_8h_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_edge_zscore_15m_only_20260720` | `strategies/vpvr_edge_zscore_15m_only_20260720/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720` | `strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720` | `strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/tests/test_signals.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_micro_reversion_1h_funding_filter_20260710` | `strategies/vpvr_micro_reversion_1h_funding_filter_20260710/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_regime_reversion_4h_vol_switch_20260710` | `strategies/vpvr_regime_reversion_4h_vol_switch_20260710/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_15m_donchian_regime_20260709` | `strategies/vpvr_reversion_15m_donchian_regime_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_5m_vwap_trail_20260709` | `strategies/vpvr_reversion_5m_vwap_trail_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_tod_session_filter_15m_20260715` | `strategies/vpvr_tod_session_filter_15m_20260715/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_tod_session_filter_15m_20260715` | `strategies/vpvr_tod_session_filter_15m_20260715/tests/test_signals.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_volume_edge_3tf_v1_20260711` | `strategies/vpvr_volume_edge_3tf_v1_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/run_cpcv.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_reversion_1d_momentum_filter_20260709` | `strategies/vpvr_xs_reversion_1d_momentum_filter_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_smart_routing_15m_20260715` | `strategies/vpvr_xs_smart_routing_15m_20260715/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_smart_routing_15m_20260715` | `strategies/vpvr_xs_smart_routing_15m_20260715/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_smart_routing_15m_20260715` | `strategies/vpvr_xs_smart_routing_15m_20260715/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_smart_routing_15m_20260715` | `strategies/vpvr_xs_smart_routing_15m_20260715/tests/test_v3_basic.py` | cautionary | contains hardcoded ticker symbols |
| `xs_momentum_rank_1d_20260709` | `strategies/xs_momentum_rank_1d_20260709/backtest.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `xs_momentum_rank_1d_20260709` | `strategies/xs_momentum_rank_1d_20260709/tests/test_backtest.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `xs_momentum_rank_1d_20260709` | `strategies/xs_momentum_rank_1d_20260709/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `bb_reversion_rsi_1m_20260707` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `bb_reversion_rsi_1m_20260707_p3opt_049` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_049/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `bb_reversion_rsi_1m_20260707_p3opt_050` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_050/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `bb_reversion_rsi_1m_20260707_p3opt_099` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_099/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `bb_reversion_rsi_1m_20260707_p3opt_100` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_100/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vol_breakout_1m_15m_vpvr_confluence_u6_20260718` | `strategies/_graveyard/1m_klines_reversal/vol_breakout_1m_15m_vpvr_confluence_u6_20260718/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_mtf_reversion_5m_consensus_20260710` | `strategies/_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_1m_kama_reversal_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_1m_kama_reversal_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/tests/test_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_reversion_1m_volume_profile_break_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_1m_volume_profile_break_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/tests/test_signals.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_sentiment_attention_1m_20260716` | `strategies/_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_sentiment_attention_1m_20260716` | `strategies/_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_sentiment_attention_1m_20260716` | `strategies/_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/tests/test_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_leadlag_5m_20260711` | `strategies/_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/run_u5.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/sma34946/run_sma34946.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/prototype.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/sma34928_low_threshold.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/sweep_top_combo.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_oscillator_mr` | `strategies/_graveyard/funding_carry/funding_oscillator_mr/run_u5noncarry.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_oscillator_mr` | `strategies/_graveyard/funding_carry/funding_oscillator_mr/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `sma_34925_btc_funding_delta` | `strategies/_graveyard/funding_carry/sma_34925_btc_funding_delta/backtest_inhouse.py` | cautionary | contains hardcoded ticker symbols |
| `sma_34925_btc_funding_delta` | `strategies/_graveyard/funding_carry/sma_34925_btc_funding_delta/backtest_vectorbt.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/tests/test_signals.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_onchain_proxy_1h_20260711` | `strategies/_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_onchain_proxy_1h_20260711` | `strategies/_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_onchain_proxy_1h_20260711` | `strategies/_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/tests/test_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/tests/test_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/tests/test_signals.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_funding_hvn_lvn_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_hvn_lvn_20260718/vpvr_funding_hvn_lvn_backtest.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_funding_hvn_lvn_confluence_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_hvn_lvn_confluence_20260718/vpvr_funding_hvn_lvn_confluence_backtest.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_funding_regime_15m_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_regime_15m_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_regime_15m_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/tests/test_v3_basic.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/tests/test_strategy.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/cpcv_optimize.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |

### Evaluation

| Strategy | Module | Movability | Reason |
|----------|--------|------------|--------|
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `donchian_breakout_atr_1d_20260709` | `strategies/donchian_breakout_atr_1d_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `impl_vpvr_multi_tf_funding` | `strategies/impl_vpvr_multi_tf_funding/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `loid_iceberg_v4_1m_20260720` | `strategies/loid_iceberg_v4_1m_20260720/backtest.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `loid_iceberg_v4_1m_20260720` | `strategies/loid_iceberg_v4_1m_20260720/run_first_btc_90d.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `loid_iceberg_v4_1m_20260720` | `strategies/loid_iceberg_v4_1m_20260720/run_first_btc_90d_chunked.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `loid_iceberg_v4_1m_20260720` | `strategies/loid_iceberg_v4_1m_20260720/run_param_scan.py` | cautionary | contains hardcoded ticker symbols; imports sibling module 'strategies.loid_iceberg_v4_1m_20260720.iceberg_detector'; imports sibling module 'strategies.loid_iceberg_v4_1m_20260720.backtest' |
| `loid_iceberg_v4_1m_20260720` | `strategies/loid_iceberg_v4_1m_20260720/run_param_scan_fast.py` | cautionary | contains hardcoded ticker symbols; imports sibling module 'strategies.loid_iceberg_v4_1m_20260720.iceberg_detector'; imports sibling module 'strategies.loid_iceberg_v4_1m_20260720.backtest' |
| `loid_iceberg_v4_1m_20260720` | `strategies/loid_iceberg_v4_1m_20260720/run_param_scan_minimal.py` | cautionary | contains hardcoded ticker symbols; imports sibling module 'strategies.loid_iceberg_v4_1m_20260720.iceberg_detector'; imports sibling module 'strategies.loid_iceberg_v4_1m_20260720.backtest' |
| `loid_iceberg_v4_1m_20260720` | `strategies/loid_iceberg_v4_1m_20260720/run_threshold_scan.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `loid_iceberg_v4_1m_20260720` | `strategies/loid_iceberg_v4_1m_20260720/tests/test_backtest.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `loid_vpvr_confluence_20260717` | `strategies/loid_vpvr_confluence_20260717/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `loid_vpvr_confluence_20260717` | `strategies/loid_vpvr_confluence_20260717/run_g1g7_backtest.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `momentum_trend_btc_only_softer_stop_1h_20260712` | `strategies/momentum_trend_btc_only_softer_stop_1h_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `momentum_trend_btc_only_softer_stop_1h_20260712` | `strategies/momentum_trend_btc_only_softer_stop_1h_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `momentum_trend_btc_only_softer_stop_1h_20260712` | `strategies/momentum_trend_btc_only_softer_stop_1h_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `momentum_trend_btc_only_softer_stop_1h_20260712` | `strategies/momentum_trend_btc_only_softer_stop_1h_20260712/walk_forward.py` | cautionary | references strategy-local paths |
| `momentum_trend_multi_tf_atr_scaled_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_1h_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `momentum_trend_multi_tf_atr_scaled_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_1h_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `momentum_trend_multi_tf_atr_scaled_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_1h_20260712/walk_forward.py` | cautionary | references strategy-local paths |
| `momentum_trend_multi_tf_atr_scaled_v2_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_v2_1h_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `momentum_trend_multi_tf_atr_scaled_v2_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_v2_1h_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `momentum_trend_multi_tf_atr_scaled_v2_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_v2_1h_20260712/walk_forward.py` | cautionary | references strategy-local paths |
| `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_v3_1h_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_v3_1h_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_v3_1h_20260712/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `momentum_trend_multi_tf_atr_scaled_v3_1h_20260712` | `strategies/momentum_trend_multi_tf_atr_scaled_v3_1h_20260712/walk_forward.py` | cautionary | references strategy-local paths |
| `mtf_vpvr_edge_zscore_1m_15m_2h_20260718` | `strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `mtf_vpvr_edge_zscore_1m_15m_2h_20260718` | `strategies/mtf_vpvr_edge_zscore_1m_15m_2h_20260718/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `mtf_xs_pairs_1m_15m_2h_h1_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h1_20260718/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `mtf_xs_pairs_1m_15m_2h_h1_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h1_20260718/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `mtf_xs_pairs_1m_15m_2h_h1_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h1_20260718/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `mtf_xs_pairs_1m_15m_2h_h2_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h2_20260718/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `mtf_xs_pairs_1m_15m_2h_h2_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h2_20260718/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `mtf_xs_pairs_1m_15m_2h_h2_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h2_20260718/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `mtf_xs_pairs_1m_15m_2h_h3_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/framework_validate.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `mtf_xs_pairs_1m_15m_2h_h3_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/sizing_sweep.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `mtf_xs_pairs_1m_15m_2h_h3_20260718` | `strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/backtest.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/tests/test_backtest.py` | cautionary | references strategy-local paths |
| `pairs_cointegration_1d_20260709` | `strategies/pairs_cointegration_1d_20260709/walk_forward.py` | cautionary | references strategy-local paths |
| `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714` | `strategies/trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714` | `strategies/trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714` | `strategies/trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/tests/test_backtest_outputs.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `trend_multi_tf_momentum_cascade_4h_1h_15m_20260714` | `strategies/trend_multi_tf_momentum_cascade_4h_1h_15m_20260714/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `trend_regime_gate_1d_adx_4h_1h_20260714` | `strategies/trend_regime_gate_1d_adx_4h_1h_20260714/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `trend_regime_gate_1d_adx_4h_1h_20260714` | `strategies/trend_regime_gate_1d_adx_4h_1h_20260714/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `trend_regime_gate_1d_adx_4h_1h_20260714` | `strategies/trend_regime_gate_1d_adx_4h_1h_20260714/walk_forward.py` | cautionary | references strategy-local paths |
| `vol_breakout_2tf_vpvr_confluence_4h_20260712` | `strategies/vol_breakout_2tf_vpvr_confluence_4h_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vol_breakout_2tf_vpvr_confluence_4h_20260712` | `strategies/vol_breakout_2tf_vpvr_confluence_4h_20260712/walk_forward.py` | cautionary | references strategy-local paths |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/scripts/b6_fwer.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/tests/test_b3_artifacts.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vol_breakout_vpvr_val_fade_1h_5m_20260714` | `strategies/vol_breakout_vpvr_val_fade_1h_5m_20260714/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_carry_term_8h_20260711` | `strategies/vpvr_carry_term_8h_20260711/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_edge_zscore_15m_only_20260720` | `strategies/vpvr_edge_zscore_15m_only_20260720/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_edge_zscore_15m_only_20260720` | `strategies/vpvr_edge_zscore_15m_only_20260720/smoke_backtest.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720` | `strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720` | `strategies/vpvr_edge_zscore_multi_tf_1m_15m_2h_20260720/smoke_backtest.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_inverse_reversion_4h_funding_filter_20260712` | `strategies/vpvr_inverse_reversion_4h_funding_filter_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_micro_reversion_1h_funding_filter_20260710` | `strategies/vpvr_micro_reversion_1h_funding_filter_20260710/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_micro_reversion_1h_funding_filter_20260710` | `strategies/vpvr_micro_reversion_1h_funding_filter_20260710/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_regime_reversion_4h_vol_switch_20260710` | `strategies/vpvr_regime_reversion_4h_vol_switch_20260710/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_regime_reversion_4h_vol_switch_20260710` | `strategies/vpvr_regime_reversion_4h_vol_switch_20260710/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_regime_reversion_4h_vol_switch_20260710` | `strategies/vpvr_regime_reversion_4h_vol_switch_20260710/tests/test_strategy.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_reversal_check_20260717` | `strategies/vpvr_reversal_check_20260717/run_cross_check.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_15m_donchian_regime_20260709` | `strategies/vpvr_reversion_15m_donchian_regime_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_reversion_15m_donchian_regime_20260709` | `strategies/vpvr_reversion_15m_donchian_regime_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_5m_vwap_trail_20260709` | `strategies/vpvr_reversion_5m_vwap_trail_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_reversion_5m_vwap_trail_20260709` | `strategies/vpvr_reversion_5m_vwap_trail_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_tod_session_filter_15m_20260715` | `strategies/vpvr_tod_session_filter_15m_20260715/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_volume_edge_3tf_v1_20260711` | `strategies/vpvr_volume_edge_3tf_v1_20260711/rebuild_summary.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_volume_edge_3tf_v1_20260711` | `strategies/vpvr_volume_edge_3tf_v1_20260711/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_volume_edge_3tf_v1_20260711` | `strategies/vpvr_volume_edge_3tf_v1_20260711/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_volume_edge_3tf_v1_20260711` | `strategies/vpvr_volume_edge_3tf_v1_20260711/tests/test_backtest_outputs.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_volume_edge_3tf_v1_20260711` | `strategies/vpvr_volume_edge_3tf_v1_20260711/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_basis_zscore_15m_funding_filter_20260712` | `strategies/vpvr_xs_basis_zscore_15m_funding_filter_20260712/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_reversion_1d_momentum_filter_20260709` | `strategies/vpvr_xs_reversion_1d_momentum_filter_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_reversion_1d_momentum_filter_20260709` | `strategies/vpvr_xs_reversion_1d_momentum_filter_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_xs_reversion_1d_momentum_filter_20260709` | `strategies/vpvr_xs_reversion_1d_momentum_filter_20260709/tests/test_strategy.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_smart_routing_15m_20260715` | `strategies/vpvr_xs_smart_routing_15m_20260715/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `xs_momentum_rank_1d_20260709` | `strategies/xs_momentum_rank_1d_20260709/backtest.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `xs_momentum_rank_1d_20260709` | `strategies/xs_momentum_rank_1d_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `xs_momentum_rank_1d_20260709` | `strategies/xs_momentum_rank_1d_20260709/tests/test_backtest.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `xs_momentum_rank_1d_20260709` | `strategies/xs_momentum_rank_1d_20260709/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `xs_momentum_rank_1d_20260709` | `strategies/xs_momentum_rank_1d_20260709/walk_forward.py` | cautionary | references strategy-local paths |
| `bb_reversion_rsi_1m_20260707` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `bb_reversion_rsi_1m_20260707` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `bb_reversion_rsi_1m_20260707_p3opt_049` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_049/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `bb_reversion_rsi_1m_20260707_p3opt_049` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_049/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `bb_reversion_rsi_1m_20260707_p3opt_050` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_050/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `bb_reversion_rsi_1m_20260707_p3opt_050` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_050/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `bb_reversion_rsi_1m_20260707_p3opt_099` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_099/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `bb_reversion_rsi_1m_20260707_p3opt_099` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_099/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `bb_reversion_rsi_1m_20260707_p3opt_100` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_100/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `bb_reversion_rsi_1m_20260707_p3opt_100` | `strategies/_graveyard/1m_klines_reversal/bb_reversion_rsi_1m_20260707_p3opt_100/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vol_breakout_1m_15m_vpvr_confluence_u6_20260718` | `strategies/_graveyard/1m_klines_reversal/vol_breakout_1m_15m_vpvr_confluence_u6_20260718/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_mtf_reversion_5m_consensus_20260710` | `strategies/_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_mtf_reversion_5m_consensus_20260710` | `strategies/_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_mtf_reversion_5m_consensus_20260710` | `strategies/_graveyard/1m_klines_reversal/vpvr_mtf_reversion_5m_consensus_20260710/tests/test_strategy.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_reversion_1m_kama_reversal_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_reversion_1m_kama_reversal_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_kama_reversal_20260709/strategy.py` | cautionary | entry/runner/strategy wiring; not a reusable library module |
| `vpvr_reversion_1m_volume_profile_break_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_reversion_1m_volume_profile_break_20260709` | `strategies/_graveyard/1m_klines_reversal/vpvr_reversion_1m_volume_profile_break_20260709/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_sentiment_attention_1m_20260716` | `strategies/_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_sentiment_attention_1m_20260716` | `strategies/_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_sentiment_attention_1m_20260716` | `strategies/_graveyard/1m_klines_reversal/vpvr_sentiment_attention_1m_20260716/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_leadlag_5m_20260711` | `strategies/_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_leadlag_5m_20260711` | `strategies/_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_leadlag_5m_20260711` | `strategies/_graveyard/1m_klines_reversal/vpvr_xs_leadlag_5m_20260711/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/run_u5.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/run_u5_multiwindow.py` | cautionary | contains hardcoded ticker symbols |
| `funding_carry` | `strategies/_graveyard/funding_carry/funding_carry/sma34946/run_sma34946.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/prototype.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/sma34928_low_threshold.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_carry_asym` | `strategies/_graveyard/funding_carry/funding_carry_asym/sweep_top_combo.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `funding_oscillator_mr` | `strategies/_graveyard/funding_carry/funding_oscillator_mr/run_u5noncarry.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `sma_34925_btc_funding_delta` | `strategies/_graveyard/funding_carry/sma_34925_btc_funding_delta/backtest_backtrader.py` | cautionary | contains hardcoded ticker symbols |
| `sma_34925_btc_funding_delta` | `strategies/_graveyard/funding_carry/sma_34925_btc_funding_delta/backtest_inhouse.py` | cautionary | contains hardcoded ticker symbols |
| `sma_34925_btc_funding_delta` | `strategies/_graveyard/funding_carry/sma_34925_btc_funding_delta/backtest_vectorbt.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_macro_calendar_4h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_macro_calendar_4h_20260715/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_onchain_proxy_1h_20260711` | `strategies/_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_onchain_proxy_1h_20260711` | `strategies/_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_onchain_proxy_1h_20260711` | `strategies/_graveyard/options_macro_sentiment/vpvr_onchain_proxy_1h_20260711/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_options_putcall_oi_pressure_8h_20260715` | `strategies/_graveyard/options_macro_sentiment/vpvr_options_putcall_oi_pressure_8h_20260715/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_stable_depeg_regime_4h_20260716_p3opt_091` | `strategies/_graveyard/options_macro_sentiment/vpvr_stable_depeg_regime_4h_20260716_p3opt_091/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `paper_trading_mtf_xs_pairs_eth_sol_20260719` | `strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/fill_engine.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `paper_trading_mtf_xs_pairs_eth_sol_20260719` | `strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/kill_criteria.py` | cautionary | contains hardcoded ticker symbols |
| `paper_trading_mtf_xs_pairs_eth_sol_20260719` | `strategies/_graveyard/paper_trading/paper_trading_mtf_xs_pairs_eth_sol_20260719/paper_runner.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_asym_4h_20260713` | `strategies/_graveyard/vpvr_funding/vpvr_funding_asym_4h_20260713/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/audit_maxdd.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_aware_v1_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_aware_v1_20260711/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/run_cpcv.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/state_machine.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_funding_carry_asym_v2_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_carry_asym_v2_20260718/tests/test_state_machine.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_funding_hvn_lvn_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_hvn_lvn_20260718/vpvr_funding_hvn_lvn_backtest.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_funding_hvn_lvn_confluence_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_hvn_lvn_confluence_20260718/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_hvn_lvn_confluence_20260718` | `strategies/_graveyard/vpvr_funding/vpvr_funding_hvn_lvn_confluence_20260718/vpvr_funding_hvn_lvn_confluence_backtest.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_funding_regime_15m_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_regime_15m_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_regime_15m_20260711` | `strategies/_graveyard/vpvr_funding/vpvr_funding_regime_15m_20260711/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_reset_window_1h_20260715` | `strategies/_graveyard/vpvr_funding/vpvr_funding_reset_window_1h_20260715/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_funding_term_curve_1h_20260714` | `strategies/_graveyard/vpvr_funding/vpvr_funding_term_curve_1h_20260714/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `cointegration_pairs_vpvr_poc_4h_20260714` | `strategies/_graveyard/vpvr_xs_pairs_4h/cointegration_pairs_vpvr_poc_4h_20260714/framework_adapter_vectorbt.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_4h_zscore_vpvr_20260710` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_4h_zscore_vpvr_20260710/walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/cpcv_optimize.py` | cautionary | contains hardcoded ticker symbols |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_btc_sol_4h_20260712` | `strategies/_graveyard/vpvr_xs_pairs_4h/vpvr_xs_pairs_btc_sol_4h_20260712/walk_forward.py` | cautionary | references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_30m_funding_filter_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_20260712/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_bnb_v5_loose_20260717/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_doge_20260717/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_regularized_20260712/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v10_optimize_20260717/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_btc_sol_v3_20260712/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/tests/test_walk_forward.py` | portable | generic helper; no hardcoded symbols/strategy paths |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260712/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_20260717/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/framework_adapter_backtrader.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/framework_adapter_freqtrade.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/run_backtest.py` | cautionary | entry/runner/framework glue; strategy-specific wiring |
| `vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717` | `strategies/_graveyard/xs_pairs_30m/vpvr_xs_pairs_30m_funding_filter_eth_sol_v5_loose_20260717/walk_forward.py` | cautionary | contains hardcoded ticker symbols; references strategy-local paths |
