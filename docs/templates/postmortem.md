# Postmortem Template (multica workspace)

> **Purpose.** Standardize how the multica workspace writes incident retrospectives
> so that every outage — whether it stalls trading, breaks observability, or
> silently corrupts data — leaves behind a fixed set of evidence that a future
> agent or human can read cold and learn from in under 10 minutes.
>
> **Scope.** Any P0/P1 incident in `Multica Trading Platform`, the agent runtime,
> the data pipeline, the strategy harness, or any layer that can affect
> **money-in / money-out** on the ROOT GOAL. P2 incidents may use a trimmed
> variant (§8). Healthy-state retrospectives (e.g. W5 archives, family seals)
> should follow the existing `quant-loop/docs/decisions/*` convention instead.
>
> **Author & timing.** Filled within **72 hours** of incident resolution by the
> on-call agent (L1 ops) or the agent who owned the recovery. The author does
> **not** need to be the author of the bug. A reviewer (L4 sign-off or
> decision-maker agent) must sign the §11 sign-off block before the doc is
> merged to main.
>
> **Blame-free rule.** Section headers ask *what* and *why*, never *who*. Personal
> names appear only in the timeline when the entry is `"human operator
> restarted nginx"` style, not as the subject of an "X broke Y" sentence.
>
> **Companion docs.**
> - Runbooks (real-time signal → action): `docs/runbooks/*.md`
> - Family / strategy retrospectives: `quant-loop/docs/decisions/*.md`
> - Operational knowledge: `knowledge/curator/<date>-*.md`

---

## 1. Frontmatter

Copy this block to the top of the new file and replace every `<…>` placeholder.
Do not delete a line — if a field is unknown, write `unknown — investigated,
no signal` rather than leaving it blank.

```yaml
---
id: PM-YYYY-NNN                  # see §1.1 for issuance rule
title: <one-line summary, "verb + object + scope">
date_resolved: <YYYY-MM-DDTHH:MM+08:00>
date_detected: <YYYY-MM-DDTHH:MM+08:00>     # first signal that something was wrong
severity: P0 | P1 | P2            # see §2.1 for criteria
status: resolved | monitoring    # "monitoring" allowed only with §10.2 follow-up open
systems: [<list of components touched, e.g. "data ingest", "multica daemon", "btcusdt 1m replay">]
related_issues:
  - <multica issue id, e.g. SMA-36472>
  - <multica issue id>
runbooks_invoked:
  - <path, e.g. docs/runbooks/data-outage.md>
estimated_cost_usd: <number or "0 — observability only">
data_loss: <none | partial — see §5 | full — see §5>
author: <agent name or human handle>
reviewer: <L4 sign-off agent or smark — required before merge>
---
```

### 1.1 Issue id rule

`PM-YYYY-NNN` where `YYYY` is the calendar year of resolution and `NNN` is a
zero-padded sequence number per year. Cross-reference the id in:
- the closing issue comment (so future agents can grep `PM-`)
- any new issues spawned from §10 action items
- the parent project's `metadata.decision` field if the incident prompts a
  policy change

---

## 2. Executive summary

Two to four sentences. A reader who reads only this section must understand
(a) what broke, (b) how bad it was, (c) how long it lasted, (d) the one-line
root cause. No jargon, no internal-only acronyms. If a fund manager asks
"what happened last Tuesday", this is the paragraph they get.

> **Template:**
>
> On `<date_detected>` the `<system>` experienced `<failure mode>`, lasting
> `<duration>` and impacting `<scope: trades blocked / data stale / observability
> lost / X users unable to Y>`. Estimated cost: `<estimated_cost_usd>`.
> Root cause: `<one sentence>`. Fully resolved by `<date_resolved>` via
> `<one-sentence fix>`.

### 2.1 Severity criteria

