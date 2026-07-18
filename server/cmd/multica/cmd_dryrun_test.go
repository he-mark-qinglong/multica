package main

import (
	"bytes"
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/multica-ai/multica/server/internal/cli"
)

// --dry-run must intercept every mutating verb at the client layer: the hook
// observes the request, the server never sees it, and the caller gets
// cli.ErrDryRun so it can exit before touching post-request result handling.
func TestDryRunInterceptsMutations(t *testing.T) {
	var hits int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&hits, 1)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	type call struct {
		method string
		path   string
		body   any
	}
	var calls []call

	client := cli.NewAPIClient(srv.URL, "ws-1", "test-token")
	client.DryRun = func(method, path string, body any) {
		calls = append(calls, call{method, path, body})
	}

	ctx := context.Background()
	body := map[string]any{"title": "x"}

	if err := client.PostJSON(ctx, "/api/issues", body, nil); !errors.Is(err, cli.ErrDryRun) {
		t.Errorf("PostJSON: expected ErrDryRun, got %v", err)
	}
	if err := client.PutJSON(ctx, "/api/issues/1", body, nil); !errors.Is(err, cli.ErrDryRun) {
		t.Errorf("PutJSON: expected ErrDryRun, got %v", err)
	}
	if err := client.PatchJSON(ctx, "/api/issues/1", body, nil); !errors.Is(err, cli.ErrDryRun) {
		t.Errorf("PatchJSON: expected ErrDryRun, got %v", err)
	}
	if err := client.DeleteJSON(ctx, "/api/issues/1"); !errors.Is(err, cli.ErrDryRun) {
		t.Errorf("DeleteJSON: expected ErrDryRun, got %v", err)
	}

	if got := atomic.LoadInt32(&hits); got != 0 {
		t.Errorf("server received %d requests, want 0", got)
	}
	if len(calls) != 4 {
		t.Fatalf("hook saw %d calls, want 4", len(calls))
	}
	wantMethods := []string{http.MethodPost, http.MethodPut, http.MethodPatch, http.MethodDelete}
	for i, want := range wantMethods {
		if calls[i].method != want {
			t.Errorf("call %d: method = %s, want %s", i, calls[i].method, want)
		}
	}
	if calls[3].body != nil {
		t.Errorf("DELETE hook body = %v, want nil", calls[3].body)
	}
}

// Reads must still pass through in dry-run mode so read-then-write commands
// (e.g. issue update resolving its target) preview against real data.
func TestDryRunLetsReadsThrough(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
		}
		w.Write([]byte(`{"id":"1"}`))
	}))
	defer srv.Close()

	client := cli.NewAPIClient(srv.URL, "ws-1", "test-token")
	client.DryRun = func(method, path string, body any) {
		t.Errorf("hook fired for read: %s %s", method, path)
	}

	var out map[string]any
	if err := client.GetJSON(context.Background(), "/api/issues/1", &out); err != nil {
		t.Fatalf("GetJSON: %v", err)
	}
	if out["id"] != "1" {
		t.Errorf("out = %v, want id 1", out)
	}
}

func TestPrintDryRunHook(t *testing.T) {
	var buf bytes.Buffer
	hook := cli.PrintDryRunHook(&buf)
	hook(http.MethodPost, "/api/issues", map[string]any{"title": "hello"})
	hook(http.MethodDelete, "/api/issues/1", nil)

	out := buf.String()
	if !strings.Contains(out, "DRY-RUN POST /api/issues") {
		t.Errorf("missing POST line: %q", out)
	}
	if !strings.Contains(out, `"title": "hello"`) {
		t.Errorf("missing body: %q", out)
	}
	if !strings.Contains(out, "DRY-RUN DELETE /api/issues/1") {
		t.Errorf("missing DELETE line: %q", out)
	}
}
