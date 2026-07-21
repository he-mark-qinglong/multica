package treescheduler

import (
	"context"
	"errors"
	"fmt"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

const (
	// DefaultIssuePageSize matches the API's maximum page size. Scanning the
	// complete tree means requesting multiple pages, not raising this limit.
	DefaultIssuePageSize = 100
	// MaxIssuePageSize is intentionally equal to the API cap: callers must
	// request another page rather than trying to bypass the server limit.
	MaxIssuePageSize = 100

	// MaxIssueScanPages is a safety valve for a malformed source that reports
	// no total and never returns a short page. A valid API response includes a
	// total, so normal scans terminate before this guard.
	MaxIssueScanPages = 100_000
)

var (
	errNilIssuePageFetcher  = errors.New("issue page fetcher is nil")
	errInvalidIssuePageSize = errors.New("issue page size must be between 1 and 100")
	errNilIssueScanContext  = errors.New("issue scan context is nil")
	errIssueScanPageLimit   = errors.New("issue scan exceeded maximum page count")
)

// IssuePage is one response from the paginated issue-list query. Total is the
// filtered result count when the query provides it; -1 means that the count is
// unavailable and a short page marks the end of the scan.
type IssuePage struct {
	Issues []db.Issue
	Total  int64
}

// IssuePageFetcher loads one filtered issue-list page. Offset is the number of
// rows already requested and limit is always the scanner's configured page
// size. The callback owns transport/database concerns and should honor ctx.
//
// TODO(server/pagination): migrate the existing offset loops in
// server/cmd/multica/cmd_id_resolver.go and cmd_task.go here once their
// response types share the issue-list page contract.
type IssuePageFetcher func(ctx context.Context, offset, limit int) (IssuePage, error)

// ScanAllIssues loads every issue page using the API-compatible page size.
// Results preserve the order returned by the fetcher and are never truncated
// at the single-page limit.
func ScanAllIssues(ctx context.Context, fetch IssuePageFetcher) ([]db.Issue, error) {
	return ScanAllIssuesWithPageSize(ctx, DefaultIssuePageSize, fetch)
}

// ScanAllIssuesWithPageSize loads every issue page until the reported total is
// reached, an empty page is returned, or (when Total is unknown) a short page
// is returned. It is deliberately transport-agnostic so the autopilot can use
// the same scan with an HTTP client or a direct database query.
func ScanAllIssuesWithPageSize(ctx context.Context, pageSize int, fetch IssuePageFetcher) ([]db.Issue, error) {
	if ctx == nil {
		return nil, errNilIssueScanContext
	}
	if pageSize < 1 || pageSize > MaxIssuePageSize {
		return nil, errInvalidIssuePageSize
	}
	if fetch == nil {
		return nil, errNilIssuePageFetcher
	}

	issues := make([]db.Issue, 0, pageSize)
	pages := 0
	for offset := 0; ; offset += pageSize {
		if pages >= MaxIssueScanPages {
			return nil, errIssueScanPageLimit
		}
		pages++
		if err := ctx.Err(); err != nil {
			return nil, err
		}

		page, err := fetch(ctx, offset, pageSize)
		if err != nil {
			return nil, fmt.Errorf("fetch issue page at offset %d: %w", offset, err)
		}
		if page.Total < -1 {
			return nil, fmt.Errorf("fetch issue page at offset %d: invalid total %d", offset, page.Total)
		}

		issues = append(issues, page.Issues...)
		if len(page.Issues) == 0 {
			return issues, nil
		}
		if page.Total >= 0 && int64(len(issues)) >= page.Total {
			return issues, nil
		}
		if page.Total < 0 && len(page.Issues) < pageSize {
			return issues, nil
		}
	}
}
