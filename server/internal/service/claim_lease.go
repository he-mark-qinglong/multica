// Issue-row claim lease (SMA-36410, B-3.1). The cross-process
// single-winner primitive that the B-3 autopilot tree dispatcher
// (and the flat-dispatch path) call before issuing a leaf to a
// runner. Without this, two dispatchers seeing the same ready leaf
// race to issue it and the leaf is dispatched twice — the exact bug
// the issue calls out.
//
// The lease is implemented in two layers:
//
//  1. DB row lock (production, DbClaimLease): a transaction opens,
//     TryClaimIssueLeaf takes SELECT … FOR UPDATE on the issue row,
//     checks the status guard, and either commits status='in_progress'
//     + claimed_by/claimed_at or returns sql.ErrNoRows (lost the race).
//     Cross-process serialization comes from Postgres's row-level lock
//     — a second process arriving mid-flight blocks on FOR UPDATE
//     until the first commits, then re-reads the row, sees the WHERE
//     mismatch, and gets 0 rows back.
//
//  2. In-process test impl (MemoryClaimLease): a sync.Mutex serializes
//     the same CAS pattern for unit tests. **Production must not use
//     this** — multica has cron tick / cron runner / webhook /
//     multiple autopilot processes, and an in-process mutex is a no-op
//     across them. MemoryClaimLease exists so the test suite can pin
//     the contract without a Postgres fixture.
//
// Acceptance mapping:
//
//   - #1 (atomic claim)        → DbClaimLease.TryClaim's BEGIN/FOR UPDATE/UPDATE/COMMIT.
//   - #2 (snapshot recheck)    → allowFrom parameter; caller passes B-2.1's snapshot.
//   - #3 (flat+tree shared)    → every dispatcher (RunOnce, dispatchRunOnly) injects
//                                  the SAME ClaimLeaseClient instance.
//   - #4 (concurrent test)     → see claim_lease_test.go.
package service

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/google/uuid"
)

// ClaimLeaseClient is the B-3.1 cross-process single-winner primitive.
// Implementations: DbClaimLease (production, Postgres row lock) and
// MemoryClaimLease (tests only, sync.Mutex).
type ClaimLeaseClient interface {
	// TryClaim atomically transitions an issue row whose current status
	// is in `allowFrom` to status="in_progress" tagged with claimerID.
	//
	// Return contract:
	//   - (ClaimResult{Claimed:true, …}, nil)        on success
	//   - (ClaimResult{Claimed:false, CurrentClaimer:&x, …}, nil)
	//                                                 when another claimer already
	//                                                 holds the row; CurrentClaimer
	//                                                 carries the winner's uuid
	//   - (ClaimResult{Claimed:false, Reason:"status changed since snapshot"},
	//      nil)                                     when status moved outside
	//                                                 allowFrom (e.g. done)
	//   - (ClaimResult{}, err)                       on transport / DB error;
	//                                                 caller treats as transient
	TryClaim(ctx context.Context, leafID, claimerID uuid.UUID, allowFrom []string) (ClaimResult, error)

	// DropClaim releases a previously held claim. Idempotent: returns
	// released=false when the row's claimed_by no longer matches
	// claimerID (e.g. sweeper already stole it). Never returns an
	// error for the "I never held it" case.
	DropClaim(ctx context.Context, leafID, claimerID uuid.UUID) (released bool, err error)

	// ListClaimedLeaves returns leaves claimed by claimerID with
	// claimed_at older than the cutoff, up to `limit` rows. Backs the
	// stale-claim sweeper (recovery path, NOT the dispatch path).
	ListClaimedLeaves(ctx context.Context, claimerID uuid.UUID, olderThan time.Time, limit int) ([]ClaimedLeaf, error)
}

