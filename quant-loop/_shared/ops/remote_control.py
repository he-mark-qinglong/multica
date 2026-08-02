"""Local-only remote control endpoint for the live runner (P3).

A minimal REST surface (stdlib ``http.server`` only, no web framework)
exposing four operator actions against *local* control state — the server
never talks to an exchange; it flips flag files that the strategy loop
polls, exactly like the heartbeat/kill-switch discipline elsewhere in
``_shared/ops``:

    GET  /status          -> current control state (+ heartbeat, if wired)
    POST /pause           -> latch/unlatch the pause flag   {"paused": bool}
    POST /kill            -> latch the kill flag            {"reason": str}
    POST /reload_config   -> trigger an ops-config hot reload

Every action — including ``/status`` — is appended to the audit trail
(``_shared/ops/audit_trail.py``) with ``actor="manual"``: an operator
touching the control plane is always a human-initiated transition, and
"who paused/killed/reloaded, when" is the first question in any incident
review.

Security posture: binds to 127.0.0.1 by default; there is deliberately
no authentication layer — the endpoint is a localhost operator tool, not
a network service. Do not expose it beyond the loopback interface.

References:
- Google SRE Workbook, ch. 9 "Incident Response" — a single, audited
  control surface beats ad-hoc SSH + kill during an incident.
- Nygard, "Release It!", ch. 5 — operations interfaces (control panel
  for the running process) as a first-class production feature.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from _shared.ops.audit_trail import AuditRecord, TransitionKind, append_record

PAUSE_FLAG = "paused.json"
KILL_FLAG = "killed.json"


# --- control flag files ------------------------------------------------------
def _write_flag(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomic flag write (tmp + rename, same discipline as heartbeat)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload)), encoding="utf-8")
    tmp.replace(path)


def read_flag(control_dir, name: str) -> Mapping[str, Any] | None:
    """Read one flag file; None if absent or corrupt."""
    try:
        payload = json.loads((Path(control_dir) / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def is_paused(control_dir) -> bool:
    """True while the pause flag is latched (poll from the strategy loop)."""
    return read_flag(control_dir, PAUSE_FLAG) is not None


def is_killed(control_dir) -> bool:
    """True once the kill flag is latched (poll from the strategy loop)."""
    return read_flag(control_dir, KILL_FLAG) is not None


def control_state(control_dir) -> Mapping[str, Any]:
    """Snapshot of the control flags; pure read."""
    pause = read_flag(control_dir, PAUSE_FLAG)
    kill = read_flag(control_dir, KILL_FLAG)
    return {
        "paused": pause is not None,
        "pause": pause,
        "killed": kill is not None,
        "kill": kill,
    }


# --- server ------------------------------------------------------------------
class RemoteControlServer:
    """Threaded HTTP server wrapping the four control actions.

    Attributes:
        control_dir: directory holding the flag files.
        audit_path: audit-trail JSONL every action is appended to.
        reload_config: optional callable performing an ops-config reload
            (e.g. ``OpsConfigReloader.check_once``); ``/reload_config``
            answers 409 when none is wired.
        status_extra: optional callable returning extra keys merged into
            the ``/status`` payload (e.g. heartbeat freshness).
    """

    def __init__(
        self,
        control_dir,
        audit_path,
        host: str = "127.0.0.1",
        port: int = 8765,
        reload_config: Callable[[], Mapping[str, Any]] | None = None,
        status_extra: Callable[[], Mapping[str, Any]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.control_dir = Path(control_dir)
        self.audit_path = Path(audit_path)
        self.reload_config = reload_config
        self.status_extra = status_extra
        self.clock = clock
        server = self  # closure for the handler

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:  # keep stdout clean
                pass

            def _send(self, code: int, payload: Mapping[str, Any]) -> None:
                body = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> Mapping[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                try:
                    raw = json.loads(self.rfile.read(length).decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return {}
                return raw if isinstance(raw, dict) else {}

            def do_GET(self) -> None:
                if self.path == "/status":
                    server._handle_status(self)
                else:
                    self._send(404, {"error": f"unknown path {self.path}"})

            def do_POST(self) -> None:
                if self.path == "/pause":
                    server._handle_pause(self, self._body())
                elif self.path == "/kill":
                    server._handle_kill(self, self._body())
                elif self.path == "/reload_config":
                    server._handle_reload(self)
                else:
                    self._send(404, {"error": f"unknown path {self.path}"})

        self._httpd = ThreadingHTTPServer((host, int(port)), Handler)

    # -- lifecycle -----------------------------------------------------------
    @property
    def address(self) -> tuple[str, int]:
        """The bound ``(host, port)`` (useful with port=0 in tests)."""
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    def serve_in_thread(self, daemon: bool = True) -> threading.Thread:
        """Run the server in a background thread; returns the thread."""
        thread = threading.Thread(target=self._httpd.serve_forever, daemon=daemon)
        thread.start()
        return thread

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    # -- audit ---------------------------------------------------------------
    def _audit(
        self,
        kind: TransitionKind,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        note: str = "",
    ) -> AuditRecord:
        record = AuditRecord(
            ts=float(self.clock()),
            kind=kind.value,
            actor="manual",
            before=dict(before),
            after=dict(after),
            note=note,
        )
        return append_record(self.audit_path, record)

    # -- actions ---------------------------------------------------------------
    def _handle_status(self, handler: BaseHTTPRequestHandler) -> None:
        payload: dict[str, Any] = {
            "ts": float(self.clock()),
            **control_state(self.control_dir),
        }
        if self.status_extra is not None:
            try:
                payload.update(dict(self.status_extra()))
            except Exception as exc:  # a broken probe must not break /status
                payload["status_extra_error"] = str(exc)
        self._audit(
            TransitionKind.STATUS,
            {},
            {"paused": payload["paused"], "killed": payload["killed"]},
            note="GET /status",
        )
        handler._send(200, payload)

    def _handle_pause(self, handler: BaseHTTPRequestHandler, body: Mapping[str, Any]) -> None:
        before = control_state(self.control_dir)
        target = bool(body.get("paused", True))
        flag = self.control_dir / PAUSE_FLAG
        if target:
            _write_flag(flag, {"ts": float(self.clock()), "reason": str(body.get("reason", ""))})
        else:
            try:
                flag.unlink()
            except FileNotFoundError:
                pass
        after = control_state(self.control_dir)
        self._audit(
            TransitionKind.CONFIG_CHANGE,
            {"paused": before["paused"]},
            {"paused": after["paused"]},
            note=str(body.get("reason", "")) or "POST /pause",
        )
        handler._send(200, {"ok": True, "paused": after["paused"]})

    def _handle_kill(self, handler: BaseHTTPRequestHandler, body: Mapping[str, Any]) -> None:
        before = control_state(self.control_dir)
        if not before["killed"]:  # kill is latched; first trigger wins
            _write_flag(
                self.control_dir / KILL_FLAG,
                {"ts": float(self.clock()), "reason": str(body.get("reason", ""))},
            )
        after = control_state(self.control_dir)
        self._audit(
            TransitionKind.KILL,
            {"killed": before["killed"]},
            {"killed": after["killed"]},
            note=str(body.get("reason", "")) or "POST /kill",
        )
        handler._send(200, {"ok": True, "killed": after["killed"]})

    def _handle_reload(self, handler: BaseHTTPRequestHandler) -> None:
        if self.reload_config is None:
            handler._send(409, {"ok": False, "error": "no config reloader wired"})
            return
        try:
            result = dict(self.reload_config())
        except Exception as exc:
            self._audit(TransitionKind.CONFIG_CHANGE, {}, {}, note=f"reload_config failed: {exc}")
            handler._send(500, {"ok": False, "error": str(exc)})
            return
        self._audit(
            TransitionKind.CONFIG_CHANGE, {}, {"reload": result}, note="POST /reload_config"
        )
        handler._send(200, {"ok": True, "reload": result})


def main() -> None:
    """Standalone entry: ``python -m _shared.ops.remote_control CTRL_DIR AUDIT.jsonl``."""
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("control_dir")
    ap.add_argument("audit_path")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    server = RemoteControlServer(args.control_dir, args.audit_path, host=args.host, port=args.port)
    host, port = server.address
    print(f"remote control listening on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
