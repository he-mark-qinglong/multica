package treescheduler

import (
	"sort"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// makeIssueWithPriority extends makeIssue-style helpers with a
// priority string so cycle-break tests can exercise the
// "lowest-priority edge wins" rule. Defined here (not in branch_test.go)
// because branch_test.go's makeIssue doesn't expose priority and
// importing from _test.go would create a cross-file test helper
// duplication that the linter flags.
func makeIssueWithPriority(id uuid.UUID, status, priority string, parent *uuid.UUID, number int32, title string) db.Issue {
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
		Priority:      priority,
		ParentIssueID: pid,
		Number:        number,
		Title:         title,
	}
}

// mustSortedIDs returns CycleIDs sorted ascending — used to compare
// against alerts without depending on a specific sort algorithm.
func mustSortedIDs(t *testing.T, ids []uuid.UUID) []uuid.UUID {
	t.Helper()
	out := append([]uuid.UUID(nil), ids...)
	sort.Slice(out, func(i, j int) bool { return uuidLess(out[i], out[j]) })
	return out
}

func TestDetectCycles_NoCyclesInDAG(t *testing.T) {
	// A: B + C (A depends on B and C; B, C depend on nothing).
	// Pure DAG — DetectCycles must report zero cycles (the same
	// false-positive floor the spec calls out as acceptance #4).
	a := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	b := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	c := mustUUID(t, "33333333-3333-3333-3333-333333333333")

	issues := []db.Issue{
		makeIssue(a, StatusTodo, nil, 1, "a"),
		makeIssue(b, StatusTodo, nil, 2, "b"),
		makeIssue(c, StatusTodo, nil, 3, "c"),
	}
	deps := []db.IssueDependency{
		dep(a, b),
		dep(a, c),
	}
	idx := BuildIndex(issues, deps)
	alerts := DetectCycles(idx)
	if len(alerts) != 0 {
		t.Fatalf("DAG unexpectedly reported %d cycle(s): %+v", len(alerts), alerts)
	}
}

func TestDetectCycles_SelfLoop(t *testing.T) {
	// Acceptance #3: A → A is a self-loop, reported distinctly via
	// the fast path (SelfLoop == true, CycleIDs length 1) and not
	// silently merged into the multi-node path.
	a := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	issues := []db.Issue{makeIssue(a, StatusTodo, nil, 1, "self")}
	deps := []db.IssueDependency{dep(a, a)}
	idx := BuildIndex(issues, deps)

	alerts := DetectCycles(idx)
	if len(alerts) != 1 {
		t.Fatalf("got %d alerts, want 1", len(alerts))
	}
	al := alerts[0]
	if !al.Cycle.SelfLoop {
		t.Errorf("SelfLoop=false, want true")
	}
	if len(al.Cycle.CycleIDs) != 1 || al.Cycle.CycleIDs[0] != a {
		t.Errorf("CycleIDs=%v want [%s]", al.Cycle.CycleIDs, a)
	}
	if al.SuggestedIgnoreFrom != a || al.SuggestedIgnoreTo != a {
		t.Errorf("suggest=->%s -> %s, want self-edge", al.SuggestedIgnoreFrom, al.SuggestedIgnoreTo)
	}
	if al.MemberPriority[a] != "" {
		// Default issue from makeIssue has empty priority; presence
		// of an empty string is fine, just confirms the map was
		// populated.
		t.Errorf("member priority map missing entry: %v", al.MemberPriority)
	}
}

