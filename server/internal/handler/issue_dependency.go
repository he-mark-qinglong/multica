package handler

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// Dependency types the map view (and this API) lets users create. The DB
// CHECK constraint allows more types written internally by agents; the
// user-facing create path is deliberately narrower.
var validIssueDependencyTypes = []string{"blocks", "related", "supersedes"}

type ProjectGraphNode struct {
	ID            string  `json:"id"`
	Identifier    string  `json:"identifier"`
	Title         string  `json:"title"`
	Status        string  `json:"status"`
	Priority      string  `json:"priority"`
	ParentIssueID *string `json:"parent_issue_id"`
}

type ProjectGraphEdge struct {
	ID               string `json:"id"`
	IssueID          string `json:"issue_id"`
	DependsOnIssueID string `json:"depends_on_issue_id"`
	Type             string `json:"type"`
}

type ProjectGraphResponse struct {
	Nodes []ProjectGraphNode `json:"nodes"`
	Edges []ProjectGraphEdge `json:"edges"`
}

// GetProjectGraph returns every issue of the project as a node (isolated
// issues included) plus every intra-project dependency edge, for the
// project map view.
func (h *Handler) GetProjectGraph(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	idUUID, ok := parseUUIDOrBadRequest(w, chi.URLParam(r, "id"), "project id")
	if !ok {
		return
	}
	wsUUID, ok := parseUUIDOrBadRequest(w, h.resolveWorkspaceID(r), "workspace id")
	if !ok {
		return
	}
	if _, err := h.Queries.GetProjectInWorkspace(ctx, db.GetProjectInWorkspaceParams{
		ID: idUUID, WorkspaceID: wsUUID,
	}); err != nil {
		writeError(w, http.StatusNotFound, "project not found")
		return
	}

	issues, err := h.Queries.ListProjectGraphIssues(ctx, db.ListProjectGraphIssuesParams{
		ProjectID:   idUUID,
		WorkspaceID: wsUUID,
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list project issues")
		return
	}
	deps, err := h.Queries.ListProjectGraphDependencies(ctx, db.ListProjectGraphDependenciesParams{
		ProjectID:   idUUID,
		WorkspaceID: wsUUID,
	})
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list project dependencies")
		return
	}

	prefix := h.getIssuePrefix(ctx, wsUUID)
	nodes := make([]ProjectGraphNode, len(issues))
	for i, issue := range issues {
		nodes[i] = ProjectGraphNode{
			ID:            uuidToString(issue.ID),
			Identifier:    fmt.Sprintf("%s-%d", prefix, issue.Number),
			Title:         issue.Title,
			Status:        issue.Status,
			Priority:      issue.Priority,
			ParentIssueID: uuidToPtr(issue.ParentIssueID),
		}
	}
	edges := make([]ProjectGraphEdge, len(deps))
	for i, dep := range deps {
		edges[i] = ProjectGraphEdge{
			ID:               uuidToString(dep.ID),
			IssueID:          uuidToString(dep.IssueID),
			DependsOnIssueID: uuidToString(dep.DependsOnIssueID),
			Type:             dep.Type,
		}
	}
	writeJSON(w, http.StatusOK, ProjectGraphResponse{Nodes: nodes, Edges: edges})
}

type CreateIssueDependencyRequest struct {
	DependsOnIssueID string `json:"depends_on_issue_id"`
	Type             string `json:"type"`
}

