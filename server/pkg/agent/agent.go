// Package agent provides a unified interface for executing prompts via
// coding agents (Claude Code, Codex, Copilot, OpenCode, OpenClaw, Hermes,
// Gemini, Pi, Cursor, Kimi, Kiro, Antigravity). It mirrors the happy-cli
// AgentBackend pattern, translated to idiomatic Go.
package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"time"
)

// Backend is the unified interface for executing prompts via coding agents.
//
// Lifecycle (caller's responsibility):
//
//   - Execute returns immediately with a *Session; the agent runs in a
//     background goroutine owned by the backend.
//   - Drain Session.Messages concurrently with waiting on Session.Result.
//     A reader that blocks on Result before draining Messages will
//     deadlock once the channel buffer fills.
//   - Session.Messages is closed before Result receives its single value.
//   - Session.Result receives exactly one Result, then is closed.
//
// Lifecycle (implementor's responsibility):
//
//   - Send every emitted Message on the Messages channel BEFORE Result,
//     and close Messages before sending Result.
//   - Honour ctx cancellation: a cancelled ctx must cause Execute to
//     finish promptly with Result.Status="aborted" (not "completed"),
//     and to close the child CLI process within the platform-reasonable
//     grace period (cmd.WaitDelay covers this on POSIX/Windows).
//   - Never return from the background goroutine without sending Result
//     and closing both channels; callers rely on Result firing exactly
//     once to release the task.
//   - Apply redact.Text to any user-visible string before it leaves the
//     backend (the daemon-side handler relies on this so secrets don't
//     reach the database or WebSocket broadcast).
//
// The Execute method itself only documents the caller-facing contract;
// implementors should read this comment too — the daemon-side watchdog
// is built around the invariants above.
type Backend interface {
	// Execute runs a prompt and returns a Session for streaming results.
	// The caller should read from Session.Messages (optional) and wait on
	// Session.Result for the final outcome.
	Execute(ctx context.Context, prompt string, opts ExecOptions) (*Session, error)
}

// ExecOptions configures a single execution.
type ExecOptions struct {
	Cwd   string
	Model string
	// SystemPrompt is consumed only by providers that can pass or safely inline
	// developer/system instructions. Hermes ACP intentionally ignores it and
	// relies on cwd-scoped context files such as AGENTS.md instead.
	SystemPrompt              string
	ThreadName                string
	MaxTurns                  int
	Timeout                   time.Duration
	SemanticInactivityTimeout time.Duration
	ResumeSessionID           string          // if non-empty, resume a previous agent session
	ExtraArgs                 []string        // daemon-wide default CLI arguments appended before CustomArgs; currently read by claude and codex backends only
	CustomArgs                []string        // per-agent CLI arguments appended after ExtraArgs
	McpConfig                 json.RawMessage // if non-nil, MCP server config to pass via --mcp-config
	// ThinkingLevel is the runtime-native reasoning/effort value (e.g.
	// Claude's "low|medium|high|xhigh|max", Codex's "none|minimal|low|
	// medium|high|xhigh", OpenCode's model variant names). Empty means
	// "use the runtime/model default" —
	// every backend that consumes this skips its --effort / reasoning_effort
	// injection so the upstream CLI's own default applies. Currently honoured
	// by the claude, codex, and opencode backends; other backends ignore the
	// field rather than fail (so MUL-2339 can grow runtime support
	// incrementally without breaking unrelated agents).
	ThinkingLevel string
}

// runContext derives the execution context for an agent subprocess from the
// configured per-run timeout. A positive timeout imposes a hard wall-clock
// deadline; a zero (or negative) timeout imposes NO deadline, leaving liveness
// entirely to the daemon's inactivity watchdog so a session that keeps emitting
// events is never killed merely for running long (MUL-3064). The caller owns
// the returned CancelFunc and must call it to release resources.
func runContext(ctx context.Context, timeout time.Duration) (context.Context, context.CancelFunc) {
	if timeout > 0 {
		return context.WithTimeout(ctx, timeout)
	}
	return context.WithCancel(ctx)
}

// Session represents a running agent execution.
type Session struct {
	// Messages streams events as the agent works. The channel is closed
	// when the agent finishes (before Result is sent).
	Messages <-chan Message
	// Result receives exactly one value — the final outcome — then closes.
	Result <-chan Result
}

// MessageType identifies the kind of Message.
type MessageType string

// Canonical MessageType values emitted by every Backend. Backends pick
// the value closest to the upstream protocol — the WebSocket / DB
// translation layer is responsible for the rest of the wire shape, so
// downstream consumers can switch on Message.Type without knowing
// which agent runtime produced the event.
const (
	// MessageText is free-form assistant prose. Carried in Message.Content.
	MessageText MessageType = "text"
	// MessageThinking is the model's internal reasoning trace (Claude's
	// "thinking" blocks, Codex's reasoning deltas). Carried in Message.Content;
	// callers should hide it from end-users unless the UI explicitly opted in.
	MessageThinking MessageType = "thinking"
	// MessageToolUse is a tool invocation request emitted by the agent.
	// Message.Tool carries the tool name, Message.CallID the opaque call id
	// paired with the follow-up MessageToolResult, and Message.Input the
	// structured parameters.
	MessageToolUse MessageType = "tool-use"
	// MessageToolResult is the outcome of a previously-emitted MessageToolUse.
	// Message.CallID matches the originating MessageToolUse; Message.Output
	// carries the tool's textual result (errors are surfaced via MessageError,
	// not via a special MessageToolResult value).
	MessageToolResult MessageType = "tool-result"
	// MessageStatus is a coarse lifecycle update ("running", "compacting",
	// "completed", ...). Message.Status carries the verbatim token; the
	// protocol layer decides whether to map it onto a finer-grained event.
	MessageStatus MessageType = "status"
	// MessageError is a non-fatal error mid-run (e.g. a single tool call
	// failed). Fatal errors that abort the session land in Result.Error
	// instead. Message.Content carries the error text.
	MessageError MessageType = "error"
	// MessageLog is a backend diagnostic line intended for the daemon log
	// but surfaced on the wire so the UI's "show logs" pane can render it
	// alongside the user-visible stream. Message.Level carries the
	// severity ("debug" / "info" / "warn" / "error").
	MessageLog MessageType = "log"
)

