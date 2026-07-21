package treescheduler

import (
	"sort"

	"github.com/google/uuid"
)

// Cycle describes a single directed cycle in the depends_on ∪ blocks
// graph built by BuildIndex.
//
// SelfLoop == true means the cycle has length 1 — exactly one issue
// depending on itself (u → u). CycleIDs has length 1 in that case. For
// length-≥2 cycles the members are the nodes of the strongly connected
// component; they all live on the same cycle but the path order is not
// preserved (Tarjan yields maximal SCCs, not literal loops). CycleIDs
// is sorted by uuidLess for stable test output and audit log diffs.
type Cycle struct {
	CycleIDs []uuid.UUID
	SelfLoop bool
}

// CycleAlert is the autopilot/human-facing record for one detected
// cycle. The autopilot (or a human reviewer) breaks the cycle by either
// marking the SuggestedIgnore edge as ignored or escalating.
//
// SuggestedIgnoreFrom -> SuggestedIgnoreTo is the edge owned by the
// lowest-priority member of the cycle — dropping it removes the cycle
// with the minimum priority loss. For self-loops both fields hold the
// same node (the self-edge u → u).
//
// MemberPriority is a per-member snapshot of Issue.Priority taken at
// detection time so the caller can render the alert without a second
// index lookup. Empty string means the issue had no priority recorded.
type CycleAlert struct {
	Cycle               Cycle
	SuggestedIgnoreFrom uuid.UUID
	SuggestedIgnoreTo   uuid.UUID
	MemberPriority      map[uuid.UUID]string
}

// priority rank: higher = more important. We pick the LOWEST-ranked
// member as the "edge to drop" owner — a low-priority node is the
// least painful to lose. Anything outside the known buckets collapses
// to 0 so unknown values don't pretend to be "low" silently.
func priorityRank(s string) int {
	switch s {
	case "high":
		return 3
	case "medium":
		return 2
	case "low":
		return 1
	}
	return 0
}

// DetectCycles runs Tarjan's strongly connected components algorithm
// over the graph implied by idx — edges go from a node to every member
// of its BlockerIDs set (i.e. the direction B-2.1's ready check walks:
// "u depends on v") — and returns one CycleAlert per directed cycle.
// Self-loops (u ∈ u.BlockerIDs) are detected first via a fast linear
// scan and reported distinctly (SelfLoop == true, CycleIDs length 1);
// the multi-node SCC pass then surfaces the larger cycles.
//
// The output is sorted so two runs over the same input yield identical
// slices — necessary for stable audit logs and for snapshot tests.
// Each member of a multi-node SCC is "on" the cycle (Tarjan returns
// maximal strongly connected subgraphs, which for our graph coincide
// with the cycle members in practice; nodes that fan-in to a cycle
// are NOT included).
//
// Performance: O(V + E) over the index, purely functional, no I/O.
// Safe to call concurrently with other readers of idx (the function
// does not mutate it).
//
// When the caller wires this into the autopilot dispatch tick, the
// returned alerts MUST be surfaced — autopilot/issue operators decide
// whether to ignore the suggested edge, mark the issue as blocked, or
// escalate. Silently dropping the result re-creates the B-2.4 deadlock
// mode this function exists to break.
func DetectCycles(idx map[uuid.UUID]*IssueNode) []CycleAlert {
	if len(idx) == 0 {
		return nil
	}

	// Phase 1 — fast path: explicit self-loop scan. O(V), no recursion,
	// so a self-loop-only workspace pays constant cost here.
	var alerts []CycleAlert
	seenInPhase1 := make(map[uuid.UUID]struct{})
	for uid, node := range idx {
		if _, has := node.BlockerIDs[uid]; !has {
			continue
		}
		alerts = append(alerts, CycleAlert{
			Cycle: Cycle{
				CycleIDs: []uuid.UUID{uid},
				SelfLoop: true,
			},
			SuggestedIgnoreFrom: uid,
			SuggestedIgnoreTo:   uid,
			MemberPriority:      map[uuid.UUID]string{uid: priorityOf(idx, uid)},
		})
		seenInPhase1[uid] = struct{}{}
	}

	// Phase 2 — Tarjan SCC over the same graph (self-edges stay in
	// place; Tarjan naturally produces a 1-node SCC for them but we
	// already reported them, so we filter).
	sccs := tarjanSCC(idx)

	for _, scc := range sccs {
		if len(scc) == 1 {
			uid := scc[0]
			if _, dup := seenInPhase1[uid]; dup {
				continue
			}
			// Singleton SCC without a self-edge — not a cycle.
			continue
		}
		// Multi-node SCC: every member sits on the cycle.
		sortedSCC := append([]uuid.UUID(nil), scc...)
		sort.Slice(sortedSCC, func(i, j int) bool { return uuidLess(sortedSCC[i], sortedSCC[j]) })

		memberPri := make(map[uuid.UUID]string, len(sortedSCC))
		for _, uid := range sortedSCC {
			memberPri[uid] = priorityOf(idx, uid)
		}

		owner, target := suggestIgnoredEdge(idx, sortedSCC)
		alerts = append(alerts, CycleAlert{
			Cycle: Cycle{
				CycleIDs: sortedSCC,
				SelfLoop: false,
			},
			SuggestedIgnoreFrom: owner,
			SuggestedIgnoreTo:   target,
			MemberPriority:      memberPri,
		})
	}

	// Sort the outer slice by lex-smallest member so callers get a
	// stable order. (Within Cycle, IDs are already sorted.)
	sort.Slice(alerts, func(i, j int) bool {
		return uuidLess(alerts[i].Cycle.CycleIDs[0], alerts[j].Cycle.CycleIDs[0])
	})
	return alerts
}

