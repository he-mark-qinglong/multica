package treescheduler

import (
	"sort"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// makeIssue builds a db.Issue with a stable UUID. The parent is taken
// as a *uuid.UUID so the test can distinguish "no parent" (nil) from
// "parent happens to be the zero UUID" — both encode a real parent in
// the production schema, so a value-vs-Nil signature would silently
// drop the latter (caught in this test suite; see the
// TestPlan_SwitchWhenAllBlockersHopeless regression).
func makeIssue(id uuid.UUID, status string, parent *uuid.UUID, number int32, title string) db.Issue {
	var pid pgtype.UUID
	if parent != nil {
		copy(pid.Bytes[:], parent[:])
		pid.Valid = true
	}
	var pgid pgtype.UUID
	copy(pgid.Bytes[:], id[:])
	pgid.Valid = true
	return db.Issue{
		ID:            pgid,
		Status:        status,
		ParentIssueID: pid,
		Number:        number,
		Title:         title,
	}
}

func dep(owner, blocker uuid.UUID) db.IssueDependency {
	var o, b pgtype.UUID
	copy(o.Bytes[:], owner[:])
	o.Valid = true
	copy(b.Bytes[:], blocker[:])
	b.Valid = true
	return db.IssueDependency{IssueID: o, DependsOnIssueID: b, Type: DepTypeBlocks}
}

func mustUUID(t *testing.T, s string) uuid.UUID {
	t.Helper()
	u, err := uuid.Parse(s)
	if err != nil {
		t.Fatalf("parse uuid %q: %v", s, err)
	}
	return u
}

func TestPlan_ProceedWhenNoBlockers(t *testing.T) {
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	issues := []db.Issue{makeIssue(leaf, StatusTodo, nil, 1, "leaf")}
	plan := Plan(issues, nil, []ReadyLeaf{{IssueID: leaf}})
	if len(plan) != 1 {
		t.Fatalf("plan len=%d want 1", len(plan))
	}
	if plan[0].Kind != AvoidanceProceed {
		t.Errorf("kind=%v want proceed", plan[0].Kind)
	}
	if len(plan[0].BlockerIDs) != 0 {
		t.Errorf("unexpected blockers: %v", plan[0].BlockerIDs)
	}
}

func TestPlan_PromoteBlockerWhenBlockerOpen(t *testing.T) {
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	blocker := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	issues := []db.Issue{
		makeIssue(leaf, StatusTodo, nil, 1, "leaf"),
		makeIssue(blocker, StatusInProgress, nil, 2, "blocker"),
	}
	deps := []db.IssueDependency{dep(leaf, blocker)}
	plan := Plan(issues, deps, []ReadyLeaf{{IssueID: leaf}})
	if plan[0].Kind != AvoidancePromoteBlocker {
		t.Errorf("kind=%v want promote_blocker", plan[0].Kind)
	}
	if len(plan[0].BlockerIDs) != 1 || plan[0].BlockerIDs[0] != blocker {
		t.Errorf("blocker ids=%v want [%s]", plan[0].BlockerIDs, blocker)
	}
	if plan[0].SiblingID != uuid.Nil {
		t.Errorf("sibling=%s want nil", plan[0].SiblingID)
	}
}

func TestPlan_SwitchWhenAllBlockersHopeless(t *testing.T) {
	parent := mustUUID(t, "00000000-0000-0000-0000-000000000000")
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	sibling := mustUUID(t, "33333333-3333-3333-3333-333333333333")
	blocker := mustUUID(t, "22222222-2222-2222-2222-222222222222")

	issues := []db.Issue{
		makeIssue(parent, StatusInProgress, nil, 0, "parent"),
		makeIssue(leaf, StatusTodo, &parent, 1, "blocked leaf"),
		makeIssue(sibling, StatusTodo, &parent, 2, "ready sibling"),
		makeIssue(blocker, StatusBlocked, nil, 3, "stuck blocker"),
	}
	deps := []db.IssueDependency{dep(leaf, blocker)}

	plan := Plan(issues, deps, []ReadyLeaf{
		{IssueID: leaf, PriorityRank: 1},
		{IssueID: sibling, PriorityRank: 2},
	})
	if plan[0].Kind != AvoidanceSwitch {
		t.Errorf("kind=%v want switch", plan[0].Kind)
	}
	if plan[0].SiblingID != sibling {
		t.Errorf("sibling=%s want %s", plan[0].SiblingID, sibling)
	}
	// Sibling itself is fine — proceed.
	if plan[1].Kind != AvoidanceProceed {
		t.Errorf("sibling kind=%v want proceed", plan[1].Kind)
	}
}

func TestPlan_SwitchFallsBackWhenNoUsableSibling(t *testing.T) {
	parent := mustUUID(t, "00000000-0000-0000-0000-000000000000")
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	otherSibling := mustUUID(t, "33333333-3333-3333-3333-333333333333")
	blocker := mustUUID(t, "22222222-2222-2222-2222-222222222222")

	// The only other sibling is itself blocked → no usable switch
	// target. The recommendation is still Switch (so the caller knows
	// the leaf is hopeless), but SiblingID is uuid.Nil.
	issues := []db.Issue{
		makeIssue(parent, StatusInProgress, nil, 0, "parent"),
		makeIssue(leaf, StatusTodo, &parent, 1, "blocked leaf"),
		makeIssue(otherSibling, StatusTodo, &parent, 2, "also blocked sibling"),
		makeIssue(blocker, StatusBlocked, nil, 3, "stuck blocker"),
		makeIssue(mustUUID(t, "44444444-4444-4444-4444-444444444444"),
			StatusInProgress, nil, 4, "other blocker"),
	}
	deps := []db.IssueDependency{
		dep(leaf, blocker),
		dep(otherSibling, mustUUID(t, "44444444-4444-4444-4444-444444444444")),
	}
	plan := Plan(issues, deps, []ReadyLeaf{
		{IssueID: leaf, PriorityRank: 1},
		{IssueID: otherSibling, PriorityRank: 2},
	})
	if plan[0].Kind != AvoidanceSwitch {
		t.Errorf("kind=%v want switch", plan[0].Kind)
	}
	if plan[0].SiblingID != uuid.Nil {
		t.Errorf("sibling=%s want nil", plan[0].SiblingID)
	}
}

func TestPlan_MissingBlockerCountsAsHopeless(t *testing.T) {
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	missingBlocker := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	issues := []db.Issue{makeIssue(leaf, StatusTodo, nil, 1, "leaf")}
	deps := []db.IssueDependency{dep(leaf, missingBlocker)}
	plan := Plan(issues, deps, []ReadyLeaf{{IssueID: leaf}})
	if plan[0].Kind != AvoidanceSwitch {
		t.Errorf("kind=%v want switch (missing blocker is hopeless)", plan[0].Kind)
	}
}

func TestPlan_PreservesReadyOrder(t *testing.T) {
	leafA := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	leafB := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	leafC := mustUUID(t, "33333333-3333-3333-3333-333333333333")
	issues := []db.Issue{
		makeIssue(leafA, StatusTodo, nil, 1, "a"),
		makeIssue(leafB, StatusTodo, nil, 2, "b"),
		makeIssue(leafC, StatusTodo, nil, 3, "c"),
	}
	plan := Plan(issues, nil, []ReadyLeaf{
		{IssueID: leafC, PriorityRank: 3},
		{IssueID: leafA, PriorityRank: 1},
		{IssueID: leafB, PriorityRank: 2},
	})
	if len(plan) != 3 {
		t.Fatalf("plan len=%d want 3", len(plan))
	}
	if plan[0].IssueID != leafC || plan[1].IssueID != leafA || plan[2].IssueID != leafB {
		t.Errorf("plan order lost: %v %v %v", plan[0].IssueID, plan[1].IssueID, plan[2].IssueID)
	}
}

func TestPlan_SwitchSiblingDeterministic(t *testing.T) {
	parent := mustUUID(t, "00000000-0000-0000-0000-000000000000")
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	sibA := mustUUID(t, "33333333-3333-3333-3333-333333333333")
	sibB := mustUUID(t, "44444444-4444-4444-4444-444444444444")
	blocker := mustUUID(t, "22222222-2222-2222-2222-222222222222")

	// Two siblings — the older one (lower number) should win so the
	// recommendation is stable across runs.
	issues := []db.Issue{
		makeIssue(parent, StatusInProgress, nil, 0, "p"),
		makeIssue(leaf, StatusTodo, &parent, 1, "leaf"),
		makeIssue(sibB, StatusTodo, &parent, 3, "b"),
		makeIssue(sibA, StatusTodo, &parent, 2, "a"),
		makeIssue(blocker, StatusBlocked, nil, 4, "blocker"),
	}
	deps := []db.IssueDependency{dep(leaf, blocker)}
	plan := Plan(issues, deps, []ReadyLeaf{
		{IssueID: leaf, PriorityRank: 1},
		{IssueID: sibA, PriorityRank: 2},
		{IssueID: sibB, PriorityRank: 3},
	})
	if plan[0].Kind != AvoidanceSwitch {
		t.Fatalf("kind=%v want switch", plan[0].Kind)
	}
	if plan[0].SiblingID != sibA {
		t.Errorf("sibling=%s want %s (older sibling should win)", plan[0].SiblingID, sibA)
	}
}

func TestBuildIndex_IgnoresNonBlocksDeps(t *testing.T) {
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	blocker := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	issues := []db.Issue{
		makeIssue(leaf, StatusTodo, nil, 1, "leaf"),
		makeIssue(blocker, StatusTodo, nil, 2, "blocker"),
	}
	deps := []db.IssueDependency{
		{
			IssueID:          issues[0].ID,
			DependsOnIssueID: issues[1].ID,
			Type:             "related", // not "blocks"
		},
	}
	idx := BuildIndex(issues, deps)
	if _, ok := idx[leaf].BlockerIDs[blocker]; ok {
		t.Errorf("non-blocks dep was kept as blocker")
	}
}

func TestPlan_PreservesBlockerOrderAcrossRuns(t *testing.T) {
	leaf := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	// Two open blockers, listed in random order in `deps`. Plan
	// must sort them so log lines are diff-friendly.
	b1 := mustUUID(t, "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
	b2 := mustUUID(t, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
	issues := []db.Issue{
		makeIssue(leaf, StatusTodo, nil, 1, "leaf"),
		makeIssue(b1, StatusInProgress, nil, 2, "b1"),
		makeIssue(b2, StatusInProgress, nil, 3, "b2"),
	}
	deps := []db.IssueDependency{dep(leaf, b1), dep(leaf, b2)}
	plan := Plan(issues, deps, []ReadyLeaf{{IssueID: leaf}})
	if plan[0].Kind != AvoidancePromoteBlocker {
		t.Fatalf("kind=%v want promote_blocker", plan[0].Kind)
	}
	got := append([]uuid.UUID(nil), plan[0].BlockerIDs...)
	want := []uuid.UUID{b2, b1} // b2 < b1 by byte order
	if !sort.SliceIsSorted(got, func(i, j int) bool { return uuidLess(got[i], got[j]) }) {
		t.Errorf("blocker ids not sorted: %v", got)
	}
	_ = want
}

func TestIsTerminalStatus(t *testing.T) {
	cases := map[string]bool{
		StatusDone:      true,
		StatusCancelled: true,
		StatusTodo:      false,
		StatusBlocked:   false,
		StatusBacklog:   false,
	}
	for s, want := range cases {
		if got := IsTerminalStatus(s); got != want {
			t.Errorf("IsTerminalStatus(%q)=%v want %v", s, got, want)
		}
	}
}
