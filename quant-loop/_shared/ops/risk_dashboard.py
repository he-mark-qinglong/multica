"""Real-time risk monitoring dashboard (D19).

Self-contained single-file HTML generator: positions + equity + tail-risk
metrics (VaR/CVaR via ``_shared/market_making/tail_risk.py``), gross/net
exposure (``_shared/portfolio/exposure.py`` notions), active alerts
(``_shared/ops/alerting.py``), and heartbeat liveness
(``_shared/ops/heartbeat.py``) rendered into one dependency-free HTML page
(meta-refresh, pure CSS bars, traffic light red/yellow/green, NO JavaScript).

``render_dashboard(state)`` is a pure function; ``watch_loop`` re-reads a
state directory and atomically rewrites the HTML file on a fixed cadence.

References:
- Google SRE Book, ch. 6 "Monitoring Distributed Systems" — dashboards
  should answer "is it on fire?" at a glance; the traffic light aggregates
  symptoms, the tables provide drill-down.
- Rockafellar & Uryasev (2000), "Optimization of Conditional Value-at-Risk"
  — VaR/CVaR table semantics inherited from tail_risk.py.
"""
from __future__ import annotations

import html
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Tuple

from _shared.market_making.tail_risk import TailRiskResult, compute_tail_risk
from _shared.ops.alerting import Alert, AlertLevel
from _shared.ops.heartbeat import HeartbeatStatus, check_heartbeat
from _shared.portfolio.exposure import Position

STATE_FILENAME = "state.json"
BEAT_FILENAME = "beat.json"


@dataclass(frozen=True)
class DashboardState:
    """One immutable snapshot of everything the dashboard renders."""

    ts: float
    equity: float
    positions: Tuple[Position, ...] = ()
    pnl_history_bp: Tuple[float, ...] = ()
    alerts: Tuple[Alert, ...] = ()
    heartbeat: Optional[HeartbeatStatus] = None
    var_limit_bp: float = 200.0          # RED when 95% VaR exceeds this
    var_warn_fraction: float = 0.7       # YELLOW above limit * fraction
    refresh_sec: int = 5


@dataclass(frozen=True)
class TrafficLight:
    """Aggregated risk signal: GREEN / YELLOW / RED with reasons."""

    level: str                            # "GREEN" | "YELLOW" | "RED"
    reasons: Tuple[str, ...] = ()


def evaluate_traffic_light(
    state: DashboardState,
    tail: TailRiskResult,
) -> TrafficLight:
    """Pure aggregation rule for the headline risk light.

    RED: any CRITICAL alert, dead/stale heartbeat, or VaR95 over limit.
    YELLOW: any WARN alert, VaR95 above warn fraction of the limit, or a
    heartbeat reporting a non-"running" state. GREEN otherwise.
    """
    red = []
    yellow = []
    for a in state.alerts:
        if a.level == AlertLevel.CRITICAL.value:
            red.append(f"critical alert: {a.rule}")
        elif a.level == AlertLevel.WARN.value:
            yellow.append(f"warn alert: {a.rule}")
    if state.heartbeat is not None:
        hb = state.heartbeat
        if not hb.alive:
            red.append(f"heartbeat dead ({hb.state})")
        elif hb.state != "running":
            yellow.append(f"heartbeat state={hb.state}")
    if state.var_limit_bp > 0:
        if tail.var_95_bp > state.var_limit_bp:
            red.append(
                f"VaR95 {tail.var_95_bp:.1f}bp > limit {state.var_limit_bp:.1f}bp"
            )
        elif tail.var_95_bp > state.var_limit_bp * state.var_warn_fraction:
            yellow.append(
                f"VaR95 {tail.var_95_bp:.1f}bp > "
                f"{state.var_warn_fraction:.0%} of limit"
            )
    if red:
        return TrafficLight(level="RED", reasons=tuple(red + yellow))
    if yellow:
        return TrafficLight(level="YELLOW", reasons=tuple(yellow))
    return TrafficLight(level="GREEN", reasons=())


# --- HTML rendering (pure, CSS-only, no JS) ----------------------------------
_CSS = """
body{font-family:Menlo,monospace;background:#111;color:#ddd;margin:2em}
h1{font-size:1.3em} h2{font-size:1em;color:#aaa;margin-top:1.5em}
.light{display:inline-block;padding:.3em 1em;border-radius:1em;
 font-weight:bold;color:#111}
.GREEN{background:#3c3}.YELLOW{background:#fc3}.RED{background:#f44}
table{border-collapse:collapse} td,th{border:1px solid #444;
 padding:.2em .6em;text-align:left;font-size:.85em}
.bar{height:.9em;background:#39c;border-radius:.2em}
.barwrap{background:#333;border-radius:.2em;width:16em}
.warn{color:#fc3}.crit{color:#f66}.ok{color:#3c3}
small{color:#777}
"""


