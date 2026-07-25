-- Revert: drop the wait_reason CHECK constraint and autopilot fairness index.
-- Note: this leaves the wait_reason TEXT column in place — migration 109 owns
-- the original column add, and re-adding it here would collide with the
-- upstream migration on re-apply.
DROP INDEX IF EXISTS idx_agent_task_queue_runtime_autopilot_queued;

ALTER TABLE agent_task_queue
    DROP CONSTRAINT IF EXISTS agent_task_queue_wait_reason_queued_check;