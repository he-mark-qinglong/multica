package treescheduler

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"

	db "github.com/multica-ai/multica/server/pkg/db/generated"
)

func TestScanAllIssuesUsesAPIPageSize(t *testing.T) {
	calls := 0
	got, err := ScanAllIssues(context.Background(), func(_ context.Context, offset, limit int) (IssuePage, error) {
		calls++
		if limit != DefaultIssuePageSize {
			t.Fatalf("limit = %d, want %d", limit, DefaultIssuePageSize)
		}
		if offset == 0 {
			return IssuePage{Issues: make([]db.Issue, DefaultIssuePageSize), Total: DefaultIssuePageSize + 1}, nil
		}
		return IssuePage{Issues: []db.Issue{{Number: 101}}, Total: DefaultIssuePageSize + 1}, nil
	})
	if err != nil {
		t.Fatalf("ScanAllIssues() error = %v", err)
	}
	if len(got) != DefaultIssuePageSize+1 || calls != 2 {
		t.Fatalf("got %d issues after %d calls, want %d after 2", len(got), calls, DefaultIssuePageSize+1)
	}
}

func TestScanAllIssuesFetchesEveryPage(t *testing.T) {
	issues := make([]db.Issue, 5)
	for i := range issues {
		issues[i].Number = int32(i + 1)
	}

	var requests [][2]int
	got, err := ScanAllIssuesWithPageSize(context.Background(), 2, func(_ context.Context, offset, limit int) (IssuePage, error) {
		requests = append(requests, [2]int{offset, limit})
		if offset >= len(issues) {
			return IssuePage{Total: int64(len(issues))}, nil
		}
		end := offset + limit
		if end > len(issues) {
			end = len(issues)
		}
		return IssuePage{Issues: issues[offset:end], Total: int64(len(issues))}, nil
	})
	if err != nil {
		t.Fatalf("ScanAllIssuesWithPageSize() error = %v", err)
	}
	if !reflect.DeepEqual(got, issues) {
		t.Fatalf("issues = %#v, want %#v", got, issues)
	}
	wantRequests := [][2]int{{0, 2}, {2, 2}, {4, 2}}
	if !reflect.DeepEqual(requests, wantRequests) {
		t.Fatalf("requests = %#v, want %#v", requests, wantRequests)
	}
}

func TestScanAllIssuesStopsAtReportedTotal(t *testing.T) {
	calls := 0
	got, err := ScanAllIssuesWithPageSize(context.Background(), 2, func(_ context.Context, _, _ int) (IssuePage, error) {
		calls++
		return IssuePage{Issues: []db.Issue{{Number: 1}, {Number: 2}}, Total: 2}, nil
	})
	if err != nil {
		t.Fatalf("ScanAllIssuesWithPageSize() error = %v", err)
	}
	if len(got) != 2 || calls != 1 {
		t.Fatalf("got %d issues after %d calls, want 2 issues after 1 call", len(got), calls)
	}
}

func TestScanAllIssuesStopsAtShortPageWithoutTotal(t *testing.T) {
	calls := 0
	got, err := ScanAllIssuesWithPageSize(context.Background(), 3, func(_ context.Context, offset, _ int) (IssuePage, error) {
		calls++
		if offset == 0 {
			return IssuePage{Issues: []db.Issue{{Number: 1}, {Number: 2}}, Total: -1}, nil
		}
		return IssuePage{Issues: []db.Issue{{Number: 3}}, Total: -1}, nil
	})
	if err != nil {
		t.Fatalf("ScanAllIssuesWithPageSize() error = %v", err)
	}
	if len(got) != 2 || calls != 1 {
		t.Fatalf("got %d issues after %d calls, want short first page to end scan", len(got), calls)
	}
}

func TestScanAllIssuesReturnsFetcherError(t *testing.T) {
	wantErr := errors.New("database unavailable")
	got, err := ScanAllIssuesWithPageSize(context.Background(), 100, func(_ context.Context, _, _ int) (IssuePage, error) {
		return IssuePage{}, wantErr
	})
	if !errors.Is(err, wantErr) {
		t.Fatalf("error = %v, want %v", err, wantErr)
	}
	if got != nil {
		t.Fatalf("issues = %#v, want nil on error", got)
	}
}

func TestScanAllIssuesRejectsInvalidArguments(t *testing.T) {
	fetch := func(_ context.Context, _, _ int) (IssuePage, error) { return IssuePage{}, nil }
	for name, pageSize := range map[string]int{"zero": 0, "negative": -1, "over-api-cap": MaxIssuePageSize + 1} {
		t.Run(name, func(t *testing.T) {
			if _, err := ScanAllIssuesWithPageSize(context.Background(), pageSize, fetch); err == nil {
				t.Fatal("expected invalid page size error")
			}
		})
	}
	if _, err := ScanAllIssuesWithPageSize(context.Background(), 1, nil); err == nil {
		t.Fatal("expected nil fetcher error")
	}
}

