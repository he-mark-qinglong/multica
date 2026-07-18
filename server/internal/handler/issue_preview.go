package handler

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/jackc/pgx/v5/pgtype"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// PreviewDispatchRequest mirrors the dispatch-relevant fields of
// CreateIssueRequest. Title/description are accepted so the caller can send
// the exact body it would POST to /api/issues; they are validated but do not
// influence the dispatch outcome.
type PreviewDispatchRequest struct {
	Title        string  `json:"title"`
	Description  *string `json:"description"`
	Status       string  `json:"status"`
	Priority     string  `json:"priority"`
	AssigneeType *string `json:"assignee_type"`
	AssigneeID   *string `json:"assignee_id"`
}

// PreviewDispatchResponse describes what creating the issue WOULD trigger,
// without creating anything. Agent/Skills/Queue are only populated when the
// assignee is an agent; member/squad/unassigned never dispatch directly.
type PreviewDispatchResponse struct {
	Status        string                 `json:"status"`
	Priority      string                 `json:"priority"`
	AssigneeType  string                 `json:"assignee_type,omitempty"`
	AssigneeID    string                 `json:"assignee_id,omitempty"`
	WouldDispatch bool                   `json:"would_dispatch"`
	Reason        string                 `json:"reason,omitempty"`
	Agent         *PreviewDispatchAgent  `json:"agent,omitempty"`
	Skills        []PreviewDispatchSkill `json:"skills,omitempty"`
	Queue         *PreviewDispatchQueue  `json:"queue,omitempty"`
}

type PreviewDispatchAgent struct {
	ID                 string                  `json:"id"`
	Name               string                  `json:"name"`
	Model              string                  `json:"model,omitempty"`
	InstructionsSet    bool                    `json:"instructions_set"`
	MaxConcurrentTasks int32                   `json:"max_concurrent_tasks"`
	Runtime            *PreviewDispatchRuntime `json:"runtime,omitempty"`
}

type PreviewDispatchRuntime struct {
	ID       string `json:"id"`
	Provider string `json:"provider"`
	Status   string `json:"status"`
	Online   bool   `json:"online"`
}

type PreviewDispatchSkill struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

type PreviewDispatchQueue struct {
	Queued             int64 `json:"queued"`
	Running            int64 `json:"running"`
	MaxConcurrentTasks int32 `json:"max_concurrent_tasks"`
}

// dispatchStatuses is the issue-status set that fires the dispatcher when an
// agent-assigned issue is created. Kept in sync with the CLI's
// dispatchStatuses (cmd/multica/cmd_issue.go): backlog is the parking lot,
// and done/blocked/cancelled never trigger a fresh run.
var dispatchStatuses = []string{"todo", "in_progress", "in_review"}

func statusInDispatchSet(status string) bool {
	for _, s := range dispatchStatuses {
		if s == status {
			return true
		}
	}
	return false
}