// Message is a unified event emitted by an agent during execution.
type Message struct {
	Type      MessageType
	Content   string         // text content (Text, Error, Log)
	Tool      string         // tool name (ToolUse, ToolResult)
	CallID    string         // tool call ID (ToolUse, ToolResult)
	Input     map[string]any // tool input (ToolUse)
	Output    string         // tool output (ToolResult)
	Status    string         // agent status string (Status)
	Level     string         // log level (Log)
	SessionID string         // backend session id (Status), for early resume-pointer pinning
}

// TokenUsage tracks token consumption for a single model.
type TokenUsage struct {
	InputTokens      int64
	OutputTokens     int64
	CacheReadTokens  int64
	CacheWriteTokens int64
}

// Result is the final outcome after an agent session completes.
type Result struct {
	Status     string // "completed", "failed", "aborted", "timeout", "cancelled"
	Output     string // accumulated text output
	Error      string // error message if failed
	DurationMs int64
	SessionID  string
	Usage      map[string]TokenUsage // keyed by model name
}

// Config configures a Backend instance.
type Config struct {
	ExecutablePath string            // path to CLI binary (claude, codex, copilot, opencode, openclaw, hermes, gemini, pi, cursor, kimi, kiro-cli, agy)
	Env            map[string]string // extra environment variables
	Logger         *slog.Logger
}

// New creates a Backend for the given agent type.
// Supported types: "claude", "codex", "copilot", "opencode", "openclaw", "hermes", "gemini", "pi", "cursor", "kimi", "kiro", "antigravity".
func New(agentType string, cfg Config) (Backend, error) {
	if cfg.Logger == nil {
		cfg.Logger = slog.Default()
	}

	switch agentType {
	case "claude":
		return &claudeBackend{cfg: cfg}, nil
	case "codex":
		return &codexBackend{cfg: cfg}, nil
	case "copilot":
		return &copilotBackend{cfg: cfg}, nil
	case "opencode":
		return &opencodeBackend{cfg: cfg}, nil
	case "openclaw":
		return &openclawBackend{cfg: cfg}, nil
	case "hermes":
		return &hermesBackend{cfg: cfg}, nil
	case "gemini":
		return &geminiBackend{cfg: cfg}, nil
	case "pi":
		return &piBackend{cfg: cfg}, nil
	case "cursor":
		return &cursorBackend{cfg: cfg}, nil
	case "kimi":
		return &kimiBackend{cfg: cfg}, nil
	case "kiro":
		return &kiroBackend{cfg: cfg}, nil
	case "antigravity":
		return &antigravityBackend{cfg: cfg}, nil
	default:
		return nil, fmt.Errorf("unknown agent type: %q (supported: claude, codex, copilot, opencode, openclaw, hermes, gemini, pi, cursor, kimi, kiro, antigravity)", agentType)
	}
}

// DetectVersion invokes `<executablePath> --version` and returns the cleaned
// version line that the daemon should persist on the runtime row. The cleaning
// matters: on Windows, npm-installed CLI shims (notably Gemini's) prepend a
// `chcp` banner like `Active code page: 65001` to stdout, and without the
// downstream filter that banner was being stored verbatim as the runtime
// version (see #2516). The actual filter is extractVersionLine — it picks
// the first non-empty line carrying a semver-shaped token (vX.Y.Z) and
// returns the whole line, so full version strings like "2.1.5 (Claude Code)"
// or "codex-cli 0.118.0" survive intact. If no line matches, the trimmed raw
// stdout is returned so unusual version formats degrade gracefully rather
// than silently collapsing to empty.
//
// executablePath is resolved through the OS PATH (exec.CommandContext does
// not shell out), so a bare name like "claude" works if the binary is on
// $PATH. A non-nil error is returned only when the process fails to spawn or
// exits non-zero — an empty or garbage-but-parseable version string still
// returns successfully (callers should validate via parseSemver if they need
// strict semver semantics).
func DetectVersion(ctx context.Context, executablePath string) (string, error) {
	return detectCLIVersion(ctx, executablePath)
}

// launchHeaders maps each supported agent type to the user-visible skeleton
// that the daemon spawns before any custom_args are appended. This is
// intentionally minimal — only the command + subcommand (or a short mode
// label when there is no subcommand). Internal flags, transport values, and
// environment variables are deliberately omitted so the string is a hint
// about *what* users are extending, not a dump of the full command line.
var launchHeaders = map[string]string{
	"antigravity": "agy -p (print mode)",
	"claude":      "claude (stream-json)",
	"codex":       "codex app-server",
	"copilot":     "copilot (json)",
	"cursor":      "cursor-agent (stream-json)",
	"gemini":      "gemini (stream-json)",
	"hermes":      "hermes acp",
	"kimi":        "kimi acp",
	"kiro":        "kiro-cli acp",
	"openclaw":    "openclaw agent (json)",
	"opencode":    "opencode run (json)",
	"pi":          "pi (json mode)",
}

// LaunchHeader returns the user-visible launch skeleton for agentType, or an
// empty string if the type is unknown. Callers render this as a preview so
// users understand which command their custom_args get appended to.
func LaunchHeader(agentType string) string {
	return launchHeaders[agentType]
}
