// Tree-aware autopilot dispatch (B-3). Reads the full issue tree
// (paged), computes ready leaves with B-2.1, applies B-2.2's
// block-avoidance plan, and dispatches each Mode branch:
//
//   - Proceed        → claim + enqueue task on the leaf
//   - PromoteBlocker → raise blocker priority, skip this tick
//   - Switch + sibling ≠ nil → dispatch sibling
//   - Switch + sibling == nil → ESCALATE (no busy-wait retry)
//
// The Switch + SiblingID == uuid.Nil branch is the deadlock defense
// from the B-3 contract: a leaf with no usable switch target must
// surface to the operator, not re-attempt on every tick.
//
// Coexistence with the flat dispatch path (cmd/server/
// autopilot_scheduler.go) is via the OnProceed / OnSwitch callbacks.
// Both paths MUST wire their callbacks to the SAME
// ClaimLeaseClient instance (see claim_lease.go) so the same leaf
// cannot be claimed by both paths in the same window. The B-3.1
// sub-issue lands that lease. The recommended pattern:
//
//   - OnProceed: call lease.TryClaim(leaf.ID, runnerID, snapshotStatuses)
//     before doing any work. If TryClaim returns Claimed=false,
//     call lease.IncClaimLossStatusChanged() (or whatever metric the
//     caller wired) and return nil — DO NOT retry, DO NOT requeue.
//   - OnSwitch: same pattern, with sibling.ID.
//   - OnEscalate: do NOT call TryClaim — escalation is a terminal
//     state and the operator's manual action will move the leaf.
//
// The dispatcher itself remains algorithm-only; the lease lives in
// the callback wiring so the B-2.* pure functions stay untouched.
package service

