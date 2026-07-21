package service

import (
	"context"
	"errors"
	"log/slog"
	"sort"
	"sync"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
	"github.com/multica-ai/multica/server/internal/treescheduler"
)

// stubHarness is the in-memory dependency container for tree dispatch
// tests. Each callback field records the calls it received so tests can
// assert on dispatch ordering. The IssueFetcher / DepFetcher are
// pre-populated slices so tests don't have to wire pagination.
type stubHarness struct {
	mu sync.Mutex

	issues []db.Issue
	deps   []db.IssueDependency

	proceeded        []uuid.UUID
	promotedBlockers [][]uuid.UUID
	switchedTo       []uuid.UUID
	escalated        []uuid.UUID
	escalatedReasons []string
	cycleAlerts      []treescheduler.CycleAlert
}

func (s *stubHarness) fetchIssues(_ context.Context, offset, limit int) (treescheduler.IssuePage, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	end := offset + limit
	if end > len(s.issues) {
		end = len(s.issues)
	}
	if offset >= len(s.issues) {
		return treescheduler.IssuePage{Issues: nil, Total: int64(len(s.issues))}, nil
	}
	return treescheduler.IssuePage{Issues: s.issues[offset:end], Total: int64(len(s.issues))}, nil
}

func (s *stubHarness) fetchDeps(_ context.Context) ([]db.IssueDependency, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]db.IssueDependency(nil), s.deps...), nil
}

func (s *stubHarness) onProceed(_ context.Context, iss db.Issue) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.proceeded = append(s.proceeded, issueIDOf(iss))
	return nil
}

func (s *stubHarness) onPromote(_ context.Context, blockers []uuid.UUID) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := append([]uuid.UUID(nil), blockers...)
	sort.Slice(cp, func(i, j int) bool { return cp[i].String() < cp[j].String() })
	s.promotedBlockers = append(s.promotedBlockers, cp)
	return nil
}

func (s *stubHarness) onSwitch(_ context.Context, sibling uuid.UUID, _ db.Issue) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.switchedTo = append(s.switchedTo, sibling)
	return nil
}

func (s *stubHarness) onEscalate(_ context.Context, iss db.Issue, reason string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.escalated = append(s.escalated, issueIDOf(iss))
	s.escalatedReasons = append(s.escalatedReasons, reason)
	return nil
}

func (s *stubHarness) onCycles(_ context.Context, alerts []treescheduler.CycleAlert) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.cycleAlerts = alerts
	return nil
}

// issueIDOf pulls the canonical UUID out of a db.Issue. Tests use
// raw UUID literals for fixtures; the production codepath goes through
// pgtype.UUID. This helper keeps the test code readable.
func issueIDOf(iss db.Issue) uuid.UUID {
	if !iss.ID.Valid {
		return uuid.Nil
	}
	var u uuid.UUID
	copy(u[:], iss.ID.Bytes[:])
	return u
}

// pgID is the inverse of issueIDOf: it wraps a uuid.UUID into the
// pgtype.UUID the db.Issue ID field uses.
func pgID(u uuid.UUID) pgtype.UUID {
	if u == uuid.Nil {
		return pgtype.UUID{}
	}
	var p pgtype.UUID
	copy(p.Bytes[:], u[:])
	p.Valid = true
	return p
}

// makeTreeIssue is the in-test fixture builder for db.Issue. Number
// is fixed so tests can deterministically assert priority tie-breaks.
func makeTreeIssue(id uuid.UUID, status, priority string, parent *uuid.UUID, number int32) db.Issue {
	var pid pgtype.UUID
	if parent != nil {
		copy(pid.Bytes[:], parent[:])
		pid.Valid = true
	}
	return db.Issue{
		ID:            pgID(id),
		Status:        status,
		Priority:      priority,
		ParentIssueID: pid,
		Number:        number,
		Title:         "issue-" + id.String()[:8],
	}
}

func makeTreeDep(owner, blocker uuid.UUID) db.IssueDependency {
	return db.IssueDependency{
		IssueID:          pgID(owner),
		DependsOnIssueID: pgID(blocker),
		Type:             treescheduler.DepTypeBlocks,
	}
}