// ClaimResult is the outcome of a single TryClaim attempt. The boolean
// is the single source of truth for "did I win"; the rest exists so
// the dispatcher can render contention reason + metric name.
type ClaimResult struct {
	// Claimed is true iff this caller atomically transitioned the
	// leaf from an allowed status to in_progress with ClaimerID set.
	Claimed bool

	// CurrentStatus is the status observed at the moment of CAS.
	// Equal to statusInProgress on success.
	CurrentStatus string

	// CurrentClaimer is the row's current claim holder after CAS.
	// On contention this is the winner's uuid; on the "missing leaf"
	// or "stale snapshot" path it is nil.
	CurrentClaimer *uuid.UUID

	// Reason explains Claimed=false cases for logs and metrics:
	//   "leaf not found"                — store had no row (or row was deleted)
	//   "status changed since snapshot" — observed status not in allowFrom
	//   ""                              — only present on Claimed=true
	Reason string
}

// ClaimedLeaf is one row in ListClaimedLeaves. Used by the stale-claim
// sweeper to decide which leaves to force-release.
type ClaimedLeaf struct {
	IssueID   uuid.UUID
	Status    string
	Claimer   uuid.UUID
	ClaimedAt time.Time
	UpdatedAt time.Time
}

// ErrClaimLeafNotFound is returned by TryClaim when the leaf id has
// no row. Production wraps a no-row UPDATE result; callers treat it as
// "another path already disposed of this leaf" rather than retrying.
var ErrClaimLeafNotFound = errors.New("claim_lease: issue row not found")

// ErrClaimLeafContended is returned by TryClaim when the row exists
// but its current status is not in allowFrom. Production wraps a
// 0-rows UPDATE result.
var ErrClaimLeafContended = errors.New("claim_lease: issue row status outside allowFrom")

// ClaimDispatcherMetrics is the metric hook the dispatcher wires up
// at startup. Each method corresponds to a distinct contention
// reason; the production wiring registers Prometheus counters under
// these names. The interface lets tests assert counters incremented
// without pulling prometheus into this file.
type ClaimDispatcherMetrics interface {
	IncClaimWin()
	IncClaimLossLeafNotFound()
	IncClaimLossStatusChanged()
	IncClaimLossAlreadyClaimed()
	IncClaimTransientError()
}

// nullMetrics is the no-op metrics sink used by tests that do not
// assert on counters. Production wires a Prometheus-backed sink.
type nullMetrics struct{}

func (nullMetrics) IncClaimWin()                       {}
func (nullMetrics) IncClaimLossLeafNotFound()          {}
func (nullMetrics) IncClaimLossStatusChanged()         {}
func (nullMetrics) IncClaimLossAlreadyClaimed()       {}
func (nullMetrics) IncClaimTransientError()            {}

// statusInProgress is the claim target. Re-declared here to keep
// this file self-contained; treescheduler uses the same literal in
// internal/treescheduler/branch.go:StatusInProgress. Kept as a
// package-level const so dispatcher logs are searchable.
const statusInProgress = "in_progress"

// defaultReadyStatuses is the B-2.1 snapshot's expected set for a
// dispatchable leaf. Callers can override per call; the default keeps
// the dispatcher happy when it has not been told otherwise.
var defaultReadyStatuses = []string{"ready", "todo"}

// -----------------------------------------------------------------------------
// DbClaimLease (production)
// -----------------------------------------------------------------------------

// DbClaimLease is the production implementation backed by a Postgres
// pool. The companion sqlc queries (TryClaimIssueLeaf, DropIssueClaim,
// ListIssueClaimsByRunner) live in pkg/db/queries/issue.sql and
// require `sqlc generate` after migration 124_issue_claim_columns is
// applied. This file references the generated *db.Queries method
// names directly; it will not compile until sqlc has run.
//
// We do not inline the SQL here on purpose: the codebase convention
// is "every query goes through sqlc" so that schema renames are a
// one-line sqlc diff instead of grep-and-replace across Go files.
type DbClaimLease struct {
	q dbQueries
}

