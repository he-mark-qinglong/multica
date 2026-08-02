# `_shared/` API 索引 (J13)

由 `python3 scripts/gen_api_docs.py` 生成；请勿手改。


## `_shared.adapters`

- [`_shared.adapters.backtrader_adapter`](adapters/backtrader_adapter.md) — backtrader framework adapter — SMA-35409 / MAP-P5 #042.
- [`_shared.adapters.fastquant_adapter`](adapters/fastquant_adapter.md) — fastquant framework adapter — MAP-P5 / SMA-35404.

## `_shared.attribution`

- [`_shared.attribution.decompose`](attribution/decompose.md) — Performance attribution — strategy-level PnL decomposition.

## `_shared.bench_backtest`

- [`_shared.bench_backtest`](bench_backtest.md) — Backtest performance benchmark (B16) — target: >100K bars/s.

## `_shared.data`

- [`_shared.data.api`](data/api.md) — Unified data access layer (F20).
- [`_shared.data.backfill`](data/backfill.md) — Gap backfill from Binance klines REST (F15).
- [`_shared.data.feature_store`](data/feature_store.md) — Lightweight versioned feature store (F18).
- [`_shared.data.fetch_common`](data/fetch_common.md) — Shared helpers for Binance futures-data fetchers (F6/F7/F8/F15).
- [`_shared.data.freshness`](data/freshness.md) — Dataset freshness monitoring (F13).
- [`_shared.data.liq_loader`](data/liq_loader.md) — Liquidation-event loader + supervised collector (F6).
- [`_shared.data.ls_ratio_fetch`](data/ls_ratio_fetch.md) — Long/short account-ratio history fetcher for Binance USDⓈ-M futures (F8).
- [`_shared.data.oi_fetch`](data/oi_fetch.md) — Open-interest history fetcher for Binance USDⓈ-M futures (F7).
- [`_shared.data.quality`](data/quality.md) — Data-quality checks for bar/tick series (F14).
- [`_shared.data.snapshot_replay`](data/snapshot_replay.md) — Immutable data snapshots and deterministic replay (F17).
- [`_shared.data.stream_ingest`](data/stream_ingest.md) — Realtime aggTrade stream ingestion (F16).
- [`_shared.data.versioning`](data/versioning.md) — Data file version management (F19).

## `_shared.data_loader`

- [`_shared.data_loader`](data_loader.md) — Authoritative unified data loader for quant-loop strategies.

## `_shared.execution`

- [`_shared.execution.check_inline_costs`](execution/check_inline_costs.md) — Inline-cost scanner — read-only drift detector for quant-loop strategies.
- [`_shared.execution.cost_model`](execution/cost_model.md) — Authoritative execution cost model for quant-loop strategies.

## `_shared.gates`

- [`_shared.gates.enforce`](gates/enforce.md) — Gate enforcement — refuses to certify a strategy as SHIP-eligible if metrics fail G1-G7 + Wave 2 additions (CPCV + DSR).

## `_shared.indicators`

- [`_shared.indicators.vpvr`](indicators/vpvr.md) — Volume Profile Visible Range (VPVR) level detector.

## `_shared.l2`

- [`_shared.l2.book`](l2/book.md) — L2 order-book reconstruction engine (B4).
- [`_shared.l2.bookdepth`](l2/bookdepth.md) — Loader for Binance public-data ``bookDepth`` snapshots (B4).
- [`_shared.l2.replay`](l2/replay.md) — L2 diff-driven replay engine (B4).

## `_shared.latency_model`

- [`_shared.latency_model`](latency_model.md) — Backtest latency model (B7).

## `_shared.liquidation_sim`

- [`_shared.liquidation_sim`](liquidation_sim.md) — Per-bar liquidation simulator for leveraged positions (B13).

## `_shared.market_making`

