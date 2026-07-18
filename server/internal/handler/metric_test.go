package handler

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// Pure-mapping tests: parseRunMetricJSON and campaignIterationFromMeta never
// touch the DB.

func TestParseRunMetricJSONCanonical(t *testing.T) {
	f, ok := parseRunMetricJSON([]byte(`{
		"sharpe": 1.5, "sortino": 2.1, "calmar": 0.9,
		"ann_return": 0.42, "max_drawdown": -0.12, "profit_factor": 1.8,
		"oos_sharpe": 1.1, "oos_windows": 4,
		"timeframe": "1h", "symbols": ["BTCUSDT", "ETHUSDT"],
		"params": {"fast": 12, "slow": 48}
	}`))
	if !ok {
		t.Fatal("canonical blob rejected")
	}
	if f.Sharpe == nil || *f.Sharpe != 1.5 {
		t.Errorf("sharpe: got %v", f.Sharpe)
	}
	if f.Sortino == nil || *f.Sortino != 2.1 {
		t.Errorf("sortino: got %v", f.Sortino)
	}
	if f.Calmar == nil || *f.Calmar != 0.9 {
		t.Errorf("calmar: got %v", f.Calmar)
	}
	if f.AnnReturn == nil || *f.AnnReturn != 0.42 {
		t.Errorf("ann_return: got %v", f.AnnReturn)
	}
	if f.MaxDrawdown == nil || *f.MaxDrawdown != -0.12 {
		t.Errorf("max_drawdown: got %v", f.MaxDrawdown)
	}
	if f.ProfitFactor == nil || *f.ProfitFactor != 1.8 {
		t.Errorf("profit_factor: got %v", f.ProfitFactor)
	}
	if f.OOSSharpe == nil || *f.OOSSharpe != 1.1 {
		t.Errorf("oos_sharpe: got %v", f.OOSSharpe)
	}
	if f.OOSWindows == nil || *f.OOSWindows != 4 {
		t.Errorf("oos_windows: got %v", f.OOSWindows)
	}
	if f.Timeframe != "1h" {
		t.Errorf("timeframe: got %q", f.Timeframe)
	}
	if len(f.Symbols) != 2 || f.Symbols[0] != "BTCUSDT" || f.Symbols[1] != "ETHUSDT" {
		t.Errorf("symbols: got %v", f.Symbols)
	}
	var params map[string]any
	if err := json.Unmarshal(f.Params, &params); err != nil || params["fast"] != float64(12) {
		t.Errorf("params: got %s (err %v)", f.Params, err)
	}
	// No unknown keys → empty object, not nil.
	if string(f.Extra) != "{}" {
		t.Errorf("extra: got %s, want {}", f.Extra)
	}
}

func TestParseRunMetricJSONAliases(t *testing.T) {
	f, ok := parseRunMetricJSON([]byte(`{
		"sharpe_ratio": 1.2, "ann": 0.3, "mdd": 0.15, "pf": 2.0,
		"oos": 0.8, "tf": "4h", "symbol": "SOLUSDT"
	}`))
	if !ok {
		t.Fatal("alias blob rejected")
	}
	if f.Sharpe == nil || *f.Sharpe != 1.2 {
		t.Errorf("sharpe_ratio alias: got %v", f.Sharpe)
	}
	if f.AnnReturn == nil || *f.AnnReturn != 0.3 {
		t.Errorf("ann alias: got %v", f.AnnReturn)
	}
	if f.MaxDrawdown == nil || *f.MaxDrawdown != 0.15 {
		t.Errorf("mdd alias: got %v", f.MaxDrawdown)
	}
	if f.ProfitFactor == nil || *f.ProfitFactor != 2.0 {
		t.Errorf("pf alias: got %v", f.ProfitFactor)
	}
	if f.OOSSharpe == nil || *f.OOSSharpe != 0.8 {
		t.Errorf("oos alias: got %v", f.OOSSharpe)
	}
	if f.Timeframe != "4h" {
		t.Errorf("tf alias: got %q", f.Timeframe)
	}
	if len(f.Symbols) != 1 || f.Symbols[0] != "SOLUSDT" {
		t.Errorf("symbol alias: got %v", f.Symbols)
	}
}

