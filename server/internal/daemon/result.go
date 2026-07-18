package daemon

import (
	"encoding/json"
	"log/slog"
	"os"
	"path/filepath"
)

// resultFileName is the exact filename an agent may drop into the task
// workdir to report a structured, machine-readable run result.
const resultFileName = "result.json"

// maxResultFileSize caps how much of the workdir the daemon will ever
// forward to the server as a structured result (64 KiB).
const maxResultFileSize = 64 * 1024

// collectResultFile reads <workDir>/result.json and returns its contents as
// raw JSON for inclusion in the task-complete payload. It is deliberately
// fail-soft: a missing file, unreadable file, oversized file, or invalid
// JSON all yield nil (with a log line for the non-missing cases) so a run
// is never failed over its result file.
func collectResultFile(workDir string, log *slog.Logger) json.RawMessage {
	if workDir == "" {
		return nil
	}
	path := filepath.Join(workDir, resultFileName)
	info, err := os.Stat(path)
	if err != nil {
		if !os.IsNotExist(err) {
			log.Warn("stat result file failed (skipping)", "path", path, "error", err)
		}
		return nil
	}
	if info.Size() > maxResultFileSize {
		log.Warn("result file too large (skipping)", "path", path, "size", info.Size(), "max", maxResultFileSize)
		return nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		log.Warn("read result file failed (skipping)", "path", path, "error", err)
		return nil
	}
	if !json.Valid(data) {
		log.Warn("result file is not valid JSON (skipping)", "path", path)
		return nil
	}
	return json.RawMessage(data)
}
