package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// ErrDryRun is returned by mutating request methods when APIClient.DryRun is
// set: the request was reported through the hook and deliberately NOT sent.
// Callers (the multica CLI) treat it as a clean exit, not a failure.
var ErrDryRun = errors.New("dry-run: request not sent")

// ClientVersion is the CLI version sent on every request as X-Client-Version.
// Set by the multica binary at init() so the package doesn't depend on the
// concrete cmd package. Defaults to "dev" when running unset (e.g. tests).
var ClientVersion = "dev"

// ClientPlatform identifies this client to the server. Override for tests
// or alternative entry points; defaults to "cli".
var ClientPlatform = "cli"

// ClientOS is the normalized operating system string sent as X-Client-OS.
// Computed once from runtime.GOOS so the server doesn't need to reverse-map
// Go's os names ("darwin"/"windows"/"linux") into the protocol vocabulary.
var ClientOS = normalizeGOOS(runtime.GOOS)

func normalizeGOOS(goos string) string {
	switch goos {
	case "darwin":
		return "macos"
	case "windows":
		return "windows"
	case "linux":
		return "linux"
	default:
		return goos
	}
}

// APIClient is a REST client for the Multica server API.
// Used by ctrl subcommands (agent, runtime, status, etc.). Requests
// automatically include auth and execution context headers when configured.
type APIClient struct {
	BaseURL     string
	WorkspaceID string
	Token       string
	AgentID     string // When set, requests are attributed to this agent instead of the user.
	TaskID      string // When set, sent as X-Task-ID for agent-task validation.
	HTTPClient  *http.Client

	// DryRun, when non-nil, intercepts every mutating request (POST/PUT/
	// PATCH/DELETE): the hook receives the method, path, and body, the
	// request is NOT sent, and the method returns ErrDryRun. Reads pass
	// through normally so read-then-write flows still resolve real data.
	DryRun func(method, path string, body any)

	// Identity overrides. Empty values fall back to the package-level
	// ClientPlatform / ClientVersion / ClientOS.
	Platform string
	Version  string
	OS       string
}

type HTTPError struct {
	Method     string
	Path       string
	StatusCode int
	Body       string
}

func (e *HTTPError) Error() string {
	return fmt.Sprintf("%s %s returned %d: %s", e.Method, e.Path, e.StatusCode, strings.TrimSpace(e.Body))
}

// newHTTPError builds a *HTTPError from an error response (status >= 400),
// reading a capped slice of the body. Every Multica API helper funnels its
// >= 400 responses through this so the top-level FormatError / ExitCodeFor can
// classify the failure via errors.As(err, **HTTPError) regardless of which
// HTTP verb the command used.
func newHTTPError(method, path string, resp *http.Response) *HTTPError {
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	return &HTTPError{
		Method:     method,
		Path:       path,
		StatusCode: resp.StatusCode,
		Body:       strings.TrimSpace(string(data)),
	}
}

// defaultHTTPTimeout is the per-request timeout for the CLI's HTTP client.
// It can be overridden with the MULTICA_HTTP_TIMEOUT environment variable
// (see httpTimeout). 30s is chosen over the historical 15s because complex
// networks (notably in mainland China) routinely need more than 15s to
// complete the TLS handshake plus request round-trip, which surfaced as an
// opaque "context deadline exceeded" to users.
const defaultHTTPTimeout = 30 * time.Second

// httpTimeout returns the HTTP client timeout, honoring MULTICA_HTTP_TIMEOUT.
// The value may be a Go duration string ("45s", "2m") or a plain integer
// number of seconds ("45"). Invalid or non-positive values fall back to the
// default.
func httpTimeout() time.Duration {
	v := strings.TrimSpace(os.Getenv("MULTICA_HTTP_TIMEOUT"))
	if v == "" {
		return defaultHTTPTimeout
	}
	if d, err := time.ParseDuration(v); err == nil && d > 0 {
		return d
	}
	if secs, err := strconv.Atoi(v); err == nil && secs > 0 {
		return time.Duration(secs) * time.Second
	}
	return defaultHTTPTimeout
}