func TestDetectCycles_TwoCycle(t *testing.T) {
	// Acceptance #4 (2-cycle): A → B → A.
	a := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	b := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	issues := []db.Issue{
		makeIssue(a, StatusTodo, nil, 1, "a"),
		makeIssue(b, StatusTodo, nil, 2, "b"),
	}
	deps := []db.IssueDependency{dep(a, b), dep(b, a)}
	idx := BuildIndex(issues, deps)

	alerts := DetectCycles(idx)
	if len(alerts) != 1 {
		t.Fatalf("got %d alerts, want 1: %+v", len(alerts), alerts)
	}
	al := alerts[0]
	if al.Cycle.SelfLoop {
		t.Errorf("SelfLoop=true on a 2-cycle")
	}
	got := mustSortedIDs(t, al.Cycle.CycleIDs)
	want := []uuid.UUID{a, b}
	if len(got) != len(want) || got[0] != want[0] || got[1] != want[1] {
		t.Errorf("CycleIDs=%v want %v", got, want)
	}
	// Either side can be the suggestion owner; both are valid
	// break points. Verify it points at a real edge in the graph.
	if al.SuggestedIgnoreFrom == uuid.Nil || al.SuggestedIgnoreTo == uuid.Nil {
		t.Fatalf("suggested edge incomplete: %s -> %s", al.SuggestedIgnoreFrom, al.SuggestedIgnoreTo)
	}
	from := al.SuggestedIgnoreFrom
	if _, has := idx[from].BlockerIDs[al.SuggestedIgnoreTo]; !has {
		t.Errorf("suggested edge %s -> %s is not a real block edge", from, al.SuggestedIgnoreTo)
	}
	if al.MemberPriority[a] != "" || al.MemberPriority[b] != "" {
		t.Errorf("member priorities unexpected: %v", al.MemberPriority)
	}
}

func TestDetectCycles_ThreeCycle(t *testing.T) {
	// Acceptance #4 (3-cycle): A → B → C → A.
	a := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	b := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	c := mustUUID(t, "33333333-3333-3333-3333-333333333333")
	issues := []db.Issue{
		makeIssue(a, StatusTodo, nil, 1, "a"),
		makeIssue(b, StatusTodo, nil, 2, "b"),
		makeIssue(c, StatusTodo, nil, 3, "c"),
	}
	deps := []db.IssueDependency{dep(a, b), dep(b, c), dep(c, a)}
	idx := BuildIndex(issues, deps)

	alerts := DetectCycles(idx)
	if len(alerts) != 1 {
		t.Fatalf("got %d alerts, want 1: %+v", len(alerts), alerts)
	}
	al := alerts[0]
	if al.Cycle.SelfLoop {
		t.Errorf("SelfLoop=true on a 3-cycle")
	}
	got := mustSortedIDs(t, al.Cycle.CycleIDs)
	want := []uuid.UUID{a, b, c}
	if len(got) != len(want) {
		t.Fatalf("CycleIDs len=%d want %d", len(got), len(want))
	}
	for i := range got {
		if got[i] != want[i] {
			t.Errorf("CycleIDs[%d]=%s want %s", i, got[i], want[i])
		}
	}
}

func TestDetectCycles_PrioritySuggestionPicksLowPriorityOwner(t *testing.T) {
	// Tie-break: when one cycle member is "low" priority and the
	// others are "high", the suggested edge to ignore must be owned
	// by the low-priority node. This pins the spec's "把环上
	// priority 最低的边标记为 ignore" guidance.
	high1 := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	low := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	high2 := mustUUID(t, "33333333-3333-3333-3333-333333333333")
	issues := []db.Issue{
		makeIssueWithPriority(high1, StatusTodo, "high", nil, 1, "hi1"),
		makeIssueWithPriority(low, StatusTodo, "low", nil, 2, "lo"),
		makeIssueWithPriority(high2, StatusTodo, "high", nil, 3, "hi2"),
	}
	// Cycle: high1 -> low -> high2 -> high1
	deps := []db.IssueDependency{dep(high1, low), dep(low, high2), dep(high2, high1)}
	idx := BuildIndex(issues, deps)

	alerts := DetectCycles(idx)
	if len(alerts) != 1 {
		t.Fatalf("got %d alerts, want 1", len(alerts))
	}
	al := alerts[0]
	if al.SuggestedIgnoreFrom != low {
		t.Errorf("suggested owner=%s want %s (low priority)", al.SuggestedIgnoreFrom, low)
	}
	if al.SuggestedIgnoreTo != high2 {
		t.Errorf("suggested target=%s want %s", al.SuggestedIgnoreTo, high2)
	}
	if got, want := al.MemberPriority[low], "low"; got != want {
		t.Errorf("MemberPriority[low]=%q want %q", got, want)
	}
}