| Tier | Trigger |
|---|---|
| **P0** | Money lost / money blocked — open positions unmanaged, fills missed, **or** > 15 min of trading-data staleness on a live strategy. Notify smark immediately. |
| **P1** | No direct P&L impact but a sub-system is unusable for > 30 min (e.g. display backend 500-storm, autopilot scheduler paused, dispatch cap 5/5 with backlog > 5). |
| **P2** | Degraded experience or one-off failure recovered in < 30 min. Use the trimmed variant in §8. |

---

## 3. Timeline

Chronological, **UTC+8**, every row grounded in evidence. Group by minute
when > 5 events land in the same minute (use the minute bucket column).
Every row links to an artifact (issue id, log line, commit, command output,
screenshot).

| Time (UTC+8) | Actor | Event | Evidence |
|---|---|---|---|
| `<HH:MM>` | `<agent / human / cron>` | `<one-line event>` | `<issue id / log line / commit / artifact>` |
| `<HH:MM>` | | | |

**Rules:**
- Time format is `HH:MM` when within the same day, `MM-DD HH:MM` for cross-day.
- Actor is the role, not the persona: `multica-ops`, `caocao tunnel`,
  `strategy-worker-1`, `human operator`, `data-ingest cron`, **not**
  `multica-ops agent 6bfc6d4c` or `smark`.
- "Event" must fit in one line. Multi-line explanations belong in §4–§7.
- "Evidence" must be a concrete, retrievable artifact. No "see Slack".
  Acceptable: `[SMA-36472](https://multica/issue/<uuid>)`, `daemon.log:1234`,
  `commit abc1234`, `screenshot.png`.

### 3.1 MTTD / MTTR

Compute these from the timeline and call them out explicitly. Targets are
workspace policy (ROOT GOAL §infra 红线):

| Metric | Definition | Target | Actual |
|---|---|---|---|
| **MTTD** | first signal → first human/agent acknowledgement | ≤ 5 min (P0/P1) | `<actual>` |
| **MTTR-contain** | first ack → service restored to user-acceptable state | ≤ 15 min (P0), ≤ 60 min (P1) | `<actual>` |
| **MTTR-full** | first ack → root cause fixed (not just symptom) | ≤ 24 h (P0), ≤ 72 h (P1) | `<actual>` |

If any target was missed, the action items in §10 must include a detection or
response hardening item. There is no "we got unlucky" exception.

---

## 4. Impact

Quantify, do not narrate. Every claim links to evidence.

### 4.1 Scope

| Dimension | Affected | Count / Magnitude |
|---|---|---|
| Strategies blocked | `<list>` | `<n>` |
| Trading hours lost | `<from — to UTC+8>` | `<n> min` |
| Data staleness window | `<from — to>` | `<n> min` |
| Users unable to `<action>` | `<role>` | `<n>` |
| Observability loss | `<probe / dashboard / log>` | `<duration>` |

### 4.2 Money impact

| Bucket | Estimate (USD) | Evidence |
|---|---|---|
| Realised P&L impact | `<+/- amount>` | `<strategy id / fill log>` |
| Missed fills (estimated) | `<amount>` | `<strategy spec + market replay>` |
| Manual recovery labour | `<hours × hourly>` | `<on-call log>` |
| Data rebuild / backfill cost | `<hours × hourly>` | `<pipeline report>` |
| **Total estimated** | **`<amount>`** | |

If `estimated_cost_usd` is `0 — observability only`, write that explicitly and
explain why no P&L number can be derived.

### 4.3 Data loss / integrity

State one of:

- **none** — every event in §3 is reconstructable from logs / commits / DB.
- **partial** — `<what was lost>`; impact: `<which downstream consumers are affected>`; recovery path: `<backfill / replay / accept-loss>`.
- **full** — `<what was lost>`; **escalate immediately**; do not write §10 until
  data-owner has confirmed scope.

---

## 5. Detection

> **Template:**
> The first signal was `<signal>` from `<source>` at `<time>`. This was
> detected by `<agent / cron / human>` via `<probe / dashboard / log query>`.
>
> **Was detection lucky or designed?**
> - **Designed**: the probe is in `<runbook §x>` / cron `<id>` /
>   `~/.multica/healthcheck.sh`. It runs every `<cadence>`.
>   Detection: <automated / on-call paged / user-reported>.
> - **Lucky**: a human happened to look at the right dashboard. **This is a
>   bug** — promote to §10.1 immediate action.