func newTestDispatcher(h *stubHarness) *TreeDispatcher {
	return &TreeDispatcher{
		IssueFetcher:    h.fetchIssues,
		DepFetcher:      h.fetchDeps,
		OnProceed:       h.onProceed,
		OnPromoteBlocker: h.onPromote,
		OnSwitch:        h.onSwitch,
		OnEscalate:      h.onEscalate,
		OnCycles:        h.onCycles,
		Logger:          slog.New(slog.NewTextHandler(testLogWriter{}, nil)),
	}
}

// TestDispatcher_ProceedForReadyLeaf verifies the happy path: a
// single todo leaf with no blockers dispatches via OnProceed.
func TestDispatcher_ProceedForReadyLeaf(t *testing.T) {
	leaf := uuid.New()
	h := &stubHarness{
		issues: []db.Issue{makeTreeIssue(leaf, treescheduler.StatusTodo, "medium", nil, 1)},
	}
	d := newTestDispatcher(h)
	if err := d.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if len(h.proceeded) != 1 || h.proceeded[0] != leaf {
		t.Errorf("proceeded=%v want [%s]", h.proceeded, leaf)
	}
	if len(h.promotedBlockers) != 0 {
		t.Errorf("unexpected promote_blocker calls: %v", h.promotedBlockers)
	}
	if len(h.switchedTo) != 0 {
		t.Errorf("unexpected switch calls: %v", h.switchedTo)
	}
	if len(h.escalated) != 0 {
		t.Errorf("unexpected escalations: %v", h.escalated)
	}
}

// TestDispatcher_PromoteBlockerWhenBlockerOpen: leaf has an open
// blocker; expect OnPromoteBlocker with the blocker ID, no
// OnProceed / OnSwitch / OnEscalate calls.
func TestDispatcher_PromoteBlockerWhenBlockerOpen(t *testing.T) {
	leaf := uuid.New()
	blocker := uuid.New()
	h := &stubHarness{
		issues: []db.Issue{
			makeTreeIssue(leaf, treescheduler.StatusTodo, "medium", nil, 1),
			makeTreeIssue(blocker, treescheduler.StatusInProgress, "high", nil, 2),
		},
		deps: []db.IssueDependency{makeTreeDep(leaf, blocker)},
	}
	d := newTestDispatcher(h)
	if err := d.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if len(h.promotedBlockers) != 1 {
		t.Fatalf("promotedBlockers=%v want 1 entry", h.promotedBlockers)
	}
	if len(h.promotedBlockers[0]) != 1 || h.promotedBlockers[0][0] != blocker {
		t.Errorf("promoted blockers=%v want [%s]", h.promotedBlockers[0], blocker)
	}
	if len(h.proceeded) != 0 {
		t.Errorf("proceeded=%v want [] (leaf has open blocker)", h.proceeded)
	}
}

// TestDispatcher_SwitchWhenAllBlockersHopeless: leaf has only stuck
// blockers; a sibling under the same parent is ready. Expect
// OnSwitch called with the sibling ID.
func TestDispatcher_SwitchWhenAllBlockersHopeless(t *testing.T) {
	parent := uuid.New()
	leaf := uuid.New()
	sibling := uuid.New()
	stuckBlocker := uuid.New()
	h := &stubHarness{
		issues: []db.Issue{
			makeTreeIssue(parent, treescheduler.StatusInProgress, "high", nil, 0),
			makeTreeIssue(leaf, treescheduler.StatusTodo, "medium", &parent, 1),
			makeTreeIssue(sibling, treescheduler.StatusTodo, "medium", &parent, 2),
			makeTreeIssue(stuckBlocker, treescheduler.StatusBlocked, "low", nil, 3),
		},
		deps: []db.IssueDependency{makeTreeDep(leaf, stuckBlocker)},
	}
	d := newTestDispatcher(h)
	if err := d.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	// Two ready leaves: leaf (switch) + sibling (proceed).
	if len(h.switchedTo) != 1 || h.switchedTo[0] != sibling {
		t.Errorf("switchedTo=%v want [%s]", h.switchedTo, sibling)
	}
	if len(h.proceeded) != 1 || h.proceeded[0] != sibling {
		t.Errorf("proceeded=%v want [%s] (sibling must also dispatch)", h.proceeded, sibling)
	}
	if len(h.escalated) != 0 {
		t.Errorf("escalated=%v want [] (a usable sibling existed)", h.escalated)
	}
}

