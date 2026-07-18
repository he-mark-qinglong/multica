package handler

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5/pgtype"
	"github.com/multica-ai/multica/server/internal/gate"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// ---------------------------------------------------------------------------
// Metrics ingestion: parse a kind=metrics artifact's JSON blob into the
// columnar fields of a run_metric row. Liberal on key aliases, exact on
// types — a known key holding a non-number/non-string of the wrong shape is
// ignored; unknown keys land in Extra. Anything that is not a JSON object
// yields ok=false so the caller stores the artifact but skips the row.
// ---------------------------------------------------------------------------

// runMetricFields is the parsed content of a kind=metrics blob. Nil pointers
// mean "absent or wrong type in the blob" and map to SQL NULL.
type runMetricFields struct {
	Sharpe       *float64
	Sortino      *float64
	Calmar       *float64
	AnnReturn    *float64
	MaxDrawdown  *float64
	ProfitFactor *float64
	OOSSharpe    *float64
	OOSWindows   *int32
	Timeframe    string
	Symbols      []string
	Params       json.RawMessage
	Extra        json.RawMessage
}

// metricFloatAliases maps each numeric run_metric column to the JSON keys
// agents use for it, most canonical first.
var metricFloatAliases = map[string][]string{
	"sharpe":        {"sharpe", "sharpe_ratio"},
	"sortino":       {"sortino", "sortino_ratio"},
	"calmar":        {"calmar", "calmar_ratio"},
	"ann_return":    {"ann_return", "ann", "annualized", "annualized_return"},
	"max_drawdown":  {"max_drawdown", "mdd", "max_dd", "maxdrawdown"},
	"profit_factor": {"profit_factor", "pf"},
	"oos_sharpe":    {"oos_sharpe", "oos"},
}

// metricKnownKeys is every blob key consumed by a column (so the rest falls
// into Extra). Populated from metricFloatAliases plus the non-float keys.
var metricKnownKeys = func() map[string]bool {
	known := map[string]bool{
		"oos_windows": true,
		"timeframe":   true, "tf": true,
		"symbols": true, "symbol": true,
		"params": true,
	}
	for _, aliases := range metricFloatAliases {
		for _, k := range aliases {
			known[k] = true
		}
	}
	return known
}()

// jsonNumber coerces a JSON value to float64, accepting only real JSON
// numbers (exact on types: a quoted "1.5" string is not a number).
func jsonNumber(v any) (float64, bool) {
	f, ok := v.(float64)
	return f, ok
}

// parseRunMetricJSON parses blob bytes into runMetricFields. ok=false means
// the blob was not a JSON object and no metric row should be created.
func parseRunMetricJSON(data []byte) (fields *runMetricFields, ok bool) {
	var obj map[string]any
	if err := json.Unmarshal(data, &obj); err != nil || obj == nil {
		return nil, false
	}

	f := &runMetricFields{}
	floatCols := map[string]**float64{
		"sharpe":        &f.Sharpe,
		"sortino":       &f.Sortino,
		"calmar":        &f.Calmar,
		"ann_return":    &f.AnnReturn,
		"max_drawdown":  &f.MaxDrawdown,
		"profit_factor": &f.ProfitFactor,
		"oos_sharpe":    &f.OOSSharpe,
	}
	for col, aliases := range metricFloatAliases {
		for _, key := range aliases {
			if v, present := obj[key]; present {
				if n, isNum := jsonNumber(v); isNum {
					*floatCols[col] = &n
				}
				break // first matching alias wins, even if wrong-typed
			}
		}
	}

	if v, present := obj["oos_windows"]; present {
		if n, isNum := jsonNumber(v); isNum {
			w := int32(n)
			f.OOSWindows = &w
		}
	}
	if v, present := obj["timeframe"]; present {
		if s, isStr := v.(string); isStr {
			f.Timeframe = s
		}
	} else if v, present := obj["tf"]; present {
		if s, isStr := v.(string); isStr {
			f.Timeframe = s
		}
	}
	if v, present := obj["symbols"]; present {
		if arr, isArr := v.([]any); isArr {
			for _, item := range arr {
				if s, isStr := item.(string); isStr {
					f.Symbols = append(f.Symbols, s)
				}
			}
		}
	} else if v, present := obj["symbol"]; present {
		if s, isStr := v.(string); isStr {
			f.Symbols = []string{s}
		}
	}
	if v, present := obj["params"]; present {
		if raw, err := json.Marshal(v); err == nil {
			f.Params = raw
		}
	}

	extra := map[string]any{}
	for k, v := range obj {
		if !metricKnownKeys[k] {
			extra[k] = v
		}
	}
	if raw, err := json.Marshal(extra); err == nil {
		f.Extra = raw
	}
	return f, true
}

