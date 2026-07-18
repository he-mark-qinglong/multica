package main

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/multica-ai/multica/server/internal/cli"
)

var taskCmd = &cobra.Command{
	Use:   "task",
	RunE:  groupRunE,
	Short: "Inspect and manage agent tasks",
}

var taskListCmd = &cobra.Command{
	Use:   "list",
	Short: "List tasks in the workspace",
	RunE:  runTaskList,
}

var taskGetCmd = &cobra.Command{
	Use:     "get <id>",
	Short:   "Get task details (includes the result payload)",
	Args:    exactArgs(1),
	Aliases: []string{"view"},
	RunE:    runTaskGet,
}

var taskCancelCmd = &cobra.Command{
	Use:   "cancel <id>",
	Short: "Cancel a queued or running task",
	Args:  exactArgs(1),
	RunE:  runTaskCancel,
}

// validTaskStatuses mirrors the server's agent_task_queue.status set
// (handler.validTaskStatuses) for client-side validation of --status.
var validTaskStatuses = []string{
	"queued", "dispatched", "running", "completed", "failed", "cancelled",
}

func init() {
	taskCmd.AddCommand(taskListCmd)
	taskCmd.AddCommand(taskGetCmd)
	taskCmd.AddCommand(taskCancelCmd)

	// task list
	taskListCmd.Flags().String("status", "", "Filter by status (comma-separated: queued, dispatched, running, completed, failed, cancelled)")
	taskListCmd.Flags().String("agent-id", "", "Filter by agent UUID")
	taskListCmd.Flags().String("issue-id", "", "Filter by issue UUID")
	taskListCmd.Flags().Int("limit", 50, "Maximum number of tasks to return")
	taskListCmd.Flags().String("output", "table", "Output format: table or json")

	// task get
	taskGetCmd.Flags().String("output", "json", "Output format: table or json")

	// task cancel
	taskCancelCmd.Flags().String("output", "json", "Output format: table or json")
}

// parseTaskStatusFlag validates the --status filter client-side so a typo
// fails before any request; the server re-validates (400) for other clients.
// Comma-separated values are passed through to the server, which merges them.
func parseTaskStatusFlag(cmd *cobra.Command) (string, error) {
	raw, _ := cmd.Flags().GetString("status")
	if strings.TrimSpace(raw) == "" {
		return "", nil
	}
	for _, part := range strings.Split(raw, ",") {
		s := strings.TrimSpace(part)
		if s == "" {
			continue
		}
		if err := validateStatus(s, validTaskStatuses); err != nil {
			return "", err
		}
	}
	return raw, nil
}

func runTaskList(cmd *cobra.Command, _ []string) error {
	client, err := newAPIClient(cmd)
	if err != nil {
		return err
	}
	if _, err := requireWorkspaceID(cmd); err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	status, err := parseTaskStatusFlag(cmd)
	if err != nil {
		return err
	}

	params := url.Values{}
	params.Set("workspace_id", client.WorkspaceID)
	if status != "" {
		params.Set("status", status)
	}
	if v, _ := cmd.Flags().GetString("agent-id"); v != "" {
		params.Set("agent_id", v)
	}
	if v, _ := cmd.Flags().GetString("issue-id"); v != "" {
		params.Set("issue_id", v)
	}
	if v, _ := cmd.Flags().GetInt("limit"); v > 0 {
		params.Set("limit", strconv.Itoa(v))
	}

	var result map[string]any
	if err := client.GetJSON(ctx, "/api/tasks?"+params.Encode(), &result); err != nil {
		return fmt.Errorf("list tasks: %w", err)
	}

	tasksRaw, _ := result["tasks"].([]any)

	output, _ := cmd.Flags().GetString("output")
	if output == "json" {
		total, _ := result["total"].(float64)
		limit, _ := cmd.Flags().GetInt("limit")
		wrapped := map[string]any{
			"tasks":    tasksRaw,
			"total":    int(total),
			"limit":    limit,
			"has_more": len(tasksRaw) < int(total),
		}
		return cli.PrintJSON(os.Stdout, wrapped)
	}

	headers := []string{"ID", "STATUS", "AGENT", "ISSUE", "AGE"}
	rows := make([][]string, 0, len(tasksRaw))
	for _, raw := range tasksRaw {
		task, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		rows = append(rows, []string{
			truncateID(strVal(task, "id")),
			strVal(task, "status"),
			truncateID(strVal(task, "agent_id")),
			strVal(task, "issue_identifier"),
			formatTaskAge(strVal(task, "created_at")),
		})
	}
	cli.PrintTable(os.Stdout, headers, rows)
	return nil
}