- [`_shared.market_making.adverse_selection`](market_making/adverse_selection.md) — Adverse-selection guard for market making.
- [`_shared.market_making.backtest_live_parity`](market_making/backtest_live_parity.md) — Backtest ↔ paper-path parity validator (B19).
- [`_shared.market_making.cancel_replace`](market_making/cancel_replace.md) — Cancel-replace decision engine — amend vs cancel+place vs hold.
- [`_shared.market_making.cross_venue`](market_making/cross_venue.md) — Cross-venue quoting and arbitrage-edge analytics.
- [`_shared.market_making.dynamic_erc`](market_making/dynamic_erc.md) — Dynamic ERC rebalancing with correlation regime detection.
- [`_shared.market_making.evolution_engine`](market_making/evolution_engine.md) — Genetic strategy evolution engine.
- [`_shared.market_making.fair_value`](market_making/fair_value.md) — Fair value estimation for market making.
- [`_shared.market_making.hmm_regime`](market_making/hmm_regime.md) — HMM regime detector — classify market state for conditional quoting.
- [`_shared.market_making.inventory`](market_making/inventory.md) — Inventory state tracking and risk limits for market making.
- [`_shared.market_making.kelly_sizing`](market_making/kelly_sizing.md) — Kelly criterion position sizing for market making.
- [`_shared.market_making.live_quoter`](market_making/live_quoter.md) — Live execution bridge — connects the quoting engine to venue adapters.
- [`_shared.market_making.maker_simulator`](market_making/maker_simulator.md) — Backtest simulator for market-making strategies.
- [`_shared.market_making.multi_level`](market_making/multi_level.md) — Multi-level quoting — post orders at multiple price tiers.
- [`_shared.market_making.online_adverse`](market_making/online_adverse.md) — Online learning of adverse selection cost.
- [`_shared.market_making.optimal_spread`](market_making/optimal_spread.md) — Avellaneda-Stoikov analytically optimal spread.
- [`_shared.market_making.portfolio_risk`](market_making/portfolio_risk.md) — Portfolio-level risk: correlation matrix and ERC allocation.
- [`_shared.market_making.queue_position`](market_making/queue_position.md) — Queue position fill probability model.
- [`_shared.market_making.quote_throttle`](market_making/quote_throttle.md) — Quote throttle — exchange rate-limit guard for market-making loops.
- [`_shared.market_making.quoting_engine`](market_making/quoting_engine.md) — Quote generation engine for market making.
- [`_shared.market_making.reservation_price`](market_making/reservation_price.md) — Avellaneda-Stoikov reservation price.
- [`_shared.market_making.strategy_sweeper`](market_making/strategy_sweeper.md) — Automated strategy discovery engine.
- [`_shared.market_making.stress_test`](market_making/stress_test.md) — Stress testing engine — predefined crisis scenarios.
- [`_shared.market_making.tail_risk`](market_making/tail_risk.md) — Tail risk metrics: VaR, CVaR, and stress scenarios.

## `_shared.multi_strategy_backtest`

- [`_shared.multi_strategy_backtest`](multi_strategy_backtest.md) — Multi-strategy portfolio backtest (B15).

## `_shared.ops`

- [`_shared.ops.alerting`](ops/alerting.md) — Structured alerting with pluggable sinks (H5).
- [`_shared.ops.audit_trail`](ops/audit_trail.md) — Runtime audit trail (H20).
- [`_shared.ops.config_hot`](ops/config_hot.md) — Ops-layer config hot-reload with audit log and rollback (H8).
- [`_shared.ops.deploy`](ops/deploy.md) — Deployment unit generator (H11).
- [`_shared.ops.drift_monitor`](ops/drift_monitor.md) — Live-vs-backtest drift monitor (H19).
- [`_shared.ops.heartbeat`](ops/heartbeat.md) — Heartbeat writer + timeout watcher (H14, H15).
- [`_shared.ops.isolation`](ops/isolation.md) — Per-strategy process resource isolation (H10).
- [`_shared.ops.metrics_export`](ops/metrics_export.md) — Prometheus text exposition exporter (H7).
- [`_shared.ops.multi_runner`](ops/multi_runner.md) — Multi-strategy parallel runner (H9).
- [`_shared.ops.pnl_attribution`](ops/pnl_attribution.md) — Live PnL attribution (H18).
- [`_shared.ops.risk_dashboard`](ops/risk_dashboard.md) — Real-time risk monitoring dashboard (D19).
- [`_shared.ops.secrets`](ops/secrets.md) — API key management (H13).
- [`_shared.ops.structured_log`](ops/structured_log.md) — JSON structured logger (H6).
- [`_shared.ops.supervisor`](ops/supervisor.md) — Process supervisor: crash-restart, version rollback, graceful drain (H16, H17, H4).