// campaignIterationFromMeta extracts campaign / iteration from an artifact's
// meta JSON object. Iteration is stringified leniently ("83" and 83 both
// become "83") since the column is TEXT by design.
func campaignIterationFromMeta(meta []byte) (campaign, iteration string) {
	var obj map[string]any
	if err := json.Unmarshal(meta, &obj); err != nil || obj == nil {
		return "", ""
	}
	if s, ok := obj["campaign"].(string); ok {
		campaign = s
	}
	switch v := obj["iteration"].(type) {
	case string:
		iteration = v
	case float64:
		iteration = strconv.FormatFloat(v, 'f', -1, 64)
	}
	return campaign, iteration
}

func float8FromPtr(p *float64) pgtype.Float8 {
	if p == nil {
		return pgtype.Float8{}
	}
	return pgtype.Float8{Float64: *p, Valid: true}
}

func int4FromPtr(p *int32) pgtype.Int4 {
	if p == nil {
		return pgtype.Int4{}
	}
	return pgtype.Int4{Int32: *p, Valid: true}
}

func float8ToPtr(f pgtype.Float8) *float64 {
	if !f.Valid {
		return nil
	}
	v := f.Float64
	return &v
}

// gateMetricsFromFields maps parsed blob fields onto the gate evaluator's
// input shape (nil pointer = absent = rule skipped).
func gateMetricsFromFields(f *runMetricFields) gate.Metrics {
	return gate.Metrics{
		Sharpe:       f.Sharpe,
		AnnReturn:    f.AnnReturn,
		MaxDrawdown:  f.MaxDrawdown,
		ProfitFactor: f.ProfitFactor,
		OOSSharpe:    f.OOSSharpe,
		OOSWindows:   f.OOSWindows,
	}
}

// gateMetricsFromRow maps a stored run_metric row onto the evaluator input
// (used by the re-evaluate endpoint, where the blob is long gone).
func gateMetricsFromRow(m db.RunMetric) gate.Metrics {
	var g gate.Metrics
	g.Sharpe = float8ToPtr(m.Sharpe)
	g.AnnReturn = float8ToPtr(m.AnnReturn)
	g.MaxDrawdown = float8ToPtr(m.MaxDrawdown)
	g.ProfitFactor = float8ToPtr(m.ProfitFactor)
	g.OOSSharpe = float8ToPtr(m.OosSharpe)
	if m.OosWindows.Valid {
		w := m.OosWindows.Int32
		g.OOSWindows = &w
	}
	return g
}

// persistGate evaluates the hard gates and stores the result on the row.
// An empty status (insufficient data) leaves the gate columns NULL; a store
// failure is logged but never fails the caller — gates can be recomputed via
// POST /api/metrics/reevaluate.
func (h *Handler) persistGate(ctx context.Context, id pgtype.UUID, gm gate.Metrics) {
	status, detail := gate.Evaluate(gm)
	if status == "" {
		return
	}
	detailJSON, err := json.Marshal(detail)
	if err != nil {
		slog.Error("failed to marshal gate detail", "error", err)
		return
	}
	if err := h.Queries.SetRunMetricGate(ctx, id,
		pgtype.Text{String: status, Valid: true}, detailJSON); err != nil {
		slog.Error("failed to store gate result", "metric_id", uuidToString(id), "error", err)
	}
}

