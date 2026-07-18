package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"path"
	"strconv"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// validArtifactKinds is the canonical set of artifact.kind values. The DB
// column is plain TEXT (no enum) so new kinds ship without a migration —
// validation lives here at the API boundary.
var validArtifactKinds = []string{"metrics", "equity", "plot", "log", "dataset", "other"}

func isValidArtifactKind(kind string) bool {
	for _, k := range validArtifactKinds {
		if k == kind {
			return true
		}
	}
	return false
}

// parseArtifactMeta validates the optional `meta` form field: empty means
// "{}", anything else must be a valid JSON object so downstream consumers
// can rely on meta being an object, not a scalar/array.
func parseArtifactMeta(raw string) (json.RawMessage, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return json.RawMessage(`{}`), nil
	}
	var obj map[string]any
	if err := json.Unmarshal([]byte(raw), &obj); err != nil {
		return nil, fmt.Errorf("meta must be a JSON object: %w", err)
	}
	return json.RawMessage(raw), nil
}

// artifactDownloadFilename strips characters that would break or inject
// into a Content-Disposition header (mirrors storage.sanitizeFilename,
// which is unexported there).
func artifactDownloadFilename(name string) string {
	var b strings.Builder
	b.Grow(len(name))
	for _, r := range name {
		if r < 0x20 || r == 0x7f || r == '"' || r == ';' || r == '\\' || r == '\x00' {
			b.WriteRune('_')
		} else {
			b.WriteRune(r)
		}
	}
	if b.Len() == 0 {
		return "artifact"
	}
	return b.String()
}

// ArtifactResponse is the API shape of one artifact row. DownloadURL points
// at the API download endpoint (which streams via the storage backend) so
// the response is identical for local and S3 deployments — clients never
// handle storage URLs directly.
type ArtifactResponse struct {
	ID          string          `json:"id"`
	WorkspaceID string          `json:"workspace_id"`
	TaskID      *string         `json:"task_id"`
	IssueID     *string         `json:"issue_id"`
	Kind        string          `json:"kind"`
	Name        string          `json:"name"`
	SizeBytes   int64           `json:"size_bytes"`
	ContentType string          `json:"content_type"`
	Meta        json.RawMessage `json:"meta"`
	DownloadURL string          `json:"download_url"`
	CreatedAt   string          `json:"created_at"`
}

func artifactToResponse(a db.Artifact) ArtifactResponse {
	id := uuidToString(a.ID)
	resp := ArtifactResponse{
		ID:          id,
		WorkspaceID: uuidToString(a.WorkspaceID),
		Kind:        a.Kind,
		Name:        a.Name,
		SizeBytes:   a.SizeBytes,
		ContentType: a.ContentType,
		Meta:        a.Meta,
		DownloadURL: "/api/artifacts/" + id + "/download",
		CreatedAt:   a.CreatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
	}
	if len(resp.Meta) == 0 {
		resp.Meta = json.RawMessage(`{}`)
	}
	if a.TaskID.Valid {
		s := uuidToString(a.TaskID)
		resp.TaskID = &s
	}
	if a.IssueID.Valid {
		s := uuidToString(a.IssueID)
		resp.IssueID = &s
	}
	return resp
}

// resolveTaskInWorkspace loads a task and verifies it belongs to the given
// workspace via the task's agent (agent_task_queue has no workspace_id
// column) — the same scoping GetWorkspaceTask uses.
func (h *Handler) resolveTaskInWorkspace(ctx context.Context, taskUUID pgtype.UUID, workspaceID string) (db.AgentTaskQueue, bool) {
	task, err := h.Queries.GetAgentTask(ctx, taskUUID)
	if err != nil {
		return db.AgentTaskQueue{}, false
	}
	agent, err := h.Queries.GetAgent(ctx, task.AgentID)
	if err != nil || uuidToString(agent.WorkspaceID) != workspaceID {
		return db.AgentTaskQueue{}, false
	}
	return task, true
}

// ---------------------------------------------------------------------------
// UploadArtifact — POST /api/tasks/{taskId}/artifacts
//
// Multipart upload of a typed run artifact. Form fields: file (required),
// kind (optional, default "other"), meta (optional JSON object string). The
// task must belong to the request workspace; the artifact inherits the
// task's issue link so issue-level queries pick it up.
// ---------------------------------------------------------------------------

