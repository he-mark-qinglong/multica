# 11-gate Enforcement Audit (2026-07-20)

> Audit snapshot for `STRATEGY_DEV_SPEC.md` v1. Maps each of the 11 spec gates
> to the autopilots / scripts / lints that enforce it.
>
> Author: multica-code (run 07dd8587, 2026-07-20)
> Status: snapshot — re-run when autopilot set changes

---

## Method

1. Enumerate all 23 active autopilots in workspace (`multica autopilot list`)
2. For each autopilot, read description; classify as one of:
   - `gate-anchor` — directly enforces one of the 11 gates
   - `gate-adjacent` — touches a gate but does not enforce it
   - `operational` — dispatch / sweep / monitor / archive, NOT a gate
3. Cross-reference with `quant-loop/_shared/validation/`, `quant-loop/scripts/`, and
   `quant-loop/strategies/*/tests/` for repo-level enforcement
4. For each of the 11 gates, score enforcement severity 1-5 (5 = worst gap)

**Severity scale**:
- 1 = automated + strict + per-issue + hard reject on fail
- 2 = automated + weekly cadence (catches drift but not pre-merge)
- 3 = partial (code shipped, lint missing, or hourly vs per-commit)
- 4 = script exists but no automation (runs only on explicit dispatch)
- 5 = no enforcement (can be silently bypassed)

---

## Top-3 weakest gates (severity 5)

After the audit (full table below), three gates scored the maximum 5:
- **Gate 1 — Hypothesis** (no template enforcement)
- **Gate 8 — DSR** (in spec, not in any autopilot or lint)
- **Gate 10 — Paper trade** (no time-based autopilot tracks N days of paper)

(Gate 11 — Live — also scored 5; see "Top-3 candidates" discussion below for
why we picked the three above.)

---

## Full 11-gate enforcement table

| # | Gate | Verdict format | Existing enforcement | Severity (1-5) | Notes |
|---|------|---------------|---------------------|----------------|-------|
| 1 | **Hypothesis** | mechanistic + testable | **NONE** | **5** | No issue template requires `## Hypothesis (mechanistic)`. Any agent can create a `[STRATEGY-*]` issue with no causal story. Largest known failure mode: V5 "trend-following on vol-expansion" was curve-fit narrative, ship-gates 3/5 FAIL. The mechanism check would have caught it at creation. |
| 2 | **Data QA** | manifest + integrity hash | Partial: per-strategy `tests/test_data_loader.py` + publish-gate checks parquet exists | 3 | Repo-wide lint missing. New strategies can lack a `tests/test_data_loader.py` until human review. |
| 3 | **Signal** | pure-function + no-look-ahead proof | Partial: publish-gate structure check + framework-validate divergence detection | 3 | No dedicated look-ahead linter. A signal that quietly uses `close.shift(-1)` would be caught only by framework-CV divergence or by manual review. |
| 4 | **Engine** | deterministic + framework-CV | Partial: framework-validate hourly (rotating list of 6 frameworks) + REGRESSION-TEST daily 04:23 | 3 | Catches drift but not per-commit. If a backtest-engine dependency breaks inside a worktree, divergence surfaces only at next hourly run (mean: 30 min exposure). |
| 5 | **In-sample** | reserve judgment | n/a (informational) | 1 | No automation needed; this gate exists to remind authors IS alone is not edge. |
| 6 | **OOS** | Sharpe ≥ 1.0, annualized ≥ 15%, n ≥ 30 trades | **Automated**: SMA-34915 OOS harness + SMA-34960 3-window + SMA-34961 G1-G7 + framework-validate (rotates ONE framework per hourly run, freqtrade → backtrader → vectorbt → jesse → nautilus_trader → zipline-reloaded; OOS cross-validation converges over ~6h of cron slots) | 1 | Strongest gate. Block-merge SMA-34962 still `in_review` per 22:51 audit; if unblocked, the gate becomes stricter. |
| 7 | **Walk-forward** | wf_ratio ≥ 0.5, min_oos_sharpe ≥ 0 | Partial: `quant-loop/_shared/validation/cpcv.py` shipped, not pre-merge enforced | 4 | A new strategy can pass OOS gate but skip walk-forward; OOS gate alone doesn't catch w1=-14.77 catastrophic-window failure mode (V10 lesson). |
| 8 | **DSR** | DSR > 0.5 | **NONE** | **5** | Per 22:51 audit: "DSR > 0.5 (in spec, missing from gate code)". No autopilot computes Deflated Sharpe. Without DSR, multiple-testing bias inflates our top-line OOS metrics. |
| 9 | **Risk sizing** | vol-targeted + per-symbol cap | Partial: `quant-loop/_shared/sizing/vol_target.py` shipped; REGRESSION-TEST catches silent drift on existing sizing | 3 | No pre-trade gate that asserts new strategies use vol_target. Manual review catches it; humans miss ~20% (per W5 audit). |
| 10 | **Paper trade** | live-paper N days, no real orders | **NONE** | **5** | No autopilot tracks N days of paper. README-level rule only. Failure mode: a backtest-passed strategy can flip to `done` without ever touching Binance testnet. |
| 11 | **Live** | paper-trade gate + framework-CV passed | Partial: Evidence Review Gate (30m) reviews commit/branch/PR/test evidence on `in_review → done` close-out | 5 | Evidence Review Gate does NOT require a "live gate" section. A strategy can ship to live if its PR has passing tests + commit history — without an explicit assertion "paper trade N days cleared, no real-money slippage surprises". |

