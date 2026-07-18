package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"github.com/spf13/cobra"

	"github.com/multica-ai/multica/server/internal/cli"
)

var artifactCmd = &cobra.Command{
	Use:   "artifact",
	RunE:  groupRunE,
	Short: "Work with typed run artifacts",
}

var artifactListCmd = &cobra.Command{
	Use:   "list",
	Short: "List artifacts in the workspace (or for one task)",
	RunE:  runArtifactList,
}

var artifactAddCmd = &cobra.Command{
	Use:   "add <task-id> <file>",
	Short: "Upload a file as an artifact of a task",
	Example: `  # Upload a backtest equity curve as a metrics artifact
  $ multica artifact add abc123 equity.csv --kind equity --meta '{"symbol":"BTCUSDT","timeframe":"1h"}'`,
	Args: exactArgs(2),
	RunE: runArtifactAdd,
}

var artifactDownloadCmd = &cobra.Command{
	Use:   "download <artifact-id>",
	Short: "Download an artifact to a local file",
	Args:  exactArgs(1),
	RunE:  runArtifactDownload,
}

var artifactDeleteCmd = &cobra.Command{
	Use:   "delete <artifact-id>",
	Short: "Delete an artifact and its stored blob",
	Args:  exactArgs(1),
	RunE:  runArtifactDelete,
}

// validArtifactKinds mirrors the server's artifact.kind set
// (handler.validArtifactKinds) for client-side validation of --kind.
var validArtifactKinds = []string{"metrics", "equity", "plot", "log", "dataset", "other"}

func init() {
	artifactCmd.AddCommand(artifactListCmd)
	artifactCmd.AddCommand(artifactAddCmd)
	artifactCmd.AddCommand(artifactDownloadCmd)
	artifactCmd.AddCommand(artifactDeleteCmd)

	artifactListCmd.Flags().String("task-id", "", "List artifacts of a single task (ID or unique prefix)")
	artifactListCmd.Flags().String("issue-id", "", "Filter by issue UUID")
	artifactListCmd.Flags().String("kind", "", "Filter by kind (metrics, equity, plot, log, dataset, other)")
	artifactListCmd.Flags().Int("limit", 50, "Maximum number of artifacts to return")
	artifactListCmd.Flags().String("output", "table", "Output format: table or json")

	artifactAddCmd.Flags().String("kind", "other", "Artifact kind (metrics, equity, plot, log, dataset, other)")
	artifactAddCmd.Flags().String("meta", "", "Freeform metadata as a JSON object string, e.g. '{\"campaign\":\"c7\"}'")
	artifactAddCmd.Flags().String("output", "json", "Output format: table or json")

	artifactDownloadCmd.Flags().StringP("output", "o", "", "Destination file path (default: artifact name in current directory)")

	artifactDeleteCmd.Flags().String("output", "json", "Output format: table or json")
}

// validateArtifactKindFlag validates a --kind flag value client-side so a
// typo fails before any request; the server re-validates (400) regardless.
func validateArtifactKindFlag(kind string) error {
	for _, k := range validArtifactKinds {
		if k == kind {
			return nil
		}
	}
	return fmt.Errorf("invalid kind %q (valid: metrics, equity, plot, log, dataset, other)", kind)
}

// formatSizeBytes renders a JSON-decoded size_bytes (float64 from
// encoding/json) as a plain integer string for the list table.
func formatSizeBytes(v any) string {
	switch n := v.(type) {
	case float64:
		return strconv.FormatInt(int64(n), 10)
	case int64:
		return strconv.FormatInt(n, 10)
	default:
		return "0"
	}
}