func (h *Handler) UploadArtifact(w http.ResponseWriter, r *http.Request) {
	if h.Storage == nil {
		writeError(w, http.StatusServiceUnavailable, "artifact upload not configured")
		return
	}
	if _, ok := requireUserID(w, r); !ok {
		return
	}

	workspaceID := h.resolveWorkspaceID(r)
	wsUUID, ok := parseUUIDOrBadRequest(w, workspaceID, "workspace_id")
	if !ok {
		return
	}
	taskUUID, ok := parseUUIDOrBadRequest(w, chi.URLParam(r, "taskId"), "task id")
	if !ok {
		return
	}

	up, ok := parseArtifactUpload(w, r)
	if !ok {
		return
	}

	task, ok := h.resolveTaskInWorkspace(r.Context(), taskUUID, workspaceID)
	if !ok {
		writeError(w, http.StatusNotFound, "task not found")
		return
	}

	h.createArtifactForTask(w, r, task, workspaceID, wsUUID, up)
}

// ---------------------------------------------------------------------------
// DaemonUploadArtifact — POST /api/daemon/tasks/{taskId}/artifacts
//
// Daemon-token variant of UploadArtifact with identical multipart semantics.
// The workspace is derived from the task's agent (agent_task_queue has no
// workspace_id column — the same scoping resolveTaskInWorkspace uses)
// instead of an X-Workspace-ID header, so daemons only need the task ID
// they were handed.
// ---------------------------------------------------------------------------

func (h *Handler) DaemonUploadArtifact(w http.ResponseWriter, r *http.Request) {
	if h.Storage == nil {
		writeError(w, http.StatusServiceUnavailable, "artifact upload not configured")
		return
	}
	taskUUID, ok := parseUUIDOrBadRequest(w, chi.URLParam(r, "taskId"), "task_id")
	if !ok {
		return
	}
	task, err := h.Queries.GetAgentTask(r.Context(), taskUUID)
	if err != nil {
		if isNotFound(err) {
			writeError(w, http.StatusNotFound, "task not found")
			return
		}
		slog.Warn("get agent task failed", "task_id", taskUUID, "error", err)
		writeError(w, http.StatusInternalServerError, "failed to load task")
		return
	}
	agent, err := h.Queries.GetAgent(r.Context(), task.AgentID)
	if err != nil {
		writeError(w, http.StatusNotFound, "task not found")
		return
	}
	workspaceID := uuidToString(agent.WorkspaceID)
	if !h.requireDaemonWorkspaceAccess(w, r, workspaceID) {
		return
	}

	up, ok := parseArtifactUpload(w, r)
	if !ok {
		return
	}

	h.createArtifactForTask(w, r, task, workspaceID, agent.WorkspaceID, up)
}

// artifactUpload is one validated multipart artifact upload, fully read into
// memory (bounded by maxUploadSize) and ready to store.
type artifactUpload struct {
	kind        string
	meta        json.RawMessage
	filename    string
	contentType string
	data        []byte
}

// parseArtifactUpload reads and validates the multipart artifact form:
// file (required), kind (optional, default "other"), meta (optional JSON
// object string). On failure it writes the error response and returns
// ok=false; nothing is touched beyond the request body.
func parseArtifactUpload(w http.ResponseWriter, r *http.Request) (artifactUpload, bool) {
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadSize)
	if err := r.ParseMultipartForm(maxUploadSize); err != nil {
		writeError(w, http.StatusBadRequest, "file too large or invalid multipart form")
		return artifactUpload{}, false
	}
	defer r.MultipartForm.RemoveAll()

	// Validate cheap form fields before touching the DB or storage.
	kind := strings.TrimSpace(r.FormValue("kind"))
	if kind == "" {
		kind = "other"
	}
	if !isValidArtifactKind(kind) {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("invalid kind %q (valid: %s)", kind, strings.Join(validArtifactKinds, ", ")))
		return artifactUpload{}, false
	}
	meta, err := parseArtifactMeta(r.FormValue("meta"))
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return artifactUpload{}, false
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("missing file field: %v", err))
		return artifactUpload{}, false
	}
	defer file.Close()

	// Sniff content type from bytes (extension override for known types),
	// same as UploadFile.
	buf := make([]byte, 512)
	n, err := file.Read(buf)
	if err != nil && err != io.EOF {
		writeError(w, http.StatusBadRequest, "failed to read file")
		return artifactUpload{}, false
	}
	contentType := http.DetectContentType(buf[:n])
	if ct, ok := extContentTypes[strings.ToLower(path.Ext(header.Filename))]; ok {
		contentType = ct
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to read file")
		return artifactUpload{}, false
	}
	data, err := io.ReadAll(file)
	if err != nil {
		writeError(w, http.StatusBadRequest, "failed to read file")
		return artifactUpload{}, false
	}

	return artifactUpload{
		kind:        kind,
		meta:        meta,
		filename:    header.Filename,
		contentType: contentType,
		data:        data,
	}, true
}

