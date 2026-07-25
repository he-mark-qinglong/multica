-- Document and lock down the wait_reason values used for status='queued'
-- agent_task_queue rows. The column already exists (added in 109 for the
-- waiting_local_directory status); this migration codifies the values that
-- the queued-task classifier in TaskService.ClassifyWaitReason is allowed
-- to write so the wire format stays stable across daemon versions and the
-- CLI / frontend can rely on the enum.
--
-- Values (queued only):
--   waiting_runtime    — target runtime offline (no fresh heartbeat)
--   waiting_dependency — issue has unresolved blocker dependency (type='blocks')
--   waiting_agent      — agent at max_concurrent_tasks (CountRunningTasks >= limit)
--   waiting_capacity   — daemon global slot full, or no eligible agent/runtime
--                        match for this task (catch-all upstream-cause bucket)
--
-- Other statuses keep their existing wait_reason semantics:
--   waiting_local_directory: human-readable path of the contested local dir
--   dispatched/running/...: NULL
--
-- We do NOT change the column type or add a NOT NULL constraint because the
-- column is shared with waiting_local_directory rows, where the value is a
-- free-form path string. The CHECK is additive (OR-style) and NULL-tolerant.
--
-- The autopilot partial index speeds up the fairness path of
-- ListQueuedClaimCandidatesByRuntime / ClaimAgentTask, both of which order by
-- (autopilot_run_id IS NOT NULL) DESC under contention to prevent L1 autopilot
-- dispatches from being starved by L3 execution batches (R2 of SMA-36539).

ALTER TABLE agent_task_queue
    DROP CONSTRAINT IF EXISTS agent_task_queue_wait_reason_queued_check;
ALTER TABLE agent_task_queue
    ADD CONSTRAINT agent_task_queue_wait_reason_queued_check
    CHECK (
        wait_reason IS NULL
        OR wait_reason IN (
            'waiting_runtime',
            'waiting_dependency',
            'waiting_agent',
            'waiting_capacity'
        )
        OR wait_reason ~ '^(/|[A-Za-z]:[\\\\/])'  -- local_directory path hint
    );

CREATE INDEX IF NOT EXISTS idx_agent_task_queue_runtime_autopilot_queued
    ON agent_task_queue(runtime_id, autopilot_run_id)
    WHERE status = 'queued';