// PreviewDispatch handles POST /api/issues/preview-dispatch. It runs the same
// validation and read paths the create + claim flow uses (validateAssigneePair,
// GetAgentInWorkspace, ListAgentSkills — the query behind LoadAgentSkills —
// and GetAgentRuntime) and reports what a real create would dispatch: which
// agent, on which runtime/model, with which skills, and the agent's current
// queue depth. It is strictly read-only: no issue row, no task enqueue, no
// events.
func (h *Handler) PreviewDispatch(w http.ResponseWriter, r *http.Request) {
	var req PreviewDispatchRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if req.Title == "" {
		writeError(w, http.StatusBadRequest, "title is required")
		return
	}

	workspaceID := h.resolveWorkspaceID(r)
	wsUUID, ok := parseUUIDOrBadRequest(w, workspaceID, "workspace_id")
	if !ok {
		return
	}
	if _, ok := requireUserID(w, r); !ok {
		return
	}

	status := req.Status
	if status == "" {
		status = "todo"
	}
	priority := req.Priority
	if priority == "" {
		priority = "none"
	}

	var assigneeType pgtype.Text
	var assigneeID pgtype.UUID
	if req.AssigneeType != nil {
		assigneeType = pgtype.Text{String: *req.AssigneeType, Valid: true}
	}
	if req.AssigneeID != nil {
		id, ok := parseUUIDOrBadRequest(w, *req.AssigneeID, "assignee_id")
		if !ok {
			return
		}
		assigneeID = id
	}

	// Same pair validation as CreateIssue — unknown/archived assignees are
	// rejected here with a 400 instead of failing later at enqueue time.
	if code, msg := h.validateAssigneePair(r.Context(), r, workspaceID, assigneeType, assigneeID); code != 0 {
		writeError(w, code, msg)
		return
	}

	resp := PreviewDispatchResponse{
		Status:   status,
		Priority: priority,
	}

	if !assigneeType.Valid {
		resp.WouldDispatch = false
		resp.Reason = "issue is unassigned; nothing dispatches"
		writeJSON(w, http.StatusOK, resp)
		return
	}
	resp.AssigneeType = assigneeType.String
	resp.AssigneeID = uuidToString(assigneeID)

	switch assigneeType.String {
	case "member":
		resp.WouldDispatch = false
		resp.Reason = "assignee is a member; only agent assignees dispatch tasks"
		writeJSON(w, http.StatusOK, resp)
		return
	case "squad":
		resp.WouldDispatch = false
		resp.Reason = "assignee is a squad; the squad leader is triggered instead of a direct agent dispatch"
		writeJSON(w, http.StatusOK, resp)
		return
	}

	// Agent assignee: validateAssigneePair already proved the agent exists in
	// this workspace and is not archived, so a lookup failure here is a real
	// server error, not a 400.
	agent, err := h.Queries.GetAgentInWorkspace(r.Context(), db.GetAgentInWorkspaceParams{
		ID:          assigneeID,
		WorkspaceID: wsUUID,
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to load agent")
		return
	}

	previewAgent := &PreviewDispatchAgent{
		ID:                 uuidToString(agent.ID),
		Name:               agent.Name,
		Model:              agent.Model.String,
		InstructionsSet:    strings.TrimSpace(agent.Instructions) != "",
		MaxConcurrentTasks: agent.MaxConcurrentTasks,
	}
	if agent.RuntimeID.Valid {
		if rt, err := h.Queries.GetAgentRuntime(r.Context(), agent.RuntimeID); err == nil {
			previewAgent.Runtime = &PreviewDispatchRuntime{
				ID:       uuidToString(rt.ID),
				Provider: rt.Provider,
				Status:   rt.Status,
				Online:   rt.Status == "online", // mirrors isRuntimeOnline
			}
		}
	}
	resp.Agent = previewAgent

	// Same read path as the claim response (LoadAgentSkills), via the query it
	// wraps, so the previewed skill set is exactly what a claimed task carries.
	if skills, err := h.Queries.ListAgentSkills(r.Context(), agent.ID); err == nil {
		resp.Skills = make([]PreviewDispatchSkill, 0, len(skills))
		for _, sk := range skills {
			resp.Skills = append(resp.Skills, PreviewDispatchSkill{ID: uuidToString(sk.ID), Name: sk.Name})
		}
	}

	if depth, err := h.Queries.CountAgentQueueDepth(r.Context(), agent.ID); err == nil {
		resp.Queue = &PreviewDispatchQueue{
			Queued:             depth.Queued,
			Running:            depth.Running,
			MaxConcurrentTasks: agent.MaxConcurrentTasks,
		}
	}

	// Mirror the enqueue conditions: agent must have a runtime (enqueue fails
	// otherwise) and the status must be in the dispatcher's firing set. An
	// offline runtime does NOT block dispatch — tasks sit queued until the
	// runtime comes back.
	switch {
	case !agent.RuntimeID.Valid:
		resp.WouldDispatch = false
		resp.Reason = "agent has no runtime configured; enqueue would fail"
	case !statusInDispatchSet(status):
		resp.WouldDispatch = false
		resp.Reason = "status " + status + " is not in the dispatch set (" + strings.Join(dispatchStatuses, ", ") + ")"
	default:
		resp.WouldDispatch = true
	}

	writeJSON(w, http.StatusOK, resp)
}
