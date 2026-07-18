-- Hard-gate evaluation results for run_metric rows, computed at ingest time
-- by server/internal/gate (rule set hardcoded in P1; config in P2).
-- gate_status: 'pass' | 'fail' | NULL (NULL = not evaluated / insufficient
-- data, e.g. sharpe missing). gate_detail: JSONB array of per-rule results
-- ({rule, op, threshold, actual, pass, note?}) in evaluator rule order;
-- NULL whenever gate_status is NULL. Recomputed on demand via
-- POST /api/metrics/reevaluate.
ALTER TABLE run_metric
    ADD COLUMN gate_status TEXT,
    ADD COLUMN gate_detail JSONB;

CREATE INDEX idx_run_metric_workspace_gate ON run_metric(workspace_id, gate_status)
    WHERE gate_status IS NOT NULL;
