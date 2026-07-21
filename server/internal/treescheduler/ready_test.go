package treescheduler

import (
	"testing"

	"github.com/google/uuid"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

func TestComputeReadyLeaves_TodoWithoutBlockersIsReady(t *testing.T) {
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	issues := []db.Issue{makeIssue(leaf, StatusTodo, nil, 1, "leaf")}
	got := ComputeReadyLeaves(issues, nil)
	if len(got) != 1 {
		t.Fatalf("got %d ready leaves, want 1", len(got))
	}
	if got[0].IssueID != leaf {
		t.Errorf("ready leaf=%s want %s", got[0].IssueID, leaf)
	}
}

func TestComputeReadyLeaves_NonTodoNotReady(t *testing.T) {
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	cases := []string{
		StatusInProgress,
		StatusInReview,
		StatusBlocked,
		StatusBacklog,
		StatusDone,
		StatusCancelled,
	}
	for _, status := range cases {
		issues := []db.Issue{makeIssue(leaf, status, nil, 1, "leaf")}
		got := ComputeReadyLeaves(issues, nil)
		if len(got) != 0 {
			t.Errorf("status=%s: got %d ready, want 0 (status=%s not ready)", status, len(got), status)
		}
	}
}

func TestComputeReadyLeaves_AllBlockersDoneIsReady(t *testing.T) {
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	b1 := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	b2 := mustUUID(t, "33333333-3333-3333-3333-333333333333")
	issues := []db.Issue{
		makeIssue(leaf, StatusTodo, nil, 1, "leaf"),
		makeIssue(b1, StatusDone, nil, 2, "b1"),
		makeIssue(b2, StatusCancelled, nil, 3, "b2"),
	}
	deps := []db.IssueDependency{
		dep(leaf, b1),
		dep(leaf, b2),
	}
	got := ComputeReadyLeaves(issues, deps)
	if len(got) != 1 {
		t.Fatalf("got %d ready leaves, want 1", len(got))
	}
	if got[0].IssueID != leaf {
		t.Errorf("ready leaf=%s want %s", got[0].IssueID, leaf)
	}
}

// All blockers resolved + one extra todo leaf. The blocker, even
// when still in todo status, is its own ready leaf — they are
// classified independently. The blocked leaf appears in the ready
// set (Plan will then mark it Proceed / PromoteBlocker / Switch
// based on the actual blocker state).
func TestComputeReadyLeaves_TodoBlockerIsIndependentReady(t *testing.T) {
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	blocker := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	issues := []db.Issue{
		makeIssue(leaf, StatusTodo, nil, 1, "leaf"),
		makeIssue(blocker, StatusTodo, nil, 2, "blocker"),
	}
	deps := []db.IssueDependency{dep(leaf, blocker)}
	got := ComputeReadyLeaves(issues, deps)
	if len(got) != 2 {
		t.Fatalf("got %d ready leaves, want 2 (both leaves are todo)", len(got))
	}
}

// A leaf with a blocked blocker is still surfaced as a candidate —
// Plan() decides the recommendation. The ready calc never claims to
// know whether a leaf can make progress; that is Plan's job.
func TestComputeReadyLeaves_BlockedBlockerStillCandidate(t *testing.T) {
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	blocker := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	issues := []db.Issue{
		makeIssue(leaf, StatusTodo, nil, 1, "leaf"),
		makeIssue(blocker, StatusBlocked, nil, 2, "stuck blocker"),
	}
	deps := []db.IssueDependency{dep(leaf, blocker)}
	got := ComputeReadyLeaves(issues, deps)
	if len(got) != 1 || got[0].IssueID != leaf {
		t.Errorf("got=%v want [%s]", got, leaf)
	}
}

// A leaf with a missing blocker is also still surfaced; the
// dispatcher surfaces it for Plan → Switch escalation.
func TestComputeReadyLeaves_MissingBlockerStillCandidate(t *testing.T) {
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	missing := mustUUID(t, "99999999-9999-9999-9999-999999999999")
	issues := []db.Issue{makeIssue(leaf, StatusTodo, nil, 1, "leaf")}
	deps := []db.IssueDependency{dep(leaf, missing)}
	got := ComputeReadyLeaves(issues, deps)
	if len(got) != 1 || got[0].IssueID != leaf {
		t.Errorf("got=%v want [%s]", got, leaf)
	}
}

func TestComputeReadyLeaves_PriorityRankOrdering(t *testing.T) {
	high := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	med := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	low := mustUUID(t, "33333333-3333-3333-3333-333333333333")
	none := mustUUID(t, "44444444-4444-4444-4444-444444444444")
	issues := []db.Issue{
		makeIssueWithPriority(high, StatusTodo, "high", nil, 1, "high"),
		makeIssueWithPriority(med, StatusTodo, "medium", nil, 2, "med"),
		makeIssueWithPriority(low, StatusTodo, "low", nil, 3, "low"),
		makeIssueWithPriority(none, StatusTodo, "", nil, 4, "none"),
	}
	got := ComputeReadyLeaves(issues, nil)
	if len(got) != 4 {
		t.Fatalf("got %d, want 4", len(got))
	}
	// Expected order: high (3), med (2), low (1), none (0).
	want := []uuid.UUID{high, med, low, none}
	for i, w := range want {
		if got[i].IssueID != w {
			t.Errorf("position %d: got %s want %s", i, got[i].IssueID, w)
		}
		if got[i].PriorityRank != 3-i {
			t.Errorf("position %d: rank=%d want %d", i, got[i].PriorityRank, 3-i)
		}
	}
}

func TestComputeReadyLeaves_TieBreakByNumber(t *testing.T) {
	// Two leaves with the same priority — older (lower number) wins.
	older := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	newer := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	issues := []db.Issue{
		makeIssueWithPriority(older, StatusTodo, "high", nil, 10, "older"),
		makeIssueWithPriority(newer, StatusTodo, "high", nil, 20, "newer"),
	}
	got := ComputeReadyLeaves(issues, nil)
	if len(got) != 2 {
		t.Fatalf("got %d, want 2", len(got))
	}
	if got[0].IssueID != older {
		t.Errorf("first leaf=%s want %s (older should rank first)", got[0].IssueID, older)
	}
	if got[1].IssueID != newer {
		t.Errorf("second leaf=%s want %s", got[1].IssueID, newer)
	}
}

func TestComputeReadyLeaves_DeterministicAcrossRuns(t *testing.T) {
	// Build the same index twice; the output order must match.
	ids := []uuid.UUID{
		mustUUID(t, "11111111-1111-1111-1111-111111111111"),
		mustUUID(t, "22222222-2222-2222-2222-222222222222"),
		mustUUID(t, "33333333-3333-3333-3333-333333333333"),
		mustUUID(t, "44444444-4444-4444-4444-444444444444"),
	}
	issuesA := []db.Issue{
		makeIssue(ids[2], StatusTodo, nil, 3, "c"),
		makeIssue(ids[0], StatusTodo, nil, 1, "a"),
		makeIssue(ids[3], StatusTodo, nil, 4, "d"),
		makeIssue(ids[1], StatusTodo, nil, 2, "b"),
	}
	issuesB := []db.Issue{
		makeIssue(ids[1], StatusTodo, nil, 2, "b"),
		makeIssue(ids[3], StatusTodo, nil, 4, "d"),
		makeIssue(ids[0], StatusTodo, nil, 1, "a"),
		makeIssue(ids[2], StatusTodo, nil, 3, "c"),
	}
	gotA := ComputeReadyLeaves(issuesA, nil)
	gotB := ComputeReadyLeaves(issuesB, nil)
	if len(gotA) != len(gotB) {
		t.Fatalf("lengths differ: %d vs %d", len(gotA), len(gotB))
	}
	for i := range gotA {
		if gotA[i].IssueID != gotB[i].IssueID {
			t.Errorf("position %d: %s vs %s — output is not deterministic", i, gotA[i].IssueID, gotB[i].IssueID)
		}
	}
}

func TestComputeReadyLeaves_StableOutput(t *testing.T) {
	// Sanity check: result must be sorted by PriorityRank desc, then
	// issue number asc, then UUID. Catches regressions where we sort
	// by an unstable key (map iteration).
	ids := []uuid.UUID{
		mustUUID(t, "44444444-4444-4444-4444-444444444444"),
		mustUUID(t, "33333333-3333-3333-3333-333333333333"),
		mustUUID(t, "22222222-2222-2222-2222-222222222222"),
		mustUUID(t, "11111111-1111-1111-1111-111111111111"),
	}
	issues := []db.Issue{
		makeIssueWithPriority(ids[0], StatusTodo, "high", nil, 1, "1"),
		makeIssueWithPriority(ids[1], StatusTodo, "high", nil, 2, "2"),
		makeIssueWithPriority(ids[2], StatusTodo, "low", nil, 3, "3"),
		makeIssueWithPriority(ids[3], StatusTodo, "low", nil, 4, "4"),
	}
	got := ComputeReadyLeaves(issues, nil)
	// Expected: ids[0] (#1 high), ids[1] (#2 high), ids[2] (#3 low), ids[3] (#4 low)
	want := []uuid.UUID{ids[0], ids[1], ids[2], ids[3]}
	for i, w := range want {
		if got[i].IssueID != w {
			t.Errorf("position %d: got %s want %s", i, got[i].IssueID, w)
		}
	}
}