// priorityOf reads the priority string of the node with id `uid` from
// the index, returning "" if the node is missing (the ready layer will
// already have flagged a missing blocker; we don't crash on it here).
func priorityOf(idx map[uuid.UUID]*IssueNode, uid uuid.UUID) string {
	n, ok := idx[uid]
	if !ok {
		return ""
	}
	return n.Issue.Priority
}

// suggestIgnoredEdge picks the edge owned by the lowest-priority
// member of the cycle. The "owner" of an edge u → v is u (u depends
// on v). When the lowest-priority member has multiple blockers in the
// cycle, the lexicographically smallest target wins so the result is
// deterministic.
func suggestIgnoredEdge(idx map[uuid.UUID]*IssueNode, cycle []uuid.UUID) (uuid.UUID, uuid.UUID) {
	if len(cycle) == 0 {
		return uuid.Nil, uuid.Nil
	}
	owner := cycle[0]
	bestRank := priorityRank(priorityOf(idx, owner))
	bestNum := int32(1<<31 - 1) // higher than any realistic issue number
	for _, uid := range cycle[1:] {
		r := priorityRank(priorityOf(idx, uid))
		n := issueNumber(idx, uid)
		if r < bestRank || (r == bestRank && n < bestNum) {
			bestRank = r
			bestNum = n
			owner = uid
		}
	}

	// Pick the lex-smallest blocker of `owner` that also appears in
	// the cycle. If none of owner's blockers are cycle members (rare;
	// would mean Tarjan included a node in the SCC that doesn't have a
	// blocks edge to another cycle member — defensive fall-through to
	// uuid.Nil signals "no recommendation").
	var target uuid.UUID
	found := false
	for bid := range idx[owner].BlockerIDs {
		if !inCycle(bid, cycle) {
			continue
		}
		if !found || uuidLess(bid, target) {
			target = bid
			found = true
		}
	}
	if !found {
		return owner, uuid.Nil
	}
	return owner, target
}

func inCycle(uid uuid.UUID, cycle []uuid.UUID) bool {
	for _, c := range cycle {
		if c == uid {
			return true
		}
	}
	return false
}

func issueNumber(idx map[uuid.UUID]*IssueNode, uid uuid.UUID) int32 {
	n, ok := idx[uid]
	if !ok {
		return 0
	}
	return n.Issue.Number
}

// tarjanSCC returns every strongly connected component of the graph
// defined by "u -> v" for every v ∈ idx[u].BlockerIDs. Implementation
// is iterative (no recursion) so it survives deep issue trees without
// blowing the goroutine stack. Each component is returned as a slice
// of UUIDs; non-trivial components (size >= 2) are the multi-node
// cycles the callers care about.
//
// Time: O(V + E). Space: O(V).
func tarjanSCC(idx map[uuid.UUID]*IssueNode) [][]uuid.UUID {
	// Stable traversal order over the map so the returned slice is
	// deterministic — the test suite compares across runs and the
	// autopilot log greps cycle alerts across ticks.
	nodes := make([]uuid.UUID, 0, len(idx))
	for uid := range idx {
		nodes = append(nodes, uid)
	}
	sort.Slice(nodes, func(i, j int) bool { return uuidLess(nodes[i], nodes[j]) })

	indexOf := make(map[uuid.UUID]int, len(nodes))
	lowlink := make(map[uuid.UUID]int, len(nodes))
	onStack := make(map[uuid.UUID]bool, len(nodes))
	var stk []uuid.UUID
	var sccs [][]uuid.UUID
	counter := 0

	// A "frame" is one active DFS call. The work stack grows when
	// the top frame discovers an unvisited successor and shrinks
	// when the top frame has finished all its successors.
	type frame struct {
		v       uuid.UUID
		succIdx int
		succs   []uuid.UUID
	}
	var work []frame

	visit := func(v uuid.UUID) {
		indexOf[v] = counter
		lowlink[v] = counter
		counter++
		stk = append(stk, v)
		onStack[v] = true
		succs := make([]uuid.UUID, 0, len(idx[v].BlockerIDs))
		for bid := range idx[v].BlockerIDs {
			succs = append(succs, bid)
		}
		sort.Slice(succs, func(i, j int) bool { return uuidLess(succs[i], succs[j]) })
		work = append(work, frame{v: v, succIdx: 0, succs: succs})
	}

	for _, start := range nodes {
		if _, ok := indexOf[start]; ok {
			continue
		}
		visit(start)
		for len(work) > 0 {
			top := &work[len(work)-1]
			if top.succIdx < len(top.succs) {
				w := top.succs[top.succIdx]
				top.succIdx++
				if _, seen := indexOf[w]; !seen {
					visit(w)
					continue
				}
				if onStack[w] && indexOf[w] < lowlink[top.v] {
					lowlink[top.v] = indexOf[w]
				}
				continue
			}
			// All successors done — finalize v.
			v := top.v
			if lowlink[v] == indexOf[v] {
				// v is the root of an SCC; pop until v.
				var scc []uuid.UUID
				for {
					w := stk[len(stk)-1]
					stk = stk[:len(stk)-1]
					onStack[w] = false
					scc = append(scc, w)
					if w == v {
						break
					}
				}
				sccs = append(sccs, scc)
			}
			// Pop this frame and propagate lowlink to the parent
			// (mirrors `lowlink[parent] = min(lowlink[parent],
			// lowlink[v])` in the recursive version).
			work = work[:len(work)-1]
			if len(work) > 0 {
				parent := work[len(work)-1].v
				if lowlink[v] < lowlink[parent] {
					lowlink[parent] = lowlink[v]
				}
			}
		}
	}
	return sccs
}
