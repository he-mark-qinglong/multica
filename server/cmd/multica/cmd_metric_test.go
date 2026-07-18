package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/spf13/cobra"
)

// metricTestServer stubs the two endpoints the metrics commands hit.
func metricTestServer(t *testing.T, sawQuery *map[string]string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/metrics/query" && r.Method == http.MethodGet:
			if sawQuery != nil {
				*sawQuery = map[string]string{
					"campaign": r.URL.Query().Get("campaign"),
					"issue_id": r.URL.Query().Get("issue_id"),
					"task_id":  r.URL.Query().Get("task_id"),
					"limit":    r.URL.Query().Get("limit"),
				}
			}
			json.NewEncoder(w).Encode(map[string]any{
				"metrics": []map[string]any{{
					"id":       "33333333-3333-3333-3333-333333333333",
					"campaign": "c7", "iteration": "iter#83",
					"sharpe": 1.5, "ann_return": 0.42, "max_drawdown": 0.12,
					"profit_factor": 1.8, "oos_sharpe": 1.1, "timeframe": "1h",
				}},
			})
		case r.URL.Path == "/api/metrics/campaigns" && r.Method == http.MethodGet:
			json.NewEncoder(w).Encode(map[string]any{"campaigns": []string{"c7", "c8"}})
		default:
			http.NotFound(w, r)
		}
	}))
}

func freshMetricCmd(use string, strFlags map[string]string) *cobra.Command {
	c := &cobra.Command{Use: use}
	for name, def := range strFlags {
		c.Flags().String(name, def, "")
	}
	c.Flags().Int("limit", 50, "")
	c.Flags().Int("offset", 0, "")
	c.Flags().String("output", "table", "")
	return c
}

func TestMetricQueryPassesFilters(t *testing.T) {
	var saw map[string]string
	srv := metricTestServer(t, &saw)
	defer srv.Close()
	setArtifactTestEnv(t, srv.URL)

	cmd := freshMetricCmd("query", map[string]string{"campaign": "", "issue-id": "", "task-id": ""})
	cmd.Flags().Set("campaign", "c7")
	cmd.Flags().Set("issue-id", "11111111-1111-1111-1111-111111111111")
	cmd.Flags().Set("limit", "10")

	if err := runMetricQuery(cmd, nil); err != nil {
		t.Fatalf("runMetricQuery: %v", err)
	}
	if saw["campaign"] != "c7" {
		t.Fatalf("server saw campaign %q, want c7", saw["campaign"])
	}
	if saw["issue_id"] != "11111111-1111-1111-1111-111111111111" {
		t.Fatalf("server saw issue_id %q", saw["issue_id"])
	}
	if saw["limit"] != "10" {
		t.Fatalf("server saw limit %q, want 10", saw["limit"])
	}
	if saw["task_id"] != "" {
		t.Fatalf("task_id should be omitted, got %q", saw["task_id"])
	}
}

func TestMetricQueryJSONOutput(t *testing.T) {
	srv := metricTestServer(t, nil)
	defer srv.Close()
	setArtifactTestEnv(t, srv.URL)

	cmd := freshMetricCmd("query", map[string]string{"campaign": "", "issue-id": "", "task-id": ""})
	cmd.Flags().Set("output", "json")
	if err := runMetricQuery(cmd, nil); err != nil {
		t.Fatalf("runMetricQuery json: %v", err)
	}
}

func TestMetricCampaigns(t *testing.T) {
	srv := metricTestServer(t, nil)
	defer srv.Close()
	setArtifactTestEnv(t, srv.URL)

	cmd := freshMetricCmd("campaigns", nil)
	if err := runMetricCampaigns(cmd, nil); err != nil {
		t.Fatalf("runMetricCampaigns: %v", err)
	}

	cmdJSON := freshMetricCmd("campaigns", nil)
	cmdJSON.Flags().Set("output", "json")
	if err := runMetricCampaigns(cmdJSON, nil); err != nil {
		t.Fatalf("runMetricCampaigns json: %v", err)
	}
}

func TestMetricFloatFormat(t *testing.T) {
	if got := metricFloat(1.5); got != "1.50" {
		t.Fatalf("metricFloat(1.5) = %q, want 1.50", got)
	}
	if got := metricFloat(nil); got != "-" {
		t.Fatalf("metricFloat(nil) = %q, want -", got)
	}
}