// createArtifactForTask stores the blob and inserts the artifact row, then
// writes the 201 response. The artifact inherits the task's issue link so
// issue-level queries pick it up.
func (h *Handler) createArtifactForTask(w http.ResponseWriter, r *http.Request, task db.AgentTaskQueue, workspaceID string, wsUUID pgtype.UUID, up artifactUpload) {
	id, err := uuid.NewV7()
	if err != nil {
		slog.Error("failed to generate uuid", "error", err)
		writeError(w, http.StatusInternalServerError, "internal error")
		return
	}
	key := "workspaces/" + workspaceID + "/artifacts/" + id.String() + path.Ext(up.filename)

	if _, err := h.Storage.Upload(r.Context(), key, up.data, up.contentType, up.filename); err != nil {
		slog.Error("artifact upload failed", "error", err)
		writeError(w, http.StatusInternalServerError, "upload failed")
		return
	}

	artifact, err := h.Queries.CreateArtifact(r.Context(), db.CreateArtifactParams{
		ID:          pgtype.UUID{Bytes: id, Valid: true},
		WorkspaceID: wsUUID,
		TaskID:      task.ID,
		IssueID:     task.IssueID,
		Kind:        up.kind,
		Name:        up.filename,
		StorageKey:  key,
		SizeBytes:   int64(len(up.data)),
		ContentType: up.contentType,
		Meta:        up.meta,
	})
	if err != nil {
		slog.Error("failed to create artifact record", "error", err)
		// Don't leave an orphaned blob behind when the row insert fails.
		h.Storage.Delete(r.Context(), key)
		writeError(w, http.StatusInternalServerError, "failed to record artifact")
		return
	}

	// kind=metrics blobs additionally parse into a queryable run_metric row.
	// Ingestion is best-effort: an unparseable blob never fails the upload,
	// it just flips metrics_ingested to false in the response.
	if up.kind == "metrics" {
		metricsIngested := h.ingestRunMetric(r.Context(), artifact, up.data)
		writeJSON(w, http.StatusCreated, artifactUploadResponse{
			ArtifactResponse: artifactToResponse(artifact),
			MetricsIngested:  metricsIngested,
		})
		return
	}

	writeJSON(w, http.StatusCreated, artifactToResponse(artifact))
}

// ---------------------------------------------------------------------------
// ListTaskArtifacts — GET /api/tasks/{taskId}/artifacts
// ---------------------------------------------------------------------------

func (h *Handler) ListTaskArtifacts(w http.ResponseWriter, r *http.Request) {
	workspaceID := h.resolveWorkspaceID(r)
	wsUUID, ok := parseUUIDOrBadRequest(w, workspaceID, "workspace_id")
	if !ok {
		return
	}
	taskUUID, ok := parseUUIDOrBadRequest(w, chi.URLParam(r, "taskId"), "task id")
	if !ok {
		return
	}
	if _, ok := h.resolveTaskInWorkspace(r.Context(), taskUUID, workspaceID); !ok {
		writeError(w, http.StatusNotFound, "task not found")
		return
	}

	artifacts, err := h.Queries.ListArtifactsByTask(r.Context(), db.ListArtifactsByTaskParams{
		TaskID:      taskUUID,
		WorkspaceID: wsUUID,
	})
	if err != nil {
		slog.Error("failed to list task artifacts", "error", err)
		writeError(w, http.StatusInternalServerError, "failed to list artifacts")
		return
	}

	resp := make([]ArtifactResponse, len(artifacts))
	for i, a := range artifacts {
		resp[i] = artifactToResponse(a)
	}
	writeJSON(w, http.StatusOK, resp)
}

// ---------------------------------------------------------------------------
// ListArtifacts — GET /api/artifacts?kind=&issue_id=&limit=&offset=
// ---------------------------------------------------------------------------

