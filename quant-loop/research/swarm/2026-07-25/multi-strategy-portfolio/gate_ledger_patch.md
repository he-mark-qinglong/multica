# Gate-Ledger fix — patch proposal

> Research output only. Production files are NOT modified by this task.

## 1. Problem statement

Current `server/internal/gate/gate.go` skips a rule when its input metric is `nil`.
This allows strategies such as `vpvr_stable_depeg_regime_4h_20260716_p3opt_091`
(Sharpe 31.7, but no `ann_return`, no OOS windows, no `profit_factor`) to obtain
an overall `pass` because the only evaluated rule is `sharpe >= 1.0`.

`scripts/build_results_ledger.py` then collapses the `pass/fail` signal into a
single `Verdict` column that mixes three unrelated concepts:

- framework-consistency pass (`W5_PASS` / cross-framework tolerance)
- in-house profitability (`CV_PASS`)
- kill/hold decisions

## 2. Desired behaviour

### 2.1 Gate semantics

| input condition | old status | new status | reason |
|-----------------|------------|------------|--------|
| `sharpe` missing | `null` | `null` ("insufficient data") | nothing to gate on |
| `sharpe` present, any **mandatory** metric missing | `pass` | `fail` | a Sharpe-only pass is not enough |
| `sharpe` present, all mandatory metrics present, all pass | `pass` | `pass` | normal pass |
| any evaluated rule fails | `fail` | `fail` | unchanged |
| `profit_factor` missing but `profit_factor_daily` present | skipped | evaluated with daily PF | computable substitute |

Mandatory metrics (P1 hard gate): `ann_return`, `max_drawdown`, `profit_factor` (or substitute), `oos_sharpe`, `oos_windows`.

`oos_windows` must be `>= 3`. Missing or fewer windows is a fail.

### 2.2 Ledger semantics

New state machine with explicit columns:

| State | Meaning | Entry condition |
|-------|---------|-----------------|
| `UNTESTED` | No metrics, no frameworks | No `metrics.json`, no `framework_cv_*.json` |
| `NO_DATA` | Sharpe present but core metrics missing | Gate would return `null` / `fail` for missing data |
| `PROFITABLE` | In-house gates G1-G4 pass, no framework CV yet | Sharpe≥1, ann≥0.15, maxDD>-0.25, PF>1.5, OOS≥3 windows |
| `CV_PASS` | Cross-framework validation passed | At least one `framework_cv_*.json` has `PASS` / `W5_PASS` and in-house gates pass |
| `HOLD` | Borderline / incomplete / fee-shock divergence | e.g. `W5_FAIL_FEE_SHOCK`, OOS pending, single-framework pass only |
| `KILL` | Graveyard or clear failure | Graveyard dir, `AUTO-ARCHIVE`, `NOT-PROFITABLE`, gate fail |

## 3. Minimal server patch

### 3.1 `server/internal/gate/gate.go`

Replace the rule/evaluator block with a strict version. The JSON shape of
`RuleResult` stays identical so the frontend is unaffected.

```go
package gate

import "math"

const (
    StatusPass = "pass"
    StatusFail = "fail"
)

const skipNote = "skipped: no data"
const missingNote = "missing: required metric"

type Metrics struct {
    Sharpe           *float64
    AnnReturn        *float64
    MaxDrawdown      *float64
    ProfitFactor     *float64 // raw PF if reported
    ProfitFactorDaily *float64 // computable substitute from daily returns
    OOSSharpe        *float64
    OOSWindows       *int32
}

type RuleResult struct {
    Rule      string   `json:"rule"`
    Op        string   `json:"op"`
    Threshold float64  `json:"threshold"`
    Actual    *float64 `json:"actual"`
    Pass      bool     `json:"pass"`
    Note      string   `json:"note,omitempty"`
}

type rule struct {
    name      string
    op        string
    threshold float64
    required  bool            // false => skip when absent; true => fail when absent
    actual    func(Metrics) *float64
}

func compare(op string, actual, threshold float64) bool {
    switch op {
    case ">=":
        return actual >= threshold
    case ">":
        return actual > threshold
    case "<":
        return actual < threshold
    }
    return false
}

func profitFactor(m Metrics) *float64 {
    if m.ProfitFactor != nil {
        return m.ProfitFactor
    }
    return m.ProfitFactorDaily
}

var DefaultRules = []rule{
    {"sharpe", ">=", 1.0, false, func(m Metrics) *float64 { return m.Sharpe }},
    {"ann_return", ">=", 0.15, true, func(m Metrics) *float64 { return m.AnnReturn }},
    {"max_drawdown", "<", 0.25, true, func(m Metrics) *float64 {
        if m.MaxDrawdown == nil {
            return nil
        }
        v := math.Abs(*m.MaxDrawdown)
        return &v
    }},
    {"profit_factor", ">", 1.5, true, profitFactor},
    {"oos_windows", ">=", 3, true, func(m Metrics) *float64 {
        if m.OOSWindows == nil {
            return nil
        }
        v := float64(*m.OOSWindows)
        return &v
    }},
    {"oos_sharpe", ">=", 1.0, true, func(m Metrics) *float64 { return m.OOSSharpe }},
}

func Evaluate(m Metrics) (status string, detail []RuleResult) {
    detail = make([]RuleResult, 0, len(DefaultRules))
    failed := false
    sharpeEvaluated := false

    for _, r := range DefaultRules {
        res := RuleResult{Rule: r.name, Op: r.op, Threshold: r.threshold}
        actual := r.actual(m)
        if actual == nil {
            if r.required {
                res.Note = missingNote
                res.Pass = false
                failed = true
            } else {
                res.Pass = true
                res.Note = skipNote
            }
        } else {
            res.Actual = actual
            res.Pass = compare(r.op, *actual, r.threshold)
            if r.name == "sharpe" {
                sharpeEvaluated = true
            }
        }
        if !res.Pass {
            failed = true
        }
        detail = append(detail, res)
    }

    switch {
    case failed:
        return StatusFail, detail
    case sharpeEvaluated:
        return StatusPass, detail
    default:
        return "", detail
    }
}
```

