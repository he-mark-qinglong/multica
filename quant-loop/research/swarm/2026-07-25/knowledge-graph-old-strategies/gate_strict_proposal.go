// Package gate — STRICT evaluation proposal (server/internal/gate/gate.go patch)
//
// Goal: close the "skip = pass" loophole.  Core metrics missing must fail or
// become "no_data"; profit_factor should be computable from daily returns when
// the scalar is absent; OOS windows must be present and >= 3.
//
// This file is a drop-in design, NOT compiled into the server binary.  The
// proposed diff is summarised in knowledge-graph-old-strategies/SUMMARY.md.
package gate

import "math"

// Proposed overall statuses.  Empty string keeps backward compatibility with
// SQL NULL / JSON null meaning "not evaluated".
const (
	StatusNoData     = "no_data"     // insufficient metrics to evaluate
	StatusFail       = "fail"        // one or more evaluated gates failed
	StatusHold       = "hold"        // in-sample profitable but OOS not proven
	StatusCVPass     = "cv_pass"     // passed all hard gates incl. OOS
	StatusProfitable = "profitable"  // in-sample gates pass, OOS missing/failing
)

// Rule-level result shape stays identical so the frontend needs no change.
type RuleResult struct {
	Rule      string   `json:"rule"`
	Op        string   `json:"op"`
	Threshold float64  `json:"threshold"`
	Actual    *float64 `json:"actual"`
	Pass      bool     `json:"pass"`
	Note      string   `json:"note,omitempty"`
}

type Metrics struct {
	Sharpe       *float64
	AnnReturn    *float64
	MaxDrawdown  *float64
	ProfitFactor *float64
	OOSSharpe    *float64
	OOSWindows   *int32
	// NEW: optional daily returns so the gate can compute a PF fallback
	DailyReturns []float64
}

type rule struct {
	name       string
	op         string
	threshold  float64
	required   bool        // true -> missing value fails the gate
	actual     func(Metrics) *float64
	fallback   func(Metrics) *float64 // optional fallback when actual is nil
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

// profitFactorFromReturns computes gross daily PF = sum(gain days)/sum(|loss days|).
func profitFactorFromReturns(rets []float64) *float64 {
	if len(rets) == 0 {
		return nil
	}
	pos, neg := 0.0, 0.0
	for _, r := range rets {
		if r > 0 {
			pos += r
		} else if r < 0 {
			neg += -r
		}
	}
	if neg <= 0 {
		return nil
	}
	v := pos / neg
	return &v
}

// StrictRules is the proposed replacement for DefaultRules.
var StrictRules = []rule{
	{"sharpe", ">=", 1.0, true, func(m Metrics) *float64 { return m.Sharpe }, nil},
	{"ann_return", ">=", 0.15, true, func(m Metrics) *float64 { return m.AnnReturn }, nil},
	{"max_drawdown", "<", 0.25, true, func(m Metrics) *float64 {
		if m.MaxDrawdown == nil {
			return nil
		}
		v := math.Abs(*m.MaxDrawdown)
		return &v
	}, nil},
	{"profit_factor", ">", 1.5, true,
		func(m Metrics) *float64 { return m.ProfitFactor },
		func(m Metrics) *float64 { return profitFactorFromReturns(m.DailyReturns) },
	},
	{"oos_windows", ">=", 3, true, func(m Metrics) *float64 {
		if m.OOSWindows == nil {
			return nil
		}
		v := float64(*m.OOSWindows)
		return &v
	}, nil},
	{"oos_sharpe", ">=", 1.0, true, func(m Metrics) *float64 { return m.OOSSharpe }, nil},
}

// StrictEvaluate returns one of the richer status constants above.
//
// Rules:
//   1. sharpe missing => NO_DATA (no meaningful information).
//   2. Any required metric missing => FAIL (cannot certify without evidence).
//   3. profit_factor missing but DailyReturns present => compute fallback PF.
//   4. OOS windows must be present and >= 3.
//   5. Status mapping when all evaluated rules pass:
//        - all in-sample + OOS pass => CV_PASS
//        - in-sample pass but OOS missing/failing => PROFITABLE (continue research)
//        - in-sample mixed => HOLD
func StrictEvaluate(m Metrics) (status string, detail []RuleResult) {
	detail = make([]RuleResult, 0, len(StrictRules))
	failed := false
	missingRequired := false

	for _, r := range StrictRules {
		res := RuleResult{Rule: r.name, Op: r.op, Threshold: r.threshold}
		actual := r.actual(m)
		if actual == nil && r.fallback != nil {
			actual = r.fallback(m)
			if actual != nil {
				res.Note = "fallback: computed from daily returns"
			}
		}
		if actual == nil {
			res.Note = "missing"
			if r.required {
				missingRequired = true
			}
		} else {
			res.Actual = actual
			res.Pass = compare(r.op, *actual, r.threshold)
			if !res.Pass {
				failed = true
			}
		}
		detail = append(detail, res)
	}

	// Rule 1: sharpe is the absolute minimum for any judgement.
	if m.Sharpe == nil {
		return StatusNoData, detail
	}

	if failed || missingRequired {
		return StatusFail, detail
	}

	// At this point all evaluated rules pass.
	isOOSPass := m.OOSSharpe != nil && m.OOSWindows != nil && *m.OOSWindows >= 3 && *m.OOSSharpe >= 1.0
	if isOOSPass {
		return StatusCVPass, detail
	}

	// In-sample gates passed (sharpe, ann_return, drawdown, PF).
	return StatusProfitable, detail
}
