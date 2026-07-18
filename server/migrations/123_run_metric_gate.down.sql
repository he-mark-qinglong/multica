DROP INDEX IF EXISTS idx_run_metric_workspace_gate;

ALTER TABLE run_metric
    DROP COLUMN IF EXISTS gate_detail,
    DROP COLUMN IF EXISTS gate_status;
