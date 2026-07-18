package daemon

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"mime"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

func TestGuessArtifactKind(t *testing.T) {
	cases := []struct {
		name string
		want string
	}{
		{"metrics.json", "metrics"},
		{"METRICS.JSON", "metrics"},
		{"other-metrics.json", "other"}, // exact-name rule only
		{"equity.csv", "equity"},
		{"equity_curve.json", "equity"},
		{"trades.csv", "equity"},
		{"plot.html", "plot"},
		{"equity_plot.csv", "equity"}, // equity rule wins over plot
		{"chart.png", "plot"},
		{"graph.SVG", "plot"},
		{"report.html", "plot"},
		{"run.log", "log"},
		{"agent.LOG", "log"},
		{"notes.txt", "other"},
		{"data.json", "other"},
		{"no-extension", "other"},
	}
	for _, tc := range cases {
		if got := guessArtifactKind(tc.name); got != tc.want {
			t.Errorf("guessArtifactKind(%q) = %q, want %q", tc.name, got, tc.want)
		}
	}
}

// uploadedArtifact captures one multipart artifact upload seen by the test
// server.
type uploadedArtifact struct {
	taskID   string
	kind     string
	meta     string
	filename string
	body     string
}

// artifactRecorder is an httptest handler that records artifact uploads
// (POST /api/daemon/tasks/{id}/artifacts) and optionally every request path.
type artifactRecorder struct {
	mu       sync.Mutex
	uploads  []uploadedArtifact
	paths    []string
	status   int // response status for uploads; 0 means 201
	complete string
}

