package gate

import (
	"encoding/json"
	"testing"
)

func f64(v float64) *float64 { return &v }
func i32(v int32) *int32     { return &v }

// ruleByName finds one detail entry by rule name.
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

func TestEvaluateTypicalPass(t *testing.T) {
	status, detail := Evaluate(Metrics{
		Sharpe:       f64(1.8),
		AnnReturn:    f64(0.42),
		MaxDrawdown:  f64(-0.12),
		ProfitFactor: f64(1.9),
		OOSSharpe:    f64(1.3),
		OOSWindows:   i32(4),
	})
	if status != StatusPass {
		t.Fatalf("status = %q, want pass (detail %+v)", status, detail)
	}
	if len(detail) != len(DefaultRules) {
		t.Fatalf("detail has %d entries, want %d", len(detail), len(DefaultRules))
	}
	for _, r := range detail {
		if !r.Pass {
			t.Errorf("rule %s unexpectedly failed: %+v", r.Rule, r)
		}
		if r.Note != "" {
			t.Errorf("rule %s unexpectedly noted: %q", r.Rule, r.Note)
		}
		if r.Actual == nil {
			t.Errorf("rule %s actual nil despite data present", r.Rule)
		}
	}
}

func TestEvaluateOverfitFailsOnOOS(t *testing.T) {
	// The canonical trap: stellar in-sample numbers, garbage out-of-sample.
	// In-sample rules all pass; the OOS guards must fail loudly.
	status, detail := Evaluate(Metrics{
		Sharpe:       f64(5.72),
		AnnReturn:    f64(2.40),
		MaxDrawdown:  f64(0.05),
		ProfitFactor: f64(4.1),
		OOSSharpe:    f64(0.61),
		OOSWindows:   i32(2),
	})
	if status != StatusFail {
		t.Fatalf("status = %q, want fail", status)
	}
	for _, name := range []string{"sharpe", "ann_return", "max_drawdown", "profit_factor"} {
		if r := ruleByName(t, detail, name); !r.Pass {
			t.Errorf("in-sample rule %s should pass: %+v", name, r)
		}
	}
	if r := ruleByName(t, detail, "oos_sharpe"); r.Pass || r.Actual == nil || *r.Actual != 0.61 {
		t.Errorf("oos_sharpe should fail with actual 0.61: %+v", r)
	}
	if r := ruleByName(t, detail, "oos_windows"); r.Pass || r.Actual == nil || *r.Actual != 2 {
		t.Errorf("oos_windows should fail with actual 2: %+v", r)
	}
}

func TestEvaluateMissingOOSDataNowFails(t *testing.T) {
	// No OOS data at all: OOS rules are missing-required — visible with
	// actual=null, pass=false, note "missing required metric" — and they
	// FAIL the gate even though every in-sample rule passes.
	status, detail := Evaluate(Metrics{
		Sharpe:       f64(1.5),
		AnnReturn:    f64(0.30),
		MaxDrawdown:  f64(0.10),
		ProfitFactor: f64(2.0),
	})
	if status != StatusFail {
		t.Fatalf("status = %q, want fail", status)
	}
	for _, name := range []string{"oos_windows", "oos_sharpe"} {
		r := ruleByName(t, detail, name)
		if r.Actual != nil || r.Pass || r.Note != missingNote {
			t.Errorf("missing required OOS rule malformed: %+v", r)
		}
	}
	// Partial OOS data: present-but-failing rule fails on value; absent
	// rule fails as missing-required.
	status, detail = Evaluate(Metrics{
		Sharpe:       f64(1.5),
		AnnReturn:    f64(0.30),
		MaxDrawdown:  f64(0.10),
		ProfitFactor: f64(2.0),
		OOSSharpe:    f64(0.4), // present and below the >= 1.0 bar
	})
	if status != StatusFail {
		t.Fatalf("partial OOS: status = %q, want fail", status)
	}
	if r := ruleByName(t, detail, "oos_sharpe"); r.Pass || r.Note != "" {
		t.Errorf("oos_sharpe present-but-failing must fail on value: %+v", r)
	}
	if r := ruleByName(t, detail, "oos_windows"); r.Pass || r.Note != missingNote {
		t.Errorf("oos_windows absent must fail as missing required: %+v", r)
	}
}

func TestEvaluateMissingSharpeIsNoData(t *testing.T) {
	// Everything but sharpe present and passing → no-data: without sharpe
	// there is no basis to judge. The sharpe rule itself is missing-required.
	status, detail := Evaluate(Metrics{
		AnnReturn:    f64(0.30),
		MaxDrawdown:  f64(0.10),
		ProfitFactor: f64(2.0),
		OOSSharpe:    f64(1.2),
		OOSWindows:   i32(4),
	})
	if status != StatusNoData {
		t.Fatalf("status = %q, want no-data", status)
	}
	r := ruleByName(t, detail, "sharpe")
	if r.Actual != nil || r.Pass || r.Note != missingNote {
		t.Errorf("missing sharpe should be missing-required: %+v", r)
	}

	// No usable metrics at all → no-data as well (every rule missing,
	// sharpe missing dominates).
	if status, _ := Evaluate(Metrics{}); status != StatusNoData {
		t.Fatalf("empty metrics: status = %q, want no-data", status)
	}
}

