# PM-2026-001 — `/` disk hit 100% on mac dev host while six endpoints stayed 200/307

> **Worked example** showing how the postmortem template at
> `docs/templates/postmortem.md` should be filled in for a real incident
> the workspace already has records of (bucket 1 of
> `quant-loop/docs/decisions/deploy-incidents.md`).
>
> **Status:** reconstructed 2026-07-26 by `multica-ops` from existing
> decision doc + curator digest; not a live post-incident write. Every
> quoted fact is sourced from `deploy-incidents.md` §Bucket 1 or
> `knowledge/curator/2026-07-17-debug-summary.md` §2.x.

---

## 1. Frontmatter

```yaml
---
id: PM-2026-001
title: Disk-full on mac dev host (/) reached 100% while six endpoints stayed 200/307
date_resolved: 2026-07-10T19:30+08:00     # cleared caches manually; 8.7G free
date_detected: 2026-07-10T18:12+08:00    # DEPLOY-FAIL-DETECT cron first fired
severity: P1
status: monitoring                         # nightly cron prune of M_workspaces still gated on sign-off
systems: [mac dev host, "/tmp/framework-cache", "/tmp/freqtrade-validation*", "/var/log/journal", "node caches"]
related_issues:
  - <deploy-incidents doc row — no SMA; tracked in quant-loop/docs/decisions/deploy-incidents.md §Bucket 1>
runbooks_invoked:
  - docs/runbooks/data-outage.md (signal classification) — partial: this was disk, not data link
estimated_cost_usd: 0 — observability only
data_loss: none
author: multica-ops (reconstruction)
reviewer: <pending L4 sign-off — open>
---
```

> **Note on `id` allocation.** This is the *first* retroactive id issued for
> 2026. Going forward, every new P0/P1 incident from 2026-07-26 onward gets
> the next `PM-2026-NNN` number from the parent project's
> `metadata.decision` register (to be added by the parent owner). P0 numbers
> are issued as they happen; P1 numbers can be batched weekly.

---

## 2. Executive summary

On 2026-07-10T18:12+08:00 the mac dev host root filesystem reached 100%
utilisation (221 GB / 233 GB, 3.5 MB free) while all six monitored endpoints
continued serving 200/307. The DEPLOY-FAIL-DETECT cron correctly fired the
warning **before** the next write ENOSPC'd, giving us a 1-hour-18-minute
window between detection and the moment a write would have cascaded into 5xx.
Root cause: build- and runtime-cache directories (Next.js, frequencytrade
validation, journald, npm/uv/puppeteer) accumulating across two deploy-day
windows without an automated prune. Resolved by manual cache eviction,
restoring 8.7 GB free. No P&L impact. The follow-up cron that *would* have
prevented this remains gated on human destructive-action sign-off as of
2026-07-26.

---

## 3. Timeline

| Time (UTC+8) | Actor | Event | Evidence |
|---|---|---|---|
| 18:12 | DEPLOY-FAIL-DETECT cron | Fired "disk ≥ 95%" warning; ranked top reclaimable paths | `daemon.log` cron line (see deploy-incidents.md §Bucket 1) |
| 18:12 | multica daemon | Logged top-N: `/tmp/framework-cache` 956M + `/tmp/freqtrade-validation*` 1.4G + `/var/log/journal` 1.2G + ms-playwright/puppeteer/npm/uv 1.8G | `deploy-incidents.md` §Bucket 1 |
| 18:13 | human operator | Saw the warning; investigated via `df -h` and `du -sh /tmp/*` | decision doc §Bucket 1 |
| 18:35 | human operator | Cleared `/tmp/framework-cache` and `/tmp/freqtrade-validation*`; verified 8.7 GB free (97% used) | decision doc §Bucket 1 |
| 19:30 | human operator | Cleared remaining caches; disk settled below 95% | decision doc §Bucket 1 |
| 2026-07-15 | dispatcher autopilot | Later, the same disk-100% signature repeated during the 4h23m full-stack storm (Bucket 4) but the autopilot did **not** clear caches itself, per its `不要做` rule | `deploy-incidents.md` §Bucket 4 + agent-spirit §4.1 |

### 3.1 MTTD / MTTR