func runTaskGet(cmd *cobra.Command, args []string) error {
	client, err := newAPIClient(cmd)
	if err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	taskRef, err := resolveTaskID(ctx, client, args[0])
	if err != nil {
		return fmt.Errorf("resolve task: %w", err)
	}

	var task map[string]any
	if err := client.GetJSON(ctx, "/api/tasks/"+url.PathEscape(taskRef.ID), &task); err != nil {
		return fmt.Errorf("get task: %w", err)
	}

	output, _ := cmd.Flags().GetString("output")
	if output == "table" {
		headers := []string{"ID", "STATUS", "AGENT", "ISSUE", "ATTEMPT", "CREATED", "STARTED", "COMPLETED", "ERROR"}
		rows := [][]string{{
			strVal(task, "id"),
			strVal(task, "status"),
			strVal(task, "agent_id"),
			strVal(task, "issue_id"),
			strVal(task, "attempt"),
			strVal(task, "created_at"),
			strVal(task, "started_at"),
			strVal(task, "completed_at"),
			strVal(task, "error"),
		}}
		cli.PrintTable(os.Stdout, headers, rows)
		return nil
	}

	// Full detail as indented JSON — PrintJSON pretty-prints the nested
	// result payload along with everything else.
	return cli.PrintJSON(os.Stdout, task)
}

func runTaskCancel(cmd *cobra.Command, args []string) error {
	client, err := newAPIClient(cmd)
	if err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	taskRef, err := resolveTaskID(ctx, client, args[0])
	if err != nil {
		return fmt.Errorf("resolve task: %w", err)
	}

	var task map[string]any
	if err := client.PostJSON(ctx, "/api/tasks/"+url.PathEscape(taskRef.ID)+"/cancel", map[string]any{}, &task); err != nil {
		return fmt.Errorf("cancel task: %w", err)
	}

	fmt.Fprintf(os.Stderr, "Task %s cancelled.\n", truncateID(taskRef.ID))

	output, _ := cmd.Flags().GetString("output")
	if output == "table" {
		return nil
	}
	return cli.PrintJSON(os.Stdout, task)
}

// resolveTaskID resolves a full UUID or a unique short-id prefix against the
// workspace task list, mirroring how resolveIssueRef handles issue ids.
func resolveTaskID(ctx context.Context, client *cli.APIClient, input string) (resolvedID, error) {
	return resolveIDByPrefix(ctx, client, "task", input, fetchTaskCandidates)
}

func fetchTaskCandidates(ctx context.Context, client *cli.APIClient) ([]idCandidate, error) {
	if client.WorkspaceID == "" {
		return nil, fmt.Errorf("workspace_id is required to resolve task id prefixes")
	}
	const limit = resolverListPageLimit
	candidates := []idCandidate{}
	for offset := 0; ; {
		params := url.Values{}
		params.Set("workspace_id", client.WorkspaceID)
		params.Set("limit", strconv.Itoa(limit))
		if offset > 0 {
			params.Set("offset", strconv.Itoa(offset))
		}
		var result map[string]any
		if err := client.GetJSON(ctx, "/api/tasks?"+params.Encode(), &result); err != nil {
			return nil, err
		}
		tasksRaw, _ := result["tasks"].([]any)
		for _, raw := range tasksRaw {
			task, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			if id := strVal(task, "id"); id != "" {
				candidates = append(candidates, idCandidate{
					ID:      id,
					Display: id,
					Detail:  strVal(task, "status"),
				})
			}
		}
		offset += len(tasksRaw)
		total, _ := result["total"].(float64)
		if len(tasksRaw) == 0 || (total > 0 && offset >= int(total)) || (total == 0 && len(tasksRaw) < limit) {
			break
		}
	}
	return candidates, nil
}

// formatTaskAge renders a created_at timestamp as a compact age ("5m", "2h",
// "3d") for the task list table; anything older than a week shows the date.
func formatTaskAge(createdAt string) string {
	t, err := time.Parse(time.RFC3339, createdAt)
	if err != nil {
		return createdAt
	}
	d := time.Since(t)
	switch {
	case d < time.Minute:
		return "<1m"
	case d < time.Hour:
		return fmt.Sprintf("%dm", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh", int(d.Hours()))
	case d < 7*24*time.Hour:
		return fmt.Sprintf("%dd", int(d.Hours()/24))
	default:
		return t.Format("2006-01-02")
	}
}
