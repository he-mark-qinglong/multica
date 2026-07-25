// Package gate evaluates trading-strategy hard gates against parsed run
// metrics so overfit / below-bar candidates are flagged automatically at
// ingest time (an in-sample Sharpe of 5.7 with OOS Sharpe 0.6 must fail
// loudly).
//
// PROPOSAL (gate-ledger-fix, 2026-07-25):
//  1. All core gate metrics are REQUIRED. Missing ann_return / max_drawdown /
//     profit_factor / oos_sharpe / oos_windows now yields a FAIL, not a skip.
//  2. sharpe remains the bare-minimum metric: without it the row is "no-data".
//  3. profit_factor, if absent, must be supplied as a computable proxy by the
//     ingest layer (e.g. daily-return PF from an equity_curve/daily_returns
//     array in the same blob). gate itself does not fetch other artifacts.
//  4. oos_windows enforces at least 3 walk-forward windows.
//  5. Status values are now "pass" | "fail" | "no-data". A DB migration
//     updates the gate_status CHECK constraint and API docs.
package gate

import "math"

// Overall gate statuses. The empty string maps to SQL NULL / JSON null,
// meaning "not evaluated / insufficient data".
const (
	StatusPass   = "pass"
	StatusFail   = "fail"
	StatusNoData = "no-data"
)

const (
	// skipNote marks a rule whose input metric was absent but is OPTIONAL.
	// Kept only for forward compatibility; every current DefaultRules metric
	// is required.
	skipNote = "skipped: no data"
	// missingNote marks a required metric that was absent.
	missingNote = "missing required metric"
)

// Metrics is the evaluator input: one pointer per run_metric column the gate
// rules read. Nil means "absent" (SQL NULL / missing in the source blob).
type Metrics struct {
	Sharpe       *float64
	AnnReturn    *float64
	MaxDrawdown  *float64
	ProfitFactor *float64
	OOSSharpe    *float64
	OOSWindows   *int32
}

// RuleResult is one gate rule's outcome. Actual is nil when the input metric
// was absent (rule skipped or missing). Note carries "skipped: no data" for
// optional skips and "missing required metric" for required misses.
type RuleResult struct {
	Rule      string   `json:"rule"`
	Op        string   `json:"op"`
	Threshold float64  `json:"threshold"`
	Actual    *float64 `json:"actual"`
	Pass      bool     `json:"pass"`
	Note      string   `json:"note,omitempty"`
}

// rule is one hardcoded gate rule: name, comparison op, threshold, metric
// extractor and whether the metric is required for a PASS.
type rule struct {
	name      string
	op        string
	threshold float64
	actual    func(Metrics) *float64
	required  bool
}

// compare applies op to actual vs threshold. max_drawdown is compared as a
// MAGNITUDE: agents emit it both as 0.20 and -0.20, so the rule takes
// abs(actual) — a 25% drawdown fails "< 0.25" however it is signed.
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

// DefaultRules is the strict P1.5 rule set. Every metric is required except
// for future optional rules. Required + absent => FAIL (sharpe absent is the
// special case that yields "no-data").
var DefaultRules = []rule{
	{"sharpe", ">=", 1.0, func(m Metrics) *float64 { return m.Sharpe }, true},
	{"ann_return", ">=", 0.15, func(m Metrics) *float64 { return m.AnnReturn }, true},
	// max_drawdown is treated as a magnitude: abs() applied before compare.
	{"max_drawdown", "<", 0.25, func(m Metrics) *float64 {
		if m.MaxDrawdown == nil {
			return nil
		}
		v := math.Abs(*m.MaxDrawdown)
		return &v
	}, true},
	{"profit_factor", ">", 1.5, func(m Metrics) *float64 { return m.ProfitFactor }, true},
	{"oos_windows", ">=", 3, func(m Metrics) *float64 {
		if m.OOSWindows == nil {
			return nil
		}
		v := float64(*m.OOSWindows)
		return &v
	}, true},
	{"oos_sharpe", ">=", 1.0, func(m Metrics) *float64 { return m.OOSSharpe }, true},
}

// Evaluate runs DefaultRules against m and returns the overall status plus
// one detail entry per rule (always len(DefaultRules) entries, in rule order).
//
// Overall status semantics:
//   - "fail" when any evaluated rule fails OR a required metric is absent;
//   - "pass" when every required rule is present AND passes;
//   - "no-data" when sharpe is absent and nothing failed (no basis to judge);
//   - "" (→ JSON null) reserved for backward compatibility only.
func Evaluate(m Metrics) (status string, detail []RuleResult) {
	detail = make([]RuleResult, 0, len(DefaultRules))
	failed := false
	sharpeMissing := false
	for _, r := range DefaultRules {
		res := RuleResult{Rule: r.name, Op: r.op, Threshold: r.threshold}
		actual := r.actual(m)
		if actual == nil {
			if r.required {
				res.Note = missingNote
				res.Pass = false
				if r.name == "sharpe" {
					sharpeMissing = true
				}
			} else {
				res.Pass = true
				res.Note = skipNote
			}
		} else {
			res.Actual = actual
			res.Pass = compare(r.op, *actual, r.threshold)
		}
		if !res.Pass {
			failed = true
		}
		detail = append(detail, res)
	}

	switch {
	case failed && sharpeMissing:
		// If sharpe is missing we cannot meaningfully fail; mark as no-data.
		return StatusNoData, detail
	case failed:
		return StatusFail, detail
	case sharpeMissing:
		return StatusNoData, detail
	default:
		return StatusPass, detail
	}
}
