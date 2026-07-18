package daemon

import (
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func testResultLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func TestCollectResultFile_Missing(t *testing.T) {
	dir := t.TempDir()
	if got := collectResultFile(dir, testResultLogger()); got != nil {
		t.Fatalf("missing result.json: expected nil, got %q", got)
	}
}

func TestCollectResultFile_EmptyWorkDir(t *testing.T) {
	if got := collectResultFile("", testResultLogger()); got != nil {
		t.Fatalf("empty workdir: expected nil, got %q", got)
	}
}

func TestCollectResultFile_InvalidJSON(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, resultFileName), []byte(`{not json`), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := collectResultFile(dir, testResultLogger()); got != nil {
		t.Fatalf("invalid JSON: expected nil, got %q", got)
	}
}

func TestCollectResultFile_TooLarge(t *testing.T) {
	dir := t.TempDir()
	// Valid JSON whose size exceeds the 64 KiB cap: {"data":"...."}
	payload := `{"data":"` + strings.Repeat("x", maxResultFileSize) + `"}`
	if err := os.WriteFile(filepath.Join(dir, resultFileName), []byte(payload), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := collectResultFile(dir, testResultLogger()); got != nil {
		t.Fatalf("oversized result.json: expected nil, got %d bytes", len(got))
	}
}

func TestCollectResultFile_Valid(t *testing.T) {
	dir := t.TempDir()
	content := []byte(`{"summary":"fixed the bug","prs":["https://x/1"],"score":0.9}`)
	if err := os.WriteFile(filepath.Join(dir, resultFileName), content, 0o644); err != nil {
		t.Fatal(err)
	}
	got := collectResultFile(dir, testResultLogger())
	if got == nil {
		t.Fatal("valid result.json: expected bytes, got nil")
	}
	if string(got) != string(content) {
		t.Fatalf("valid result.json: got %q, want %q", got, content)
	}
}

// Any valid JSON value is accepted, not just objects — the server stores it
// as-is, so a scalar result must pass through too.
func TestCollectResultFile_ScalarValue(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, resultFileName), []byte(`"done"`), 0o644); err != nil {
		t.Fatal(err)
	}
	got := collectResultFile(dir, testResultLogger())
	if string(got) != `"done"` {
		t.Fatalf("scalar result.json: got %q, want %q", got, `"done"`)
	}
}
