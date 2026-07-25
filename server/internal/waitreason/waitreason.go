// Package waitreason implements the SMA-36539 R1 wait_reason classifier.
//
// ClassifyWaitReason is a pure function over a prefetched snapshot — no DB,
// no clock — so the unit tests can exercise the entire decision surface
// (runtime > dependency > agent > capacity, plus the "ready" empty branch)
// without spinning up Postgres.
//
// The TaskService in internal/service owns the DB-backed bulk loader
// (WaitReasonInputsForBulk) and the per-task convenience method; both call
// into this package's ClassifyWaitReason. Pulling the classifier out keeps
// it independently testable in environments where the service package has
// pre-existing build failures unrelated to wait_reason.
package waitreason

import (
	"time"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// WaitReason values for status='queued' agent_task_queue rows. Locked down
// in migration 126 (CHECK constraint) so the wire format stays stable. The
// waiting_local_directory status reuses the same wait_reason column for a
// free-form path string; that row shape never crosses into this enum because
// the two statuses are mutually exclusive in the status CHECK.
const (
	WaitReasonRuntime    = "waiting_runtime"
	WaitReasonDependency = "waiting_dependency"
	WaitReasonAgent      = "waiting_agent"
	WaitReasonCapacity   = "waiting_capacity"
)

// RuntimeFreshnessWindow bounds how stale last_seen_at can be before we
// declare the runtime offline. The default daemon heartbeat interval is 15s
// (server/internal/daemon/config.go DefaultHeartbeatInterval), so 90s — six
// missed beats — leaves slack for transient GC pauses / ws reconnects
// without letting a genuinely dead daemon keep masquerading as live.
const RuntimeFreshnessWindow = 90 * time.Second

// Inputs is the prefetched snapshot ClassifyWaitReason needs. Passing a
// snapshot keeps the classifier pure and makes the unit tests trivial:
// build an Inputs in memory, call ClassifyWaitReason, assert.
type Inputs struct {
	Task                    db.AgentTaskQueue
	RuntimeOnline           bool // false = runtime offline / unknown
	OpenBlockerCount        int  // > 0 means a blocker dependency is still open
	AgentRunningCount       int64 // dispatch count for the agent (sum across same-(issue,agent) gates)
	AgentMaxSlots           int32 // agent.max_concurrent_tasks
	// AnyOtherQueuedSameAgent is the count of OTHER queued tasks ahead of this
	// one for the same agent — a positive value is the strongest signal that
	// "the daemon slot is the gate, not the upstream causes".
	AnyOtherQueuedSameAgent int
}

// Classify picks one of the four wait_reason values for a queued task.
// Evaluation order matches the spec: runtime → dependency → agent →
// capacity. The function is pure and never touches the DB; callers must
// pre-fetch all inputs. Returns "" when none of the four apply (e.g. the
// task's daemon is local and idle) — callers should treat that as "ready,
// waiting for the next poll".
func Classify(in Inputs) string {
	if !in.RuntimeOnline {
		return WaitReasonRuntime
	}
	if in.OpenBlockerCount > 0 {
		return WaitReasonDependency
	}
	if in.AgentMaxSlots > 0 && in.AgentRunningCount >= int64(in.AgentMaxSlots) {
		return WaitReasonAgent
	}
	if in.AnyOtherQueuedSameAgent > 0 {
		return WaitReasonCapacity
	}
	return ""
}

// RuntimeOnlineNow is a small helper that maps (status, lastSeenAt, now) into
// the boolean the classifier wants. Centralising it here keeps the bulk
// loader in service/ from duplicating the freshness predicate, and gives
// the test suite one place to override "now" for time-dependent cases.
func RuntimeOnlineNow(status string, lastSeenAt time.Time, now time.Time, valid bool) bool {
	if status == "offline" || !valid {
		return false
	}
	return now.Sub(lastSeenAt) <= RuntimeFreshnessWindow
}