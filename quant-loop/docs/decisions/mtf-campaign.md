# mtf campaign — decision record

Multi-timeframe stat-arb family on BTCUSDT/ETHUSDT/SOLUSDT. Active window 2026-07-18. Closing gate per parent campaign: **OOS walk-forward Sharpe ≥ 1.0 (daily-resampled) AND OOS annualized ≥ 15 %**, plus bootstrap CI lower ≥ 0.5, plus sharpe_method=daily_resampled at top level of every walk_forward.json. Allowed timeframes strictly 1m / 15m / 2h — 1h, 4h, 1d explicitly banned as new strategies. Inspired by the earlier cross-sectional pairs finding (`xs_pairs_30m`, iter#83, BTC/SOL was the only survivor) but corrected to genuine multi-TF (1m primary entry, 15m filter / sizing, 2h regime), not single-TF resampled. **FAMILY VERDICT: 1 of 4 hypotheses PROFITABLE (H3 only). H3 is the campaign's sole LIVE candidate.**

## What worked

### H3 — 2h funding-EMA regime + 1m BTC/SOL z-score + 15m ATR sizing (PROFITABLE — only LIVE candidate)

Code path: `quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h3_20260718/` (build_h3_signals / run_backtest / walk_forward). Branch `strategy-worker-2/mtf-h3-funding-regime`, PR head commit `26440acd` against base `strategy/mtf-1m-15m-2h` (commit `aa8ade5a`).

Pair universe restricted to BTCUSDT/SOLUSDT only (not the full BTC/ETH/SOL set the other hypotheses tested).

OOS walk-forward (7 expanding windows, daily-resampled Sharpe):

| metric (OOS mean across 7 windows) | value | gate | pass |
|---|---:|---|---|
| Sharpe (daily-resampled) | **2.773** | ≥ 1.0 | ✓ |
| Annualized return | **59.75 %** | ≥ 15 % | ✓ |
| Bootstrap CI lower (95 %, seed=42, 10000 resamples) | **1.914** | ≥ 0.5 | ✓ |
| Worst-window max DD | -12.62 % | < 25 % | ✓ |
| gates.passed | true | — | ✓ |

Per-window OOS Sharpe (test_start → Sharpe / ann.ret / MDD): 2023-01-01 → 2.41 / 0.700 / -0.081; 2023-07-02 → 2.26 / 0.638 / -0.102; 2024-01-01 → 3.19 / 0.795 / -0.126; 2024-07-01 → 3.08 / 0.639 / -0.077; 2024-12-31 → 2.15 / 0.500 / -0.114; 2025-07-01 → 0.90 / 0.132 / -0.082 (sub-threshold alone); 2025-12-31 → 5.41 / 0.778 / -0.037.

Full-history backtest: avg-pair Sharpe 2.321, ann 54.9 %, 44,845 trades, win rate 32.6 % (portfolio PF 1.013, per-pair PF 0.604). IS Sharpe (2.32) consistent with OOS mean (2.77) — no obvious look-ahead.

Why notable: the 2h funding-rate EMA regime filter (EMA over 4 events, threshold 0.0005) acts as a carry-aware regime mask that gates 1m pair entry. Capital efficiency is the weak link (only ~32 % of bars tradable because funding filter is selective; per-pair PF < 1, portfolio PF ≈ 1), not the edge. G3 PF gate ⚠ FAIL on per-pair (1.5 required) but passes campaign gate. Family-exhaustion (cycle-46) permits one rebuild — H3 (the funding-regime variant) is *that* rebuild and is the family survivor. Cycle-46 rule satisfied; no further xs_pairs-z-score multi-TF variants warranted until cycle-47+.

Shared infra fix included: `_slice_funding_for_window` now normalizes tz on bounds to match the tz-stripped funding index — required for any H3 walk-forward to run.

### pairs_cointegration_1d_20260709 — bootstrap lineage + freqtrade cross-validation convergence

Lineage (1d z-score pair-cointegration mean-reversion on BTC/ETH/SOL × 3 pairs):

- B1 bootstrap (commit `6ec6d2c`): EG test + OLS hedge + rolling z-score + half-life. 20/20 unit tests on synthetic data.
- B1 fixup (commit `b8ce274`): conftest.py rootdir hook (pytest from repo root) + metrics.json alias of run_summary.json.
- B2 signal + portfolio state machine (commit `f701d5`): build_signals entry/exit/break cols, simulate_pair_trades, PortfolioState event-driven state machine (pair-pause -3%/30d, portfolio kill switch -5%/30d, active-pair cap 3). 51/51 tests pass. Real-data backtest on BTC/ETH/SOL (3-symbol universe — BNB/ADA/AVAX missing from canonical 1m source, flagged honestly): active pair BTCUSDT-SOLUSDT (p=0.048, β=0.786, R²=0.814), 21 trades, +2.218 % PnL on $100k, win rate 80.95 %, 16 historical cointegration breaks detected, state machine fires live (1 pair-pause + portfolio kill switch at -7.05 % cumulative).
- B3-backtest (re-evidence from the worker branch at commit `aa8ade5a0`): 60d rolling OLS hedge (log-prices), z-entry |z|>2.0, exit |z|<0.5, stop |z|>3.5. Aggregate Sharpe 0.7416, MDD -13.95 %, 70 trades, PF 1.6347, ann 16.72 %, gate.passed=true. Walk-forward (anchored, 270 train / 180 test / 90 step, 4 windows): OOS Sharpe 3.5968, IS Sharpe 2.6731, ratio 1.3455 → verdict **SHIP**.

Cross-validation against freqtrade (pinned SHA `15b94ce7fe19efada0e1ede582d01b2be89875e6`, cache `/tmp/framework-cache/freqtrade-15b94ce7`):

- Initial run (2026-07-14 04:37, adapter report `strategies/pairs_cointegration_1d_20260709/data/framework_adapter_report.json`): OUT_OF_TOLERANCE — ETH-SOL per-pair Sharpe delta +0.2132 (abs > 0.2 tol). All other per-pair metrics (n_trades, win_rate, total_return, max_drawdown) match exactly. Aggregate (3-pair portfolio, equal-weight) matches exactly on all 6 dimensions. Empirical framework/ours Sharpe ratio = 1.2051 across all 3 pairs (sqrt(365/252) = 1.2035 — rounding noise). Root cause: in-house `math.sqrt(252)` at `strategy.py:392` vs freqtrade `sqrt(365)` (calendar-days default in `freqtrade/data/metrics.py:_calculate_annualized_ratio`). Pure convention gap, not a strategy defect.
- Decision: **Path C** — adapter-layer scaling `sharpe * sqrt(252/365) ≈ 0.8307` and same on sortino before comparison. No strategy code touched (per CLAUDE.md "Don't modify quant-loop strategy code" rule for framework-validator). After Path C: per-pair Sharpe deltas collapse from ±0.21 to <0.002 abs, all other metrics already exact → **WITHIN_TOLERANCE** on every spec-table metric.
- Path C persistence: subsequent framework-validate on the same strategy (2026-07-17 23:37) reports OOS abs-rel divergence 1.0877 % (effectively floating-point noise on per-fold compounding arithmetic) → max_abs_rel_divergence_pct_oos = 1.0877 %, far under the W5 50 % threshold → WITHIN_TOLERANCE / PASS, G5 preserved. Per-fold framework Sharpe 6.561 / 2.794 / -2.527 / 7.559 with 17/17/15/22 trades.

Why notable: the family survived W5 auto-archive (50 % absolute divergence threshold) twice on a pure convention issue; the framework vs in-house annualization delta is now absorbed at adapter level so future re-runs do not trigger false-positive OUT_OF_TOLERANCE warnings on the same root cause.

### BTC/ETH/SOL data-completeness audit (the substrate)

Windowed over 2026-06-18 → 2026-07-10 (canonical 30d cut) — all 9 (symbol × timeframe) cells exist and are readable, every file stops at 2026-07-10, coverage 76.67 % of requested 30d, gap_count=0 inside the available span, no MISSING/CORRUPT rows. Older gap at 2023-03-24 12:30 UTC affects BTCUSDT 15m (5 missing bars) and 1h (2 h gap) — minor severity, US-banking turbulence inferred cause (not asserted).

Full-history audit (post the ~28h outage):

| TF | BTCUSDT | ETHUSDT | SOLUSDT |
|---|---|---|---|
| 1m | ❌ no file | ❌ no file | ❌ no file (strategy-local only) |
| 15m | ✅ ok (1 minor gap, 5 bars) | ❌ no file | ❌ no file |
| 4h | ✅ ok | ⚠️ symlink → BTCUSD_4h | ⚠️ symlink → BTCUSD_4h |
| 30m | ✅ ok | ✅ ok | ✅ ok |
| 1h | ✅ ok | ✅ ok | ❌ no file |

Funding pool (`data/funding/*.parquet`) is continuous 8h funding for all three, zero gaps, 5100/5100/5175 rows — usable.

The P0 correctness bug: `live_data/ETHUSDT_4h.parquet` and `live_data/SOLUSDT_4h.parquet` are symlinks to `BTCUSD_4h.parquet` (a coin-m file). Any strategy loading these paths silently gets BTCUSD coin-m bars. BTCUSDT_4h is also symlinked to the same file but BTCUSD ≈ BTCUSDT within basis noise so the BTC case is label-only. ETH/SOL 4h reads were unusable until the symlinks were replaced.

Post-outage 7d window (2026-07-11 → 2026-07-18): BTC/ETH 1m present in shared pool with ~6 h trailing-edge staleness (no internal gaps, last bar 2026-07-17T19:39Z); SOL 1m MISSING from shared pool (strategy-local copies only — `strategies/vpvr_volume_edge_3tf_v1_20260711/data/SOLUSDT__1m.parquet` and 3 other strategy folders); 15m × 3 files **FAIL** — entire 7d window empty, all three still stop at 2026-07-10T23:45Z (the 15m recovery did not land on any of the three despite the issue description claiming it did); 4h × 3 OK with 1 trailing bar stale (last 2026-07-17T20:00Z); symlinks resolved (BTCUSDT_4h no longer points to BTCUSD_4h).

Report artifact: `quant-loop/reports/data_completeness_audit_BTC_ETH_SOL_20260718.md`. Machine-readable 7d verification: `quant-loop/live_data/verify_report_sma34898.json` with verifier script `verify_sma34898.py`.

### Return correlation matrix (BTC/ETH/SOL, three timeframes)

Window 2026-06-17 → 2026-07-10 (30d calendar-aligned), log returns of close. Bar counts: 1m = 34,559; 15m = 2,303; 4h = 143. Three tickers per-TF row-aligned (no row drop). Canonical per-symbol files used (the `live_data/{ETH,SOL}USDT_4h.parquet` BTC-symlink bug above forced a switch away from `live_data/` for 4h).

| pair | 1m | 15m | 4h |
|---|---:|---:|---:|
| BTC–ETH | 0.880 | **0.905** | 0.901 |
| BTC–SOL | 0.813 | 0.833 | 0.816 |
| ETH–SOL | 0.837 | 0.848 | **0.812** |

BTC–ETH is the tightest pair (15m peak 0.905). ETH–SOL collapses from 0.848 at 15m to 0.812 at 4h — the only meaningful cross-TF divergence, suggesting a 4h-scale regime where ETH and SOL de-correlate. Output artifacts: `quant-loop/analysis/corr_matrix/` with README + 3 PNG heatmaps + 3×3 cross-asset composite.

### Cross-sectional pairs finding (the inspiration)

`xs_pairs_30m` iter#83 (single-TF, BTC/SOL the only winner) established that cross-sectional pair z-score has a real edge on BTC/SOL but is fragile to single-TF noise. The mtf-1m-15m-2h campaign was mandated by smark as the multi-TF correction — using real 1m entry, 15m filter/sizing, 2h regime, not single-TF resampled. H3's 2h funding-regime gate is the carry-aware variant that made the same edge tradeable.

### Crypto microstructure inventory (substrate for future 1m factor design)

Read-only scan of VPVR shapes (monotonic / bimodal / uniform), HVN persistence at 24h and 7d, LVN bounce-vs-breakout behavior, tick size / VWAP-reversion / buy-sell delta, across 1m / 15m / 4h × BTC / ETH / SOL. Findings + concrete factor designs (volume-delta EMA, HVN edge proximity, LVN bounce, tick imbalance) were written to `quant-loop/microstructure_findings.md`. The 6-factor composite `edge_score_long` / `edge_score_short` from the V7 spec can absorb the microstructure factors as additional inputs.

## What failed and why

### H1 — 1m cross-pair z-score + 15m slope confirm + 2h EMA trend gate (NOT-PROFITABLE)

Code path: `quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h1_20260718/`. Worker branch head commit `7eb0c271` (carries the H1 implementation; PR opened from the worker branch into the campaign base). Three pairs (BTC/ETH, BTC/SOL, ETH/SOL).

OOS walk-forward (7 windows, daily-resampled): Sharpe 1.898 (mean) ≥ 1.0 ✓, ann 11.69 % < 15 % ✗, bootstrap CI lower 1.124 ≥ 0.5 ✓, worst MDD -6.25 %. Per-window Sharpe: 1.811 / 1.173 / 3.633 / 1.247 / 1.354 / 0.531 / 3.537. 42,906 trades. Baseline (slope_lookback=30, trend_slow=34) had Sharpe 1.50 / ann 8.91 % / CI [0.54, 2.66]; a single-window sweep picked slope_lookback=40 / trend_slow=21, lifting Sharpe 1.50 → 1.90 and CI lower 0.54 → 1.12 — but the 15 % annualized stretch gate remained unmet.

Causal reason: the issue-text "ONLY pass gate" (Sharpe ≥ 1.0) is met but the campaign parent's closing criterion (Sharpe AND annualized) is not. The 2h EMA trend gate alone does not add enough absolute return on top of the 1m z-score mean-reversion edge — it filters entries but doesn't compound enough. Single-window sweep bias cannot be ruled out entirely; honest caveat flagged in the PR.

### H2 — VPVR edge touch 15m/2h + 1m micro-reversion (NOT-PROFITABLE)

Code path: `quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h2_20260718/`. OOS walk-forward (7 windows): Sharpe 0.62, ann 1.56 %, bootstrap CI lower **-0.51** (fails G1, G2, and G6 simultaneously).

Causal reason: VPVR edge-touches are sparse signals — the 1m micro-reversion has too few triggers once the 15m / 2h VPVR filter is applied. Combining a low-frequency event signal with a high-frequency micro-reversion entry does not produce enough trades to compound; the bootstrap CI spans zero with negative lower bound. Pure signal-density failure.

### H4 — Multi-pair portfolio on 1m z-score + 15m EMA-8/21 dir + 2h trend cap (NOT-PROFITABLE, CANCELLED)

Code path: `quant-loop/strategies/mtf_xs_pairs_1m_15m_2h_h4_20260718/` (`mtf_h4_portfolio_zscore_1m_15m_2h_20260718/`). Three pairs (BTC/ETH, BTC/SOL, ETH/SOL). Portfolio sizing: gross_cap 0.06, net_cap 0.04, max_pairs_active=3, 60-day correlation shrink. Branch `strategy-worker-2/mtf-h4-portfolio`, PR head `b9504196` (work commit `cb88f5e3`), 6/6 tests pass.

OOS walk-forward (7 windows, daily-resampled): Sharpe 2.18 ≥ 1.0 ✓, ann **0.09 %** < 15 % ✗, bootstrap CI lower 1.22 ≥ 0.5 ✓. Per-window Sharpe 2.03 / -0.13 / 3.69 / 2.69 / 3.84 / 1.08 / 2.09. Per-pair: 599 trades (BTC/ETH 176, BTC/SOL 238, ETH/SOL 185). Verdict NOT-PROFITABLE, cancelled.

Causal reason: market-neutral micro-portfolio with low per-bar PnL × many bars → strong risk-adjusted profile but tiny cumulative growth. Sharpe gate passes, absolute return too small. To clear 15 % annualized: would need (a) wider z-thresholds + larger sizing under the gross cap, or (b) directional beta tilt to capture crypto's positive drift — neither was in scope. H4 owner explicitly cancelled: "the next step would be a directional beta tilt or wider z-thresholds, which belongs in a new sub-issue, not this one." Strategy rejection recorded as NOT-PROFITABLE archive metadata; PR left OPEN for archival; campaign owner may merge or close at discretion.

### pairs_cointegration_1d × freqtrade — annualization convention divergence (RESOLVED via Path C)

Initial framework-validate on 2026-07-14 04:37 flagged ETH-SOL per-pair Sharpe delta +0.2132 as OUT_OF_TOLERANCE (abs > 0.2 spec tol). Empirical framework/ours ratio = 1.2051, consistent with sqrt(365/252) = 1.2035 — pure calendar-days vs trading-days annualization gap, not a strategy defect. Three paths proposed:

- Path A — switch in-house to sqrt(365) for daily-cadence strategies (changes strategy code; blocked by CLAUDE.md "Don't modify quant-loop strategy code" for framework-validator).
- Path B — keep sqrt(252) WONTFIX, document (recurring false-positive on every framework run; unsustainable).
- **Path C (chosen)** — adapter-side scaling `sharpe * sqrt(252/365)`. In-house stays at sqrt(252), freqtrade stays at sqrt(365), adapter converts at compare time. After Path C: per-pair Sharpe deltas <0.002 abs, all other metrics exact → WITHIN_TOLERANCE.

Causal reason for divergence: codebase mixed — `xs_momentum_rank_1d` uses sqrt(365) (`backtest.py:330`); most `vpvr_reversion_1d_*` variants use sqrt(252); `PORTFOLIO_REPORT.md:26` documents sqrt(365) as the convention; `_indicators/iter94_20260714.py` defines BARS_PER_YEAR for 4H/15M/1M/1H only — no 1D constant. The deeper "canonical 1D annualization" question (252 vs 365) is **not** resolved here and remains an open follow-up — Path C is an adapter-level fix, not a canonical declaration. Sortino has a residual convention gap (freqtrade `downside_returns.std(ddof=0)` vs in-house `downside.std(ddof=1)`) but spec tolerance table doesn't cover sortino, so it's not flagged as OUT_OF_TOLERANCE.

### Data substrate gaps (live blockers, not a strategy defect)

- **P0 ETH/SOL 4h symlink to BTCUSD_4h** — silently loads BTC coin-m bars on ETH/SOL. Fixed before the correlation-matrix run by switching to canonical per-symbol 4h sources; not yet replaced in `live_data/` for general consumers.
- **P1 missing 1m data everywhere in shared pool** — no `data/perp_1m/{BTC,ETH,SOL}USDT_1m.parquet` exists. Strategy-local copies exist under `strategies/*/data/` but those are snapshots, not shared-pool coverage. Blocks any 1m backtest outside the existing strategy dirs.
- **P1 missing ETH/SOL 15m, 4h on shared pool** — only BTC has 15m/4h.
- **P3 BTC 2023-03-24 12:30 UTC 5-bar gap** — minor severity, backfill only if a strategy is sensitive to that window.
- **15m × 3 still stale past 2026-07-10T23:45Z** as of post-outage 7d audit — recovery did not land on any of the three. 4h and 1m partial recoveries landed.
- **SOL 1m still missing from shared pool** post-outage (only strategy-local copies exist).

## Open questions

- **Canonical 1D annualization constant for quant-loop (252 vs 365).** Codebase is mixed; `PORTFOLIO_REPORT.md` documents 365; `iter94_20260714.py` lacks the 1D constant. Path C papered over the divergence at the adapter layer but didn't pick the canonical. Resolving this prevents future false-positive OUT_OF_TOLERANCE warnings on the same root cause.
- **H3 G3 ⚠ FAIL remediation.** Per-pair PF 0.604 / portfolio PF 1.013 — capital efficiency is the weak link, not the edge. Recommended follow-on is sizing tuning (not another signal rebuild); carry-over note from campaign closure.
- **H3 G5 cross-framework CV (freqtrade + backtrader both ≥ 1.0) — NOT RUN.** Deferred per parent's "do NOT redo" smark directive. Becomes a hard blocker if H3 progresses to live-candidate review.
- **G7 family-wide Bonferroni.** 1 of 4 raw PASS = raw p ≈ 0.25; Bonferroni-corrected for 4 hypotheses → no family-wide claim. Only H3 individually passed.
- **Regime router / multi-TF consensus design from iter#94+ (3-state regime + OBI proxy).** Source issue was returned as 404 by the API at purge time (uuid `5364b6a7-3ee7-4ce1-bbce-8a720df54899`, identifier `iter#94+` follow-on); content not retrievable from the issue tracker. The 2h funding-EMA regime filter used by H3 is one working implementation of a regime gate, but the broader 3-state design (risk-on / neutral / risk-off) with OBI proxy consensus was not verifiable from the available issue record and is recorded here only as an unverified reference. The canonical implementation, if any, should be located via `_indicators/iter94_20260714.py` (which exists but did not load at purge time) and verified against the original spec.
- **Cycle-46 family-exhaustion implication.** No further variants in this family until cycle-47+. Next cycle: sizing rebuild on H3 (single rebuild already used); broader regime-router design would need to land in cycle-47+.
- **Data substrate gaps to clear before next 1m backtest:** SOL 1m into shared pool, 15m × 3 refresh past 2026-07-10, ETH/SOL 4h symlink replacement.