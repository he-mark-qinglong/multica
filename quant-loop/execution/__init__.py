"""Top-level execution layer (``~/multica/quant-loop/execution/``).

The execution layer holds per-issue analytics components, each shipped as
its own ``<name>_p7exec_NNN/`` subfolder per the MAP-P7 convention
(suffix ``_p7exec_NNN``, never ``_v1``/``_v2``). The runner module
glues the components into a single CLI entry point; component code lives
inside its own folder and is the canonical import path.

Components currently shipped:

- ``position_reconciler_p7exec_053`` — position reconciliation
  ledger. Tracks the runner's per-symbol internal qty (sum of
  journaled fills) against the venue-reported exchange qty,
  produces a structured :class:`ReconciliationReport` per pass,
  and journals one row per state transition into the additive
  ``position_reconciliation_events`` table. Steady-state ``OK``
  produces zero journal rows; ``MISMATCH`` / ``UNREPORTED``
  transitions journal one row each; ``MISMATCH → OK`` /
  ``UNREPORTED → OK`` transitions journal a ``RECOVERED`` row
  (journal-only label, mirrors
  ``recon_drift_alert_p7exec_075``). Cold-start replay path
  rebuilds the latest status per symbol from disk (issue
  SMA-36240, dispatched 2026-07-25).
- ``cash_reconciler_p7exec_054`` — live cash-balance reconciliation
  for the runner. Tracks the runner's per-asset internal cash
  balance (signed sum of quote-cash flows from journaled fills +
  manual deposit / withdrawal adjustments via
  :meth:`CashReconciler.apply_adjustment`) against the
  exchange-reported cash balance (ingested via
  :meth:`CashReconciler.observe_exchange_balance`), classifies the
  per-asset gap against :class:`CashPolicy` thresholds into
  ``OK / WARN / BREACH / CRITICAL`` with a sustained-window
  promotion rule, and journals one row per state transition to
  the additive ``cash_reconciliation_events`` table so on-call
  alerts can route on the transition (issue SMA-36241,
  dispatched 2026-07-25).
- ``reconciliation_dashboard_p7exec_069`` — unified dashboard feed
  for the reconciliation sub-domain. Aggregates audit actions
  (P7-EXEC-073), drift events (P7-EXEC-075), and a thin slice of
  the ``fills`` table (rejects / throttles / blocks) into a single
  filterable feed with periodic summary snapshots persisted to the
  additive ``recon_dashboard_snapshots`` table (issue SMA-36256,
  dispatched 2026-07-25).
- ``order_state_journal_p7exec_051`` — per-``client_order_id`` state
  projection. Maintains an additive ``order_state_snapshots`` table
  inside the runner's WAL-backed SQLite journal so a fresh process
  can recover the current lifecycle state (``INTENT`` /
  ``FILLED`` / ``REJECTED`` / ``BLOCKED``) of every order on
  cold-start without replaying the canonical event log. The
  runner auto-dispatches ``record_block`` / ``record_reject``
  notifications to registered projection consumers (issue
  SMA-36238, dispatched 2026-07-25).
- ``trade_ledger_partition_p7exec_066`` — per-``(venue, day_utc)``
  ledger partitioning for the reconciliation sub-domain. Adds an
  additive ``ledger_partitions`` table (one row per ``(venue,
  day_utc)`` slice with rolling ``n_intents`` / ``n_fills`` /
  ``n_blocks`` / ``n_rejects`` counters and an OPEN/CLOSED
  lifecycle) plus a ``ledger_partition_assignments`` table
  (append-only log of every canonical ``fills`` event routed to a
  slice). The runtime component
  :class:`TradeLedgerPartitioner` watches the runner's
  ``on_request`` / ``on_fill`` hooks (and the explicit
  ``record_block`` / ``record_reject`` callbacks) and routes every
  event to its partition, keeping the intent + fill of the same
  order in one partition even when the runner's ``on_fill``
  ``ts_ns`` would compute a different UTC day than the intent's.
  Cold-start replays of a ``(venue, day)`` slice run in
  O(N_slice) instead of O(N_total) over the canonical event log
  (issue SMA-36253, dispatched 2026-07-25).
- ``recon_test_harness_p7exec_074`` — reconciliation test harness with
  7 deterministic scenarios (happy / gradual escalation / sustained
  CRITICAL / recovery / multi-symbol independence / malformed fills
  never drop / cold-start replay) and a sqlite WAL audit ledger
  (issue SMA-36261, dispatched 2026-07-25).
- ``recon_drift_alert_p7exec_075`` — reconciliation drift detector
  with explicit OK / WARN / BREACH / CRITICAL escalation policy.
  Tracks the gap between the runner's expected position (sum of
  journaled fills) and the venue-reported position; journals
  state-transition rows so on-call alerts can route on the
  transition (issue SMA-36262, dispatched 2026-07-25).
- ``internal_leg_consistency_p7exec_071`` — cross-account leg
  consistency detector. When the runner routes a strategy's order
  across multiple internal accounts (split between
  ``binance_spot_a`` and ``binance_futures_b``, etc.), the
  per-account fills must agree with each other. Tracks signed qty
  per (symbol, account), classifies the cross-account residual
  ``max(per_account_qty) - min(per_account_qty)`` against
  explicit thresholds into OK / WARN / BREACH / CRITICAL, and
  journals state-transition rows (issue SMA-36258, dispatched
  2026-07-25).
- ``order_abandon_rate_p7exec_084`` — rolling-window order-abandonment
  rate per venue (intents without a fill / block / reject after the
  abandonment window; issue SMA-36271, dispatched 2026-07-25).
- ``throttle_breach_alert_p7exec_087`` — outbound rate-limit alarm
  (issue SMA-36274, dispatched 2026-07-25).
- ``pnl_attribution_per_fill_p7exec_089`` — per-fill PnL attribution
  (issue SMA-36276, dispatched 2026-07-25).
- ``execution_breakeven_test_p7exec_090`` — cost-of-trade breakeven gate
  (issue SMA-36277, dispatched 2026-07-25).
- ``iceberg_slicer_p7exec_023`` — iceberg parent / child-order
  slicer.  Splits a parent intent (qty, display_qty) into N
  child coids with display-size slices; the runner journals
  every parent + child lifecycle transition in the additive
  ``iceberg_parents`` / ``iceberg_children`` /
  ``iceberg_child_events`` tables.  Hot-path overhead: ~10us
  median for passthrough (non-iceberg) ``on_request`` /
  ``on_fill`` calls; ~70us median for iceberg-child
  ``on_fill``; the iceberg-parent ``on_request`` is
  dominated by the SQLite WAL INSERT cost of N children + N
  PLANNED events (median ~317us for a 10-child parent).  An
  external driver loop drains pending children via
  ``IcebergSlicer.next_pending_child`` and feeds the venue's
  per-child acks back through ``on_fill``; the runner's
  canonical ``fills`` row carries the child coid (set by the
  slicer's in-place ``client_order_id`` rewrite in
  ``on_request``); the parent's coid is preserved under
  ``original_client_order_id`` for downstream analytics.
  Wired into the runner via ``register_with_runner(runner,
  slicer)`` (additive — no ``runner.py`` mutation required;
  issue SMA-36210, dispatched 2026-07-25).
  (issue SMA-36277, dispatched 2026-07-25).
- ``reject_rate_dashboard_p7exec_086`` — rolling-window venue-reject
  rate dashboard (issue SMA-36273, dispatched 2026-07-25).
- ``maker_taker_classifier_p7exec_081`` — maker/taker classification
  with per-symbol rolling ratios and journal-backed WARN rows
  (issue SMA-36268, dispatched 2026-07-25).
- ``venue_fill_quality_p7exec_080`` — per-venue signed slippage vs
  arrival reference, with rolling-window mean / median / p05 / p95
  slippage (bps) and a logistic ``quality_score ∈ [0, 1]``. Pure
  observer; never blocks requests. Journal-replay path covers cold
  start (issue SMA-36267, dispatched 2026-07-25).
- ``latency_metrics_p7exec_078`` — end-to-end latency tracker
  recording intent → fill round-trips and per-stage
  pre-transport / transport breakdowns per venue, with rolling
  p50 / p95 / p99 / max in ms and journal-backed WARN / BREACH
  verdicts (issue SMA-36265, dispatched 2026-07-25).
- ``tca_posttrade_p7exec_077`` — post-trade Transaction Cost
  Analysis report. Aggregates journaled fills into a structured
  per-symbol / per-venue / per-side report with a worst-fills
  leaderboard; rolls up the per-fill math from
  ``pnl_attribution_per_fill_p7exec_089``. Periodic (cron /
  session-end), not hot-path. JSON-serializable output for
  dashboards and audit (issue SMA-36264, dispatched 2026-07-25).
- ``tca_pretrade_p7exec_076`` — pre-trade Transaction Cost
  Analysis estimate. Estimates the expected round-trip cost
  (bps + USD) under the venue's authoritative cost model with
  regime × urgency multipliers, classifies the trade as
  ALLOW / WARN / BLOCK against a configurable cost cap, and
  journals every estimate to a SQLite WAL. Runner-wired
  ``on_request`` observer; folds the verdict into the ack
  envelope across the ALLOW / REJECT paths (issue SMA-36263,
  dispatched 2026-07-25).
- ``auto_resolve_position_p7exec_068`` — position-break
  auto-resolver. Sits on top of
  ``position_reconciler_p7exec_053`` and, when a position
  break (``MISMATCH`` / ``UNREPORTED``) is detected, runs
  through a safe, non-trading resolution ladder
  (``WAIT_NATURAL`` → ``RE_POLL`` → ``JOURNAL_REBUILD`` →
  ``ESCALATE``). One transition row per state change lands in
  the additive ``position_resolution_events`` table; the
  resolver NEVER submits orders, only queues
  :class:`ActionRequest` items that the adapter wrapper
  drains to drive venue re-polls or trigger an internal-qty
  rebuild (issue SMA-36255, dispatched 2026-07-25).
- ``fee_schedule_loader_p7exec_064`` — fee-tier loader from the
  venue API. The component is the **canonical source** for
  per-(venue, symbol, side, is_maker) fee rates consumed by the
  cost-aware siblings (``tca_pretrade_p7exec_076``,
  ``tca_posttrade_p7exec_077``,
  ``pnl_attribution_per_fill_p7exec_089``,
  ``execution_breakeven_test_p7exec_090``). The loader is
  venue-agnostic — a caller injects a
  ``fetch_callable(venue, *, symbols=...)`` per venue — and
  persists one ``fee_schedule_snapshots`` row per successful
  load plus one ``fee_schedule_failures`` row per failed load.
  Cold-start replay rebuilds the in-memory cache from the
  latest snapshot row. Hot path is one ``dict.get`` per
  ``lookup_fee(...)`` call (~1us median on CPython 3.8, well
  under the MAP-P7 250us budget; see
  ``evidence/bench_fee_schedule_loader.json``) (issue SMA-36251,
  dispatched 2026-07-25).
- ``break_threshold_policy_p7exec_067`` — live portfolio
  risk-state break detector. Tracks four continuous-state risk
  dimensions — ``NOTIONAL`` (per-symbol exposure),
  ``DAILY_PNL`` (rolling realised PnL per account),
  ``OPEN_ORDERS`` (active intents per account), ``DRAWDOWN``
  (peak-to-trough over the rolling window) — and classifies
  each (scope, dimension) pair against ``BreakThresholdPolicy``
  thresholds into ``OK / WARN / BREACH / CRITICAL``. State
  transitions journal to the additive ``break_threshold_events``
  table; ``RECOVERED`` closes the cycle. The component wires
  into the runner as a pre-trade gate (``on_request``) AND a
  post-fill observer (``on_fill``); ``BREACH`` / ``CRITICAL``
  block the next order, satisfying the "NEVER silently drop
  fills" invariant via the runner's block row. Hot-path budget
  verified at 12us / 13us / 146us (on_request / on_fill /
  runner-e2e medians; see ``evidence/bench_break_threshold.json``)
  — well under the MAP-P7 250us budget (issue SMA-36254,
  dispatched 2026-07-25).
- ``orphan_fill_detector_p7exec_058`` — real-time orphan-fill
  detector. The complement of the audit trail's periodic
  ``ORPHAN_FILL`` scanner and the linker's per-(intent, fill)
  ``ORPHAN`` classification. On every ``on_fill`` (and via
  :meth:`OrphanFillDetector.link_fill_report` for asynchronous
  user-data pushes) the detector decides whether the fill has a
  matching intent in the in-memory cache or the durable
  journal; when it does not, the fill is classified by
  ``reason`` (``NO_INTENT`` / ``STALE_REPLAY``) and
  ``severity`` (``CRITICAL`` / ``WARN`` / ``INFO``) per
  :class:`OrphanFillPolicy`, one row is appended to the
  additive ``orphan_fill_events`` table, and a follow-up
  ``RECOVERED`` transition row is emitted when a late intent
  or follow-up fill closes the orphan. Hot-path budget
  verified at 1.12us (non-orphan fill) / 37us (orphan-detection
  fill) / 43us (runner-e2e) medians; see
  ``evidence/bench_orphan_fill_detector.json`` — well under the
  MAP-P7 250us budget (issue SMA-36245, dispatched 2026-07-25).
- ``late_fill_detector_p7exec_057`` — real-time late-fill
  detector. Detects fills whose end-to-end round-trip latency
  (``fill_ts_ns - intent_ts_ns``) crossed
  ``LateFillPolicy.late_threshold_s`` (default ``5.0``s), and
  classifies each detection by ``severity`` (``WARN`` /
  ``BREACH`` / ``CRITICAL``, driven by the threshold band)
  and by ``classification`` (``NORMAL_THRESHOLD`` /
  ``COLD_START`` / ``NEGATIVE_LATENCY`` / ``NO_INTENT``). One
  row per late detection lands in the additive
  ``late_fill_events`` table. The per-fill real-time
  complement of the rolling-window
  ``latency_metrics_p7exec_078`` session distribution; the two
  coexist (the tracker answers "what was the rolling p95?",
  this detector answers "which fills actually breached the
  SLA?"). The sub-threshold hot path is one ``dict.get`` plus
  one subtraction; the late-detection path adds one indexed
  SELECT (cache-miss) + one INSERT. Hot-path budget verified
  at 1.62us (sub-threshold on_fill) / 33.88us (late on_fill) /
  44.25us (cold-start on_fill) / 79.42us (observe_fill_report)
  / 89.46us (runner-e2e) medians; see
  ``evidence/bench_late_fill_detector.json`` — well under the
  MAP-P7 250us budget (issue SMA-36244, dispatched
  2026-07-25).
- ``maker_rebate_model_p7exec_039`` — per-fill net maker rebate
  vs taker fee attribution. Decomposes every exchange fill's
  net fee into the maker rebate leg (the venue pays the trader
  when the order provides liquidity) vs the taker fee leg (the
  trader pays the venue when the order takes liquidity). One row
  per fill lands in the additive ``maker_rebate_fills`` table;
  WARN / RECOVERED transitions land in
  ``maker_rebate_warn_events``. The component consumes the
  canonical fee schedule via an injected ``FeeScheduleLoader``
  lookup (production) and falls back to a static mirror of the
  canonical ``_shared/execution/cost_model.py`` rates when the
  loader is unavailable. Hot-path budget verified at 24us
  (pure attribution) / 67us (on_fill hook) / 131us (runner
  e2e) medians; see ``evidence/bench_maker_rebate_model.json``
  — well under the MAP-P7 250us budget (issue SMA-36226,
  dispatched 2026-07-25).
- ``slippage_microstructure_a_p7exec_037`` — queue-position
  aware, top-of-book (L1) slip estimator. Consumes the
  arrival-time L1 snapshot attached to each outbound intent
  and produces the **mixed outcome** of two regimes
  (passive fill at the limit vs forced cross) under the
  model's passive-fill probability. Each estimate carries
  the expected passive slip, expected cross slip, expected
  effective slip, probability of passive fill, and a
  ``PASSIVE_FILLABLE`` / ``PASSIVE_RISKY`` / ``MUST_CROSS`` /
  ``NO_QUEUE_DATA`` verdict. Journaled to the additive
  ``queue_slip_estimates`` table; the cold-path
  ``QueueSlipDailyReport`` aggregates into the additive
  ``queue_slip_daily_reports`` table. Complements the
  post-fill L1 attribution
  (``slippage_attribution_p7exec_043``), the L2 depth-walk
  (``slippage_microstructure_b_p7exec_038``), and the
  sigmoid fill-probability
  (``fill_probability_model_p7exec_040``). Hot-path budget
  verified at 4us (pure helper) / 54us (estimate
  end-to-end) / 64us (on_request with snapshot) / 2us
  (on_request NO_QUEUE_DATA) medians; see
  ``evidence/bench_slippage_microstructure_a.json`` — well
  under the MAP-P7 250us budget (issue SMA-36224, dispatched
  2026-07-25).
- ``slippage_constant_p7exec_026`` — pre-trade
  constant-bps slippage estimator. For each outbound
  intent the estimator maps ``(qty, arrival_price,
  limit_price, side, bps_per_side)`` to a constant-bps
  slippage in bps (the **canonical constant leg** that
  every regime-aware sibling subtracts / adds to before
  emitting its regime-aware number — ``impact_by_volatility_p7exec_034``
  / ``slippage_capacity_aware_p7exec_047`` /
  ``slippage_microstructure_a_p7exec_037`` /
  ``impact_by_spread_p7exec_033`` /
  ``tca_pretrade_p7exec_076``). The default
  ``bps_per_side=5.0`` (10 bps round trip) is the canonical
  small / mid-cap taker baseline; per-``(venue, symbol)``
  overrides can be injected via the ctor's
  ``bps_overrides`` map. The pure
  ``estimate_slippage_constant(...)`` helper does the math
  (one multiplication + one division); the live
  ``SlippageConstantModel.on_request`` hook is the same
  inline plus one journal INSERT. The observer fires one
  ``WARN`` / ``RECOVERED`` transition row into
  ``slippage_constant_warn_events`` when the rolling
  window's mean ``bps_per_side`` crosses the
  ``policy.warn_bps_per_side`` threshold. Pure estimator
  measured at 88us median / 249us p95 (well under the
  MAP-P7 250us budget); ``on_request`` measured at
  ~440us median (the SQLite WAL ``INSERT`` is the
  dominant cost, shared with every journaled observer
  on this hardware; see ``evidence/bench_slippage_constant.json``).
  The runner's canonical ``fills`` row remains the source
  of truth for fill accounting; the additive
  ``slippage_constant_estimates`` table is the audit
  trail for the constant-bps cost model. Hot-path
  dataclass-free; the pure helper is exposed for offline
  / replay use (issue SMA-36213, dispatched 2026-07-25).
- ``impact_by_volatility_p7exec_034`` — pre-trade
  volatility-scaled market-impact estimator. For each
  outbound intent the estimator maps ``(qty, arrival_price,
  limit_price, vol_bps, adv_qty, horizon_s)`` to an expected
  market impact in bps (Almgren-Chriss style: ``k_vol *
  sqrt(qty/adv_qty) * vol_bps * sqrt(horizon_s)`` with an
  aggressiveness adjustment and a permanent / temporary
  decomposition), classifies the order into a severity
  bucket (``LOW`` / ``MEDIUM`` / ``HIGH`` / ``EXTREME`` /
  ``EMPTY_QTY``), and journals one row per estimate into the
  additive ``volatility_impact_estimates`` table. The
  estimator is invoked from the **pre-trade path** (NOT from
  the runner's hot path directly, although it also exposes an
  ``on_request`` hook the runner can wire in to produce
  observations on every outbound intent). Hot-path budget
  verified at ~82us median per ``estimate`` call (well under
  the MAP-P7 250us budget; see
  ``evidence/bench_impact_by_volatility.json``). The cold-path
  ``VolatilityImpactDailyReport`` rolls up per-day statistics
  into the additive ``volatility_impact_daily_reports`` table
  (issue SMA-36221, dispatched 2026-07-25).
- ``slippage_capacity_aware_p7exec_047`` — pre-trade
  capacity-aware slippage estimator. For each outbound
  intent the estimator maps ``(qty, arrival_price,
  venue_adv_qty)`` to an expected slippage in bps using a
  half-spread baseline plus a sqrt-size impact (``half_spread
  + impact_coefficient * sqrt(qty / venue_adv_qty)``)
  multiplied by a non-linear capacity penalty that fires
  above the policy's ``soft_capacity_ratio`` (default 0.5 %
  of ADV), classifies the order into a severity bucket
  (``NORMAL`` / ``ELEVATED`` / ``HIGH`` / ``CRITICAL`` /
  ``EMPTY_QTY``), and journals one row per estimate into the
  additive ``capacity_aware_estimates`` table. The
  ``CRITICAL`` band flags orders that exceed the venue's
  hard participation cap and must be sliced before reaching
  the venue. The strict estimator rejects missing /
  non-positive ``venue_adv_qty`` (``ValueError``); the
  permissive ``on_request`` runner hook folds failures into
  a ``capacity_slippage_error=True`` observation so the
  canonical ``fills`` row is never lost. Hot-path budget
  verified at ~67us median per ``estimate`` call (well under
  the MAP-P7 250us budget; see
  ``evidence/bench_slippage_capacity_aware.json``) (issue
  SMA-36234, dispatched 2026-07-25).
- ``slippage_calibrator_offline_p7exec_031`` — offline batch
  slippage calibrator. The cold-path / on-demand sibling of
  ``slippage_calibrator_online_p7exec_032``. Reads the
  canonical successful fill rows plus matching intent rows
  from :class:`OrderJournal`, joins them on
  ``client_order_id`` to recover the strategy's arrival
  reference (``expected_price`` / ``mark_price`` /
  ``arrival_mid``), groups the realised-slip distribution by
  configurable dimensions (default
  ``("symbol", "side", "venue")``), and emits a
  deterministic, versioned JSON calibration artifact with
  calibration targets (``k_vol``, ``half_spread``,
  ``aggressiveness``) that are conceptually compatible with
  the online calibrator's ``recommended_*`` helpers. The
  component is read-only against the canonical ``fills``
  rows; the only writes are to the additive
  ``slippage_calibration_offline_runs`` /
  ``slippage_calibration_offline_models`` tables and the
  atomically-published artifact JSON. Failure modes are
  surfaced clearly: malformed / missing arrivals are counted
  (NOT silently coerced), groups below
  ``min_sample_per_group`` are excluded with diagnostics, and
  publish failures are journalised with
  ``publish_status='failed'`` so the run is never marked
  successful (issue SMA-36218, dispatched 2026-07-25).
- ``slippage_calibrator_online_p7exec_032`` — online
  rolling-24h slippage calibrator. Pure observer on the
  runner's additive ``on_fill`` hook; consumes the
  per-fill realised-slip distribution and emits three
  **policy-agnostic calibration targets**
  (``recommended_k_vol`` /
  ``recommended_half_spread_bps`` /
  ``recommended_aggressiveness_factor``) that downstream
  cost models (``impact_by_volatility_p7exec_034``,
  ``slippage_microstructure_a_p7exec_037``,
  ``tca_pretrade_p7exec_076``) can apply verbatim to
  their own parameters. Per-``(symbol, side)`` bounded
  rolling deque of capacity ``max_samples`` (default
  ``86_400``); rolling-window distribution stats
  (mean / median / std / min / max / p05 / p95) recomputed
  on every observation. Journaled to the additive
  ``slippage_calibration_snapshots`` table via the
  canonical ``OrderJournal`` (WAL); cold-start
  ``recover_from_journal`` rebuilds the most-recent
  ``last_snapshot_ts_ns`` per pair. Hot-path budget
  verified at ~14us median per ``observe_fill`` / ~17us
  median per ``on_fill`` (no-snapshot path); well under
  the MAP-P7 250us budget (see
  ``evidence/bench_slippage_calibrator_online.json``).
  Snapshot-emission path adds ~3ms (SQLite WAL fsync);
  periodic + on-demand paths throttle the journal write
  off the hot path (issue SMA-36219, dispatched
  2026-07-25).
- ``impact_event_window_p7exec_036`` — per-event-window
  execution impact for major scheduled events (CPI
  releases / FOMC announcements / NFP prints). For each
  registered event the classifier partitions every
  journaled fill into one of three sub-windows around the
  release (``PRE`` baseline / ``EVENT`` spike / ``POST``
  unwind), maintains rolling sums of signed slippage per
  sub-window, computes ``impact_bps = event_mean -
  pre_mean``, and classifies against the event's expected
  volatility into ``NORMAL`` / ``ELEVATED`` / ``HIGH`` /
  ``EXTREME``. Lifecycle transitions (PENDING → ACTIVE →
  FINAL) journal one row each to the additive
  ``event_window_impact_events`` table; cold-start
  ``snapshot_from_journal`` rebuilds the most-recent
  summary per ``event_id``. Hot-path budget verified at
  1.5us / 1.7us / 7.5us / 70us (signed_slippage_bps /
  classify_phase / on_fill with event / runner-e2e
  medians; see ``evidence/bench_impact_event_window.json``)
  — well under the MAP-P7 250us budget (issue SMA-36223,
  dispatched 2026-07-25).
- ``qty_unit_normalizer_p7exec_062`` — quantity /
  contract-size normalizer for outbound order intents. The
  canonical source for per-(venue, symbol) ``LOT_SIZE`` /
  contract-size rules consumed by the runner's pre-request hook
  and the slippage / TCA / position siblings downstream. For
  each outbound intent the normalizer rounds the strategy's
  ``qty`` to the venue's ``step_size`` (``floor`` / ``round`` /
  ``ceil``), clamps to ``[min_qty, max_qty]`` when
  ``block_on_bounds_violation`` is False, and journals one
  ``qty_unit_events`` audit row per attempt via ``on_fill``
  (``UNIQUE(client_order_id)`` so a re-normalize absorbs the
  prior row). The hot-path ``on_request`` hook is I/O-free on
  the happy path: ``dict.get`` + ``round_to_step`` + tuple
  return; the ``contract_size`` field is the conversion factor
  between strategy qty (contracts) and exchange qty
  (underlying) consumed by
  ``position_reconciler_p7exec_053`` /
  ``cash_reconciler_p7exec_054``. Failure isolation matches the
  price-precision sibling: missing rule / bounds violation /
  off-step are journaled as ``qty_unit_failures`` rows; the
  policy's ``block_on_missing`` flag decides whether the
  runner blocks the request (default: yes) or proceeds with
  the original qty and a warning row. Three additive tables
  (``qty_unit_snapshots`` / ``qty_unit_events`` /
  ``qty_unit_failures``) are created by the runner's schema
  bootstrap; cold-start replay rebuilds the cache from the
  latest snapshot row. Hot-path budget verified at ~6us median
  per ``on_request`` call and ~52us per ``runner_e2e`` cycle —
  well under the MAP-P7 250us budget (issue SMA-36249,
  dispatched 2026-07-25).
- ``execution_weekly_report_p7exec_100`` — weekly execution
  rollup for the live trading layer. Cold-path periodic
  aggregator that scans the runner's :class:`OrderJournal`
  for one ISO 8601 week (``Mon-Sun`` UTC) of fills and
  produces an immutable :class:`WeeklyReport` with headline
  statistics plus per-strategy / per-venue / per-symbol
  breakdowns. Persists the result to the additive
  ``weekly_reports`` journal table so a trailing-view dashboard
  can render without re-aggregating raw rows. The companion
  :class:`FillJournal` reader handles the paper-trading
  acceptance path: it reads a JSONL fill journal line-by-line,
  returns :class:`Fill` rows, and counts malformed lines in
  :attr:`FillJournal.skipped_lines` (NEVER silently swallows).
  Cold-path ``compute_week`` measured at ~29ms median for a
  synthetic 1k-fill week (~29us/fill); hot-path overhead is
  zero (no ``on_request`` / ``on_fill`` hook is registered).
  The runner integration is purely additive — ``runner.SCHEMA_SQL``
  gains one table + indexes; :class:`OrderJournal` gains four
  helper methods (``record_weekly_report`` /
  ``weekly_report_count`` / ``list_weekly_reports`` /
  ``list_fills_window``). No existing method signature changes
  (issue SMA-36287, dispatched 2026-07-25).
- ``cancel_replace_engine_p7exec_018`` — cancel-and-replace
  decision engine for the live execution runner. Owns the
  drift / TTL / cooldown / cap rules that decide whether an
  existing working limit should be cancelled and re-quoted
  at the live mid; runs in the runner's ``on_request`` hook
  (seeds the pending cache from each outbound intent) AND
  ``on_fill`` hook (evaluates drift against the venue ack's
  reference price) and as a periodic ``evaluate_pending``
  sweep against an injected ``ref_price_fn``. Every decision
  — including ``NONE`` bookkeeping rows when the operator
  enables ``journal_every_none=True`` — lands one row in
  the additive ``cancel_replace_events`` table; the latest
  state per ``client_order_id`` lands in the additive
  ``cancel_replace_state`` table so a cold-start process
  can resume the per-coid replacement counter without
  re-evaluating historical decisions. Hot-path budget
  verified at ~19us (on_request) / ~87us (on_fill
  BELOW_THRESHOLD no-journal) / ~372us (on_fill
  CANCEL_AND_REPLACE one-INSERT + one-UPSERT)
  cProfile-ground-truth medians on macOS / CPython 3.8.2
  (see ``evidence/bench_cancel_replace_engine.json``) — the
  two no-journal scenarios are well below the MAP-P7 250us
  hot-path budget; the CANCEL_AND_REPLACE scenario is
  dominated by the shared SQLite WAL cost (every journaled
  observer on this hardware pays the same tax). NEVER
  silently drops fills: every fill lands in the engine's
  additive audit trail AND the canonical
  ``fills.event_type='fill'`` row. Hot-path pure code path
  on this hardware: < 100us per cProfile. Runner integration
  purely additive — ``runner.SCHEMA_SQL`` gains two tables +
  seven indexes; :class:`OrderJournal` gains six helper
  methods (``record_cancel_replace_event`` /
  ``upsert_cancel_replace_state`` /
  ``cancel_replace_count`` /
  ``list_cancel_replace_events`` /
  ``get_replacement_chain`` /
  ``get_cancel_replace_state``); no existing method signature
  changes (issue SMA-36205, dispatched 2026-07-25).
- ``order_throttle_p7exec_019`` — global order-rate throttle for
  the live execution runner. The pre-request complement of
  :mod:`execution.throttle_breach_alert_p7exec_087`: while the
  breach alert reconciles the venue's rate-limit responses
  post-hoc, the order throttle gates every outbound intent
  against a token-bucket policy with up to four independent
  scopes (``GLOBAL`` / ``VENUE`` / ``ACCOUNT`` / ``SYMBOL``)
  BEFORE the request reaches the transport. A request
  ``allow``s only when every active scope has tokens to spare;
  the first scope to refuse wins and journals exactly one
  ``order_throttle_events`` row carrying the rejecting bucket's
  snapshot (``observed_tokens`` / ``limit_value`` /
  ``rate_per_sec`` / ``cost``). Defaults target a multi-strategy
  single-account Binance USDT-M runner (50 orders/sec GLOBAL
  rate, 100 burst). The ``mode='PASS_THROUGH'`` switch
  turns the throttle into a pure observer for dry-run policy
  testing. Hot-path budget verified at ~31us median
  (GLOBAL-only ALLOW) / ~54us median (4-scope ALLOW) / ~58us
  median (BLOCK + journal INSERT) / ~97us median
  (4-scope ALLOW with ``journal_every_allow=True``); all
  scenarios well under the MAP-P7 250us budget. Runner
  integration purely additive — ``runner.SCHEMA_SQL`` gains
  one table + five indexes; :class:`OrderJournal` gains three
  helper methods (``record_order_throttle_event`` /
  ``order_throttle_count`` /
  ``list_order_throttle_events``); no existing method
  signature changes (issue SMA-36206, dispatched 2026-07-21).
- ``time_in_force_gtd_p7exec_013`` — GTD (good-till-date)
  Time-In-Force builder for the live execution runner. Pure
  :class:`GTDBuilder` turns a strategy-supplied ``expires_at``
  (ISO 8601 string, ``datetime``, or epoch ``int``) into an
  absolute expiry, validates the remaining TTL against
  :class:`GTDPolicy` bounds (default 601s ≤ TTL ≤ 7 days with
  per-venue overrides) plus clock-skew tolerance, then emits
  the venue-specific wire payload — ``goodTillDate`` epoch
  seconds (Binance USDT-M / Spot), ``timeInForce='GTD' +
  orderExpiry`` epoch ms (Bybit linear / inverse / spot),
  ``expTime`` ms (OKX swap), ``timeInForce='GTD' +
  expireTime`` ms (Coinbase Advanced Trade), or a generic
  ``timeInForce='GTD' + expireTime`` ms fallback when
  ``policy.allow_generic_venue=True``. :class:`GTDHook` wires
  through the runner's pre-trade ``on_request`` loop and
  blocks via :class:`BlockReason` whenever validation fails
  (missing / malformed expiry, expired in the past, TTL too
  short, TTL too long, unsupported venue); every decision
  (ACCEPT and BLOCK_* alike) is journaled to the additive
  ``gtd_decisions`` table so a cold-start replay can rebuild
  the latest expiry per ``(venue, client_order_id)`` pair
  with :meth:`GTDRegistry.rebuild_from_journal`. Runner
  integration is purely additive — ``runner.SCHEMA_SQL``
  gains one table + three indexes; :class:`OrderJournal`
  gains three helper methods (``record_gtd_decision`` /
  ``gtd_decision_count`` / ``list_gtd_decisions``); no
  existing method signature changes. Hot-path budget
  verified at ~10us median per ``GTDHook.on_request`` call
  (pure builder + one journal INSERT — well under the
  MAP-P7 250us budget; see
  ``evidence/bench_time_in_force_gtd.json``)
  (issue SMA-36200, dispatched 2026-07-25).
- ``venue_selector_p7exec_002`` — sub-domain A (Order Routers)
  liquidity-scoring venue-routing model. For each outbound
  intent the component scores every candidate venue on six
  liquidity sub-criteria (spread, top-of-book depth, depth
  within N bps, ADV, expected impact, taker fee), ranks the
  candidates by composite ``[0, 1]`` score (with optional
  7th-term blend from the
  ``venue_reliability_score_p7exec_088`` reliability tracker
  when injected), and selects the best. The decision is
  attached to the outbound request as
  ``request["venue_decision"]`` so downstream in-loop
  components can read it, folded into the runner's ack
  envelope as ``ack["observations"]["venue_decision"]``,
  and persisted to the additive ``venue_selector_decisions``
  table (one row per ``on_request``); the
  ``venue_liquidity_snapshots`` table holds the latest
  ``(venue, symbol)`` observation per venue (UPSERT). The
  cold-start ``rebuild_from_journal`` re-derives the live
  selector's decision byte-for-byte from the journal rows
  alone. Three additive tables are installed by
  ``bootstrap_journal(journal)``; no edits to ``runner.py``
  are required — the component is wired in via
  ``register_with_runner(runner, selector)``. Eligibility
  rules (snapshot freshness, top-of-book floor, ADV floor,
  composite floor with ``min_sample_for_floor`` guard) and
  a policy-driven ``block_when_no_eligible`` switch let the
  caller govern the "no candidate passed the floor"
  behaviour; the runner's canonical ``fills`` row is never
  silently dropped (the rejection lands as a ``block`` row
  via the canonical ``BlockReason`` protocol). Hot-path
  overhead verified at ~220us median / ~235us p95 over a
  5-venue workload (see ``evidence/bench_venue_selector.json``);
  the SQLite WAL INSERT is the dominant cost (shared with
  every journaled observer on this hardware; the
  ``venue_reliability_score_p7exec_088`` companion similarly
  reports hot-path journal-write as the bottleneck). Pure
  ``score_venue(...)`` helper is 8us median; the per-rank
  pure path is ~120us for 5 venues; cold-start ``on_request``
  via the runner is verified end-to-end (live + cold-start
  + on-disk cold-start all agree to within 1e-12;
  ``evidence/smoke.json``).
  (issue SMA-36189, dispatched 2026-07-25).
- ``venue_adapter_deribit_p7exec_006`` — sub-domain A (Order
  Routers) **Deribit options + futures** REST + WS venue
  adapter for the live execution runner. Sits between the
  canonical :class:`execution.runner.ExecutionRunner` and the
  Deribit v2 ``/api/v2/private/{buy,sell}`` REST endpoints +
  ``user.orders.{kind}.{currency}`` user-orders WebSocket
  stream. One adapter covers both Deribit option instruments
  (``BTC-27JUN25-100000-C`` / ``BTC-27JUN25-100000-P``) and
  Deribit future instruments (``BTC-PERPETUAL`` /
  ``BTC-27JUN25``); the instrument parser
  (:func:`parse_deribit_instrument`) recognises both shapes
  with a single heuristic and tags the result with the kind
  (``OPTION`` / ``FUTURE`` / ``COMBO``) so analytics can
  split the flow without re-parsing the name. Pre-trade
  validation rejects unrecognised instruments, missing /
  malformed quantities, misnamed sides, unsupported
  ``order_type`` / ``time_in_force``; signing is OAuth2-style
  bearer-token (``Authorization: Bearer <access_token>``) via
  :func:`sign_deribit_request`; ``api_key`` / ``api_secret``
  are forwarded for the OAuth2 ``client_credentials`` flow
  (token refresh is the caller's responsibility via
  ``DeribitAdapter.rotate_token(...)``). Every accepted
  intent + every received ack (REST or WS) journals one row
  in the additive ``deribit_intents`` /
  ``deribit_events`` / ``deribit_acks`` tables so a
  cold-start process rebuilds the live adapter in O(N) over
  the intent projection without replaying the canonical
  ``fills`` log. REST acks are classified via
  :func:`classify_deribit_rest_ack` (``open`` /
  ``partially_filled`` / ``filled`` / ``cancelled`` /
  ``expired`` / ``rejected`` / ``untriggered``); Deribit
  error envelopes (HTTP 4xx/5xx nested under ``error.code``
  / ``error.message``) are folded into the canonical alias
  set (``INSUFFICIENT_FUNDS`` / ``RATE_LIMITED`` /
  ``INVALID_PRICE`` / etc.) with a ``OTHER:code_NNNNN`` /
  ``OTHER:<message>`` fallback when the code is unknown.
  WS ``order_state`` change frames are folded via
  :func:`parse_deribit_wss_message` +
  :meth:`DeribitAdapter.apply_wss_event`; the durable
  :class:`DeribitWssConsumer` exposes
  ``user.orders.{option,future,combo,...}`` subscription
  state + heartbeat + reconnect / halted state machine.
  Vendor-style transports
  (:class:`OutboundDeribitTransport` + paper-mode
  :class:`DeribitPaperTransport`) let the adapter run
  end-to-end against a synthetic order book without a live
  Deribit endpoint. Cold-start replay rebuilds the live
  cache from ``deribit_intents``; additive — no
  ``runner.py`` mutation required; wired in via
  ``register_with_runner(runner, adapter)``. Hot-path
  budget verified: pure ``validate_deribit_intent`` ≈ 8.5us
  median, pure ``classify_deribit_rest_ack`` ≈ 10.8us
  median, pure ``parse_deribit_instrument`` ≈ 3.7us median
  (all well under the MAP-P7 250us budget; the
  journaled ``on_request_deribit_path`` ≈ 222us median — the
  SQLite WAL INSERT is the dominant cost, shared with every
  journaled observer on this hardware). See
  ``evidence/bench_venue_adapter_deribit.json`` and
  ``evidence/smoke.json`` for the full profile; 91/91 unit
  tests + 7-stage smoke + runner integration all green
  (issue SMA-36193, dispatched 2026-07-25).
- ``venue_adapter_binance_perp_p7exec_003`` — sub-domain A (Order
  Routers) **Binance USDT-M perpetual (futures)** REST + WS
  venue adapter for the live execution runner. Sits between the
  canonical :class:`execution.runner.ExecutionRunner` and the
  Binance ``fapi`` (USD-M futures) REST API + user-data WebSocket
  stream. HMAC-SHA256 signing of outbound REST requests
  (:func:`sign_binance_perp_request`, sorted alphabetically per
  Binance's wire spec); pre-trade validation rejects non-perp
  symbols, missing prices on LIMIT orders, unsupported order
  types / time-in-force, zero qty; ``MARKET`` is accepted
  without ``allow_algos`` (algorithmic order types
  ``STOP`` / ``TAKE_PROFIT`` / ``STOP_MARKET`` /
  ``TAKE_PROFIT_MARKET`` / ``TRAILING_STOP_MARKET`` still
  require ``policy.allow_algos=True``); REST ack classification
  maps Binance status strings (``NEW`` / ``PARTIALLY_FILLED`` /
  ``FILLED`` / ``CANCELED`` / ``EXPIRED`` / ``REJECTED`` /
  ``EXPIRED_IN_MATCH``) and reject codes (-2010 / -2008 / -1013
  / -1021 / -1022 / -1003 / -2015..-2019) into canonical labels;
  WS ``ORDER_TRADE_UPDATE`` / ``ACCOUNT_UPDATE`` /
  ``listenKeyExpired`` frames are parsed via
  :func:`parse_wss_userdata_message` + folded into the additive
  tables via :meth:`BinancePerpAdapter.apply_wss_event`. Every
  accepted intent + every received ack (REST or WS) journals one
  row in the additive ``binance_perp_intents`` /
  ``binance_perp_events`` / ``binance_perp_acks`` tables so a
  cold-start process rebuilds the live adapter in O(N) over the
  intent projection without replaying the canonical ``fills``
  log; canonical ``fills`` rows are unchanged (single source of
  truth). Vendor-style transports
  (:class:`OutboundBinancePerpTransport` + paper-mode
  :class:`BinancePerpPaperTransport`) let the adapter run
  end-to-end against a synthetic order book without a live
  Binance endpoint; the durable :class:`BinancePerpWssConsumer`
  exposes ``CONNECTING`` / ``CONNECTED`` / ``RECONNECTING`` /
  ``HALTED`` state machine with ``listenKeyExpired`` triggering
  a reconnect (capped by ``policy.wss_max_reconnects``).
  ``api_key`` / ``api_secret`` are forwarded to the signer via
  policy; ``policy_fingerprint`` records only the ``api_secret_set``
  boolean (no secret material). Additive — no ``runner.py``
  mutation required; wired in via
  ``register_with_runner(runner, adapter)``. Hot-path budget
  verified: pure ``validate_perp_intent`` ≈ 2.5us median,
  ``sign_binance_perp_request`` ≈ 21us median,
  ``classify_binance_perp_rest_ack`` ≈ 4.1us median,
  ``parse_wss_userdata_message`` ≈ 7us median,
  ``on_request`` passthrough ≈ 1.2us, tagged perp ≈ 43us,
  ``on_fill`` ≈ 60us, ``apply_wss_event`` ≈ 53us, runner e2e
  ≈ 162us median (all comfortably under the MAP-P7 250us
  budget). See ``evidence/bench.json`` and
  ``evidence/smoke.json`` for the full profile; 50/50 unit
  tests + 14-stage smoke (REST + WSS + reconnect + cold-start
  reopen) + runner integration all green (issue SMA-36190,
  dispatched 2026-07-25).
"""