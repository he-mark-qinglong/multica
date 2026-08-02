"""Deployment unit generator (H11).

Renders service definitions for the two init systems we deploy on:

  * **systemd** unit files (Linux production hosts);
  * **launchd** plist files (macOS dev / edge hosts).

Both render from one frozen :class:`DeploySpec` so a strategy's
deployment shape is declared once and generated per-platform. Common
guarantees baked into both outputs:

  * restart-on-failure (systemd ``Restart=on-failure``; launchd
    ``KeepAlive.SuccessfulExit=false``) with a throttle interval;
  * stdout/stderr appended to per-strategy log files;
  * environment variables injected from a mapping — use
    :func:`placeholder_env` for secrets so the unit references
    ``${VAR}`` placeholders instead of carrying key material (the real
    values are resolved at load time, never written here; see
    ``_shared/ops/secrets.py``, H13).

References:
- systemd.service(5) — Restart=on-failure semantics: restart on non-zero
  exit, signal, watchdog timeout, but not on clean exit code 0.
- launchd.plist(5) — KeepAlive.SuccessfulExit=false mirrors the same
  policy on macOS.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple
from xml.sax.saxutils import escape as _xml_escape


@dataclass(frozen=True)
class DeploySpec:
    """One strategy's deployment shape.

    Attributes:
        strategy: strategy identifier (used for unit name and log files).
        argv: full command line, e.g. ("/usr/bin/python3", "runner.py", "--live").
        working_dir: service working directory.
        log_dir: directory for appended stdout/stderr logs.
        env: environment variables; values may be "${VAR}" placeholders.
        user: systemd User= ("" = leave to the unit's default).
        restart_sec: delay between restart attempts.
    """

    strategy: str
    argv: Tuple[str, ...]
    working_dir: str
    log_dir: str
    env: Mapping[str, str] = field(default_factory=dict)
    user: str = ""
    restart_sec: int = 5

    def __post_init__(self) -> None:
        if not self.strategy:
            raise ValueError("strategy must be non-empty")
        if not self.argv:
            raise ValueError("argv must be a non-empty command")
        if self.restart_sec < 0:
            raise ValueError("restart_sec must be >= 0")

    @property
    def stdout_log(self) -> str:
        return str(Path(self.log_dir) / f"{self.strategy}.out.log")

    @property
    def stderr_log(self) -> str:
        return str(Path(self.log_dir) / f"{self.strategy}.err.log")


def placeholder_env(*names: str) -> Mapping[str, str]:
    """Map VAR -> "${VAR}" so secrets are injected at load time, not written."""
    return {name: "${" + name + "}" for name in names}


def render_systemd_unit(spec: DeploySpec) -> str:
    """Render a systemd .service unit. Pure."""
    lines = [
        "[Unit]",
        f"Description=quant-loop strategy {spec.strategy}",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
    ]
    if spec.user:
        lines.append(f"User={spec.user}")
    lines.append(f"WorkingDirectory={spec.working_dir}")
    for key in sorted(spec.env):
        lines.append(f'Environment="{key}={spec.env[key]}"')
    lines += [
        f"ExecStart={shlex.join(spec.argv)}",
        "Restart=on-failure",
        f"RestartSec={spec.restart_sec}",
        f"StandardOutput=append:{spec.stdout_log}",
        f"StandardError=append:{spec.stderr_log}",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(lines)


def render_launchd_plist(spec: DeploySpec) -> str:
    """Render a launchd .plist (XML). Pure."""
    args = "\n".join(
        f"    <string>{_xml_escape(a)}</string>" for a in spec.argv
    )
    env_items = "\n".join(
        f"    <key>{_xml_escape(k)}</key>\n"
        f"    <string>{_xml_escape(spec.env[k])}</string>"
        for k in sorted(spec.env)
    )
    env_block = ""
    if spec.env:
        env_block = (
            "  <key>EnvironmentVariables</key>\n"
            "  <dict>\n" + env_items + "\n  </dict>\n"
        )
    label = f"com.quant-loop.{spec.strategy}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{_xml_escape(label)}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>WorkingDirectory</key>
  <string>{_xml_escape(spec.working_dir)}</string>
{env_block}  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ThrottleInterval</key>
  <integer>{spec.restart_sec}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{_xml_escape(spec.stdout_log)}</string>
  <key>StandardErrorPath</key>
  <string>{_xml_escape(spec.stderr_log)}</string>
</dict>
</plist>
"""


def write_unit(spec: DeploySpec, out_path, platform: Optional[str] = None) -> Path:
    """Write the unit for ``platform`` ("systemd" | "launchd") to ``out_path``.

    When ``platform`` is None it is inferred from the file suffix:
    ".plist" -> launchd, anything else -> systemd.
    """
    out_path = Path(out_path)
    if platform is None:
        platform = "launchd" if out_path.suffix == ".plist" else "systemd"
    if platform == "systemd":
        text = render_systemd_unit(spec)
    elif platform == "launchd":
        text = render_launchd_plist(spec)
    else:
        raise ValueError(f"platform must be 'systemd' or 'launchd', got {platform!r}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path
