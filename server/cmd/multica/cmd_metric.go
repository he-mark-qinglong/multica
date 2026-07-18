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

var metricCmd = &cobra.Command{
	Use:   "metrics",
	RunE:  groupRunE,
	Short: "Query backtest run metrics",
}

var metricQueryCmd = &cobra.Command{
	Use:   "query",
	Short: "Query run metrics by campaign, issue, or task",
	Example: `  # All Sharpe values for one campaign
  $ multica metrics query --campaign c7
  # Metrics for one issue, as JSON
  $ multica metrics query --issue-id 3f2b... --output json`,
	RunE: runMetricQuery,
}

var metricCampaignsCmd = &cobra.Command{
	Use:   "campaigns",
	Short: "List distinct metric campaign names in the workspace",
	RunE:  runMetricCampaigns,
}

var metricReevaluateCmd = &cobra.Command{
	Use:   "reevaluate",
	Short: "Recompute hard gates over stored run metrics",
	Example: `  # Re-evaluate every row in the workspace
  $ multica metrics reevaluate
  # Only one campaign
  $ multica metrics reevaluate --campaign c7`,
	RunE: runMetricReevaluate,
}

func init() {
	metricCmd.AddCommand(metricQueryCmd)
	metricCmd.AddCommand(metricCampaignsCmd)
	metricCmd.AddCommand(metricReevaluateCmd)

	metricQueryCmd.Flags().String("campaign", "", "Filter by campaign name")
	metricQueryCmd.Flags().String("issue-id", "", "Filter by issue UUID")
	metricQueryCmd.Flags().String("task-id", "", "Filter by task UUID")
	metricQueryCmd.Flags().Int("limit", 50, "Maximum number of rows to return")
	metricQueryCmd.Flags().Int("offset", 0, "Number of rows to skip")
	metricQueryCmd.Flags().String("output", "table", "Output format: table or json")

	metricCampaignsCmd.Flags().String("output", "table", "Output format: table or json")

	metricReevaluateCmd.Flags().String("campaign", "", "Restrict re-evaluation to one campaign")
	metricReevaluateCmd.Flags().String("issue-id", "", "Restrict re-evaluation to one issue UUID")
	metricReevaluateCmd.Flags().String("output", "table", "Output format: table or json")
}

// metricFloat renders a JSON-decoded optional number (nil when SQL NULL) for
// the table, mirroring formatSizeBytes in cmd_artifact.go.
func metricFloat(v any) string {
	switch n := v.(type) {
	case float64:
		return strconv.FormatFloat(n, 'f', 2, 64)
	case nil:
		return "-"
	default:
		return "-"
	}
}

func metricInt(v any) string {
	switch n := v.(type) {
	case float64:
		return strconv.FormatInt(int64(n), 10)
	case nil:
		return "-"
	default:
		return "-"
	}
}

// metricGate renders gate_status for the GATE column: PASS / FAIL / "-"
// (null = not evaluated or insufficient data).
func metricGate(v any) string {
	switch s := v.(type) {
	case string:
		return strings.ToUpper(s)
	default:
		return "-"
	}
}