func (r *artifactRecorder) handler(t *testing.T) http.HandlerFunc {
	t.Helper()
	return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		r.mu.Lock()
		r.paths = append(r.paths, req.Method+" "+req.URL.Path)
		r.mu.Unlock()

		if strings.HasSuffix(req.URL.Path, "/artifacts") {
			taskID := strings.TrimSuffix(strings.TrimPrefix(req.URL.Path, "/api/daemon/tasks/"), "/artifacts")
			if err := req.ParseMultipartForm(32 << 20); err != nil {
				t.Errorf("parse multipart: %v", err)
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			file, header, err := req.FormFile("file")
			if err != nil {
				t.Errorf("form file: %v", err)
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			defer file.Close()
			data, _ := io.ReadAll(file)

			r.mu.Lock()
			status := r.status
			if status == 0 {
				status = http.StatusCreated
			}
			// Record only successful uploads; failed attempts are still
			// visible in paths.
			if status < 400 {
				r.uploads = append(r.uploads, uploadedArtifact{
					taskID:   taskID,
					kind:     req.FormValue("kind"),
					meta:     req.FormValue("meta"),
					filename: header.Filename,
					body:     string(data),
				})
			}
			r.mu.Unlock()

			w.WriteHeader(status)
			w.Write([]byte(`{}`))
			return
		}

		// Everything else (e.g. /complete) just succeeds.
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{}`))
	})
}

func (r *artifactRecorder) recordedUploads() []uploadedArtifact {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]uploadedArtifact(nil), r.uploads...)
}

func (r *artifactRecorder) recordedPaths() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.paths...)
}

func TestClientUploadArtifact(t *testing.T) {
	t.Parallel()

	rec := &artifactRecorder{}
	srv := httptest.NewServer(rec.handler(t))
	t.Cleanup(srv.Close)

	dir := t.TempDir()
	path := filepath.Join(dir, "equity.csv")
	if err := os.WriteFile(path, []byte("a,b\n1,2\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	c := NewClient(srv.URL)
	c.SetToken("daemon-token")
	if err := c.UploadArtifact(context.Background(), "task-123", path, "equity", map[string]any{"symbol": "BTCUSDT"}); err != nil {
		t.Fatalf("UploadArtifact: %v", err)
	}

	uploads := rec.recordedUploads()
	if len(uploads) != 1 {
		t.Fatalf("expected 1 upload, got %d", len(uploads))
	}
	up := uploads[0]
	if up.taskID != "task-123" {
		t.Errorf("task id: got %q", up.taskID)
	}
	if up.kind != "equity" {
		t.Errorf("kind: got %q", up.kind)
	}
	if up.meta != `{"symbol":"BTCUSDT"}` {
		t.Errorf("meta: got %q", up.meta)
	}
	if up.filename != "equity.csv" {
		t.Errorf("filename: got %q", up.filename)
	}
	if up.body != "a,b\n1,2\n" {
		t.Errorf("body: got %q", up.body)
	}
}

// TestClientUploadArtifactError pins the fail-soft contract: a server error
// surfaces as *requestError (so the daemon can warn-log and continue), and a
// 404 "task not found" body is recognized by isTaskNotFoundError.
func TestClientUploadArtifactError(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "task not found", http.StatusNotFound)
	}))
	t.Cleanup(srv.Close)

	dir := t.TempDir()
	path := filepath.Join(dir, "run.log")
	if err := os.WriteFile(path, []byte("line\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	c := NewClient(srv.URL)
	err := c.UploadArtifact(context.Background(), "gone-task", path, "log", nil)
	if err == nil {
		t.Fatal("expected error for 404 response")
	}
	if !isTaskNotFoundError(err) {
		t.Fatalf("expected isTaskNotFoundError, got %v", err)
	}
}

// TestClientUploadArtifactEmptyKindOmitsField: kind "" must not be sent as an
// empty form field — the server defaults missing kind to "other".
func TestClientUploadArtifactEmptyKindOmitsField(t *testing.T) {
	t.Parallel()

	var gotKind string
	var hasKind bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		mediaType, params, err := mime.ParseMediaType(req.Header.Get("Content-Type"))
		if err != nil || !strings.HasPrefix(mediaType, "multipart/") {
			t.Errorf("content type: %q err %v", req.Header.Get("Content-Type"), err)
			http.Error(w, "bad content type", http.StatusBadRequest)
			return
		}
		reader := multipart.NewReader(req.Body, params["boundary"])
		for {
			part, err := reader.NextPart()
			if err == io.EOF {
				break
			}
			if err != nil {
				t.Errorf("next part: %v", err)
				break
			}
			if part.FormName() == "kind" {
				data, _ := io.ReadAll(part)
				gotKind = string(data)
				hasKind = true
			} else {
				io.Copy(io.Discard, part)
			}
			part.Close()
		}
		w.WriteHeader(http.StatusCreated)
		w.Write([]byte(`{}`))
	}))
	t.Cleanup(srv.Close)

	dir := t.TempDir()
	path := filepath.Join(dir, "notes.txt")
	if err := os.WriteFile(path, []byte("hi"), 0o644); err != nil {
		t.Fatal(err)
	}

	c := NewClient(srv.URL)
	if err := c.UploadArtifact(context.Background(), "task-1", path, "", nil); err != nil {
		t.Fatalf("UploadArtifact: %v", err)
	}
	if hasKind {
		t.Fatalf("kind field should be omitted when empty, got %q", gotKind)
	}
}

func TestCollectArtifacts(t *testing.T) {
	t.Parallel()

	rec := &artifactRecorder{}
	srv := httptest.NewServer(rec.handler(t))
	t.Cleanup(srv.Close)

	workDir := t.TempDir()
	artDir := filepath.Join(workDir, "artifacts")
	if err := os.MkdirAll(artDir, 0o755); err != nil {
		t.Fatal(err)
	}
	files := map[string]string{
		"metrics.json": `{"sharpe":1.5}`,
		"equity.csv":   "t,v\n1,2\n",
		"plot.html":    "<html></html>",
		"run.log":      "line1\n",
		"notes.txt":    "freeform",
	}
	for name, body := range files {
		if err := os.WriteFile(filepath.Join(artDir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// A subdirectory must be skipped.
	if err := os.MkdirAll(filepath.Join(artDir, "nested"), 0o755); err != nil {
		t.Fatal(err)
	}
	// A symlink must be skipped (never follow links out of the workdir).
	outside := filepath.Join(workDir, "secret.txt")
	if err := os.WriteFile(outside, []byte("nope"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(artDir, "linked.txt")); err != nil {
		t.Fatal(err)
	}
	// An oversized file must be skipped (sparse file: cheap to create).
	big, err := os.Create(filepath.Join(artDir, "huge.csv"))
	if err != nil {
		t.Fatal(err)
	}
	if err := big.Truncate(maxArtifactFileSize + 1); err != nil {
		big.Close()
		t.Fatal(err)
	}
	big.Close()

	d := &Daemon{client: NewClient(srv.URL), logger: slog.Default()}
	d.collectArtifacts(context.Background(), "task-9", workDir, slog.Default())

	uploads := rec.recordedUploads()
	if len(uploads) != len(files) {
		t.Fatalf("expected %d uploads, got %d: %+v", len(files), len(uploads), uploads)
	}
	got := map[string]uploadedArtifact{}
	for _, up := range uploads {
		if up.taskID != "task-9" {
			t.Errorf("upload task id: got %q", up.taskID)
		}
		got[up.filename] = up
	}
	wantKinds := map[string]string{
		"metrics.json": "metrics",
		"equity.csv":   "equity",
		"plot.html":    "plot",
		"run.log":      "log",
		"notes.txt":    "other",
	}
	for name, kind := range wantKinds {
		up, ok := got[name]
		if !ok {
			t.Errorf("missing upload for %s", name)
			continue
		}
		if up.kind != kind {
			t.Errorf("%s kind = %q, want %q", name, up.kind, kind)
		}
		if up.body != files[name] {
			t.Errorf("%s body = %q, want %q", name, up.body, files[name])
		}
	}
}

// TestCollectArtifactsCap: at most maxArtifactsPerTask files are uploaded.
func TestCollectArtifactsCap(t *testing.T) {
	t.Parallel()

	rec := &artifactRecorder{}
	srv := httptest.NewServer(rec.handler(t))
	t.Cleanup(srv.Close)

	workDir := t.TempDir()
	artDir := filepath.Join(workDir, "artifacts")
	if err := os.MkdirAll(artDir, 0o755); err != nil {
		t.Fatal(err)
	}
	total := maxArtifactsPerTask + 5
	for i := 0; i < total; i++ {
		name := filepath.Join(artDir, "f"+strings.Repeat("x", i+1)+".txt") // unique, ordered
		if err := os.WriteFile(name, []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	d := &Daemon{client: NewClient(srv.URL), logger: slog.Default()}
	d.collectArtifacts(context.Background(), "task-cap", workDir, slog.Default())

	if got := len(rec.recordedUploads()); got != maxArtifactsPerTask {
		t.Fatalf("expected cap of %d uploads, got %d", maxArtifactsPerTask, got)
	}
}

// TestCollectArtifactsFailSoft: upload failures and a missing directory must
// never propagate — collection simply logs and moves on.
func TestCollectArtifactsFailSoft(t *testing.T) {
	t.Parallel()

	rec := &artifactRecorder{status: http.StatusInternalServerError}
	srv := httptest.NewServer(rec.handler(t))
	t.Cleanup(srv.Close)

	workDir := t.TempDir()
	artDir := filepath.Join(workDir, "artifacts")
	if err := os.MkdirAll(artDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(artDir, "run.log"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}

	d := &Daemon{client: NewClient(srv.URL), logger: slog.Default()}
	// Must not panic or hang despite the server 500ing every upload.
	d.collectArtifacts(context.Background(), "task-err", workDir, slog.Default())
	// Missing artifacts dir is a silent no-op.
	d.collectArtifacts(context.Background(), "task-none", t.TempDir(), slog.Default())
	// Empty workdir is a no-op.
	d.collectArtifacts(context.Background(), "task-empty", "", slog.Default())

	if got := len(rec.recordedUploads()); got != 0 {
		t.Fatalf("expected 0 successful uploads, got %d", got)
	}
	// The failed upload was still attempted (fail-soft = warn + continue).
	if got := len(rec.recordedPaths()); got != 1 {
		t.Fatalf("expected 1 upload attempt, got %d", got)
	}
}

// TestReportTaskResultUploadsArtifactsAfterComplete pins the ordering: the
// /complete call lands first, artifact uploads follow — the task completes
// even if uploads lag or fail.
func TestReportTaskResultUploadsArtifactsAfterComplete(t *testing.T) {
	t.Parallel()

	rec := &artifactRecorder{}
	srv := httptest.NewServer(rec.handler(t))
	t.Cleanup(srv.Close)

	workDir := t.TempDir()
	artDir := filepath.Join(workDir, "artifacts")
	if err := os.MkdirAll(artDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(artDir, "metrics.json"), []byte(`{"ok":true}`), 0o644); err != nil {
		t.Fatal(err)
	}

	d := &Daemon{client: NewClient(srv.URL), logger: slog.Default()}
	d.reportTaskResult(context.Background(), "task-1", TaskResult{
		Status:  "completed",
		Comment: "done",
		WorkDir: workDir,
	}, slog.Default())

	paths := rec.recordedPaths()
	if len(paths) != 2 {
		t.Fatalf("expected 2 requests (complete + artifact), got %v", paths)
	}
	if paths[0] != "POST /api/daemon/tasks/task-1/complete" {
		t.Fatalf("first request must be /complete, got %q", paths[0])
	}
	if paths[1] != "POST /api/daemon/tasks/task-1/artifacts" {
		t.Fatalf("second request must be /artifacts, got %q", paths[1])
	}
	uploads := rec.recordedUploads()
	if len(uploads) != 1 || uploads[0].kind != "metrics" {
		t.Fatalf("expected one metrics upload, got %+v", uploads)
	}
}

// TestReportTaskResultCompleteFailureSkipsArtifacts: when /complete fails
// (falling back to /fail), artifacts of a doomed run are not uploaded.
func TestReportTaskResultCompleteFailureSkipsArtifacts(t *testing.T) {
	t.Parallel()

	var artifactsRequested atomic.Bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if strings.HasSuffix(req.URL.Path, "/artifacts") {
			artifactsRequested.Store(true)
		}
		if strings.HasSuffix(req.URL.Path, "/complete") {
			http.Error(w, "boom", http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{}`))
	}))
	t.Cleanup(srv.Close)

	workDir := t.TempDir()
	artDir := filepath.Join(workDir, "artifacts")
	if err := os.MkdirAll(artDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(artDir, "run.log"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}

	d := &Daemon{client: NewClient(srv.URL), logger: slog.Default()}
	d.reportTaskResult(context.Background(), "task-2", TaskResult{
		Status:  "completed",
		Comment: "done",
		WorkDir: workDir,
	}, slog.Default())

	if artifactsRequested.Load() {
		t.Fatal("artifact upload must not be attempted after a failed /complete")
	}
}

// Guard: JSON-shaped meta from the daemon must stay a valid object string.
func TestArtifactMetaJSONRoundTrip(t *testing.T) {
	t.Parallel()
	meta := map[string]any{"iteration": 3, "ok": true}
	data, err := json.Marshal(meta)
	if err != nil {
		t.Fatal(err)
	}
	var back map[string]any
	if err := json.Unmarshal(data, &back); err != nil {
		t.Fatalf("meta must stay a JSON object: %v", err)
	}
}
