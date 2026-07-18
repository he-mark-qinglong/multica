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

func TestEvaluateMissingOOSDataSkippedVisible(t *testing.T) {
	// No OOS data at all: OOS rules are skipped but must stay visible —
	// actual=null, pass=true, note "skipped: no data" — and must not fail
	// the gate.
	status, detail := Evaluate(Metrics{
		Sharpe:       f64(1.5),
		AnnReturn:    f64(0.30),
		MaxDrawdown:  f64(0.10),
		ProfitFactor: f64(2.0),
	})
	if status != StatusPass {
		t.Fatalf("status = %q, want pass", status)
	}
	for _, name := range []string{"oos_windows", "oos_sharpe"} {
		r := ruleByName(t, detail, name)
		if r.Actual != nil || !r.Pass || r.Note != "skipped: no data" {
			t.Errorf("skipped OOS rule malformed: %+v", r)
		}
	}
	// Partial OOS data: only the present rule is evaluated.
	_, detail = Evaluate(Metrics{
		Sharpe:       f64(1.5),
		AnnReturn:    f64(0.30),
		MaxDrawdown:  f64(0.10),
		ProfitFactor: f64(2.0),
		OOSSharpe:    f64(0.4), // present and failing
	})
	if r := ruleByName(t, detail, "oos_sharpe"); r.Pass || r.Note != "" {
		t.Errorf("oos_sharpe present-but-failing must fail, not skip: %+v", r)
	}
	if r := ruleByName(t, detail, "oos_windows"); !r.Pass || r.Note == "" {
		t.Errorf("oos_windows absent must skip: %+v", r)
	}
}

func TestEvaluateMissingSharpeStatusNull(t *testing.T) {
	// Everything but sharpe present and passing → not a pass (sharpe is the
	// mandatory metric), not a fail (nothing evaluated failed) → null.
	status, detail := Evaluate(Metrics{
		AnnReturn:    f64(0.30),
		MaxDrawdown:  f64(0.10),
		ProfitFactor: f64(2.0),
	})
	if status != "" {
		t.Fatalf("status = %q, want \"\" (null)", status)
	}
	r := ruleByName(t, detail, "sharpe")
	if r.Actual != nil || !r.Pass || r.Note != "skipped: no data" {
		t.Errorf("missing sharpe should be a visible skip: %+v", r)
	}

	// No usable metrics at all → null as well.
	status, _ = Evaluate(Metrics{})
	if status != "" {
		t.Fatalf("empty metrics: status = %q, want \"\" (null)", status)
	}
}

func TestEvaluateDrawdownMagnitudeSignConvention(t *testing.T) {
	// 0.20 and -0.20 are the same 20% drawdown: both pass the < 0.25 bar.
	for _, v := range []float64{0.20, -0.20} {
		status, detail := Evaluate(Metrics{Sharpe: f64(1.5), MaxDrawdown: f64(v)})
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
		status, _ := Evaluate(Metrics{Sharpe: f64(1.5), MaxDrawdown: f64(v)})
		if status != StatusFail {
			t.Fatalf("mdd %v: status = %q, want fail", v, status)
		}
	}
}

func TestEvaluateBoundaryValues(t *testing.T) {
	// Sharpe exactly at the >= 1.0 bar passes.
	status, detail := Evaluate(Metrics{Sharpe: f64(1.0)})
	if status != StatusPass {
		t.Fatalf("sharpe 1.0: status = %q, want pass", status)
	}
	if r := ruleByName(t, detail, "sharpe"); !r.Pass {
		t.Errorf("sharpe 1.0 must pass >= 1.0: %+v", r)
	}
	// Just below fails.
	if status, _ := Evaluate(Metrics{Sharpe: f64(0.9999)}); status != StatusFail {
		t.Fatalf("sharpe 0.9999: status = %q, want fail", status)
	}
	// profit_factor is strictly greater: exactly 1.5 fails.
	if status, _ := Evaluate(Metrics{Sharpe: f64(1.5), ProfitFactor: f64(1.5)}); status != StatusFail {
		t.Fatalf("pf 1.5: status = %q, want fail (> 1.5 is strict)", status)
	}
	// max_drawdown exactly 0.25 fails (< 0.25 is strict).
	if status, _ := Evaluate(Metrics{Sharpe: f64(1.5), MaxDrawdown: f64(0.25)}); status != StatusFail {
		t.Fatalf("mdd 0.25: status = %q, want fail (< 0.25 is strict)", status)
	}
	// ann_return exactly 0.15 passes; oos_windows exactly 3 passes.
	status, _ = Evaluate(Metrics{
		Sharpe: f64(1.5), AnnReturn: f64(0.15),
		OOSWindows: i32(3), OOSSharpe: f64(1.0),
	})
	if status != StatusPass {
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

	// Skipped rule: actual renders null, note present.
	raw, _ = json.Marshal(ruleByName(t, detail, "oos_sharpe"))
	if err := json.Unmarshal(raw, &obj); err != nil {
		t.Fatal(err)
	}
	if v, ok := obj["actual"]; !ok || v != nil {
		t.Errorf("skipped rule actual should be explicit null: %s", raw)
	}
	if obj["note"] != "skipped: no data" {
		t.Errorf("skipped rule note: %s", raw)
	}
}