func (h *Handler) CreateIssueDependency(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	issueUUID, ok := parseUUIDOrBadRequest(w, chi.URLParam(r, "id"), "issue id")
	if !ok {
		return
	}
	wsUUID, ok := parseUUIDOrBadRequest(w, h.resolveWorkspaceID(r), "workspace id")
	if !ok {
		return
	}
	var req CreateIssueDependencyRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	validType := false
	for _, t := range validIssueDependencyTypes {
		if req.Type == t {
			validType = true
			break
		}
	}
	if !validType {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("invalid type %q; valid values: %s", req.Type, strings.Join(validIssueDependencyTypes, ", ")))
		return
	}
	depUUID, ok := parseUUIDOrBadRequest(w, req.DependsOnIssueID, "depends_on_issue_id")
	if !ok {
		return
	}
	if depUUID == issueUUID {
		writeError(w, http.StatusBadRequest, "an issue cannot depend on itself")
		return
	}
	// Both issues must live in the same workspace — cross-workspace edges
	// would leak existence across tenant boundaries.
	if _, err := h.Queries.GetIssueInWorkspace(ctx, db.GetIssueInWorkspaceParams{ID: issueUUID, WorkspaceID: wsUUID}); err != nil {
		writeError(w, http.StatusNotFound, "issue not found")
		return
	}
	if _, err := h.Queries.GetIssueInWorkspace(ctx, db.GetIssueInWorkspaceParams{ID: depUUID, WorkspaceID: wsUUID}); err != nil {
		writeError(w, http.StatusNotFound, "depends-on issue not found")
		return
	}
	// Duplicate edge → 409 so the client can tell "already exists" apart
	// from a real failure.
	if _, err := h.Queries.GetIssueDependencyByEndpoints(ctx, db.GetIssueDependencyByEndpointsParams{
		IssueID:          issueUUID,
		DependsOnIssueID: depUUID,
		Type:             req.Type,
	}); err == nil {
		writeError(w, http.StatusConflict, "dependency already exists")
		return
	} else if !errors.Is(err, pgx.ErrNoRows) {
		writeError(w, http.StatusInternalServerError, "failed to check existing dependency")
		return
	}
	dep, err := h.Queries.CreateIssueDependency(ctx, db.CreateIssueDependencyParams{
		IssueID:          issueUUID,
		DependsOnIssueID: depUUID,
		Type:             req.Type,
	})
	if err != nil {
		if isCheckViolation(err) {
			writeError(w, http.StatusBadRequest, "dependency rejected by database constraint")
			return
		}
		writeError(w, http.StatusInternalServerError, "failed to create dependency")
		return
	}
	writeJSON(w, http.StatusCreated, ProjectGraphEdge{
		ID:               uuidToString(dep.ID),
		IssueID:          uuidToString(dep.IssueID),
		DependsOnIssueID: uuidToString(dep.DependsOnIssueID),
		Type:             dep.Type,
	})
}

func (h *Handler) DeleteIssueDependency(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	issueUUID, ok := parseUUIDOrBadRequest(w, chi.URLParam(r, "id"), "issue id")
	if !ok {
		return
	}
	depUUID, ok := parseUUIDOrBadRequest(w, chi.URLParam(r, "depId"), "dependency id")
	if !ok {
		return
	}
	wsUUID, ok := parseUUIDOrBadRequest(w, h.resolveWorkspaceID(r), "workspace id")
	if !ok {
		return
	}
	dep, err := h.Queries.GetIssueDependency(ctx, depUUID)
	if errors.Is(err, pgx.ErrNoRows) {
		writeError(w, http.StatusNotFound, "dependency not found")
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to load dependency")
		return
	}
	// The path issue must be an endpoint of the edge and live in this
	// workspace; otherwise the delete would cross tenant boundaries.
	if dep.IssueID != issueUUID && dep.DependsOnIssueID != issueUUID {
		writeError(w, http.StatusNotFound, "dependency not found")
		return
	}
	if _, err := h.Queries.GetIssueInWorkspace(ctx, db.GetIssueInWorkspaceParams{ID: issueUUID, WorkspaceID: wsUUID}); err != nil {
		writeError(w, http.StatusNotFound, "issue not found")
		return
	}
	if err := h.Queries.DeleteIssueDependency(ctx, depUUID); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to delete dependency")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

type IssueDependencyEntry struct {
	ID                    string `json:"id"`
	IssueID               string `json:"issue_id"`
	DependsOnIssueID      string `json:"depends_on_issue_id"`
	Type                  string `json:"type"`
	CounterpartID         string `json:"counterpart_id"`
	CounterpartIdentifier string `json:"counterpart_identifier"`
	CounterpartTitle      string `json:"counterpart_title"`
}

// ListIssueDependencies returns every dependency edge touching the issue in
// either direction, with the other-end issue's identifier/title inlined so
// callers don't need a second lookup per edge.
func (h *Handler) ListIssueDependencies(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	issueUUID, ok := parseUUIDOrBadRequest(w, chi.URLParam(r, "id"), "issue id")
	if !ok {
		return
	}
	wsUUID, ok := parseUUIDOrBadRequest(w, h.resolveWorkspaceID(r), "workspace id")
	if !ok {
		return
	}
	if _, err := h.Queries.GetIssueInWorkspace(ctx, db.GetIssueInWorkspaceParams{ID: issueUUID, WorkspaceID: wsUUID}); err != nil {
		writeError(w, http.StatusNotFound, "issue not found")
		return
	}
	rows, err := h.Queries.ListIssueDependencies(ctx, issueUUID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list dependencies")
		return
	}
	prefix := h.getIssuePrefix(ctx, wsUUID)
	resp := make([]IssueDependencyEntry, len(rows))
	for i, row := range rows {
		resp[i] = IssueDependencyEntry{
			ID:                    uuidToString(row.ID),
			IssueID:               uuidToString(row.IssueID),
			DependsOnIssueID:      uuidToString(row.DependsOnIssueID),
			Type:                  row.Type,
			CounterpartID:         uuidToString(row.CounterpartID),
			CounterpartIdentifier: fmt.Sprintf("%s-%d", prefix, row.CounterpartNumber),
			CounterpartTitle:      row.CounterpartTitle,
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"dependencies": resp})
}
