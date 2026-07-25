package gate

import (
	"encoding/json"
	"testing"
)

func f64(v float64) *float64 { return &v }
func i32(v int32) *int32     { return &v }

func ruleByName(t *testing.T, detail []RuleResult, name string) RuleResult {
	t.Helper()
	for _, r := range detail {
		if r.Rule == name {
			return r
		}
	}
	t.Fatalf("rule %q missing from detail: %+v", name, detail)
	return RuleResult{}
}

// PROPOSED ADDITIONS to server/internal/gate/gate_test.go
// (keep all existing tests except those that assert "skip on missing OOS")

func TestEvaluateMissingCoreFieldsNowFail(t *testing.T) {
	// The canonical gate-ledger-fix regression: a stellar sharpe with no
	// supporting metrics must FAIL, not pass via skipped rules.
	status, detail := Evaluate(Metrics{
		Sharpe: f64(31.7), // vpvr_stable_depeg_p3opt_091 shape
	})
	if status != StatusFail {
		t.Fatalf("status = %q, want fail", status)
	}
	for _, name := range []string{"ann_return", "max_drawdown", "profit_factor", "oos_windows", "oos_sharpe"} {
		r := ruleByName(t, detail, name)
		if r.Pass || r.Actual != nil || r.Note != missingNote {
			t.Errorf("missing required rule %s malformed: %+v", name, r)
		}
	}
}

func TestEvaluateProfitFactorRequired(t *testing.T) {
	// H3 as-uploaded has no profit_factor; strict gate fails.
	status, _ := Evaluate(Metrics{
		Sharpe:      f64(1.35),
		AnnReturn:   f64(0.25),
		MaxDrawdown: f64(-0.137),
		OOSSharpe:   f64(2.77),
		OOSWindows:  i32(7),
	})
	if status != StatusFail {
		t.Fatalf("status = %q, want fail (PF missing)", status)
	}

	// Same strategy with a computed daily-return PF=1.22 still fails PF gate.
	status, detail := Evaluate(Metrics{
		Sharpe:       f64(1.35),
		AnnReturn:    f64(0.25),
		MaxDrawdown:  f64(-0.137),
		ProfitFactor: f64(1.22),
		OOSSharpe:    f64(2.77),
		OOSWindows:   i32(7),
	})
	if status != StatusFail {
		t.Fatalf("status = %q, want fail (PF below 1.5)", status)
	}
	if r := ruleByName(t, detail, "profit_factor"); r.Pass || r.Actual == nil || *r.Actual != 1.22 {
		t.Errorf("profit_factor should fail at 1.22: %+v", r)
	}
}

func TestEvaluateMissingSharpeIsNoData(t *testing.T) {
	status, _ := Evaluate(Metrics{
		AnnReturn:    f64(0.30),
		MaxDrawdown:  f64(0.10),
		ProfitFactor: f64(2.0),
	})
	if status != StatusNoData {
		t.Fatalf("status = %q, want no-data (sharpe missing)", status)
	}
}

func TestEvaluateOOSWindowsAtLeastThree(t *testing.T) {
	status, detail := Evaluate(Metrics{
		Sharpe:       f64(1.5),
		AnnReturn:    f64(0.30),
		MaxDrawdown:  f64(0.10),
		ProfitFactor: f64(2.0),
		OOSSharpe:    f64(1.2),
		OOSWindows:   i32(2),
	})
	if status != StatusFail {
		t.Fatalf("status = %q, want fail (<3 OOS windows)", status)
	}
	if r := ruleByName(t, detail, "oos_windows"); r.Pass || r.Actual == nil || *r.Actual != 2 {
		t.Errorf("oos_windows should fail with actual 2: %+v", r)
	}
}

func TestEvaluateStrongCandidateStillPasses(t *testing.T) {
	status, detail := Evaluate(Metrics{
		Sharpe:       f64(1.8),
		AnnReturn:    f64(0.42),
		MaxDrawdown:  f64(-0.12),
		ProfitFactor: f64(1.9),
		OOSSharpe:    f64(1.3),
		OOSWindows:   i32(4),
	})
	if status != StatusPass {
		t.Fatalf("status = %q, want pass", status)
	}
	for _, r := range detail {
		if !r.Pass {
			t.Errorf("rule %s unexpectedly failed: %+v", r.Rule, r)
		}
	}
}

func TestRuleResultJSONShapeForMissingRequired(t *testing.T) {
	_, detail := Evaluate(Metrics{Sharpe: f64(1.2)})
	raw, _ := json.Marshal(ruleByName(t, detail, "ann_return"))
	var obj map[string]any
	if err := json.Unmarshal(raw, &obj); err != nil {
		t.Fatal(err)
	}
	if v, ok := obj["actual"]; !ok || v != nil {
		t.Errorf("missing required rule actual should be null: %s", raw)
	}
	if obj["pass"] != false {
		t.Errorf("missing required rule pass should be false: %s", raw)
	}
	if obj["note"] != missingNote {
		t.Errorf("missing required rule note wrong: %s", raw)
	}
}
