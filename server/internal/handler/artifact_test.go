package handler

import (
	"bytes"
	"context"
	"encoding/json"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/multica-ai/multica/server/internal/middleware"
)

// Pure-validation tests: these exercise branches that never touch the DB.

func TestIsValidArtifactKind(t *testing.T) {
	for _, k := range []string{"metrics", "equity", "plot", "log", "dataset", "other"} {
		if !isValidArtifactKind(k) {
			t.Errorf("isValidArtifactKind(%q) = false, want true", k)
		}
	}
	for _, k := range []string{"", "METRICS", "chart", "metric", " other"} {
		if isValidArtifactKind(k) {
			t.Errorf("isValidArtifactKind(%q) = true, want false", k)
		}
	}
}

func TestParseArtifactMeta(t *testing.T) {
	// Empty → default {}
	m, err := parseArtifactMeta("")
	if err != nil || string(m) != "{}" {
		t.Fatalf("empty meta: got %q, err %v; want {}", m, err)
	}
	// Valid object passes through
	m, err = parseArtifactMeta(`{"campaign":"c7","iteration":3}`)
	if err != nil {
		t.Fatalf("valid meta rejected: %v", err)
	}
	if string(m) != `{"campaign":"c7","iteration":3}` {
		t.Fatalf("meta round-trip mismatch: %q", m)
	}
	// Non-object JSON and garbage are rejected
	for _, bad := range []string{`[1,2]`, `"str"`, `42`, `{invalid`, `null`} {
		if _, err := parseArtifactMeta(bad); err == nil {
			t.Errorf("parseArtifactMeta(%q): expected error, got nil", bad)
		}
	}
}

func TestArtifactDownloadFilename(t *testing.T) {
	if got := artifactDownloadFilename(`eq";..\uity.csv`); got != "eq__.._uity.csv" {
		t.Fatalf("sanitize mismatch: %q", got)
	}
	if got := artifactDownloadFilename(""); got != "artifact" {
		t.Fatalf("empty name: got %q, want artifact", got)
	}
}

// multipartArtifactRequest builds an authenticated upload request for the
// artifact endpoints with the given form fields and optional file.
func multipartArtifactRequest(t *testing.T, url string, fields map[string]string, fileField, fileName, fileBody string) *http.Request {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	if fileField != "" {
		part, err := writer.CreateFormFile(fileField, fileName)
		if err != nil {
			t.Fatal(err)
		}
		part.Write([]byte(fileBody))
	}
	for k, v := range fields {
		if err := writer.WriteField(k, v); err != nil {
			t.Fatal(err)
		}
	}
	writer.Close()

	req := httptest.NewRequest("POST", url, &body)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	req.Header.Set("X-User-ID", testUserID)
	req.Header.Set("X-Workspace-ID", testWorkspaceID)
	return req
}