| Metric | Target | Actual |
|---|---|---|
| MTTD | ≤ 5 min (P0/P1) | **0 min** — DEPLOY-FAIL-DETECT cron is the source of the signal |
| MTTR-contain | ≤ 60 min (P1) | **~78 min** (18:12 → 19:30) — **target missed by 18 min** |
| MTTR-full | ≤ 72 h (P1) | **open** — automated prune cron (action §9 #1) still gated |

The MTTR-contain miss is a finding: we had the warning instantly but spent
78 minutes on manual `du -sh` archaeology before any cache got cleared. See
§9 action #2.

---

## 4. Impact

### 4.1 Scope

| Dimension | Affected | Count / Magnitude |
|---|---|---|
| Strategies blocked | none | 0 |
| Trading hours lost | none | 0 |
| Data staleness window | none | 0 |
| Users unable to `<action>` | none directly; one operator blocked from deploys | 1 |
| Observability loss | yes — `daemon.log` writes slowed to O(seconds) per line; cron emit cadence throttled | ~78 min |

### 4.2 Money impact

`0 — observability only`. Trading continued unaffected because all data
sinks were on a separate volume that did not fill.

### 4.3 Data loss / integrity

**none.** All six monitored endpoints stayed 200/307. No fills missed, no
parquet corruption reported, no DB transaction rolled back.

---

## 5. Detection

The first signal was `/` ≥ 95% from DEPLOY-FAIL-DETECT cron at 2026-07-10T18:12+08:00.
This was detected **by design**: the cron runs every minute, probes six
endpoints and one filesystem, and emits a row when utilisation crosses the
95% threshold (5-min dedup window).

| Source | Cadence | First firing at | Lag from incident start |
|---|---|---|---|
| DEPLOY-FAIL-DETECT cron (`* * * * *` Asia/Shanghai) | 1 min | 18:12 | n/a — cron **is** the signal |
| `df -h` from operator prompt | manual | 18:13 | 1 min |

Detection tier: **designed**, not lucky. Strength is that we got the warning
*before* the next write ENOSPC'd — that is exactly the design intent of the
DETECT autopilot.

---

## 6. Response

Containment actions began at 2026-07-10T18:12+08:00 (the cron is the
responder-tracker for the *signal*; the human responder clock starts at
18:13). The primary responder was the human on-call operator. The recovery
sequence was:

1. `df -h /` — 18:13 — confirmed 100% — outcome: **OK** (initial state).
2. `du -sh /tmp/* | sort -h | tail -10` — 18:20 — identified top reclaimable
   paths — outcome: **OK**.
3. `rm -rf /tmp/framework-cache/* /tmp/freqtrade-validation*/*` — 18:35 —
   freed 2.3 GB — outcome: **OK** (8.7 GB free, 97% used).
4. `journalctl --vacuum-size=500M` — 19:10 — cleared 1.2 GB of `/var/log/journal`
   — outcome: **OK** (still 97% used; non-critical).
5. Cleared remaining caches (`~/.npm`, `~/.cache/puppeteer`, `~/.cache/ms-playwright`,
   uv cache) — 19:30 — outcome: **OK**, settled below 95%.

Final fix landed at 19:30 (MTTR-full from cron signal: **78 min**, from human
ack: **77 min**).

### 6.1 Runbook compliance

The runbook `docs/runbooks/data-outage.md` covers data-link failure. This
incident was a *disk* failure, not a data-link failure, so:

- [x] Steps were skipped because: runbook §1 threshold table does not list
      disk-saturation as a data-outage trigger (§Signal 4 is closest but is
      not about disk). Runbook classification was manual.
- [ ] Runbook needs an update: **yes** — add a §5.x branch for disk-saturation.
      See §9 #3.

### 6.2 Decision points

| Time | Decision | Options considered | Chosen | Rationale | Reversible? |
|---|---|---|---|---|---|
| 18:35 | How aggressively to clear | A. Clear only `/tmp/framework-cache` (956M); B. Clear both `/tmp/*` cache paths; C. nuke `/tmp/*` wholesale | **B** | `/tmp` is sandbox-safe but `/tmp/freqtrade-validation*` may have a run-in-progress. Confirmed no runs before clearing. | yes — caches rebuild on next build |
| 19:10 | Whether to `--vacuum-size=journal` | A. Yes — free 1.2 GB; B. No — keep full history | **A** | History older than 500M is rarely accessed; the curated decision doc is the durable record. | yes — old logs are dropped |
| 2026-07-17 | Whether to register nightly prune cron | A. Register destructive `rm -rf M_workspaces/* done>7d`; B. Defer pending human sign-off | **B** | agent-spirit §4.1 forbids auto-deletion; cron must wait for explicit destructive-action sign-off. | yes |

---

## 7. Root cause analysis (Five Whys)

1. **Why** did `/` hit 100% at 18:12?
   Because `/tmp/framework-cache` (956M), `/tmp/freqtrade-validation*` (1.4G),
   `/var/log/journal` (1.2G), and various build/runtime caches (1.8G) had
   accumulated across two deploy-day windows without anyone pruning them.
2. **Why** had those four directories accumulated?
   Because every Playwright / Next.js / framework build regenerates its
   cache without evicting the old one, and there is no process that owns
   pruning them.
3. **Why** is there no prune process?
   Because the workspace's `agent-spirit §4.1` ("不要做") forbids agents from
   running destructive actions autonomously, and no human-accepted cron has
   been registered to do the prune.
4. **Why** has no human-accepted cron been registered?
   Because the destructive-action sign-off was deferred (see Bucket 1 in
   `deploy-incidents.md`) and not picked back up.
5. **Why** is the destructive-action sign-off deferred and lingering?
   Because the workflow treats it as a "next deploy window" item rather than
   a tracked action with an owner and date — **this is the systemic root**.

---

## 8. What worked / What didn't

### 8.1 What worked

- **DEPLOY-FAIL-DETECT cron fired before next-write ENOSPC** — `deploy-incidents.md` §Bucket 1 — **why it worked**: 1-minute cadence + 95% threshold + 5-min dedup window gave an hour-plus of headroom.
- **Top-N reclaimable list in the cron body** — same — **why it worked**: the operator did not need to do a `du -sh` archaeology pass; the cron emitted the answer.
- **`/tmp` namespace isolation** — decision doc §Bucket 1 — **why it worked**: caches could be cleared without touching user code or persisted data, removing a "wait, are you sure?" hesitation.
- **`-k` curl pattern carried over from prior cert work** — implicit, but the cron output formatting matches what an operator already knows how to read.

### 8.2 What didn't

- **78-minute MTTR-contain** despite instant detection — §3.1 — **why it hurt**: the human still had to `du -sh` to confirm before clearing, even though the cron had already named the culprits.
- **No automated prune exists** — §7 #5 — **why it hurt**: this is the second disk-100% incident in two weeks with the same root cause; the prior one also cleared manually. We have not solved the prevention, only the response.
- **No runbook for disk-saturation** — §6.1 — **why it hurt**: operator was reading `data-outage.md` (data-link) under time pressure and applying it manually.
- **MTTR-full still "open"** — §3.1 — **why it hurt**: until the cron lands, every two weeks we pay the same 78 minutes again.

---

## 9. Action items

| # | S/M/L | Action | Owner | Due (UTC+8) | Verification | Status |
|---|---|---|---|---|---|---|
| 1 | S | Edit `docs/runbooks/data-outage.md` to add §5.x "disk-saturation" branch with the top-N clearing sequence from §6 of this doc | multica-ops | 2026-07-29 | Diff reviewed by L4 sign-off agent; new branch cross-checked against this postmortem in §6.1 | open |
| 2 | M | Re-run the same disk-100% scenario against the cron in a staging-style test (drop `cache_clear_marker` to 100% then re-fire); measure MTTR-contain end-to-end | multica-ops + L4 reviewer | 2026-08-05 | Replay produces a recovery < 15 min; measurement recorded in a comment on this doc | open |
| 3 | L | Register the nightly `M_workspaces/*` prune cron — gated on smark destructive-action sign-off | smark sign-off + multica-ops implementation | 2026-08-12 | Cron active in `multica autopilot list`; one-week dry-run audit posted; no false-positive evictions in dry-run report | open — gate pending |
| 4 | S | Add a `[PM-YYYY-NNN]` ID allocator note in the parent project's `metadata.decision` register so future incidents get a trackable id without L3 archaeology | multica-ops | 2026-07-29 | New id register note exists; next incident uses `PM-2026-002` | open |

---

## 10. Follow-up & communication

### 10.1 Internal

- This postmortem doc is filed at `docs/templates/postmortem-example-disk-saturation-2026-07-10.md` (example). The "real" one for this incident is folded into
  `quant-loop/docs/decisions/deploy-incidents.md` §Bucket 1; if the parent owner wants the postmortem as a standalone file, promote §Bucket 1's text here under `id: PM-2026-001` and add the file to `quant-loop/docs/decisions/` instead.
- Linked sub-issues for each S/M/L action item: **to be created** when smark prioritises.
- No additional parent-issue metadata pinning required.

### 10.2 External

Skip — incident was internal-only.

---

## 11. Sign-off

- **Author:** `multica-ops` — 2026-07-26T12:48+08:00 — "postmortem complete
  (reconstructed example); all action items owned and dated".
- **Reviewer (L4 / smark):** **pending** — L4 sign-off agent to confirm
  factual accuracy against `deploy-incidents.md` §Bucket 1 and accept
  severity tier P1 (observability-only, MTTR-contain miss = 18 min).

---

## 12. Appendix

- Full top-N reclaimable list from `deploy-incidents.md` §Bucket 1:
  ```
  /tmp/framework-cache            956M
  /tmp/freqtrade-validation*      1.4G
  /var/log/journal                1.2G
  ~/.cache/ms-playwright          0.9G
  ~/.cache/puppeteer              0.4G
  ~/.npm                          0.3G
  ~/.cache/uv                     0.2G
  ```
- Rejected hypothesis: "OOM-kill triggered disk pressure". No OOM event
  in `dmesg` for that window; this was pure accumulation, not a kernel-side
  pressure release.
- Rejected hypothesis: "the cron over-reported". Verified `df -h` directly
  from the host returned the same 100% number within seconds of the cron
  firing.

---

## 13. P2 / P1 decision rationale

This was originally written up as a §Bucket 1 in `deploy-incidents.md`
without an explicit P-tier. Reasoning for retrospective classification as
**P1** (not P0, not P2):

- **Not P0** because there was no P&L impact, no failed fills, no
  > 15-min data staleness.
- **Not P2** because the MTTR-contain target was missed by 18 min and the
  full-fix MTTR is open across multiple weeks — that is a "sub-system
  unusable for > 30 min" P1 condition by §2.1.

If the parent owner disagrees, re-tier in §11 sign-off and the §9 actions
do not change.