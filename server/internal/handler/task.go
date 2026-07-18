package handler

import (
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5/pgtype"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// validTaskStatuses is the canonical set of agent_task_queue.status values,
// used to validate the ?status= filter on GET /api/tasks the same way
// ListIssues validates its status filter.
var validTaskStatuses = []string{
	"queued", "dispatched", "running", "completed", "failed", "cancelled",
}

// WorkspaceTaskResponse extends AgentTaskResponse with the linked issue's
// display identifier (e.g. "MUL-42") so list consumers — the CLI table, the
// frontend — don't need an N+1 lookup per row. Empty when the task has no
// linked issue.
type WorkspaceTaskResponse struct {
	AgentTaskResponse
	IssueIdentifier string `json:"issue_identifier,omitempty"`
}

// ListWorkspaceTasks handles GET /api/tasks — a workspace-scoped, filterable
// task list. Query params: status (comma-separated list), agent_id, issue_id,
// limit (default 100), offset (default 0). Returns
// {"tasks": [...], "total": N} where total ignores limit/offset.
func (h *Handler) ListWorkspaceTasks(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	workspaceID := h.resolveWorkspaceID(r)
	wsUUID, ok := parseUUIDOrBadRequest(w, workspaceID, "workspace_id")
	if !ok {
		return
	}

	// Same comma-list semantics as the issue list status filter (see
	// ListIssues): validate every value up front so an unknown status 400s
	// instead of silently matching zero rows.
	statuses, statusErr := parseStatusFilter(r.URL.Query().Get("status"), validTaskStatuses)
	if statusErr != nil {
		writeError(w, http.StatusBadRequest, statusErr.Error())
		return
	}

	// Malformed UUIDs in filters return 400, matching ListIssues — silently
	// coercing them to a zero UUID would mask a client bug.
	var agentFilter pgtype.UUID
	if a := r.URL.Query().Get("agent_id"); a != "" {
		id, ok := parseUUIDOrBadRequest(w, a, "agent_id")
		if !ok {
			return
		}
		agentFilter = id
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

	rows, err := h.Queries.ListWorkspaceTasks(ctx, db.ListWorkspaceTasksParams{
		WorkspaceID: wsUUID,
		Statuses:    statuses,
		AgentID:     agentFilter,
		IssueID:     issueFilter,
		Limit:       int32(limit),
		Offset:      int32(offset),
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list tasks")
		return
	}

	// Get the true total count for pagination awareness.
	total, err := h.Queries.CountWorkspaceTasks(ctx, db.CountWorkspaceTasksParams{
		WorkspaceID: wsUUID,
		Statuses:    statuses,
		AgentID:     agentFilter,
		IssueID:     issueFilter,
	})
	if err != nil {
		total = int64(len(rows))
	}

	prefix := h.getIssuePrefix(ctx, wsUUID)
	resp := make([]WorkspaceTaskResponse, len(rows))
	for i, row := range rows {
		resp[i] = WorkspaceTaskResponse{AgentTaskResponse: taskToResponse(row.AgentTaskQueue, uuidToString(wsUUID))}
		if row.IssueNumber.Valid {
			resp[i].IssueIdentifier = prefix + "-" + strconv.Itoa(int(row.IssueNumber.Int32))
		}
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"tasks": resp,
		"total": total,
	})
}

// GetWorkspaceTask handles GET /api/tasks/{taskId} — a single task including
// its result JSONB. The task must belong to the request workspace; scoping
// goes through the task's agent (agent_task_queue has no workspace_id) so the
// check covers every task kind — issue-linked, chat, autopilot, quick-create.
func (h *Handler) GetWorkspaceTask(w http.ResponseWriter, r *http.Request) {
	workspaceID := h.resolveWorkspaceID(r)
	if _, ok := parseUUIDOrBadRequest(w, workspaceID, "workspace_id"); !ok {
		return
	}

	taskUUID, ok := parseUUIDOrBadRequest(w, chi.URLParam(r, "taskId"), "task id")
	if !ok {
		return
	}
	task, err := h.Queries.GetAgentTask(r.Context(), taskUUID)
	if err != nil {
		writeError(w, http.StatusNotFound, "task not found")
		return
	}

	agent, err := h.Queries.GetAgent(r.Context(), task.AgentID)
	if err != nil || uuidToString(agent.WorkspaceID) != workspaceID {
		writeError(w, http.StatusNotFound, "task not found")
		return
	}

	writeJSON(w, http.StatusOK, taskToResponse(task, uuidToString(agent.WorkspaceID)))
}
