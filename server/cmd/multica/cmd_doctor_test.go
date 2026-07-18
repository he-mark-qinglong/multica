package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// doctorTestOptions returns fully-injected options against srv: the daemon
// probe and disk check never touch the real machine.
func doctorTestOptions(serverURL string) doctorOptions {
	return doctorOptions{
		ServerURL:   serverURL,
		WorkspaceID: "ws-test",
		Token:       "test-token",
		DataDir:     "/tmp/multica-doctor-test",
		HTTPTimeout: 2 * time.Second,
		DaemonStatus: func(context.Context, string) map[string]any {
			return map[string]any{"status": "running", "pid": 4242, "uptime": "1h"}
		},
		DiskFree: func(string) (uint64, error) { return 50 << 30, nil },
	}
}

func doctorFind(t *testing.T, checks []doctorCheck, name string) doctorCheck {
	t.Helper()
	for _, c := range checks {
		if c.Name == name {
			return c
		}
	}
	t.Fatalf("check %q not found in %+v", name, checks)
	return doctorCheck{}
}

// doctorMux builds the happy-path API: /healthz 200, /api/me 200,
// /api/runtimes with the given status list.
func doctorMux(t *testing.T, runtimeStatuses ...string) *http.ServeMux {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	mux.HandleFunc("/api/me", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-token" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		fmt.Fprint(w, `{"id":"u1","email":"op@example.com"}`)
	})
	mux.HandleFunc("/api/runtimes", func(w http.ResponseWriter, _ *http.Request) {
		parts := make([]string, len(runtimeStatuses))
		for i, s := range runtimeStatuses {
			parts[i] = fmt.Sprintf(`{"id":"rt-%d","status":%q}`, i, s)
		}
		fmt.Fprintf(w, "[%s]", strings.Join(parts, ","))
	})
	return mux
}

func TestDoctorAllPass(t *testing.T) {
	srv := httptest.NewServer(doctorMux(t, "online", "online"))
	defer srv.Close()

	checks := runDoctorChecks(context.Background(), doctorTestOptions(srv.URL))
	if len(checks) != 7 {
		t.Fatalf("expected 7 checks, got %d", len(checks))
	}
	for _, c := range checks {
		if c.Status != doctorPass {
			t.Errorf("check %q = %s (%s), want PASS", c.Name, c.Status, c.Detail)
		}
	}
	if d := doctorFind(t, checks, "auth token valid").Detail; !strings.Contains(d, "op@example.com") {
		t.Errorf("token check detail should identify the user, got %q", d)
	}
	if d := doctorFind(t, checks, "runtimes online").Detail; d != "2 of 2 runtimes online" {
		t.Errorf("runtimes detail = %q, want %q", d, "2 of 2 runtimes online")
	}
}

func TestDoctorExpiredTokenFailsWithLoginHint(t *testing.T) {
	// Every token is rejected → /api/me returns 401.
	mux := doctorMux(t, "online")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/me" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		mux.ServeHTTP(w, r)
	}))
	defer srv.Close()

	checks := runDoctorChecks(context.Background(), doctorTestOptions(srv.URL))

	c := doctorFind(t, checks, "auth token valid")
	if c.Status != doctorFail {
		t.Fatalf("token check = %s, want FAIL", c.Status)
	}
	if !strings.Contains(c.Hint, "multica login") {
		t.Fatalf("token FAIL hint must point at re-login, got %q", c.Hint)
	}
	if got := doctorFind(t, checks, "server reachable").Status; got != doctorPass {
		t.Fatalf("server should still be reachable on 401, got %s", got)
	}
}

func TestDoctorUnreachableServer(t *testing.T) {
	srv := httptest.NewServer(doctorMux(t, "online"))
	srv.Close() // immediately unreachable

	checks := runDoctorChecks(context.Background(), doctorTestOptions(srv.URL))

	if got := doctorFind(t, checks, "server reachable").Status; got != doctorFail {
		t.Fatalf("reachable check = %s, want FAIL", got)
	}
	// Dependent checks must skip gracefully, not fail or hang.
	for _, name := range []string{"auth token valid", "runtimes online"} {
		c := doctorFind(t, checks, name)
		if c.Status != doctorWarn || !strings.Contains(c.Detail, "skipped") {
			t.Fatalf("check %q = %s %q, want WARN skipped", name, c.Status, c.Detail)
		}
	}
	// Local checks must still run.
	if got := doctorFind(t, checks, "daemon running locally").Status; got != doctorPass {
		t.Fatalf("daemon check should not depend on the server, got %s", got)
	}
}

func TestDoctorJSONShape(t *testing.T) {
	srv := httptest.NewServer(doctorMux(t, "online", "offline"))
	defer srv.Close()

	checks := runDoctorChecks(context.Background(), doctorTestOptions(srv.URL))
	data, err := json.Marshal(checks)
	if err != nil {
		t.Fatal(err)
	}
	var decoded []map[string]any
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("doctor JSON must be an array of objects: %v", err)
	}
	if len(decoded) != 7 {
		t.Fatalf("expected 7 entries, got %d", len(decoded))
	}
	for _, entry := range decoded {
		for _, key := range []string{"name", "status", "detail"} {
			if _, ok := entry[key]; !ok {
				t.Fatalf("entry missing key %q: %v", key, entry)
			}
		}
		s, _ := entry["status"].(string)
		if s != doctorPass && s != doctorWarn && s != doctorFail {
			t.Fatalf("unexpected status %q in %v", s, entry)
		}
	}
	// One runtime offline → WARN with a hint for the operator.
	rt := doctorFind(t, checks, "runtimes online")
	if rt.Status != doctorWarn || rt.Hint == "" {
		t.Fatalf("runtimes check = %s hint %q, want WARN with hint", rt.Status, rt.Hint)
	}
}

func TestDoctorNoRuntimesFails(t *testing.T) {
	srv := httptest.NewServer(doctorMux(t)) // zero registered runtimes
	defer srv.Close()

	checks := runDoctorChecks(context.Background(), doctorTestOptions(srv.URL))
	if got := doctorFind(t, checks, "runtimes online").Status; got != doctorFail {
		t.Fatalf("0 online runtimes = %s, want FAIL", got)
	}
}

func TestDoctorNoServerURLFailsWithoutExit(t *testing.T) {
	opts := doctorTestOptions("")
	checks := runDoctorChecks(context.Background(), opts)

	if got := doctorFind(t, checks, "server-url configured").Status; got != doctorFail {
		t.Fatalf("server-url check = %s, want FAIL", got)
	}
	c := doctorFind(t, checks, "server reachable")
	if c.Status != doctorWarn || !strings.Contains(c.Detail, "skipped") {
		t.Fatalf("reachable check = %s %q, want WARN skipped", c.Status, c.Detail)
	}
}

func TestDoctorDiskThresholds(t *testing.T) {
	srv := httptest.NewServer(doctorMux(t, "online"))
	defer srv.Close()

	for _, tc := range []struct {
		free uint64
		want string
	}{
		{500 << 20, doctorFail}, // < 1 GB
		{5 << 30, doctorWarn},   // < 10 GB
		{50 << 30, doctorPass},  // plenty
	} {
		opts := doctorTestOptions(srv.URL)
		opts.DiskFree = func(string) (uint64, error) { return tc.free, nil }
		checks := runDoctorChecks(context.Background(), opts)
		if got := doctorFind(t, checks, "disk free").Status; got != tc.want {
			t.Errorf("disk free %d bytes = %s, want %s", tc.free, got, tc.want)
		}
	}
}