func (h *Handler) ListArtifacts(w http.ResponseWriter, r *http.Request) {
	workspaceID := h.resolveWorkspaceID(r)
	wsUUID, ok := parseUUIDOrBadRequest(w, workspaceID, "workspace_id")
	if !ok {
		return
	}

	var kindFilter pgtype.Text
	if kind := strings.TrimSpace(r.URL.Query().Get("kind")); kind != "" {
		if !isValidArtifactKind(kind) {
			writeError(w, http.StatusBadRequest, fmt.Sprintf("invalid kind %q (valid: %s)", kind, strings.Join(validArtifactKinds, ", ")))
			return
		}
		kindFilter = pgtype.Text{String: kind, Valid: true}
	}
	var issueFilter pgtype.UUID
	if i := r.URL.Query().Get("issue_id"); i != "" {
		id, ok := parseUUIDOrBadRequest(w, i, "issue_id")
		if !ok {
			return
		}
		issueFilter = id
	}

	limit := 100
	offset := 0
	if l := r.URL.Query().Get("limit"); l != "" {
		if v, err := strconv.Atoi(l); err == nil && v > 0 {
			limit = v
		}
	}
	if o := r.URL.Query().Get("offset"); o != "" {
		if v, err := strconv.Atoi(o); err == nil && v >= 0 {
			offset = v
		}
	}

	artifacts, err := h.Queries.ListArtifactsByWorkspace(r.Context(), db.ListArtifactsByWorkspaceParams{
		WorkspaceID: wsUUID,
		Kind:        kindFilter,
		IssueID:     issueFilter,
		Limit:       int32(limit),
		Offset:      int32(offset),
	})
	if err != nil {
		slog.Error("failed to list artifacts", "error", err)
		writeError(w, http.StatusInternalServerError, "failed to list artifacts")
		return
	}

	resp := make([]ArtifactResponse, len(artifacts))
	for i, a := range artifacts {
		resp[i] = artifactToResponse(a)
	}
	writeJSON(w, http.StatusOK, resp)
}

// loadArtifactForRequest resolves the workspace + {id} URL param and loads
// the workspace-scoped artifact, writing the error response on failure.
func (h *Handler) loadArtifactForRequest(w http.ResponseWriter, r *http.Request) (db.Artifact, bool) {
	workspaceID := h.resolveWorkspaceID(r)
	wsUUID, ok := parseUUIDOrBadRequest(w, workspaceID, "workspace_id")
	if !ok {
		return db.Artifact{}, false
	}
	artUUID, ok := parseUUIDOrBadRequest(w, chi.URLParam(r, "id"), "artifact id")
	if !ok {
		return db.Artifact{}, false
	}
	artifact, err := h.Queries.GetArtifact(r.Context(), db.GetArtifactParams{
		ID:          artUUID,
		WorkspaceID: wsUUID,
	})
	if err != nil {
		writeError(w, http.StatusNotFound, "artifact not found")
		return db.Artifact{}, false
	}
	return artifact, true
}

// ---------------------------------------------------------------------------
// DownloadArtifact — GET /api/artifacts/{id}/download
//
// Streams the blob through the storage backend (GetReader works for both
// local disk and S3, so behavior is identical across deployments) with the
// original filename in Content-Disposition.
// ---------------------------------------------------------------------------

func (h *Handler) DownloadArtifact(w http.ResponseWriter, r *http.Request) {
	if h.Storage == nil {
		writeError(w, http.StatusServiceUnavailable, "storage not configured")
		return
	}
	artifact, ok := h.loadArtifactForRequest(w, r)
	if !ok {
		return
	}

	reader, err := h.Storage.GetReader(r.Context(), artifact.StorageKey)
	if err != nil {
		slog.Error("failed to open artifact", "id", artifact.ID, "key", artifact.StorageKey, "error", err)
		writeError(w, http.StatusNotFound, "artifact object not found")
		return
	}
	defer reader.Close()

	contentType := artifact.ContentType
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, artifactDownloadFilename(artifact.Name)))
	if artifact.SizeBytes > 0 {
		w.Header().Set("Content-Length", strconv.FormatInt(artifact.SizeBytes, 10))
	}
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if _, err := io.Copy(w, reader); err != nil {
		slog.Error("failed to stream artifact", "id", artifact.ID, "error", err)
	}
}

// ---------------------------------------------------------------------------
// DeleteArtifact — DELETE /api/artifacts/{id}
//
// Deletes the row and the stored blob, mirroring DeleteAttachment. Any
// workspace member may delete (artifact rows carry no uploader identity).
// ---------------------------------------------------------------------------

func (h *Handler) DeleteArtifact(w http.ResponseWriter, r *http.Request) {
	if _, ok := requireUserID(w, r); !ok {
		return
	}
	artifact, ok := h.loadArtifactForRequest(w, r)
	if !ok {
		return
	}

	if err := h.Queries.DeleteArtifact(r.Context(), db.DeleteArtifactParams{
		ID:          artifact.ID,
		WorkspaceID: artifact.WorkspaceID,
	}); err != nil {
		slog.Error("failed to delete artifact", "error", err)
		writeError(w, http.StatusInternalServerError, "failed to delete artifact")
		return
	}

	if h.Storage != nil {
		h.Storage.Delete(r.Context(), artifact.StorageKey)
	}
	w.WriteHeader(http.StatusNoContent)
}
