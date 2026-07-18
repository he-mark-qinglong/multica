package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// TestPreviewDispatch_Validation covers the request-validation paths that
// reject before any dispatch computation: malformed bodies, missing title,
// malformed assignee UUIDs, unpaired assignee fields, and unknown assignee
// types. Unknown (but well-formed) agent assignees are covered by
// TestPreviewDispatch_UnknownAgent below.
func TestPreviewDispatch_Validation(t *testing.T) {
	if testHandler == nil {
		t.Skip("database not available")
	}

	rawPost := func(body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "/api/issues/preview-dispatch", strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-User-ID", testUserID)
		req.Header.Set("X-Workspace-ID", testWorkspaceID)
		w := httptest.NewRecorder()
		testHandler.PreviewDispatch(w, req)
		return w
	}

	t.Run("invalid JSON body", func(t *testing.T) {
		w := rawPost("{not json")
		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
		}
	})

	t.Run("missing title", func(t *testing.T) {
		w := rawPost(`{"status":"todo"}`)
		if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "title is required") {
			t.Fatalf("expected 400 title error, got %d: %s", w.Code, w.Body.String())
		}
	})

	t.Run("non-UUID assignee_id", func(t *testing.T) {
		w := rawPost(`{"title":"x","assignee_type":"agent","assignee_id":"nope"}`)
		if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "assignee_id") {
			t.Fatalf("expected 400 assignee_id error, got %d: %s", w.Code, w.Body.String())
		}
	})

	t.Run("assignee_type without assignee_id", func(t *testing.T) {
		w := rawPost(`{"title":"x","assignee_type":"agent"}`)
		if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "provided together") {
			t.Fatalf("expected 400 pairing error, got %d: %s", w.Code, w.Body.String())
		}
	})

	t.Run("unknown assignee_type", func(t *testing.T) {
		w := rawPost(`{"title":"x","assignee_type":"robot","assignee_id":"aaaaaaaa-1111-2222-3333-444444444444"}`)
		if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "assignee_type must be") {
			t.Fatalf("expected 400 type error, got %d: %s", w.Code, w.Body.String())
		}
	})
}

// TestPreviewDispatch_Unassigned: an unassigned issue is valid but dispatches
// nothing — the response says so explicitly instead of guessing.
func TestPreviewDispatch_Unassigned(t *testing.T) {
	if testHandler == nil {
		t.Skip("database not available")
	}
	w := httptest.NewRecorder()
	testHandler.PreviewDispatch(w, newRequest("POST", "/api/issues/preview-dispatch", map[string]any{
		"title": "no assignee",
	}))
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
	var resp PreviewDispatchResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.WouldDispatch {
		t.Fatalf("unassigned issue must not dispatch: %+v", resp)
	}
	if !strings.Contains(resp.Reason, "unassigned") {
		t.Fatalf("reason should explain unassigned, got %q", resp.Reason)
	}
}

// TestPreviewDispatch_UnknownAgent is the validation win over plain create: a
// well-formed but nonexistent agent UUID is rejected with 400 at preview time
// instead of failing later at enqueue.
func TestPreviewDispatch_UnknownAgent(t *testing.T) {
	if testHandler == nil {
		t.Skip("database not available")
	}
	w := httptest.NewRecorder()
	testHandler.PreviewDispatch(w, newRequest("POST", "/api/issues/preview-dispatch", map[string]any{
		"title":         "x",
		"assignee_type": "agent",
		"assignee_id":   "aaaaaaaa-0000-0000-0000-000000000000",
	}))
	if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "does not refer to an agent") {
		t.Fatalf("expected 400 unknown agent, got %d: %s", w.Code, w.Body.String())
	}
}

// TestPreviewDispatch_Agent is the happy path: an agent assignee in the
// dispatch set reports the agent, its runtime, and the live queue depth.
func TestPreviewDispatch_Agent(t *testing.T) {
	if testHandler == nil {
		t.Skip("database not available")
	}
	agentID := createHandlerTestAgent(t, "preview-dispatch-agent", nil)
	// Queue depth: 2 queued + 1 running.
	createHandlerTestTaskForAgent(t, agentID)
	createHandlerTestTaskForAgent(t, agentID)
	var runningID string
	if err := testPool.QueryRow(context.Background(), `
		INSERT INTO agent_task_queue (agent_id, runtime_id, status, priority)
		VALUES ($1, $2, 'running', 0)
		RETURNING id
	`, agentID, handlerTestRuntimeID(t)).Scan(&runningID); err != nil {
		t.Fatalf("seed running task: %v", err)
	}
	t.Cleanup(func() {
		testPool.Exec(context.Background(), `DELETE FROM agent_task_queue WHERE id = $1`, runningID)
	})

	post := func(status string) PreviewDispatchResponse {
		w := httptest.NewRecorder()
		testHandler.PreviewDispatch(w, newRequest("POST", "/api/issues/preview-dispatch", map[string]any{
			"title":         "preview me",
			"status":        status,
			"assignee_type": "agent",
			"assignee_id":   agentID,
		}))
		if w.Code != http.StatusOK {
			t.Fatalf("status %s: expected 200, got %d: %s", status, w.Code, w.Body.String())
		}
		var resp PreviewDispatchResponse
		if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
			t.Fatalf("decode: %v", err)
		}
		return resp
	}

	resp := post("todo")
	if !resp.WouldDispatch {
		t.Fatalf("todo should dispatch, reason=%q", resp.Reason)
	}
	if resp.Agent == nil || resp.Agent.Name != "preview-dispatch-agent" {
		t.Fatalf("agent payload wrong: %+v", resp.Agent)
	}
	if resp.Agent.Runtime == nil || !resp.Agent.Runtime.Online || resp.Agent.Runtime.Provider == "" {
		t.Fatalf("runtime payload wrong: %+v", resp.Agent.Runtime)
	}
	if resp.Queue == nil || resp.Queue.Queued != 2 || resp.Queue.Running != 1 || resp.Queue.MaxConcurrentTasks != 1 {
		t.Fatalf("queue depth wrong: %+v", resp.Queue)
	}

	// Outside the dispatch set → explicit no with reason.
	resp = post("backlog")
	if resp.WouldDispatch || !strings.Contains(resp.Reason, "backlog") {
		t.Fatalf("backlog should not dispatch: %+v", resp)
	}
}

// TestStatusInDispatchSet pins the firing set the preview mirrors.
func TestStatusInDispatchSet(t *testing.T) {
	for _, s := range []string{"todo", "in_progress", "in_review"} {
		if !statusInDispatchSet(s) {
			t.Errorf("%s should be in the dispatch set", s)
		}
	}
	for _, s := range []string{"backlog", "done", "blocked", "cancelled", ""} {
		if statusInDispatchSet(s) {
			t.Errorf("%s should NOT be in the dispatch set", s)
		}
	}
}