For each detection source, fill:

| Source | Cadence | First firing at | Lag from incident start |
|---|---|---|---|
| `<healthcheck / cron / metric / user report>` | `<period>` | `<time>` | `<min>` |

---

## 6. Response

> **Template:**
> Containment actions began at `<time>` (MTTR-contain from §3.1: `<actual>`).
> The primary responder was `<agent / human>`. The recovery sequence was:
>
> 1. `<step>` — `<time>` — `<evidence>` — outcome: `<OK / partial / failed>`
> 2. `<step>` — `<time>` — `<evidence>` — outcome: `<…>`
> 3. `<step>` — `<time>` — `<evidence>` — outcome: `<…>`
>
> Final fix landed at `<time>` (MTTR-full: `<actual>`).

### 6.1 Runbook compliance

For each runbook invoked (frontmatter `runbooks_invoked`), tick:

- [ ] The runbook was followed step-by-step
- [ ] Steps were skipped because: `<reason>`
- [ ] Steps were added on-the-fly because: `<reason>`
- [ ] The runbook needs an update (promote to §10.2)

### 6.2 Decision points

For every branching decision during the response, log:

| Time | Decision | Options considered | Chosen | Rationale | Reversible? |
|---|---|---|---|---|---|
| `<HH:MM>` | `<"restart nginx" / "failover to backup" / "halt live trading">` | `<A / B / C>` | `<X>` | `<one line>` | yes / no |

If any decision was made without evidence (e.g. "we guessed"), flag it in §9.

---

## 7. Root cause analysis

Pick **one** of the two formats. Do not mix.

### 7.1 Five Whys (preferred for single-component incidents)

> **Template:**
>
> 1. **Why** did `<user-visible symptom>` happen?
>    Because `<technical cause>`.
> 2. **Why** did `<technical cause>` happen?
>    Because `<deeper cause>`.
> 3. **Why** did `<deeper cause>` happen?
>    Because `<process / config / design gap>`.
> 4. **Why** did `<process gap>` exist?
>    Because `<root cause>`.
> 5. **Why** did `<root cause>` exist?
>    Because `<systemic cause>` — this is the actionable root.

Each "Why" must be backed by evidence (commit, log, config, doc). "Why" can
never be answered with "human error" — that is a category, not an answer.
Drill further.

### 7.2 Fishbone (preferred for multi-component or systemic incidents)

