# T11 — VPVR edge-limit reversion (KILLED 2026-07-26 round-1; round-2 ALSO KILLED 2026-07-26)

## Status
**2026-07-26 20:50+08 update (round-2 verdict, this session, SMA-36661)**:
**KILL (round-2 confirms round-1)**. smark authorized methodology change in
SMA-36661 to attempt revival: finer VPVR (≤10bp buckets), 4h profile window,
z24-confirmation trigger, new cost assumption (maker 0.8bp + taker 2bp).
All four changes implemented in `vpvr_edge_round2.py`. With honest +4h shift
discipline (same audit fix class as round-1's +1d correction), 6/6 (sym ×
horizon) cells are NEGATIVE post-cost. Both smark acceptance gates (TP1-first
> 65% AND markout > +30bp) FAIL on every cell.

**2026-07-26 17:10+08 update (round-1 verdict)**: **KILL** (provisional, awaiting
smark final verdict per orchestrator flow — see [comment 712650a0-...](https://multica/comment/712650a0-eee5-4f69-8b2f-5727a8c0d8b4)).

Look-ahead bug in original `vpvr_edge_firsttouch.py` (signal_time used
D 00:00 instead of D+1 00:00) inverted the entire result set. After the
+1d shift correction, 9/9 (sym × horizon) cells are negative. Geometry
hypothesis (LVN-edge → HVN-center mean-reversion at 1d) does NOT hold on
Binance perp kline data.

**Earlier status (superseded)**: 2026-07-26 ~13:14 was SPEC candidate
maturing. Pre-check + first-touch + figures + SPEC draft all published
under branch `agent/quant-researcher/379f0585` commit `01dea43`. All
those artifacts are STALE — they reference pre-correction numbers and
are NOT the current basis for verdict. SPEC draft
`vpvr_edge_reversion_1d_20260726/SPEC.md` should NOT be promoted; if
smark decides to try a different methodology, a NEW SPEC draft replaces
this one under a fresh strategy dir.

**Round-2 pre-fix look-ahead catch**: round-2 also had a +4h shift bug
(signal_time = bucket start, not bucket end) — caught and fixed in this
session. Pre-fix numbers (TP1 86%, +36bp mean) were 100% look-ahead bias.
Post-fix honest numbers are TP1 49-63%, mean markout -7 to -19bp.

**Cycle-46 family-exhaustion update**: now applied across **two distinct
methodologies** on the same family:
1. Vanilla kline proxy (200 buckets, daily, no trigger) — round-1 KILL.
2. Finer kline proxy + 4h window + z24-confirm + lower cost — round-2 KILL.
Both kills converge on the same verdict: the geometry hypothesis
(LVN-edge → HVN-center mean-reversion at multi-hour horizon) is NOT
supported on Binance perp 1m kline proxy under honest signal timing,
regardless of profile resolution, window length, confirmation trigger, or
cost assumption.

## Question
Does the **inverse-geometry VPVR reversion** (limit entry at LVN edge,
TP1 partial at HVN center, TP2 runner at opposite LVN edge) produce
post-cost-positive alpha on BTC/ETH/SOL perp at 1d timescale?

**Answer (corrected)**: NO. Pre-registered K conditions triggered.

## Prior content — distinct from killed VPVR families (still recorded for history)
- **vpvr_xs_pairs_4h_* (T09, SMA-35167)**: 4h single-TF pair stat-arb;
  cycle-46 exhausted. This spec is single-asset MAKER, multi-TF, asymmetric.
- **T08 VPVR-funding-HVN-LVN (archived-campaign-close 2026-07-22)**:
  HVN entry → LVN exit, funding>0.03% trigger (dead 18mo). This spec is
  LVN entry → HVN exit, no funding trigger. **Both T08 and T11 now KILL
  on "next-day mean-reversion"** — symmetric failure across the two
  VPVR-axis geometries.
- **T06 funding-carry-asym**: long-pays-carry, killed on sub-gate. This
  spec has no funding trigger.
- **T01 OFI / T04 iceberg (cost-cap kills, retail-taker)**: This spec is
  maker-execution (T10 floor 9bp VIP0); T01/T04 cost-cap pattern does not
  apply here.

## 2026-07-26 round-2 measurement (this session, SMA-36661)

### Cost-cap pre-check (4h window, 730 days per symbol)

Round-2 4h profile with finer 5bp target buckets, intra-bar uniform distribution
over [low, high]. Valid windows: 4380 per symbol (6 profiles/day × 730d). All
3 symbols cleared the 4h range pre-screen (most 4h windows have valid HVN/LVN
separation ≥ 1 bucket width).

### Multi-horizon first-touch (2y BTC/ETH/SOL, 1m klines, +4h shift discipline,
#### intra-bar uniform VPVR, z24≥1.0 confirmation trigger, maker 0.8bp + taker 2bp cost)

Combined (long+short), scenario_b_defensive, post-cost (net of round-trip fee):

| Horizon | BTC markout | ETH markout | SOL markout | BTC TP1 | ETH TP1 | SOL TP1 | BTC drop | ETH drop | SOL drop |
|---------|------------:|------------:|------------:|--------:|--------:|--------:|---------:|---------:|---------:|
| 4h (240)  | **−9.8 bp** | **−17.2 bp** | **−18.5 bp** | 49.3%  | 46.8%  | 48.3%   | 27.1%   | 30.9%   | 31.3%   |
| 1d (1440)| **−7.0 bp** | **−14.0 bp** | **−15.7 bp** | 62.9%  | 60.4%  | 59.7%   | 34.9%   | 37.6%   | 38.6%   |

- 6/6 (sym × horizon) NEGATIVE post-cost.
- TP1-first rates 47-63% — improved from round-1 (45-48%) but still below
  smark 65% gate.
- Dropout rates 27-39% — unchanged from round-1 (prices BREAK the entry LVN
  edge at the same rate regardless of profile resolution).
- Fill rates 59-85% — improved from round-1 (40-63%) due to z-confirm trigger.

### Honest verdict (round-2)

Methodology change produced measurable improvement (mean markout up ~30bp on
average vs round-1; TP1 rate up ~15pp), but **every cell is still net negative
post-cost**. Both smark acceptance gates (TP1-first > 65% AND markout > +30bp)
FAIL on every cell. The geometry hypothesis is **NOT recoverable on Binance
perp kline proxy** even with:
- Finer VPVR (5bp target bucket width, intra-bar uniform distribution)
- Shorter profile window (4h instead of daily)
- Confirmation trigger (z24 ≥ 1.0 in LVN-touch direction)
- Lower cost (maker 0.8bp + taker 2bp vs VIP0 9bp pair-RT)

### Pre-registered K conditions re-triggered

| gate | threshold | round-2 status |
|------|-----------|----------------|
| K1 median OOS Sharpe | < 0.5 | TRIGGERED (-7/-14/-16bp) |
| K4 TP1 hit rate | < 50% | TRIGGERED at 4h (47-49%); PASS at 1d (60-63%) — marginal |
| K5 cycle-46 negative-fold | ≥ 1 cell negative | TRIGGERED (6/6 cells) |

K1 + K5 unambiguously TRIGGERED. K4 partial: 1d horizons clear 50% but smark
acceptance gate is 65% (per issue contract), so all 6 cells FAIL.

### Cycle-46 family-exhaustion: confirmed across two methodologies

The `vpvr_edge_reversion` family is now closed across **two distinct
methodology passes** on Binance perp kline proxy:
1. Vanilla: 200 buckets, daily, no trigger → 9/9 cells negative.
2. Refined: 5bp buckets, 4h, z24-confirm → 6/6 cells negative.

Both kills converge on the same structural verdict: LVN-edge → HVN-center
mean-reversion is NOT a property of Binance perp kline at multi-hour horizon.
This is a substrate limitation, not a methodology-sweep miss.

## 2026-07-26 measurement (corrected, after orchestrator audit)

### Cost-cap pre-check (daily window, 730 days per symbol)
Unchanged by +1d correction (pre-check uses no future data).

| Symbol | Median half-range | Median full-range | pct windows with full-range > 30bp |
|--------|------------------:|------------------:|----------------------------------:|
| BTCUSDT | 80bp             | 160bp             | 99.7%                              |
| ETHUSDT | 121bp            | 242bp             | 99.9%                              |
| SOLUSDT | 144bp            | 287bp             | 100.0%                             |

Cost-cap floor clears at structural distance level. **But cost-cap not
the binding constraint** — the issue is whether price actually MEANS-REVERTS
to HVN within 1d, not whether the structural distance is large enough.

### Multi-horizon first-touch (2y BTC/ETH/SOL, 1m klines, scenario_b_defensive, +1d shift correction)
Combined (long+short) results, signal_time = D+1 00:00:

| Horizon | BTC markout | ETH markout | SOL markout | BTC TP1 | ETH TP1 | SOL TP1 | BTC drop | ETH drop | SOL drop |
|---------|------------:|------------:|------------:|--------:|--------:|--------:|---------:|---------:|---------:|
| 1h (60)  | **−52 bp** | **−88 bp** | **−95 bp** | 7.0%   | 5.4%   | 3.1%   | 22.3%   | 25.5%   | 24.2%   |
| 4h (240) | **−37 bp** | **−58 bp** | **−71 bp** | 19.7%  | 20.5%  | 18.1%  | 25.3%   | 27.9%   | 26.4%   |
| 1d (1440)| **−28 bp** | **−42 bp** | **−46 bp** | 48.1%  | 45.9%  | 48.0%  | 37.1%   | 39.1%   | 37.0%   |

- 9/9 cells (sym × horizon) NEGATIVE after +1d correction.
- TP1-first rates 45-48% — essentially coin flip, NOT 80%.
- Dropout rates 37-39% at 1d — price BREAKS entry LVN more often than
  it reverts to HVN center.
- Fill rate 63% (1d) — only ~2/3 of LVN limits get filled within the next day.

### Honest verdict (corrected)
The geometry hypothesis (LVN-edge entry reverts to HVN center at 1d) is
**NOT supported**. This is a mechanism kill, not a cost-cap kill. The
issue is that LVN edges on 1d Binance perp klines are NOT mean-reversion
attractors in the next 24h — they get broken 37-39% of the time.

### Pre-registered K conditions triggered
- K1 (median OOS Sharpe < 0.5): TRIGGERED — unconditional mean
  -28/-42/-46 bp.
- K4 (TP1 hit rate < 50%): TRIGGERED — three sym all 45-48%.
- K5 (cycle-46 structural negative-fold): TRIGGERED — 9/9 cells negative.

KEEP/KILL final verdict pending smark decision per orchestrator flow.
**My recommendation: KILL the current geometric hypothesis on Binance perp
kline proxy. Any revival must use stronger data (tick-level profile or
orderbook depth).**

## Why this happened (bug + discipline lesson)

**Look-ahead bug**: `vpvr_edge_firsttouch.py` originally had
`signal_time = setup.window_end`, which in my code = day-D's 00:00 (the
FLOOR of the profile-building day). The intended signal time was
D+1 00:00 (start of next day, after the day's profile is finalized).
With D 00:00, the "future horizon" walked through the SAME day's bars
that built the profile — pure look-ahead. Pre-correction results
(TP1-first ~88%, mean markout +73~+127bp) reflected **intra-day reflexive
patterns** (either the day's own LVN gets hit, or the day's own HVN gets
hit), not next-day mean-reversion. Orchestrator caught this at 17:10+08
and the +1d shift was applied. The corrected numbers are honest.

**Discipline lesson**: pre-registered K conditions are valuable only if
checked against pre-registered measurement, not post-hoc discovery.
Future T* threads MUST run sanity checks like "do I ever observe
future < setup_time?" to catch similar bugs.

## Revival conditions (this hypothesis is KILLed until ALL of these hold)

1. **Tick-level profile data** — kline-derived HVN/LVN approximation has
   geometric bias of single-digit bp (per `proxy_calibration.csv` audit
   Item 1, basic PASS) but loses wick-level / orderbook depth information.
   Real Bookmap / TradingView Profile+Volume-at-Price data is required
   to confirm the geometry assumption before any further attempts.
2. **OFI-augmented signal** — limit orders at LVN edges hit by sweep
   events have negative markout (Albers 2025 dilemma). Filter or augment
   with OFI to avoid getting picked off.
3. **Liquidation-cascade sub-regime only** — the only condition under
   which mean-reversion to LVN/edge may hold (since cascades create
   forced flow that auto-reverts).
4. **Regime gate** — restrict to high-vol-of-vol windows where mean-
   reversion magnitude is large enough to clear the maker AS cost.
5. **Stronger cost model** — VIP0 9bp pair-RT is the T10 floor; if our
   realized cost is 12+bp (entry maker + exit taker on TP1/SL), the
   negative mean markout is even more negative.

**Only if smark greenlights a methodology change AND any ONE of these
conditions is met** should a new SPEC candidate be drafted.

## Artifacts (now stale — kept for reproducibility)
- Pre-check JSON: `~/multica/quant-loop/research/vpvr_edge_reversion/precheck_summary.json`
- Daily metrics parquet: `daily_metrics_{SYM}.parquet`
- First-touch parquets: `firsttouch_{SYM}_{1h|4h|1d}.parquet` (corrected)
- First-touch summary: `firsttouch_summary.json` (corrected)
- First-touch horizon summary: `firsttouch_horizons.json` (corrected)
- Proxy calibration: `proxy_calibration.csv`
- Scripts: `vpvr_edge_precheck.py`, `vpvr_edge_firsttouch.py` (corrected),
  `vpvr_edge_firsttouch_horizons.py`, `vpvr_proxy_calibration.py`,
  `make_figures.py`
- Figures: `figures/fig{1,2,3}_*.png` (reference numbers in titles
  match PRE-CORRECTION data; treated as superseded)
- SPEC draft (stale): `trading/strategies/vpvr_edge_reversion_1d_20260726/SPEC.md`
  — should NOT be promoted, figures/SPEC body reference pre-correction
  numbers. Stale SPEC should be marked `superseded` if smark confirms KILL;
  if smark supports continuing, a new draft must replace it under fresh
  strategy dir.

## Links
- SMA-36615 (parent — current status in_review, KEEP/KILL final verdict
  pending smark)
- [orchestrator EVIDENCE comment 712650a0-...](https://multica/comment/712650a0-eee5-4f69-8b2f-5727a8c0d8b4)
  (the audit that caught the look-ahead bug at 17:10+08)
- SMA-36598 (T10 pre-SPEC, cost-cap floor upstream — still in flight,
  independent of this KILL)
- SMA-34901 (T08 VPVR-confluence archived — both T08 and T11 now KILL
  on next-day mean-reversion, distinct geometries but shared failure mode)
- SMA-35167 (T09 vpvr_xs_pairs_4h KILL — cycle-46 family-exhaustion baseline)
- SMA-34990 (T06 funding-carry-asym KILL — funding prior content)
- SMA-35037 (T01 OFI cost-cap KILL — taker-side microstructure)
- SMA-35021 (T04 iceberg cost-cap KILL — maker-side absorption)
- multica-agent-base §strategy-layer (cycle-46 family-exhaustion rule)
- execution-microstructure skill (cost-cap falsification)
- paper-replication skill (35-cell sweep + Aumann test)
- research-journal skill (kill-with-reason + revival-condition discipline)