func TestParseRunMetricJSONWrongTypesIgnored(t *testing.T) {
	// Exact on types: quoted numbers and mistyped values stay NULL, and the
	// rest of the row still parses.
	f, ok := parseRunMetricJSON([]byte(`{
		"sharpe": "1.5", "sortino": "high", "oos_windows": "four",
		"timeframe": 60, "symbols": "BTCUSDT", "calmar": 0.7
	}`))
	if !ok {
		t.Fatal("blob with wrong-typed values rejected outright")
	}
	if f.Sharpe != nil {
		t.Errorf("string sharpe mapped: %v", *f.Sharpe)
	}
	if f.Sortino != nil {
		t.Errorf("string sortino mapped: %v", *f.Sortino)
	}
	if f.OOSWindows != nil {
		t.Errorf("string oos_windows mapped: %v", *f.OOSWindows)
	}
	if f.Timeframe != "" {
		t.Errorf("numeric timeframe mapped: %q", f.Timeframe)
	}
	if f.Symbols != nil {
		t.Errorf("string (not array) symbols mapped: %v", f.Symbols)
	}
	if f.Calmar == nil || *f.Calmar != 0.7 {
		t.Errorf("valid calmar lost: %v", f.Calmar)
	}
}

func TestParseRunMetricJSONUnknownKeysToExtra(t *testing.T) {
	f, ok := parseRunMetricJSON([]byte(`{"sharpe": 1.0, "win_rate": 0.55, "notes": "good"}`))
	if !ok {
		t.Fatal("blob rejected")
	}
	var extra map[string]any
	if err := json.Unmarshal(f.Extra, &extra); err != nil {
		t.Fatalf("extra not an object: %v", err)
	}
	if extra["win_rate"] != 0.55 || extra["notes"] != "good" {
		t.Errorf("unknown keys missing from extra: %s", f.Extra)
	}
	if _, leaked := extra["sharpe"]; leaked {
		t.Errorf("known key leaked into extra: %s", f.Extra)
	}
}

func TestParseRunMetricJSONNonObjectSkipped(t *testing.T) {
	for _, bad := range []string{`[1,2]`, `"str"`, `42`, `{invalid`, ``, `null`} {
		if f, ok := parseRunMetricJSON([]byte(bad)); ok || f != nil {
			t.Errorf("parseRunMetricJSON(%q): expected skip, got ok=%v", bad, ok)
		}
	}
}

func TestCampaignIterationFromMeta(t *testing.T) {
	cases := []struct {
		meta                  string
		wantCampaign, wantItr string
	}{
		{`{"campaign":"c7","iteration":"iter#83"}`, "c7", "iter#83"},
		{`{"campaign":"c7","iteration":83}`, "c7", "83"},
		{`{"campaign":"c7"}`, "c7", ""},
		{`{"iteration":"3"}`, "", "3"},
		{`{}`, "", ""},
		{`{invalid`, "", ""},
	}
	for _, c := range cases {
		gotC, gotI := campaignIterationFromMeta([]byte(c.meta))
		if gotC != c.wantCampaign || gotI != c.wantItr {
			t.Errorf("campaignIterationFromMeta(%s) = (%q, %q), want (%q, %q)",
				c.meta, gotC, gotI, c.wantCampaign, c.wantItr)
		}
	}
}

// DB-backed query-filter tests. The whole handler suite exits early when no
// database is reachable (see TestMain), so these run whenever the suite runs.

// insertRunMetricForTest stores an artifact + run_metric row pair in the
// shared test workspace and returns the metric id.
func insertRunMetricForTest(t *testing.T, campaign, iteration string, sharpe float64) string {
	t.Helper()
	if testPool == nil {
		t.Skip("database not available")
	}
	ctx := t.Context()
	var artifactID string
	if err := testPool.QueryRow(ctx, `
		INSERT INTO artifact (workspace_id, kind, name, storage_key, meta)
		VALUES ($1, 'metrics', 'm.json', $2, $3)
		RETURNING id
	`, testWorkspaceID, "test/metrics/"+campaign+iteration+".json",
		fmt.Sprintf(`{"campaign":%q,"iteration":%q}`, campaign, iteration)).Scan(&artifactID); err != nil {
		t.Fatalf("insert artifact: %v", err)
	}
	var metricID string
	if err := testPool.QueryRow(ctx, `
		INSERT INTO run_metric (workspace_id, artifact_id, campaign, iteration, sharpe)
		VALUES ($1, $2, $3, $4, $5)
		RETURNING id
	`, testWorkspaceID, artifactID, campaign, iteration, sharpe).Scan(&metricID); err != nil {
		t.Fatalf("insert run_metric: %v", err)
	}
	return metricID
}