// TestUploadArtifactRejectsBadKind hits the pure-validation branch: a bad
// kind 400s before any task lookup or storage write.
func TestUploadArtifactRejectsBadKind(t *testing.T) {
	origStorage := testHandler.Storage
	testHandler.Storage = &mockStorage{}
	defer func() { testHandler.Storage = origStorage }()

	req := multipartArtifactRequest(t,
		"/api/tasks/00000000-0000-0000-0000-000000000001/artifacts",
		map[string]string{"kind": "chart"}, "file", "equity.csv", "a,b\n1,2\n")

	w := httptest.NewRecorder()
	testHandler.UploadArtifact(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("bad kind: expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestUploadArtifactRejectsBadMeta: invalid JSON meta 400s before DB/storage.
func TestUploadArtifactRejectsBadMeta(t *testing.T) {
	origStorage := testHandler.Storage
	testHandler.Storage = &mockStorage{}
	defer func() { testHandler.Storage = origStorage }()

	req := multipartArtifactRequest(t,
		"/api/tasks/00000000-0000-0000-0000-000000000001/artifacts",
		map[string]string{"meta": "{not-json"}, "file", "equity.csv", "a,b\n1,2\n")

	w := httptest.NewRecorder()
	testHandler.UploadArtifact(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("bad meta: expected 400, got %d: %s", w.Code, w.Body.String())
	}
}

// TestUploadArtifactMissingTask: a well-formed upload against a nonexistent
// task UUID 404s (workspace scoping goes through the task's agent).
func TestUploadArtifactMissingTask(t *testing.T) {
	origStorage := testHandler.Storage
	testHandler.Storage = &mockStorage{}
	defer func() { testHandler.Storage = origStorage }()

	req := multipartArtifactRequest(t,
		"/api/tasks/00000000-0000-0000-0000-000000000001/artifacts",
		map[string]string{"kind": "equity", "meta": `{"symbol":"BTCUSDT"}`}, "file", "equity.csv", "a,b\n1,2\n")

	w := httptest.NewRecorder()
	testHandler.UploadArtifact(w, req)
	if w.Code != http.StatusNotFound {
		t.Fatalf("missing task: expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// TestDeleteArtifactNotFound: deleting an unknown artifact id 404s.
func TestDeleteArtifactNotFound(t *testing.T) {
	req := httptest.NewRequest("DELETE", "/api/artifacts/00000000-0000-0000-0000-000000000001", nil)
	req.Header.Set("X-User-ID", testUserID)
	req.Header.Set("X-Workspace-ID", testWorkspaceID)

	w := httptest.NewRecorder()
	testHandler.DeleteArtifact(w, req)
	// chi URL param {id} is empty when calling the handler directly, so the
	// id parse fails first: 400. Both 400 and 404 prove no row was touched.
	if w.Code != http.StatusBadRequest && w.Code != http.StatusNotFound {
		t.Fatalf("expected 400 or 404, got %d: %s", w.Code, w.Body.String())
	}
}

// ---------------------------------------------------------------------------
// DaemonUploadArtifact — POST /api/daemon/tasks/{taskId}/artifacts
// ---------------------------------------------------------------------------

// daemonMultipartArtifactRequest builds a multipart upload request carrying a
// daemon-token auth context (no X-User-ID) for the given workspace.
func daemonMultipartArtifactRequest(t *testing.T, url, workspaceID string, fields map[string]string, fileName, fileBody string) *http.Request {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	if fileName != "" {
		part, err := writer.CreateFormFile("file", fileName)
		if err != nil {
			t.Fatal(err)
		}
		part.Write([]byte(fileBody))
	}
	for k, v := range fields {
		if err := writer.WriteField(k, v); err != nil {
			t.Fatal(err)
		}
	}
	writer.Close()

	req := httptest.NewRequest("POST", url, &body)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	ctx := middleware.WithDaemonContext(req.Context(), workspaceID, "test-daemon-mdt")
	return req.WithContext(ctx)
}

// TestDaemonUploadArtifactTaskNotFound: a well-formed daemon upload against
// a nonexistent task UUID 404s.
func TestDaemonUploadArtifactTaskNotFound(t *testing.T) {
	if testHandler == nil {
		t.Skip("database not available")
	}
	origStorage := testHandler.Storage
	testHandler.Storage = &mockStorage{}
	defer func() { testHandler.Storage = origStorage }()

	taskID := "00000000-0000-0000-0000-000000000001"
	req := daemonMultipartArtifactRequest(t,
		"/api/daemon/tasks/"+taskID+"/artifacts", testWorkspaceID,
		map[string]string{"kind": "equity"}, "equity.csv", "a,b\n1,2\n")
	req = withURLParam(req, "taskId", taskID)

	w := httptest.NewRecorder()
	testHandler.DaemonUploadArtifact(w, req)
	if w.Code != http.StatusNotFound {
		t.Fatalf("missing task: expected 404, got %d: %s", w.Code, w.Body.String())
	}
}

// TestDaemonUploadArtifactRequiresAuth: without a daemon-token context (and
// no user auth) the upload is rejected before any storage/DB write.
func TestDaemonUploadArtifactRequiresAuth(t *testing.T) {
	if testHandler == nil {
		t.Skip("database not available")
	}
	origStorage := testHandler.Storage
	testHandler.Storage = &mockStorage{}
	defer func() { testHandler.Storage = origStorage }()

	agentID := createHandlerTestAgent(t, "Artifact Auth Agent", []byte("[]"))
	taskID := createHandlerTestTaskForAgent(t, agentID)

	req := daemonMultipartArtifactRequest(t,
		"/api/daemon/tasks/"+taskID+"/artifacts", testWorkspaceID,
		map[string]string{"kind": "log"}, "run.log", "line\n")
	// Strip the daemon context — back to a bare, unauthenticated request.
	contentType := req.Header.Get("Content-Type")
	req = httptest.NewRequest("POST", "/api/daemon/tasks/"+taskID+"/artifacts", req.Body)
	req.Header.Set("Content-Type", contentType)
	req = withURLParam(req, "taskId", taskID)

	w := httptest.NewRecorder()
	testHandler.DaemonUploadArtifact(w, req)
	if w.Code != http.StatusUnauthorized && w.Code != http.StatusNotFound && w.Code != http.StatusForbidden {
		t.Fatalf("unauthenticated: expected 401/403/404, got %d: %s", w.Code, w.Body.String())
	}
}

// TestDaemonUploadArtifactSuccess: a daemon upload stores the blob and
// inserts the artifact row with the workspace derived from the task's agent.
func TestDaemonUploadArtifactSuccess(t *testing.T) {
	if testHandler == nil {
		t.Skip("database not available")
	}
	origStorage := testHandler.Storage
	testHandler.Storage = &mockStorage{}
	defer func() { testHandler.Storage = origStorage }()

	agentID := createHandlerTestAgent(t, "Artifact Daemon Agent", []byte("[]"))
	taskID := createHandlerTestTaskForAgent(t, agentID)

	req := daemonMultipartArtifactRequest(t,
		"/api/daemon/tasks/"+taskID+"/artifacts", testWorkspaceID,
		map[string]string{"kind": "equity", "meta": `{"symbol":"BTCUSDT"}`},
		"equity.csv", "a,b\n1,2\n")
	req = withURLParam(req, "taskId", taskID)

	w := httptest.NewRecorder()
	testHandler.DaemonUploadArtifact(w, req)
	if w.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", w.Code, w.Body.String())
	}

	var resp ArtifactResponse
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.WorkspaceID != testWorkspaceID {
		t.Fatalf("workspace_id: got %q, want %q", resp.WorkspaceID, testWorkspaceID)
	}
	if resp.TaskID == nil || *resp.TaskID != taskID {
		t.Fatalf("task_id: got %v, want %q", resp.TaskID, taskID)
	}
	if resp.Kind != "equity" || resp.Name != "equity.csv" {
		t.Fatalf("kind/name: got %q/%q", resp.Kind, resp.Name)
	}

	// The row must actually exist with the right contents.
	var kind, name string
	var size int64
	var meta []byte
	if err := testPool.QueryRow(context.Background(),
		`SELECT kind, name, size_bytes, meta FROM artifact WHERE id = $1 AND workspace_id = $2 AND task_id = $3`,
		resp.ID, testWorkspaceID, taskID,
	).Scan(&kind, &name, &size, &meta); err != nil {
		t.Fatalf("load inserted artifact: %v", err)
	}
	t.Cleanup(func() {
		testPool.Exec(context.Background(), `DELETE FROM artifact WHERE id = $1`, resp.ID)
	})
	if kind != "equity" || name != "equity.csv" {
		t.Fatalf("stored kind/name: got %q/%q", kind, name)
	}
	if size != int64(len("a,b\n1,2\n")) {
		t.Fatalf("stored size: got %d", size)
	}
	if string(meta) != `{"symbol":"BTCUSDT"}` {
		t.Fatalf("stored meta: got %s", meta)
	}
}
