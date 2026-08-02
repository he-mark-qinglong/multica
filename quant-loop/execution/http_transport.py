"""execution.http_transport — real HTTP transport for venue adapters.

Provides a production-ready HTTP transport layer that performs actual
network I/O via ``urllib.request`` (stdlib only — no ``requests``
dependency required).  Designed as a drop-in ``callable_send`` for the
existing ``OutboundTransport`` / venue adapter transport classes.

Key features:

* **HMAC signing reuse** — delegates to the per-venue signer
  (``sign_binance_perp_request`` / ``sign_binance_spot_request``) so the
  signature logic is not duplicated.
* **Timeout / retry** — configurable connect/read timeout, bounded
  retry with exponential backoff on transient failures (5xx, 429,
  socket errors).
* **429 rate-limit handling** — honours ``Retry-After`` header when
  present, otherwise backs off with the exponential schedule.
* **Paper / live dual mode** — ``paper=True`` (default) intercepts
  every request and returns a deterministic mock ack without touching
  the network.  ``paper=False`` (live mode) requires an explicit
  ``api_key`` + ``api_secret`` and raises if either is missing.
* **Secrets** — API keys are read from the constructor args or from
  environment variables when ``env_key`` / ``env_secret`` are set.
  Live mode is **default off** — the caller must explicitly opt in.

Usage::

    from execution.http_transport import HttpTransport
    from execution.runner import ExecutionRunner, OutboundTransport

    # Paper mode (default) — safe for testing.
    transport = OutboundTransport(callable_send=HttpTransport.paper())

    # Live mode — requires explicit credentials.
    live = HttpTransport.live(
        base_url="https://fapi.binance.com",
        api_key=os.environ["BINANCE_API_KEY"],
        api_secret=os.environ["BINANCE_API_SECRET"],
        signer=sign_binance_perp_request,
    )
    runner = ExecutionRunner(journal=journal, transport=OutboundTransport(callable_send=live))

Wire protocol:

* The transport is agnostic to HTTP method — the caller supplies the
  method in the request dict via ``"_method"`` (default ``"POST"``).
  Binance cancel uses ``"_method": "DELETE"``; Hyperliquid always uses
  ``"POST"``.
* For Binance (query-string style APIs), wire params are URL-encoded
  into the query string; the body is empty.
* For Hyperliquid (JSON body style APIs), wire params are sent as a
  JSON POST body.
* The ``"_path"`` key in the request dict overrides the default path.
* The ``"_json_body"`` flag (or detection of a nested ``"action"``
  key) switches to JSON-body mode.

The transport is intentionally thin — venue-specific wire shaping
(Binance ``newOrderRespType``, HL EIP-712 envelope) belongs in the
adapter's wire builder; this module only handles transport concerns
(sign, send, retry, classify).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

# Re-export signers for convenience (importable from one place).
try:
    from execution.venue_adapter_binance_perp_p7exec_003.venue_adapter_binance_perp import (  # noqa: F401
        sign_binance_perp_request,
    )
except ImportError:  # pragma: no cover — vendored / sys.path fallback
    sign_binance_perp_request = None  # type: ignore[assignment]

try:
    from execution.venue_adapter_binance_spot.venue_adapter_binance_spot import (  # noqa: F401
        sign_binance_spot_request,
    )
except ImportError:  # pragma: no cover
    sign_binance_spot_request = None  # type: ignore[assignment]


#: Default HTTP connect/read timeout (seconds).
DEFAULT_TIMEOUT_S = 10.0

#: Default max retry attempts for transient failures.
DEFAULT_MAX_RETRIES = 3

#: Base backoff (seconds) for exponential schedule.
DEFAULT_BACKOFF_BASE_S = 0.5

#: HTTP statuses that trigger a retry.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Binance REST base URLs.
BINANCE_PERP_REST_BASE = "https://fapi.binance.com"
BINANCE_SPOT_REST_BASE = "https://api.binance.com"

#: Default paths.
DEFAULT_ORDER_PATH = "/fapi/v1/order"


@dataclass
class HttpTransportPolicy:
    """Transport-level configuration (no secrets)."""

    timeout_s: float = DEFAULT_TIMEOUT_S
    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base_s: float = DEFAULT_BACKOFF_BASE_S
    #: Honour ``Retry-After`` on 429 responses.  When False, the
    #: exponential schedule is used unconditionally.
    honour_retry_after: bool = True

    def __post_init__(self) -> None:
        if self.timeout_s <= 0:
            raise ValueError(f"timeout_s must be positive, got {self.timeout_s!r}")
        if self.max_retries < 0:
            raise ValueError(
                f"max_retries must be non-negative, got {self.max_retries!r}"
            )
        if self.backoff_base_s < 0:
            raise ValueError(
                f"backoff_base_s must be non-negative, got {self.backoff_base_s!r}"
            )


#: Signer type — ``(params, *, api_secret, ...) -> signed_params_dict``.
SignerFn = Callable[..., Dict[str, Any]]


class HttpTransportError(Exception):
    """Raised when the transport exhausts retries or encounters a
    non-retryable failure in live mode."""


@dataclass
class HttpTransport:
    """Real or paper HTTP transport callable.

    Construct via :meth:`paper` or :meth:`live` factory methods (or
    directly with explicit parameters).  Instances are callable:
    ``transport(request_dict) -> ack_dict`` — the same interface the
    runner's ``OutboundTransport`` expects for ``callable_send``.
    """

    base_url: str = BINANCE_PERP_REST_BASE
    default_path: str = DEFAULT_ORDER_PATH
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    signer: Optional[SignerFn] = None
    paper: bool = True
    policy: HttpTransportPolicy = field(default_factory=HttpTransportPolicy)
    #: Injected HTTP poster (for testing).  When ``None``, the real
    #: ``urllib.request.urlopen`` is used.  Paper mode ignores this.
    _http_post: Optional[Callable[..., "HttpResponse"]] = None

    # -- factory methods ---------------------------------------------------

    @classmethod
    def paper(
        cls,
        *,
        base_url: str = BINANCE_PERP_REST_BASE,
        default_path: str = DEFAULT_ORDER_PATH,
    ) -> "HttpTransport":
        """Create a paper-mode transport (no network I/O).

        Returns deterministic mock acks.  Safe for tests and
        cold-start smoke runs.
        """
        return cls(
            base_url=base_url,
            default_path=default_path,
            paper=True,
        )

    @classmethod
    def live(
        cls,
        *,
        base_url: str = BINANCE_PERP_REST_BASE,
        default_path: str = DEFAULT_ORDER_PATH,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        signer: Optional[SignerFn] = None,
        env_key: Optional[str] = None,
        env_secret: Optional[str] = None,
        policy: Optional[HttpTransportPolicy] = None,
    ) -> "HttpTransport":
        """Create a live-mode transport (real network I/O).

        **Live mode is default-off** — the caller must explicitly
        supply ``api_key`` + ``api_secret`` (or the matching env var
        names).  Raises :class:`ValueError` when credentials are
        missing.

        Parameters
        ----------
        env_key / env_secret
            Environment variable names to read credentials from when
            ``api_key`` / ``api_secret`` are not passed directly.
            E.g. ``env_key="BINANCE_API_KEY"`` reads
            ``os.environ["BINANCE_API_KEY"]``.
        """
        resolved_key = api_key
        if resolved_key is None and env_key:
            resolved_key = os.environ.get(env_key)
        resolved_secret = api_secret
        if resolved_secret is None and env_secret:
            resolved_secret = os.environ.get(env_secret)

        if not resolved_key:
            raise ValueError(
                "HttpTransport.live: api_key is required (pass "
                "api_key= or set the env_key variable)"
            )
        if not resolved_secret:
            raise ValueError(
                "HttpTransport.live: api_secret is required (pass "
                "api_secret= or set the env_secret variable)"
            )

        return cls(
            base_url=base_url,
            default_path=default_path,
            api_key=resolved_key,
            api_secret=resolved_secret,
            signer=signer,
            paper=False,
            policy=policy or HttpTransportPolicy(),
        )

    # -- callable interface ------------------------------------------------

    def __call__(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        """Process one request dict and return the venue ack dict."""
        if self.paper:
            return self._paper_call(request)
        return self._live_call(request)

    # -- paper mode --------------------------------------------------------

    def _paper_call(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        """Deterministic mock response — no network I/O.

        The paper ack shape mirrors a successful venue ack so the
        runner's ``classify_terminal_event`` classifies it as a fill.
        """
        action = str(request.get("action") or "").lower()
        coid = (
            request.get("client_order_id")
            or request.get("clientOrderId")
            or request.get("origClientOrderId")
            or ""
        )
        symbol = request.get("symbol") or ""
        side = request.get("side") or "BUY"
        qty = request.get("qty") or request.get("quantity") or 0
        price = request.get("price") or request.get("expected_price") or 0

        if action == "cancel":
            return {
                "ok": True,
                "status": "CANCELED",
                "clientOrderId": coid,
                "symbol": symbol,
                "venue": "paper",
            }
        if action == "amend":
            return {
                "ok": True,
                "status": "NEW",
                "clientOrderId": coid,
                "symbol": symbol,
                "side": side,
                "price": str(price),
                "origQty": str(qty),
                "executedQty": "0",
                "venue": "paper",
            }
        # Default: treat as new order.
        return {
            "ok": True,
            "status": "FILLED",
            "clientOrderId": coid,
            "orderId": 1,
            "symbol": symbol,
            "side": side,
            "price": str(price),
            "origQty": str(qty),
            "executedQty": str(qty),
            "cumQty": str(qty),
            "avgPrice": str(price),
            "venue": "paper",
        }

    # -- live mode ---------------------------------------------------------

    def _live_call(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        """Real HTTP POST/DELETE with signing, retry, and 429 backoff."""
        if not self.api_secret:
            raise HttpTransportError(
                "live mode requires api_secret"
            )

        # Separate transport-control keys from wire params.
        method = str(request.get("_method") or "POST").upper()
        path = str(request.get("_path") or self.default_path)
        wire = {k: v for k, v in request.items() if not k.startswith("_")}

        # Determine if this is a JSON-body request (HL style) or a
        # query-string request (Binance style).
        is_json_body = bool(
            request.get("_json_body")
            or "action" in wire
            or "nonce" in wire
            or "signature" in wire
        )

        # Sign query-string requests (Binance).  JSON-body requests
        # (HL) arrive pre-signed (the signer wraps the action in the
        # adapter's transport layer).
        if not is_json_body and self.signer is not None:
            wire = self.signer(
                wire,
                api_secret=self.api_secret,
            )

        url = self.base_url.rstrip("/") + path
        headers: Dict[str, str] = {"User-Agent": "quant-loop/1.0"}
        if self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key

        body_bytes: Optional[bytes]
        if is_json_body:
            headers["Content-Type"] = "application/json"
            body_bytes = json.dumps(wire).encode("utf-8")
        else:
            # Query string goes in the URL for GET/DELETE, in the
            # body for POST (Binance convention).  We use query-string
            # in the URL for all methods — Binance accepts both.
            qs = urllib.parse.urlencode(wire, doseq=True)
            if qs:
                url = url + "?" + qs
            body_bytes = None

        return self._send_with_retry(
            url=url,
            method=method,
            headers=headers,
            body=body_bytes,
        )

    def _send_with_retry(
        self,
        *,
        url: str,
        method: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
    ) -> Dict[str, Any]:
        """Execute the HTTP request with bounded retry + backoff."""
        last_error: Optional[str] = None
        for attempt in range(self.policy.max_retries + 1):
            try:
                resp = self._do_http(
                    url=url,
                    method=method,
                    headers=headers,
                    body=body,
                )
            except _RateLimited as exc:
                last_error = str(exc)
                if attempt >= self.policy.max_retries:
                    break
                wait = self._compute_backoff(attempt, exc.retry_after_s)
                time.sleep(wait)
                continue
            except _TransientError as exc:
                last_error = str(exc)
                if attempt >= self.policy.max_retries:
                    break
                wait = self._compute_backoff(attempt, None)
                time.sleep(wait)
                continue
            except _HttpError as exc:
                # Non-retryable HTTP error — return the error body.
                return exc.body
            except OSError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= self.policy.max_retries:
                    break
                wait = self._compute_backoff(attempt, None)
                time.sleep(wait)
                continue

            # Success — parse and return.
            return self._parse_response(resp)

        raise HttpTransportError(
            f"exhausted {self.policy.max_retries} retries: {last_error}"
        )

    def _do_http(
        self,
        *,
        url: str,
        method: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
    ) -> "HttpResponse":
        """Execute one HTTP request, classifying the outcome."""
        http_post = self._http_post or _default_http_post
        return http_post(
            url=url,
            method=method,
            headers=dict(headers),
            body=body,
            timeout_s=self.policy.timeout_s,
        )

    def _compute_backoff(
        self,
        attempt: int,
        retry_after_s: Optional[float],
    ) -> float:
        if retry_after_s is not None and self.policy.honour_retry_after:
            return float(retry_after_s)
        return self.policy.backoff_base_s * (2 ** attempt)

    @staticmethod
    def _parse_response(resp: "HttpResponse") -> Dict[str, Any]:
        """Parse the HTTP response body into a venue ack dict."""
        body_text = resp.body.decode("utf-8", errors="replace") if resp.body else ""
        if not body_text:
            return {"ok": resp.is_success, "status": resp.status_text}
        try:
            parsed = json.loads(body_text)
        except (ValueError, TypeError):
            return {
                "ok": resp.is_success,
                "status": resp.status_text,
                "raw": body_text,
            }
        if isinstance(parsed, dict):
            parsed.setdefault("ok", resp.is_success)
            return parsed
        return {"ok": resp.is_success, "status": resp.status_text, "raw": body_text}


# ---------------------------------------------------------------------------
# HTTP response wrapper + default poster (stdlib urllib)
# ---------------------------------------------------------------------------


@dataclass
class HttpResponse:
    """Minimal HTTP response envelope."""

    status: int
    body: bytes
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return 200 <= self.status < 300

    @property
    def status_text(self) -> str:
        return _STATUS_TEXTS.get(self.status, f"HTTP_{self.status}")


_STATUS_TEXTS = {
    200: "OK",
    201: "CREATED",
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_SERVER_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


class _RateLimited(Exception):
    """Raised on HTTP 429."""

    def __init__(self, retry_after_s: Optional[float] = None) -> None:
        self.retry_after_s = retry_after_s
        super().__init__(f"429 rate limited (retry_after={retry_after_s})")


class _TransientError(Exception):
    """Raised on 5xx / socket-level transient failures."""


class _HttpError(Exception):
    """Raised on non-retryable HTTP errors (4xx except 429)."""

    def __init__(self, status: int, body: Dict[str, Any]) -> None:
        self.body = body
        super().__init__(f"HTTP {status}: {body}")


def _default_http_post(
    *,
    url: str,
    method: str,
    headers: Mapping[str, str],
    body: Optional[bytes],
    timeout_s: float,
) -> HttpResponse:
    """Default HTTP poster using ``urllib.request``.

    Raises :class:`_RateLimited` on 429, :class:`_TransientError` on
    5xx / ``URLError``, and :class:`_HttpError` on non-retryable 4xx.
    """
    req = urllib.request.Request(
        url=url,
        data=body,
        method=method,
        headers=dict(headers),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return HttpResponse(
                status=resp.status,
                body=raw,
                headers=resp_headers,
            )
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        resp_headers = {k.lower(): v for k, v in exc.headers.items()}
        if exc.code == 429:
            retry_after = resp_headers.get("retry-after")
            retry_after_s: Optional[float] = None
            if retry_after:
                try:
                    retry_after_s = float(retry_after)
                except (TypeError, ValueError):
                    retry_after_s = None
            raise _RateLimited(retry_after_s=retry_after_s)
        if exc.code in _RETRY_STATUSES:
            raise _TransientError(f"HTTP {exc.code}")
        # Non-retryable 4xx — return the error body.
        body_text = raw.decode("utf-8", errors="replace") if raw else ""
        try:
            parsed = json.loads(body_text)
        except (ValueError, TypeError):
            parsed = {"ok": False, "code": exc.code, "msg": body_text}
        raise _HttpError(exc.code, parsed)
    except urllib.error.URLError as exc:
        raise _TransientError(str(exc))


__all__ = [
    "BINANCE_PERP_REST_BASE",
    "BINANCE_SPOT_REST_BASE",
    "DEFAULT_BACKOFF_BASE_S",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_ORDER_PATH",
    "DEFAULT_TIMEOUT_S",
    "HttpTransport",
    "HttpTransportError",
    "HttpTransportPolicy",
    "HttpResponse",
    "SignerFn",
]
