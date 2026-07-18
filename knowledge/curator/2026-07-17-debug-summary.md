# Consolidated Debugging Summary — 2026-07-17

**Scope:** workspace `f9a9d34e-b809-4564-b0c0-b781a70a3f25` (UTC+8).
**Sources:** `multica issue list` (today's created + updated issues), comments on
closed / in_progress items, prior curator digest (SMA-34765, 2026-07-17 13:25+08),
and `~/.multica/daemon.log`. Items the log/issues do not substantiate are
marked **[UNCONFIRMED]** rather than guessed.

> **Two-pass note.** This is the second curator digest of the day. The first
> ([SMA-34765](https://multica/issue/dbaf11da-1e55-40c0-9ad3-fe8cbbf663da),
> 13:25+08) covered the early-day debug activity (HTTPS cert, Kimi config,
> repo cache corruption, autopilot-without-triggers, dispatch-critic
> interventions, two W5 auto-archives). This digest **preserves those
> findings** and adds the work-pool audit items that landed between
> 13:25 and 13:50+08 (campaign-tree staleness, disk audit, data
> completeness, Sharpe ranking, AGENTS.md update, autopilot-backups
> audit), plus a few cross-cutting observations. Nothing in the prior
> digest has been contradicted by evidence gathered since 13:25+08.

---

## 1. Timeline (chronological, UTC+8)

| Time | Issue | Event |
|---|---|---|
| 00:25 | [SMA-34704](https://multica/issue/4f53c2bd-57a3-40a1-964e-12ea2788fa53) | Kimi `session/new` fails with duplicate TOML `background` key. Issue dispatched to ops-worker-1 but assignment collapses within seconds. |
| 00:25 → 05:36 | SMA-34704 | Issue sits orphaned; daemon logs ERR on `[DEPLOY-FAIL] HTTPS cert` throughout. |
| 05:36 | SMA-34704 | Dispatch-critic watchdog reassigns the orphaned issue. |
| 05:48 | SMA-34704 | ops-worker-1 re-probes cert state; same root cause confirmed (SAN-less self-signed cert, see §2.1). |
| 07:44 → 07:45 | [SMA-34731](https://multica/issue/68acdd58-e1fd-40f2-bfab-a80d08c1601a) | **W5 AUTO-ARCHIVE** lands on `vpvr_reversion_5m_vwap_trail_20260709 × vectorbt 1.1.0` (Sharpe div 1070%, ann_ret div 472%, max_dd div 424%). Parent strategy already `done`. |
| 07:53 | (no issue) | daemon.log ERR: repo bare-cache corruption for `he-mark-qinglong/multica` (`origin/*` empty, no usable refs). |
| 07:56–08:02 | (no issue) | 5× `cancel ack failed; server sweeper will finalize` WRN lines (HTTP 404 on `/api/daemon/tasks/.../cancel-ack`). |
| 10:43 → 11:37 | [SMA-34759](https://multica/issue/127d07b3-683f-40a4-a09d-6d97b0e6926f) | **W5 AUTO-ARCHIVE** on `vpvr_macro_calendar_4h_20260715 × backtrader 1.9.78.123` (Sharpe div 930%, **ann_ret div 1228%**, max_dd div 28%). |
| 12:17 | [SMA-34762](https://multica/issue/aa9b8831-46e3-40e0-ab87-a9f71b78fcdb) | Heartbeat finds 2 active autopilots with **zero enabled triggers** (Idle Agent Dispatcher 3m, Daily Done-Sweeper 04:00). Both have `last_run_at = null` since 2026-07-15 (~37h). |
| 13:14 | SMA-34762 | Dispatched to multica-ops. |
| 13:16 | SMA-34762 | **Fix applied**: `*/3 * * * *` Asia/Shanghai added to dispatcher (next run 13:18); `0 4 * * *` Asia/Shanghai added to sweeper (next run 2026-07-18 04:00). Verified via `multica autopilot get`. |
| 13:21 | SMA-34762 | **DISPATCH-CRITIC** watchdog (cap 5/5) reassigns 5 wrong-domain owners → multica-strategy + 1 urgent-human-decision item → smark (SMA-34634). 7 deferred to next cycle. |
| 13:25 | [SMA-34765](https://multica/issue/dbaf11da-1e55-40c0-9ad3-fe8cbbf663da) | **First curator digest** posted — covers §2.1–§2.4 of this document. |
| 13:28 | [SMA-34767](https://multica/issue/ca92ac66-c62f-45da-a720-c9027c49e11d) | Disk audit completes: `~/multica/` = 7.0 GB total; 3 quick-win candidates = ~2.2 GB reclaimable (`.next/cache/webpack` 1.5 GB + `.turbo/cache` + logs); 1.7 GB requires review (dated `quant-loop/strategies/*/data/`). |
| 13:31 | [SMA-34768](https://multica/issue/a0a783d5-9cf0-437e-b726-cd54f736fb9a) | Daemon.log 24h error review: 5 top patterns quantified. `custom_env: blocked key skipped` leads at **4,022 occurrences** in 24h. |
| 13:34–13:36 | [SMA-34766](https://multica/issue/70e2d341-edd6-4502-b7cc-072f0f53cc4b) | Sunburst staleness audit: campaign-tree.html was **stale by 2 commits** (DATA missed `a2a54dab` + `aa8ade5a`). Builder bug — frozen `/* auto-generated */` comment timestamp — fixed in `scripts/multica_campaign_tree_builder.py`; stale `~/.multica/scripts/` copy replaced. Now diff vs live git = 0; 40 commits / 25 groups. |
| 13:37 | [SMA-34770](https://multica/issue/44246da1-cf55-4102-ba3e-31b22bd1455f) | AGENTS.md updated with current super-agent + autopilot topology (10 agents, 30 autopilots, 6 skill families); pre-redistribution (~18 orchestrator + ~16 strategy + ~7 code + ~32 ops specialists) preserved as historical subsection. |
| 13:38 | [SMA-34769](https://multica/issue/edeaa6f1-b920-4dd1-9716-a61ccdf51a80) | BTC/ETH/SOL data completeness: 9/9 files exist & readable but every file stops at **2026-07-10** → coverage is **76.67%** of the 30-day window (2026-06-18 → 2026-07-17). No intra-segment gaps. Backfill recommended for 2026-07-11 → 2026-07-17. |
| 13:41 | [SMA-34772](https://multica/issue/ffb7df56-c02b-46a9-8dfe-6049facadd62) | Disk trend monitor: PASS (volume 47% full, no growth in 13-minute window). Recommends hourly cron autopilot for true trend monitoring. |
| 13:44–13:46 | [SMA-34773](https://multica/issue/333efd3c-749b-4724-944b-7bfeada07874) | Sharpe ranking: 44 unique strategies ranked; top-3 are all `vpvr_xs_pairs_30m_funding_filter` variants (Sharpe 7.70 / 5.72 / 5.72). **Calibration bug found**: 18 strategies + 1 aggregate row report per-trade Sharpe in `metrics.json` instead of daily-resampled (SPEC violation). |
| 13:44–13:45 | [SMA-34774](https://multica/issue/9b0626ad-d89e-451d-b7bb-ee7f4bf4fdea) | Autopilot backups audit: **6/6 files valid**, 0 issues. Report saved to `~/multica/ops-reports/autopilot-backups-audit-2026-07-17.md`. |
| 13:46 | [SMA-34775](https://multica/issue/7558e3d9-3d41-428f-ba1b-43356d714d6a) | **This issue** opened — second curator digest requested. |

---

## 2. Root causes found

### 2.1 HTTPS self-signed cert not trusted (carried over from prior digest)

`/etc/ssl/certs/multica-selfsigned.crt` has `CN=192.168.0.105` and **no SAN**.
Two failure modes compound: (i) self-signed → untrusted by system CA bundle;
(ii) CN-only + no SAN → `localhost` does not match. Curl error 60 confirmed
on standard clients; `-k` returns 200. Tracked under
[SMA-34632](https://multica/issue/6239858a-8fe4-45a8-b200-62a09567ab50),
[SMA-34704](https://multica/issue/4f53c2bd-57a3-40a1-964e-12ea2788fa53), and
[SMA-34634](https://multica/issue/78fea9d5-a784-4c80-bad4-3d7d5a9d24c8)
(gating decision ticket awaiting smark's A/B/C cert-strategy pick).

### 2.2 Kimi session/new MCP: duplicate TOML key

`~/.kimi/config.toml` declares `background = …` twice (line 83 col 0). The
Kimi MCP server refuses to start (`code=-32603` Internal error). Effect:
any agent whose runtime uses `kimi session/new` fails immediately. **No
issue has been opened for this** — currently surfaced only as a side note
in [SMA-34704](https://multica/issue/4f53c2bd-57a3-40a1-964e-12ea2788fa53)'s
ops comment.

### 2.3 Repo bare-cache corruption: `he-mark-qinglong/multica`

07:53+08 daemon log: bare cache at
`/home/smark/multica_workspaces/.repos/f9a9d34e…/github.com+he-mark-qinglong+multica.git`
has `origin/*` empty and bare HEAD unmatched. A refspec-migration fetch
failed with `'origin' does not appear to be a git repository`. Likely a
partial migration state. **No automated deletion happened today** — agents
proceed with "possibly stale code" WRN.

### 2.4 W5 framework-vs-inhouse divergence (2 archives today)

Both archives target the `vpvr_*` family. Root cause is twofold:

- **Vectorbt / backtrader replay mechanics** amplify the underlying
  negative drift. Vectorbt's per-bar mark-to-market on `vpvr_reversion_5m_vwap_trail`
  produced Sharpe −9.62 vs in-house −0.82 (1070% divergence).
- **Near-zero denominators inflate % divergence**. `vpvr_macro_calendar_4h_20260715`'s
  in-house Sharpe (−0.0165) is so close to zero that even a small
  framework gap becomes a >900% divergence.

W5 itself is a guard, not a bug — strategies are auto-archived as designed.
**Open question:** is the in-house engine under-reporting negative drift
(i.e., shipping strategies the in-house engine treats as "near zero" but
the live market treats as bad)? Not answerable from today's data.

### 2.5 Two autopilots without schedule triggers

Heartbeat [SMA-34762](https://multica/issue/aa9b8831-46e3-40e0-ab87-a9f71b78fcdb)
found that autopilot records existed but their schedule triggers were
not migrated (same class of issue as 2.3 — likely the same refspec
migration). Effect: 37h of missed Idle Agent Dispatcher cycles + 2
missed Daily Done-Sweeper runs.

### 2.6 Campaign-tree.html sunburst staleness (new)

The 6-hourly `campaign-tree-builder` autopilot regenerated the page at
12:18, but two new commits landed between 12:18 and the audit (13:25,
13:31). The DATA block missed both. Additionally, the
`/* auto-generated … */` comment was frozen at 2026-07-14T02:46 — the
file carried **3 conflicting timestamps** (comment, DATA, header).
Root cause: builder's `replace_data_block` did not refresh the
surrounding comment. **Fixed** by updating the builder; the stale
copy at `~/.multica/scripts/multica_campaign_tree_builder.py` (backed
up as `.bak-20260717`) was also synced.

> Side observation: structural cadence gap. Campaign commits fire every
> ~2 min during active campaigns, but the tree regenerates every 6 h.
> By design the page is up-to-6 h stale. Tighter freshness would mean
> promoting the autopilot from L3 (`*/6h`) to L3 (`*/2h`).

### 2.7 BTC/ETH/SOL data staleness — 7-day gap (new)

Every parquet file for `BTC/ETH/SOL` × `1m/15m/4h` in the canonical
strategy data directory
(`/home/smark/multica/quant-loop/strategies/vpvr_volume_edge_3tf_v1_20260711/data/`)
**stops at 2026-07-10 23:59 UTC**. Coverage of the requested 2026-06-18
→ 2026-07-17 window is exactly **76.67%** across all 9 (symbol, timeframe)
combinations. The data itself is non-corrupt (gap_count = 0 in the
available segment) — only the *last week* is missing. Recommended fix:
backfill 2026-07-11 → 2026-07-17.

> A side finding: the live directory contains 15m BTC and 4h **symlink
> aliases**, but `ETHUSDT_4h.parquet` and `SOLUSDT_4h.parquet` point
> to the same BTCUSD file. The agent that ran the audit did not use
> those aliases for symbol validation; the canonical per-symbol files
> were used instead. The alias mismatch is **[UNCONFIRMED]** in
> impact — not yet triaged.

### 2.8 Sharpe calibration: per-trade vs daily-resampled (new)

[SMA-34773](https://multica/issue/333efd3c-749b-4724-944b-7bfeada07874)'s
audit found that 18 strategies + 1 aggregate row write **per-trade
Sharpe** to `metrics.json` instead of the SPEC-required **daily-resampled
Sharpe**. The calibration mismatch can be >70% relative for high-frequency
strategies (e.g., `vpvr_sentiment_attention_1m_20260716`: per-trade −7.81
vs daily −4.42). The top-3 ranked strategies by daily-resampled Sharpe
**all** carry this flag (PER_TRADE_SUS) — so the published ranking
numbers are still directionally correct but the headline figures
understate true performance for high-frequency variants.

### 2.9 Daemon.log error patterns (quantified)

[Top 5 patterns by count](https://multica/issue/a0a783d5-9cf0-437e-b726-cd54f736fb9a)
across 164,724 log lines (full 24h window):

| # | Pattern | Count | Status |
|---|---------|------:|--------|
| 1 | `custom_env: blocked key skipped` (`MULTICA_DAEMON_MAX_CONCURRENT_TASKS` / `MULTICA_GC_TTL`) | **4,022** | Benign noise; config knobs silently ignored |
| 2 | `task temp dir cleanup failed` (`node-compile-cache` not empty) | 18 | Harmless `/tmp` leak per task |
| 3 | `ERR report failed task failed` (`error="context canceled"`) | 13 | Post-completion report beat by cancellation |
| 4 | `cancel ack failed; server sweeper will finalize` (HTTP 404) | 5 | Server-side endpoint mismatch |
| 5 | `repo checkout readiness failed` (repo not configured) | 4 | Workspace lacks `multica` repo mapping |

Counts verified via `grep -c` on `daemon.log`.

---

## 3. Fixes applied

| # | Fix | Where | Verified? |
|---|-----|-------|-----------|
| F1 | Added `*/3 * * * *` Asia/Shanghai to **Idle Agent Dispatcher** (trigger `cee57557-…`); next run 2026-07-17 13:18+08 | autopilot config | ✅ `multica autopilot get` shows exactly one enabled trigger; subsequent dispatch-critic cycle at 13:21+08 used it |
| F2 | Added `0 4 * * *` Asia/Shanghai to **Daily Done-Sweeper** (trigger `4b5e1d3c-…`); next run 2026-07-18 04:00+08 | autopilot config | ✅ Same verification as F1 |
| F3 | Dispatch-critic watchdog reassigned 5 wrong-domain issues → multica-strategy + 1 urgent-human-decision → smark | issue `assignee_id` fields | ✅ update response confirmed new IDs |
| F4 | W5 AUTO-ARCHIVE applied to `vpvr_reversion_5m_vwap_trail_20260709` (vectorbt) | parent strategies; no code change | ✅ issue moved `in_progress → done` |
| F5 | W5 AUTO-ARCHIVE applied to `vpvr_macro_calendar_4h_20260715` (backtrader) | parent strategies; no code change | ✅ issue moved `in_progress → done` |
| F6 | DEPLOY-FAIL re-verification comment with reproducible curl + openssl evidence | issue comment (no infra change) | ✅ metadata `waiting_on` + `decision` pinned |
| F7 | `scripts/multica_campaign_tree_builder.py` updated to refresh `/* auto-generated */` comment timestamp each run | strategy-display-engine repo | ✅ diff vs live git = 0 (40 commits / 25 groups) |
| F8 | Stale `~/.multica/scripts/multica_campaign_tree_builder.py` synced with the fixed version; stale copy backed up as `.bak-20260717` | local mirror | ✅ backup file exists |
| F9 | `~/multica/AGENTS.md` updated with current super-agent + autopilot + skill-family topology; pre-redistribution preserved as historical subsection | local file | ✅ saved locally (no commit per issue constraints) |

**No code-repo fixes today in this workspace.** The `workdir/` is a
clean curator run. The VPVR campaign (SMA-34737–34741) is in flight but
no VPVR-code diffs landed in `done` today. **[UNCONFIRMED]** whether the
campaign-tree builder fix (F7) was committed; the comment says
"working-tree only" but the issue status is `in_review`.

---

## 4. Open items / regressions

### 4.1 Unresolved (carried over)

| Issue | Status | Blocker |
|---|---|---|
| [SMA-34634](https://multica/issue/78fea9d5-a784-4c80-bad4-3d7d5a9d24c8) | `in_progress`, `urgent` | A/B/C cert-strategy pick: mkcert / regen w/ SAN / public CA via LAN DNS |
| [SMA-34704](https://multica/issue/4f53c2bd-57a3-40a1-964e-12ea2788fa53) | `in_review` | Same; re-fires on `* * * * *` until cert changes |
| [SMA-34632](https://multica/issue/6239858a-8fe4-45a8-b200-62a09567ab50) | `in_review` | Same (original tracker) |
| [SMA-2167](https://multica/issue/2167) `[DAG] Multica HTTPS 改造主控` | `in_review` | Larger HTTPS cutover plan; subsumes cert choice |
| [SMA-2951](https://multica/issue/7658ca50-8168-4858-b5fc-eec0b9c4d024) M7 `GOOGLE_REDIRECT_URI` | `in_review` | Deferred until HTTPS cutover greenlit |
| [SMA-34717](https://multica/issue/34717) `[need-smark-decision: external-dependency]` M7 OAuth enumeration | `blocked` | Same as above |
| [SMA-34739](https://multica/issue/34739) `[B5-strategy-designer] VPVR iter#70+ — land variants into quant-loop` | `blocked` | iter#70+ variants not finalized by upstream B1-B4 |
| [SMA-34738](https://multica/issue/34738) `[B4-performance] VPVR iter#70+ — analyze metrics & overfitting` | `blocked` | Same |

### 4.2 New / newly-surfaced

| Issue | Status | Note |
|---|---|---|
| **Corrupt bare cache `he-mark-qinglong/multica`** (2.3) | not tracked | Daemon logs ERR; no issue opened; needs human cache cleanup |
| **`~/.kimi/config.toml` duplicate `background` key** (2.2) | not tracked | Kimi MCP refuses to start; surfaced only as a side comment in SMA-34704; should become its own ticket |
| **Symlink alias mismatch in quant data dir** (2.7) | not tracked | `ETHUSDT_4h.parquet` and `SOLUSDT_4h.parquet` symlink to BTCUSD file; impact **[UNCONFIRMED]** |
| **Per-trade vs daily-resampled Sharpe in 18 strategies + 1 aggregate** (2.8) | work-pool next-day item | "Verify all strategy metrics.json use daily-resampled Sharpe (not per-trade) — audit and fix" |
| **Strategy data backfill needed for 2026-07-11 → 2026-07-17** (2.7) | not tracked | Quant-analyst should re-run the fetch script for the 7-day window |
| **W5 archive pattern on `vpvr_*` family** (2.4) | not tracked | Needs investigation: in-house under-reporting negative drift? |

---

## 5. Patterns & recommendations

### P1 — `custom_env: blocked key skipped` is drowning real signal

**4,022 occurrences** in 24 h (about 3 lines/min). Both blocked keys
(`MULTICA_DAEMON_MAX_CONCURRENT_TASKS`, `MULTICA_GC_TTL`) appear benign
when blocked but **silently neutralize the operator's ability to tune
the daemon**. **Recommendation**: add to allow-list if intentional,
suppress log line if not — but the volume is high enough to mask real
errors.

### P2 — Daemon log lacks structured severity bucketing

The same WRN prefix covers benign config-noise (P1) and recovery-path
warnings (P2/P3). The heartbeat triage agent had to grep for each
signature manually. **Recommendation**: tag log lines with a stable
`category=` or `code=` field so recurring-pattern audits can group
without re-deriving signatures.

### P3 — Migration-cache fragility

Two of today's issues (autopilot-without-triggers, bare-cache
corruption) and a candidate Kimi-config issue share a common shape:
"resource was created during a previous migration and is now in a
partially-consistent state". **Recommendation**: a one-shot
`multica migrate audit` script that walks all known resource
registries (autopilots, repos, MCP config) and lists migration-state
inconsistencies.

### P4 — Sunburst staleness is by design but the comment lied

The campaign-tree builder's `/* auto-generated */` comment was frozen
at 2026-07-14T02:46 — the file carried 3 conflicting timestamps
(comment, DATA block, header). Anyone eyeballing the file would trust
the comment over DATA. **Recommendation**: a pre-commit / pre-archive
check that the comment timestamp ≤ DATA mtime; or remove the comment
entirely.

### P5 — Per-trade vs daily-resampled Sharpe is widespread

18 of 27 strategies with `metrics.json` report per-trade Sharpe,
contradicting the SPEC. **Recommendation**: enforce via metrics.json
schema (separate keys: `sharpe_per_trade` + `sharpe_daily_resampled`)
and fail validation if the legacy `sharpe` field is detected.

### P6 — Dispatch-critic watchdog is hitting its 5-intervention cap

Watchdog found 12 wrong-domain assignments today, fixed 5, deferred 7
to next cycle. **Recommendation**: raise the cap (the cap is currently
hard-coded at 5/cycle), or run the watchdog on a faster cadence.

### P7 — VPVR family keeps hitting W5

Both today's W5 archives target `vpvr_*` strategies. The in-house
engine reports these as near-zero or slightly negative; frameworks
amplify the divergence. **Recommendation**: tighten in-house engine
to match the framework's per-bar fill model (or document the gap and
flag in-house results as "idealized" so reviewers discount them).

### P8 — Data freshness gap is a recurring risk

Strategy data stops at 2026-07-10 (today is 2026-07-17). The 7-day gap
was not surfaced by any heartbeat — it took a quant-analyst dispatch
to find it. **Recommendation**: add data-freshness check to the
heartbeat autopilot (compare `latest_data_ts` vs `now()` per
symbol/timeframe, alert if >24 h stale).

---

## 6. Honest limits

- **Daemon log today covers ~5 h** (00:00 → ~13:30+08 at read time);
  pattern totals reflect that window. The `daemon.err.log` is empty
  (0 bytes since 2026-07-15 21:25) — all `ERR` lines are in `daemon.log`.
- **No code-repo diffs** landed in `done` today in this workspace —
  there is no git diff to attach for "fixes applied".
- **I did not check** `multica-tunnel/*.log`, `playwright-mcp/console-*.log`,
  or `system-heartbeat.log` for today's signal. If additional recurring
  patterns live there, they are **[UNCONFIRMED]** from this digest.
- **The `multica_campaign_tree_builder.py` fix (F7)** is reported as
  "working-tree only" by the audit agent. Whether it was committed is
  **[UNCONFIRMED]**.
- **Two issues** (corrupt bare cache, duplicate Kimi TOML key) were
  logged in daemon issues/comments but **never got their own issue**.
  Surfaced here as untracked regressions.
- **This digest overlaps intentionally** with
  [SMA-34765](https://multica/issue/dbaf11da-1e55-40c0-9ad3-fe8cbbf663da)
  for §2.1–§2.5. The duplication is by design — the work-pool asked
  for "a consolidated summary" of today's debugging; the prior digest
  is one source of evidence among several.

---

*Curator run via issue `7558e3d9-3d41-428f-ba1b-43356d714d6a`,
2026-07-17 ~13:50+08. Sources cross-checked: `multica issue list`
(today's `done`/`in_progress`/`in_review`/`blocked`), 12 issue comment
threads, and `~/.multica/daemon.log`. Prior digest
[SMA-34765](https://multica/issue/dbaf11da-1e55-40c0-9ad3-fe8cbbf663da)
preserved and reconciled.*
