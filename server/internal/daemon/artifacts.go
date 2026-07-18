package daemon

import (
	"context"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
)

// artifactsDirName is the task-workdir subdirectory whose files the daemon
// uploads as typed artifacts when a run completes. An agent emits artifacts
// simply by writing files into <workdir>/artifacts/.
const artifactsDirName = "artifacts"

// maxArtifactFileSize caps a single artifact upload (100 MiB); larger files
// are skipped with a warn log.
const maxArtifactFileSize = 100 * 1024 * 1024

// maxArtifactsPerTask caps how many files one task may upload; the rest are
// skipped with a warn log.
const maxArtifactsPerTask = 20

// guessArtifactKind maps an artifact filename to one of the server's
// artifact kinds. First matching rule wins.
func guessArtifactKind(name string) string {
	lower := strings.ToLower(name)
	switch {
	case lower == "metrics.json":
		return "metrics"
	case strings.Contains(lower, "equity") || strings.HasSuffix(lower, ".csv"):
		return "equity"
	case strings.Contains(lower, "plot"),
		strings.HasSuffix(lower, ".html"),
		strings.HasSuffix(lower, ".png"),
		strings.HasSuffix(lower, ".svg"):
		return "plot"
	case strings.HasSuffix(lower, ".log"):
		return "log"
	default:
		return "other"
	}
}

// collectArtifacts uploads every regular file in <workDir>/artifacts to the
// server as a typed artifact. It is deliberately fail-soft, mirroring
// collectResultFile: a missing dir, unreadable entries, oversized files, and
// individual upload failures are all warn-logged and skipped — a run never
// fails over its artifacts.
func (d *Daemon) collectArtifacts(ctx context.Context, taskID, workDir string, taskLog *slog.Logger) {
	if workDir == "" {
		return
	}
	dir := filepath.Join(workDir, artifactsDirName)
	entries, err := os.ReadDir(dir)
	if err != nil {
		if !os.IsNotExist(err) {
			taskLog.Warn("read artifacts dir failed (skipping)", "dir", dir, "error", err)
		}
		return
	}

	uploaded := 0
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		if uploaded >= maxArtifactsPerTask {
			taskLog.Warn("artifact count cap reached, skipping remaining files",
				"dir", dir, "max", maxArtifactsPerTask)
			break
		}
		path := filepath.Join(dir, entry.Name())
		info, err := entry.Info()
		if err != nil {
			taskLog.Warn("stat artifact failed (skipping)", "path", path, "error", err)
			continue
		}
		// Skip symlinks and other non-regular files: following a symlink
		// could upload arbitrary files from outside the workdir.
		if !info.Mode().IsRegular() {
			continue
		}
		if info.Size() > maxArtifactFileSize {
			taskLog.Warn("artifact too large (skipping)",
				"path", path, "size", info.Size(), "max", maxArtifactFileSize)
			continue
		}

		kind := guessArtifactKind(entry.Name())
		if err := d.client.UploadArtifact(ctx, taskID, path, kind, nil); err != nil {
			taskLog.Warn("artifact upload failed (skipping)", "path", path, "kind", kind, "error", err)
			continue
		}
		uploaded++
		taskLog.Info("artifact uploaded", "path", path, "kind", kind, "size", info.Size())
	}
}
