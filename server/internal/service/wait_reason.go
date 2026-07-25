// wait_reason classification for queued agent_task_queue rows (SMA-36539 R1).
//
// Four-valued enum that the daemon dequeue loop can already compute locally,
// but lifting it to the server lets `GET /api/tasks?status=queued` show
// operators *why* a task is stuck without forcing every caller to inspect
// runtime heartbeat, agent capacity, and dependency state in three separate
// places. The classifier (waitreason.Classify) is a pure function on
// prefetched facts so the handler can drive it with a single DB roundtrip
// per fact kind:
//
//	1. issue blocker counts (bulk ListIssuesWithOpenBlockers)
//	2. runtime freshness (bulk ListAgentRuntimeFreshness)
//	3. per-agent running task count (CountRunningTasks)
//	4. per-(agent, runtime) queued ahead (ListQueuedClaimCandidatesByRuntimeForAgent)
//
// Evaluation order — runtime → dependency → agent → capacity — reports the
// most upstream cause first, so a task blocked by an offline daemon always
// reads "waiting_runtime" even when the agent is also at capacity.
package service

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5/pgtype"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
	"github.com/multica-ai/multica/server/internal/waitreason"
)

// WaitReason* constants are re-exported here so callers in service/ don't
// have to import the internal package just to read the wire values.
const (
	WaitReasonRuntime    = waitreason.WaitReasonRuntime
	WaitReasonDependency = waitreason.WaitReasonDependency
	WaitReasonAgent      = waitreason.WaitReasonAgent
	WaitReasonCapacity   = waitreason.WaitReasonCapacity
)

// ClassifyWaitReason is the thin pass-through to waitreason.Classify.
// Kept as a service method so existing callers in handler/ can keep using
// the TaskService method, and so the per-task variant below (which does
// the DB lookups) reads naturally.
func (s *TaskService) ClassifyWaitReason(task db.AgentTaskQueue, in waitreason.Inputs) string {
	return waitreason.Classify(in)
}

// ClassifyWaitReasonForTask is the convenience wrapper the handler calls
// per row. It uses TaskService.Queries (sqlc-generated) to resolve the
// three inputs and applies the pure classifier. Failures degrade
// gracefully: missing data yields the most pessimistic reason (so
// operators see the stale "waiting_runtime" instead of a false "ready").
func (s *TaskService) ClassifyWaitReasonForTask(ctx context.Context, task db.AgentTaskQueue) string {
	if task.Status != "queued" {
		return ""
	}

	in := waitreason.Inputs{Task: task, AgentMaxSlots: 1}

	// 1. Runtime freshness — cover both "row missing" (runtime deleted) and
	//    "row present but stale or status=offline" as offline.
	if task.RuntimeID.Valid {
		fresh, err := s.Queries.GetAgentRuntimeFreshness(ctx, task.RuntimeID)
		if err != nil {
			in.RuntimeOnline = false
		} else {
			in.RuntimeOnline = waitreason.RuntimeOnlineNow(fresh.Status, fresh.LastSeenAt.Time, time.Now(), fresh.LastSeenAt.Valid)
		}
	} else {
		// No runtime_id means the row is not yet bound to a runtime — it
		// could be a non-runtime task (chat/quick-create) or one whose
		// runtime target has not been picked. Treat as "runtime not online"
		// because there is literally nothing the daemon could claim against.
		in.RuntimeOnline = false
	}

	// 2. Open blocker count for the linked issue. Chat / quick-create tasks
	//    have no issue_id and never gate on dependencies.
	if task.IssueID.Valid {
		if n, err := s.Queries.CountOpenBlockersForIssue(ctx, task.IssueID); err == nil {
			in.OpenBlockerCount = int(n)
		}
	}

	// 3. Agent capacity. CountRunningTasks includes dispatched/running/
	//    waiting_local_directory, which matches what ClaimTask's gate uses.
	if task.AgentID.Valid {
		if n, err := s.Queries.CountRunningTasks(ctx, task.AgentID); err == nil {
			in.AgentRunningCount = n
		}
		if a, err := s.Queries.GetAgent(ctx, task.AgentID); err == nil {
			in.AgentMaxSlots = a.MaxConcurrentTasks
		}
	}

	// 4. Other queued tasks ahead of this one for the same agent. The
	//    "excess over agent capacity" case is the strongest signal that the
	//    daemon slot, not an upstream cause, is the gate.
	if task.AgentID.Valid {
		others, err := s.Queries.ListQueuedClaimCandidatesByRuntimeForAgent(ctx, db.ListQueuedClaimCandidatesByRuntimeForAgentParams{
			RuntimeID: task.RuntimeID,
			AgentID:   task.AgentID,
		})
		if err == nil {
			extra := len(others)
			for _, o := range others {
				if o.ID == task.ID {
					extra--
					break
				}
			}
			in.AnyOtherQueuedSameAgent = extra
		}
	}

	return waitreason.Classify(in)
}