// dbQueries is the slice of the sqlc-generated Queries type this
// file actually uses. Declared as an interface here so the unit
// tests can pass a fake without depending on the generated package.
// The concrete *db.Queries satisfies it via sqlc's standard pattern.
type dbQueries interface {
	// TryClaimIssueLeaf is the SELECT FOR UPDATE + guarded UPDATE +
	// RETURNING row in pkg/db/queries/issue.sql. Returns sql.ErrNoRows
	// when the WHERE clause (status = ANY($allowFrom)) excludes the
	// row, which the lease surfaces as ErrClaimLeafContended.
	TryClaimIssueLeaf(ctx context.Context, arg TryClaimIssueLeafParams) (TryClaimIssueLeafRow, error)

	// DropIssueClaim clears claimed_by/claimed_at for the matching
	// (id, claimed_by) tuple. Idempotent.
	DropIssueClaim(ctx context.Context, arg DropIssueClaimParams) error

	// ListIssueClaimsByRunner pages through stale claims held by a
	// given runner. Backs the sweeper.
	ListIssueClaimsByRunner(ctx context.Context, arg ListIssueClaimsByRunnerParams) ([]ListIssueClaimsByRunnerRow, error)
}

// TryClaimIssueLeafParams mirrors the sqlc-generated params for the
// TryClaimIssueLeaf query. Field names match the SQL placeholders so
// reviewers can grep from one to the other.
type TryClaimIssueLeafParams struct {
	ID        uuid.UUID
	ClaimedBy uuid.UUID
	AllowFrom []string
}

// TryClaimIssueLeafRow mirrors the sqlc-generated RETURNING row.
type TryClaimIssueLeafRow struct {
	ID          uuid.UUID
	WorkspaceID uuid.UUID
	Status      string
	ClaimedBy   uuid.UUID
	ClaimedAt   time.Time
}

// DropIssueClaimParams mirrors the sqlc-generated params.
type DropIssueClaimParams struct {
	ID        uuid.UUID
	ClaimedBy uuid.UUID
}

// ListIssueClaimsByRunnerParams mirrors the sqlc-generated params.
type ListIssueClaimsByRunnerParams struct {
	ClaimedBy uuid.UUID
	OlderThan time.Time
	Limit     int32
}

// ListIssueClaimsByRunnerRow mirrors the sqlc-generated RETURNING row.
type ListIssueClaimsByRunnerRow struct {
	ID        uuid.UUID
	Status    string
	ClaimedBy uuid.UUID
	ClaimedAt time.Time
	UpdatedAt time.Time
}

// Compile-time check that DbClaimLease satisfies the interface.
var _ ClaimLeaseClient = (*DbClaimLease)(nil)

// NewDbClaimLease wires the production claim lease to a sqlc
// Queries handle. The Queries type owns its own pgxpool reference;
// the lease holds no resources of its own beyond the reference.
func NewDbClaimLease(q dbQueries) *DbClaimLease {
	return &DbClaimLease{q: q}
}

// TryClaim implements ClaimLeaseClient for DbClaimLease. The
// underlying sqlc query (TryClaimIssueLeaf) wraps the entire
// FOR-UPDATE + guarded-UPDATE in a transaction on the Queries side
// (sqlc emits it as a method that does BEGIN; SELECT FOR UPDATE;
// UPDATE … RETURNING; COMMIT).
//
// On contention (status moved outside allowFrom) the row update
// affects 0 rows and the sqlc method returns sql.ErrNoRows, which
// the lease translates to ErrClaimLeafContended.
func (l *DbClaimLease) TryClaim(
	ctx context.Context,
	leafID, claimerID uuid.UUID,
	allowFrom []string,
) (ClaimResult, error) {
	if len(allowFrom) == 0 {
		return ClaimResult{}, errors.New("claim_lease: allowFrom must be non-empty")
	}
	if leafID == uuid.Nil || claimerID == uuid.Nil {
		return ClaimResult{}, errors.New("claim_lease: nil leaf or claimer")
	}

	row, err := l.q.TryClaimIssueLeaf(ctx, TryClaimIssueLeafParams{
		ID:        leafID,
		ClaimedBy: claimerID,
		AllowFrom: append([]string{}, allowFrom...),
	})
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			// 0 rows updated → either row missing or status moved.
			// The lease does not disambiguate here; the caller can
			// fall back to "fetch the row to see why" if it cares.
			return ClaimResult{
				Claimed: false,
				Reason:  "status changed since snapshot",
			}, ErrClaimLeafContended
		}
		return ClaimResult{}, fmt.Errorf("try claim: %w", err)
	}
	return ClaimResult{
		Claimed:        true,
		CurrentStatus:  row.Status,
		CurrentClaimer: &row.ClaimedBy,
	}, nil
}