// ingestRunMetric parses a kind=metrics artifact's blob and inserts the
// run_metric row. It never fails the upload: any parse or insert problem is
// logged and reported via the returned bool (false → metrics_ingested:false
// in the upload response).
func (h *Handler) ingestRunMetric(ctx context.Context, artifact db.Artifact, data []byte) bool {
	fields, ok := parseRunMetricJSON(data)
	if !ok {
		return false
	}
	campaign, iteration := campaignIterationFromMeta(artifact.Meta)
	params := fields.Params
	if len(params) == 0 {
		params = json.RawMessage(`{}`)
	}
	extra := fields.Extra
	if len(extra) == 0 {
		extra = json.RawMessage(`{}`)
	}

	created, err := h.Queries.CreateRunMetric(ctx, db.CreateRunMetricParams{
		WorkspaceID:  artifact.WorkspaceID,
		ArtifactID:   artifact.ID,
		TaskID:       artifact.TaskID,
		IssueID:      artifact.IssueID,
		Campaign:     campaign,
		Iteration:    iteration,
		Sharpe:       float8FromPtr(fields.Sharpe),
		Sortino:      float8FromPtr(fields.Sortino),
		Calmar:       float8FromPtr(fields.Calmar),
		AnnReturn:    float8FromPtr(fields.AnnReturn),
		MaxDrawdown:  float8FromPtr(fields.MaxDrawdown),
		ProfitFactor: float8FromPtr(fields.ProfitFactor),
		OosSharpe:    float8FromPtr(fields.OOSSharpe),
		OosWindows:   int4FromPtr(fields.OOSWindows),
		Timeframe:    fields.Timeframe,
		Symbols:      fields.Symbols,
		Params:       params,
		Extra:        extra,
	})
	if err != nil {
		slog.Error("failed to ingest run metric", "artifact_id", uuidToString(artifact.ID), "error", err)
		return false
	}
	h.persistGate(ctx, created.ID, gateMetricsFromFields(fields))
	return true
}

// artifactUploadResponse extends the artifact response for kind=metrics
// uploads with whether a queryable run_metric row was also created.
type artifactUploadResponse struct {
	ArtifactResponse
	MetricsIngested bool `json:"metrics_ingested"`
}

// ---------------------------------------------------------------------------
// Query API
// ---------------------------------------------------------------------------

// RunMetricResponse is the API shape of one run_metric row. Optional numbers
// are pointers so absent values render as JSON null, not 0.
type RunMetricResponse struct {
	ID           string          `json:"id"`
	WorkspaceID  string          `json:"workspace_id"`
	ArtifactID   string          `json:"artifact_id"`
	TaskID       *string         `json:"task_id"`
	IssueID      *string         `json:"issue_id"`
	Campaign     string          `json:"campaign"`
	Iteration    string          `json:"iteration"`
	Sharpe       *float64        `json:"sharpe"`
	Sortino      *float64        `json:"sortino"`
	Calmar       *float64        `json:"calmar"`
	AnnReturn    *float64        `json:"ann_return"`
	MaxDrawdown  *float64        `json:"max_drawdown"`
	ProfitFactor *float64        `json:"profit_factor"`
	OOSSharpe    *float64        `json:"oos_sharpe"`
	OOSWindows   *int32          `json:"oos_windows"`
	Timeframe    string          `json:"timeframe"`
	Symbols      []string        `json:"symbols"`
	Params       json.RawMessage `json:"params"`
	Extra        json.RawMessage `json:"extra"`
	// GateStatus is "pass" | "fail" | null (null = not evaluated /
	// insufficient data). GateDetail is the per-rule array produced by
	// server/internal/gate, or null when GateStatus is null.
	GateStatus *string         `json:"gate_status"`
	GateDetail json.RawMessage `json:"gate_detail"`
	CreatedAt  string          `json:"created_at"`
}

