# Research Journal

> Append-only. Every entry: `[YYYY-MM-DD] thread-id step-type: what | links: prior | next: concrete`.
> The `links:` field is MANDATORY — isolated findings are useless.

---

[2026-07-19] BOOTSTRAP — research-journal skill + quant-researcher agent created tonight.
- 4 deep domain skills shipped (execution-microstructure / portfolio-risk / regime-macro / paper-replication), bound to strategy/worker/signoff agents.
- OPEN_QUESTIONS.md seeded with 7 threads (T01-T07) drawn from tonight's session.
- Bayesian direction KILLED (SMA-35002 5/7 gates FAIL); funding-carry-asym lineage KILLED.
- aggTrades 24GB (7 sym × 3mo) staged — enables T01/T04 real-data replication.
- SMA-34992 iceberg task produced output (106f7349) — awaiting analysis.
| links: SMA-34990/34997/35002/35005/35007/34992 | next: first research session picks T04 (analyze iceberg output) or T01 (OFI replication on real data).

[2026-07-20] T06-snapshot delivery: wrote 2026-07-18 knowledge snapshot (SMA-34981, reassigned to quant-researcher by smark-proxy 08:55+08).
- Snapshot files: `~/multica/AGENTS.md` § "Knowledge snapshots" (terse 4-bullet pointer) + `~/multica/knowledge/curator/2026-07-18-knowledge-snapshot.md` (full evidence).
- Material new finding for T06: max_dd sentinel fix (SMA-34922 / SMA-34980) **did NOT revive** `vpvr_funding_aware_v1` iter#82 — under corrected max_dd, smark-proxy verdict at 2026-07-18T17:09 was **KILL** (Sharpe 0.74 < G1, maxDD -43.07% > G3, ann passes). The methodology artefact was hiding real gate failures under single-metric W5 archive. Family `vpvr_funding_*` stays in the kill bucket; T06 revival condition (new prior source, NOT funding/VPVR) unchanged.
- Material new finding for H3 lineage: `mtf_xs_pairs` H3 BTC/SOL **PROFITABLE** ship (PR#6 commit `26440acd`, Sharpe 2.773, ann 59.8%, bootstrap CI lower 1.914). ETH/SOL leg U7 accepted via SMA-34951 but LIVE candidacy gated on G5 cross-framework CV (SMA-34966 in_review). Family NOT exhausted — this is the first live candidate from the mtf_xs_pairs family.
- Material new finding for execution substrate: k3 model-not-allowed 403 across knowledge-curator + multica-code runtimes is **now self-resolved** via M3 swap (knowledge-curator moved to Codex runtime `c3791fa0`, multica-code now on `07dd8587` with MiniMAX-M3). Sign-off chain (SMA-34959/34961/34962/34915) unblock candidate. Online-spare runtime IDs `8203abf5` / `afcdb292` listed in task body but not surfaced by agent list — flagged unverified.
- Material new finding for cron substrate: 4 heavy crons (`pool`/`orchestrator`/`decision-triage`/`signoff`) confirmed converted to wrapper-style subagent dispatches; the cron tick now wakes an idle-dispatcher subagent that does the work in-foreground instead of running inline.
| links: SMA-34981/34980/34922/34927/34878/34951/34966/34875 + `~/multica/knowledge/curator/2026-07-17-debug-summary.md` (SMA-34775) + prior JOURNAL.md BOOTSTRAP entry + OPEN_QUESTIONS.md T06 | next: (1) on next research session, return to T01 (OFI on real aggTrades) or T04 (iceberg output analysis) per BOOTSTRAP pick; (2) the H3 ship unblocks T07 (strategy-line diversification — can now include mtf_xs_pairs H3 in the OOS PnL correlation matrix); (3) T06 revival condition unchanged — do NOT retry funding/VPVR prior content.

[2026-07-20] T01 — Cont-Kukanov-Stoikov OFI on real BTC 1m aggTrades → KILL (cost-cap). SMA-35037.
- Built 1m BTC bars 2026-04-19 → 2026-06-30 (105,119 bars, ~108M raw trades) from `~/multica/quant-loop/data/trades/BTCUSDT_aggtrades.parquet/`.
- per-bar `ofi = buy_vol − sell_vol` (taker-volume imbalance from `is_buyer_maker`); z-scored over rolling window L ∈ {60, 240, 1440} min.
- Mechanistic check PASSES: `corr(z_ofi_t, mid_ret_{t+1})` = +0.206, top-bottom quintile spread = +3.41bp/trade. GROSS L/S spread Sharpe = +498 (theoretical, no cost). Signal is real.
- Cost-cap FAILS (G5): round-trip taker cost 17.83bp SPOT (10.83bp FUTURES) ≫ 3.41bp gross edge. Net edge per trade = −14.4bp SPOT / −7.4bp FUTURES.
- 0/90 sweep cells (3 lookbacks × 6 thresholds × 5 hold-bars) pass G1 (post-cost OOS Sharpe ≥ 1.0). Best cell OOS Sharpe = −33.0; CPCV over 4 subwindows: −38.9 / −37.9 / −28.6 / −31.1 (mean −34.1 ± 5.1).
- KILL verdict: signal exists but cost-bound, NOT noise-bound. v1 (SMA-34997) was killed on kline proxy (signal-noise); v2 (this) is killed on cost-cap (mechanism-vs-cost). Different gates, same kill bucket.
- Revival requires (a) sub-taker execution (maker + queue priority, eff cost <1bp), (b) T04/SMA-34992 iceberg-confluence per trade to push edge >20bp, (c) liquidation-cascade sub-regime only, or (d) fundamentally stronger signal at higher horizon — none on near-term roadmap.
- Task item 3 (pairing with pair strategy for entry optimization) is DEFERRED: gross alpha is real but too small to contribute to a pair strategy's edge unless the pair signal itself is much larger — out of scope of pure OFI.
| links: SMA-35037 (this task) + SMA-34997 (v1 KILL) + SMA-35021 (research-journal parent) + SMA-34992 (T04 iceberg, sibling) + execution-microstructure skill §Falsification (cost-cap) + paper-replication skill §Falsification gates + THREADS/T01-ofi-aggtrades.md + OPEN_QUESTIONS.md T01 (status: killed) | next: pick up T04 (iceberg output analysis from task 106f7349) next session per BOOTSTRAP; T07 portfolio-correlation can now use mtf_xs_pairs H3 LIVE numbers from JOURNAL above as the first non-prior diversified line.
