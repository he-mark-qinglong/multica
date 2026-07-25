# Metrics Blob Convention — `extra.verdict` / `kill_reason` / `kill_evidence`

> 状态：约定 v1（2026-07-25）· 消费方：packages/views/compare（w2-s3/w2-s4）· 生产方：任何上传 kind=metrics artifact 的 agent

This document is the contract between **publishers** (any agent / script that uploads
a `kind=metrics` artifact) and **consumers** (compare-page code that reads
`run_metric.extra`). It exists because the server-side `extra` field is an open
JSON bag — so the three keys that drive KILL grey-out and the one-line verdict
badge must be written *exactly* with these names.

There are **zero** server-schema changes required to ship this contract.

---

## 1. Data path (recap)

```
agent / script
  │  builds blob.json  ← producer code (this contract)
  ▼
multica artifact add <task-id> blob.json \
    --kind metrics \
    --meta '{"campaign":"<name>","iteration":"<iter>"}'
  │  client-side validation in server/cmd/multica/cmd_artifact.go:30-37, :69-70
  ▼
server handler ingestRunMetric
  │  parses the blob
  │  copies every key NOT in metricKnownKeys into run_metric.extra
  │  (server/internal/handler/metric.go:140-148)
  ▼
run_metric row (run_metric.extra = JSON of the leftover keys)
  │
  ▼
compare page reads run_metric.extra.verdict / .kill_reason / .kill_evidence
```

The known-key whitelist (anything in this set goes to a typed column instead
of `extra`) lives in `server/internal/handler/metric.go:43-68`:

```
sharpe / sharpe_ratio
sortino / sortino_ratio
calmar / calmar_ratio
ann_return / ann / annualized / annualized_return
max_drawdown / mdd / max_dd / maxdrawdown
profit_factor / pf
oos_sharpe / oos
oos_windows
timeframe / tf
symbols / symbol
params
```

**Warning:** do **not** smuggle verdict information into a known key's alias.
A `{"pf": "KILL"}` will be parsed as a numeric column and the `verdict` is
lost — the column will hold `NULL` and the compare page will render it as
"untested".

---

## 2. The three contract keys (all top-level, all optional strings)

| key              | type   | required when               | meaning                                          |
|------------------|--------|-----------------------------|--------------------------------------------------|
| `verdict`        | string | always when published       | Ledger adjudication — see enum below.            |
| `kill_reason`    | string | **only when verdict=KILL**  | One human-readable sentence for hover-tooltip.   |
| `kill_evidence`  | string | only when verdict=KILL (recommended) | Evidence pointer (file path, issue URL, or run id). |

### 2.1 `verdict` — allowed values

Mirrored from `quant-loop/scripts/build_results_ledger.py:_status()`
(current output, 2026-07-25):

```
PASS       framework consistency AND in-house profitability both hold
           (LIVE candidate — both gates green)
CV_PASS    framework consistency holds, but in-house profitability bar missed
           (e.g. some metrics missing, or below G1/G3/G4/T1 bars)
HOLD       profitable but no framework cross-validation agreement yet
KILL       (a) strategy archived to strategies/_graveyard/, or
           (b) framework verdict is AUTO-ARCHIVE / NOT-PROFITABLE with no
               counter-balancing PASS verdict from another framework
UNTESTED   no in-house metrics AND no framework cross-validation at all
```

**Forward compatibility note (do not remove):**
The ledger workstream (`quant-loop/research/swarm/2026-07-25/gate-ledger-fix/ledger_proposal.py`)
will eventually rename `PASS → PROFITABLE`. Consumers **must** treat any
unrecognized verdict string as "no verdict" (render neutral, do not greylight
as KILL). Publishing code should keep emitting one of the five strings above
until the rename lands.

### 2.2 `kill_reason`

- Type: string. Free-form, short, one sentence. Used as hover tooltip and
  compare-page KILL tooltip.
