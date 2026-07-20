# Top-3 Anchor Proposals (2026-07-20)

> Concrete anchor configs for the three weakest gates identified in
> `gate_audit_20260720.md`: Gate 1 (Hypothesis), Gate 8 (DSR), Gate 10 (Paper trade).
>
> Author: multica-code (run 07dd8587, 2026-07-20)
> Status: proposals — not yet wired

Each proposal is **concrete YAML/snippet**, not description. Owner: smark or
designated autopilot-administrator to wire via `multica autopilot create` (or
issue-template create for A1-001).

---

## A1-001 — Hypothesis template gate (severity 5 → 2)

**Gate**: 1 (Hypothesis)
**Mechanism**: issue template + pre-create lint
**Why**: V5 spec was curve-fit narrative (ship-gates 3/5 FAIL). Without a mechanistic
hypothesis requirement at issue-creation, every fresh strategy starts with zero
mechanism discipline.

```yaml
# File 1: /home/smark/.multica/issue_templates/strategy-variant.md
# (issue template attached to `[STRATEGY-*]` and `[STRATEGY-EXPLORATION-*]` labels)
---
name: strategy-variant
description: Strategy variant or exploration issue
labels: [strategy]
---

## Hypothesis (mechanistic)
<1-2 paragraphs. State the causal mechanism. Cite prior work (paper URL /
market microstructure fact) or mark UNCERTAIN.>

## Signal Design
<How is the signal computed? What is the pure function?>

## UNCERTAIN
<List any unverified assumption. Empty section means all assumptions are cited.>

## Out-of-scope
<What this strategy explicitly does NOT do.>
```

```yaml
# File 2: /home/smark/.multica/issue_templates/strategy-exploration.md
# (variant for exploration label — same shape, allows UNCERTAIN to be longer)
---
name: strategy-exploration
description: Strategy exploration / research-direction issue
labels: [strategy-exploration]
---

## Hypothesis (mechanistic OR open-question)
<State the question, not yet a hypothesis.>

## Cited prior work
<Papers, blog posts, conversations.>

## UNCERTAIN
<What we'd need to verify before promoting to strategy-variant.>
```

```yaml
# Anchor A1-001 (uses anchor_format.md schema)
anchor_id: A1-001
schema_version: 1

gate:
  id: 1
  name: "Hypothesis"

owner:
  agent_id: null  # template-level, no agent
  agent_type: null
  fallback: human_escalation

enforcement:
  mechanism: template
  trigger:
    type: on_create
    spec: "labels IN ('strategy', 'strategy-exploration')"

blast_radius:
  issue_types: [strategy_variant, strategy_exploration]
  status_filter: [null]  # blocks at creation

action:
  kind: template_gate
  criteria: |
    body contains "## Hypothesis (mechanistic)" OR
    body contains "## Hypothesis (mechanistic OR open-question)"
  on_pass: allow create
  on_fail: comment("[GATE-FAIL] Strategy issue missing `## Hypothesis` section. Use template strategy-variant.md or strategy-exploration.md.") + status=blocked

evidence:
  artifact: /home/smark/multica/quant-loop/logs/A1-001.jsonl
  retention_days: 90

rollback:
  command: "multica issue template delete strategy-variant strategy-exploration"
  config_backup: /home/smark/.multica/issue_templates/.bak/A1-001.yaml
```

**Effort**: 2 files + 1 template registration. Owner: issue-templates-admin or
human.

---

## A8-001 — DSR computation gate (severity 5 → 1)

**Gate**: 8 (DSR)
**Mechanism**: autopilot + cron
**Why**: per 22:51 audit, DSR > 0.5 is in spec but no automation enforces it.
Without DSR, multiple-testing bias inflates our top-line OOS metrics; 10-candidate
frontier research batch (per kimi 2026-07-19 02:15) compounds the issue.

```python
# File: /home/smark/multica/quant-loop/_shared/validation/dsr_gate.py
# Pure function: compute Deflated Sharpe Ratio from OOS trade list.
# Reference: Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014).