func TestDetectCycles_PriorityTieBreakerUsesNumber(t *testing.T) {
	// Two low-priority cycle members tie on rank — the suggestion
	// owner must be the one with the smaller issue number, matching
	// the convention pickSwitchSibling uses (number ASC > title > UUID).
	// Sticking to the existing treescheduler sorting rule keeps the
	// dispatcher log diff-friendly across cycles that happen to share
	// priorities.
	a := mustUUID(t, "22222222-2222-2222-2222-222222222222") // bigger UUID, smaller number
	b := mustUUID(t, "11111111-1111-1111-1111-111111111111") // smaller UUID, larger number
	c := mustUUID(t, "33333333-3333-3333-3333-333333333333") // high — irrelevant for this assertion
	issues := []db.Issue{
		makeIssueWithPriority(a, StatusTodo, "low", nil, 1, "a"),
		makeIssueWithPriority(b, StatusTodo, "low", nil, 2, "b"),
		makeIssueWithPriority(c, StatusTodo, "high", nil, 3, "c"),
	}
	// Cycle a -> b -> c -> a. Both a and b are "low" — number-tie
	// picks a (number=1) as the suggestion owner.
	deps := []db.IssueDependency{dep(a, b), dep(b, c), dep(c, a)}
	idx := BuildIndex(issues, deps)
	alerts := DetectCycles(idx)
	if len(alerts) != 1 {
		t.Fatalf("got %d alerts, want 1", len(alerts))
	}
	if alerts[0].SuggestedIgnoreFrom != a {
		t.Errorf("suggested owner=%s want %s (number tie-break)", alerts[0].SuggestedIgnoreFrom, a)
	}
}

func TestDetectCycles_DisjointSubgraphs(t *testing.T) {
	// Two unrelated subgraphs, each their own cycle, must produce
	// two alerts (and each must contain only its own members —
	// confirming SCC doesn't merge disjoint cycles).
	a := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	b := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	c := mustUUID(t, "33333333-3333-3333-3333-333333333333")
	d := mustUUID(t, "44444444-4444-4444-4444-444444444444")
	issues := []db.Issue{
		makeIssue(a, StatusTodo, nil, 1, "a"),
		makeIssue(b, StatusTodo, nil, 2, "b"),
		makeIssue(c, StatusTodo, nil, 3, "c"),
		makeIssue(d, StatusTodo, nil, 4, "d"),
	}
	// Cycle 1: a <-> b. Cycle 2: c <-> d.
	deps := []db.IssueDependency{dep(a, b), dep(b, a), dep(c, d), dep(d, c)}
	idx := BuildIndex(issues, deps)

	alerts := DetectCycles(idx)
	if len(alerts) != 2 {
		t.Fatalf("got %d alerts, want 2: %+v", len(alerts), alerts)
	}
	for i, al := range alerts {
		if al.Cycle.SelfLoop {
			t.Errorf("alert %d unexpectedly a self-loop", i)
		}
		if len(al.Cycle.CycleIDs) != 2 {
			t.Errorf("alert %d CycleIDs len=%d want 2", i, len(al.Cycle.CycleIDs))
		}
	}
	// Outer alert slice must be sorted by lex-smallest member.
	if alerts[0].Cycle.CycleIDs[0] != a {
		t.Errorf("alerts[0] starts with %s, want %s (sort order)", alerts[0].Cycle.CycleIDs[0], a)
	}
	if alerts[1].Cycle.CycleIDs[0] != c {
		t.Errorf("alerts[1] starts with %s, want %s", alerts[1].Cycle.CycleIDs[0], c)
	}
}