// apiContextGrace is added on top of the HTTP transport timeout when deriving
// a command-level context deadline, so the transport timeout (which produces a
// clean, classifiable "request timed out" error) is the one that fires rather
// than the outer context being canceled first.
const apiContextGrace = 5 * time.Second

// APITimeout returns the deadline budget for a single CLI API command. It is
// always at least the configured HTTP transport timeout (see httpTimeout,
// which honors MULTICA_HTTP_TIMEOUT) plus a small grace margin, so a
// command-level context never truncates an in-flight request below the timeout
// the user configured. This is the fix for command contexts that previously
// hardcoded a 15s deadline shorter than the 30s/env transport timeout.
func APITimeout() time.Duration {
	return AtLeastAPITimeout(0)
}

// AtLeastAPITimeout returns max(min, APITimeout()). Use it for commands that
// need a larger floor than usual (for example file uploads, which historically
// used a 60s budget).
func AtLeastAPITimeout(min time.Duration) time.Duration {
	budget := httpTimeout() + apiContextGrace
	if min > budget {
		return min
	}
	return budget
}

// APIContext derives a command-scoped context whose deadline is APITimeout().
// The returned cancel func must be called (typically via defer) to release
// resources. Commands should use this instead of context.WithTimeout with a
// hardcoded duration so the deadline always respects MULTICA_HTTP_TIMEOUT.
func APIContext(parent context.Context) (context.Context, context.CancelFunc) {
	if parent == nil {
		parent = context.Background()
	}
	return context.WithTimeout(parent, APITimeout())
}

// NewAPIClient creates a new API client for ctrl commands.
func NewAPIClient(baseURL, workspaceID, token string) *APIClient {
	return &APIClient{
		BaseURL:     strings.TrimRight(baseURL, "/"),
		WorkspaceID: workspaceID,
		Token:       token,
		HTTPClient:  &http.Client{Timeout: httpTimeout()},
	}
}

// PrintDryRunHook returns a DryRun hook that prints each intercepted request
// to w in a stable, human-readable form:
//
//	DRY-RUN POST /api/issues
//	{ "title": "..." }
func PrintDryRunHook(w io.Writer) func(method, path string, body any) {
	return func(method, path string, body any) {
		fmt.Fprintf(w, "DRY-RUN %s %s\n", method, path)
		if body != nil {
			if data, err := json.MarshalIndent(body, "", "  "); err == nil {
				fmt.Fprintln(w, string(data))
			}
		}
	}
}

func (c *APIClient) setHeaders(req *http.Request) {
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	if c.WorkspaceID != "" {
		req.Header.Set("X-Workspace-ID", c.WorkspaceID)
	}
	if c.AgentID != "" {
		req.Header.Set("X-Agent-ID", c.AgentID)
	}
	if c.TaskID != "" {
		req.Header.Set("X-Task-ID", c.TaskID)
	}

	platform := c.Platform
	if platform == "" {
		platform = ClientPlatform
	}
	if platform != "" {
		req.Header.Set("X-Client-Platform", platform)
	}
	version := c.Version
	if version == "" {
		version = ClientVersion
	}
	if version != "" {
		req.Header.Set("X-Client-Version", version)
	}
	osName := c.OS
	if osName == "" {
		osName = ClientOS
	}
	if osName != "" {
		req.Header.Set("X-Client-OS", osName)
	}
}

