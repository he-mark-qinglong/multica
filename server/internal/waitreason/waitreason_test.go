package waitreason

import (
	"testing"
	"time"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// TestClassify covers the four-value enum from SMA-36539 R1 and the
// upstream-cause ordering rule (runtime > dependency > agent > capacity).
// Classify is a pure function — no DB, no clock — so we exercise the
// entire decision surface from in-memory inputs in one place.
func TestClassify(t *testing.T) {
	cases := []struct {
		name string
		in   Inputs
		want string
	}{
		{
			name: "offline runtime wins over everything else",
			in: func() Inputs {
				in := baseInputs()
				in.RuntimeOnline = false
				in.OpenBlockerCount = 5
				in.AgentRunningCount = 9
				in.AgentMaxSlots = 1
				in.AnyOtherQueuedSameAgent = 100
				return in
			}(),
			want: WaitReasonRuntime,
		},
		{
			name: "open blockers win over agent capacity",
			in: func() Inputs {
				in := baseInputs()
				in.RuntimeOnline = true
				in.OpenBlockerCount = 1
				in.AgentRunningCount = 9
				in.AgentMaxSlots = 1
				return in
			}(),
			want: WaitReasonDependency,
		},
		{
			name: "agent at capacity wins over other-queued contention",
			in: func() Inputs {
				in := baseInputs()
				in.RuntimeOnline = true
				in.OpenBlockerCount = 0
				in.AgentRunningCount = 4
				in.AgentMaxSlots = 4
				in.AnyOtherQueuedSameAgent = 7
				return in
			}(),
			want: WaitReasonAgent,
		},
		{
			name: "excess queued tasks after agent capacity is daemon-saturation",
			in: func() Inputs {
				in := baseInputs()
				in.RuntimeOnline = true
				in.OpenBlockerCount = 0
				in.AgentRunningCount = 2
				in.AgentMaxSlots = 4
				in.AnyOtherQueuedSameAgent = 3
				return in
			}(),
			want: WaitReasonCapacity,
		},
		{
			name: "ready (nothing stuck) → empty (no wait_reason)",
			in: func() Inputs {
				in := baseInputs()
				in.RuntimeOnline = true
				in.OpenBlockerCount = 0
				in.AgentRunningCount = 0
				in.AgentMaxSlots = 1
				in.AnyOtherQueuedSameAgent = 0
				return in
			}(),
			want: "",
		},
		{
			name: "agent_max_zero is treated as 'unknown' → fall through to capacity",
			in: func() Inputs {
				in := baseInputs()
				in.RuntimeOnline = true
				in.OpenBlockerCount = 0
				in.AgentRunningCount = 0
				in.AgentMaxSlots = 0
				in.AnyOtherQueuedSameAgent = 2
				return in
			}(),
			want: WaitReasonCapacity,
		},
		{
			name: "dependency has higher priority than agent capacity but lower than runtime",
			in: func() Inputs {
				in := baseInputs()
				in.RuntimeOnline = true
				in.OpenBlockerCount = 1
				in.AgentRunningCount = 9
				in.AgentMaxSlots = 1
				in.AnyOtherQueuedSameAgent = 100
				return in
			}(),
			want: WaitReasonDependency,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Classify(tc.in)
			if got != tc.want {
				t.Fatalf("Classify() = %q, want %q", got, tc.want)
			}
		})
	}
}

// TestRuntimeOnlineNow pins the freshness predicate so a future tweak
// (e.g. a shorter / longer window) is intentional, not drift.
func TestRuntimeOnlineNow(t *testing.T) {
	now := time.Now()
	cases := []struct {
		name       string
		status     string
		lastSeen   time.Time
		valid      bool
		wantOnline bool
	}{
		{
			name:       "explicit offline wins over fresh heartbeat",
			status:     "offline",
			lastSeen:   now,
			valid:      true,
			wantOnline: false,
		},
		{
			name:       "NULL last_seen_at → offline even if status is online",
			status:     "online",
			lastSeen:   time.Time{},
			valid:      false,
			wantOnline: false,
		},
		{
			name:       "fresh heartbeat → online",
			status:     "online",
			lastSeen:   now.Add(-30 * time.Second),
			valid:      true,
			wantOnline: true,
		},
		{
			name:       "heartbeat within freshness window (60s) → online",
			status:     "online",
			lastSeen:   now.Add(-60 * time.Second),
			valid:      true,
			wantOnline: true,
		},
		{
			name:       "heartbeat at the freshness window boundary → online",
			status:     "online",
			lastSeen:   now.Add(-RuntimeFreshnessWindow),
			valid:      true,
			wantOnline: true,
		},
		{
			name:       "stale heartbeat past the window → offline",
			status:     "online",
			lastSeen:   now.Add(-RuntimeFreshnessWindow - time.Second),
			valid:      true,
			wantOnline: false,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := RuntimeOnlineNow(tc.status, tc.lastSeen, now, tc.valid)
			if got != tc.wantOnline {
				t.Fatalf("RuntimeOnlineNow() = %v, want %v", got, tc.wantOnline)
			}
		})
	}
}

// baseInputs returns the "ready" defaults. Tests override the specific
// fields they want to flip. Keeping a single base keeps the matrix above
// readable: every row only mutates what's distinct about its case.
func baseInputs() Inputs {
	return Inputs{
		Task:                    db.AgentTaskQueue{Status: "queued"},
		RuntimeOnline:           true,
		OpenBlockerCount:        0,
		AgentRunningCount:       0,
		AgentMaxSlots:           1,
		AnyOtherQueuedSameAgent: 0,
	}
}