// TestDispatcher_SwitchFallsBackToEscalate: leaf has stuck blockers
// and no usable sibling. The Switch plan with SiblingID=uuid.Nil
// MUST trigger OnEscalate — not a busy-wait retry.
func TestDispatcher_SwitchFallsBackToEscalate(t *testing.T) {
	parent := uuid.New()
	leaf := uuid.New()
	otherSibling := uuid.New()
	stuckBlocker := uuid.New()
	otherBlocker := uuid.New()
	h := &stubHarness{
		issues: []db.Issue{
			makeTreeIssue(parent, treescheduler.StatusInProgress, "high", nil, 0),
			makeTreeIssue(leaf, treescheduler.StatusTodo, "medium", &parent, 1),
			makeTreeIssue(otherSibling, treescheduler.StatusTodo, "medium", &parent, 2),
			makeTreeIssue(stuckBlocker, treescheduler.StatusBlocked, "low", nil, 3),
			makeTreeIssue(otherBlocker, treescheduler.StatusInProgress, "low", nil, 4),
		},
		deps: []db.IssueDependency{
			makeTreeDep(leaf, stuckBlocker),
			makeTreeDep(otherSibling, otherBlocker),
		},
	}
	d := newTestDispatcher(h)
	if err := d.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if len(h.escalated) != 1 || h.escalated[0] != leaf {
		t.Errorf("escalated=%v want [%s] — Switch+uuid.Nil MUST escalate", h.escalated, leaf)
	}
	if len(h.proceeded) != 0 {
		t.Errorf("proceeded=%v want [] (no usable leaf)", h.proceeded)
	}
	if len(h.switchedTo) != 0 {
		t.Errorf("switchedTo=%v want [] (sibling not usable)", h.switchedTo)
	}
	if len(h.escalatedReasons) != 1 {
		t.Fatalf("expected 1 escalation reason")
	}
	// The reason MUST mention the lack of a switch target so the
	// operator doesn't have to grep code to know what to do.
	if !contains(h.escalatedReasons[0], "switch") {
		t.Errorf("escalation reason %q does not mention switch; operator needs actionable text", h.escalatedReasons[0])
	}
}

// TestDispatcher_NeverBusyWaits: even when the loop is invoked
// multiple times in a row, the Switch+uuid.Nil path always escalates
// rather than re-attempting the same leaf. This is the deadlock
// defense called out in the swarm prep.
func TestDispatcher_NeverBusyWaits(t *testing.T) {
	parent := uuid.New()
	leaf := uuid.New()
	otherSibling := uuid.New()
	stuckBlocker := uuid.New()
	otherBlocker := uuid.New()
	h := &stubHarness{
		issues: []db.Issue{
			makeTreeIssue(parent, treescheduler.StatusInProgress, "high", nil, 0),
			makeTreeIssue(leaf, treescheduler.StatusTodo, "medium", &parent, 1),
			makeTreeIssue(otherSibling, treescheduler.StatusTodo, "medium", &parent, 2),
			makeTreeIssue(stuckBlocker, treescheduler.StatusBlocked, "low", nil, 3),
			makeTreeIssue(otherBlocker, treescheduler.StatusInProgress, "low", nil, 4),
		},
		deps: []db.IssueDependency{
			makeTreeDep(leaf, stuckBlocker),
			makeTreeDep(otherSibling, otherBlocker),
		},
	}
	d := newTestDispatcher(h)
	for i := 0; i < 3; i++ {
		if err := d.RunOnce(context.Background()); err != nil {
			t.Fatalf("RunOnce iteration %d: %v", i, err)
		}
	}
	if got := len(h.escalated); got != 3 {
		t.Errorf("escalated=%d, want 3 (one per tick — no busy-wait retry)", got)
	}
	if got := len(h.proceeded); got != 0 {
		t.Errorf("proceeded=%d, want 0 across 3 ticks", got)
	}
}

