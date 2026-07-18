package main

import (
	"net/http"
	"strings"
	"testing"
)

// TestTaskRoutesRegistered verifies the workspace task list/detail routes are
// wired into the chi router with the same auth + workspace scoping as their
// sibling routes. Behavioral coverage of filters and workspace isolation
// lives in internal/handler/task_query_test.go.
func TestTaskRoutesRegistered(t *testing.T) {
	t.Run("GET /api/tasks responds", func(t *testing.T) {
		resp := authRequest(t, "GET", "/api/tasks", nil)
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("expected 200, got %d", resp.StatusCode)
		}
		var body map[string]any
		readJSON(t, resp, &body)
		if _, ok := body["tasks"]; !ok {
			t.Errorf("response missing tasks field: %v", body)
		}
		if _, ok := body["total"]; !ok {
			t.Errorf("response missing total field: %v", body)
		}
	})

	t.Run("GET /api/tasks/{id} route exists", func(t *testing.T) {
		// A random UUID 404s from the handler — which proves the route is
		// registered (an unregistered path would 404 from chi's default
		// NotFound with a plain-text body, not our JSON error shape).
		resp := authRequest(t, "GET", "/api/tasks/00000000-0000-0000-0000-000000000000", nil)
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusNotFound {
			t.Fatalf("expected 404, got %d", resp.StatusCode)
		}
		if ct := resp.Header.Get("Content-Type"); !strings.Contains(ct, "application/json") {
			t.Errorf("expected JSON error from the handler, got content-type %q", ct)
		}
	})

	t.Run("invalid status filter 400s", func(t *testing.T) {
		resp := authRequest(t, "GET", "/api/tasks?status=bogus", nil)
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d", resp.StatusCode)
		}
	})
}
