package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// taskTestFixture seeds an isolated workspace with a runtime, an agent, and a
// handful of tasks in different statuses.
type taskTestFixture struct {
	workspaceID string
	agentID     string
	queuedID    string
	runningID   string
	completedID string // linked to an issue, carries a result payload
	issueID     string
}

func setupTaskTestFixture(t *testing.T) taskTestFixture {
	t.Helper()
	ctx := context.Background()
	workspaceID := createIsolatedWorkspace(t)

	var runtimeID string
	if err := testPool.QueryRow(ctx, `
		INSERT INTO agent_runtime (
			workspace_id, daemon_id, name, runtime_mode, provider, status, device_info, metadata, last_seen_at
		)
		VALUES ($1, NULL, $2, 'cloud', $3, 'online', '{}'::jsonb, '{}'::jsonb, now())
		RETURNING id
	`, workspaceID, "Task List Test Runtime", "task_list_test_runtime").Scan(&runtimeID); err != nil {
		t.Fatalf("create runtime: %v", err)
	}

	var agentID string
	if err := testPool.QueryRow(ctx, `
		INSERT INTO agent (
			workspace_id, name, description, runtime_mode, runtime_config,
			runtime_id, visibility, max_concurrent_tasks, owner_id
		)
		VALUES ($1, $2, '', 'cloud', '{}'::jsonb, $3, 'workspace', 1, $4)
		RETURNING id
	`, workspaceID, "Task List Test Agent", runtimeID, testUserID).Scan(&agentID); err != nil {
		t.Fatalf("create agent: %v", err)
	}

	insertTask := func(status string, issueID *string, result *string) string {
		var taskID string
		var issueArg any
		if issueID != nil {
			issueArg = *issueID
		}
		var resultArg any
		if result != nil {
			resultArg = *result
		}
		if err := testPool.QueryRow(ctx, `
			INSERT INTO agent_task_queue (agent_id, runtime_id, issue_id, status, priority, result)
			VALUES ($1, $2, $3::uuid, $4, 0, $5::jsonb)
			RETURNING id
		`, agentID, runtimeID, issueArg, status, resultArg).Scan(&taskID); err != nil {
			t.Fatalf("create %s task: %v", status, err)
		}
		return taskID
	}

	fx := taskTestFixture{workspaceID: workspaceID, agentID: agentID}
	fx.queuedID = insertTask("queued", nil, nil)
	fx.runningID = insertTask("running", nil, nil)
	fx.issueID = createIssueInWorkspace(t, workspaceID, "task list test issue", "in_progress")
	fx.completedID = insertTask("completed", &fx.issueID, ptr(`{"summary":"all done"}`))
	return fx
}

func listWorkspaceTasksRequest(t *testing.T, workspaceID, query string) (int, map[string]any) {
	t.Helper()
	path := "/api/tasks?workspace_id=" + workspaceID
	if query != "" {
		path += "&" + query
	}
	w := httptest.NewRecorder()
	req := newScopedRequest("GET", path, workspaceID, nil)
	testHandler.ListWorkspaceTasks(w, req)
	var resp map[string]any
	if w.Code == http.StatusOK {
		if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
			t.Fatalf("decode list response: %v", err)
		}
	}
	return w.Code, resp
}

func taskIDs(t *testing.T, resp map[string]any) map[string]bool {
	t.Helper()
	out := map[string]bool{}
	tasks, _ := resp["tasks"].([]any)
	for _, raw := range tasks {
		task, _ := raw.(map[string]any)
		id, _ := task["id"].(string)
		out[id] = true
	}
	return out
}

