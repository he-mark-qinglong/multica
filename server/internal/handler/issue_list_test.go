package handler

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestParseStatusFilter(t *testing.T) {
	t.Run("empty input means no filter", func(t *testing.T) {
		got, err := parseStatusFilter("", validIssueStatuses)
		if err != nil || got != nil {
			t.Fatalf("got (%v, %v), want (nil, nil)", got, err)
		}
	})

	t.Run("single value", func(t *testing.T) {
		got, err := parseStatusFilter("todo", validIssueStatuses)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(got) != 1 || got[0] != "todo" {
			t.Fatalf("got %v, want [todo]", got)
		}
	})

	t.Run("comma list with whitespace and duplicates", func(t *testing.T) {
		got, err := parseStatusFilter("todo, in_progress ,todo", validIssueStatuses)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(got) != 2 || got[0] != "todo" || got[1] != "in_progress" {
			t.Fatalf("got %v, want [todo in_progress]", got)
		}
	})

	t.Run("invalid value lists valid statuses", func(t *testing.T) {
		_, err := parseStatusFilter("todo,bogus", validIssueStatuses)
		if err == nil {
			t.Fatal("expected error for invalid status")
		}
		for _, want := range []string{`"bogus"`, "backlog", "todo", "in_progress", "in_review", "done", "blocked", "cancelled"} {
			if !bytes.Contains([]byte(err.Error()), []byte(want)) {
				t.Errorf("error %q missing %q", err.Error(), want)
			}
		}
	})
}

// createIsolatedWorkspace seeds a throwaway workspace (with testUserID as
// owner) so list tests aren't polluted by rows other tests leave in the
// shared handler-test workspace.
func createIsolatedWorkspace(t *testing.T) string {
	t.Helper()
	ctx := context.Background()

	var wsID string
	slug := fmt.Sprintf("list-test-%d", time.Now().UnixNano())
	if err := testPool.QueryRow(ctx,
		`INSERT INTO workspace (name, slug, issue_prefix) VALUES ($1, $2, $3) RETURNING id`,
		"List Test", slug, "LST").Scan(&wsID); err != nil {
		t.Fatalf("create workspace: %v", err)
	}
	if _, err := testPool.Exec(ctx,
		`INSERT INTO member (workspace_id, user_id, role) VALUES ($1, $2, 'owner')`, wsID, testUserID); err != nil {
		t.Fatalf("create member: %v", err)
	}
	t.Cleanup(func() {
		testPool.Exec(context.Background(), `DELETE FROM workspace WHERE id = $1`, wsID)
	})
	return wsID
}

// newScopedRequest mirrors newRequest but targets an explicit workspace.
func newScopedRequest(method, path, workspaceID string, body any) *http.Request {
	var buf bytes.Buffer
	if body != nil {
		json.NewEncoder(&buf).Encode(body)
	}
	req := httptest.NewRequest(method, path, &buf)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-User-ID", testUserID)
	req.Header.Set("X-Workspace-ID", workspaceID)
	return req
}

// createIssueInWorkspace creates an issue with the given status via the real
// CreateIssue handler and returns its ID.
func createIssueInWorkspace(t *testing.T, workspaceID, title, status string) string {
	t.Helper()
	w := httptest.NewRecorder()
	req := newScopedRequest("POST", "/api/issues?workspace_id="+workspaceID, workspaceID, map[string]any{
		"title":  title,
		"status": status,
	})
	testHandler.CreateIssue(w, req)
	if w.Code != http.StatusCreated {
		t.Fatalf("CreateIssue(%q): expected 201, got %d: %s", title, w.Code, w.Body.String())
	}
	var resp IssueResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode created issue: %v", err)
	}
	return resp.ID
}

func TestListIssuesCommaSeparatedStatuses(t *testing.T) {
	workspaceID := createIsolatedWorkspace(t)
	todoID := createIssueInWorkspace(t, workspaceID, "comma-test todo", "todo")
	inProgressID := createIssueInWorkspace(t, workspaceID, "comma-test in progress", "in_progress")
	createIssueInWorkspace(t, workspaceID, "comma-test backlog", "backlog")

	list := func(query string) (int, map[string]any) {
		w := httptest.NewRecorder()
		req := newScopedRequest("GET", "/api/issues?workspace_id="+workspaceID+"&"+query, workspaceID, nil)
		testHandler.ListIssues(w, req)
		if w.Code != http.StatusOK {
			t.Fatalf("ListIssues(%s): expected 200, got %d: %s", query, w.Code, w.Body.String())
		}
		var resp map[string]any
		if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
			t.Fatalf("decode list response: %v", err)
		}
		return w.Code, resp
	}

	t.Run("comma list returns merged results", func(t *testing.T) {
		_, resp := list("status=todo,in_progress")
		issues, _ := resp["issues"].([]any)
		total, _ := resp["total"].(float64)
		if len(issues) != 2 {
			t.Fatalf("expected 2 issues, got %d: %v", len(issues), issues)
		}
		if int(total) != 2 {
			t.Fatalf("expected total=2, got %v", total)
		}
		gotIDs := map[string]bool{}
		for _, raw := range issues {
			issue, _ := raw.(map[string]any)
			gotIDs[issue["id"].(string)] = true
		}
		if !gotIDs[todoID] || !gotIDs[inProgressID] {
			t.Fatalf("merged result missing expected issues: %v", gotIDs)
		}
	})

	t.Run("limit applies after merge", func(t *testing.T) {
		_, resp := list("status=todo,in_progress&limit=1")
		issues, _ := resp["issues"].([]any)
		total, _ := resp["total"].(float64)
		if len(issues) != 1 {
			t.Fatalf("expected 1 issue with limit=1, got %d", len(issues))
		}
		if int(total) != 2 {
			t.Fatalf("expected total=2 (pre-limit), got %v", total)
		}
	})

	t.Run("single status still works", func(t *testing.T) {
		_, resp := list("status=backlog")
		issues, _ := resp["issues"].([]any)
		if len(issues) != 1 {
			t.Fatalf("expected 1 backlog issue, got %d", len(issues))
		}
	})
}

func TestListIssuesInvalidStatusReturns400(t *testing.T) {
	workspaceID := createIsolatedWorkspace(t)

	w := httptest.NewRecorder()
	req := newScopedRequest("GET", "/api/issues?workspace_id="+workspaceID+"&status=todo,bogus", workspaceID, nil)
	testHandler.ListIssues(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
	}
	for _, want := range []string{`"bogus"`, "backlog", "todo", "in_progress", "in_review", "done", "blocked", "cancelled"} {
		if !bytes.Contains(w.Body.Bytes(), []byte(want)) {
			t.Errorf("400 body missing %q: %s", want, w.Body.String())
		}
	}
}
