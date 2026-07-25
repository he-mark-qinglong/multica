-- gate_status gains a third value "no-data" (sharpe absent → nothing to
-- judge) alongside "pass"/"fail". Migration 123 added the column without a
-- CHECK constraint, so the DROP below is a defensive no-op on fresh
-- databases; the ADD is the real change. The partial index from 123
-- (idx_run_metric_workspace_gate) already covers all three values.
ALTER TABLE run_metric
    DROP CONSTRAINT IF EXISTS run_metric_gate_status_check;

ALTER TABLE run_metric
    ADD CONSTRAINT run_metric_gate_status_check
    CHECK (gate_status IS NULL OR gate_status IN ('pass', 'fail', 'no-data'));
