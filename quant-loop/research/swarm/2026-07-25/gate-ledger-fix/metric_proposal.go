package handler

// PROPOSAL (gate-ledger-fix, 2026-07-25):
//
// This file shows the minimal delta against server/internal/handler/metric.go
// needed to support the strict gate rules in gate_proposal.go.
//
// The key idea: profit_factor is a REQUIRED gate metric. If the uploaded
// metrics blob does not contain it, we attempt to compute it from a companion
// daily return / equity curve array in the same blob. If neither is present,
// the gate layer will see a nil ProfitFactor and fail loudly (no silent skip).

import (
	"math"
)

// computeProfitFactorFromDailyReturns returns gross-profit / |gross-loss| for
// a slice of daily returns. Empty or single-value slices return ok=false.
// This mirrors the "daily-return PF" proxy requested in gate-ledger-fix.
func computeProfitFactorFromDailyReturns(returns []float64) (pf float64, ok bool) {
	if len(returns) < 2 {
		return 0, false
	}
	var grossProfit, grossLoss float64
	for _, r := range returns {
		if r > 0 {
			grossProfit += r
		} else if r < 0 {
			grossLoss += -r
		}
	}
	if grossLoss == 0 {
		if grossProfit > 0 {
			return math.Inf(1), true
		}
		return 0, false
	}
	return grossProfit / grossLoss, true
}

// equityCurveToReturns converts an equity curve (cumulative NAV) into simple
// period returns. Requires at least two points.
func equityCurveToReturns(curve []float64) (returns []float64, ok bool) {
	if len(curve) < 2 {
		return nil, false
	}
	returns = make([]float64, 0, len(curve)-1)
	for i := 1; i < len(curve); i++ {
		if curve[i-1] == 0 {
			return nil, false
		}
		returns = append(returns, (curve[i]-curve[i-1])/curve[i-1])
	}
	return returns, true
}

// extractDailyReturns looks for known blob keys that carry a daily return or
// equity-curve array and coerces them to []float64. This lets a metrics upload
// satisfy the strict profit_factor gate even when the agent did not emit a
// pre-computed PF.
func extractDailyReturns(obj map[string]any) ([]float64, bool) {
	for _, key := range []string{"daily_returns", "daily_pnl", "equity_curve", "nav_curve"} {
		v, present := obj[key]
		if !present {
			continue
		}
		switch vv := v.(type) {
		case []any:
			out := make([]float64, 0, len(vv))
			for _, item := range vv {
				if f, ok := item.(float64); ok {
					out = append(out, f)
				} else {
					// mixed types: bail out, do not silently accept partial data
					return nil, false
				}
			}
			if key == "equity_curve" || key == "nav_curve" {
				if rets, ok := equityCurveToReturns(out); ok {
					return rets, true
				}
				return nil, false
			}
			if len(out) > 0 {
				return out, true
			}
		case []float64:
			if key == "equity_curve" || key == "nav_curve" {
				if rets, ok := equityCurveToReturns(vv); ok {
					return rets, true
				}
				return nil, false
			}
			if len(vv) > 0 {
				return vv, true
			}
		}
	}
	return nil, false
}

// parseRunMetricJSON patch: after parsing the float columns, if profit_factor
// is still nil, try to derive it from daily returns / equity curve in the blob.
// Add this block immediately after the floatCols loop and before the
// oos_windows handling.
//
//	if f.ProfitFactor == nil {
//		if rets, ok := extractDailyReturns(obj); ok {
//			if pf, ok := computeProfitFactorFromDailyReturns(rets); ok {
//				f.ProfitFactor = &pf
//			}
//		}
//	}
//
// No other changes to metric.go are required. The gate re-evaluate endpoint
// (ReevaluateRunMetrics) will automatically backfill stored rows using the
// updated gate.Evaluate after POST /api/metrics/reevaluate is called.