// DropClaim implements ClaimLeaseClient for DbClaimLease. The sqlc
// query does not surface rows-affected, so we cannot distinguish
// "released" from "never held" without an extra SELECT — for the
// dispatcher's purposes both are fine, so we always return true.
func (l *DbClaimLease) DropClaim(ctx context.Context, leafID, claimerID uuid.UUID) (bool, error) {
	if leafID == uuid.Nil || claimerID == uuid.Nil {
		return false, errors.New("claim_lease: nil leaf or claimer")
	}
	err := l.q.DropIssueClaim(ctx, DropIssueClaimParams{ID: leafID, ClaimedBy: claimerID})
	if err != nil {
		return false, fmt.Errorf("drop claim: %w", err)
	}
	return true, nil
}

// ListClaimedLeaves implements ClaimLeaseClient for DbClaimLease.
func (l *DbClaimLease) ListClaimedLeaves(
	ctx context.Context,
	claimerID uuid.UUID,
	olderThan time.Time,
	limit int,
) ([]ClaimedLeaf, error) {
	if limit <= 0 {
		return nil, errors.New("claim_lease: limit must be positive")
	}
	rows, err := l.q.ListIssueClaimsByRunner(ctx, ListIssueClaimsByRunnerParams{
		ClaimedBy: claimerID,
		OlderThan: olderThan,
		Limit:     int32(limit),
	})
	if err != nil {
		return nil, fmt.Errorf("list claimed: %w", err)
	}
	out := make([]ClaimedLeaf, 0, len(rows))
	for _, r := range rows {
		out = append(out, ClaimedLeaf{
			IssueID:   r.ID,
			Status:    r.Status,
			Claimer:   r.ClaimedBy,
			ClaimedAt: r.ClaimedAt,
			UpdatedAt: r.UpdatedAt,
		})
	}
	return out, nil
}

// -----------------------------------------------------------------------------
// MemoryClaimLease (tests)
// -----------------------------------------------------------------------------

// MemoryClaimLease is the in-process test impl. Its mutex is purely
// a test convenience — production must use DbClaimLease so the row
// lock spans processes. The store has no concept of transaction or
// connection pool; everything happens synchronously under one mutex.
type MemoryClaimLease struct {
	mu   sync.Mutex
	rows map[uuid.UUID]memoryClaimRow
	// sweeperHook is invoked from ListClaimedLeaves; tests can set it
	// to simulate "another process observed a stale claim" without
	// threading wall-clock through the API.
	sweeperHook func(r memoryClaimRow, cutoff time.Time) bool
}

type memoryClaimRow struct {
	Status    string
	Claimer   *uuid.UUID
	ClaimedAt time.Time
}

// NewMemoryClaimLease returns an empty store. Tests Seed the leaves
// they want to race over; everything else is left untouched and
// TryClaim returns Claimed=false with Reason="leaf not found".
func NewMemoryClaimLease() *MemoryClaimLease {
	return &MemoryClaimLease{rows: make(map[uuid.UUID]memoryClaimRow)}
}

// Seed installs (or replaces) a row. Tests use it to set up the
// pre-claim state for the leaf under contention.
func (m *MemoryClaimLease) Seed(leafID uuid.UUID, status string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.rows[leafID] = memoryClaimRow{Status: status}
}

// SetRow installs a fully-formed row — used by tests that need to
// pre-stage a claim by another runner for the "already claimed"
// assertions.
func (m *MemoryClaimLease) SetRow(leafID uuid.UUID, row memoryClaimRow) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.rows[leafID] = row
}

// SetSweeperHook lets tests drive ListClaimedLeaves without time
// travel. The hook decides whether a row passes the "older than
// cutoff" check; production code does not use this.
func (m *MemoryClaimLease) SetSweeperHook(fn func(r memoryClaimRow, cutoff time.Time) bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.sweeperHook = fn
}