## `_shared.ops_profile`

- [`_shared.ops_profile`](ops_profile.md) — Performance profiling utilities (J18).

## `_shared.paper`

- [`_shared.paper.ledger_writer`](paper/ledger_writer.md) — Atomic, idempotent, per-date ledger writer for paper-trading results.
- [`_shared.paper.repair_ledger`](paper/repair_ledger.md) — Graveyard daily-metrics ledger repair tool.
- [`_shared.paper.runner`](paper/runner.md) — Paper-trading runner skeleton — config-driven, idempotent, kill-aware.

## `_shared.partial_fill`

- [`_shared.partial_fill`](partial_fill.md) — Backtest partial-fill simulator (B5).

## `_shared.paths`

- [`_shared.paths`](paths.md) — Path resolution for quant-loop. Single source of truth.

## `_shared.portfolio`

- [`_shared.portfolio.account_view`](portfolio/account_view.md) — Strategy-level independent account views (I12, partial I11).
- [`_shared.portfolio.attribution`](portfolio/attribution.md) — Portfolio performance & drawdown attribution (I10, I15).
- [`_shared.portfolio.benchmark`](portfolio/benchmark.md) — Benchmark construction and comparison (I17).
- [`_shared.portfolio.capital_pool`](portfolio/capital_pool.md) — Inter-strategy shared capital pool (I11).
- [`_shared.portfolio.concentration`](portfolio/concentration.md) — Theme/sector concentration limiter (I14).
- [`_shared.portfolio.exposure`](portfolio/exposure.md) — Portfolio exposure limiter (I13).
- [`_shared.portfolio.lifecycle`](portfolio/lifecycle.md) — Strategy lifecycle state machine (I16).
- [`_shared.portfolio.reoptimize`](portfolio/reoptimize.md) — Portfolio re-optimization scheduler (I18).
- [`_shared.portfolio.reporting`](portfolio/reporting.md) — HTML portfolio report generator (I20).
- [`_shared.portfolio.snapshot`](portfolio/snapshot.md) — Portfolio state snapshots persisted to parquet (I19).

## `_shared.queue_aware_backtest`

- [`_shared.queue_aware_backtest`](queue_aware_backtest.md) — Queue-position-aware wrapper around the authoritative backtester (B6).

## `_shared.regime`

- [`_shared.regime.btc_gate`](regime/btc_gate.md) — BTC regime classifier — shared across strategies.

## `_shared.risk`

- [`_shared.risk.budget`](risk/budget.md) — Per-strategy risk budget allocation (D18).
- [`_shared.risk.event_log`](risk/event_log.md) — Risk event audit log (D20).

## `_shared.run_backtest`

- [`_shared.run_backtest`](run_backtest.md) — Authoritative in-house equity-walk engine — per-bar compounding.

## `_shared.sizing`

- [`_shared.sizing.liquidity`](sizing/liquidity.md) — Multi-Cap Liquidity Sizing (MCLS) — per SPEC liquidity_sizing_v1_20260726/SPEC.md.
- [`_shared.sizing.vol_target`](sizing/vol_target.md) — Volatility-targeted position sizing layer.

## `_shared.strategy_kit`

