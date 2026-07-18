package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/spf13/cobra"
)

const previewAgentID = "aaaaaaaa-1111-2222-3333-444444444444"

// newIssueCreatePreviewCmd builds a throwaway command carrying the flag set
// runIssueCreate reads, pre-set for a --preview run against the test server.
func newIssueCreatePreviewCmd(t *testing.T, output string) *cobra.Command {
	t.Helper()
	cmd := &cobra.Command{Use: "create"}
	cmd.Flags().String("title", "", "")
	cmd.Flags().String("description", "", "")
	cmd.Flags().Bool("description-stdin", false, "")
	cmd.Flags().String("description-file", "", "")
	cmd.Flags().String("status", "", "")
	cmd.Flags().String("priority", "", "")
	cmd.Flags().String("parent", "", "")
	cmd.Flags().String("project", "", "")
	cmd.Flags().String("due-date", "", "")
	cmd.Flags().String("assignee", "", "")
	cmd.Flags().String("assignee-id", "", "")
	cmd.Flags().String("output", "json", "")
	cmd.Flags().StringSlice("attachment", nil, "")
	cmd.Flags().Bool("preview", false, "")
	_ = cmd.Flags().Set("title", "preview me")
	_ = cmd.Flags().Set("assignee-id", previewAgentID)
	_ = cmd.Flags().Set("preview", "true")
	_ = cmd.Flags().Set("output", output)
	return cmd
}

func TestRunIssueCreatePreview(t *testing.T) {
	var createHits, previewHits int32

	previewResp := map[string]any{
		"status":         "todo",
		"priority":       "none",
		"assignee_type":  "agent",
		"assignee_id":    previewAgentID,
		"would_dispatch": true,
		"agent": map[string]any{
			"id":                   previewAgentID,
			"name":                 "CodeBot",
			"model":                "claude-opus-4.1",
			"instructions_set":     true,
			"max_concurrent_tasks": 2,
			"runtime":              map[string]any{"id": "rt-1", "provider": "claude", "status": "online", "online": true},
		},
		"skills": []map[string]any{{"id": "s1", "name": "go-review"}, {"id": "s2", "name": "testing"}},
		"queue":  map[string]any{"queued": 2, "running": 1, "max_concurrent_tasks": 2},
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/issues" && r.Method == http.MethodPost:
			atomic.AddInt32(&createHits, 1)
			http.Error(w, "create must not be called in preview mode", http.StatusInternalServerError)
		case r.URL.Path == "/api/issues/preview-dispatch" && r.Method == http.MethodPost:
			atomic.AddInt32(&previewHits, 1)
			json.NewEncoder(w).Encode(previewResp)
		case r.URL.Path == "/api/workspaces/ws-1/members":
			json.NewEncoder(w).Encode([]map[string]any{})
		case r.URL.Path == "/api/agents":
			json.NewEncoder(w).Encode([]map[string]any{{"id": previewAgentID, "name": "CodeBot"}})
		case r.URL.Path == "/api/squads":
			json.NewEncoder(w).Encode([]map[string]any{})
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	t.Setenv("MULTICA_SERVER_URL", srv.URL)
	t.Setenv("MULTICA_WORKSPACE_ID", "ws-1")
	t.Setenv("MULTICA_TOKEN", "test-token")

	t.Run("human summary", func(t *testing.T) {
		out, err := captureStdout(t, func() error {
			return runIssueCreate(newIssueCreatePreviewCmd(t, "table"), nil)
		})
		if err != nil {
			t.Fatalf("runIssueCreate --preview: %v", err)
		}
		for _, want := range []string{
			"CodeBot", "claude (online)", "claude-opus-4.1",
			"go-review, testing", "Would dispatch: yes",
			"2 queued / 1 running (max 2)",
		} {
			if !strings.Contains(out, want) {
				t.Errorf("preview output missing %q:\n%s", want, out)
			}
		}
	})

	t.Run("json output", func(t *testing.T) {
		out, err := captureStdout(t, func() error {
			return runIssueCreate(newIssueCreatePreviewCmd(t, "json"), nil)
		})
		if err != nil {
			t.Fatalf("runIssueCreate --preview --output json: %v", err)
		}
		if !strings.Contains(out, `"would_dispatch": true`) {
			t.Errorf("json output missing verdict:\n%s", out)
		}
	})

	if got := atomic.LoadInt32(&createHits); got != 0 {
		t.Errorf("POST /api/issues was called %d times in preview mode, want 0", got)
	}
	if got := atomic.LoadInt32(&previewHits); got != 2 {
		t.Errorf("preview endpoint called %d times, want 2", got)
	}
}

func TestRunIssueCreatePreview_ServerValidationError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/issues/preview-dispatch" && r.Method == http.MethodPost:
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "assignee_id does not refer to an agent of this workspace"})
		case r.URL.Path == "/api/workspaces/ws-1/members":
			json.NewEncoder(w).Encode([]map[string]any{})
		case r.URL.Path == "/api/agents":
			json.NewEncoder(w).Encode([]map[string]any{{"id": previewAgentID, "name": "CodeBot"}})
		case r.URL.Path == "/api/squads":
			json.NewEncoder(w).Encode([]map[string]any{})
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	t.Setenv("MULTICA_SERVER_URL", srv.URL)
	t.Setenv("MULTICA_WORKSPACE_ID", "ws-1")
	t.Setenv("MULTICA_TOKEN", "test-token")

	err := runIssueCreate(newIssueCreatePreviewCmd(t, "table"), nil)
	if err == nil || !strings.Contains(err.Error(), "does not refer to an agent") {
		t.Fatalf("expected server validation error to surface, got %v", err)
	}
}
