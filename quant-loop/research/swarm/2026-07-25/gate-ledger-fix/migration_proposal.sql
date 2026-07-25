-- PROPOSAL (gate-ledger-fix, 2026-07-25):
-- Update the gate_status CHECK constraint to accept the new "no-data" value.
-- The existing index covers pass/fail/no-data equally because it only filters
-- NULL values.

-- If the column was created without a CHECK constraint, add one now.
-- Adjust the migration id to follow the project's sequence.
ALTER TABLE run_metric
    DROP CONSTRAINT IF EXISTS run_metric_gate_status_check;

ALTER TABLE run_metric
    ADD CONSTRAINT run_metric_gate_status_check
    CHECK (gate_status IS NULL OR gate_status IN ('pass', 'fail', 'no-data'));

-- Optional: backfill any existing rows whose gate_status is now stale.
-- This is normally done via POST /api/metrics/reevaluate after deploy, but
-- a one-shot SQL update can be used if the gate_detail JSON shape must stay
-- in sync immediately:
--
--   UPDATE run_metric
--   SET gate_status = NULL, gate_detail = NULL
--   WHERE gate_status IS NOT NULL;
--
-- Then call the re-evaluate endpoint to repopulate.
