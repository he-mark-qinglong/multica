// Package gate evaluates trading-strategy hard gates against parsed run
// metrics so overfit / below-bar candidates are flagged automatically at
// ingest time (an in-sample Sharpe of 5.7 with OOS Sharpe 0.6 must fail
// loudly).
//
// The rule set is HARDCODED for now (P1). Per-workspace / per-campaign
// configuration is planned for P2 — when it lands, DefaultRules becomes the
// fallback and Evaluate gains a rules parameter. Keep the rule semantics
// stable: the frontend renders gate_detail entries straight from this
// package's JSON shape.
package gate

import "math"

// Overall gate statuses. The empty string maps to SQL NULL / JSON null,
// meaning "not evaluated / insufficient data".
const (
	StatusPass = "pass"
	StatusFail = "fail"
)

// skipNote marks a rule whose input metric was absent. Skipped rules stay
// visible in the detail array with actual=null and pass=true so a missing
// OOS window can never silently "pass" the gate — it just doesn't fail it.
const skipNote = "skipped: no data"

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
// was absent (rule skipped); Note carries "skipped: no data" in that case
// and is omitted otherwise.
type RuleResult struct {
	Rule      string   `json:"rule"`
	Op        string   `json:"op"`
	Threshold float64  `json:"threshold"`
	Actual    *float64 `json:"actual"`
	Pass      bool     `json:"pass"`
	Note      string   `json:"note,omitempty"`
}

// rule is one hardcoded gate rule: name, comparison op, threshold, and the
// metric extractor. OOS rules return nil when their data is absent and are
// then skipped (visible in detail, not counted as failure).
type rule struct {
	name      string
	op        string
	threshold float64
	actual    func(Metrics) *float64
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

// DefaultRules is the hardcoded P1 rule set, in stable evaluation order:
// in-sample bars first, then the out-of-sample anti-overfit guards.
var DefaultRules = []rule{
	{"sharpe", ">=", 1.0, func(m Metrics) *float64 { return m.Sharpe }},
	{"ann_return", ">=", 0.15, func(m Metrics) *float64 { return m.AnnReturn }},
	// max_drawdown is treated as a magnitude: abs() applied before compare.
	{"max_drawdown", "<", 0.25, func(m Metrics) *float64 {
		if m.MaxDrawdown == nil {
			return nil
		}
		v := math.Abs(*m.MaxDrawdown)
		return &v
	}},
	{"profit_factor", ">", 1.5, func(m Metrics) *float64 { return m.ProfitFactor }},
	{"oos_windows", ">=", 3, func(m Metrics) *float64 {
		if m.OOSWindows == nil {
			return nil
		}
		v := float64(*m.OOSWindows)
		return &v
	}},
	{"oos_sharpe", ">=", 1.0, func(m Metrics) *float64 { return m.OOSSharpe }},
}

// Evaluate runs DefaultRules against m and returns the overall status plus
// one detail entry per rule (always len(DefaultRules) entries, in rule
// order).
//
// Overall status semantics:
//   - "fail" when any EVALUATED rule fails (skipped rules never fail);
//   - "pass" when every evaluated rule passes AND sharpe is present (sharpe
//     is the one mandatory metric — without it there is nothing to gate on);
//   - "" (→ JSON null) otherwise: no usable metrics at all, or metrics
//     present but sharpe missing.
func Evaluate(m Metrics) (status string, detail []RuleResult) {
	detail = make([]RuleResult, 0, len(DefaultRules))
	failed := false
	for _, r := range DefaultRules {
		res := RuleResult{Rule: r.name, Op: r.op, Threshold: r.threshold}
		actual := r.actual(m)
		if actual == nil {
			res.Pass = true
			res.Note = skipNote
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
	case failed:
		return StatusFail, detail
	case m.Sharpe != nil:
		return StatusPass, detail
	default:
		return "", detail
	}
}