// TestScanAllIssuesBreaksAPIPageSizeCap pins the regression scenario
// behind SMA-36400 [B-2.3]: when the API's ListIssues handler clamps
// every page to 100 rows, a scheduler scan of 250 issues must still
// return all 250, fetched across the expected (250/100)+1 = 3 pages.
// A scan that only consults a single page would return 100 issues and
// fail this test, exposing the truncation before any in-flight
// autopilot run is dispatched against stale data.
func TestScanAllIssuesBreaksAPIPageSizeCap(t *testing.T) {
	const total = 250
	want := make([]db.Issue, total)
	for i := range want {
		want[i].Number = int32(i + 1)
	}

	var calls int
	var requests [][2]int
	got, err := ScanAllIssuesWithPageSize(context.Background(), DefaultIssuePageSize, func(_ context.Context, offset, limit int) (IssuePage, error) {
		calls++
		requests = append(requests, [2]int{offset, limit})
		if offset >= total {
			return IssuePage{Total: int64(total)}, nil
		}
		end := offset + limit
		if end > total {
			end = total
		}
		return IssuePage{Issues: want[offset:end], Total: int64(total)}, nil
	})
	if err != nil {
		t.Fatalf("ScanAllIssuesWithPageSize() error = %v", err)
	}
	if len(got) != total {
		t.Fatalf("issues = %d, want %d (truncated by API page-size cap?)", len(got), total)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("scanned issues do not match the page-bounded source; first 3 numbers: %d,%d,%d want 1,2,3",
			got[0].Number, got[1].Number, got[2].Number)
	}
	wantRequests := [][2]int{
		{0, DefaultIssuePageSize},
		{DefaultIssuePageSize, DefaultIssuePageSize},
		{2 * DefaultIssuePageSize, DefaultIssuePageSize},
	}
	if !reflect.DeepEqual(requests, wantRequests) {
		t.Fatalf("requests = %#v, want %#v", requests, wantRequests)
	}
	if calls != len(wantRequests) {
		t.Fatalf("fetcher called %d times, want %d", calls, len(wantRequests))
	}
}

func TestScanAllIssuesRejectsInvalidTotal(t *testing.T) {
	_, err := ScanAllIssuesWithPageSize(context.Background(), 1, func(_ context.Context, _, _ int) (IssuePage, error) {
		return IssuePage{Total: -2}, nil
	})
	if err == nil || !strings.Contains(err.Error(), "invalid total") {
		t.Fatalf("error = %v, want invalid total error", err)
	}
}

func TestScanAllIssuesHandlesEmptyTotal(t *testing.T) {
	got, err := ScanAllIssuesWithPageSize(context.Background(), 1, func(_ context.Context, _, _ int) (IssuePage, error) {
		return IssuePage{Total: 0}, nil
	})
	if err != nil {
		t.Fatalf("ScanAllIssuesWithPageSize() error = %v", err)
	}
	if got == nil || len(got) != 0 {
		t.Fatalf("issues = %#v, want non-nil empty slice", got)
	}
}

func TestScanAllIssuesHandlesAlignedUnknownTotal(t *testing.T) {
	const total = 3
	calls := 0
	got, err := ScanAllIssuesWithPageSize(context.Background(), 1, func(_ context.Context, offset, _ int) (IssuePage, error) {
		calls++
		if offset >= total {
			return IssuePage{Total: -1}, nil
		}
		return IssuePage{Issues: []db.Issue{{Number: int32(offset + 1)}}, Total: -1}, nil
	})
	if err != nil {
		t.Fatalf("ScanAllIssuesWithPageSize() error = %v", err)
	}
	if len(got) != total || calls != total+1 {
		t.Fatalf("got %d issues after %d calls, want %d after %d", len(got), calls, total, total+1)
	}
}

func TestScanAllIssuesHonorsMidScanCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	calls := 0
	_, err := ScanAllIssuesWithPageSize(ctx, 1, func(_ context.Context, _, _ int) (IssuePage, error) {
		calls++
		cancel()
		return IssuePage{Issues: []db.Issue{{Number: 1}}, Total: -1}, nil
	})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled", err)
	}
	if calls != 1 {
		t.Fatalf("fetcher called %d times, want 1", calls)
	}
}

func TestScanAllIssuesEnforcesMaxScanPages(t *testing.T) {
	calls := 0
	_, err := ScanAllIssuesWithPageSize(context.Background(), 1, func(_ context.Context, _, _ int) (IssuePage, error) {
		calls++
		return IssuePage{Issues: []db.Issue{{Number: 1}}, Total: -1}, nil
	})
	if !errors.Is(err, errIssueScanPageLimit) {
		t.Fatalf("error = %v, want errIssueScanPageLimit", err)
	}
	if calls != MaxIssueScanPages {
		t.Fatalf("fetcher called %d times, want %d", calls, MaxIssueScanPages)
	}
}

func TestScanAllIssuesHonorsCancelledContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	called := false
	_, err := ScanAllIssuesWithPageSize(ctx, 1, func(_ context.Context, _, _ int) (IssuePage, error) {
		called = true
		return IssuePage{}, nil
	})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled", err)
	}
	if called {
		t.Fatal("fetcher called with cancelled context")
	}
}
