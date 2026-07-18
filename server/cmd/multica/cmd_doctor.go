package main

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/multica-ai/multica/server/internal/cli"
)

// doctor is a read-only environment diagnostic. It never starts/stops the
// daemon, never writes config, and never sends a mutating request — every
// network call is a plain GET.

const (
	doctorPass = "PASS"
	doctorWarn = "WARN"
	doctorFail = "FAIL"
)

type doctorCheck struct {
	Name   string `json:"name"`
	Status string `json:"status"`
	Detail string `json:"detail"`
	Hint   string `json:"hint,omitempty"`
}

// doctorOptions carries everything the checks need, with the environment-
// dependent pieces behind function fields so tests can inject fakes (no
// real daemon probe, no real statfs).
type doctorOptions struct {
	ServerURL   string // already normalized; "" means unconfigured
	WorkspaceID string
	Token       string
	Profile     string
	DataDir     string // daemon state dir for the disk-free check

	HTTPTimeout time.Duration

	// DaemonStatus probes the local daemon (read-only, like `daemon status`).
	// Returns the health map; health["status"] == "running" means up.
	DaemonStatus func(ctx context.Context, profile string) map[string]any
	// DiskFree returns free bytes available on the filesystem holding path.
	DiskFree func(path string) (uint64, error)
}

var doctorCmd = &cobra.Command{
	Use:   "doctor",
	Short: "Diagnose the local Multica environment",
	Long: `Run read-only checks against the local Multica environment and print
PASS/WARN/FAIL per check with a remediation hint.

Checks: server URL configured, server reachable, auth token valid,
workspace configured, local daemon running, runtimes online, disk free.

Exit code is 1 when any check FAILs, 0 otherwise.`,
	RunE: runDoctor,
}

func init() {
	doctorCmd.Flags().String("output", "table", "Output format: table or json")
}

func runDoctor(cmd *cobra.Command, _ []string) error {
	profile := resolveProfile(cmd)
	opts := doctorOptions{
		ServerURL:   doctorResolveServerURL(cmd),
		WorkspaceID: resolveWorkspaceID(cmd),
		Token:       resolveToken(cmd),
		Profile:     profile,
		DataDir:     daemonDirForProfile(profile),
		HTTPTimeout: 5 * time.Second,
		DaemonStatus: func(ctx context.Context, p string) map[string]any {
			return checkDaemonHealthOnPort(ctx, healthPortForProfile(p))
		},
		DiskFree: diskFreeBytesForPath,
	}

	checks := runDoctorChecks(cmd.Context(), opts)
	printDoctorReport(cmd, checks)

	for _, c := range checks {
		if c.Status == doctorFail {
			return errSilent // report already printed; exit code 1 without noise
		}
	}
	return nil
}

// doctorResolveServerURL mirrors resolveServerURL but never calls os.Exit —
// doctor must report a missing URL as a FAIL check, not kill the process.
func doctorResolveServerURL(cmd *cobra.Command) string {
	val := cli.FlagOrEnv(cmd, "server-url", "MULTICA_SERVER_URL", "")
	if val == "" {
		cfg, err := cli.LoadCLIConfigForProfile(resolveProfile(cmd))
		if err == nil {
			val = cfg.ServerURL
		}
	}
	if val == "" {
		return ""
	}
	return normalizeAPIBaseURL(val)
}

