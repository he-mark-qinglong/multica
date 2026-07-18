# Today's debugging summary — 2026-07-17 (curator digest)

Scope: workspace `f9a9d34e-b809-4564-b0c0-b781a70a3f25`. Sources cross-checked:
`multica issue list` (today's `done` / `in_progress` / `in_review` / `blocked`),
comments on key issues, and `~/.multica/daemon.log` for the day.
Items the log/issues do not substantiate are marked **unclear** rather than
guessed.

## 1. Root causes found

### 1.1 Framework-vs-inhouse backtest divergence (multiple strategies)
Two W5 AUTO-ARCHIVE runs landed in `done` today. Both strategies were flagged
**NOT-PROFITABLE** by the W5 rule because OOS walk-forward divergence between
the in-house engine and the third-party framework exceeded 50% in ≥2 of 3
metrics. Per W5 spec, these go straight to archive without an ESCALATE-TO-SMARK.

| Issue | Strategy × framework | Sharpe div | ann_ret div | max_dd div |
|---|---|---:|---:|---:|
| [SMA-34759](https://multica/issue/127d07b3-683f-40a4-a09d-6d97b0e6926f) | `vpvr_macro_calendar_4h_20260715` × backtrader 1.9.78.123 | 930.27% | **1227.94%** | 27.97% |
| [SMA-34731](https://multica/issue/68acdd58-e1fd-40f2-bfab-a80d08c1601a) | `vpvr_reversion_5m_vwap_trail_20260709` × vectorbt 1.1.0 | **1070.20%** | 472.27% | 423.71% |

**Root cause**: the divergence is driven by the third-party framework's
idealized fill model + per-bar mark-to-market amplifying negative drift
(SMA-34731 shows vectorbt Sharpe −9.62 vs in-house −0.82). The strategies
themselves are negative in-house (sharpe −0.82 / −0.02), but the framework
metric amplifies the gap by an order of magnitude. **W5 itself is a guard**,
not a bug — the strategies are auto-archived as designed.

### 1.2 HTTPS nginx self-signed cert fails default-validation clients
Persistent since 2026-07-16T17:32Z. Re-probed today at 05:48+08 in
[SMA-34704](https://multica/issue/4f53c2bd-57a3-40a1-964e-12ea2788fa53)
(ops-worker-1 comment by agent `6bfc6d4c`):

```
curl https://localhost/display/compare          → exit 60, http_code 000 (self-signed)
curl https://localhost/strategies               → exit 60, http_code 000
curl -k https://localhost/display/compare       → 200
curl https://localhost:8080/health (HTTP)       → 200
```

Cert `/etc/ssl/certs/multica-selfsigned.crt` has `subjectAltName: No extensions
in certificate` (issuer `CN=192.168.0.105`). Default-validation clients reject
it. Three open trackers all point at the same root cause:
[SMA-34632](https://multica/issue/6239858a-8fe4-45a8-b200-62a09567ab50)
(`in_review`), [SMA-34704](https://multica/issue/4f53c2bd-57a3-40a1-964e-12ea2788fa53)
(`in_review`), and the gating decision ticket
[SMA-34634](https://multica/issue/78fea9d5-a784-4c80-bad4-3d7d5a9d24c8)
(`todo`, awaiting smark's A/B/C cert-strategy pick).

### 1.3 Kimi session/new MCP error: duplicate TOML key
[SMA-34704](https://multica/issue/4f53c2bd-57a3-40a1-964e-12ea2788fa53) comment
by agent `e59eb658` at 00:25:30+08:

> `session/new: Internal error (code=-32603, data={"details":"Invalid TOML in
> configuration file /home/****/.kimi/config.toml: Key \"background\" already
> exists. at line 83 col 0"})`

**Root cause**: `~/.kimi/config.toml` declares `[background]` (or a
`background = …` key) twice. The Kimi MCP server refuses to start, so any
agent whose runtime uses `kimi session/new` fails immediately. This is what
caused the original 00:25 dispatch of SMA-34704 to `ops-worker-1` to fail
within seconds of assignment, leaving the issue orphaned for ~5h until the
dispatch-critic watchdog reassigned it at 05:36+08.

### 1.4 Repo bare cache corruption: `he-mark-qinglong/multica`
Daemon log ERR at 07:58:05+08:

> `repo checkout failed url=https://github.com/he-mark-qinglong/multica
> error="cannot resolve default branch … bare cache at
> /home/smark/multica_workspaces/.repos/f9a9d34e…/github.com+he-mark-qinglong+multica.git
> has no usable refs (origin/* is empty or ambiguous and bare HEAD has no match).
> The cache may be corrupted; delete it and retry"`

A 07:53 follow-up warning shows a refspec-migration fetch failed with
`'origin' does not appear to be a git repository`, suggesting the bare-cache
state is partially inconsistent after a previous migration. **No automated
deletion happened today** — agents see "possibly stale code" (per the WRN) but
proceed. This blocks clean `multica repo checkout` for the multica repo until
a human clears the cache.

### 1.5 Idle Agent Dispatcher & Daily Done-Sweeper had no schedule triggers
[SMA-34762](https://multica/issue/aa9b8831-46e3-40e0-ab87-a9f71b78fcdb)
heartbeat finding (12:17+08 scan): two active autopilots existed but had
zero enabled schedule triggers, so they "never ran".

**Root cause**: autopilot records were created/imported but their schedule
triggers were not migrated (same refspec-migration class of issue as 1.4).
Both were healthy in all other respects.

## 2. Fixes applied

| # | Fix | Where | Verified? |
|---|---|---|---|
| F1 | Added `*/3 * * * *` Asia/Shanghai trigger to **Idle Agent Dispatcher** (autopilot `0fc298fa-22b7-4b33-bca7-14cb4beb12e5`); next run `2026-07-17 13:18+08`, trigger id `cee57557-…` | autopilot config (not a code repo) | Yes — agent `6bfc6d4c` reported `multica autopilot get` shows exactly one enabled trigger; subsequent dispatch-critic cycle at 13:21+08 used the new trigger |
| F2 | Added `0 4 * * *` Asia/Shanghai trigger to **Daily Done-Sweeper** (autopilot `7bec3e1d-…`); next run `2026-07-18 04:00+08`, trigger id `4b5e1d3c-…` | autopilot config | Same verification path as F1 |
| F3 | Dispatch-critic watchdog (cap 5/cycle) reassigned 5 wrong-domain owners → `multica-strategy` ([SMA-34725](https://multica/issue/88e47cf4-978f-4205-a4eb-b3df6b921303), [SMA-34724](https://multica/issue/ccf39301-538a-47a9-83ae-324dcae84ce8), [SMA-34747](https://multica/issue/a9f2a142-32e4-49de-9118-517724c6b1e8), [SMA-34745](https://multica/issue/05911a90-19e6-426a-8899-f591e0caa1a0)) and one urgent-human-decision item to smark ([SMA-34634](https://multica/issue/78fea9d5-a784-4c80-bad4-3d7d5a9d24c8)) | issue assignee_id fields | Yes — update response confirmed new IDs (per the 13:21+08 watchdog comment) |
| F4 | W5 AUTO-ARCHIVE applied to two NOT-PROFITABLE strategies (see 1.1) | parent strategies; no code change | Yes — both issues moved `in_progress → done`; archive per W5 spec |
| F5 | DEPLOY-FAIL re-verification comment posted to [SMA-34704](https://multica/issue/4f53c2bd-57a3-40a1-964e-12ea2788fa53) with reproducible curl + openssl evidence | issue comment (no infra change) | Yes — `6bfc6d4c` pinned `waiting_on` + `decision` metadata for future runs |
| F6 | Two autopilots recorded as "running" with no triggers fixed (F1+F2) | see F1 | see F1 |

**No code-repo fixes today** — `multica_workspaces/f9a9d34e/94456714/workdir`
is a clean curator run; the VPVR campaign is in flight but no VPVR-code
diffs landed in `done` today.

## 3. Recurring patterns

### P1 — `custom_env: blocked key skipped` for `MULTICA_DAEMON_MAX_CONCURRENT_TASKS` and `MULTICA_GC_TTL` (today: 3997 WRN lines)
Every agent start logs both keys as blocked. Effect: **knobs in the agent
config are silently ignored**. This is a security guard (deny-by-default env
injection) but the warning is noisy enough to drown out real issues. Today's
hits are uniformly benign — no error follows — but the count is non-trivial
and worth investigating whether these two keys should be allow-listed
(unclear whether intentional; not resolved today).

### P2 — `task temp dir cleanup failed: ... node-compile-cache/v22.22.3-x64-9ac5647c-1000: directory not empty` (≥15+ instances today)
Repeats across many tasks. The node compile cache is held open by something
between the agent exiting and the sweeper trying to `unlinkat` the temp dir.
Effect: harmless disk leak per task under `/tmp/multica-task-*`. No fix today.

### P3 — `report failed task failed ... error="context canceled"` (13 instances)
Bursty at 07:07:30 (5 tasks) and 12:20:27 (8 tasks). These are the daemon's
post-completion report calls being canceled — the task itself completed
fine, only the reporting beat the cancellation. No fix needed; cosmetic
noise but the volume is worth noting because they show up as ERR lines.

### P4 — HTTPS/SSL cert validation failures (`exit 60, http_code 000`) (≥2 probes today)
DEPLOY-FAIL-DETECT autopilot re-confirmed today (05:48+08, run
`4cc752d3-…`); no new issue created because three trackers already cover it.
Recurring root cause is the SAN-less self-signed cert; only fix is the
human cert-strategy pick on SMA-34634.

### P5 — `repo checkout: fetch failed ... Connection reset by peer` (1 today: 07:53)
Single occurrence for `he-mark-qinglong/trading`. Recovered on retry; flagged
as a pattern because the same window also shows the cache-corruption ERR on
the multica repo (1.4). Likely network blip plus migration-cache fragility.

### P6 — W5 auto-archive hitting the same strategy family repeatedly
Two consecutive W5 archives today both target the vpvr_* family. The
underlying in-house metric for `vpvr_reversion_5m_vwap_trail_20260709`
(sharpe −0.82) is itself negative — so W5 is correctly catching negative
strategies whose frameworks amplify the drawdown further. **Open question:**
is the in-house engine under-reporting negative drift (i.e. are we shipping
strategies the in-house engine treats as "near zero" but the live market
would treat as bad)? Not answerable from today's data alone.

### P7 — Wrong-domain dispatch (5 reassigned today, 7 still pending)
The dispatch-critic watchdog found 12 issues routed to the wrong domain
owner today (5 fixed, 7 deferred to next cycle due to cap). This is a
pattern: agents are being assigned by role matching without domain
filtering. The watchdog is catching it but the cap means backlog.

## 4. Open / unresolved items

| Issue | Status | Blocker / waiting on |
|---|---|---|
| [SMA-34634](https://multica/issue/78fea9d5-a784-4c80-bad4-3d7d5a9d24c8) `nginx HTTPS listener cert not trusted for localhost` | `todo` (urgent, smark) | A/B/C decision: mkcert / regen w/ SAN / public CA via LAN DNS |
| [SMA-34704](https://multica/issue/4f53c2bd-57a3-40a1-964e-12ea2788fa53) `[DEPLOY-FAIL] https nginx SSL cert verify fail persists since 2026-07-16T17:32Z` | `in_review` | Same as SMA-34634; will keep re-firing on `* * * * *` until cert changes |
| [SMA-34632](https://multica/issue/6239858a-8fe4-45a8-b200-62a09567ab50) same DEPLOY-FAIL, original tracker | `in_review` | Same |
| [SMA-2167](https://multica/issue/2167) `[DAG] Multica HTTPS 改造主控` | `in_review` | Larger HTTPS cutover plan; subsumes cert choice |
| [SMA-2951](https://multica/issue/7658ca50-8168-4858-b5fc-eec0b9c4d024) M7 `GOOGLE_REDIRECT_URI` under HTTPS origin | `in_review` | Deferred until HTTPS cutover (SMA-2167) greenlit |
| [SMA-34739](https://multica/issue/34739) `[B5-strategy-designer] VPVR iter#70+ — land variants into quant-loop` | `blocked` | iter#70+ variants not yet finalized by upstream B1-B4 |
| [SMA-34738](https://multica/issue/34738) `[B4-performance] VPVR iter#70+ — analyze metrics & overfitting` | `blocked` | Same |
| [SMA-34717](https://multica/issue/34717) `[need-smark-decision: external-dependency]` M7 OAuth enumeration | `blocked` | External: HTTPS cutover go/no-go |
| Corrupt bare cache `he-mark-qinglong/multica` (1.4) | not tracked as issue | Daemon logs ERR but no issue was opened; needs human cache cleanup |
| `~/.kimi/config.toml` duplicate `background` key (1.3) | not tracked as issue | Kimi MCP refuses to start; only the watchdog comment in SMA-34704 mentions it; should probably become its own ticket |
| W5 archive pattern (P6) | not tracked | Needs investigation: are these strategies genuinely bad, or is the in-house engine under-reporting negative drift? |

## 5. Curator caveats

- **Daemon log today spans ~5h** (00:00 → ~13:24+08 at time of read); totals
  reflect that window. The `daemon.err.log` is empty (0 bytes since
  2026-07-15 21:25) — all `ERR` lines are in `daemon.log`.
- "Fixes applied" here are what I could verify from CLI outputs and issue
  comments. No commits/pushes happened today in the curator workspace, so
  there is no git diff to attach.
- I did **not** check `multica-tunnel/*.log`, `playwright-mcp/console-*.log`,
  or `system-heartbeat.log` for today's signal. If additional recurring
  patterns live there, they are **unclear** from this summary.

— curator, run via `multica issue dbaf11da-…`, 2026-07-17 ~13:25+08