Use six branches (4 P's of incident analysis):

```
                         <symptom>
                              |
        ┌─────────┬───────────┼───────────┬─────────┬─────────┐
     Process      People    Product    Platform   Policy   Procedure
        |           |          |           |         |          |
     <cause>     <cause>    <cause>     <cause>   <cause>   <cause>
```

Mark the **most impactful 2–3 causes** with `**` and link them to §10 actions.

---

## 8. What worked / What didn't

Two columns. Same length per row so the imbalance itself is the lesson.

### 8.1 What worked

- `<thing that helped>` — `<evidence>` — **why it worked**: `<one line>`.
- ...

### 8.2 What didn't

- `<thing that hurt>` — `<evidence>` — **why it hurt**: `<one line>`.
- ...

For every "What didn't" item, §10 must contain a matching action item.
"What didn't" without a follow-up is a complaint, not a postmortem.

---

## 9. Action items

Numbered, owned, dated, verifiable. No "investigate later" — that is a
backlog item, not an action item.

| # | Severity (S/M/L) | Action | Owner | Due (UTC+8) | Verification | Status |
|---|---|---|---|---|---|---|
| 1 | S | `<one-line action>` | `<agent or role>` | `<date>` | `<how we will prove it's done>` | open / done |
| 2 | M | `<…>` | `<…>` | `<…>` | `<…>` | open |
| 3 | L | `<…>` | `<…>` | `<…>` | `<…>` | open |

### 9.1 Tiers

- **S (Small, ≤ 1 day)** — code/config tweak, runbook sentence edit, single
  file. Owner: any L1/L2 agent.
- **M (Medium, ≤ 1 week)** — requires a PR + review, possibly a cron
  registration, possibly a one-host deploy. Owner: L2/L3 agent with sign-off
  from L1.
- **L (Large, ≤ 1 month)** — design change, cross-system rollout, schema
  migration, capacity work. Owner: L2 with smark sign-off, tracked as a
  sub-issue under the parent project.

### 9.2 Verification discipline

The "Verification" column must answer: **what evidence would prove this action
is done and effective?** Examples:

- "Re-running `~/.multica/healthcheck.sh` exits 0 within 30 s of `kill -9` of `<process>`."
- "Adding a synthetic 500-storm in staging triggers the new alert within 60 s."
- "Commit `<sha>` deployed to `<host>`, 24 h of post-deploy metrics show no regression."

A vague verification like "monitor for a week" is rejected. Re-write.

---

## 10. Follow-up & communication

### 10.1 Internal

- Parent issue: `<multica issue id>` → set `metadata.pipeline_status = postmortem_done`.
- Linked sub-issues for each S/M/L action item.
- Cross-reference: `[PM-YYYY-NNN]` is mentioned in the closing comment so the
  history-grep works.

### 10.2 External (only if customer-visible)

Skip this section if the incident was internal-only (default — most are).
If user-visible:

> `<one-line user-facing summary>` posted to `<channel>` at `<time>`. Tone:
> factual, blameless, focused on what we did. No promises of prevention unless
> §9 has a verified action with a deadline.

---

## 11. Sign-off

Two signatures required before merge. Date format ISO 8601 + UTC+8.

- **Author:** `<agent / handle>` — `<YYYY-MM-DDTHH:MM+08:00>` — "postmortem
  complete, all action items owned and dated".
- **Reviewer (L4 / smark):** `<agent / handle>` — `<YYYY-MM-DDTHH:MM+08:00>`
  — "factual accuracy confirmed, action items accepted, severity tier
  matches impact".

If sign-off is blocked because of unresolved disagreement, do **not** merge.
Open a `[need-smark-decision]` thread referencing this doc and link the
decision in a comment.

---

## 12. Appendix (optional)

Anything that does not belong in the body but a future reader may want:
full log excerpts, raw metric snapshots, screen recordings, alternative
hypotheses that were rejected and why. Keep this section small; if it grows
past ~30% of the document, the body is under-written and needs §3–§9
expanded instead.

---

## 13. P2 trimmed variant

For P2 incidents (degraded experience, < 30 min recovery, no money impact),
you may collapse the template to:

- §2 executive summary (mandatory)
- §3 timeline (one table, ≤ 8 rows)
- §5 detection (one paragraph)
- §8 what worked / what didn't (≤ 3 items each)
- §9 action items (≤ 3, all S tier)

Skip §4 money impact, §6.2 decision points, §7 fishbone, §10.2, §11.
Severity tier, related issues, and §9 action items are still mandatory.
A P2 that recurs three times becomes a P1 and must be rewritten in full.

---

## 14. Anti-patterns (do not write a postmortem like this)

- "**Root cause: human error.**" — "human error" is a category. Drill.
- "**Action item: investigate.**" — backlog, not an action. Replace with a
  specific question and an owner.
- "**We should monitor better.**" — what monitor, what threshold, what alert
  channel, what on-call rotation? Rewrite as a concrete §9 row.
- **Filling the doc only after the next incident** — backlog of postmortems
  is itself an incident. The 72-hour window is a hard cap.
- **Blame sentences** — "Alice should have caught this". Rewrite as
  "the runbook did not say to check X under condition Y".
- **Skipping §9 verification** — a §9 row without a verification column is
  not an action item, it is a wish.
- **Postmortem doc as substitute for closing the loop** — the incident is
  not resolved when the doc is written; it is resolved when §9 items are
  *done* and verified. Update the table until every row is `done`.