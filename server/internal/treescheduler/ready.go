// B-2.1 ready-leaf calculation. Pure: takes the flat issue list and
// the flat dependency list, returns one ReadyLeaf per todo issue
// (regardless of dependency state). The "ready" semantics here are
// the scheduler's "candidate for dispatch this tick" — Plan() then
// applies block-avoidance to recommend Proceed / PromoteBlocker /
// Switch per leaf.
//
// Why include leaves with open blockers? Plan() emits PromoteBlocker
// when a leaf has an unresolved blocker (see branch_test.go:
// TestPlan_PromoteBlockerWhenBlockerOpen). If B-2.1 filtered those
// out, PromoteBlocker would be unreachable and the autopilot could
// never raise a stuck dependency's priority. Likewise, a leaf with
// only hopeless blockers must reach Plan() so Switch + escalation can
// fire. B-2.1's only job is to surface candidates; avoidance is
// B-2.2's.
//
// Sort order is deterministic and ranks for dispatch:
//
//	priority desc ("high"=3, "medium"=2, "low"=1, other=0)
//	  issue number asc (older work first)
//	    uuid asc (tiebreaker for snapshot stability)
//
// The function never touches I/O and is safe to call concurrently.
package treescheduler

import (
	"sort"

	"github.com/google/uuid"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// priorityForIssue reads Issue.Priority into the same rank used by
// cycle.go's suggestIgnoredEdge, so "high" outranks "medium" outranks
// "low" outranks "" / unknown. Keeping the rank here as well lets the
// caller sort without re-implementing the mapping.
func priorityForIssue(iss db.Issue) int {
	switch iss.Priority {
	case "high":
		return 3
	case "medium":
		return 2
	case "low":
		return 1
	}
	return 0
}

// issueUUIDOf returns the canonical uuid.UUID for a db.Issue. Lives
// here (not as a method) for the same reason branch.go's
// free-function form is used: the type belongs to another package.
func issueUUIDOf(iss db.Issue) uuid.UUID {
	if !iss.ID.Valid {
		return uuid.Nil
	}
	var u uuid.UUID
	copy(u[:], iss.ID.Bytes[:])
	return u
}

// ComputeReadyLeaves returns one ReadyLeaf per todo issue, sorted for
// dispatch. The output is deterministic across runs over the same
// input (see TestComputeReadyLeaves_DeterministicAcrossRuns). Plan()
// applies block-avoidance to the result; the dispatcher interprets
// each Plan recommendation.
func ComputeReadyLeaves(issues []db.Issue, deps []db.IssueDependency) []ReadyLeaf {
	idx := BuildIndex(issues, deps)
	var out []ReadyLeaf
	for _, iss := range issues {
		id := issueUUIDOf(iss)
		if id == uuid.Nil {
			continue
		}
		if iss.Status != StatusTodo {
			continue
		}
		out = append(out, ReadyLeaf{
			IssueID:      id,
			PriorityRank: priorityForIssue(iss),
		})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].PriorityRank != out[j].PriorityRank {
			return out[i].PriorityRank > out[j].PriorityRank
		}
		// Tie-break by issue number (asc). Need to read it back from
		// the index because ReadyLeaf does not carry it.
		ni, oki := idx[out[i].IssueID]
		nj, okj := idx[out[j].IssueID]
		if oki && okj && ni.Issue.Number != nj.Issue.Number {
			return ni.Issue.Number < nj.Issue.Number
		}
		return uuidLess(out[i].IssueID, out[j].IssueID)
	})
	return out
}