- Required **only when** `verdict == "KILL"`. Otherwise omit or write `null`.
- Compare-page rule: `extra.kill_reason` non-empty ⇒ row is "killed"
  (see `packages/views/compare/utils/verdict.ts → readVerdict`, w2-s3 ships).
  Do not rely on `verdict` alone — many publishers cannot compute a verdict
  but can still set `kill_reason` to mark a hard kill.
- Prefer a phrase that fits `extra.kill_reason` in a tooltip:
  `"framework verdict NOT-PROFITABLE (backtrader)"`,
  `"archived to strategies/_graveyard/1m_reversal"`,
  `"oos_sharpe < 0 across all windows"`.

### 2.3 `kill_evidence`

- Type: string. A pointer that lets a human trace *why* the row was killed.
- Recommended: a path relative to repo root (e.g.
  `strategies/_graveyard/1m_reversal/synthetic_1h_20260725`),
  an issue URL, or a run id.
- Optional even when `verdict == "KILL"`, but always worth filling.

---

## 3. Worked example (copy-paste safe)

`metrics.json`:

```json
{
  "sharpe": 1.875,
  "ann_return": 0.598,
  "max_drawdown": -0.137,
  "profit_factor": 1.62,
  "oos_sharpe": 2.773,
  "oos_windows": 7,
  "timeframe": "2h",
  "symbols": ["BTCUSDT", "SOLUSDT"],

  "verdict": "CV_PASS",
  "kill_reason": null,
  "kill_evidence": null
}
```

Upload command:

```bash
multica artifact add <task-id> metrics.json \
  --kind metrics \
  --meta '{"campaign":"mtf-xs-pairs","iteration":"mtf_xs_pairs_1m_15m_2h_h3_20260718"}'
```

KILL example:

```json
{
  "sharpe": 0.42,
  "ann_return": -0.18,
  "max_drawdown": -0.34,
  "profit_factor": 0.81,
  "oos_sharpe": null,
  "oos_windows": 0,
  "timeframe": "1h",
  "symbols": ["BTCUSDT"],

  "verdict": "KILL",
  "kill_reason": "framework verdict NOT-PROFITABLE (backtrader); in-house |maxDD|=0.34 exceeds G4",
  "kill_evidence": "strategies/synthetic_1h_20260725/results/framework_cv_backtrader.json"
}
```

---

## 4. Verification

After upload, confirm the keys landed in `run_metric.extra`:

```bash
multica metrics query --campaign mtf-xs-pairs --output json | \
  python3 -c "import sys, json
for r in json.load(sys.stdin):
    e = r.get('extra') or {}
    print(r['campaign'], r['iteration'], '->', e.get('verdict'), '|', e.get('kill_reason'))"
```

The server-side read path is `server/cmd/multica/cmd_metric.go:23-31`.

---

## 5. Relationship to existing data

The 38 pre-existing rows in `run_metric` have **none** of these three keys.
The compare page must therefore render them as "no verdict, not killed"
today. Back-fill of historical rows is the ledger/ops workstream's call —
not in scope here.

---

## 6. Provenance / cross-references

- Server ingest + known-key set: `server/internal/handler/metric.go:43-68, 140-148`
- CLI upload form: `server/cmd/multica/cmd_artifact.go:30-37, 69-70`
- Read-side CLI: `server/cmd/multica/cmd_metric.go:23-31`
- Ledger producer (canonical verdict enum): `quant-loop/scripts/build_results_ledger.py` (`_status`, currently `:221-235`); JSON sidecar at `quant-loop/results-ledger.json` (sibling doc, w2-s5-T10b)
- Future ledger semantics: `quant-loop/research/swarm/2026-07-25/gate-ledger-fix/ledger_proposal.py` (NOT a contract source yet — workstream pending)
- Compare-page consumer: `packages/views/compare/utils/verdict.ts` (`readVerdict`, owned by w2-s3/w2-s4)