### 3.2 `server/internal/handler/metric.go`

Map a new alias `profit_factor_daily` and pass it to the gate.

```go
var metricFloatAliases = map[string][]string{
    // ... existing aliases ...
    "profit_factor":        {"profit_factor", "pf"},
    "profit_factor_daily":  {"profit_factor_daily", "pf_daily", "daily_profit_factor"},
    // ...
}

func gateMetricsFromFields(f *runMetricFields) gate.Metrics {
    return gate.Metrics{
        Sharpe:            f.Sharpe,
        AnnReturn:         f.AnnReturn,
        MaxDrawdown:       f.MaxDrawdown,
        ProfitFactor:      f.ProfitFactor,
        ProfitFactorDaily: f.ProfitFactorDaily,
        OOSSharpe:         f.OOSSharpe,
        OOSWindows:        f.OOSWindows,
    }
}
```

If you also want the ingestor to **compute** `profit_factor_daily` when only a
daily equity array is present in the blob, add a helper after parsing the known
keys:

```go
func dailyProfitFactorFromEquity(equity []float64) *float64 {
    if len(equity) < 2 {
        return nil
    }
    gross, loss := 0.0, 0.0
    for i := 1; i < len(equity); i++ {
        r := equity[i]/equity[i-1] - 1
        if r > 0 {
            gross += r
        } else {
            loss += -r
        }
    }
    if loss == 0 {
        return nil
    }
    pf := gross / loss
    return &pf
}
```

Then in `parseRunMetricJSON` check for an `equity_daily` array and compute the
substitute.

### 3.3 DB migration (optional, if storing the substitute)

```sql
ALTER TABLE run_metric ADD COLUMN profit_factor_daily DOUBLE PRECISION;
```

This keeps `profit_factor` and `profit_factor_daily` separate so the original
agent-reported value is preserved.

## 4. Ledger script patch

`scripts/build_results_ledger.py` should change its `_status` function and table
headers.

### 4.1 New `_status` logic

```python
def _status(row: dict[str, Any]) -> str:
    if row["status"] == "GRAVEYARD":
        return "KILL"

    inhouse_sharpe = row.get("sharpe_inhouse")
    inhouse_pf = row.get("pf_inhouse")
    inhouse_mdd = row.get("maxdd_inhouse")
    frameworks = row.get("frameworks", {})

    has_metrics = any(v is not None for v in (inhouse_sharpe, inhouse_pf, inhouse_mdd))
    if not has_metrics and not frameworks:
        return "UNTESTED"

    # Gate pass on in-house numbers (G1-G4 + OOS windows)
    g1 = inhouse_sharpe is not None and inhouse_sharpe >= 1.0
    g2 = row.get("ann_return") is not None and row["ann_return"] >= 0.15
    g3 = inhouse_mdd is not None and abs(inhouse_mdd) < 0.25
    g4 = inhouse_pf is not None and inhouse_pf > 1.5
    # oos_windows is not currently extracted; add it to the row scan
    g5 = row.get("oos_windows") is not None and row["oos_windows"] >= 3

    if not has_metrics:
        return "NO_DATA"
    if not all((g1, g2, g3, g4, g5)):
        return "HOLD"  # or KILL if a hard fail is unambiguous

    verdicts = [v.get("verdict", "") for v in frameworks.values()]
    if any("PASS" in v or "WITHIN_TOLERANCE" in v or "W5_PASS" in v for v in verdicts):
        return "CV_PASS"

    if frameworks:
        # Frameworks exist but none passed
        if any("AUTO-ARCHIVE" in v or "NOT-PROFITABLE" in v or "W5_FAIL" in v for v in verdicts):
            return "KILL"
        return "HOLD"

    return "PROFITABLE"
```

### 4.2 New table headers

```python
"| Strategy | TF | Family | Sharpe(in-house) | PF | maxDD | Trades | OOS windows | BT Sharpe | FT Sharpe | VBT Sharpe | State |",
```

And emit `State` instead of `Verdict`.

## 5. Roll-out steps

1. Merge the gate patch first and run `POST /api/metrics/reevaluate` on the
   workspace to backfill `gate_status` under the new rules.
2. Add `profit_factor_daily` extraction/computation for strategies that do not
   report raw `profit_factor`.
3. Land the ledger script patch and regenerate `results-ledger.md`.
4. Audit any strategy that flips from `PASS` to `fail` / `HOLD`; most will be
   the ones currently passing on Sharpe alone.