// WaitReasonInputsForBulk is the bulk path used by the workspace task list
// handler. It collects the three input kinds in three roundtrips and then
// drives the classifier per row. Returned maps are keyed by the same
// UUID strings used in the response JSON so the handler can do a single
// map lookup per row.
func (s *TaskService) WaitReasonInputsForBulk(ctx context.Context, tasks []db.AgentTaskQueue) (map[string]string, error) {
	if len(tasks) == 0 {
		return map[string]string{}, nil
	}
	out := make(map[string]string, len(tasks))
	now := time.Now()

	// 1. Bulk runtime freshness.
	runtimeIDs := make([]pgtype.UUID, 0, len(tasks))
	runtimeSeen := map[string]bool{}
	for _, t := range tasks {
		if !t.RuntimeID.Valid || runtimeSeen[t.RuntimeID.String()] {
			continue
		}
		runtimeIDs = append(runtimeIDs, t.RuntimeID)
		runtimeSeen[t.RuntimeID.String()] = true
	}
	runtimeFresh := map[string]bool{}
	if len(runtimeIDs) > 0 {
		rows, err := s.Queries.ListAgentRuntimeFreshness(ctx, db.ListAgentRuntimeFreshnessParams{
			Ids: runtimeIDs,
		})
		if err != nil {
			return nil, err
		}
		for _, r := range rows {
			runtimeFresh[r.RuntimeID.String()] = waitreason.RuntimeOnlineNow(r.Status, r.LastSeenAt.Time, now, r.LastSeenAt.Valid)
		}
	}

	// 2. Bulk open blockers per issue.
	issueIDs := make([]pgtype.UUID, 0, len(tasks))
	issueSeen := map[string]bool{}
	for _, t := range tasks {
		if !t.IssueID.Valid || issueSeen[t.IssueID.String()] {
			continue
		}
		issueIDs = append(issueIDs, t.IssueID)
		issueSeen[t.IssueID.String()] = true
	}
	issueBlockers := map[string]int{}
	if len(issueIDs) > 0 {
		rows, err := s.Queries.ListIssuesWithOpenBlockers(ctx, db.ListIssuesWithOpenBlockersParams{
			IssueIds: issueIDs,
		})
		if err != nil {
			return nil, err
		}
		for _, r := range rows {
			issueBlockers[r.IssueID.String()] = int(r.OpenBlockerCount)
		}
	}

	// 3. Per-agent state. We could go fully bulk, but CountRunningTasks +
	//    GetAgent is already cheap per agent and the number of distinct
	//    agents in a 100-row page is bounded — accept the small N+1 in
	//    exchange for reusing the well-tested CountRunningTasks path.
	agentSeen := map[string]bool{}
	agentRunning := map[string]int{}
	agentMax := map[string]int{}
	for _, t := range tasks {
		if !t.AgentID.Valid || agentSeen[t.AgentID.String()] {
			continue
		}
		agentSeen[t.AgentID.String()] = true
		if n, err := s.Queries.CountRunningTasks(ctx, t.AgentID); err == nil {
			agentRunning[t.AgentID.String()] = int(n)
		}
		if a, err := s.Queries.GetAgent(ctx, t.AgentID); err == nil {
			agentMax[t.AgentID.String()] = int(a.MaxConcurrentTasks)
		}
	}

	// 4. Per-(agent, runtime) "other queued ahead" count. Reuse the
	//    ListQueuedClaimCandidatesByRuntime query — it already filters by
	//    runtime_id AND status='queued'. Subtract the task itself to get
	//    "ahead of me".
	type agentRuntimeKey struct{ agent, runtime string }
	groupSeen := map[agentRuntimeKey]bool{}
	groupOtherQueued := map[agentRuntimeKey]int{}
	for _, t := range tasks {
		k := agentRuntimeKey{
			agent:   t.AgentID.String(),
			runtime: t.RuntimeID.String(),
		}
		if groupSeen[k] {
			continue
		}
		groupSeen[k] = true
		var rtID pgtype.UUID
		if t.RuntimeID.Valid {
			rtID = t.RuntimeID
		}
		others, err := s.Queries.ListQueuedClaimCandidatesByRuntimeForAgent(ctx, db.ListQueuedClaimCandidatesByRuntimeForAgentParams{
			RuntimeID: rtID,
			AgentID:   t.AgentID,
		})
		if err != nil {
			continue
		}
		count := 0
		for _, o := range others {
			if o.ID != t.ID {
				count++
			}
		}
		groupOtherQueued[k] = count
	}

	for _, t := range tasks {
		in := waitreason.Inputs{
			Task:              t,
			AgentMaxSlots:     int32(agentMax[t.AgentID.String()]),
			AgentRunningCount: int64(agentRunning[t.AgentID.String()]),
			OpenBlockerCount:  issueBlockers[t.IssueID.String()],
			AnyOtherQueuedSameAgent: groupOtherQueued[agentRuntimeKey{
				agent:   t.AgentID.String(),
				runtime: t.RuntimeID.String(),
			}],
		}
		if t.RuntimeID.Valid {
			online, seen := runtimeFresh[t.RuntimeID.String()]
			in.RuntimeOnline = seen && online
		} else {
			in.RuntimeOnline = false
		}
		out[t.ID.String()] = waitreason.Classify(in)
	}
	return out, nil
}