// GetJSON performs a GET request and decodes the JSON response.
//
// On an HTTP error response (status >= 400) the returned error is a
// *HTTPError so callers can use errors.As to inspect the status code
// (for example to recognize a 404 from a server that does not expose a
// given endpoint and degrade gracefully). The error string format
// ("GET <path> returned <code>: <body>") is preserved by HTTPError.Error().
func (c *APIClient) GetJSON(ctx context.Context, path string, out any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+path, nil)
	if err != nil {
		return err
	}
	c.setHeaders(req)

	resp, err := c.HTTPClient.Do(req)
	err = wrapTransport(req, err)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return newHTTPError(http.MethodGet, path, resp)
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// GetJSONWithHeaders performs a GET request, decodes the JSON response, and
// returns the response headers. Useful when callers need header values like
// X-Total-Count for pagination.
func (c *APIClient) GetJSONWithHeaders(ctx context.Context, path string, out any) (http.Header, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+path, nil)
	if err != nil {
		return nil, err
	}
	c.setHeaders(req)

	resp, err := c.HTTPClient.Do(req)
	err = wrapTransport(req, err)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return nil, newHTTPError(http.MethodGet, path, resp)
	}
	if out != nil {
		if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
			return resp.Header, err
		}
	}
	return resp.Header, nil
}

// DeleteJSON performs a DELETE request.
func (c *APIClient) DeleteJSON(ctx context.Context, path string) error {
	if c.DryRun != nil {
		c.DryRun(http.MethodDelete, path, nil)
		return ErrDryRun
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, c.BaseURL+path, nil)
	if err != nil {
		return err
	}
	c.setHeaders(req)

	resp, err := c.HTTPClient.Do(req)
	err = wrapTransport(req, err)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return newHTTPError(http.MethodDelete, path, resp)
	}
	return nil
}

// DeleteJSONWithBody performs a DELETE request with a JSON body.
func (c *APIClient) DeleteJSONWithBody(ctx context.Context, path string, body any) error {
	data, err := json.Marshal(body)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, c.BaseURL+path, bytes.NewReader(data))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	c.setHeaders(req)

	resp, err := c.HTTPClient.Do(req)
	err = wrapTransport(req, err)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return newHTTPError(http.MethodDelete, path, resp)
	}
	return nil
}

// PostJSON performs a POST request with a JSON body.
func (c *APIClient) PostJSON(ctx context.Context, path string, body any, out any) error {
	if c.DryRun != nil {
		c.DryRun(http.MethodPost, path, body)
		return ErrDryRun
	}
	data, err := json.Marshal(body)
	if err != nil {
		return err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+path, bytes.NewReader(data))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	c.setHeaders(req)

	resp, err := c.HTTPClient.Do(req)
	err = wrapTransport(req, err)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return newHTTPError(http.MethodPost, path, resp)
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// PutJSON performs a PUT request with a JSON body.
func (c *APIClient) PutJSON(ctx context.Context, path string, body any, out any) error {
	if c.DryRun != nil {
		c.DryRun(http.MethodPut, path, body)
		return ErrDryRun
	}
	data, err := json.Marshal(body)
	if err != nil {
		return err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPut, c.BaseURL+path, bytes.NewReader(data))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	c.setHeaders(req)

	resp, err := c.HTTPClient.Do(req)
	err = wrapTransport(req, err)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return newHTTPError(http.MethodPut, path, resp)
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// PatchJSON performs a PATCH request with a JSON body.
func (c *APIClient) PatchJSON(ctx context.Context, path string, body any, out any) error {
	if c.DryRun != nil {
		c.DryRun(http.MethodPatch, path, body)
		return ErrDryRun
	}
	data, err := json.Marshal(body)
	if err != nil {
		return err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPatch, c.BaseURL+path, bytes.NewReader(data))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	c.setHeaders(req)

	resp, err := c.HTTPClient.Do(req)
	err = wrapTransport(req, err)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return newHTTPError(http.MethodPatch, path, resp)
	}
	if out == nil {
		return nil
	}
	return json.NewDecoder(resp.Body).Decode(out)
}

// AttachmentResponse mirrors the server's upload-file response.
type AttachmentResponse struct {
	ID          string `json:"id"`
	URL         string `json:"url"`
	DownloadURL string `json:"download_url"`
	Filename    string `json:"filename"`
	ContentType string `json:"content_type"`
	SizeBytes   int64  `json:"size_bytes"`
	CreatedAt   string `json:"created_at"`
}

// UploadFile uploads a file via multipart form to /api/upload-file.
// It returns the attachment ID from the server response.
func (c *APIClient) UploadFile(ctx context.Context, fileData []byte, filename string, issueID string) (string, error) {
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)

	part, err := writer.CreateFormFile("file", filepath.Base(filename))
	if err != nil {
		return "", fmt.Errorf("create form file: %w", err)
	}
	if _, err := part.Write(fileData); err != nil {
		return "", fmt.Errorf("write file data: %w", err)
	}

	if issueID != "" {
		if err := writer.WriteField("issue_id", issueID); err != nil {
			return "", fmt.Errorf("write issue_id field: %w", err)
		}
	}

	if err := writer.Close(); err != nil {
		return "", fmt.Errorf("close multipart writer: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/api/upload-file", &body)
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())
	c.setHeaders(req)

	resp, err := c.HTTPClient.Do(req)
	err = wrapTransport(req, err)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return "", newHTTPError(http.MethodPost, "/api/upload-file", resp)
	}

	var result map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("decode upload response: %w", err)
	}

	id, _ := result["id"].(string)
	if id == "" {
		return "", fmt.Errorf("upload response missing attachment id")
	}
	return id, nil
}