import (
	"context"
	"errors"
	"fmt"
	"log/slog"

	"github.com/google/uuid"

	"github.com/multica-ai/multica/server/internal/treescheduler"
	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

// DepFetcher returns every "blocks" dependency in the workspace in
// one shot. Production wires this to a paged db.Queries.List call;
// tests use a fixture slice.
type DepFetcher func(ctx context.Context) ([]db.IssueDependency, error)

// ProceedFunc, PromoteBlockerFunc, SwitchFunc, EscalateFunc, CycleFunc
// are the dispatcher's mode callbacks. They exist so tests can stub
// the side effects without touching the database.
//
// OnProceed / OnSwitch take the issue row because the caller needs
// status, priority, and assignee to claim the lease and enqueue the
// task. The dispatcher never reads or writes issue rows itself.
type ProceedFunc func(ctx context.Context, leaf db.Issue) error
type PromoteBlockerFunc func(ctx context.Context, blockerIDs []uuid.UUID) error
type SwitchFunc func(ctx context.Context, sibling uuid.UUID, leaf db.Issue) error
type EscalateFunc func(ctx context.Context, leaf db.Issue, reason string) error
type CycleFunc func(ctx context.Context, alerts []treescheduler.CycleAlert) error

// TreeDispatcher wires the B-2 algorithm to the autopilot's side
// effects. Construct one per tick (or once per workspace if the
// fetcher closures are stable) and call RunOnce per trigger fire.
type TreeDispatcher struct {
	IssueFetcher     treescheduler.IssuePageFetcher
	DepFetcher       DepFetcher
	OnProceed        ProceedFunc
	OnPromoteBlocker PromoteBlockerFunc
	OnSwitch         SwitchFunc
	OnEscalate       EscalateFunc
	OnCycles         CycleFunc
	Logger           *slog.Logger
}

// errNilIssueFetcher is the configuration-error sentinel. RunOnce
// returns this so the caller can record a "skipped" autopilot run
// instead of silently no-op'ing.
var errNilIssueFetcher = errors.New("tree dispatcher: IssueFetcher is nil")

// RunOnce executes a single tree-aware dispatch tick: scan issues →
// load deps → build index → detect cycles (gate, log via OnCycles) →
// compute ready → plan → dispatch by Mode. Returns nil when the tick
// completes cleanly (even with zero work to do) and the first error
// from any fetcher or callback otherwise.
func (d *TreeDispatcher) RunOnce(ctx context.Context) error {
	log := d.logger()
	if d.IssueFetcher == nil {
		return errNilIssueFetcher
	}
	if d.DepFetcher == nil {
		return errors.New("tree dispatcher: DepFetcher is nil")
	}

	issues, err := treescheduler.ScanAllIssues(ctx, d.IssueFetcher)
	if err != nil {
		return fmt.Errorf("scan issues: %w", err)
	}
	deps, err := d.DepFetcher(ctx)
	if err != nil {
		return fmt.Errorf("load deps: %w", err)
	}

	idx := treescheduler.BuildIndex(issues, deps)

	// Cycle gate. DetectCycles never returns a cycle for a healthy
	// graph; a non-empty alert here is a data-integrity bug or a
	// manual configuration mistake. Surface it via OnCycles (logged
	// in production) but DO NOT halt the tick — other leaves may
	// still be dispatchable, and the operator action ("drop the
	// suggested edge") is async anyway.
	if d.OnCycles != nil {
		if alerts := treescheduler.DetectCycles(idx); len(alerts) > 0 {
			if cerr := d.OnCycles(ctx, alerts); cerr != nil {
				log.Warn("tree dispatcher: OnCycles callback error",
					"alerts", len(alerts),
					"error", cerr)
			}
		}
	}

	ready := treescheduler.ComputeReadyLeaves(issues, deps)
	if len(ready) == 0 {
		log.Info("tree dispatcher: no ready leaves this tick")
		return nil
	}

	recs := treescheduler.Plan(issues, deps, ready)
	for _, rec := range recs {
		if err := d.dispatchOne(ctx, log, idx, rec); err != nil {
			// One bad leaf must not abort the rest. Log + continue so
			// sibling leaves still get a chance this tick.
			log.Warn("tree dispatcher: dispatch error",
				"leaf_id", rec.IssueID.String(),
				"kind", rec.Kind.String(),
				"error", err)
		}
	}
	return nil
}

// dispatchOne applies the Mode of a single LeafAvoidance. The index
// is consulted to resolve leaf rows; a missing leaf is logged and
// skipped (B-2.1 already filtered for ready but the planner may have
// produced a rec for an id that vanished between Plan and dispatch —
// data race with the issue listener).
func (d *TreeDispatcher) dispatchOne(
	ctx context.Context,
	log *slog.Logger,
	idx map[uuid.UUID]*treescheduler.IssueNode,
	rec treescheduler.LeafAvoidance,
) error {
	node, ok := idx[rec.IssueID]
	if !ok {
		log.Warn("tree dispatcher: leaf missing at dispatch",
			"leaf_id", rec.IssueID.String(),
			"kind", rec.Kind.String())
		return nil
	}
	leaf := node.Issue

	switch rec.Kind {
	case treescheduler.AvoidanceProceed:
		if d.OnProceed == nil {
			log.Warn("tree dispatcher: OnProceed unset, skipping proceed",
				"leaf_id", rec.IssueID.String())
			return nil
		}
		log.Info("tree dispatcher: proceed",
			"leaf_id", rec.IssueID.String())
		return d.OnProceed(ctx, leaf)

	case treescheduler.AvoidancePromoteBlocker:
		if d.OnPromoteBlocker == nil {
			log.Warn("tree dispatcher: OnPromoteBlocker unset, skipping promote",
				"leaf_id", rec.IssueID.String())
			return nil
		}
		log.Info("tree dispatcher: promote_blocker",
			"leaf_id", rec.IssueID.String(),
			"blockers", len(rec.BlockerIDs))
		return d.OnPromoteBlocker(ctx, rec.BlockerIDs)

	case treescheduler.AvoidanceSwitch:
		if rec.SiblingID == uuid.Nil {
			// Deadlock defense: no usable sibling. Per the B-3
			// contract, this branch must NOT busy-wait retry. Surface
			// the leaf to the operator via OnEscalate. If OnEscalate
			// is unset we still log + return; the alternative
			// (silently re-attempt) is exactly the deadlock mode the
			// swarm prep called out.
			reason := "no usable switch sibling; leaf blocked, escalate to operator"
			log.Warn("tree dispatcher: switch+nil escalate",
				"leaf_id", rec.IssueID.String(),
				"reason", rec.Reason)
			if d.OnEscalate == nil {
				return nil
			}
			return d.OnEscalate(ctx, leaf, reason)
		}
		if d.OnSwitch == nil {
			log.Warn("tree dispatcher: OnSwitch unset, skipping switch",
				"leaf_id", rec.IssueID.String(),
				"sibling_id", rec.SiblingID.String())
			return nil
		}
		log.Info("tree dispatcher: switch",
			"leaf_id", rec.IssueID.String(),
			"sibling_id", rec.SiblingID.String())
		return d.OnSwitch(ctx, rec.SiblingID, leaf)

	default:
		return fmt.Errorf("unknown avoidance kind: %v", rec.Kind)
	}
}

// logger returns the configured logger or slog.Default. Pulled into
// a helper so every call site stays one line.
func (d *TreeDispatcher) logger() *slog.Logger {
	if d.Logger != nil {
		return d.Logger
	}
	return slog.Default()
}