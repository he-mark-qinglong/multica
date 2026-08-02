"""Token-based authentication for the risk dashboard (H-auth).

The existing :mod:`_shared.ops.risk_dashboard` generates a self-contained
HTML page and writes it to disk via ``watch_loop``.  That page had **zero
authentication** — anyone who could reach the file (or a future HTTP
serving layer) could read live positions, equity, and VaR.

This module adds a thin token-gated HTTP layer:

  * :func:`generate_token` — cryptographically random bearer token.
  * :func:`check_token` — constant-time comparison (avoids timing oracles).
  * :class:`TokenAuthHandler` — ``http.server.BaseHTTPRequestHandler``
    subclass that serves a static HTML file **only** when the request
    carries the correct token (``?token=...`` query parameter or
    ``Authorization: Bearer ...`` header).  Missing / wrong token →
    ``401 Unauthorized``.
  * :func:`serve_dashboard_blocking` — convenience wrapper that starts
    a single-threaded HTTP server in the calling thread.

The token is loaded from the environment by convention
(``DASHBOARD_TOKEN``); fall back to :func:`generate_token` for local dev.

References:
- OWASP Authentication Cheat Sheet — bearer tokens in URL must use HTTPS;
  for local-only / VPC-internal dashboards HTTP + token is acceptable.
- ``secrets.compare_digest`` — constant-time string comparison to avoid
  timing side-channels on token validation.
- http.server docs — ``BaseHTTPRequestHandler`` is deliberately minimal;
  for internet-facing deployments put a reverse proxy (nginx / Caddy)
  in front with TLS termination.
"""
from __future__ import annotations

import hmac
import secrets
import threading
import urllib.parse
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def generate_token(nbytes: int = 32) -> str:
    """Return a URL-safe cryptographically random token (default ~43 chars)."""
    if nbytes < 16:
        raise ValueError("nbytes must be >= 16 for adequate entropy")
    return secrets.token_urlsafe(nbytes)


def check_token(provided: str | None, expected: str) -> bool:
    """Constant-time token comparison.

    Returns ``False`` when *provided* is ``None`` or empty, or when it
    does not match *expected*.  Uses :func:`hmac.compare_digest` to
    avoid timing oracles.
    """
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def _extract_token(handler: BaseHTTPRequestHandler) -> str | None:
    """Pull the bearer token from query string or Authorization header."""
    # 1. ?token=... query parameter
    parsed = urllib.parse.urlparse(handler.path)
    qs = urllib.parse.parse_qs(parsed.query)
    token_list = qs.get("token")
    if token_list:
        return token_list[0]

    # 2. Authorization: Bearer <token>
    auth_header = handler.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):].strip()

    return None


class TokenAuthHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves a single HTML file behind a token gate.

    Class attributes (set before ``serve_forever``)::

        TokenAuthHandler.html_path = "/path/to/dashboard.html"
        TokenAuthHandler.auth_token = "expected-token"
        TokenAuthHandler.realm     = "quant-loop-dashboard"

    A request without a valid token receives ``401`` with a plain-text
    body.  A valid request receives the file contents with
    ``Content-Type: text/html``.
    """

    html_path: str = ""
    auth_token: str = ""
    realm: str = "quant-loop-dashboard"

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler convention)
        token = _extract_token(self)
        if not check_token(token, self.auth_token):
            self._send_unauthorized()
            return
        try:
            content = Path(self.html_path).read_bytes()
        except FileNotFoundError:
            self._send_not_found()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_HEAD(self) -> None:  # noqa: N802
        token = _extract_token(self)
        if not check_token(token, self.auth_token):
            self._send_unauthorized()
            return
        if not Path(self.html_path).exists():
            self._send_not_found()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send_unauthorized(self) -> None:
        body = b"401 Unauthorized: missing or invalid token\n"
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "WWW-Authenticate",
            f'Bearer realm="{self.realm}"',
        )
        self.end_headers()
        self.wfile.write(body)

    def _send_not_found(self) -> None:
        body = b"404 Not Found: dashboard file not generated yet\n"
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        """Suppress default stderr logging (no secrets in access logs)."""
        # Intentionally silent; operators route through structured_log instead.
        pass


def serve_dashboard_blocking(
    html_path: str | Path,
    token: str,
    host: str = "0.0.0.0",
    port: int = 8080,
    *,
    stop: Callable[[], bool] | None = None,
) -> int:
    """Start a token-gated HTTP server that serves *html_path*.

    The server runs in the calling thread (blocking).  Pass *stop* — a
    callable returning ``True`` to shut down — for controlled teardown.

    Returns the number of requests served.
    """
    html_path = str(html_path)
    TokenAuthHandler.html_path = html_path
    TokenAuthHandler.auth_token = token

    server = HTTPServer((host, port), TokenAuthHandler)
    served = 0
    try:
        while True:
            if stop is not None and stop():
                break
            server.handle_request()  # non-blocking: processes one request
            served += 1
    finally:
        server.server_close()
    return served


def serve_dashboard_background(
    html_path: str | Path,
    token: str,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> tuple[HTTPServer, threading.Thread]:
    """Start the dashboard server in a daemon thread.

    Returns ``(server, thread)`` so the caller can ``server.shutdown()``
    or ``thread.join()``.
    """
    html_path = str(html_path)
    TokenAuthHandler.html_path = html_path
    TokenAuthHandler.auth_token = token

    server = HTTPServer((host, port), TokenAuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