// UploadFileWithURL uploads a file via multipart form to /api/upload-file
// without associating it with an issue or comment. It decodes the full
// AttachmentResponse and returns the attachment ID and URL.
func (c *APIClient) UploadFileWithURL(ctx context.Context, fileData []byte, filename string) (string, string, error) {
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)

	part, err := writer.CreateFormFile("file", filepath.Base(filename))
	if err != nil {
		return "", "", fmt.Errorf("create form file: %w", err)
	}
	if _, err := part.Write(fileData); err != nil {
		return "", "", fmt.Errorf("write file data: %w", err)
	}

	if err := writer.Close(); err != nil {
		return "", "", fmt.Errorf("close multipart writer: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/api/upload-file", &body)
	if err != nil {
		return "", "", err
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())
	c.setHeaders(req)

	// Use a client that respects the context deadline for slow uploads
	// (e.g. avatar uploads with 5MB files). The default HTTP client timeout
	// shadows any longer context deadline.
	httpClient := c.HTTPClient
	if deadline, ok := ctx.Deadline(); ok {
		remaining := time.Until(deadline)
		if remaining > httpClient.Timeout {
			clientCopy := *httpClient
			clientCopy.Timeout = remaining
			httpClient = &clientCopy
		}
	}

	resp, err := httpClient.Do(req)
	err = wrapTransport(req, err)
	if err != nil {
		return "", "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return "", "", newHTTPError(http.MethodPost, "/api/upload-file", resp)
	}

	var result AttachmentResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", "", fmt.Errorf("decode upload response: %w", err)
	}
	if result.URL == "" {
		return "", "", fmt.Errorf("upload response missing attachment url")
	}
	// Allow empty ID: the server returns id="" in the fallback path where
	// S3 upload succeeded but the attachment DB record failed. The file
	// is still usable via its URL.
	return result.ID, result.URL, nil
}

// DownloadFile downloads a file from the given URL and returns the response body.
// This is used for downloading attachments via their signed download_url.
// Downloads are limited to 100 MB to match the upload size limit.
//
// The URL may be absolute (a signed CloudFront/S3 URL) or relative
// (a server-relative path like "/api/attachments/{id}/download" or
// "/uploads/...") depending on how the
// server is configured. Relative URLs are resolved against the client's
// BaseURL and sent with the standard auth headers; absolute URLs are
// used as-is so that their query-string signatures are not disturbed.
func (c *APIClient) DownloadFile(ctx context.Context, downloadURL string) ([]byte, error) {
	isRelative := !strings.HasPrefix(downloadURL, "http://") && !strings.HasPrefix(downloadURL, "https://")
	if isRelative {
		if c.BaseURL == "" {
			return nil, fmt.Errorf("download URL %q is relative but client has no BaseURL", downloadURL)
		}
		downloadURL = c.BaseURL + downloadURL
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, downloadURL, nil)
	if err != nil {
		return nil, err
	}
	if isRelative {
		c.setHeaders(req)
	}

	resp, err := c.HTTPClient.Do(req)
	err = wrapTransport(req, err)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return nil, newHTTPError(http.MethodGet, downloadURL, resp)
	}

	const maxDownloadSize = 100 << 20 // 100 MB
	return io.ReadAll(io.LimitReader(resp.Body, maxDownloadSize))
}