// TestDispatcher_DetectsCycleAndSurfacesAlert: a self-loop must be
// reported through OnCycles (the gate); the dispatcher still runs the
// rest of the loop because dispatch is gated on cycles being
// human-handled, not on cycles being absent.
func TestDispatcher_DetectsCycleAndSurfacesAlert(t *testing.T) {
	a := uuid.New()
	h := &stubHarness{
		issues: []db.Issue{makeTreeIssue(a, treescheduler.StatusTodo, "medium", nil, 1)},
		deps:   []db.IssueDependency{makeTreeDep(a, a)}, // self-loop
	}
	d := newTestDispatcher(h)
	if err := d.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if len(h.cycleAlerts) != 1 {
		t.Fatalf("cycleAlerts=%d, want 1", len(h.cycleAlerts))
	}
	if !h.cycleAlerts[0].Cycle.SelfLoop {
		t.Errorf("cycle.SelfLoop=false; want true (self-loop fixture)")
	}
	if h.cycleAlerts[0].SuggestedIgnoreFrom != a || h.cycleAlerts[0].SuggestedIgnoreTo != a {
		t.Errorf("suggested ignore edge=(%s -> %s), want (%s -> %s)",
			h.cycleAlerts[0].SuggestedIgnoreFrom, h.cycleAlerts[0].SuggestedIgnoreTo, a, a)
	}
}

// TestDispatcher_NilFetcherFailsFast: a missing IssueFetcher is a
// configuration bug; the dispatcher returns an error rather than
// panicking or no-op'ing silently.
func TestDispatcher_NilFetcherFailsFast(t *testing.T) {
	d := &TreeDispatcher{
		Logger: slog.New(slog.NewTextHandler(testLogWriter{}, nil)),
	}
	err := d.RunOnce(context.Background())
	if err == nil {
		t.Fatal("RunOnce with nil IssueFetcher must error, not silently no-op")
	}
}

// TestDispatcher_PropagatesFetcherError: any error from the fetcher
// bubbles up so the caller (cron tick, manual trigger) can record a
// skipped run.
func TestDispatcher_PropagatesFetcherError(t *testing.T) {
	want := errors.New("boom")
	d := &TreeDispatcher{
		IssueFetcher: func(_ context.Context, _, _ int) (treescheduler.IssuePage, error) {
			return treescheduler.IssuePage{}, want
		},
		DepFetcher: func(_ context.Context) ([]db.IssueDependency, error) {
			return nil, nil
		},
		Logger: slog.New(slog.NewTextHandler(testLogWriter{}, nil)),
	}
	err := d.RunOnce(context.Background())
	if !errors.Is(err, want) {
		t.Errorf("RunOnce err=%v, want wraps %v", err, want)
	}
}

// TestDispatcher_MultipleLeavesProcessedInOrder: three ready leaves
// should all dispatch in one tick (priority desc).
func TestDispatcher_MultipleLeavesProcessedInOrder(t *testing.T) {
	high := uuid.New()
	med := uuid.New()
	low := uuid.New()
	h := &stubHarness{
		issues: []db.Issue{
			makeTreeIssue(low, treescheduler.StatusTodo, "low", nil, 3),
			makeTreeIssue(med, treescheduler.StatusTodo, "medium", nil, 2),
			makeTreeIssue(high, treescheduler.StatusTodo, "high", nil, 1),
		},
	}
	d := newTestDispatcher(h)
	if err := d.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	want := []uuid.UUID{high, med, low}
	if len(h.proceeded) != len(want) {
		t.Fatalf("proceeded=%v want %v", h.proceeded, want)
	}
	for i, w := range want {
		if h.proceeded[i] != w {
			t.Errorf("position %d: got %s want %s", i, h.proceeded[i], w)
		}
	}
}

// contains is a tiny strings.Contains helper to keep imports tight.
func contains(haystack, needle string) bool {
	if len(needle) == 0 {
		return true
	}
	for i := 0; i+len(needle) <= len(haystack); i++ {
		if haystack[i:i+len(needle)] == needle {
			return true
		}
	}
	return false
}

// testLogWriter is an io.Writer that drops log output. Real tests
// rely on the structured Logger.Info path; this writer keeps test
// runs quiet without bringing in a whole logging library.
type testLogWriter struct{}

func (testLogWriter) Write(p []byte) (int, error) { return len(p), nil }