- [`_shared.strategy_kit.composer`](strategy_kit/composer.md) — Signal composer — combine multiple strategy signals into one composite.
- [`_shared.strategy_kit.doc_links`](strategy_kit/doc_links.md) — Research-document linkage for strategy directories (metric A20).
- [`_shared.strategy_kit.factor_library`](strategy_kit/factor_library.md) — Built-in factor library (metric A5) — production-grade cross-asset / crypto-perp factors with paper-backed definitions.
- [`_shared.strategy_kit.feature_pipeline`](strategy_kit/feature_pipeline.md) — Declarative feature pipeline with topological ordering and a no-lookahead assertion checker.
- [`_shared.strategy_kit.hot_reload`](strategy_kit/hot_reload.md) — Config hot-reload — swap strategy parameters without restarting the loop.
- [`_shared.strategy_kit.indicators`](strategy_kit/indicators.md) — Technical indicator library (metric A6) — vectorized, pure functions.
- [`_shared.strategy_kit.labels`](strategy_kit/labels.md) — Triple-barrier labels (López de Prado 2018, AFML ch. 3).
- [`_shared.strategy_kit.meta_labeling`](strategy_kit/meta_labeling.md) — Meta-labeling (López de Prado 2018, AFML ch. 3.6–3.8).
- [`_shared.strategy_kit.ml_gateway`](strategy_kit/ml_gateway.md) — ML gateway — single, version-checked entry point for model inference.
- [`_shared.strategy_kit.registry`](strategy_kit/registry.md) — Indicator registry — single source of truth for named, schema-validated indicators used across strategy research.
- [`_shared.strategy_kit.signal_bus`](strategy_kit/signal_bus.md) — Inter-strategy signal bus — in-memory pub/sub with TTL and versioning.
- [`_shared.strategy_kit.templates.funding_carry_template.strategy`](strategy_kit/templates/funding_carry_template/strategy.md) — Funding-carry strategy template (A11).
- [`_shared.strategy_kit.templates.hedged_grid_template.strategy`](strategy_kit/templates/hedged_grid_template/strategy.md) — Hedged grid strategy template (A11).
- [`_shared.strategy_kit.templates.mean_reversion_template.strategy`](strategy_kit/templates/mean_reversion_template/strategy.md) — RSI mean-reversion strategy template (A11).
- [`_shared.strategy_kit.templates.meta_label_template.strategy`](strategy_kit/templates/meta_label_template/strategy.md) — Meta-labeling strategy template (A11).
- [`_shared.strategy_kit.templates.momentum_template.strategy`](strategy_kit/templates/momentum_template/strategy.md) — EMA-cross momentum strategy template (A11).
- [`_shared.strategy_kit.versioning`](strategy_kit/versioning.md) — Strategy version management (metric A12).

## `_shared.swarm`

- [`_shared.swarm.accept`](swarm/accept.md) — Mechanical acceptance executor for swarm runs (SMA-36514 / W5-T12).
- [`_shared.swarm.upload_artifacts`](swarm/upload_artifacts.md) — Swarm-run artifact uploader: push collected files to multica + post an EVIDENCE receipt.

## `_shared.templates`

- [`_shared.templates.example_strategy`](templates/example_strategy.md) — Minimal example strategy implementing strategy contract v2.
- [`_shared.templates.preregistered_cpcv`](templates/preregistered_cpcv.md) — Pre-registered CPCV evaluation template (Phase D — HF pipeline).
- [`_shared.templates.run_strategy`](templates/run_strategy.md) — Generic strategy runner — contract v2 -> backtest -> 9-key metrics.
- [`_shared.templates.scaffold`](templates/scaffold.md) — Generate a contract-v2-compliant strategy directory.
- [`_shared.templates.strategy_contract_v2`](templates/strategy_contract_v2.md) — Strategy contract v2 — the interface every new (high-frequency) strategy must implement.

## `_shared.validation`

- [`_shared.validation.compute_metrics`](validation/compute_metrics.md) — Shared helper to compute the 9-key metrics dict expected by metrics_validator.
- [`_shared.validation.cpcv`](validation/cpcv.md) — Combinatorial Purged Cross-Validation (CPCV) harness.
- [`_shared.validation.decay_monitor`](validation/decay_monitor.md) — Signal decay monitoring (G20).
- [`_shared.validation.fee_shock`](validation/fee_shock.md) — Fee-shock replay for validation reports.
- [`_shared.validation.sensitivity`](validation/sensitivity.md) — Parameter sensitivity analysis for strategies (G18).
- [`_shared.validation.validate_metrics`](validation/validate_metrics.md) — Validate metrics.json against the 9-key schema + provenance fields.

## `_shared.validators`

- [`_shared.validators.framework_cv_validator`](validators/framework_cv_validator.md) — Framework cross-validation validator.
- [`_shared.validators.metrics_validator`](validators/metrics_validator.md) — Range and sentinel validator for strategy metrics.json.

## `_shared.vectorized_backtest`

- [`_shared.vectorized_backtest`](vectorized_backtest.md) — Fully vectorised signal-driven backtest engine (B2).

## `_shared.visualization`

- [`_shared.visualization.core`](visualization/core.md) ⚠ast — Standard strategy visualization bundle.