// HealthCheck hits the /health endpoint and returns the response body.
func (c *APIClient) HealthCheck(ctx context.Context) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+"/health", nil)
	if err != nil {
		return "", err
	}
	resp, err := c.HTTPClient.Do(req)
	err = wrapTransport(req, err)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	data, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if resp.StatusCode >= 400 {
		return "", &HTTPError{
			Method:     http.MethodGet,
			Path:       "/health",
			StatusCode: resp.StatusCode,
			Body:       strings.TrimSpace(string(data)),
		}
	}
	return strings.TrimSpace(string(data)), nil
}

// ---------------------------------------------------------------------------
// Artifacts
// ---------------------------------------------------------------------------

// ArtifactResponse mirrors handler.ArtifactResponse — one typed run artifact
// row. DownloadURL is a server-relative API path (/api/artifacts/{id}/download).
type ArtifactResponse struct {
	ID          string          `json:"id"`
	WorkspaceID string          `json:"workspace_id"`
	TaskID      *string         `json:"task_id"`
	IssueID     *string         `json:"issue_id"`
	Kind        string          `json:"kind"`
	Name        string          `json:"name"`
	SizeBytes   int64           `json:"size_bytes"`
	ContentType string          `json:"content_type"`
	Meta        json.RawMessage `json:"meta"`
	DownloadURL string          `json:"download_url"`
	CreatedAt   string          `json:"created_at"`
}

// UploadArtifact uploads a file to POST /api/tasks/{taskId}/artifacts as a
// multipart form (file + optional kind + optional meta JSON string) and
// decodes the created artifact row.
func (c *APIClient) UploadArtifact(ctx context.Context, taskID string, fileData []byte, filename, kind, meta string) (*ArtifactResponse, error) {
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)

	part, err := writer.CreateFormFile("file", filepath.Base(filename))
	if err != nil {
		return nil, fmt.Errorf("create form file: %w", err)
	}
	if _, err := part.Write(fileData); err != nil {
		return nil, fmt.Errorf("write file data: %w", err)
	}
	if kind != "" {
		if err := writer.WriteField("kind", kind); err != nil {
			return nil, fmt.Errorf("write kind field: %w", err)
		}
	}
	if meta != "" {
		if err := writer.WriteField("meta", meta); err != nil {
			return nil, fmt.Errorf("write meta field: %w", err)
		}
	}
	if err := writer.Close(); err != nil {
		return nil, fmt.Errorf("close multipart writer: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+"/api/tasks/"+taskID+"/artifacts", &body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())
	c.setHeaders(req)

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		respData, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return nil, fmt.Errorf("upload artifact returned %d: %s", resp.StatusCode, strings.TrimSpace(string(respData)))
	}

	var result ArtifactResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode artifact response: %w", err)
	}
	return &result, nil
}

// DownloadArtifact fetches GET /api/artifacts/{id}/download and returns the
// body plus the server-suggested filename from Content-Disposition.
func (c *APIClient) DownloadArtifact(ctx context.Context, id string) ([]byte, string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+"/api/artifacts/"+id+"/download", nil)
	if err != nil {
		return nil, "", err
	}
	c.setHeaders(req)

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		respData, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return nil, "", fmt.Errorf("download artifact returned %d: %s", resp.StatusCode, strings.TrimSpace(string(respData)))
	}

	filename := ""
	if cd := resp.Header.Get("Content-Disposition"); cd != "" {
		if _, params, err := mime.ParseMediaType(cd); err == nil {
			filename = params["filename"]
		}
	}

	const maxDownloadSize = 100 << 20 // 100 MB
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxDownloadSize))
	if err != nil {
		return nil, "", err
	}
	return data, filename, nil
}