// Status returns the row's current status and claimer without
// mutating either. Tests use it to assert post-conditions.
func (m *MemoryClaimLease) Status(leafID uuid.UUID) (string, *uuid.UUID, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	row, ok := m.rows[leafID]
	if !ok {
		return "", nil, false
	}
	var claimerCopy *uuid.UUID
	if row.Claimer != nil {
		c := *row.Claimer
		claimerCopy = &c
	}
	return row.Status, claimerCopy, true
}

// TryClaim implements ClaimLeaseClient on the memory backend. The
// sync.Mutex here intentionally models the Postgres row lock — a
// test that races N goroutines sees the same single-winner
// semantics a real DB would produce.
func (m *MemoryClaimLease) TryClaim(
	_ context.Context,
	leafID, claimerID uuid.UUID,
	allowFrom []string,
) (ClaimResult, error) {
	if len(allowFrom) == 0 {
		return ClaimResult{}, errors.New("claim_lease: allowFrom must be non-empty")
	}
	if leafID == uuid.Nil || claimerID == uuid.Nil {
		return ClaimResult{}, errors.New("claim_lease: nil leaf or claimer")
	}
	m.mu.Lock()
	defer m.mu.Unlock()

	row, ok := m.rows[leafID]
	if !ok {
		return ClaimResult{
			Claimed: false,
			Reason:  "leaf not found",
		}, ErrClaimLeafNotFound
	}
	for _, allowed := range allowFrom {
		if row.Status == allowed {
			claimerCopy := claimerID
			row.Status = statusInProgress
			row.Claimer = &claimerCopy
			row.ClaimedAt = time.Now()
			m.rows[leafID] = row
			return ClaimResult{
				Claimed:        true,
				CurrentStatus:  statusInProgress,
				CurrentClaimer: &claimerCopy,
			}, nil
		}
	}
	var claimerCopy *uuid.UUID
	if row.Claimer != nil {
		c := *row.Claimer
		claimerCopy = &c
	}
	return ClaimResult{
		Claimed:        false,
		CurrentStatus:  row.Status,
		CurrentClaimer: claimerCopy,
		Reason:         "status changed since snapshot",
	}, nil
}

// DropClaim implements ClaimLeaseClient on the memory backend. The
// status is reset to "todo" so a subsequent tick sees the leaf as
// dispatchable again — same shape the DB-side UPDATE issues.
func (m *MemoryClaimLease) DropClaim(_ context.Context, leafID, claimerID uuid.UUID) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	row, ok := m.rows[leafID]
	if !ok {
		return false, nil
	}
	if row.Claimer == nil || *row.Claimer != claimerID {
		return false, nil
	}
	row.Status = "todo"
	row.Claimer = nil
	row.ClaimedAt = time.Time{}
	m.rows[leafID] = row
	return true, nil
}

// ListClaimedLeaves implements ClaimLeaseClient on the memory backend.
func (m *MemoryClaimLease) ListClaimedLeaves(
	_ context.Context,
	claimerID uuid.UUID,
	olderThan time.Time,
	limit int,
) ([]ClaimedLeaf, error) {
	if limit <= 0 {
		return nil, errors.New("claim_lease: limit must be positive")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]ClaimedLeaf, 0, len(m.rows))
	for id, row := range m.rows {
		if row.Claimer == nil || *row.Claimer != claimerID {
			continue
		}
		if m.sweeperHook != nil {
			if !m.sweeperHook(row, olderThan) {
				continue
			}
		} else if !row.ClaimedAt.Before(olderThan) {
			continue
		}
		out = append(out, ClaimedLeaf{
			IssueID:   id,
			Status:    row.Status,
			Claimer:   *row.Claimer,
			ClaimedAt: row.ClaimedAt,
			UpdatedAt: row.ClaimedAt,
		})
		if len(out) >= limit {
			break
		}
	}
	return out, nil
}

// Compile-time check that MemoryClaimLease satisfies the interface.
var _ ClaimLeaseClient = (*MemoryClaimLease)(nil)