func runMetricQuery(cmd *cobra.Command, _ []string) error {
	client, err := newAPIClient(cmd)
	if err != nil {
		return err
	}
	if _, err := requireWorkspaceID(cmd); err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	params := url.Values{}
	params.Set("workspace_id", client.WorkspaceID)
	if v, _ := cmd.Flags().GetString("campaign"); v != "" {
		params.Set("campaign", v)
	}
	if v, _ := cmd.Flags().GetString("issue-id"); v != "" {
		params.Set("issue_id", v)
	}
	if v, _ := cmd.Flags().GetString("task-id"); v != "" {
		params.Set("task_id", v)
	}
	if v, _ := cmd.Flags().GetInt("limit"); v > 0 {
		params.Set("limit", strconv.Itoa(v))
	}
	if v, _ := cmd.Flags().GetInt("offset"); v > 0 {
		params.Set("offset", strconv.Itoa(v))
	}

	var resp struct {
		Metrics []any `json:"metrics"`
	}
	if err := client.GetJSON(ctx, "/api/metrics/query?"+params.Encode(), &resp); err != nil {
		return fmt.Errorf("query metrics: %w", err)
	}

	output, _ := cmd.Flags().GetString("output")
	if output == "json" {
		return cli.PrintJSON(os.Stdout, map[string]any{"metrics": resp.Metrics})
	}

	headers := []string{"CAMPAIGN", "ITER", "SHARPE", "ANN", "MDD", "PF", "OOS", "GATE", "TF"}
	rows := make([][]string, 0, len(resp.Metrics))
	for _, raw := range resp.Metrics {
		m, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		rows = append(rows, []string{
			strVal(m, "campaign"),
			strVal(m, "iteration"),
			metricFloat(m["sharpe"]),
			metricFloat(m["ann_return"]),
			metricFloat(m["max_drawdown"]),
			metricFloat(m["profit_factor"]),
			metricFloat(m["oos_sharpe"]),
			metricGate(m["gate_status"]),
			strVal(m, "timeframe"),
		})
	}
	cli.PrintTable(os.Stdout, headers, rows)
	return nil
}

func runMetricCampaigns(cmd *cobra.Command, _ []string) error {
	client, err := newAPIClient(cmd)
	if err != nil {
		return err
	}
	if _, err := requireWorkspaceID(cmd); err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	params := url.Values{}
	params.Set("workspace_id", client.WorkspaceID)

	var resp struct {
		Campaigns []string `json:"campaigns"`
	}
	if err := client.GetJSON(ctx, "/api/metrics/campaigns?"+params.Encode(), &resp); err != nil {
		return fmt.Errorf("list campaigns: %w", err)
	}

	output, _ := cmd.Flags().GetString("output")
	if output == "json" {
		return cli.PrintJSON(os.Stdout, map[string]any{"campaigns": resp.Campaigns})
	}

	rows := make([][]string, 0, len(resp.Campaigns))
	for _, c := range resp.Campaigns {
		rows = append(rows, []string{c})
	}
	cli.PrintTable(os.Stdout, []string{"CAMPAIGN"}, rows)
	return nil
}

func runMetricReevaluate(cmd *cobra.Command, _ []string) error {
	client, err := newAPIClient(cmd)
	if err != nil {
		return err
	}
	if _, err := requireWorkspaceID(cmd); err != nil {
		return err
	}

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	body := map[string]string{}
	if v, _ := cmd.Flags().GetString("campaign"); v != "" {
		body["campaign"] = v
	}
	if v, _ := cmd.Flags().GetString("issue-id"); v != "" {
		body["issue_id"] = v
	}

	params := url.Values{}
	params.Set("workspace_id", client.WorkspaceID)

	var resp struct {
		Reevaluated int `json:"reevaluated"`
		Pass        int `json:"pass"`
		Fail        int `json:"fail"`
		Skipped     int `json:"skipped"`
		Errors      int `json:"errors"`
	}
	if err := client.PostJSON(ctx, "/api/metrics/reevaluate?"+params.Encode(), body, &resp); err != nil {
		return fmt.Errorf("reevaluate metrics: %w", err)
	}

	output, _ := cmd.Flags().GetString("output")
	if output == "json" {
		return cli.PrintJSON(os.Stdout, resp)
	}

	cli.PrintTable(os.Stdout,
		[]string{"REEVALUATED", "PASS", "FAIL", "SKIPPED", "ERRORS"},
		[][]string{{
			strconv.Itoa(resp.Reevaluated),
			strconv.Itoa(resp.Pass),
			strconv.Itoa(resp.Fail),
			strconv.Itoa(resp.Skipped),
			strconv.Itoa(resp.Errors),
		}})
	return nil
}
