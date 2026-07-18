package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

const artifactTestTaskID = "11111111-1111-1111-1111-111111111111"
const artifactTestID = "22222222-2222-2222-2222-222222222222"

// artifactTestServer stubs the endpoints the artifact commands hit: task
// prefix resolution (GET /api/tasks), upload, workspace list, download,
// delete.
func artifactTestServer(t *testing.T, uploaded *map[string]string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/tasks" && r.Method == http.MethodGet:
			json.NewEncoder(w).Encode(map[string]any{
				"tasks": []map[string]any{{"id": artifactTestTaskID, "status": "completed"}},
				"total": 1,
			})
		case r.URL.Path == "/api/tasks/"+artifactTestTaskID+"/artifacts" && r.Method == http.MethodPost:
			if err := r.ParseMultipartForm(10 << 20); err != nil {
				t.Errorf("parse multipart: %v", err)
			}
			if uploaded != nil {
				*uploaded = map[string]string{
					"kind": r.FormValue("kind"),
					"meta": r.FormValue("meta"),
				}
				if _, hdr, err := r.FormFile("file"); err == nil {
					(*uploaded)["filename"] = hdr.Filename
				}
			}
			w.WriteHeader(http.StatusCreated)
			json.NewEncoder(w).Encode(map[string]any{
				"id": artifactTestID, "kind": r.FormValue("kind"), "name": "equity.csv",
			})
		case r.URL.Path == "/api/artifacts" && r.Method == http.MethodGet:
			if k := r.URL.Query().Get("kind"); k != "" && k != "equity" {
				t.Errorf("unexpected kind filter: %q", k)
			}
			json.NewEncoder(w).Encode([]map[string]any{
				{"id": artifactTestID, "kind": "equity", "name": "equity.csv", "size_bytes": 7},
			})
		case r.URL.Path == "/api/artifacts/"+artifactTestID+"/download" && r.Method == http.MethodGet:
			w.Header().Set("Content-Disposition", `attachment; filename="equity.csv"`)
			w.Write([]byte("ts,eq\n1,100\n"))
		case r.URL.Path == "/api/artifacts/"+artifactTestID && r.Method == http.MethodDelete:
			w.WriteHeader(http.StatusNoContent)
		default:
			http.NotFound(w, r)
		}
	}))
}

func freshArtifactCmd(use string, flags map[string]string) *cobra.Command {
	c := &cobra.Command{Use: use}
	for name, def := range flags {
		c.Flags().String(name, def, "")
	}
	c.Flags().Int("limit", 50, "")
	c.Flags().StringP("output", "o", "", "")
	return c
}

func setArtifactTestEnv(t *testing.T, serverURL string) {
	t.Helper()
	t.Setenv("MULTICA_SERVER_URL", serverURL)
	t.Setenv("MULTICA_WORKSPACE_ID", "ws-1")
	t.Setenv("MULTICA_TOKEN", "test-token")
}

func TestArtifactAddUploadsMultipart(t *testing.T) {
	var uploaded map[string]string
	srv := artifactTestServer(t, &uploaded)
	defer srv.Close()
	setArtifactTestEnv(t, srv.URL)

	dir := t.TempDir()
	csv := filepath.Join(dir, "equity.csv")
	if err := os.WriteFile(csv, []byte("ts,eq\n1,100\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	cmd := freshArtifactCmd("add", map[string]string{"kind": "other", "meta": ""})
	cmd.Flags().Set("kind", "equity")
	cmd.Flags().Set("meta", `{"symbol":"BTCUSDT"}`)

	if err := runArtifactAdd(cmd, []string{artifactTestTaskID, csv}); err != nil {
		t.Fatalf("runArtifactAdd: %v", err)
	}
	if uploaded["kind"] != "equity" {
		t.Fatalf("server saw kind %q, want equity", uploaded["kind"])
	}
	if uploaded["meta"] != `{"symbol":"BTCUSDT"}` {
		t.Fatalf("server saw meta %q", uploaded["meta"])
	}
	if uploaded["filename"] != "equity.csv" {
		t.Fatalf("server saw filename %q", uploaded["filename"])
	}
}

func TestArtifactAddRejectsBadKindAndMeta(t *testing.T) {
	srv := artifactTestServer(t, nil)
	defer srv.Close()
	setArtifactTestEnv(t, srv.URL)

	cmd := freshArtifactCmd("add", map[string]string{"kind": "other", "meta": ""})
	cmd.Flags().Set("kind", "chart")
	if err := runArtifactAdd(cmd, []string{artifactTestTaskID, "x.csv"}); err == nil {
		t.Fatal("expected bad kind error")
	}

	cmd2 := freshArtifactCmd("add", map[string]string{"kind": "other", "meta": ""})
	cmd2.Flags().Set("meta", "{nope")
	if err := runArtifactAdd(cmd2, []string{artifactTestTaskID, "x.csv"}); err == nil {
		t.Fatal("expected bad meta error")
	}
}

func TestArtifactListWorkspace(t *testing.T) {
	srv := artifactTestServer(t, nil)
	defer srv.Close()
	setArtifactTestEnv(t, srv.URL)

	cmd := freshArtifactCmd("list", map[string]string{"task-id": "", "issue-id": "", "kind": ""})
	cmd.Flags().Set("kind", "equity")
	if err := runArtifactList(cmd, nil); err != nil {
		t.Fatalf("runArtifactList: %v", err)
	}
}

func TestArtifactDownloadWritesFile(t *testing.T) {
	srv := artifactTestServer(t, nil)
	defer srv.Close()
	setArtifactTestEnv(t, srv.URL)

	dest := filepath.Join(t.TempDir(), "out.csv")
	cmd := freshArtifactCmd("download", nil)
	cmd.Flags().Set("output", dest)

	if err := runArtifactDownload(cmd, []string{artifactTestID}); err != nil {
		t.Fatalf("runArtifactDownload: %v", err)
	}
	data, err := os.ReadFile(dest)
	if err != nil {
		t.Fatalf("read downloaded file: %v", err)
	}
	if !strings.Contains(string(data), "1,100") {
		t.Fatalf("unexpected file content: %q", data)
	}
}

func TestArtifactDelete(t *testing.T) {
	srv := artifactTestServer(t, nil)
	defer srv.Close()
	setArtifactTestEnv(t, srv.URL)

	cmd := freshArtifactCmd("delete", nil)
	if err := runArtifactDelete(cmd, []string{artifactTestID}); err != nil {
		t.Fatalf("runArtifactDelete: %v", err)
	}
}