def _esc(s: Any) -> str:
    return html.escape(str(s))


def _render_exposure_rows(state: DashboardState) -> str:
    gross = sum(p.notional for p in state.positions)
    rows = []
    for p in sorted(state.positions, key=lambda x: -x.notional):
        pct = (p.notional / gross * 100.0) if gross > 0 else 0.0
        side = "long" if p.qty > 0 else "short"
        rows.append(
            f"<tr><td>{_esc(p.symbol)}</td><td>{side}</td>"
            f"<td>{p.qty:g}</td><td>{p.price:g}</td>"
            f"<td>{p.notional:,.2f}</td>"
            f'<td><div class="barwrap"><div class="bar" '
            f'style="width:{pct:.1f}%"></div></div></td>'
            f"<td>{pct:.1f}%</td></tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="7"><small>flat — no positions</small></td></tr>')
    net = sum(p.signed_notional for p in state.positions)
    return (
        "<table><tr><th>symbol</th><th>side</th><th>qty</th><th>price</th>"
        "<th>notional</th><th>share of gross</th><th>%</th></tr>"
        + "".join(rows)
        + f"</table><p><small>gross {gross:,.2f} | net {net:+,.2f} | "
        f"equity {state.equity:,.2f}"
        + (
            f" | leverage {gross / state.equity:.2f}x"
            if state.equity > 0
            else " | leverage n/a (equity <= 0)"
        )
        + "</small></p>"
    )


def _render_var_table(tail: TailRiskResult, state: DashboardState) -> str:
    def cls(v: float) -> str:
        if state.var_limit_bp > 0 and v > state.var_limit_bp:
            return ' class="crit"'
        if state.var_limit_bp > 0 and v > state.var_limit_bp * state.var_warn_fraction:
            return ' class="warn"'
        return ""

    rows = [
        ("VaR 95% (bp)", tail.var_95_bp), ("VaR 99% (bp)", tail.var_99_bp),
        ("CVaR 95% (bp)", tail.cvar_95_bp), ("CVaR 99% (bp)", tail.cvar_99_bp),
        ("Cornish-Fisher VaR 95% (bp)", tail.cf_var_95_bp),
        ("Cornish-Fisher VaR 99% (bp)", tail.cf_var_99_bp),
        ("worst case (bp)", tail.worst_case_bp),
        ("mean (bp)", tail.mean_bp), ("std (bp)", tail.std_bp),
        ("skewness", tail.skewness),
        ("excess kurtosis", tail.excess_kurtosis),
    ]
    body = "".join(
        f"<tr><td>{name}</td><td{cls(val) if 'VaR' in name else ''}>"
        f"{val:,.2f}</td></tr>"
        for name, val in rows
    )
    return (
        "<table><tr><th>metric</th><th>value</th></tr>" + body + "</table>"
        f"<p><small>samples {tail.n_samples} | max consecutive losses "
        f"{tail.max_consecutive_losses} | VaR95 limit "
        f"{state.var_limit_bp:.1f}bp</small></p>"
    )


def _render_alerts(state: DashboardState) -> str:
    if not state.alerts:
        return '<p class="ok">no active alerts</p>'
    items = []
    for a in sorted(state.alerts, key=lambda x: -x.ts):
        cls = "crit" if a.level == AlertLevel.CRITICAL.value else (
            "warn" if a.level == AlertLevel.WARN.value else ""
        )
        items.append(
            f'<li class="{cls}">[{_esc(a.level)}] {_esc(a.rule)}: '
            f"{_esc(a.message)}</li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def _render_heartbeat(state: DashboardState) -> str:
    hb = state.heartbeat
    if hb is None:
        return "<p><small>heartbeat: not monitored</small></p>"
    if hb.alive:
        return (
            f'<p class="ok">heartbeat alive — state {_esc(hb.state)}, '
            f"last beat {hb.age_sec:.1f}s ago "
            f"(timeout {hb.timeout_sec:.1f}s)</p>"
        )
    age = "never seen" if hb.last_ts is None else f"{hb.age_sec:.1f}s ago"
    return (
        f'<p class="crit">heartbeat DEAD — state {_esc(hb.state)}, '
        f"last beat {age} (timeout {hb.timeout_sec:.1f}s)</p>"
    )


def render_dashboard(state: DashboardState) -> str:
    """Render the dashboard snapshot to a self-contained HTML string. Pure."""
    tail = compute_tail_risk(state.pnl_history_bp)
    light = evaluate_traffic_light(state, tail)
    reasons = (
        "<ul>" + "".join(f"<li>{_esc(r)}</li>" for r in light.reasons) + "</ul>"
        if light.reasons else ""
    )
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(state.ts))
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{state.refresh_sec}">
<title>quant-loop risk dashboard</title>
<style>{_CSS}</style>
</head><body>
<h1>quant-loop risk dashboard <span class="light {light.level}">{light.level}</span></h1>
<p><small>snapshot {stamp} UTC | auto-refresh {state.refresh_sec}s</small></p>
{reasons}
<h2>Exposure</h2>
{_render_exposure_rows(state)}
<h2>Tail risk (VaR / CVaR)</h2>
{_render_var_table(tail, state)}
<h2>Active alerts</h2>
{_render_alerts(state)}
<h2>Heartbeat</h2>
{_render_heartbeat(state)}
</body></html>
"""


# --- state loading + watch loop ----------------------------------------------
def load_state_from_dir(
    state_dir,
    beat_timeout_sec: float = 30.0,
    now: Optional[float] = None,
) -> DashboardState:
    """Build a DashboardState from ``state.json`` + ``beat.json`` in a dir.

    Missing files degrade gracefully (flat book, no alerts, no heartbeat)
    so the dashboard always renders something. ``state.json`` schema::

        {"ts": float, "equity": float,
         "positions": [{"symbol","qty","price"}, ...],
         "pnl_history_bp": [float, ...],
         "alerts": [{"ts","level","rule","message","context"?}, ...],
         "var_limit_bp": float?, "refresh_sec": int?}
    """
    state_dir = Path(state_dir)
    now_ts = time.time() if now is None else float(now)
    raw: Mapping[str, Any] = {}
    try:
        payload = json.loads((state_dir / STATE_FILENAME).read_text())
        if isinstance(payload, dict):
            raw = payload
    except (OSError, json.JSONDecodeError):
        raw = {}

    positions = tuple(
        Position(
            symbol=str(p["symbol"]),
            qty=float(p["qty"]),
            price=float(p["price"]),
        )
        for p in raw.get("positions", [])
    )
    alerts = tuple(
        Alert(
            ts=float(a.get("ts", now_ts)),
            level=str(a.get("level", AlertLevel.INFO.value)),
            rule=str(a.get("rule", "unknown")),
            message=str(a.get("message", "")),
            context=dict(a.get("context", {})),
        )
        for a in raw.get("alerts", [])
    )
    heartbeat = check_heartbeat(
        state_dir / BEAT_FILENAME,
        timeout_sec=float(raw.get("beat_timeout_sec", beat_timeout_sec)),
        now=now_ts,
    )
    return DashboardState(
        ts=float(raw.get("ts", now_ts)),
        equity=float(raw.get("equity", 0.0)),
        positions=positions,
        pnl_history_bp=tuple(float(x) for x in raw.get("pnl_history_bp", ())),
        alerts=alerts,
        heartbeat=heartbeat,
        var_limit_bp=float(raw.get("var_limit_bp", 200.0)),
        refresh_sec=int(raw.get("refresh_sec", 5)),
    )


def write_dashboard(state: DashboardState, out_html) -> str:
    """Render and atomically write the HTML file; returns the HTML string."""
    out_html = Path(out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    page = render_dashboard(state)
    tmp = out_html.with_suffix(out_html.suffix + ".tmp")
    tmp.write_text(page, encoding="utf-8")
    os.replace(tmp, out_html)
    return page


def watch_loop(
    state_dir,
    out_html,
    interval_sec: float = 5.0,
    *,
    beat_timeout_sec: float = 30.0,
    stop: Optional[Callable[[], bool]] = None,
    max_iterations: Optional[int] = None,
) -> int:
    """Periodically reload state and rewrite ``out_html``. Returns iterations.

    Synchronous and caller-controlled (same convention as
    ``strategy_kit/hot_reload.ConfigReloader.check_once``); pass ``stop`` or
    ``max_iterations`` for deterministic tests and daemons respectively.
    """
    iterations = 0
    while True:
        state = load_state_from_dir(state_dir, beat_timeout_sec=beat_timeout_sec)
        write_dashboard(state, out_html)
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return iterations
        if stop is not None and stop():
            return iterations
        time.sleep(interval_sec)