func metricQueryRequest(t *testing.T, query string) *http.Request {
	t.Helper()
	req := httptest.NewRequest("GET", "/api/metrics/query?"+query, nil)
	req.Header.Set("X-User-ID", testUserID)
	req.Header.Set("X-Workspace-ID", testWorkspaceID)
	return req
}

func TestQueryRunMetricsFilters(t *testing.T) {
	if testPool == nil {
		t.Skip("database not available")
	}
	// Isolate: wipe this workspace's metric rows (artifacts cascade).
	if _, err := testPool.Exec(t.Context(),
		`DELETE FROM run_metric WHERE workspace_id = $1`, testWorkspaceID); err != nil {
		t.Fatalf("cleanup run_metric: %v", err)
	}

	insertRunMetricForTest(t, "cmp-a", "1", 1.1)
	insertRunMetricForTest(t, "cmp-a", "2", 1.2)
	insertRunMetricForTest(t, "cmp-b", "1", 2.5)

	// No filter → all three rows.
	w := httptest.NewRecorder()
	testHandler.QueryRunMetrics(w, metricQueryRequest(t, "limit=50"))
	if w.Code != http.StatusOK {
		t.Fatalf("unfiltered query: got %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		Metrics []map[string]any `json:"metrics"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(resp.Metrics) != 3 {
		t.Fatalf("unfiltered query returned %d rows, want 3", len(resp.Metrics))
	}

	// Campaign filter → only cmp-a rows.
	w = httptest.NewRecorder()
	testHandler.QueryRunMetrics(w, metricQueryRequest(t, "campaign=cmp-a"))
	if w.Code != http.StatusOK {
		t.Fatalf("campaign query: got %d: %s", w.Code, w.Body.String())
	}
	resp.Metrics = nil
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(resp.Metrics) != 2 {
		t.Fatalf("campaign=cmp-a returned %d rows, want 2", len(resp.Metrics))
	}
	for _, m := range resp.Metrics {
		if m["campaign"] != "cmp-a" {
			t.Fatalf("campaign filter leaked row: %v", m["campaign"])
		}
	}

	// Unknown issue_id → empty list, not an error.
	w = httptest.NewRecorder()
	testHandler.QueryRunMetrics(w, metricQueryRequest(t, "issue_id=00000000-0000-0000-0000-0000000000aa"))
	if w.Code != http.StatusOK {
		t.Fatalf("issue filter: got %d: %s", w.Code, w.Body.String())
	}
	resp.Metrics = nil
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(resp.Metrics) != 0 {
		t.Fatalf("unknown issue_id returned %d rows, want 0", len(resp.Metrics))
	}

	// Limit applies.
	w = httptest.NewRecorder()
	testHandler.QueryRunMetrics(w, metricQueryRequest(t, "limit=1"))
	resp.Metrics = nil
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(resp.Metrics) != 1 {
		t.Fatalf("limit=1 returned %d rows, want 1", len(resp.Metrics))
	}
}

func TestListMetricCampaigns(t *testing.T) {
	if testPool == nil {
		t.Skip("database not available")
	}
	if _, err := testPool.Exec(t.Context(),
		`DELETE FROM run_metric WHERE workspace_id = $1`, testWorkspaceID); err != nil {
		t.Fatalf("cleanup run_metric: %v", err)
	}
	insertRunMetricForTest(t, "cmp-x", "1", 1.0)
	insertRunMetricForTest(t, "cmp-x", "2", 1.1)
	insertRunMetricForTest(t, "cmp-y", "1", 2.0)

	req := httptest.NewRequest("GET", "/api/metrics/campaigns", nil)
	req.Header.Set("X-User-ID", testUserID)
	req.Header.Set("X-Workspace-ID", testWorkspaceID)
	w := httptest.NewRecorder()
	testHandler.ListMetricCampaigns(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("campaigns: got %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		Campaigns []string `json:"campaigns"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(resp.Campaigns) != 2 || resp.Campaigns[0] != "cmp-x" || resp.Campaigns[1] != "cmp-y" {
		t.Fatalf("campaigns: got %v, want [cmp-x cmp-y]", resp.Campaigns)
	}
}

// DB-backed gate re-evaluation test. Skips when no database is reachable
// (see TestMain) or when migration 091 has not been applied to the test
// database — the gate columns are added by files-only migrations locally.
func TestReevaluateRunMetrics(t *testing.T) {
	if testPool == nil {
		t.Skip("database not available")
	}
	ctx := t.Context()
	if _, err := testPool.Exec(ctx, `SELECT gate_status FROM run_metric LIMIT 0`); err != nil {
		t.Skip("migration 091 (gate columns) not applied")
	}
	if _, err := testPool.Exec(ctx,
		`DELETE FROM run_metric WHERE workspace_id = $1`, testWorkspaceID); err != nil {
		t.Fatalf("cleanup run_metric: %v", err)
	}

	// Overfit row: passes every in-sample bar, fails both OOS guards.
	var artifactID string
	if err := testPool.QueryRow(ctx, `
		INSERT INTO artifact (workspace_id, kind, name, storage_key, meta)
		VALUES ($1, 'metrics', 'overfit.json', 'test/metrics/overfit.json', '{"campaign":"cmp-gate","iteration":"1"}')
		RETURNING id
	`, testWorkspaceID).Scan(&artifactID); err != nil {
		t.Fatalf("insert artifact: %v", err)
	}
	if _, err := testPool.Exec(ctx, `
		INSERT INTO run_metric (workspace_id, artifact_id, campaign, iteration,
			sharpe, ann_return, max_drawdown, profit_factor, oos_sharpe, oos_windows)
		VALUES ($1, $2, 'cmp-gate', '1', 5.72, 2.40, 0.05, 4.1, 0.61, 2)
	`, testWorkspaceID, artifactID); err != nil {
		t.Fatalf("insert overfit run_metric: %v", err)
	}

	body := strings.NewReader(`{"campaign":"cmp-gate"}`)
	req := httptest.NewRequest("POST", "/api/metrics/reevaluate", body)
	req.Header.Set("X-User-ID", testUserID)
	req.Header.Set("X-Workspace-ID", testWorkspaceID)
	w := httptest.NewRecorder()
	testHandler.ReevaluateRunMetrics(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("reevaluate: got %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		Reevaluated int `json:"reevaluated"`
		Fail        int `json:"fail"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.Reevaluated != 1 || resp.Fail != 1 {
		t.Fatalf("reevaluate counts: %+v, want 1 reevaluated / 1 fail", resp)
	}

	var gateStatus string
	var gateDetail []byte
	if err := testPool.QueryRow(ctx, `
		SELECT gate_status, gate_detail FROM run_metric
		WHERE workspace_id = $1 AND campaign = 'cmp-gate'
	`, testWorkspaceID).Scan(&gateStatus, &gateDetail); err != nil {
		t.Fatalf("read back gate result: %v", err)
	}
	if gateStatus != "fail" {
		t.Fatalf("gate_status = %q, want fail", gateStatus)
	}
	var detail []map[string]any
	if err := json.Unmarshal(gateDetail, &detail); err != nil {
		t.Fatalf("gate_detail not a JSON array: %v", err)
	}
	failed := map[string]bool{}
	for _, entry := range detail {
		if p, _ := entry["pass"].(bool); !p {
			failed[entry["rule"].(string)] = true
		}
	}
	if !failed["oos_sharpe"] || !failed["oos_windows"] {
		t.Fatalf("overfit row must fail on OOS rules, failed: %v (detail %s)", failed, gateDetail)
	}
	if failed["sharpe"] {
		t.Fatalf("in-sample sharpe 5.72 must pass: %s", gateDetail)
	}
}