func runMetricToResponse(m db.RunMetric) RunMetricResponse {
	resp := RunMetricResponse{
		ID:           uuidToString(m.ID),
		WorkspaceID:  uuidToString(m.WorkspaceID),
		ArtifactID:   uuidToString(m.ArtifactID),
		Campaign:     m.Campaign,
		Iteration:    m.Iteration,
		Sharpe:       float8ToPtr(m.Sharpe),
		Sortino:      float8ToPtr(m.Sortino),
		Calmar:       float8ToPtr(m.Calmar),
		AnnReturn:    float8ToPtr(m.AnnReturn),
		MaxDrawdown:  float8ToPtr(m.MaxDrawdown),
		ProfitFactor: float8ToPtr(m.ProfitFactor),
		OOSSharpe:    float8ToPtr(m.OosSharpe),
		Timeframe:    m.Timeframe,
		Symbols:      m.Symbols,
		Params:       m.Params,
		Extra:        m.Extra,
		CreatedAt:    m.CreatedAt.Time.Format("2006-01-02T15:04:05Z07:00"),
	}
	if m.OosWindows.Valid {
		w := m.OosWindows.Int32
		resp.OOSWindows = &w
	}
	if m.TaskID.Valid {
		s := uuidToString(m.TaskID)
		resp.TaskID = &s
	}
	if m.IssueID.Valid {
		s := uuidToString(m.IssueID)
		resp.IssueID = &s
	}
	if len(resp.Params) == 0 {
		resp.Params = json.RawMessage(`{}`)
	}
	if len(resp.Extra) == 0 {
		resp.Extra = json.RawMessage(`{}`)
	}
	if m.GateStatus.Valid {
		s := m.GateStatus.String
		resp.GateStatus = &s
	}
	if len(m.GateDetail) > 0 {
		resp.GateDetail = json.RawMessage(m.GateDetail)
	}
	if resp.Symbols == nil {
		resp.Symbols = []string{}
	}
	return resp
}