from __future__ import annotations
import math
import scipy.special
from typing import Sequence


def deflated_sharpe(
    sharpe_estimates: Sequence[float],
    n_trials: int,
    n_obs_per_trial: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """
    Returns probability that observed max-Sharpe exceeds the theoretical
    E[max SR] under the null. DSR > 0.5 ⇒ edge is statistically defensible
    after correcting for multiple testing.

    Args:
        sharpe_estimates: per-trial Sharpe ratios tried in the research batch.
        n_trials: number of independent strategy variants tried.
        n_obs_per_trial: OOS trades per variant.
        skewness, kurtosis: return distribution moments (defaults: Gaussian).
    """
    if not sharpe_estimates:
        return 0.0
    e_max_sr = (
        (1 - scipy.special.gammainc((n_obs_per_trial - 1) / 2, 0.5))
        * math.sqrt(2 * math.log(n_trials))
    )
    # ... full Bailey-Lopez-de-Prado computation follows (TODO 1-line per spec).
    # For this proposal: shape, not implementation. Real impl ~30 LOC.
    raise NotImplementedError("TODO: port from quant-research-methodology skill")
```

```yaml
# Anchor A8-001 (cron-triggered; reads in_review strategy issues + 10-batch context)
anchor_id: A8-001
schema_version: 1

gate:
  id: 8
  name: "DSR"

owner:
  agent_id: 07cc9e07-3832-4c38-8df4-565cea79cbf2  # strategy-validator
  agent_type: agent
  fallback: human_escalation

enforcement:
  mechanism: autopilot
  trigger:
    type: schedule
    spec: "37 * * * *"
    tz: Asia/Shanghai

blast_radius:
  issue_types: [strategy_backtest, strategy_exploration]
  status_filter: [in_review]

action:
  kind: gate
  criteria: |
    dsr > 0.5
    AND n_oos_trades_total >= 30
    AND dsr computed from min(in_house_sharpe, freqtrade_sharpe, backtrader_sharpe)
  on_pass: comment("[GATE-PASS] DSR dsr=<val> trades=<n>") + no status flip
  on_fail: comment("[GATE-FAIL] DSR dsr=<val> trades=<n>: <reason>") + status=blocked

evidence:
  artifact: /home/smark/multica/quant-loop/logs/A8-001.jsonl
  retention_days: 90

rollback:
  command: "multica autopilot delete <autopilot_id>"
  config_backup: /home/smark/.multica/autopilot-backups/A8-001.yaml
```

**Effort**: 1 Python file (~30 LOC) + 1 autopilot registration. The Python
function shape is shown; real implementation = port of Bailey-López de Prado's
formula (already partially shipped in `quant-research-methodology` skill per
quant-researcher agent skill list).

---

## A10-001 — Paper trade duration gate (severity 5 → 2)

**Gate**: 10 (Paper trade)
**Mechanism**: autopilot + cron
**Why**: paper trade is the bridge from backtest → live. Without an autopilot that
tracks N days of paper, a backtest-passed strategy can flip to `done` without
ever touching Binance testnet. Live-skip failure mode.

```python
# File: /home/smark/multica/quant-loop/_shared/validation/paper_tracker.py
# Tracks paper-trade days per strategy. Reads from binance testnet connector
# (see specs/SPEC_live_paper_connector_binance_usdm.md).

from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path


PAPER_STATE_DIR = Path("/home/smark/multica/quant-loop/state/paper")


def paper_days(strategy_id: str) -> int:
    """Return number of consecutive UTC days the strategy has been paper-traded."""
    state_file = PAPER_STATE_DIR / f"{strategy_id}.json"
    if not state_file.exists():
        return 0
    state = json.loads(state_file.read_text())
    started = datetime.fromisoformat(state["started_at_utc"])
    return (datetime.now(timezone.utc) - started).days


def paper_passes(strategy_id: str, min_days: int = 14) -> bool:
    """Return True iff paper-trade duration >= min_days."""
    return paper_days(strategy_id) >= min_days
```

```yaml
# Anchor A10-001
anchor_id: A10-001
schema_version: 1

gate:
  id: 10
  name: "Paper trade"

owner:
  agent_id: 07cc9e07-3832-4c38-8df4-565cea79cbf2  # strategy-validator
  agent_type: agent
  fallback: human_escalation

enforcement:
  mechanism: autopilot
  trigger:
    type: schedule
    spec: "37 8 * * *"  # 08:37 daily (after framework-validate :37 hourly slot)
    tz: Asia/Shanghai

blast_radius:
  issue_types: [strategy_live_transition]
  status_filter: [in_review]

action:
  kind: gate
  criteria: |
    paper_days(strategy_id) >= 14
    AND no_errors in last 7 days (from /home/smark/multica/quant-loop/state/paper/<strategy_id>.json)
  on_pass: comment("[GATE-PASS] Paper trade paper_days=<n>") + no status flip
  on_fail: comment("[GATE-FAIL] Paper trade paper_days=<n>, need >=14: <reason>") + status=blocked

evidence:
  artifact: /home/smark/multica/quant-loop/logs/A10-001.jsonl
  retention_days: 90

rollback:
  command: "multica autopilot delete <autopilot_id>"
  config_backup: /home/smark/.multica/autopilot-backups/A10-001.yaml
```

**Effort**: 1 Python file (~30 LOC) + 1 autopilot registration. Depends on
`binance_usdm_paper` connector already shipped (per `specs/SPEC_live_paper_connector_binance_usdm.md`).

**Precondition (must verify before wiring)**: confirm the `binance_usdm_paper`
connector has produced at least one state file under
`/home/smark/multica/quant-loop/state/paper/<strategy_id>.json`. If the directory
does not exist (`ls /home/smark/multica/quant-loop/state/paper/` returns "No such
file or directory"), the gate will fire `paper_days(...) = 0` for every strategy
and block all live transitions. Wiring sequence therefore is: (a) confirm
connector shipped state, (b) confirm at least one strategy has paper_days ≥ 14,
(c) only then wire the cron anchor.

---

## Selection criteria recap

- **Coverage diversity**: pre-creation template (A1) / mid-pipeline compute (A8) /
  post-backtest time-based (A10) — three mechanism types, no two anchors patch
  the same shape of gap.
- **Failure-mode diversity**: V5 curve-fit (A1), multiple-testing bias (A8),
  live-skip (A10) — three different historical failure modes caught.
- **Effort symmetry**: 1-2 files + 1 cron/registration each. None requires
  redesigning existing infrastructure.
- **Reversibility**: each rollback is one `multica autopilot delete` or
  `multica issue template delete` command. Matches smark-decision-loop rule.

---

## Wiring order (recommended)

1. **A1-001** first — template-only, no live compute. Cheapest to deploy, biggest
   human-discipline lift. Catches V5-style curve-fit at issue creation.
2. **A8-001** second — DSR depends on having a 10-trial batch context to compute
   against. Pair with the next frontier research batch (kimi 02:15 directive:
   1-2 promoted at a time, so the batch size is small). Wiring A8 with the
   strategy-validator agent (07cc9e07) slots cleanly into existing cron `37 *`.
3. **A10-001** third — needs the `binance_usdm_paper` connector to have shipped
   paper state to `/home/smark/multica/quant-loop/state/paper/<strategy_id>.json`. Confirm connector
   status before wiring.

---

## Out of scope for this proposal

- **Gate 11 (Live)** severity 5 — close-second. Evidence Review Gate already
  partial. Fix is one-line addition to its close-out check (require "live gate"
  section). Defer to next cycle.
- **Gate 7 (Walk-forward)** severity 4 — `cpcv.py` exists but no automation.
  Could become A7-001 next cycle.
- **1d-TF ban + family-exhaustion counter** — gates 12/13 if smark confirms in
  canonical SPEC. Reconstruct document flagged these as UNCERTAIN.