func runDoctorChecks(ctx context.Context, opts doctorOptions) []doctorCheck {
	checks := make([]doctorCheck, 0, 7)

	// 1. server-url configured
	serverURL := opts.ServerURL
	if serverURL == "" {
		checks = append(checks, doctorCheck{"server-url configured", doctorFail, "not set",
			"run 'multica setup' or 'multica config set server_url <url>'"})
	} else {
		checks = append(checks, doctorCheck{"server-url configured", doctorPass, serverURL, ""})
	}

	// 2. server reachable
	reachable := false
	if serverURL == "" {
		checks = append(checks, doctorCheck{"server reachable", doctorWarn, "skipped: no server URL", ""})
	} else {
		c := doctorCheck{Name: "server reachable"}
		latency, code, err := doctorProbeHealthz(ctx, serverURL, opts.HTTPTimeout)
		switch {
		case err != nil:
			c.Status, c.Detail = doctorFail, fmt.Sprintf("%s unreachable: %v", serverURL, err)
			c.Hint = "check the URL and that the server is running"
		case code >= 400:
			c.Status, c.Detail = doctorFail, fmt.Sprintf("%s returned HTTP %d (%s)", serverURL, code, latency)
			c.Hint = "server responded but is unhealthy; check server logs"
		default:
			c.Status, c.Detail = doctorPass, fmt.Sprintf("%s (%s)", serverURL, latency)
			reachable = true
		}
		checks = append(checks, c)
	}

	client := cli.NewAPIClient(serverURL, opts.WorkspaceID, opts.Token)

	// 3. auth token valid
	switch {
	case !reachable:
		checks = append(checks, doctorCheck{"auth token valid", doctorWarn, "skipped: server unreachable", ""})
	case opts.Token == "":
		checks = append(checks, doctorCheck{"auth token valid", doctorWarn, "no token configured",
			"run 'multica login' to authenticate"})
	default:
		c := doctorCheck{Name: "auth token valid"}
		var me map[string]any
		err := client.GetJSON(ctx, "/api/me", &me)
		switch {
		case err == nil:
			c.Status = doctorPass
			switch email := strVal(me, "email"); {
			case email != "":
				c.Detail = "authenticated as " + email
			case strVal(me, "id") != "":
				c.Detail = "authenticated (id " + strVal(me, "id") + ")"
			default:
				c.Detail = "authenticated"
			}
		case doctorHTTPStatusIs(err, 401, 403):
			c.Status, c.Detail = doctorFail, "token rejected (401/403)"
			c.Hint = "token expired or invalid — run 'multica login' to re-authenticate"
		default:
			c.Status, c.Detail = doctorWarn, fmt.Sprintf("unexpected response: %v", err)
			c.Hint = "check server logs"
		}
		checks = append(checks, c)
	}

	// 4. workspace configured
	if opts.WorkspaceID == "" {
		checks = append(checks, doctorCheck{"workspace configured", doctorWarn, "no workspace ID set",
			"run 'multica config set workspace_id <id>' or 'multica workspace list'"})
	} else {
		checks = append(checks, doctorCheck{"workspace configured", doctorPass, opts.WorkspaceID, ""})
	}

	// 5. daemon running locally
	{
		c := doctorCheck{Name: "daemon running locally"}
		health := opts.DaemonStatus(ctx, opts.Profile)
		if health["status"] == "running" {
			c.Status = doctorPass
			c.Detail = fmt.Sprintf("running (pid %v, uptime %v)", health["pid"], health["uptime"])
		} else {
			c.Status, c.Detail = doctorWarn, "not running"
			c.Hint = "run 'multica daemon start' if you expect local agents on this machine"
		}
		checks = append(checks, c)
	}

	// 6. runtimes online
	if !reachable {
		checks = append(checks, doctorCheck{"runtimes online", doctorWarn, "skipped: server unreachable", ""})
	} else {
		c := doctorCheck{Name: "runtimes online"}
		var runtimes []map[string]any
		err := client.GetJSON(ctx, "/api/runtimes", &runtimes)
		switch {
		case err != nil:
			c.Status, c.Detail = doctorWarn, fmt.Sprintf("could not list runtimes: %v", err)
		default:
			online := 0
			for _, rt := range runtimes {
				if strVal(rt, "status") == "online" {
					online++
				}
			}
			total := len(runtimes)
			switch {
			case online == 0:
				c.Status = doctorFail
				c.Detail = fmt.Sprintf("0 of %d runtimes online", total)
				c.Hint = "no runtime can execute tasks — check 'multica runtime list' and daemon heartbeats"
			case online < total:
				c.Status = doctorWarn
				c.Detail = fmt.Sprintf("%d of %d runtimes online", online, total)
				c.Hint = "offline runtimes may have dead daemons — check 'multica daemon status'"
			default:
				c.Status = doctorPass
				c.Detail = fmt.Sprintf("%d of %d runtimes online", online, total)
			}
		}
		checks = append(checks, c)
	}

	// 7. disk free
	{
		c := doctorCheck{Name: "disk free"}
		free, err := opts.DiskFree(opts.DataDir)
		const (
			gb     = uint64(1) << 30
			warnAt = 10 * gb
			failAt = 1 * gb
		)
		switch {
		case err != nil:
			c.Status, c.Detail = doctorWarn, fmt.Sprintf("could not stat %s: %v", opts.DataDir, err)
		case free < failAt:
			c.Status = doctorFail
			c.Detail = fmt.Sprintf("%.1f GB free on %s", float64(free)/float64(gb), opts.DataDir)
			c.Hint = "free disk space — the daemon needs room for workspaces and logs"
		case free < warnAt:
			c.Status = doctorWarn
			c.Detail = fmt.Sprintf("%.1f GB free on %s", float64(free)/float64(gb), opts.DataDir)
			c.Hint = "disk is getting full; consider 'multica daemon disk-usage' to find large tasks"
		default:
			c.Status = doctorPass
			c.Detail = fmt.Sprintf("%.1f GB free on %s", float64(free)/float64(gb), opts.DataDir)
		}
		checks = append(checks, c)
	}

	return checks
}

// doctorProbeHealthz GETs {base}/healthz and returns latency, HTTP status,
// and transport error. Used instead of client.HealthCheck because the health
// route registered by the server is /healthz (see cmd/server/router.go).
func doctorProbeHealthz(ctx context.Context, baseURL string, timeout time.Duration) (time.Duration, int, error) {
	httpClient := &http.Client{Timeout: timeout}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		strings.TrimRight(baseURL, "/")+"/healthz", nil)
	if err != nil {
		return 0, 0, err
	}
	start := time.Now()
	resp, err := httpClient.Do(req)
	latency := time.Since(start).Round(time.Millisecond)
	if err != nil {
		return latency, 0, err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
	return latency, resp.StatusCode, nil
}

// doctorHTTPStatusIs reports whether err (a cli.APIClient error of the form
// "GET /path returned 401: ...") carries one of the given status codes.
func doctorHTTPStatusIs(err error, codes ...int) bool {
	for _, code := range codes {
		if strings.Contains(err.Error(), fmt.Sprintf("returned %d", code)) {
			return true
		}
	}
	return false
}

func printDoctorReport(cmd *cobra.Command, checks []doctorCheck) {
	output, _ := cmd.Flags().GetString("output")
	if output == "json" {
		cli.PrintJSON(os.Stdout, checks)
		return
	}

	width := 0
	for _, c := range checks {
		if len(c.Name) > width {
			width = len(c.Name)
		}
	}
	var pass, warn, fail int
	for _, c := range checks {
		line := fmt.Sprintf("%-4s  %-*s — %s", c.Status, width, c.Name, c.Detail)
		if c.Hint != "" {
			line += " (hint: " + c.Hint + ")"
		}
		fmt.Fprintln(os.Stdout, line)
		switch c.Status {
		case doctorPass:
			pass++
		case doctorWarn:
			warn++
		case doctorFail:
			fail++
		}
	}
	fmt.Fprintf(os.Stdout, "%d checks: %d pass, %d warn, %d fail\n", len(checks), pass, warn, fail)
}