func TestEvaluateDrawdownMagnitudeSignConvention(t *testing.T) {
	base := func(v float64) Metrics {
		return Metrics{
			Sharpe: f64(1.5), AnnReturn: f64(0.30), MaxDrawdown: f64(v),
			ProfitFactor: f64(2.0), OOSSharpe: f64(1.2), OOSWindows: i32(4),
		}
	}
	// 0.20 and -0.20 are the same 20% drawdown: both pass the < 0.25 bar.
	for _, v := range []float64{0.20, -0.20} {
		status, detail := Evaluate(base(v))
		if status != StatusPass {
			t.Fatalf("mdd %v: status = %q, want pass", v, status)
		}
		r := ruleByName(t, detail, "max_drawdown")
		if r.Actual == nil || *r.Actual != 0.20 {
			t.Errorf("mdd %v: detail actual should be magnitude 0.20: %+v", v, r)
		}
	}
	// 0.30 and -0.30 both fail.
	for _, v := range []float64{0.30, -0.30} {
		if status, _ := Evaluate(base(v)); status != StatusFail {
			t.Fatalf("mdd %v: status = %q, want fail", v, status)
		}
	}
}

func TestEvaluateBoundaryValues(t *testing.T) {
	passing := func() Metrics {
		return Metrics{
			Sharpe: f64(1.5), AnnReturn: f64(0.30), MaxDrawdown: f64(0.10),
			ProfitFactor: f64(2.0), OOSSharpe: f64(1.2), OOSWindows: i32(4),
		}
	}
	// Sharpe exactly at the >= 1.0 bar passes.
	m := passing()
	m.Sharpe = f64(1.0)
	status, detail := Evaluate(m)
	if status != StatusPass {
		t.Fatalf("sharpe 1.0: status = %q, want pass", status)
	}
	if r := ruleByName(t, detail, "sharpe"); !r.Pass {
		t.Errorf("sharpe 1.0 must pass >= 1.0: %+v", r)
	}
	// Just below fails.
	m = passing()
	m.Sharpe = f64(0.9999)
	if status, _ := Evaluate(m); status != StatusFail {
		t.Fatalf("sharpe 0.9999: status = %q, want fail", status)
	}
	// profit_factor is strictly greater: exactly 1.5 fails.
	m = passing()
	m.ProfitFactor = f64(1.5)
	if status, _ := Evaluate(m); status != StatusFail {
		t.Fatalf("pf 1.5: status = %q, want fail (> 1.5 is strict)", status)
	}
	// max_drawdown exactly 0.25 fails (< 0.25 is strict).
	m = passing()
	m.MaxDrawdown = f64(0.25)
	if status, _ := Evaluate(m); status != StatusFail {
		t.Fatalf("mdd 0.25: status = %q, want fail (< 0.25 is strict)", status)
	}
	// ann_return exactly 0.15 passes; oos_windows exactly 3 passes;
	// oos_sharpe exactly 1.0 passes.
	m = passing()
	m.AnnReturn = f64(0.15)
	m.OOSWindows = i32(3)
	m.OOSSharpe = f64(1.0)
	if status, _ := Evaluate(m); status != StatusPass {
		t.Fatalf("boundary ann_return/oos: status = %q, want pass", status)
	}
}

func TestEvaluateAnyFailureDominates(t *testing.T) {
	// One failing rule flips the whole gate even when everything else passes.
	status, _ := Evaluate(Metrics{
		Sharpe:       f64(2.0),
		AnnReturn:    f64(0.05), // below the 0.15 bar
		MaxDrawdown:  f64(0.05),
		ProfitFactor: f64(3.0),
		OOSSharpe:    f64(2.0),
		OOSWindows:   i32(10),
	})
	if status != StatusFail {
		t.Fatalf("status = %q, want fail", status)
	}
}

func TestRuleResultJSONShape(t *testing.T) {
	// The frontend builds against this shape — pin the wire format.
	_, detail := Evaluate(Metrics{Sharpe: f64(1.2)})
	raw, err := json.Marshal(ruleByName(t, detail, "sharpe"))
	if err != nil {
		t.Fatal(err)
	}
	var obj map[string]any
	if err := json.Unmarshal(raw, &obj); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"rule", "op", "threshold", "actual", "pass"} {
		if _, ok := obj[key]; !ok {
			t.Errorf("detail entry missing key %q: %s", key, raw)
		}
	}
	if _, ok := obj["note"]; ok {
		t.Errorf("evaluated rule should omit note: %s", raw)
	}

	// Missing-required rule: explicit null actual, pass=false, note present.
	raw, _ = json.Marshal(ruleByName(t, detail, "oos_sharpe"))
	if err := json.Unmarshal(raw, &obj); err != nil {
		t.Fatal(err)
	}
	if v, ok := obj["actual"]; !ok || v != nil {
		t.Errorf("missing required rule actual should be explicit null: %s", raw)
	}
	if obj["pass"] != false {
		t.Errorf("missing required rule pass should be false: %s", raw)
	}
	if obj["note"] != missingNote {
		t.Errorf("missing required rule note: %s", raw)
	}
}

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

func TestEvaluateOOSWindowsAtLeastThree(t *testing.T) {
	status, detail := Evaluate(Metrics{
		Sharpe:      f64(1.5),
		AnnReturn:   f64(0.30),
		MaxDrawdown: f64(0.10),
		ProfitFactor: f64(2.0),
		OOSSharpe:   f64(1.2),
		OOSWindows:  i32(2),
	})
	if status != StatusFail {
		t.Fatalf("status = %q, want fail (<3 OOS windows)", status)
	}
	if r := ruleByName(t, detail, "oos_windows"); r.Pass || r.Actual == nil || *r.Actual != 2 {
		t.Errorf("oos_windows should fail with actual 2: %+v", r)
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