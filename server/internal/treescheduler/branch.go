// Package treescheduler computes dispatch order for issue trees. It is the
// tree-aware replacement for the flat ready-issue scan the legacy scheduler
// performed. B-2.1 (ready computation) and B-2.2 (block avoidance + branch
// switching) live side by side here so the autopilot can call a single
// entry point that returns a ranked, block-aware dispatch list.
//
// B-2.2 spec (from SMA-36399):
//
//	blocked提blocker优先;解不了换同parent另一ready叶子
//
// Translation: when the leaf the scheduler is about to dispatch is blocked
// by a depends_on, raise the blocker's priority. If the blocker is itself
// stuck (blocked / cancelled / lost) and the leaf cannot be unblocked in
// the current tick, switch to a different ready leaf that shares the same
// parent. The output of this file is a list of Avoidance actions the
// caller (B-3 autopilot) applies before claiming a lease.
//
// All functions in this file are pure: they take value slices in, return
// value slices out, perform no I/O, and are safe to call concurrently.
// Tests in branch_test.go pin this contract.
package treescheduler

import (
	"sort"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// Issue / dependency status strings. Mirrors the inline strings used in
// service/issue.go and the autopilot — the treescheduler stays
// transport-agnostic and re-states the constants so it can be unit-tested
// without spinning up the daemon.
const (
	StatusTodo       = "todo"
	StatusInProgress = "in_progress"
	StatusInReview   = "in_review"
	StatusBlocked    = "blocked"
	StatusBacklog    = "backlog"
	StatusDone       = "done"
	StatusCancelled  = "cancelled"

	// DepTypeBlocks is the only dependency type that gates ready; the
	// scheduler ignores "related" and "supersedes" for ordering
	// decisions. Cross-references those edges create are surfaced to
	// the map view, not to dispatch.
	DepTypeBlocks = "blocks"
)

// IsTerminalStatus reports whether a status is terminal. Backlog and
// blocked are intentionally NOT terminal — both can transition out (to
// todo / in_progress respectively) and may still hold a ready signal in
// the next tick.
func IsTerminalStatus(s string) bool {
	return s == StatusDone || s == StatusCancelled
}

// IsOpenStatus reports whether the issue is "in the work queue" — neither
// terminal, parked in backlog, nor stuck. A backlog issue is not yet
// ready for dispatch; a stuck (Blocked) issue is in the work queue but
// cannot make progress on its own, so promoting its priority will not
// unblock anything that depends on it. The split matters for B-2.2:
// promote-vs-switch hinges on whether the blocker is in IsOpenStatus
// (Todo / InProgress / InReview) — only those states can actually
// resolve from a priority bump.
func IsOpenStatus(s string) bool {
	switch s {
	case StatusTodo, StatusInProgress, StatusInReview:
		return true
	}
	return false
}

// IssueNode is the in-memory view of one issue plus the back-pointers
// the scheduler needs to walk the tree. The struct is built once per
// tick by BuildIndex and shared by ready (B-2.1) and branch (this file)
// calculations.
type IssueNode struct {
	Issue      db.Issue
	ChildIDs   map[uuid.UUID]struct{} // children: parent_issue_id == this
	BlockerIDs map[uuid.UUID]struct{} // depends_on_issue_id where type == "blocks"
}

// parent returns the parent issue ID and a flag indicating whether the
// issue actually has one. Returning (uuid.UUID, bool) is necessary
// because the zero UUID is a valid test fixture — using uuid.Nil as a
// "no parent" sentinel collapses two distinct states (no parent vs
// parent == the nil UUID). Anything walking the tree must respect the
// bool, not the value.
func (n *IssueNode) parent() (uuid.UUID, bool) {
	if !n.Issue.ParentIssueID.Valid {
		return uuid.Nil, false
	}
	var u uuid.UUID
	copy(u[:], n.Issue.ParentIssueID.Bytes[:])
	return u, true
}

// BuildIndex constructs the in-memory tree index from a flat issue list
// and a flat dependency list. Issues missing from `issues` are silently
// dropped from the blocker sets (the caller can detect this with
// MissingBlockers below). The function is O(N + D) and never panics.
func BuildIndex(issues []db.Issue, deps []db.IssueDependency) map[uuid.UUID]*IssueNode {
	idx := make(map[uuid.UUID]*IssueNode, len(issues))
	for i := range issues {
		iss := issues[i]
		if !iss.ID.Valid {
			continue
		}
		var id uuid.UUID
		copy(id[:], iss.ID.Bytes[:])
		idx[id] = &IssueNode{
			Issue:      iss,
			ChildIDs:   map[uuid.UUID]struct{}{},
			BlockerIDs: map[uuid.UUID]struct{}{},
		}
	}
	for i := range deps {
		d := deps[i]
		if d.Type != DepTypeBlocks {
			continue
		}
		if !d.IssueID.Valid || !d.DependsOnIssueID.Valid {
			continue
		}
		var owner, blocker uuid.UUID
		copy(owner[:], d.IssueID.Bytes[:])
		copy(blocker[:], d.DependsOnIssueID.Bytes[:])
		node, ok := idx[owner]
		if !ok {
			continue
		}
		// Even if the blocker is missing from `idx`, we record it so
		// the avoidance layer can surface a "missing blocker" warning
		// to the caller. BuildIndex does not invent a stub node —
		// doing so would corrupt priority lookups elsewhere.
		node.BlockerIDs[blocker] = struct{}{}
	}
	for i := range issues {
		iss := issues[i]
		if !iss.ID.Valid || !iss.ParentIssueID.Valid {
			continue
		}
		var parent, child uuid.UUID
		copy(parent[:], iss.ParentIssueID.Bytes[:])
		copy(child[:], iss.ID.Bytes[:])
		if p, ok := idx[parent]; ok {
			p.ChildIDs[child] = struct{}{}
		}
	}
	return idx
}

// MissingBlockers returns blocker IDs that appear in the index but have
// no corresponding issue row. A "blocks" edge pointing at a missing issue
// is a data-integrity bug; the caller (autopilot) should log + skip
// rather than loop forever.
func MissingBlockers(idx map[uuid.UUID]*IssueNode) []uuid.UUID {
	var missing []uuid.UUID
	for _, n := range idx {
		for bid := range n.BlockerIDs {
			if _, ok := idx[bid]; !ok {
				missing = append(missing, bid)
			}
		}
	}
	sort.Slice(missing, func(i, j int) bool { return uuidLess(missing[i], missing[j]) })
	return missing
}

// AvoidanceKind classifies the per-leaf recommendation produced by
// Plan. The autopilot interprets these in the listed order; new kinds
// should be appended, never reordered, to keep log greps stable.
type AvoidanceKind int

const (
	// AvoidanceProceed: leaf is fine, dispatch as-is. Most leaves end
	// up here.
	AvoidanceProceed AvoidanceKind = iota
	// AvoidancePromoteBlocker: leaf has at least one unresolved
	// blocker; raise that blocker's priority and skip the leaf this
	// tick.
	AvoidancePromoteBlocker
	// AvoidanceSwitch: leaf is hopelessly blocked (its blockers are
	// themselves blocked / cancelled / missing and will not resolve
	// soon). Recommend dispatching SiblingID instead.
	AvoidanceSwitch
)

// String renders the kind in a stable, log-friendly form. Used by
// tests and by the autopilot when it writes audit rows.
func (k AvoidanceKind) String() string {
	switch k {
	case AvoidanceProceed:
		return "proceed"
	case AvoidancePromoteBlocker:
		return "promote_blocker"
	case AvoidanceSwitch:
		return "switch"
	}
	return "unknown"
}

// LeafAvoidance is the per-leaf recommendation. The zero value is a
// "Proceed" recommendation (IssueID set, Kind=AvoidanceProceed, all
// other fields empty).
type LeafAvoidance struct {
	IssueID    uuid.UUID
	Kind       AvoidanceKind
	Reason     string
	BlockerIDs []uuid.UUID // promote_blocker or switch: the hopeless blockers
	SiblingID  uuid.UUID   // switch only: recommended sibling to dispatch instead
}

// Plan runs block-avoidance + branch-switching over the supplied ready
// leaves. It is a pure function of (issues, deps, ready) and returns one
// LeafAvoidance per ready entry, in the same order.
//
// The algorithm:
//  1. For each ready leaf, walk its "blocks" dependencies.
//  2. Any blocker whose status is not Done / Cancelled makes the leaf
//     "currently blocked" — emit AvoidancePromoteBlocker with the
//     unresolved blocker IDs (deduplicated, sorted).
//  3. If every blocker is itself hopeless (Blocked / Cancelled / missing
//     from the index), the leaf cannot make progress this tick. Look
//     for a sibling under the same parent that B-2.1 marked ready and
//     is itself in AvoidanceProceed state — emit AvoidanceSwitch.
//  4. If no usable sibling exists, still emit AvoidanceSwitch with
//     SiblingID = uuid.Nil; the autopilot's contract is "best effort
//     switch" so an empty recommendation must be auditable, not
//     silently downgraded to "proceed".
func Plan(issues []db.Issue, deps []db.IssueDependency, ready []ReadyLeaf) []LeafAvoidance {
	idx := BuildIndex(issues, deps)
	out := make([]LeafAvoidance, len(ready))

	// Pre-compute the set of leaves that look clean so the Switch
	// branch can pick a sibling whose own blockers are clean. The set
	// is what the switch picker consults; per-leaf verdicts are
	// re-derived by recommendFor so the output is self-consistent.
	proceedSet := make(map[uuid.UUID]struct{}, len(ready))
	for _, r := range ready {
		if verdictFor(idx, r.IssueID) == AvoidanceProceed {
			proceedSet[r.IssueID] = struct{}{}
		}
	}

	for i, r := range ready {
		out[i] = recommendFor(idx, r.IssueID, proceedSet)
	}
	return out
}

// ReadyLeaf is the input shape for Plan. Only the issue ID is consulted
// by the avoidance algorithm; the priority rank is reserved for the B-3
// autopilot to thread back into its dispatch order.
type ReadyLeaf struct {
	IssueID      uuid.UUID
	PriorityRank int
}

// verdictFor returns the AvoidanceKind that would apply if `id` were
// the leaf under dispatch. Exposed for the readiness index only —
// callers should usually call Plan instead.
//
// The Proceed short-circuit is `len(unresolved) == 0`, NOT
// `len(hopeless) == 0`. Conflating the two would let a leaf with an
// in-progress blocker pass the proceed gate and pollute the sibling
// picker with leaves that are not actually ready to dispatch. (The
// regression was caught by TestPlan_SwitchFallsBackWhenNoUsableSibling:
// "also blocked sibling" with an InProgress blocker must not appear
// in the switch pool.)
func verdictFor(idx map[uuid.UUID]*IssueNode, id uuid.UUID) AvoidanceKind {
	node, ok := idx[id]
	if !ok {
		// Issue disappeared between scan and plan: treat as proceed so
		// the autopilot logs the anomaly but does not deadlock.
		return AvoidanceProceed
	}
	if node.Issue.Status != StatusTodo {
		// Only todo leaves participate in this layer. B-2.1 should
		// have already filtered, but Plan is robust if it didn't.
		return AvoidanceProceed
	}
	unresolved, _ := classifyBlockers(idx, node)
	if len(unresolved) == 0 {
		return AvoidanceProceed
	}
	// Unresolved blockers present — distinguish "promote" from "switch"
	// based on whether any blocker is in a status that can still
	// resolve (Todo / InProgress / InReview). If yes, promoting
	// might unblock the chain. If every blocker is stuck-or-terminal,
	// promote is wasted motion and the leaf should switch.
	if anyOpen(unresolved, idx) {
		return AvoidancePromoteBlocker
	}
	return AvoidanceSwitch
}

func recommendFor(idx map[uuid.UUID]*IssueNode, id uuid.UUID, proceedSet map[uuid.UUID]struct{}) LeafAvoidance {
	rec := LeafAvoidance{IssueID: id, Kind: AvoidanceProceed}
	node, ok := idx[id]
	if !ok {
		rec.Reason = "issue missing from index; treating as proceed"
		return rec
	}
	if node.Issue.Status != StatusTodo {
		rec.Reason = "non-todo status; ready filter should have excluded"
		return rec
	}
	unresolved, hopeless := classifyBlockers(idx, node)
	if len(unresolved) == 0 {
		rec.Reason = "all blockers resolved"
		return rec
	}
	rec.BlockerIDs = append([]uuid.UUID(nil), hopeless...)

	// Decide promote vs switch. The cut: if any blocker is still in an
	// open status (todo / in_progress / in_review), promotion can
	// unblock the leaf — emit Promote. If every blocker is in a
	// stuck-or-terminal status, promotion is wasted motion — emit
	// Switch and try to pick a sibling.
	if anyOpen(unresolved, idx) {
		rec.Kind = AvoidancePromoteBlocker
		rec.Reason = "has open blockers; raise their priority this tick"
		// Override BlockerIDs to the broader set so the caller can
		// promote every unresolved blocker, not just the hopeless
		// tail. The "hopeless" subset is informational and can be
		// derived from status.
		rec.BlockerIDs = append(rec.BlockerIDs[:0], unresolved...)
		return rec
	}

	rec.Kind = AvoidanceSwitch
	rec.Reason = "all blockers are stuck; switch to ready sibling under same parent"
	if sibling := pickSwitchSibling(idx, node, proceedSet); sibling != uuid.Nil {
		rec.SiblingID = sibling
	}
	return rec
}

// classifyBlockers returns two sorted, deduplicated slices: every
// unresolved blocker (status not Done/Cancelled, and present in the
// index) and the subset of those that are also hopeless (Blocked /
// Cancelled / not in the index). The two are kept separate so the
// caller can promote a broader set than the hopeless tail.
func classifyBlockers(idx map[uuid.UUID]*IssueNode, node *IssueNode) (unresolved, hopeless []uuid.UUID) {
	seen := make(map[uuid.UUID]struct{}, len(node.BlockerIDs))
	for bid := range node.BlockerIDs {
		if _, dup := seen[bid]; dup {
			continue
		}
		seen[bid] = struct{}{}
		bn, ok := idx[bid]
		if !ok {
			// Missing from the index — a data-integrity bug, but
			// practically "hopeless" because we cannot raise its
			// priority without a row to update.
			unresolved = append(unresolved, bid)
			hopeless = append(hopeless, bid)
			continue
		}
		if IsTerminalStatus(bn.Issue.Status) {
			// Done / Cancelled are already accounted for by
			// B-2.1's "all deps done" check; if we see them
			// here it means B-2.1 said ready but a dep moved
			// under us. Skip — they're not blockers anymore.
			continue
		}
		unresolved = append(unresolved, bid)
		if bn.Issue.Status == StatusBlocked {
			hopeless = append(hopeless, bid)
		}
	}
	sort.Slice(unresolved, func(i, j int) bool { return uuidLess(unresolved[i], unresolved[j]) })
	sort.Slice(hopeless, func(i, j int) bool { return uuidLess(hopeless[i], hopeless[j]) })
	return unresolved, hopeless
}

// anyOpen reports whether any blocker ID in `ids` corresponds to an
// issue whose status is in the open set. Used to decide promote vs
// switch: if the hopeless blockers are themselves in a non-terminal
// state (todo / in_progress), raising their priority might still
// resolve the chain, so the leaf should wait.
func anyOpen(ids []uuid.UUID, idx map[uuid.UUID]*IssueNode) bool {
	for _, id := range ids {
		n, ok := idx[id]
		if !ok {
			// Missing blockers are never "open" in the status
			// sense; treat as hopeless (no way to promote them).
			continue
		}
		if IsOpenStatus(n.Issue.Status) {
			return true
		}
	}
	return false
}

// pickSwitchSibling returns the first ready sibling of `node` that the
// caller is allowed to dispatch instead. It scans the parent branch in
// issue-number order (older first) so the recommendation is stable
// across runs — useful for snapshot tests.
//
// Order rationale: issue numbers monotonically increase with creation
// time, so "older first" matches "the work the team was already looking
// at first". Same-project ties are broken by title to keep two siblings
// with the same number sortable in a deterministic way.
func pickSwitchSibling(idx map[uuid.UUID]*IssueNode, node *IssueNode, proceedSet map[uuid.UUID]struct{}) uuid.UUID {
	parentID, ok := node.parent()
	if !ok {
		return uuid.Nil
	}
	parent, found := idx[parentID]
	if !found {
		return uuid.Nil
	}
	var candidates []uuid.UUID
	for cid := range parent.ChildIDs {
		if cid == issueUUID(node.Issue) {
			continue
		}
		if _, ok := proceedSet[cid]; !ok {
			continue
		}
		candidates = append(candidates, cid)
	}
	if len(candidates) == 0 {
		return uuid.Nil
	}
	sort.Slice(candidates, func(i, j int) bool {
		a, b := idx[candidates[i]], idx[candidates[j]]
		if a.Issue.Number != b.Issue.Number {
			return a.Issue.Number < b.Issue.Number
		}
		if a.Issue.Title != b.Issue.Title {
			return a.Issue.Title < b.Issue.Title
		}
		return uuidLess(candidates[i], candidates[j])
	})
	return candidates[0]
}

// uuidLess compares two UUIDs lexicographically over their byte
// representation. Centralised so all sort.Slice call sites stay
// consistent — switching keying between UUID byte slices and string
// forms has bitten us in past cycles.
func uuidLess(a, b uuid.UUID) bool {
	for i := range a {
		if a[i] != b[i] {
			return a[i] < b[i]
		}
	}
	return false
}

// issueUUID extracts the canonical uuid.UUID from a db.Issue's
// pgtype.ID. Lives as a free function (not a method) because Go
// forbids adding methods to types declared in other packages; doing
// it here keeps the rest of the file from repeating the byte copy.
func issueUUID(iss db.Issue) uuid.UUID {
	if !iss.ID.Valid {
		return uuid.Nil
	}
	var u uuid.UUID
	copy(u[:], iss.ID.Bytes[:])
	return u
}

// pgtypeUUIDFrom turns a uuid.UUID into a pgtype.UUID so the file can
// stay self-contained when callers (B-2.1, B-3) hand it raw issue IDs.
// Not exported — the treescheduler is read-only and never writes issue
// rows itself.
func pgtypeUUIDFrom(u uuid.UUID) pgtype.UUID {
	if u == uuid.Nil {
		return pgtype.UUID{}
	}
	var p pgtype.UUID
	copy(p.Bytes[:], u[:])
	p.Valid = true
	return p
}