// QueryRunMetrics — GET /api/metrics/query?campaign=&issue_id=&task_id=&limit=&offset=
func (h *Handler) QueryRunMetrics(w http.ResponseWriter, r *http.Request) {
	workspaceID := h.resolveWorkspaceID(r)
	wsUUID, ok := parseUUIDOrBadRequest(w, workspaceID, "workspace_id")
	if !ok {
		return
	}

	var campaignFilter pgtype.Text
	if c := strings.TrimSpace(r.URL.Query().Get("campaign")); c != "" {
		campaignFilter = pgtype.Text{String: c, Valid: true}
	}
	var issueFilter pgtype.UUID
	if i := r.URL.Query().Get("issue_id"); i != "" {
		id, ok := parseUUIDOrBadRequest(w, i, "issue_id")
		if !ok {
			return
		}
		issueFilter = id
	}
	var taskFilter pgtype.UUID
	if t := r.URL.Query().Get("task_id"); t != "" {
		id, ok := parseUUIDOrBadRequest(w, t, "task_id")
		if !ok {
			return
		}
		taskFilter = id
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

	metrics, err := h.Queries.QueryRunMetrics(r.Context(), db.QueryRunMetricsParams{
		WorkspaceID: wsUUID,
		Campaign:    campaignFilter,
		IssueID:     issueFilter,
		TaskID:      taskFilter,
		Limit:       int32(limit),
		Offset:      int32(offset),
	})
	if err != nil {
		slog.Error("failed to query run metrics", "error", err)
		writeError(w, http.StatusInternalServerError, "failed to query metrics")
		return
	}

	resp := make([]RunMetricResponse, len(metrics))
	for i, m := range metrics {
		resp[i] = runMetricToResponse(m)
	}
	writeJSON(w, http.StatusOK, map[string]any{"metrics": resp})
}

// ListMetricCampaigns — GET /api/metrics/campaigns
func (h *Handler) ListMetricCampaigns(w http.ResponseWriter, r *http.Request) {
	workspaceID := h.resolveWorkspaceID(r)
	wsUUID, ok := parseUUIDOrBadRequest(w, workspaceID, "workspace_id")
	if !ok {
		return
	}

	campaigns, err := h.Queries.ListRunMetricCampaigns(r.Context(), wsUUID)
	if err != nil {
		slog.Error("failed to list metric campaigns", "error", err)
		writeError(w, http.StatusInternalServerError, "failed to list campaigns")
		return
	}
	if campaigns == nil {
		campaigns = []string{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"campaigns": campaigns})
}

// ---------------------------------------------------------------------------
// Gate re-evaluation
// ---------------------------------------------------------------------------

// reevaluateMetricsRequest is the optional JSON body for
// POST /api/metrics/reevaluate. Empty body / empty fields = all rows in the
// workspace.
type reevaluateMetricsRequest struct {
	Campaign string `json:"campaign"`
	IssueID  string `json:"issue_id"`
}

// ReevaluateRunMetrics — POST /api/metrics/reevaluate
//
// Recomputes hard gates (server/internal/gate) over stored run_metric rows.
// Useful when the rule set changes: ingest only evaluates new rows, this
// backfills old ones. Per-row failures are logged and counted, never fatal.
func (h *Handler) ReevaluateRunMetrics(w http.ResponseWriter, r *http.Request) {
	workspaceID := h.resolveWorkspaceID(r)
	wsUUID, ok := parseUUIDOrBadRequest(w, workspaceID, "workspace_id")
	if !ok {
		return
	}

	var req reevaluateMetricsRequest
	if r.Body != nil {
		// An empty body is valid (means "all rows"); only a malformed
		// non-empty body is an error.
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil && err.Error() != "EOF" {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}
	}

	var campaignFilter pgtype.Text
	if c := strings.TrimSpace(req.Campaign); c != "" {
		campaignFilter = pgtype.Text{String: c, Valid: true}
	}
	var issueFilter pgtype.UUID
	if i := strings.TrimSpace(req.IssueID); i != "" {
		id, ok := parseUUIDOrBadRequest(w, i, "issue_id")
		if !ok {
			return
		}
		issueFilter = id
	}

	metrics, err := h.Queries.ListRunMetricsForGateReeval(r.Context(), db.ListRunMetricsForGateReevalParams{
		WorkspaceID: wsUUID,
		Campaign:    campaignFilter,
		IssueID:     issueFilter,
	})
	if err != nil {
		slog.Error("failed to list run metrics for reevaluation", "error", err)
		writeError(w, http.StatusInternalServerError, "failed to list metrics")
		return
	}

	counts := map[string]int{"pass": 0, "fail": 0, "skipped": 0, "errors": 0}
	for _, m := range metrics {
		status, detail := gate.Evaluate(gateMetricsFromRow(m))
		if status == "" {
			counts["skipped"]++
			continue
		}
		detailJSON, err := json.Marshal(detail)
		if err != nil {
			slog.Error("failed to marshal gate detail", "metric_id", uuidToString(m.ID), "error", err)
			counts["errors"]++
			continue
		}
		if err := h.Queries.SetRunMetricGate(r.Context(), m.ID,
			pgtype.Text{String: status, Valid: true}, detailJSON); err != nil {
			slog.Error("failed to store gate result", "metric_id", uuidToString(m.ID), "error", err)
			counts["errors"]++
			continue
		}
		counts[status]++
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"reevaluated": counts["pass"] + counts["fail"],
		"pass":        counts["pass"],
		"fail":        counts["fail"],
		"skipped":     counts["skipped"],
		"errors":      counts["errors"],
	})
}