---

## Autopilots classified (sanity check on the 23 active autopilots)

| # | Autopilot | Cadence | Status | Classification | Touches gate |
|---|----------|---------|--------|---------------|--------------|
| 1 | Graph Janitor | hourly | active | operational | none |
| 2 | Idle Agent Dispatcher | 3m | active | operational | none |
| 3 | Daily Done-Sweeper | 4am | active | operational | none |
| 4 | strategy-archiver | Sun 9am | active | operational | none |
| 5 | campaign-tree-builder | 6h | active | operational | none |
| 6 | smark-decision-loop | 2h | active | operational | none |
| 7 | Workspace-Pruner | 03:17 | active | operational | none |
| 8 | Error-Pattern-Recorder | 30m | active | operational | none |
| 9 | DB-POOL-MONITOR | 2m | paused | operational | none |
| 10 | REGRESSION-TEST | 04:23 | active | gate-adjacent | 4 (Engine silent drift) |
| 11 | DEPLOY-FAIL-DETECT | 1m | paused | operational | none |
| 12 | Multica Dispatch | 5m | active | operational | none |
| 13 | publish-gate | 53 hourly | active | gate-adjacent | 2, 3, 6 (publish path) |
| 14 | framework-validate | 37 hourly | active | gate-anchor (partial) | 4, 6 (CV) |
| 15 | Strategy Portfolio Diversity Watchdog | 1d | active | gate-adjacent | family-exhaustion rule (out-of-spec) |
| 16 | GitHub Strategy Watcher | 6h | active | operational | none |
| 17 | Issue-Graph Generator | 1d | active | operational | none |
| 18 | Product Triage Weekly | Mon 09:00 | active | operational | none |
| 19 | in_review Triage | 0:10 | active | operational | none |
| 20 | Human Escalation Router | event+1h | active | operational | none |
| 21 | Evidence Review Gate | 30m | active | gate-anchor (partial) | 11 (live close-out) |
| 22 | Cross-Project Agent Intel Sync | 2h | active | operational | none |
| 23 | Workspace Queue Balancer | 30m | active | operational | none |

**Operational count**: 18 / 23 = 78% (the autopilot fleet is mostly housekeeping)
**Gate-anchor count**: 2 / 23 = 9% (framework-validate + Evidence Review Gate, both partial)
**Gap**: 3 / 23 = 13% (gate-adjacent but not enforcing)

The 11-gate pipeline needs **3 new anchor-class autopilots** (one per top-3 gap) to
reach minimum gate coverage. See `anchor_proposals.md`.

---

## Top-3 candidates (for `anchor_proposals.md`)

Three severity-5 gates were identified:
- **Gate 1** (Hypothesis) — pre-creation template
- **Gate 8** (DSR) — mid-pipeline computation
- **Gate 10** (Paper trade) — post-backtest time-based

(Gate 11 — Live — also scored 5; we exclude it from Top-3 because it has Evidence
Review Gate as a partial anchor — only one of two checks (close-out) is missing
the "live gate" assertion. Close-second for the Top-3 list.)

**Why these three**:
- **Coverage diversity**: pre-creation / mid-pipeline / post-backtest / time-based.
  Different mechanism types ensure we're not just patching one shape of gap.
- **Failure mode diversity**: V5 small-sample Sharpe (Gate 8 would have caught
  the inflation), Curve-fit narrative (Gate 1 would have caught V5 spec),
  Live skip (Gate 10 would catch a "done with no paper" path).
- **Effort symmetry**: each is one autopilot + one file + one cron. None requires
  redesigning existing infrastructure.

---

## What this audit does NOT cover

- **Pre-commit hooks**: not enumerated; would need `find . -name .pre-commit-config.yaml`
  pass. Out of scope for this snapshot.
- **1d-TF ban** + **family-exhaustion counter** (mentioned in 22:51 audit as
  possibly in canonical SPEC): not in the 11-gate table above; would add gates 12
  and 13 if smark confirms them.
- **Per-strategy tests** beyond the existence check: not enumerated.

Run `multica autopilot list --output json > autopilot_snapshot.json` to re-run this
audit on demand.
