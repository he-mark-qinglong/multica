"""Tests for _shared/ops/dashboard_auth.py (token-gated dashboard)."""
import sys

sys.path.insert(0, "/Users/mark/multica/quant-loop")

import http.client

import pytest

from _shared.ops.dashboard_auth import (
    check_token,
    generate_token,
    serve_dashboard_background,
)


# --- generate_token ----------------------------------------------------------
def test_generate_token_is_url_safe():
    token = generate_token()
    # token_urlsafe output contains only [A-Za-z0-9_-]
    assert all(c.isalnum() or c in "-_" for c in token)
    assert len(token) >= 32  # 32 bytes -> ~43 chars


def test_generate_token_entropy():
    tokens = {generate_token() for _ in range(100)}
    assert len(tokens) == 100  # no collisions


def test_generate_token_rejects_short():
    with pytest.raises(ValueError, match="entropy"):
        generate_token(nbytes=8)


# --- check_token -------------------------------------------------------------
def test_check_token_correct():
    assert check_token("secret123", "secret123") is True


def test_check_token_wrong():
    assert check_token("wrong", "secret123") is False


def test_check_token_none():
    assert check_token(None, "secret123") is False
    assert check_token("", "secret123") is False


def test_check_token_empty_expected():
    assert check_token("anything", "") is False


# --- TokenAuthHandler end-to-end ---------------------------------------------
@pytest.fixture
def dashboard_server(tmp_path):
    """Start a real HTTP server on a random port serving a temp HTML file."""
    html_file = tmp_path / "dashboard.html"
    html_file.write_text("<html><body>OK</body></html>")

    token = "test-secret-token-xyz"
    server, thread = serve_dashboard_background(html_file, token, "127.0.0.1", port=0)
    # HTTPServer binds to port 0 → OS assigns a free port
    actual_port = server.server_address[1]
    yield actual_port, token
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _get(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body


def test_no_token_returns_401(dashboard_server):
    port, token = dashboard_server
    status, body = _get(port, "/")
    assert status == 401
    assert "Unauthorized" in body


def test_wrong_token_returns_401(dashboard_server):
    port, token = dashboard_server
    status, body = _get(port, "/?token=wrong")
    assert status == 401


def test_correct_token_query_param_returns_html(dashboard_server):
    port, token = dashboard_server
    status, body = _get(port, f"/?token={token}")
    assert status == 200
    assert "OK" in body


def test_correct_token_bearer_header_returns_html(dashboard_server):
    port, token = dashboard_server
    status, body = _get(port, "/", headers={"Authorization": f"Bearer {token}"})
    assert status == 200
    assert "OK" in body


def test_response_headers(dashboard_server):
    port, token = dashboard_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", f"/?token={token}")
    resp = conn.getresponse()
    assert resp.getheader("Content-Type") == "text/html; charset=utf-8"
    assert resp.getheader("Cache-Control") == "no-store"
    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    resp.read()
    conn.close()


def test_www_authenticate_header_on_401(dashboard_server):
    port, token = dashboard_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/")
    resp = conn.getresponse()
    www_auth = resp.getheader("WWW-Authenticate", "")
    resp.read()
    conn.close()
    assert "Bearer" in www_auth
    assert "quant-loop-dashboard" in www_auth


def test_head_request_works_with_token(dashboard_server):
    port, token = dashboard_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("HEAD", f"/?token={token}")
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "text/html; charset=utf-8"
    conn.close()


# --- missing file scenario ---------------------------------------------------
def test_404_when_html_not_yet_generated(tmp_path):
    html_file = tmp_path / "nonexistent.html"
    server, thread = serve_dashboard_background(html_file, "tok", "127.0.0.1", port=0)
    port = server.server_address[1]
    try:
        status, body = _get(port, "/?token=tok")
        assert status == 404
        assert "not generated" in body.lower()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