func TestListWorkspaceTasks(t *testing.T) {
	fx := setupTaskTestFixture(t)

	t.Run("returns all workspace tasks with total", func(t *testing.T) {
		code, resp := listWorkspaceTasksRequest(t, fx.workspaceID, "")
		if code != http.StatusOK {
			t.Fatalf("expected 200, got %d", code)
		}
		ids := taskIDs(t, resp)
		for _, want := range []string{fx.queuedID, fx.runningID, fx.completedID} {
			if !ids[want] {
				t.Errorf("missing task %s in response", want)
			}
		}
		if total, _ := resp["total"].(float64); int(total) != 3 {
			t.Errorf("total = %v, want 3", total)
		}
	})

	t.Run("issue identifier is populated for issue-linked tasks", func(t *testing.T) {
		_, resp := listWorkspaceTasksRequest(t, fx.workspaceID, "")
		tasks, _ := resp["tasks"].([]any)
		for _, raw := range tasks {
			task, _ := raw.(map[string]any)
			if task["id"] == fx.completedID {
				if got := task["issue_identifier"]; got != "LST-1" {
					t.Errorf("issue_identifier = %v, want LST-1", got)
				}
			}
		}
	})

	t.Run("comma-separated status filter merges results", func(t *testing.T) {
		code, resp := listWorkspaceTasksRequest(t, fx.workspaceID, "status=queued,running")
		if code != http.StatusOK {
			t.Fatalf("expected 200, got %d", code)
		}
		ids := taskIDs(t, resp)
		if len(ids) != 2 || !ids[fx.queuedID] || !ids[fx.runningID] {
			t.Errorf("expected queued+running tasks, got %v", ids)
		}
		if total, _ := resp["total"].(float64); int(total) != 2 {
			t.Errorf("total = %v, want 2", total)
		}
	})

	t.Run("invalid status returns 400 with valid values", func(t *testing.T) {
		w := httptest.NewRecorder()
		req := newScopedRequest("GET", "/api/tasks?workspace_id="+fx.workspaceID+"&status=bogus", fx.workspaceID, nil)
		testHandler.ListWorkspaceTasks(w, req)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
		}
		for _, want := range []string{`"bogus"`, "queued", "dispatched", "running", "completed", "failed", "cancelled"} {
			if !strings.Contains(w.Body.String(), want) {
				t.Errorf("400 body missing %q: %s", want, w.Body.String())
			}
		}
	})

	t.Run("agent_id and issue_id filters", func(t *testing.T) {
		_, resp := listWorkspaceTasksRequest(t, fx.workspaceID, "agent_id="+fx.agentID)
		if ids := taskIDs(t, resp); len(ids) != 3 {
			t.Errorf("agent_id filter: expected 3 tasks, got %v", ids)
		}

		_, resp = listWorkspaceTasksRequest(t, fx.workspaceID, "issue_id="+fx.issueID)
		ids := taskIDs(t, resp)
		if len(ids) != 1 || !ids[fx.completedID] {
			t.Errorf("issue_id filter: expected only the completed task, got %v", ids)
		}
	})

	t.Run("limit and offset page through results", func(t *testing.T) {
		_, resp := listWorkspaceTasksRequest(t, fx.workspaceID, "limit=2")
		if tasks, _ := resp["tasks"].([]any); len(tasks) != 2 {
			t.Fatalf("limit=2: expected 2 tasks, got %d", len(tasks))
		}
		if total, _ := resp["total"].(float64); int(total) != 3 {
			t.Errorf("total = %v, want 3 (pre-limit)", total)
		}
		_, resp = listWorkspaceTasksRequest(t, fx.workspaceID, "limit=2&offset=2")
		if tasks, _ := resp["tasks"].([]any); len(tasks) != 1 {
			t.Fatalf("offset=2: expected 1 task, got %d", len(tasks))
		}
	})

	t.Run("malformed agent_id returns 400", func(t *testing.T) {
		w := httptest.NewRecorder()
		req := newScopedRequest("GET", "/api/tasks?workspace_id="+fx.workspaceID+"&agent_id=nope", fx.workspaceID, nil)
		testHandler.ListWorkspaceTasks(w, req)
		if w.Code != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d: %s", w.Code, w.Body.String())
		}
	})
}

func TestGetWorkspaceTask(t *testing.T) {
	fx := setupTaskTestFixture(t)

	t.Run("returns task detail including result", func(t *testing.T) {
		w := httptest.NewRecorder()
		req := newScopedRequest("GET", "/api/tasks/"+fx.completedID, fx.workspaceID, nil)
		req = withURLParam(req, "taskId", fx.completedID)
		testHandler.GetWorkspaceTask(w, req)

		if w.Code != http.StatusOK {
			t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
		}
		var resp map[string]any
		if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
			t.Fatalf("decode get response: %v", err)
		}
		if resp["id"] != fx.completedID {
			t.Errorf("id = %v, want %s", resp["id"], fx.completedID)
		}
		if resp["status"] != "completed" {
			t.Errorf("status = %v, want completed", resp["status"])
		}
		result, _ := resp["result"].(map[string]any)
		if result["summary"] != "all done" {
			t.Errorf("result = %v, want summary payload", resp["result"])
		}
	})

	t.Run("task from another workspace is not found", func(t *testing.T) {
		w := httptest.NewRecorder()
		req := newScopedRequest("GET", "/api/tasks/"+fx.completedID, testWorkspaceID, nil)
		req = withURLParam(req, "taskId", fx.completedID)
		testHandler.GetWorkspaceTask(w, req)

		if w.Code != http.StatusNotFound {
			t.Fatalf("expected 404, got %d: %s", w.Code, w.Body.String())
		}
	})

	t.Run("unknown task is not found", func(t *testing.T) {
		missing := "00000000-0000-0000-0000-000000000000"
		w := httptest.NewRecorder()
		req := newScopedRequest("GET", "/api/tasks/"+missing, fx.workspaceID, nil)
		req = withURLParam(req, "taskId", missing)
		testHandler.GetWorkspaceTask(w, req)

		if w.Code != http.StatusNotFound {
			t.Fatalf("expected 404, got %d: %s", w.Code, w.Body.String())
		}
	})
}