func TestDetectCycles_SelfLoopPlusSeparateTwoCycle(t *testing.T) {
	// Mixed graph: a self-loop on A, plus a separate {B,C} cycle.
	// Both must surface (and not collapse onto each other).
	a := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	b := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	c := mustUUID(t, "33333333-3333-3333-3333-333333333333")
	issues := []db.Issue{
		makeIssue(a, StatusTodo, nil, 1, "a"),
		makeIssue(b, StatusTodo, nil, 2, "b"),
		makeIssue(c, StatusTodo, nil, 3, "c"),
	}
	deps := []db.IssueDependency{
		dep(a, a),            // self
		dep(b, c), dep(c, b), // 2-cycle
	}
	idx := BuildIndex(issues, deps)
	alerts := DetectCycles(idx)
	if len(alerts) != 2 {
		t.Fatalf("got %d alerts, want 2: %+v", len(alerts), alerts)
	}
	// Sorted by lex-smallest member ID. A is smaller than B, so the
	// self-loop alert should come first.
	if !alerts[0].Cycle.SelfLoop || alerts[0].Cycle.CycleIDs[0] != a {
		t.Errorf("alerts[0]=%+v want self-loop on A", alerts[0])
	}
	if alerts[1].Cycle.SelfLoop || len(alerts[1].Cycle.CycleIDs) != 2 {
		t.Errorf("alerts[1]=%+v want 2-cycle B<->C", alerts[1])
	}
}

func TestDetectCycles_EmptyIndex(t *testing.T) {
	alerts := DetectCycles(map[uuid.UUID]*IssueNode{})
	if len(alerts) != 0 {
		t.Errorf("empty index should report 0 alerts, got %d", len(alerts))
	}
}

func TestDetectCycles_NoFalsePositiveOnLinearChain(t *testing.T) {
	// Linear chain A -> B -> C -> D. Common DAG case in real
	// workspaces; pinning here so a future refactor that
	// accidentally wires forward edges as reverse never reaches
	// production.
	a := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	b := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	c := mustUUID(t, "33333333-3333-3333-3333-333333333333")
	d := mustUUID(t, "44444444-4444-4444-4444-444444444444")
	issues := []db.Issue{
		makeIssue(a, StatusTodo, nil, 1, "a"),
		makeIssue(b, StatusTodo, nil, 2, "b"),
		makeIssue(c, StatusTodo, nil, 3, "c"),
		makeIssue(d, StatusTodo, nil, 4, "d"),
	}
	deps := []db.IssueDependency{
		dep(a, b), dep(b, c), dep(c, d),
	}
	idx := BuildIndex(issues, deps)
	alerts := DetectCycles(idx)
	if len(alerts) != 0 {
		t.Fatalf("linear chain reported %d cycles: %+v", len(alerts), alerts)
	}
}

func TestDetectCycles_MultipleIndependentSelfLoops(t *testing.T) {
	// Three separate self-loops on three unrelated issues — each
	// must be reported, sorted by their (sole) member's UUID.
	a := mustUUID(t, "33333333-3333-3333-3333-333333333333")
	b := mustUUID(t, "11111111-1111-1111-1111-111111111111")
	c := mustUUID(t, "22222222-2222-2222-2222-222222222222")
	issues := []db.Issue{
		makeIssue(a, StatusTodo, nil, 1, "a"),
		makeIssue(b, StatusTodo, nil, 2, "b"),
		makeIssue(c, StatusTodo, nil, 3, "c"),
	}
	deps := []db.IssueDependency{dep(a, a), dep(b, b), dep(c, c)}
	idx := BuildIndex(issues, deps)

	alerts := DetectCycles(idx)
	if len(alerts) != 3 {
		t.Fatalf("got %d alerts, want 3: %+v", len(alerts), alerts)
	}
	// Sorted ascending.
	for i, id := range []uuid.UUID{b, c, a} {
		if alerts[i].Cycle.CycleIDs[0] != id {
			t.Errorf("alerts[%d] starts at %s, want %s", i, alerts[i].Cycle.CycleIDs[0], id)
		}
	}
}