func runArtifactList(cmd *cobra.Command, _ []string) error {
	client, err := newAPIClient(cmd)
	if err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	var artifacts []any

	taskID, _ := cmd.Flags().GetString("task-id")
	if taskID != "" {
		taskRef, err := resolveTaskID(ctx, client, taskID)
		if err != nil {
			return fmt.Errorf("resolve task: %w", err)
		}
		if err := client.GetJSON(ctx, "/api/tasks/"+url.PathEscape(taskRef.ID)+"/artifacts", &artifacts); err != nil {
			return fmt.Errorf("list task artifacts: %w", err)
		}
	} else {
		if _, err := requireWorkspaceID(cmd); err != nil {
			return err
		}
		kind, _ := cmd.Flags().GetString("kind")
		if kind != "" {
			if err := validateArtifactKindFlag(kind); err != nil {
				return err
			}
		}
		params := url.Values{}
		params.Set("workspace_id", client.WorkspaceID)
		if kind != "" {
			params.Set("kind", kind)
		}
		if v, _ := cmd.Flags().GetString("issue-id"); v != "" {
			params.Set("issue_id", v)
		}
		if v, _ := cmd.Flags().GetInt("limit"); v > 0 {
			params.Set("limit", strconv.Itoa(v))
		}
		if err := client.GetJSON(ctx, "/api/artifacts?"+params.Encode(), &artifacts); err != nil {
			return fmt.Errorf("list artifacts: %w", err)
		}
	}

	output, _ := cmd.Flags().GetString("output")
	if output == "json" {
		return cli.PrintJSON(os.Stdout, map[string]any{"artifacts": artifacts})
	}

	headers := []string{"ID", "KIND", "NAME", "SIZE", "TASK", "CREATED"}
	rows := make([][]string, 0, len(artifacts))
	for _, raw := range artifacts {
		a, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		rows = append(rows, []string{
			truncateID(strVal(a, "id")),
			strVal(a, "kind"),
			strVal(a, "name"),
			formatSizeBytes(a["size_bytes"]),
			truncateID(strVal(a, "task_id")),
			strVal(a, "created_at"),
		})
	}
	cli.PrintTable(os.Stdout, headers, rows)
	return nil
}

func runArtifactAdd(cmd *cobra.Command, args []string) error {
	kind, _ := cmd.Flags().GetString("kind")
	if err := validateArtifactKindFlag(kind); err != nil {
		return err
	}
	meta, _ := cmd.Flags().GetString("meta")
	if meta != "" {
		var obj map[string]any
		if err := json.Unmarshal([]byte(meta), &obj); err != nil {
			return fmt.Errorf("--meta must be a JSON object: %w", err)
		}
	}

	filePath := args[1]
	data, err := os.ReadFile(filePath)
	if err != nil {
		return fmt.Errorf("read file: %w", err)
	}

	client, err := newAPIClient(cmd)
	if err != nil {
		return err
	}
	if _, err := requireWorkspaceID(cmd); err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	taskRef, err := resolveTaskID(ctx, client, args[0])
	if err != nil {
		return fmt.Errorf("resolve task: %w", err)
	}

	artifact, err := client.UploadArtifact(ctx, taskRef.ID, data, filepath.Base(filePath), kind, meta)
	if err != nil {
		return fmt.Errorf("upload artifact: %w", err)
	}

	fmt.Fprintf(os.Stderr, "Uploaded %s as artifact %s (kind=%s).\n", filepath.Base(filePath), truncateID(artifact.ID), artifact.Kind)

	output, _ := cmd.Flags().GetString("output")
	if output == "table" {
		return nil
	}
	return cli.PrintJSON(os.Stdout, artifact)
}

func runArtifactDownload(cmd *cobra.Command, args []string) error {
	client, err := newAPIClient(cmd)
	if err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	data, filename, err := client.DownloadArtifact(ctx, args[0])
	if err != nil {
		return fmt.Errorf("download artifact: %w", err)
	}

	destPath, _ := cmd.Flags().GetString("output")
	if destPath == "" {
		if filename == "" {
			filename = args[0]
		}
		destPath = filepath.Base(filename)
	}

	if err := os.WriteFile(destPath, data, 0o644); err != nil {
		return fmt.Errorf("write file: %w", err)
	}

	abs, err := filepath.Abs(destPath)
	if err != nil {
		abs = destPath
	}
	fmt.Fprintln(os.Stderr, "Downloaded:", abs)

	return cli.PrintJSON(os.Stdout, map[string]any{
		"id":   args[0],
		"path": abs,
		"size": len(data),
	})
}

func runArtifactDelete(cmd *cobra.Command, args []string) error {
	client, err := newAPIClient(cmd)
	if err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	if err := client.DeleteJSON(ctx, "/api/artifacts/"+url.PathEscape(args[0])); err != nil {
		return fmt.Errorf("delete artifact: %w", err)
	}

	fmt.Fprintf(os.Stderr, "Artifact %s deleted.\n", truncateID(args[0]))

	output, _ := cmd.Flags().GetString("output")
	if output == "table" {
		return nil
	}
	return cli.PrintJSON(os.Stdout, map[string]any{"id": args[0], "deleted": true})
}
